import os
import io
import re
import html
import hmac
import json
import time
import uuid
import sqlite3
import tempfile
import zipfile
from datetime import datetime
import pandas as pd
import streamlit as st
from druhy import analyzuj_slovicko, urci_deklinaci_adjektiva
from jazyky import (
    DATA_DIR,
    JAZYKY,
    aplikuj_filtry,
    get_csv_path,
    get_importy_dir,
    get_zadosti_path,
    get_zalohy_dir,
    klic_serazeni_lekci,
    odstran_diakritiku,
    parsuj_import_csv,
    vytvor_anki_balicek,
)
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
qr_path = (
    BASE_DIR / "qr-platba.png"
    if (BASE_DIR / "qr-platba.png").is_file()
    else BASE_DIR / "assets" / "qr-platba.png"
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# --- POMOCNÉ FUNKCE PRO HESLO A .ENV ---
def nacti_admin_heslo() -> str:
    """Získá administrátorské heslo ze secrets, proměnných prostředí nebo přímo ze souboru .env."""
    try:
        if hasattr(st, "secrets") and "ADMIN_PASSWORD" in st.secrets:
            return str(st.secrets["ADMIN_PASSWORD"]).strip()
    except Exception:
        pass
    if os.environ.get("ADMIN_PASSWORD"):
        return str(os.environ["ADMIN_PASSWORD"]).strip()
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == "ADMIN_PASSWORD":
                            return v.strip().strip("'\"")
        except Exception:
            pass
    return ""


def over_heslo(zadane: str, spravne: str) -> bool:
    """Bezpečné porovnání hesel odolné vůči časovým útokům (timing attacks)."""
    if not spravne:
        return False
    return hmac.compare_digest(zadane.strip(), spravne.strip())


def aktivuj_ctrl_f_pro_sidebar(label_vyhledavani: str):
    """
    Zachytí Ctrl+F / Cmd+F v nadřazeném okně prohlížeče,
    otevře sidebar (pokud je sbalený) a zaostří konkrétní text_input.
    """
    st.html(
        f"""
        <script>
        (function() {{
            const win = window.parent || window;
            const doc = win.document;

            if (doc._ctrlFHandler) {{
                doc.removeEventListener('keydown', doc._ctrlFHandler, true);
            }}

            doc._ctrlFHandler = function(e) {{
                if ((e.ctrlKey || e.metaKey) && e.key && e.key.toLowerCase() === 'f') {{
                    e.preventDefault();
                    e.stopPropagation();

                    const expandBtn = doc.querySelector('[data-testid="stExpandSidebarButton"] button')
                                   || doc.querySelector('[data-testid="stExpandSidebarButton"]')
                                   || doc.querySelector('[data-testid="collapsedControl"] button');
                    if (expandBtn) {{
                        expandBtn.click();
                    }}

                    const tryFocus = () => {{
                        const sidebar = doc.querySelector('[data-testid="stSidebar"]');
                        if (!sidebar) return false;

                        let searchInput = sidebar.querySelector('input[aria-label="{label_vyhledavani}"]');
                        if (!searchInput) {{
                            searchInput = sidebar.querySelector('input[type="text"]');
                        }}
                        if (searchInput) {{
                            searchInput.focus();
                            searchInput.select();
                            return true;
                        }}
                        return false;
                    }};

                    if (!tryFocus()) {{
                        win.setTimeout(tryFocus, 50);
                        win.setTimeout(tryFocus, 150);
                    }}
                }}
            }};

            doc.addEventListener('keydown', doc._ctrlFHandler, true);
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


# --- NASTAVENÍ STRÁNKY A SESSION STATE ---
st.set_page_config(page_title="Generátor karet do Anki", page_icon="📚", layout="wide")

# CSS a JS úprava:
# - ikona bočního panelu: hamburger / dvojitá šipka
# - přepínač diakritiky v tabulkách (hover toolbar vpravo nahoře i search bar)
# - rozložení horního menu (jazyky vlevo, ostatní položky vpravo)
# - zachycení zkratky Ctrl+F pro focus vyhledávacího pole v levém panelu
st.html(
    r"""
    <style>
    [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"],
    [data-testid="collapsedControl"] [data-testid="stIconMaterial"] {
        font-size: 0 !important;
        line-height: 0 !important;
    }
    [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"]::before,
    [data-testid="collapsedControl"] [data-testid="stIconMaterial"]::before {
        content: "\e5d2" !important;
        font-size: 1.5rem !important;
        font-family: "Material Symbols Rounded" !important;
        line-height: 1 !important;
        display: inline-block !important;
    }
    </style>

    <script>
    (function() {
        const W = window.parent;
        const D = W.document;

        // ── HELPER: odstranit diakritiku ──────────────────────────────────────────
        function stripDiacritics(str) {
            if (!str) return '';
            return str.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
        }

        // ── STAV: citlivost na diakritiku (uloženo v parent window) ──────────────
        if (typeof W.__diacriticsSensitive === 'undefined') {
            W.__diacriticsSensitive = false;
        }

        // ── MONKEY-PATCH RegExp.prototype.test V PARENT WINDOW ───────────────────
        if (!W.__diacriticsPatchInstalled) {
            W.__diacriticsPatchInstalled = true;
            const _origTest = W.RegExp.prototype.test;

            const _hasAlpha = function(src) {
                for (let i = 0; i < src.length; i++) {
                    const c = src.charCodeAt(i);
                    if (
                        (c >= 65 && c <= 90) ||
                        (c >= 97 && c <= 122) ||
                        (c >= 0x00C0 && c <= 0x024F) ||
                        (c >= 0x0370 && c <= 0x03FF) ||
                        (c >= 0x1F00 && c <= 0x1FFF)
                    ) {
                        return true;
                    }
                }
                return false;
            };

            W.RegExp.prototype.test = function(str) {
                const res = _origTest.call(this, str);
                if (res || W.__diacriticsSensitive || typeof str !== 'string') {
                    return res;
                }
                if (this.ignoreCase && _hasAlpha(this.source)) {
                    try {
                        const cleanRegex = new W.RegExp(stripDiacritics(this.source), this.flags);
                        return _origTest.call(cleanRegex, stripDiacritics(str));
                    } catch (e) {
                        return res;
                    }
                }
                return res;
            };
        }

        // ── CSS STYLY DO PARENT DOKUMENTU ────────────────────────────────────────
        if (!D.getElementById('__gdg-diacritics-styles')) {
            const styleEl = D.createElement('style');
            styleEl.id = '__gdg-diacritics-styles';
            styleEl.textContent = `
                .gdg-diacritics-toggle-container {
                    display: inline-flex; align-items: center;
                    margin-left: 6px; padding: 2px 8px;
                    border-radius: 4px; background: rgba(128,128,128,0.15);
                    font-size: 12px; user-select: none; cursor: pointer;
                    white-space: nowrap; border: 1px solid rgba(128,128,128,0.2);
                    transition: all 0.2s ease;
                }
                .gdg-diacritics-toggle-container:hover { background: rgba(128,128,128,0.25); }
                .gdg-diacritics-toggle-label {
                    display: flex; align-items: center; gap: 5px;
                    cursor: pointer; margin: 0; font-size: 12px; color: inherit;
                }
                .gdg-diacritics-checkbox {
                    cursor: pointer; margin: 0; width: 14px; height: 14px;
                    accent-color: #ff4b4b;
                }
                .gdg-diacritics-text { font-size: 11px; font-weight: 500; }
                .st-diacritics-toolbar-btn { display: inline-flex; align-items: center; }
                .st-diacritics-btn {
                    background: transparent; border: 1px solid rgba(128,128,128,0.25);
                    border-radius: 4px; cursor: pointer; padding: 2px 6px;
                    display: flex; align-items: center; justify-content: center;
                    color: inherit; font-family: inherit; transition: all 0.2s ease; height: 28px;
                }
                .st-diacritics-btn:hover { background: rgba(128,128,128,0.15); }
                .st-diacritics-btn.active {
                    background: rgba(255,75,75,0.2); border-color: #ff4b4b;
                    color: #ff4b4b; font-weight: bold;
                }
            `;
            D.head.appendChild(styleEl);
        }

        // ── HELPER: retrigger GDG search (React-kompatibilní) ────────────────────
        function retriggerGdgSearch() {
            D.querySelectorAll('[data-testid="search-input"]').forEach(inp => {
                if (!inp.value) return;
                const setter = Object.getOwnPropertyDescriptor(
                    W.HTMLInputElement.prototype, 'value'
                ).set;
                const val = inp.value;
                setter.call(inp, val + '\u200b');
                inp.dispatchEvent(new W.Event('input', { bubbles: true }));
                W.setTimeout(() => {
                    setter.call(inp, val);
                    inp.dispatchEvent(new W.Event('input', { bubbles: true }));
                }, 16);
            });
        }

        // ── NASTAVENÍ CITLIVOSTI NA DIAKRITIKU ───────────────────────────────────
        function setDiacriticsSensitive(val) {
            W.__diacriticsSensitive = !!val;

            D.querySelectorAll('.gdg-diacritics-checkbox').forEach(cb => {
                cb.checked = W.__diacriticsSensitive;
            });
            D.querySelectorAll('.st-diacritics-btn').forEach(btn => {
                btn.classList.toggle('active', W.__diacriticsSensitive);
            });

            retriggerGdgSearch();

            const sidebarToggles = D.querySelectorAll(
                '[data-testid="stSidebar"] [data-testid="stCheckbox"] input,' +
                '[data-testid="stSidebar"] [data-testid="stToggle"] input'
            );
            sidebarToggles.forEach(stToggle => {
                if (!stToggle || stToggle.checked === W.__diacriticsSensitive) return;
                const par = stToggle.closest('[data-testid="stCheckbox"],[data-testid="stToggle"]');
                if (par && par.textContent.toLowerCase().includes('diakritik')) {
                    stToggle.click();
                }
            });
        }
        W.__setDiacriticsSensitive = setDiacriticsSensitive;

        // ── INJECT TOGGLES DO GDG SEARCH BARU A TOOLBARU ────────────────────────
        function attachTableDiacriticsToggles() {
            const sidebarToggles = D.querySelectorAll(
                '[data-testid="stSidebar"] [data-testid="stCheckbox"] input,' +
                '[data-testid="stSidebar"] [data-testid="stToggle"] input'
            );
            sidebarToggles.forEach(stToggle => {
                const par = stToggle.closest('[data-testid="stCheckbox"],[data-testid="stToggle"]');
                if (par && par.textContent.toLowerCase().includes('diakritik')) {
                    if (W.__lastSidebarDiacriticsEl !== stToggle) {
                        W.__lastSidebarDiacriticsEl = stToggle;
                        W.__diacriticsSensitive = !!stToggle.checked;
                        D.querySelectorAll('.gdg-diacritics-checkbox').forEach(cb => { cb.checked = W.__diacriticsSensitive; });
                        D.querySelectorAll('.st-diacritics-btn').forEach(btn => btn.classList.toggle('active', W.__diacriticsSensitive));
                    }
                }
            });

            D.querySelectorAll('.gdg-search-bar-inner').forEach(bar => {
                if (bar.querySelector('.gdg-diacritics-toggle-container')) return;

                const container = D.createElement('div');
                container.className = 'gdg-diacritics-toggle-container';
                container.title = 'Rozlišovat diakritiku (ā, č, ě, š, ž, ...)';

                const label = D.createElement('label');
                label.className = 'gdg-diacritics-toggle-label';

                const checkbox = D.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'gdg-diacritics-checkbox';
                checkbox.checked = !!W.__diacriticsSensitive;

                const text = D.createElement('span');
                text.className = 'gdg-diacritics-text';
                text.textContent = 'Diakritika';

                label.appendChild(checkbox);
                label.appendChild(text);
                container.appendChild(label);

                checkbox.addEventListener('change', (e) => {
                    e.stopPropagation();
                    setDiacriticsSensitive(checkbox.checked);
                });
                container.addEventListener('click', (e) => e.stopPropagation());

                bar.appendChild(container);
            });

            D.querySelectorAll('[data-testid="stElementToolbar"] [data-testid="stElementToolbarButtonContainer"]').forEach(tb => {
                if (tb.querySelector('.st-diacritics-toolbar-btn')) return;

                const btnDiv = D.createElement('div');
                btnDiv.className = 'st-diacritics-toolbar-btn';
                btnDiv.setAttribute('data-testid', 'stElementToolbarButton');
                btnDiv.title = 'Rozlišovat diakritiku (ā, č, ě, š, ž, ...)';

                const btn = D.createElement('button');
                btn.type = 'button';
                btn.className = 'st-diacritics-btn' + (W.__diacriticsSensitive ? ' active' : '');
                btn.innerHTML = '<span style="font-weight:700;font-size:12px;letter-spacing:-0.5px;">Āā</span>';

                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    setDiacriticsSensitive(!W.__diacriticsSensitive);
                });

                btnDiv.appendChild(btn);
                tb.insertBefore(btnDiv, tb.firstChild);
            });
        }

        // ── ROZLOŽENÍ HORNÍHO MENU: JAZYKY VLEVO, OSTATNÍ POLOŽKY VPRAVO ──────────
        function organizeTopNav() {
            const header = D.querySelector('header[data-testid="stHeader"]');
            if (!header) return;

            if (!D.getElementById('__custom-topnav-styles')) {
                const navStyle = D.createElement('style');
                navStyle.id = '__custom-topnav-styles';
                navStyle.textContent = `
                    .custom-topnav-stretch {
                        display: flex !important;
                        align-items: center !important;
                        flex: 1 1 auto !important;
                        min-width: 0 !important;
                    }
                    .custom-topnav-lca {
                        display: flex !important;
                        align-items: center !important;
                        width: 100% !important;
                        flex: 1 1 auto !important;
                        min-width: 0 !important;
                    }
                    .custom-topnav-right-start {
                        margin-left: auto !important;
                    }
                `;
                D.head.appendChild(navStyle);
            }

            const getCleanText = (el) => {
                const clone = el.cloneNode(true);
                clone.querySelectorAll('[data-testid="stIconMaterial"], svg, [aria-hidden="true"]').forEach(n => n.remove());
                return clone.textContent.replace(/\s+/g, ' ').trim();
            };

            const orderByText = {
                'Latina': 1,
                'Řečtina': 2,
                'Knihovna kartiček': 3,
                'Jak používat kartičky': 4,
                'O projektu': 5,
                'Přihlásit se': 6,
                'Odhlásit se': 6,
            };

            const orderByHref = {
                '/knihovna': 3,
                '/navod': 4,
                '/o-projektu': 5,
                '/prihlaseni': 6,
            };

            const foundItems = [];
            header.querySelectorAll('a, button, [role="button"]').forEach(el => {
                const txt = getCleanText(el);
                let ord = orderByText[txt];
                if (!ord && el.tagName === 'A') {
                    const href = el.getAttribute('href') || '';
                    for (const [suffix, val] of Object.entries(orderByHref)) {
                        if (href.endsWith(suffix)) {
                            ord = val;
                            break;
                        }
                    }
                }
                if (ord) {
                    foundItems.push({ el, ord });
                }
            });

            if (foundItems.length < 2) return;

            let lca = foundItems[0].el.parentElement;
            while (lca && lca !== header) {
                if (foundItems.every(item => lca.contains(item.el))) {
                    break;
                }
                lca = lca.parentElement;
            }
            if (!lca || lca === header) return;

            lca.classList.add('custom-topnav-lca');
            let anc = lca.parentElement;
            while (anc && anc !== header) {
                const tid = anc.getAttribute('data-testid');
                if (tid !== 'stToolbar') {
                    anc.classList.add('custom-topnav-stretch');
                }
                anc = anc.parentElement;
            }

            const childOrders = new Map();
            for (const { el, ord } of foundItems) {
                let directChild = el;
                while (directChild.parentElement && directChild.parentElement !== lca) {
                    directChild = directChild.parentElement;
                }
                if (directChild.parentElement === lca) {
                    const prev = childOrders.get(directChild);
                    if (!prev || ord < prev) {
                        childOrders.set(directChild, ord);
                    }
                }
            }

            Array.from(lca.children).forEach(child => {
                const tid = child.getAttribute('data-testid');
                const ord = childOrders.get(child);
                child.classList.remove('custom-topnav-right-start');

                if (ord) {
                    child.style.setProperty('order', String(ord), 'important');
                    if (ord === 3) {
                        child.classList.add('custom-topnav-right-start');
                    }
                } else if (tid === 'stExpandSidebarButton' || tid === 'collapsedControl') {
                    child.style.setProperty('order', '0', 'important');
                } else {
                    child.style.setProperty('order', '99', 'important');
                }
            });
        }
        W.__organizeTopNav = organizeTopNav;

        // ── MUTATION OBSERVER NA PARENT DOCUMENT ─────────────────────────────────
        if (W.__diacriticsObserver) {
            W.__diacriticsObserver.disconnect();
        }
        W.__diacriticsObserver = new W.MutationObserver(() => {
            attachTableDiacriticsToggles();
            if (W.__organizeTopNav) W.__organizeTopNav();
        });
        W.__diacriticsObserver.observe(D.body, { childList: true, subtree: true });

        attachTableDiacriticsToggles();
        organizeTopNav();
        W.setTimeout(organizeTopNav, 100);
        W.setTimeout(organizeTopNav, 400);

        // ── SYNCHRONIZACE ZE STREAMLIT SIDEBAR PŘEPÍNAČE ─────────────────────────
        if (!W.__diacriticsSidebarListenerInstalled) {
            W.__diacriticsSidebarListenerInstalled = true;
            D.addEventListener('change', (e) => {
                const target = e.target;
                if (!target || !target.closest('[data-testid="stSidebar"]') || target.type !== 'checkbox') return;
                const par = target.closest('[data-testid="stCheckbox"],[data-testid="stToggle"]');
                if (par && par.textContent.toLowerCase().includes('diakritik')) {
                    if (W.__diacriticsSensitive !== target.checked) {
                        W.__diacriticsSensitive = target.checked;
                        D.querySelectorAll('.gdg-diacritics-checkbox').forEach(cb => { cb.checked = W.__diacriticsSensitive; });
                        D.querySelectorAll('.st-diacritics-btn').forEach(btn => btn.classList.toggle('active', W.__diacriticsSensitive));
                        retriggerGdgSearch();
                    }
                }
            });
        }

        // ── CTRL+F → FOCUS VYHLEDÁVÁNÍ V BOČNÍM PANELU ───────────────────────────
        if (!W.__diacriticsCtrlFInstalled) {
            W.__diacriticsCtrlFInstalled = true;
            W.addEventListener('keydown', function(e) {
                if ((e.ctrlKey || e.metaKey) && (e.key === 'f' || e.key === 'F')) {
                    e.preventDefault();
                    e.stopPropagation();
                    e.stopImmediatePropagation();

                    const expandBtn = D.querySelector(
                        '[data-testid="stExpandSidebarButton"] button,' +
                        '[data-testid="collapsedControl"] button,' +
                        '[data-testid="stExpandSidebarButton"],' +
                        '[data-testid="collapsedControl"]'
                    );
                    if (expandBtn) expandBtn.click();

                    const focusSearch = () => {
                        const sidebar = D.querySelector('[data-testid="stSidebar"]');
                        if (!sidebar) return false;
                        const input = sidebar.querySelector(
                            'input[placeholder*="Latina"], input[placeholder*="Řečtina"], input[aria-label*="Hledat"], [data-testid="stTextInput"] input'
                        );
                        if (input) {
                            input.focus();
                            input.select();
                            input.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                            return true;
                        }
                        return false;
                    };

                    if (!focusSearch()) {
                        W.setTimeout(focusSearch, 50);
                        W.setTimeout(focusSearch, 150);
                        W.setTimeout(focusSearch, 300);
                    }
                }
            }, true);
        }
    })();
    </script>
    """,
    unsafe_allow_javascript=True,
)

if "admin_prihlasen" not in st.session_state:
    st.session_state.admin_prihlasen = False

# ==============================================================================
# CESTY K PERZISTENTNÍMU ÚLOŽIŠTI A INICIALIZACE DATABÁZE
# ==============================================================================
KNIHOVNA_JSON_PATH = os.path.join(DATA_DIR, "knihovna_balicku.json")
BALICKY_DIR = os.path.join(DATA_DIR, "balicky")


def zajisti_csv_jazyka(lang: str) -> str:
    """
    Zajistí existenci adresářové struktury (/app/data/...) a validního CSV souboru
    s hlavičkou pro daný jazyk, pokud ještě neexistuje nebo je prázdný.
    """
    cfg = JAZYKY[lang]
    csv_soubor = get_csv_path(lang)
    adresar = os.path.dirname(csv_soubor)
    if adresar:
        os.makedirs(adresar, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(BALICKY_DIR, exist_ok=True)

    if not os.path.exists(csv_soubor) or os.path.getsize(csv_soubor) == 0:
        pd.DataFrame(columns=cfg["sloupce_csv"]).to_csv(
            csv_soubor, sep=";", index=False, encoding="utf-8"
        )
    return csv_soubor


def inicializuj_uloziste() -> None:
    """Inicializuje datové adresáře a prázdné CSV soubory pro všechny nakonfigurované jazyky."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(BALICKY_DIR, exist_ok=True)
    for lang_kod in JAZYKY:
        zajisti_csv_jazyka(lang_kod)


inicializuj_uloziste()


# --- NAČTENÍ A UKLÁDÁNÍ DAT ---
@st.cache_data
def nacti_data(lang: str = "latina") -> pd.DataFrame:
    cfg = JAZYKY[lang]
    csv_soubor = zajisti_csv_jazyka(lang)
    col_cizi = cfg["sloupec"]

    try:
        df_lang = pd.read_csv(csv_soubor, sep=";", dtype=str)
    except pd.errors.EmptyDataError:
        pd.DataFrame(columns=cfg["sloupce_csv"]).to_csv(
            csv_soubor, sep=";", index=False, encoding="utf-8"
        )
        df_lang = pd.DataFrame(columns=cfg["sloupce_csv"], dtype=str)

    df_lang.fillna("", inplace=True)

    povinne_sloupce = list(dict.fromkeys(["Lekce", col_cizi, "Čeština", "Slovní druh"] + cfg["sloupce_csv"]))
    for col in povinne_sloupce:
        if col not in df_lang.columns:
            df_lang[col] = pd.Series(dtype="str")

    if cfg["ma_gramatiku"]:
        if "Konjugace" not in df_lang.columns:
            df_lang["Konjugace"] = pd.Series(dtype="str")
        if "Deklinace" not in df_lang.columns:
            df_lang["Deklinace"] = pd.Series(dtype="str")

        # Doplnění deklinace pro přídavná jména, pokud v datech chybí
        if len(df_lang) > 0:
            mask_adj_bez_dekl = (df_lang["Slovní druh"] == "Přídavné jméno") & (
                df_lang["Deklinace"].str.strip() == ""
            )
            if mask_adj_bez_dekl.any():
                for idx in df_lang[mask_adj_bez_dekl].index:
                    clean_parts = [p.strip().lower() for p in str(df_lang.loc[idx, col_cizi]).split(",")]
                    df_lang.loc[idx, "Deklinace"] = urci_deklinaci_adjektiva(clean_parts)

        konj = df_lang["Konjugace"].astype(str).str.strip()
        dekl = df_lang["Deklinace"].astype(str).str.strip()
        df_lang["konj./dekl."] = konj.where(konj != "", dekl)
    else:
        if "Rod" not in df_lang.columns:
            df_lang["Rod"] = pd.Series(dtype="str")

    df_lang["_Cizi_norm"] = pd.Series(
        [odstran_diakritiku(str(s).lower()) for s in df_lang[col_cizi].fillna("")],
        index=df_lang.index,
        dtype="str",
    )
    df_lang["_Cestina_norm"] = pd.Series(
        [odstran_diakritiku(str(s).lower()) for s in df_lang["Čeština"].fillna("")],
        index=df_lang.index,
        dtype="str",
    )
    return df_lang


def ziskej_data_jazyka(lang: str) -> pd.DataFrame | None:
    """Načte data pro daný jazyk (při chybějícím CSV automaticky založí prázdnou databázi)."""
    try:
        return nacti_data(lang)
    except Exception as e:
        csv_soubor = get_csv_path(lang)
        cfg = JAZYKY[lang]
        st.error(
            f"Chyba při načítání databáze '{csv_soubor}' pro jazyk {cfg['nazev']}: {e}"
        )
        return None


def uloz_data(novy_df: pd.DataFrame, lang: str = "latina") -> bool:
    """Uloží DataFrame do CSV daného jazyka a zneplatní cache."""
    cfg = JAZYKY[lang]
    csv_soubor = get_csv_path(lang)
    try:
        adresar = os.path.dirname(csv_soubor)
        if adresar:
            os.makedirs(adresar, exist_ok=True)
        sloupce = cfg["sloupce_csv"]
        df_k_zapisu = novy_df.copy()
        for col in sloupce:
            if col not in df_k_zapisu.columns:
                df_k_zapisu[col] = ""
        df_k_zapisu[sloupce].to_csv(csv_soubor, sep=";", index=False, encoding="utf-8")
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Chyba při ukládání do souboru '{csv_soubor}': {e}")
        return False


# ==============================================================================
# SPRÁVA KNIHOVNY BALÍČKŮ A ADMIN STATUS
# ==============================================================================
def nacti_knihovnu() -> list:
    """Načte seznam balíčků z knihovna_balicku.json."""
    if not os.path.exists(KNIHOVNA_JSON_PATH):
        return []
    try:
        with open(KNIHOVNA_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def uloz_knihovnu(knihovna: list) -> bool:
    """Uloží seznam balíčků do knihovna_balicku.json."""
    try:
        os.makedirs(os.path.dirname(KNIHOVNA_JSON_PATH), exist_ok=True)
        with open(KNIHOVNA_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(knihovna, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        st.error(f"Chyba při ukládání knihovny: {e}")
        return False


def smaz_balicek_z_knihovny(balicek_id: str) -> bool:
    """Odstraní balíček z knihovny včetně .apkg souboru na disku."""
    knihovna = nacti_knihovnu()
    novy_seznam = []
    soubor_ke_smazani = None
    for b in knihovna:
        if b.get("id") == balicek_id:
            soubor_ke_smazani = b.get("soubor")
        else:
            novy_seznam.append(b)

    if soubor_ke_smazani and os.path.exists(soubor_ke_smazani):
        try:
            os.remove(soubor_ke_smazani)
        except Exception:
            pass

    return uloz_knihovnu(novy_seznam)


# ==============================================================================
# SPRÁVA ŽÁDOSTÍ O PŘIDÁNÍ SLOVA (PRO KAŽDÝ JAZYK ZVLÁŠŤ)
# ==============================================================================
def nacti_zadosti(lang: str = "latina") -> list:
    """Načte seznam žádostí o přidání slov ze souboru JSON daného jazyka."""
    cesta = get_zadosti_path(lang)
    if not os.path.exists(cesta):
        return []
    try:
        with open(cesta, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def uloz_zadosti(zadosti: list, lang: str = "latina") -> bool:
    """Uloží seznam žádostí do souboru JSON daného jazyka."""
    cesta = get_zadosti_path(lang)
    try:
        os.makedirs(os.path.dirname(cesta), exist_ok=True)
        with open(cesta, "w", encoding="utf-8") as f:
            json.dump(zadosti, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        st.error(f"Chyba při ukládání žádostí: {e}")
        return False


def pridej_zadost(cizi_slovo: str, cestina: str, lekce: str, poznamka: str, lang: str = "latina") -> bool:
    """Přidá novou žádost o slovo pro daný jazyk."""
    zadosti = nacti_zadosti(lang)
    nova = {
        "id": uuid.uuid4().hex[:8],
        "slovo": cizi_slovo.strip(),
        "cestina": cestina.strip(),
        "lekce": lekce.strip(),
        "poznamka": poznamka.strip(),
        "stav": "cekajici",
        "datum": datetime.now().strftime("%d. %m. %Y %H:%M"),
    }
    if lang == "latina":
        nova["latina"] = cizi_slovo.strip()
    zadosti.insert(0, nova)
    return uloz_zadosti(zadosti, lang)


def zmen_stav_zadosti(zadost_id: str, novy_stav: str, lang: str = "latina") -> bool:
    """Změní stav žádosti (cekajici / vyrizeno / zamitnuto)."""
    zadosti = nacti_zadosti(lang)
    for z in zadosti:
        if z.get("id") == zadost_id:
            z["stav"] = novy_stav
            break
    return uloz_zadosti(zadosti, lang)


def smaz_zadost(zadost_id: str, lang: str = "latina") -> bool:
    """Smaže žádost ze seznamu daného jazyka."""
    zadosti = nacti_zadosti(lang)
    novy = [z for z in zadosti if z.get("id") != zadost_id]
    return uloz_zadosti(novy, lang)


def publikuj_balicek(
    nazev: str,
    popis: str,
    smer_cizi_ces: bool,
    smer_ces_cizi: bool,
    df_data: pd.DataFrame,
    lang: str = "latina",
) -> tuple[bool, str, str]:
    """Vygeneruje .apkg, uloží jej do data/balicky a zapíše metadata do sdílené knihovny."""
    cfg = JAZYKY[lang]
    col_cizi = cfg["sloupec"]

    os.makedirs(BALICKY_DIR, exist_ok=True)
    balicek_id = f"pkg_{int(time.time())}"
    bezpecny_nazev = "".join(c for c in nazev if c.isalnum() or c in (" ", "_", "-")).strip()
    if not bezpecny_nazev:
        bezpecny_nazev = "balicek"
    nazev_souboru = f"{bezpecny_nazev.replace(' ', '_')}_{balicek_id}.apkg"
    cesta_soubor = os.path.join(BALICKY_DIR, nazev_souboru)

    apkg_buf = vytvor_anki_balicek(df_data, nazev, smer_cizi_ces, smer_ces_cizi, lang=lang)
    try:
        with open(cesta_soubor, "wb") as f:
            f.write(apkg_buf.getvalue())
    except Exception as e:
        return False, f"Nepodařilo se uložit soubor balíčku: {e}", ""

    pocet_slov = len(df_data)
    pocet_karet = pocet_slov * (int(smer_cizi_ces) + int(smer_ces_cizi))
    datum_str = datetime.now().strftime("%d. %m. %Y %H:%M")

    karticky_list = []
    for _, r in df_data.iterrows():
        polozka = {
            "Lekce": str(r.get("Lekce", "")),
            col_cizi: str(r.get(col_cizi, "")),
            "Čeština": str(r.get("Čeština", "")),
            "Slovní druh": str(r.get("Slovní druh", "")),
        }
        if cfg["ma_gramatiku"]:
            polozka["konj./dekl."] = str(r.get("konj./dekl.", ""))
        karticky_list.append(polozka)

    zaznam = {
        "id": balicek_id,
        "jazyk": lang,
        "nazev": nazev,
        "popis": popis.strip(),
        "datum": datum_str,
        "pocet_slovicek": pocet_slov,
        "pocet_karticek": pocet_karet,
        "smer_lat_ces": smer_cizi_ces,
        "smer_ces_lat": smer_ces_cizi,
        "soubor": cesta_soubor,
        "nazev_souboru": f"{bezpecny_nazev.replace(' ', '_')}.apkg",
        "karticky": karticky_list,
    }

    knihovna = nacti_knihovnu()
    knihovna.insert(0, zaznam)
    if uloz_knihovnu(knihovna):
        return True, "Balíček byl úspěšně publikován do knihovny.", cesta_soubor
    return False, "Chyba při zápisu do databáze knihovny.", ""

def _ocisti_anki_html(text: str) -> str:
    """Odstraní HTML tagy a Anki značky ([sound:...]) z pole kartičky."""
    if not text:
        return ""
    bez_zvuku = re.sub(r"\[sound:[^\]]+\]", "", text)
    bez_tagu = re.sub(r"<[^>]+>", " ", bez_zvuku)
    return re.sub(r"\s+", " ", html.unescape(bez_tagu)).strip()


def analyzuj_apkg_soubor(file_bytes: bytes) -> dict:
    """
    Zkontroluje, zda jde o platný .apkg (ZIP) archiv, a pokusí se z jeho SQLite databáze
    vyčíst název balíčku, počet karet, počet poznámek a náhled kartiček.
    """
    vysledek = {
        "platny": False,
        "nazev_balicku": "",
        "pocet_slovicek": 0,
        "pocet_karticek": 0,
        "karticky": [],
        "zstd_format": False,
    }

    if not file_bytes or not zipfile.is_zipfile(io.BytesIO(file_bytes)):
        return vysledek

    tmp_db_path = None
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
            names = zf.namelist()
            if "collection.anki21b" in names:
                vysledek["zstd_format"] = True

            db_member = None
            for candidate in ("collection.anki21", "collection.anki2"):
                if candidate in names:
                    db_member = candidate
                    break

            vysledek["platny"] = True
            if not db_member:
                return vysledek

            with tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite") as tmp_f:
                tmp_f.write(zf.read(db_member))
                tmp_db_path = tmp_f.name

        conn = sqlite3.connect(tmp_db_path)
        try:
            cur = conn.cursor()

            # Název balíčku z tabulky col (JSON sloupec decks)
            try:
                cur.execute("SELECT decks FROM col LIMIT 1")
                row_col = cur.fetchone()
                if row_col and row_col[0]:
                    decks_json = json.loads(row_col[0])
                    for d_info in decks_json.values():
                        d_name = str(d_info.get("name", "")).strip()
                        if d_name and d_name.lower() != "default":
                            vysledek["nazev_balicku"] = d_name
                            break
            except Exception:
                pass

            # Počet karet a poznámek + náhled prvních 500 záznamů
            cur.execute("SELECT COUNT(*) FROM cards")
            cards_count = int(cur.fetchone()[0] or 0)

            cur.execute("SELECT COUNT(*) FROM notes")
            notes_count = int(cur.fetchone()[0] or 0)

            cur.execute("SELECT flds FROM notes LIMIT 500")
            rows_notes = cur.fetchall()

            # Detekce zástupného upozornění moderního Anki (.anki21b)
            if (
                notes_count == 1
                and rows_notes
                and "please update to the latest anki version" in str(rows_notes[0][0]).lower()
            ):
                vysledek["zstd_format"] = True
                return vysledek

            vysledek["pocet_karticek"] = cards_count
            vysledek["pocet_slovicek"] = notes_count

            nahled = []
            for (flds_raw,) in rows_notes:
                casti = [_ocisti_anki_html(p) for p in str(flds_raw or "").split("\x1f")]
                casti = [c for c in casti if c]
                if len(casti) >= 2:
                    nahled.append({"predni": casti[0], "zadni": casti[1]})
                elif len(casti) == 1:
                    nahled.append({"predni": casti[0], "zadni": ""})
            vysledek["karticky"] = nahled
        finally:
            conn.close()
    except Exception:
        pass
    finally:
        if tmp_db_path and os.path.exists(tmp_db_path):
            try:
                os.remove(tmp_db_path)
            except Exception:
                pass

    return vysledek


def uloz_nahrany_apkg_do_knihovny(
    file_bytes: bytes,
    puvodni_nazev_souboru: str,
    nazev: str,
    popis: str,
    lang: str,
    pocet_slovicek: int,
    pocet_karticek: int,
    surove_karticky: list | None = None,
) -> tuple[bool, str]:
    """Uloží nahraný .apkg soubor beze změny do data/balicky a zapíše ho do knihovna_balicku.json."""
    cfg = JAZYKY.get(lang, JAZYKY["latina"])
    col_cizi = cfg["sloupec"]

    os.makedirs(BALICKY_DIR, exist_ok=True)
    balicek_id = f"pkg_{int(time.time())}"
    bezpecny_nazev = "".join(c for c in nazev if c.isalnum() or c in (" ", "_", "-")).strip()
    if not bezpecny_nazev:
        bezpecny_nazev = Path(puvodni_nazev_souboru).stem or "balicek"

    interni_soubor = f"{bezpecny_nazev.replace(' ', '_')}_{balicek_id}.apkg"
    cesta_soubor = os.path.join(BALICKY_DIR, interni_soubor)

    try:
        with open(cesta_soubor, "wb") as f:
            f.write(file_bytes)
    except Exception as e:
        return False, f"Nepodařilo se uložit soubor balíčku: {e}"

    karticky_list = []
    for item in surove_karticky or []:
        polozka = {
            "Lekce": "",
            col_cizi: str(item.get("predni", "")),
            "Čeština": str(item.get("zadni", "")),
            "Slovní druh": "",
        }
        if cfg["ma_gramatiku"]:
            polozka["konj./dekl."] = ""
        karticky_list.append(polozka)

    zaznam = {
        "id": balicek_id,
        "jazyk": lang,
        "nazev": nazev.strip(),
        "popis": popis.strip(),
        "datum": datetime.now().strftime("%d. %m. %Y %H:%M"),
        "pocet_slovicek": int(pocet_slovicek),
        "pocet_karticek": int(pocet_karticek),
        "smer_lat_ces": True,
        "smer_ces_lat": False,
        "soubor": cesta_soubor,
        "nazev_souboru": f"{bezpecny_nazev.replace(' ', '_')}.apkg",
        "karticky": karticky_list,
    }

    knihovna = nacti_knihovnu()
    knihovna.insert(0, zaznam)
    if uloz_knihovnu(knihovna):
        return True, f"Balíček \u201e{nazev.strip()}\u201c byl úspěšně nahrán do knihovny."
    return False, "Chyba při zápisu do databáze knihovny."


def reset_upload_apkg_state():
    st.session_state["lib_upload_apkg_aktivni"] = False
    for k in [
        "lib_up_last_file_id",
        "lib_up_uploader",
        "lib_up_nazev",
        "lib_up_jazyk",
        "lib_up_popis",
        "lib_up_pocet_slov",
        "lib_up_pocet_karet",
        "lib_up_karticky",
        "lib_up_parse_info",
    ]:
        st.session_state.pop(k, None)


@st.dialog("Nahrát .apkg soubor do knihovny", on_dismiss=reset_upload_apkg_state, width="medium")
def dialog_nahrat_apkg():
    st.write("Nahrajte hotový Anki balíček (`.apkg`) a vyplňte jeho název, jazyk a popis pro zobrazení v knihovně.")

    uploaded_apkg = st.file_uploader(
        "Soubor balíčku (.apkg) *",
        type=["apkg"],
        key="lib_up_uploader",
    )

    if uploaded_apkg is not None:
        file_id = f"{uploaded_apkg.name}_{uploaded_apkg.size}"
        if st.session_state.get("lib_up_last_file_id") != file_id:
            info = analyzuj_apkg_soubor(uploaded_apkg.getvalue())
            st.session_state["lib_up_last_file_id"] = file_id
            st.session_state["lib_up_parse_info"] = info

            vychozi_nazev = info["nazev_balicku"] or Path(uploaded_apkg.name).stem.replace("_", " ").strip()
            st.session_state["lib_up_nazev"] = vychozi_nazev
            st.session_state["lib_up_pocet_slov"] = info["pocet_slovicek"]
            st.session_state["lib_up_pocet_karet"] = info["pocet_karticek"]
            st.session_state["lib_up_karticky"] = info["karticky"]

    parse_info = st.session_state.get("lib_up_parse_info")
    if uploaded_apkg is not None and parse_info:
        if not parse_info["platny"]:
            st.error("Nahraný soubor není platný archiv .apkg.")
        elif parse_info["zstd_format"] and parse_info["pocet_karticek"] == 0:
            st.info(
                "Balíček je v novějším komprimovaném formátu Anki (.anki21b) — počty slovíček a karet můžete doplnit ručně níže.",
                icon=":material/info:",
            )
        elif parse_info["pocet_karticek"] > 0:
            st.caption(
                f"Ze souboru automaticky načteno: **{parse_info['pocet_slovicek']}** slovíček / poznámek, "
                f"**{parse_info['pocet_karticek']}** karet."
            )

    jazyk_volby = {cfg["nazev"]: kod for kod, cfg in JAZYKY.items()}
    vybrany_jazyk_nazev = st.segmented_control(
        "Jazyk balíčku *",
        options=list(jazyk_volby.keys()),
        default=list(jazyk_volby.keys())[0],
        key="lib_up_jazyk",
    )

    nazev = st.text_input(
        "Název balíčku *",
        placeholder="Např. Latina – Lekce 1 až 5",
        key="lib_up_nazev",
    )

    popis = st.text_area(
        "Popis balíčku (volitelný)",
        placeholder="Např. Kompletní slovní zásoba k zápočtu včetně rodů a genitivů...",
        key="lib_up_popis",
    )

    col_p1, col_p2 = st.columns(2)
    with col_p1:
        pocet_slov = st.number_input(
            "Počet slovíček:",
            min_value=0,
            step=1,
            key="lib_up_pocet_slov",
        )
    with col_p2:
        pocet_karet = st.number_input(
            "Počet karet v balíčku:",
            min_value=0,
            step=1,
            key="lib_up_pocet_karet",
        )

    st.markdown("<small>* Označená pole jsou povinná.</small>", unsafe_allow_html=True)
    with st.container(horizontal=True):
        tlacitko_nahrat = st.button(
            "Nahrát do knihovny",
            type="primary",
            icon=":material/upload:",
            key="btn_lib_up_save",
        )
        tlacitko_zrusit = st.button("Zrušit", key="btn_lib_up_cancel")

    if tlacitko_zrusit:
        reset_upload_apkg_state()
        st.rerun()

    if tlacitko_nahrat:
        if uploaded_apkg is None:
            st.error("Nejprve vyberte soubor `.apkg`.")
        elif parse_info and not parse_info["platny"]:
            st.error("Vybraný soubor není platný balíček `.apkg`.")
        elif not nazev.strip():
            st.error("Vyplňte název balíčku.")
        else:
            vybrany_lang_kod = jazyk_volby.get(vybrany_jazyk_nazev or "", "latina")
            ok, msg = uloz_nahrany_apkg_do_knihovny(
                file_bytes=uploaded_apkg.getvalue(),
                puvodni_nazev_souboru=uploaded_apkg.name,
                nazev=nazev,
                popis=popis,
                lang=vybrany_lang_kod,
                pocet_slovicek=int(pocet_slov),
                pocet_karticek=int(pocet_karet),
                surove_karticky=st.session_state.get("lib_up_karticky", []),
            )
            if ok:
                st.session_state["lib_zprava"] = msg
                reset_upload_apkg_state()
                st.rerun()
            else:
                st.error(msg)

def zobraz_sidebar_admin_status():
    """Zobrazí v postranním panelu informaci o přihlášení správce, pokud je aktivní."""
    if st.session_state.admin_prihlasen:
        st.sidebar.success("Přihlášen jako správce", icon=":material/verified_user:")
        if st.sidebar.button("Odhlásit se", icon=":material/logout:", key="global_sidebar_logout"):
            st.session_state.admin_prihlasen = False
            for lang_kod in JAZYKY:
                st.session_state[f"{lang_kod}_admin_selected_rows"] = []
            st.rerun()
        st.sidebar.markdown("---")


# ==============================================================================
# POMOCNÉ FUNKCE PRO CALLBACKY A STAVY DIALOGŮ
# ==============================================================================
def reset_pridat_state(lang: str | None = None):
    langs = [lang] if lang else list(JAZYKY.keys())
    for l in langs:
        st.session_state[f"{l}_pridat_slovo_aktivni"] = False
        st.session_state.pop(f"{l}_schvalovana_zadost_id", None)
        for k in [
            "dlg_add_slovo",
            "dlg_add_cestina",
            "dlg_add_nova_lekce",
            "dlg_add_vyber_lekce",
            "dlg_add_druh",
            "dlg_add_konj",
            "dlg_add_dekl",
            "dlg_add_msg",
        ]:
            st.session_state.pop(f"{l}_{k}", None)


def reset_edit_state(lang: str | None = None):
    langs = [lang] if lang else list(JAZYKY.keys())
    for l in langs:
        st.session_state[f"{l}_edit_row_idx"] = None
        for k in [
            "dlg_edit_slovo",
            "dlg_edit_cestina",
            "dlg_edit_nova_lekce",
            "dlg_edit_vyber_lekce",
            "dlg_edit_druh",
            "dlg_edit_konj",
            "dlg_edit_dekl",
            "dlg_edit_msg",
            "dlg_edit_initialized_idx",
        ]:
            st.session_state.pop(f"{l}_{k}", None)


def reset_delete_state(lang: str | None = None):
    langs = [lang] if lang else list(JAZYKY.keys())
    for l in langs:
        st.session_state[f"{l}_delete_row_idx"] = None


def reset_bulk_delete_state(lang: str | None = None):
    langs = [lang] if lang else list(JAZYKY.keys())
    for l in langs:
        st.session_state[f"{l}_bulk_delete_active"] = False


def reset_generator_balicek_state(lang: str | None = None):
    langs = [lang] if lang else list(JAZYKY.keys())
    for l in langs:
        st.session_state[f"{l}_generator_balicek_dialog"] = False


def dopln_gramatiku_v_df(
    df: pd.DataFrame, lang: str = "latina", prepis_existujici: bool = False
) -> pd.DataFrame:
    """
    Automaticky doplní mluvnické kategorie (Slovní druh, Konjugace, Deklinace)
    pro jazyky s podporou gramatické analýzy (latina).
    """
    cfg = JAZYKY[lang]
    if not cfg["ma_gramatiku"] or df is None or df.empty:
        return df

    col_cizi = cfg["sloupec"]
    res = df.copy()
    for col in ["Slovní druh", "Konjugace", "Deklinace"]:
        if col not in res.columns:
            res[col] = ""
        res[col] = res[col].fillna("").astype(str).str.strip()

    for idx, row in res.iterrows():
        lat = str(row.get(col_cizi, "")).strip()
        if not lat:
            continue

        cur_druh = str(row.get("Slovní druh", "")).strip()
        cur_konj = str(row.get("Konjugace", "")).strip()
        cur_dekl = str(row.get("Deklinace", "")).strip()

        if prepis_existujici or not cur_druh:
            druh, konj, dekl = analyzuj_slovicko(lat)
            res.at[idx, "Slovní druh"] = druh
            res.at[idx, "Konjugace"] = konj if druh == "Sloveso" else ""
            res.at[idx, "Deklinace"] = (
                dekl if druh in ["Podstatné jméno", "Přídavné jméno"] else ""
            )
        else:
            if cur_druh == "Sloveso" and not cur_konj:
                _, konj, _ = analyzuj_slovicko(lat)
                if konj:
                    res.at[idx, "Konjugace"] = konj
            elif cur_druh in ["Podstatné jméno", "Přídavné jméno"] and not cur_dekl:
                _, _, dekl = analyzuj_slovicko(lat)
                if not dekl and cur_druh == "Přídavné jméno":
                    clean_parts = [p.strip().lower() for p in lat.split(",")]
                    dekl = urci_deklinaci_adjektiva(clean_parts)
                if dekl:
                    res.at[idx, "Deklinace"] = dekl

    return res


def reset_import_csv_state(lang: str | None = None):
    langs = [lang] if lang else list(JAZYKY.keys())
    for l in langs:
        st.session_state[f"{l}_import_csv_aktivni"] = False
        for k in [
            "csv_import_data",
            "csv_import_last_file_id",
            "csv_import_uploader",
            "csv_import_vychozi_lekce",
            "csv_import_dupl_rezim",
            "csv_import_editor_ver",
        ]:
            st.session_state.pop(f"{l}_{k}", None)
        for key_name in list(st.session_state.keys()):
            if key_name.startswith(f"{l}_csv_import_data_editor"):
                st.session_state.pop(key_name, None)


def reset_zadost_state(lang: str | None = None):
    langs = [lang] if lang else list(JAZYKY.keys())
    for l in langs:
        st.session_state.pop(f"{l}_zadost_dialog_aktivni", None)
        for k in ["dlg_zadost_slovo", "dlg_zadost_cestina", "dlg_zadost_lekce", "dlg_zadost_poznamka"]:
            st.session_state.pop(f"{l}_{k}", None)


def dopln_mluvnicke_kategorie_do_state(slovo_key: str, druh_key: str, konj_key: str, dekl_key: str, msg_key: str):
    lat = st.session_state.get(slovo_key, "").strip()
    if not lat:
        st.session_state[msg_key] = ("warning", "⚠️ Nejprve zadejte slovníkový tvar do pole 'Latina'.")
        return
    druh, konj, dekl = analyzuj_slovicko(lat)
    st.session_state[druh_key] = druh
    st.session_state[konj_key] = konj
    st.session_state[dekl_key] = dekl

    detaily = [druh]
    if konj:
        detaily.append(f"{konj}. konjugace" if konj != "nepravidelná" else "nepravidelné sloveso")
    if dekl:
        if dekl == "nesklonná":
            detaily.append("nesklonné podst. jméno")
        elif "., " in dekl or dekl.endswith("."):
            detaily.append(f"{dekl} deklinace")
        else:
            detaily.append(f"{dekl}. deklinace")
    st.session_state[msg_key] = ("success", f"Mluvnické kategorie doplněny: {', '.join(detaily)}")


def on_add_doplnit_kategorie(lang: str):
    dopln_mluvnicke_kategorie_do_state(
        f"{lang}_dlg_add_slovo",
        f"{lang}_dlg_add_druh",
        f"{lang}_dlg_add_konj",
        f"{lang}_dlg_add_dekl",
        f"{lang}_dlg_add_msg",
    )


def on_edit_doplnit_kategorie(lang: str):
    dopln_mluvnicke_kategorie_do_state(
        f"{lang}_dlg_edit_slovo",
        f"{lang}_dlg_edit_druh",
        f"{lang}_dlg_edit_konj",
        f"{lang}_dlg_edit_dekl",
        f"{lang}_dlg_edit_msg",
    )


def on_admin_edit_click(lang: str):
    click = st.session_state.get(f"{lang}_btn_admin_edit")
    if click is not None and hasattr(click, "row"):
        st.session_state[f"{lang}_edit_row_idx"] = click.row
    elif click is not None and isinstance(click, dict) and "row" in click:
        st.session_state[f"{lang}_edit_row_idx"] = click["row"]


def on_admin_delete_click(lang: str):
    click = st.session_state.get(f"{lang}_btn_admin_delete")
    if click is not None and hasattr(click, "row"):
        st.session_state[f"{lang}_delete_row_idx"] = click.row
    elif click is not None and isinstance(click, dict) and "row" in click:
        st.session_state[f"{lang}_delete_row_idx"] = click["row"]


# ==============================================================================
# MODÁLNÍ DIALOGY ADMINISTRACE A GENERÁTORU
# ==============================================================================
@st.dialog("Požádat o přidání chybějícího slova", on_dismiss=reset_zadost_state)
def dialog_zadost_o_slovo(lang: str, aktualni_df: pd.DataFrame):
    cfg = JAZYKY[lang]
    col_cizi = cfg["sloupec"]

    st.markdown(
        "Pokud v databázi chybí slovíčko, které potřebujete, "
        "vyplňte formulář níže a správce jej posoudí."
    )

    existujici_lekce = sorted(list(set(aktualni_df["Lekce"].unique()) - {""}), key=klic_serazeni_lekci)
    lekce_options = ["(neurčeno)"] + existujici_lekce

    nove_slovo = st.text_input(
        f"{col_cizi} *",
        placeholder=cfg["placeholder_slovo"],
        help=cfg["help_slovo"],
        key=f"{lang}_dlg_zadost_slovo",
    )
    nova_cestina = st.text_input(
        "Čeština (volitelné)",
        placeholder="např. vítězství" if lang == "latina" else "např. slovo",
        help="Český překlad, pokud ho znáte",
        key=f"{lang}_dlg_zadost_cestina",
    )
    vyber_lekce = st.selectbox(
        "Lekce (volitelné):",
        options=lekce_options,
        key=f"{lang}_dlg_zadost_lekce",
        help="Vyberte lekci, do které by slovo mělo patřit",
    )
    poznamka = st.text_area(
        "Poznámka (volitelné)",
        placeholder="Další kontext, kde jste na slovo narazili...",
        key=f"{lang}_dlg_zadost_poznamka",
        max_chars=300,
    )

    with st.container(horizontal=True):
        tlacitko_odeslat = st.button(
            "Odeslat žádost", type="primary", icon=":material/send:", key=f"{lang}_btn_zadost_odeslat"
        )
        tlacitko_zrusit = st.button("Zrušit", key=f"{lang}_btn_zadost_zrusit")

    if tlacitko_zrusit:
        reset_zadost_state(lang)
        st.rerun()

    if tlacitko_odeslat:
        slovo_clean = nove_slovo.strip()
        if not slovo_clean:
            st.error(f"Pole '{col_cizi}' je povinné.")
        else:
            lekce_val = "" if vyber_lekce == "(neurčeno)" else vyber_lekce
            if pridej_zadost(slovo_clean, nova_cestina.strip(), lekce_val, poznamka.strip(), lang=lang):
                st.session_state[f"{lang}_uzivatel_zprava"] = (
                    f"Žádost o slovo \u201e{slovo_clean}\u201c byla úspěšně odeslána. Děkujeme!"
                )
                reset_zadost_state(lang)
                st.rerun()


@st.dialog("Přidat nové slovíčko", on_dismiss=reset_pridat_state, width="large")
def dialog_pridat_slovo(lang: str, aktualni_df: pd.DataFrame):
    cfg = JAZYKY[lang]
    col_cizi = cfg["sloupec"]
    ma_gramatiku = cfg["ma_gramatiku"]

    existujici_lekce = sorted(list(set(aktualni_df["Lekce"].unique()) - {""}), key=klic_serazeni_lekci)
    if not existujici_lekce:
        existujici_lekce = ["1"]

    if ma_gramatiku:
        druhy_moznosti = list(cfg["druhy_vychozi"])
        st.session_state.setdefault(f"{lang}_dlg_add_druh", "Podstatné jméno")
        st.session_state.setdefault(f"{lang}_dlg_add_konj", "")
        st.session_state.setdefault(f"{lang}_dlg_add_dekl", "")
    else:
        druhy_z_dat = {str(d).strip() for d in aktualni_df["Slovní druh"].unique() if str(d).strip()}
        druhy_moznosti = sorted(list(druhy_z_dat | set(cfg["druhy_vychozi"])))
        st.session_state.setdefault(f"{lang}_dlg_add_druh", druhy_moznosti[0] if druhy_moznosti else "")

    msg_key = f"{lang}_dlg_add_msg"
    if msg_key in st.session_state:
        m_t, m_v = st.session_state.pop(msg_key)
        if m_t == "success":
            st.success(m_v, icon=":material/auto_fix_high:")
        else:
            st.warning(m_v)

    col_lekce1, col_lekce2 = st.columns(2)
    with col_lekce1:
        vyber_lekce = st.selectbox(
            "Existující lekce:", options=existujici_lekce, key=f"{lang}_dlg_add_vyber_lekce"
        )
    with col_lekce2:
        nova_lekce = st.text_input(
            "Nebo zadejte nové číslo/název lekce:",
            placeholder="např. 6 (ponechte prázdné pro existující)",
            key=f"{lang}_dlg_add_nova_lekce",
        )

    col_cizi_box, col_ces = st.columns(2)
    with col_cizi_box:
        nove_slovo = st.text_input(
            f"{col_cizi} *",
            placeholder=cfg["placeholder_slovo"],
            help=cfg["help_slovo"],
            key=f"{lang}_dlg_add_slovo",
        )
    with col_ces:
        nova_cestina = st.text_input(
            "Čeština *",
            placeholder="např. vítězství" if lang == "latina" else "např. slovo",
            help="Český překlad",
            key=f"{lang}_dlg_add_cestina",
        )

    nova_konj = ""
    nova_dekl = ""
    if ma_gramatiku:
        st.button(
            "Doplnit mluvnické kategorie",
            icon=":material/auto_fix_high:",
            on_click=on_add_doplnit_kategorie,
            args=(lang,),
            key=f"{lang}_btn_add_autofill",
            help="Automaticky určí slovní druh, konjugaci nebo deklinaci ze zadaného latinského tvaru podle pravidel v druhy.py",
        )

        konj_moznosti = ["", "1", "2", "3", "4", "nepravidelná"]
        dekl_moznosti = ["", "1", "2", "3", "4", "5", "1., 2.", "nesklonná"]

        col_druh, col_gram1, col_gram2 = st.columns(3)
        with col_druh:
            novy_druh = st.selectbox(
                "Slovní druh *",
                options=druhy_moznosti,
                key=f"{lang}_dlg_add_druh",
            )
        with col_gram1:
            nova_konj = st.selectbox(
                "Konjugace (pro slovesa):",
                options=konj_moznosti,
                help="Vyplňte pouze u sloves",
                key=f"{lang}_dlg_add_konj",
            )
        with col_gram2:
            nova_dekl = st.selectbox(
                "Deklinace (podst. a příd. jména):",
                options=dekl_moznosti,
                help="Vyplňte pro podstatná nebo přídavná jména",
                key=f"{lang}_dlg_add_dekl",
            )
    else:
        novy_druh = st.selectbox(
            "Slovní druh *",
            options=druhy_moznosti,
            accept_new_options=True,
            help="Vyberte ze seznamu nebo zadejte vlastní slovní druh a potvrďte Enterem",
            key=f"{lang}_dlg_add_druh",
        )

    st.markdown("<small>* Označená pole jsou povinná.</small>", unsafe_allow_html=True)
    with st.container(horizontal=True):
        tlacitko_ulozit = st.button(
            "Uložit kartičku",
            type="primary",
            icon=":material/add_circle:",
            key=f"{lang}_btn_add_save",
        )
        tlacitko_zrusit = st.button("Zrušit", key=f"{lang}_btn_add_cancel")

    if tlacitko_zrusit:
        reset_pridat_state(lang)
        st.rerun()

    if tlacitko_ulozit:
        finalni_lekce = nova_lekce.strip() if nova_lekce.strip() else vyber_lekce.strip()
        slovo_clean = nove_slovo.strip()
        cestina_clean = nova_cestina.strip()
        druh_clean = str(novy_druh or "").strip()

        if not finalni_lekce:
            st.error("Vyberte nebo zadejte číslo lekce.")
        elif not slovo_clean:
            st.error(f"Pole '{col_cizi}' je povinné.")
        elif not cestina_clean:
            st.error("Pole 'Čeština' je povinné.")
        elif not druh_clean:
            st.error("Pole 'Slovní druh' je povinné.")
        else:
            duplicita = aktualni_df[
                (aktualni_df[col_cizi].str.lower() == slovo_clean.lower())
                & (aktualni_df["Lekce"] == finalni_lekce)
            ]
            if len(duplicita) > 0:
                st.warning(f"Upozornění: Slovíčko '{slovo_clean}' v lekci {finalni_lekce} již v databázi existuje.")

            if ma_gramatiku:
                novy_dict = {
                    "Lekce": finalni_lekce,
                    col_cizi: slovo_clean,
                    "Čeština": cestina_clean,
                    "Slovní druh": druh_clean,
                    "Konjugace": nova_konj if druh_clean == "Sloveso" else "",
                    "Deklinace": nova_dekl if druh_clean in ["Podstatné jméno", "Přídavné jméno"] else "",
                }
            else:
                novy_dict = {
                    "Lekce": finalni_lekce,
                    col_cizi: slovo_clean,
                    "Čeština": cestina_clean,
                    "Slovní druh": druh_clean,
                    "Rod": "",
                }

            novy_zaznam = pd.DataFrame([novy_dict])
            df_nove = pd.concat([aktualni_df, novy_zaznam], ignore_index=True)
            if uloz_data(df_nove, lang=lang):
                schvalovana_id = st.session_state.pop(f"{lang}_schvalovana_zadost_id", None)
                if schvalovana_id:
                    zmen_stav_zadosti(schvalovana_id, "vyrizeno", lang=lang)
                st.session_state[f"{lang}_admin_zprava"] = (
                    f"Kartička '{slovo_clean}' byla úspěšně přidána do lekce {finalni_lekce}."
                )
                reset_pridat_state(lang)
                st.rerun()


@st.dialog("Importovat slovíčka z CSV", on_dismiss=reset_import_csv_state, width="large")
def dialog_import_csv(lang: str, aktualni_df: pd.DataFrame):
    cfg = JAZYKY[lang]
    col_cizi = cfg["sloupec"]
    ma_gramatiku = cfg["ma_gramatiku"]

    if ma_gramatiku:
        st.write(
            "Nahrajte CSV soubor obsahující **latinský slovníkový tvar; český překlad; číslo lekce**. "
            "Mluvnické kategorie (slovní druh, deklinace, konjugace) budou automaticky určeny a doplněny. "
            "Před přidáním do slovníku můžete v tabulce libovolná slovíčka zkontrolovat a upravit."
        )
    else:
        st.write(
            "Nahrajte CSV soubor obsahující **řecký slovníkový tvar; český překlad; číslo lekce** "
            "(případně i sloupec **Slovní druh** a **Rod**). "
            "Před přidáním do slovníku můžete v tabulce libovolná slovíčka zkontrolovat a upravit."
        )

    col_cfg1, col_cfg2 = st.columns([1, 1])
    with col_cfg1:
        vychozi_lekce = st.text_input(
            "Výchozí číslo lekce (pokud v souboru chybí):",
            value=st.session_state.get(f"{lang}_csv_import_vychozi_lekce", "1"),
            key=f"{lang}_csv_import_vychozi_lekce",
        )
    with col_cfg2:
        duplicity_rezim = st.selectbox(
            "Pokud slovíčko již v dané lekci ve slovníku existuje:",
            options=["Přeskočit existující v dané lekci", "Povolit duplicity"],
            key=f"{lang}_csv_import_dupl_rezim",
        )

    uploaded_file = st.file_uploader(
        "Vyberte CSV soubor:",
        type=["csv", "txt"],
        key=f"{lang}_csv_import_uploader",
        help="Podporované oddělovače: středník (;), čárka (,), tabulátor.",
    )

    if uploaded_file is not None:
        file_id = f"{lang}_{uploaded_file.name}_{uploaded_file.size}_{vychozi_lekce}"
        if st.session_state.get(f"{lang}_csv_import_last_file_id") != file_id:
            df_parsed = parsuj_import_csv(uploaded_file.getvalue(), vychozi_lekce, lang=lang)
            if ma_gramatiku:
                df_parsed = dopln_gramatiku_v_df(df_parsed, lang=lang, prepis_existujici=False)
            st.session_state[f"{lang}_csv_import_data"] = df_parsed
            st.session_state[f"{lang}_csv_import_last_file_id"] = file_id
            st.session_state[f"{lang}_csv_import_editor_ver"] = (
                st.session_state.get(f"{lang}_csv_import_editor_ver", 0) + 1
            )

    df_k_uprave = st.session_state.get(f"{lang}_csv_import_data")

    if df_k_uprave is not None:
        if df_k_uprave.empty:
            st.warning("V nahraném souboru nebyla nalezena žádná platná slovíčka.")
        else:
            st.info(
                f"📋 Načteno **{len(df_k_uprave)}** slovíček. V tabulce můžete libovolnou hodnotu upravit "
                "nebo nevhodné řádky smazat před přidáním do slovníku:"
            )

            if ma_gramatiku:
                druhy_v_datech = [
                    str(d).strip()
                    for d in df_k_uprave.get("Slovní druh", pd.Series(dtype=str)).unique()
                    if str(d).strip()
                ]
                konj_v_datech = [
                    str(k).strip()
                    for k in df_k_uprave.get("Konjugace", pd.Series(dtype=str)).unique()
                    if str(k).strip()
                ]
                dekl_v_datech = [
                    str(d).strip()
                    for d in df_k_uprave.get("Deklinace", pd.Series(dtype=str)).unique()
                    if str(d).strip()
                ]

                druhy_moznosti = list(dict.fromkeys([""] + list(cfg["druhy_vychozi"]) + druhy_v_datech))
                konj_moznosti = list(
                    dict.fromkeys(["", "1", "2", "3", "4", "nepravidelná"] + konj_v_datech)
                )
                dekl_moznosti = list(
                    dict.fromkeys(
                        ["", "1", "2", "3", "4", "5", "1., 2.", "nesklonná"] + dekl_v_datech
                    )
                )

                col_cfg_editor = {
                    "Lekce": st.column_config.TextColumn("Lekce", required=True, width="small"),
                    "Latina": st.column_config.TextColumn("Latina", required=True, width="medium"),
                    "Čeština": st.column_config.TextColumn("Čeština", required=True, width="medium"),
                    "Slovní druh": st.column_config.SelectboxColumn(
                        "Slovní druh", options=druhy_moznosti, width="medium"
                    ),
                    "konj./dekl.": None,
                    "Konjugace": st.column_config.SelectboxColumn(
                        "Konjugace (slovesa)", options=konj_moznosti, width="small"
                    ),
                    "Deklinace": st.column_config.SelectboxColumn(
                        "Deklinace (podst. a příd. jména)", options=dekl_moznosti, width="small"
                    ),
                }
            else:
                druhy_z_dat = {str(d).strip() for d in aktualni_df["Slovní druh"].unique() if str(d).strip()}
                druhy_import = {str(d).strip() for d in df_k_uprave["Slovní druh"].unique() if str(d).strip()}
                druhy_moznosti = [""] + sorted(list(druhy_z_dat | set(cfg["druhy_vychozi"]) | druhy_import))
                col_cfg_editor = {
                    "Lekce": st.column_config.TextColumn("Lekce", required=True, width="small"),
                    "Řečtina": st.column_config.TextColumn("Řečtina", required=True, width="medium"),
                    "Čeština": st.column_config.TextColumn("Čeština", required=True, width="medium"),
                    "Slovní druh": st.column_config.SelectboxColumn(
                        "Slovní druh", options=druhy_moznosti, width="medium"
                    ),
                    "Rod": None,
                }

            editor_ver = st.session_state.get(f"{lang}_csv_import_editor_ver", 0)
            edited_df = st.data_editor(
                df_k_uprave,
                num_rows="dynamic",
                width="stretch",
                column_config=col_cfg_editor,
                key=f"{lang}_csv_import_data_editor_{editor_ver}",
            )

            mask_valid = (edited_df[col_cizi].astype(str).str.strip() != "") & (
                edited_df["Čeština"].astype(str).str.strip() != ""
            )
            pocet_platnych = int(mask_valid.sum())

            with st.container(horizontal=True):
                tlacitko_import = st.button(
                    f"Přidat do slovníku ({pocet_platnych} slov)",
                    type="primary",
                    icon=":material/library_add:",
                    key=f"{lang}_btn_csv_import_save",
                    disabled=(pocet_platnych == 0),
                )
                if ma_gramatiku:
                    if st.button(
                        "Doplnit mluvnické kategorie",
                        icon=":material/auto_fix_high:",
                        key=f"{lang}_btn_csv_import_autofill",
                        help="Automaticky doplní chybějící slovní druh, konjugaci a deklinaci u řádků v tabulce",
                    ):
                        st.session_state[f"{lang}_csv_import_data"] = dopln_gramatiku_v_df(
                            edited_df, lang=lang, prepis_existujici=False
                        )
                        st.session_state[f"{lang}_csv_import_editor_ver"] = editor_ver + 1
                        st.rerun()
                tlacitko_zrusit = st.button("Zrušit", key=f"{lang}_btn_csv_import_cancel")

            if tlacitko_zrusit:
                reset_import_csv_state(lang)
                st.rerun()

            if tlacitko_import:
                df_ciste = edited_df[mask_valid].copy()
                if ma_gramatiku:
                    df_ciste = dopln_gramatiku_v_df(df_ciste, lang=lang, prepis_existujici=False)

                for col in cfg["sloupce_csv"]:
                    if col not in df_ciste.columns:
                        df_ciste[col] = ""
                    df_ciste[col] = df_ciste[col].fillna("").astype(str).str.strip()

                if ma_gramatiku:
                    df_ciste["Konjugace"] = df_ciste["Konjugace"].where(
                        df_ciste["Slovní druh"] == "Sloveso", ""
                    )
                    df_ciste["Deklinace"] = df_ciste["Deklinace"].where(
                        df_ciste["Slovní druh"].isin(["Podstatné jméno", "Přídavné jméno"]), ""
                    )

                df_ciste = df_ciste[cfg["sloupce_csv"]]

                preskoceno = 0
                if duplicity_rezim == "Přeskočit existující v dané lekci":
                    existujici_pary = set(zip(aktualni_df[col_cizi].str.lower(), aktualni_df["Lekce"].astype(str)))
                    mask_nova = [
                        (row[col_cizi].lower(), str(row["Lekce"])) not in existujici_pary
                        for _, row in df_ciste.iterrows()
                    ]
                    preskoceno = len(df_ciste) - sum(mask_nova)
                    df_k_pridani = df_ciste[mask_nova]
                else:
                    df_k_pridani = df_ciste

                if len(df_k_pridani) == 0:
                    st.warning("Nebyla přidána žádná nová slovíčka (všechna již v databázi existují).")
                else:
                    zalohy_dir = get_zalohy_dir(lang)
                    importy_dir = get_importy_dir(lang)
                    os.makedirs(zalohy_dir, exist_ok=True)
                    os.makedirs(importy_dir, exist_ok=True)

                    cas_razitko = datetime.now().strftime("%Y%m%d_%H%M%S")
                    cesta_zaloha = os.path.join(zalohy_dir, f"slovicka_zaloha_{cas_razitko}.csv")
                    aktualni_df[cfg["sloupce_csv"]].to_csv(cesta_zaloha, sep=";", index=False, encoding="utf-8")

                    lekce_list = sorted(list(set(df_k_pridani["Lekce"])))
                    lekce_tag = (
                        f"lekce_{lekce_list[0]}-{lekce_list[-1]}"
                        if len(lekce_list) > 1
                        else f"lekce_{lekce_list[0]}"
                        if lekce_list
                        else "import"
                    )
                    cesta_import = os.path.join(importy_dir, f"{lekce_tag}_{cas_razitko}.csv")
                    df_k_pridani.to_csv(cesta_import, sep=";", index=False, encoding="utf-8")

                    df_nove = pd.concat([aktualni_df, df_k_pridani], ignore_index=True)
                    if uloz_data(df_nove, lang=lang):
                        msg = f"Úspěšně přidáno {len(df_k_pridani)} slovíček do slovníku!"
                        if preskoceno > 0:
                            msg += f" (Přeskočeno {preskoceno} duplicitních slovíček.)"
                        msg += f" Samostatné CSV bylo uloženo do '{cesta_import}'."
                        st.session_state[f"{lang}_admin_zprava"] = msg
                        reset_import_csv_state(lang)
                        st.rerun()
    else:
        with st.container(horizontal=True):
            if st.button("Zavřít", key=f"{lang}_btn_csv_import_close"):
                reset_import_csv_state(lang)
                st.rerun()


@st.dialog("Upravit slovíčko", on_dismiss=reset_edit_state, width="large")
def dialog_upravit_slovo(lang: str, real_idx: int, aktualni_df: pd.DataFrame):
    cfg = JAZYKY[lang]
    col_cizi = cfg["sloupec"]
    ma_gramatiku = cfg["ma_gramatiku"]

    if real_idx not in aktualni_df.index:
        reset_edit_state(lang)
        st.rerun()

    karta = aktualni_df.loc[real_idx]
    existujici_lekce = sorted(list(set(aktualni_df["Lekce"].unique()) - {""}), key=klic_serazeni_lekci)
    if not existujici_lekce:
        existujici_lekce = ["1"]

    if ma_gramatiku:
        druhy_moznosti = list(cfg["druhy_vychozi"])
        konj_moznosti = ["", "1", "2", "3", "4", "nepravidelná"]
        dekl_moznosti = ["", "1", "2", "3", "4", "5", "1., 2.", "nesklonná"]
    else:
        druhy_z_dat = {str(d).strip() for d in aktualni_df["Slovní druh"].unique() if str(d).strip()}
        druhy_moznosti = sorted(list(druhy_z_dat | set(cfg["druhy_vychozi"])))
        konj_moznosti = []
        dekl_moznosti = []

    if st.session_state.get(f"{lang}_dlg_edit_initialized_idx") != real_idx:
        lekce_val = str(karta["Lekce"]).strip()
        if lekce_val.endswith(".0"):
            lekce_val = lekce_val[:-2]
        if lekce_val == "nan":
            lekce_val = ""

        druh_val = str(karta["Slovní druh"]).strip()
        if ma_gramatiku:
            if druh_val not in druhy_moznosti:
                druh_val = "Ostatní"

            konj_val = str(karta["Konjugace"]).strip()
            if konj_val.endswith(".0"):
                konj_val = konj_val[:-2]
            if konj_val == "nan" or konj_val not in konj_moznosti:
                konj_val = ""

            dekl_val = str(karta["Deklinace"]).strip()
            if dekl_val.endswith(".0"):
                dekl_val = dekl_val[:-2]
            if dekl_val == "nan":
                dekl_val = ""
            if dekl_val and dekl_val not in dekl_moznosti:
                dekl_moznosti.append(dekl_val)

            st.session_state[f"{lang}_dlg_edit_konj"] = konj_val
            st.session_state[f"{lang}_dlg_edit_dekl"] = dekl_val
        else:
            if not druh_val:
                druh_val = druhy_moznosti[0] if druhy_moznosti else ""

        st.session_state[f"{lang}_dlg_edit_initialized_idx"] = real_idx
        st.session_state[f"{lang}_dlg_edit_vyber_lekce"] = (
            lekce_val if lekce_val in existujici_lekce else existujici_lekce[0]
        )
        st.session_state[f"{lang}_dlg_edit_nova_lekce"] = "" if lekce_val in existujici_lekce else lekce_val
        st.session_state[f"{lang}_dlg_edit_slovo"] = str(karta[col_cizi])
        st.session_state[f"{lang}_dlg_edit_cestina"] = str(karta["Čeština"])
        st.session_state[f"{lang}_dlg_edit_druh"] = druh_val
        st.session_state.pop(f"{lang}_dlg_edit_msg", None)

    msg_key = f"{lang}_dlg_edit_msg"
    if msg_key in st.session_state:
        m_t, m_v = st.session_state.pop(msg_key)
        if m_t == "success":
            st.success(m_v, icon=":material/auto_fix_high:")
        else:
            st.warning(m_v)

    col_lekce1, col_lekce2 = st.columns(2)
    with col_lekce1:
        vyber_lekce = st.selectbox(
            "Existující lekce:", options=existujici_lekce, key=f"{lang}_dlg_edit_vyber_lekce"
        )
    with col_lekce2:
        nova_lekce = st.text_input(
            "Nebo zadejte nové číslo/název lekce:",
            placeholder="např. 6 (ponechte prázdné pro existující)",
            key=f"{lang}_dlg_edit_nova_lekce",
        )

    col_cizi_box, col_ces = st.columns(2)
    with col_cizi_box:
        nove_slovo = st.text_input(
            f"{col_cizi} *",
            placeholder=cfg["placeholder_slovo"],
            help=cfg["help_slovo"],
            key=f"{lang}_dlg_edit_slovo",
        )
    with col_ces:
        nova_cestina = st.text_input(
            "Čeština *",
            placeholder="např. vítězství" if lang == "latina" else "např. slovo",
            help="Český překlad",
            key=f"{lang}_dlg_edit_cestina",
        )

    nova_konj = ""
    nova_dekl = ""
    if ma_gramatiku:
        st.button(
            "Doplnit mluvnické kategorie",
            icon=":material/auto_fix_high:",
            on_click=on_edit_doplnit_kategorie,
            args=(lang,),
            key=f"{lang}_btn_edit_autofill",
            help="Automaticky určí slovní druh, konjugaci nebo deklinaci ze zadaného latinského tvaru podle pravidel v druhy.py",
        )

        col_druh, col_gram1, col_gram2 = st.columns(3)
        with col_druh:
            novy_druh = st.selectbox(
                "Slovní druh *",
                options=druhy_moznosti,
                key=f"{lang}_dlg_edit_druh",
            )
        with col_gram1:
            nova_konj = st.selectbox(
                "Konjugace (pro slovesa):",
                options=konj_moznosti,
                help="Vyplňte pouze u sloves",
                key=f"{lang}_dlg_edit_konj",
            )
        with col_gram2:
            nova_dekl = st.selectbox(
                "Deklinace (podst. a příd. jména):",
                options=dekl_moznosti,
                help="Vyplňte pro podstatná nebo přídavná jména",
                key=f"{lang}_dlg_edit_dekl",
            )
    else:
        novy_druh = st.selectbox(
            "Slovní druh *",
            options=druhy_moznosti,
            accept_new_options=True,
            help="Vyberte ze seznamu nebo zadejte vlastní slovní druh a potvrďte Enterem",
            key=f"{lang}_dlg_edit_druh",
        )

    st.markdown("<small>* Označená pole jsou povinná.</small>", unsafe_allow_html=True)
    with st.container(horizontal=True):
        tlacitko_ulozit = st.button(
            "Uložit změny",
            type="primary",
            icon=":material/save:",
            key=f"{lang}_btn_edit_save",
        )
        tlacitko_zrusit = st.button("Zrušit", key=f"{lang}_btn_edit_cancel")

    if tlacitko_zrusit:
        reset_edit_state(lang)
        st.rerun()

    if tlacitko_ulozit:
        finalni_lekce = nova_lekce.strip() if nova_lekce.strip() else vyber_lekce.strip()
        slovo_clean = nove_slovo.strip()
        cestina_clean = nova_cestina.strip()
        druh_clean = str(novy_druh or "").strip()

        if not finalni_lekce:
            st.error("Vyberte nebo zadejte číslo lekce.")
        elif not slovo_clean:
            st.error(f"Pole '{col_cizi}' je povinné.")
        elif not cestina_clean:
            st.error("Pole 'Čeština' je povinné.")
        elif not druh_clean:
            st.error("Pole 'Slovní druh' je povinné.")
        else:
            duplicita = aktualni_df[
                (aktualni_df.index != real_idx)
                & (aktualni_df[col_cizi].str.lower() == slovo_clean.lower())
                & (aktualni_df["Lekce"] == finalni_lekce)
            ]
            if len(duplicita) > 0:
                st.warning(f"Upozornění: Slovíčko '{slovo_clean}' v lekci {finalni_lekce} již v databázi existuje.")

            aktualni_df.loc[real_idx, "Lekce"] = finalni_lekce
            aktualni_df.loc[real_idx, col_cizi] = slovo_clean
            aktualni_df.loc[real_idx, "Čeština"] = cestina_clean
            aktualni_df.loc[real_idx, "Slovní druh"] = druh_clean
            if ma_gramatiku:
                aktualni_df.loc[real_idx, "Konjugace"] = nova_konj if druh_clean == "Sloveso" else ""
                aktualni_df.loc[real_idx, "Deklinace"] = (
                    nova_dekl if druh_clean in ["Podstatné jméno", "Přídavné jméno"] else ""
                )

            if uloz_data(aktualni_df, lang=lang):
                st.session_state[f"{lang}_admin_zprava"] = f"Kartička '{slovo_clean}' byla úspěšně upravena."
                reset_edit_state(lang)
                st.rerun()


@st.dialog("Smazat kartičku", on_dismiss=reset_delete_state)
def dialog_smazat_slovo(lang: str, real_idx: int, aktualni_df: pd.DataFrame):
    cfg = JAZYKY[lang]
    col_cizi = cfg["sloupec"]

    if real_idx not in aktualni_df.index:
        reset_delete_state(lang)
        st.rerun()

    karta = aktualni_df.loc[real_idx]
    st.warning("Opravdu chcete trvale smazat tuto kartičku?", icon=":material/warning:")
    with st.container(border=True):
        st.markdown(f"**{col_cizi}:** `{karta[col_cizi]}`")
        st.markdown(f"**Čeština:** {karta['Čeština']}")
        st.caption(f"Lekce: {karta['Lekce']} | Slovní druh: {karta['Slovní druh']}")

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        if st.button("Smazat kartičku", type="primary", icon=":material/delete:", key=f"{lang}_btn_del_confirm"):
            slovo_val = karta[col_cizi]
            df_novy = aktualni_df.drop(index=real_idx).reset_index(drop=True)
            if uloz_data(df_novy, lang=lang):
                st.session_state[f"{lang}_admin_zprava"] = f"Kartička '{slovo_val}' byla úspěšně smazána."
            reset_delete_state(lang)
            st.session_state[f"{lang}_admin_selected_rows"] = []
            st.session_state[f"{lang}_admin_table_ver"] = st.session_state.get(f"{lang}_admin_table_ver", 0) + 1
            st.rerun()
    with col_d2:
        if st.button("Zrušit", key=f"{lang}_btn_del_cancel"):
            reset_delete_state(lang)
            st.rerun()


@st.dialog("Hromadné smazání kartiček", on_dismiss=reset_bulk_delete_state)
def dialog_hromadne_smazani(lang: str, selected_indices: list, aktualni_df: pd.DataFrame):
    cfg = JAZYKY[lang]
    st.warning(
        f"Opravdu chcete trvale smazat **{len(selected_indices)}** vybraných kartiček?",
        icon=":material/warning:",
    )

    sloupce_ukazky = [c for c in cfg["sloupce_tabulka"] if c in aktualni_df.columns]
    ukazka = aktualni_df.loc[selected_indices, sloupce_ukazky].head(5)
    st.dataframe(ukazka, hide_index=True)
    if len(selected_indices) > 5:
        st.caption(f"... a dalších {len(selected_indices) - 5} kartiček.")

    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            "Potvrdit smazání",
            type="primary",
            icon=":material/delete_forever:",
            key=f"{lang}_btn_bulk_del_confirm",
        ):
            df_novy = aktualni_df.drop(index=selected_indices).reset_index(drop=True)
            if uloz_data(df_novy, lang=lang):
                st.session_state[f"{lang}_admin_zprava"] = f"Úspěšně smazáno {len(selected_indices)} kartiček."
            reset_bulk_delete_state(lang)
            st.session_state[f"{lang}_admin_selected_rows"] = []
            st.session_state[f"{lang}_admin_table_ver"] = st.session_state.get(f"{lang}_admin_table_ver", 0) + 1
            st.rerun()
    with col2:
        if st.button("Zrušit", key=f"{lang}_btn_bulk_del_cancel"):
            reset_bulk_delete_state(lang)
            st.rerun()


@st.dialog("Vytvořit balíček pro Anki", on_dismiss=reset_generator_balicek_state, width="medium")
def dialog_vytvorit_balicek(lang: str, df_filtered: pd.DataFrame):
    cfg = JAZYKY[lang]
    nazev_jazyka = cfg["nazev"]
    vychozi_nazev = cfg["vychozi_balicek"]

    st.write(f"Vybráno slovíček k exportu: **{len(df_filtered)}**")

    nazev = st.text_input(
        "Název balíčku (jak se bude jmenovat v Anki):",
        value=st.session_state.get(f"{lang}_nazev_balicku", vychozi_nazev),
        key=f"{lang}_dlg_gen_nazev_balicku",
    )

    popis = st.text_area(
        "Popis balíčku (volitelný, zobrazí se v knihovně):",
        placeholder="Např. Výběr sloves a podstatných jmen z 1. a 2. lekce...",
        key=f"{lang}_dlg_gen_popis_balicku",
    )

    st.markdown("##### Typy kartiček (Směr učení)")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        smer_cizi_ces = st.checkbox(
            f"{nazev_jazyka} ➔ Čeština", value=True, key=f"{lang}_dlg_gen_smer_cizi_ces"
        )
    with col_s2:
        smer_ces_cizi = st.checkbox(
            f"Čeština ➔ {nazev_jazyka}", value=True, key=f"{lang}_dlg_gen_smer_ces_cizi"
        )

    if not smer_cizi_ces and not smer_ces_cizi:
        st.warning(f"⚠️ Vyberte alespoň jeden směr kartiček (např. {nazev_jazyka} ➔ Čeština).")
    else:
        aktualni_nazev = nazev.strip() or vychozi_nazev
        st.session_state[f"{lang}_nazev_balicku"] = aktualni_nazev
        pocet_karet = len(df_filtered) * (int(smer_cizi_ces) + int(smer_ces_cizi))
        st.info(f"📊 Celkem bude vygenerováno **{pocet_karet}** karet pro Anki.")

        apkg_soubor = vytvor_anki_balicek(
            df_filtered, aktualni_nazev, smer_cizi_ces, smer_ces_cizi, lang=lang
        )

        st.markdown("---")
        st.markdown("##### Možnosti uložení")

        col_b1, col_b2 = st.columns(2)
        with col_b1:
            st.download_button(
                label=f"📥 Stáhnout do počítače ({pocet_karet} karet)",
                data=apkg_soubor,
                file_name=f"{aktualni_nazev.replace(' ', '_')}.apkg",
                mime="application/octet-stream",
                key=f"{lang}_btn_dl_direct",
                help="Stáhne balíček do zařízení bez uložení do veřejné knihovny",
                width="stretch",
            )

        with col_b2:
            if st.session_state.admin_prihlasen:
                if st.button(
                    "🚀 Publikovat a stáhnout balíček",
                    type="primary",
                    icon=":material/publish:",
                    key=f"{lang}_btn_pub_admin",
                    width="stretch",
                ):
                    ok, msg, cesta = publikuj_balicek(
                        aktualni_nazev, popis, smer_cizi_ces, smer_ces_cizi, df_filtered, lang=lang
                    )
                    if ok:
                        st.session_state[f"{lang}_pub_success"] = (aktualni_nazev, apkg_soubor.getvalue())
                        st.rerun()
                    else:
                        st.error(msg)
            else:
                with st.popover(
                    "🚀 Publikovat a stáhnout balíček",
                    icon=":material/publish:",
                    type="primary",
                    width="stretch",
                ):
                    st.caption("Pro publikování balíčku do veřejné knihovny zadejte heslo správce:")
                    heslo_pub = st.text_input(
                        "Administrátorské heslo:", type="password", key=f"{lang}_dlg_pub_heslo"
                    )
                    if st.button(
                        "Ověřit a publikovat",
                        type="primary",
                        icon=":material/publish:",
                        key=f"{lang}_btn_pub_guest",
                        width="stretch",
                    ):
                        spravne = nacti_admin_heslo()
                        if over_heslo(heslo_pub, spravne):
                            st.session_state.admin_prihlasen = True
                            ok, msg, cesta = publikuj_balicek(
                                aktualni_nazev, popis, smer_cizi_ces, smer_ces_cizi, df_filtered, lang=lang
                            )
                            if ok:
                                st.session_state[f"{lang}_pub_success"] = (aktualni_nazev, apkg_soubor.getvalue())
                                st.rerun()
                            else:
                                st.error(msg)
                        else:
                            st.error("Nesprávné administrátorské heslo.")

        pub_key = f"{lang}_pub_success"
        if pub_key in st.session_state:
            p_nazev, p_bytes = st.session_state.pop(pub_key)
            st.success(
                f"🎉 Balíček '{p_nazev}' byl úspěšně publikován do Knihovny kartiček!",
                icon=":material/check_circle:",
            )
            st.download_button(
                label=f"📥 Stáhnout publikovaný balíček ({pocet_karet} karet)",
                data=p_bytes,
                file_name=f"{p_nazev.replace(' ', '_')}.apkg",
                mime="application/octet-stream",
                type="primary",
                key=f"{lang}_btn_dl_pub_done",
            )


# ==============================================================================
# STRÁNKA 1: GENERÁTOR KARTIČEK
# ==============================================================================
def stranka_generator(lang: str = "latina"):
    cfg = JAZYKY[lang]
    ma_gramatiku = cfg["ma_gramatiku"]

    st.title(cfg["titulek_generator"])

    zobraz_sidebar_admin_status()

    df_lang = ziskej_data_jazyka(lang)
    if df_lang is None:
        return

    # Boční panel pro filtry generátoru
    st.sidebar.header("Nastavení filtru")

    # 1. Filtr Lekcí
    dostupne_lekce = sorted([l for l in df_lang["Lekce"].unique() if l], key=klic_serazeni_lekci)
    vybrane_lekce = st.sidebar.multiselect(
        "Vyber lekce:",
        options=dostupne_lekce,
        default=dostupne_lekce,
        key=f"{lang}_gen_lekce",
    )

    # 2. Filtr Slovních druhů
    dostupne_druhy = sorted([d for d in df_lang["Slovní druh"].unique() if d])
    vybrane_druhy = st.sidebar.multiselect(
        "Vyber slovní druhy:",
        options=dostupne_druhy,
        default=dostupne_druhy,
        key=f"{lang}_gen_druhy",
    )

    # 3. Dodatečné filtry (Konjugace / Deklinace – pouze pro latinu)
    vybrane_konjugace = []
    vybrane_deklinace = []
    if ma_gramatiku:
        chci_slovesa = "Sloveso" in vybrane_druhy
        chci_substantiva = "Podstatné jméno" in vybrane_druhy
        chci_adjektiva = "Přídavné jméno" in vybrane_druhy

        if chci_slovesa:
            dostupne_konj = sorted([k for k in df_lang["Konjugace"].unique() if k])
            vybrane_konjugace = st.sidebar.multiselect(
                "Vyber konjugace (jen pro slovesa):",
                options=dostupne_konj,
                default=dostupne_konj,
                key=f"{lang}_gen_konj",
            )

        if chci_substantiva or chci_adjektiva:
            dostupne_dekl = sorted([d for d in df_lang["Deklinace"].unique() if d])
            label_dekl = (
                "Vyber deklinace (podst. a příd. jména):"
                if (chci_substantiva and chci_adjektiva)
                else (
                    "Vyber deklinace (jen pro podst. jména):"
                    if chci_substantiva
                    else "Vyber deklinace (jen pro příd. jména):"
                )
            )
            vybrane_deklinace = st.sidebar.multiselect(
                label_dekl,
                options=dostupne_dekl,
                default=dostupne_dekl,
                key=f"{lang}_gen_dekl",
            )

    # 4. Vyhledávání textu
    hledany_text = st.sidebar.text_input(
        "Hledat výraz:",
        placeholder=cfg["placeholder_hledani"],
        icon=":material/search:",
        key=f"{lang}_gen_search",
    )
    diakritika_help = (
        "Zapněte pro přesné vyhledávání včetně háčků, čárek a makrónů (ā, ē, ī, ō, ū)"
        if ma_gramatiku
        else "Zapněte pro přesné vyhledávání včetně řeckých přídechů a akcentů (ἀ, ἡ, ῶ, ...)"
    )
    citlive_diakritika = st.sidebar.toggle(
        "Rozlišovat diakritiku",
        value=st.session_state.get(f"{lang}_citlive_diakritika", False),
        key=f"{lang}_gen_diakritika",
        help=diakritika_help,
    )
    st.session_state[f"{lang}_citlive_diakritika"] = citlive_diakritika

    # Aplikování filtrů na data pomocí sdílené funkce
    df_filtered = aplikuj_filtry(
        df_lang,
        vybrane_lekce,
        vybrane_druhy,
        vybrane_konjugace,
        vybrane_deklinace,
        hledany_text,
        citlive_diakritika,
        lang=lang,
    )
    st.sidebar.metric("Vyfiltrováno kartiček", len(df_filtered))

    if len(df_lang) == 0:
        st.info(
            f"Slovník pro jazyk **{cfg['nazev']}** je zatím prázdný. "
            f"Slovíčka může přidat nebo naimportovat z CSV správce v záložce [Slovník]({cfg['url_slovnik']}).",
            icon=":material/info:",
        )
        return

    st.info(
        "💡 **Chybí vám nějaké slovo?**\n\n"
        f"Pokud v databázi postrádáte některé slovíčko, můžete požádat o jeho přidání v záložce [Slovník]({cfg['url_slovnik']}).",
        icon=":material/info:",
    )

    # Hlavní plocha (zobrazení a export)
    st.subheader(f"Nalezeno slovíček: {len(df_filtered)}")

    st.dataframe(
        df_filtered[cfg["sloupce_tabulka"]],
        key=f"{lang}_gen_df",
        alt=cfg["alt_generator"],
    )

    # Tlačítko pro vytvoření balíčku
    col_btn, _ = st.columns([1, 2])
    with col_btn:
        tlacitko_vytvorit = st.button(
            "Vytvořit balíček",
            type="primary",
            icon=":material/style:",
            disabled=(len(df_filtered) == 0),
            key=f"{lang}_btn_vytvorit_balicek",
            help="Otevře nastavení a umožní stáhnout balíček pro Anki ze zobrazených slovíček",
        )
        if tlacitko_vytvorit:
            st.session_state[f"{lang}_generator_balicek_dialog"] = True
            st.rerun()

    if st.session_state.get(f"{lang}_generator_balicek_dialog", False):
        if len(df_filtered) > 0:
            dialog_vytvorit_balicek(lang, df_filtered)
        else:
            st.session_state[f"{lang}_generator_balicek_dialog"] = False


# ==============================================================================
# STRÁNKA 2: KNIHOVNA KARTIČEK (SDÍLENÁ PRO OBA JAZYKY)
# ==============================================================================
def stranka_knihovna():
    st.title("📚 Knihovna kartiček")

    zobraz_sidebar_admin_status()

    if "lib_zprava" in st.session_state:
        st.success(st.session_state.pop("lib_zprava"), icon=":material/check_circle:")

    if st.session_state.admin_prihlasen:
        if st.button(
            "Nahrát .apkg soubor",
            type="primary",
            icon=":material/upload_file:",
            key="btn_lib_upload_apkg",
        ):
            st.session_state["lib_upload_apkg_aktivni"] = True
            st.rerun()

        if st.session_state.get("lib_upload_apkg_aktivni", False):
            dialog_nahrat_apkg()

    celkova_knihovna = nacti_knihovnu()

    st.sidebar.header("Knihovna")
    filtr_jazyk = st.sidebar.segmented_control(
        "Jazyk:",
        options=["Všechny", "Latina", "Řečtina"],
        default="Všechny",
        key="lib_lang_filter",
    )
    hledat_text = st.sidebar.text_input(
        "Hledat v knihovně:",
        placeholder="Název nebo popis...",
        icon=":material/search:",
        key="lib_search",
    )
    st.sidebar.metric("Celkem balíčků v knihovně", len(celkova_knihovna))

    knihovna = celkova_knihovna
    if filtr_jazyk == "Latina":
        knihovna = [b for b in knihovna if b.get("jazyk", "latina") == "latina"]
    elif filtr_jazyk == "Řečtina":
        knihovna = [b for b in knihovna if b.get("jazyk", "latina") == "rectina"]

    if hledat_text and hledat_text.strip():
        q = hledat_text.strip().lower()
        knihovna = [
            b
            for b in knihovna
            if q in b.get("nazev", "").lower() or q in b.get("popis", "").lower()
        ]

    if not knihovna:
        if hledat_text or (filtr_jazyk and filtr_jazyk != "Všechny"):
            st.info("Žádný balíček neodpovídá zvoleným filtrům.", icon=":material/search_off:")
        else:
            st.info(
                "V knihovně zatím nejsou žádné balíčky kartiček. "
                "Vytvořit a publikovat balíček můžete v záložce **Generátor kartiček**.",
                icon=":material/info:",
            )
        return

    st.subheader(f"Dostupné balíčky ({len(knihovna)})")

    for b in knihovna:
        b_id = b.get("id", "")
        jazyk_kod = b.get("jazyk", "latina")
        cfg_b = JAZYKY.get(jazyk_kod, JAZYKY["latina"])

        with st.container(border=True):
            st.subheader(f"🎴 {b.get('nazev', 'Bez názvu')} :{cfg_b['badge_color']}-badge[{cfg_b['nazev']}]")
            st.caption(
                f"📅 {b.get('datum', '')} • "
                f"📖 Slovíček: **{b.get('pocet_slovicek', 0)}** • "
                f"🗂️ Karet v balíčku: **{b.get('pocet_karticek', 0)}**"
            )
            if b.get("popis"):
                st.write(b["popis"])
            else:
                st.caption("_Bez popisu_")

            soubor_path = b.get("soubor", "")
            data_bytes = None
            if soubor_path and os.path.exists(soubor_path):
                try:
                    with open(soubor_path, "rb") as sf:
                        data_bytes = sf.read()
                except Exception as e:
                    st.error(f"Chyba při čtení: {e}")
            else:
                st.warning("Soubor balíčku nebyl nalezen.")

            with st.container(horizontal=True):
                if data_bytes is not None:
                    st.download_button(
                        label="📥 Stáhnout balíček",
                        data=data_bytes,
                        file_name=b.get("nazev_souboru", f"{b.get('nazev', 'balicek')}.apkg"),
                        mime="application/octet-stream",
                        type="primary",
                        key=f"dl_btn_{b_id}",
                    )

                if st.session_state.admin_prihlasen:
                    if st.button(
                        "Smazat balíček", icon=":material/delete:", type="secondary", key=f"del_btn_{b_id}"
                    ):
                        st.session_state[f"confirm_del_{b_id}"] = True
                        st.rerun()

            if st.session_state.get(f"confirm_del_{b_id}", False):
                st.warning(f"Opravdu si přejete trvale smazat balíček **{b.get('nazev')}** z knihovny?")
                with st.container(horizontal=True):
                    if st.button(
                        "Ano, smazat",
                        type="primary",
                        icon=":material/delete_forever:",
                        key=f"conf_yes_{b_id}",
                    ):
                        smaz_balicek_z_knihovny(b_id)
                        st.session_state.pop(f"confirm_del_{b_id}", None)
                        st.toast(f"Balíček '{b.get('nazev')}' byl smazán z knihovny.", icon="🗑️")
                        st.rerun()
                    if st.button("Zrušit", key=f"conf_no_{b_id}"):
                        st.session_state.pop(f"confirm_del_{b_id}", None)
                        st.rerun()

            karticky = b.get("karticky", [])
            if karticky:
                with st.expander(f"🔍 Detail balíčku — seznam kartiček ({len(karticky)} slovíček)"):
                    df_kart = pd.DataFrame(karticky)
                    if cfg_b["ma_gramatiku"] and "konj./dekl." not in df_kart.columns:
                        konj_k = (
                            df_kart.get("Konjugace", pd.Series("", index=df_kart.index))
                            .astype(str)
                            .str.strip()
                        )
                        dekl_k = (
                            df_kart.get("Deklinace", pd.Series("", index=df_kart.index))
                            .astype(str)
                            .str.strip()
                        )
                        df_kart["konj./dekl."] = konj_k.where(konj_k != "", dekl_k)
                    sloupce_kart = [c for c in cfg_b["sloupce_tabulka"] if c in df_kart.columns]
                    st.dataframe(df_kart[sloupce_kart], hide_index=True, width="stretch")


# ==============================================================================
# STRÁNKA 3: SLOVNÍK (PŘEJMENOVANÁ ADMINISTRACE)
# ==============================================================================
def stranka_slovnik(lang: str = "latina"):
    cfg = JAZYKY[lang]
    col_cizi = cfg["sloupec"]
    ma_gramatiku = cfg["ma_gramatiku"]

    st.title(cfg["titulek_slovnik"])

    zobraz_sidebar_admin_status()

    df_lang = ziskej_data_jazyka(lang)
    if df_lang is None:
        return

    # Inicializace session state pro slovník (s prefixem jazyka)
    st.session_state.setdefault(f"{lang}_admin_selected_rows", [])
    st.session_state.setdefault(f"{lang}_admin_table_ver", 0)
    st.session_state.setdefault(f"{lang}_pridat_slovo_aktivni", False)
    st.session_state.setdefault(f"{lang}_import_csv_aktivni", False)
    st.session_state.setdefault(f"{lang}_edit_row_idx", None)
    st.session_state.setdefault(f"{lang}_delete_row_idx", None)
    st.session_state.setdefault(f"{lang}_bulk_delete_active", False)

    # Zobrazení případné uložené zprávy z předchozí akce
    if f"{lang}_admin_zprava" in st.session_state:
        st.success(st.session_state.pop(f"{lang}_admin_zprava"), icon=":material/check_circle:")

    # Filtry v postranním panelu
    st.sidebar.header("Nastavení filtru")
    st.sidebar.metric("Celkem kartiček v databázi", len(df_lang))

    dostupne_lekce = sorted([l for l in df_lang["Lekce"].unique() if l], key=klic_serazeni_lekci)
    vybrane_lekce = st.sidebar.multiselect(
        "Vyber lekce:",
        options=dostupne_lekce,
        default=dostupne_lekce,
        key=f"{lang}_admin_lekce",
    )

    dostupne_druhy = sorted([d for d in df_lang["Slovní druh"].unique() if d])
    vybrane_druhy = st.sidebar.multiselect(
        "Vyber slovní druhy:",
        options=dostupne_druhy,
        default=dostupne_druhy,
        key=f"{lang}_admin_druhy",
    )

    vybrane_konjugace = []
    vybrane_deklinace = []
    if ma_gramatiku:
        chci_slovesa = "Sloveso" in vybrane_druhy
        chci_substantiva = "Podstatné jméno" in vybrane_druhy
        chci_adjektiva = "Přídavné jméno" in vybrane_druhy

        if chci_slovesa:
            dostupne_konj = sorted([k for k in df_lang["Konjugace"].unique() if k])
            vybrane_konjugace = st.sidebar.multiselect(
                "Vyber konjugace (jen pro slovesa):",
                options=dostupne_konj,
                default=dostupne_konj,
                key=f"{lang}_admin_konj",
            )

        if chci_substantiva or chci_adjektiva:
            dostupne_dekl = sorted([d for d in df_lang["Deklinace"].unique() if d])
            label_dekl = (
                "Vyber deklinace (podst. a příd. jména):"
                if (chci_substantiva and chci_adjektiva)
                else (
                    "Vyber deklinace (jen pro podst. jména):"
                    if chci_substantiva
                    else "Vyber deklinace (jen pro příd. jména):"
                )
            )
            vybrane_deklinace = st.sidebar.multiselect(
                label_dekl,
                options=dostupne_dekl,
                default=dostupne_dekl,
                key=f"{lang}_admin_dekl",
            )

    hledany_text = st.sidebar.text_input(
        "Hledat výraz:",
        placeholder=cfg["placeholder_hledani"],
        icon=":material/search:",
        key=f"{lang}_admin_search",
    )
    diakritika_help = (
        "Zapněte pro přesné vyhledávání včetně háčků, čárek a makrónů (ā, ē, ī, ō, ū)"
        if ma_gramatiku
        else "Zapněte pro přesné vyhledávání včetně řeckých přídechů a akcentů (ἀ, ἡ, ῶ, ...)"
    )
    citlive_diakritika = st.sidebar.toggle(
        "Rozlišovat diakritiku",
        value=st.session_state.get(f"{lang}_citlive_diakritika", False),
        key=f"{lang}_admin_diakritika",
        help=diakritika_help,
    )
    st.session_state[f"{lang}_citlive_diakritika"] = citlive_diakritika

    df_filtered = aplikuj_filtry(
        df_lang,
        vybrane_lekce,
        vybrane_druhy,
        vybrane_konjugace,
        vybrane_deklinace,
        hledany_text,
        citlive_diakritika,
        lang=lang,
    )
    st.sidebar.metric("Vyfiltrováno kartiček", len(df_filtered))

    if not st.session_state.admin_prihlasen:
        st.info(
            "💡 Pro přidávání slovíček, import z CSV a úpravu či mazání kartiček se přihlaste v záložce **Přihlásit se**.",
            icon=":material/lock:",
        )

        # Zobrazení zprávy po odeslání žádosti
        if f"{lang}_uzivatel_zprava" in st.session_state:
            st.success(st.session_state.pop(f"{lang}_uzivatel_zprava"), icon=":material/check_circle:")

        if st.button("Požádat o chybějící slovo", icon=":material/record_voice_over:", key=f"{lang}_btn_pozadat_slovo"):
            st.session_state[f"{lang}_zadost_dialog_aktivni"] = True
            st.rerun()

        if len(df_lang) == 0:
            st.info(
                f"Slovník pro jazyk **{cfg['nazev']}** je zatím prázdný.",
                icon=":material/info:",
            )
        else:
            st.caption(f"Zobrazeno **{len(df_filtered)}** z celkem {len(df_lang)} kartiček")
            if len(df_filtered) == 0:
                st.info("Zadaným filtrům neodpovídá žádné slovíčko.")
                if hledany_text and hledany_text.strip():
                    st.markdown(
                        f"Nenašli jste slovo **{hledany_text.strip()}**? "
                        "Klikněte na tlačítko výše a požádejte o jeho přidání."
                    )
            else:
                st.dataframe(
                    df_filtered[cfg["sloupce_tabulka"]],
                    hide_index=True,
                    width="stretch",
                    key=f"{lang}_public_slovnik_df",
                    alt=cfg["alt_slovnik"],
                )

        if st.session_state.get(f"{lang}_zadost_dialog_aktivni"):
            dialog_zadost_o_slovo(lang, df_lang)
    else:
        if f"{lang}_admin_zprava_zadost" in st.session_state:
            st.success(st.session_state.pop(f"{lang}_admin_zprava_zadost"), icon=":material/check_circle:")

        # Záložky: Přehled slovíček + Žádosti o přidání
        zadosti = nacti_zadosti(lang)
        pocet_nevyrizenych = sum(1 for z in zadosti if z.get("stav") == "cekajici")
        tab_label_slova = "📚 Přehled a správa slovíček"
        tab_label_zadosti = (
            f"📬 Žádosti o přidání slova ({pocet_nevyrizenych})"
            if pocet_nevyrizenych > 0
            else "📬 Žádosti o přidání slova"
        )
        tab_slovnik, tab_zadosti = st.tabs([tab_label_slova, tab_label_zadosti])

        with tab_slovnik:
            # Reset označených řádků při změně filtrů
            filtr_sig = (
                tuple(vybrane_lekce),
                tuple(vybrane_druhy),
                tuple(vybrane_konjugace),
                tuple(vybrane_deklinace),
                hledany_text,
                citlive_diakritika,
            )
            if st.session_state.get(f"{lang}_admin_last_filtr") != filtr_sig:
                st.session_state[f"{lang}_admin_last_filtr"] = filtr_sig
                st.session_state[f"{lang}_admin_selected_rows"] = []
                st.session_state[f"{lang}_admin_table_ver"] += 1

            # Zjištění aktuálně označených řádků
            table_key = f"{lang}_admin_table_{st.session_state[f'{lang}_admin_table_ver']}"
            table_state = st.session_state.get(table_key)
            if table_state is not None and hasattr(table_state, "selection"):
                selected_rows = [r for r in table_state.selection.rows if r < len(df_filtered)]
            else:
                selected_rows = [
                    r for r in st.session_state[f"{lang}_admin_selected_rows"] if r < len(df_filtered)
                ]
            pocet_oznacenych = len(selected_rows)

            csv_export_bytes = (
                df_lang[cfg["sloupce_csv"]]
                .to_csv(sep=";", index=False)
                .encode("utf-8")
            )

            # Horní panel nástrojů (Toolbar)
            with st.container(horizontal=True):
                if st.button("Přidat slovo", icon=":material/add:", type="primary", key=f"{lang}_btn_add_open"):
                    st.session_state[f"{lang}_pridat_slovo_aktivni"] = True
                    st.rerun()

                if st.button("Importovat z CSV", icon=":material/upload_file:", key=f"{lang}_btn_import_open"):
                    st.session_state[f"{lang}_import_csv_aktivni"] = True
                    st.rerun()

                st.download_button(
                    "Exportovat CSV",
                    data=csv_export_bytes,
                    file_name=f"slovicka_{lang}.csv",
                    mime="text/csv",
                    icon=":material/download:",
                    disabled=(len(df_lang) == 0),
                    key=f"{lang}_btn_export_csv",
                    help="Stáhne kompletní databázi slovíček tohoto jazyka jako zálohu ve formátu CSV",
                )

                if st.button(
                    "Označit všechny vyfiltrované",
                    icon=":material/select_all:",
                    disabled=(len(df_filtered) == 0),
                    key=f"{lang}_btn_sel_all",
                ):
                    st.session_state[f"{lang}_admin_selected_rows"] = list(range(len(df_filtered)))
                    st.session_state[f"{lang}_admin_table_ver"] += 1
                    st.rerun()

                if st.button(
                    "Zrušit označení",
                    icon=":material/deselect:",
                    disabled=(pocet_oznacenych == 0),
                    key=f"{lang}_btn_desel_all",
                ):
                    st.session_state[f"{lang}_admin_selected_rows"] = []
                    st.session_state[f"{lang}_admin_table_ver"] += 1
                    st.rerun()

                if st.button(
                    f"Smazat označené ({pocet_oznacenych})" if pocet_oznacenych > 0 else "Smazat označené",
                    type="secondary",
                    icon=":material/delete_sweep:",
                    disabled=(pocet_oznacenych == 0),
                    key=f"{lang}_btn_bulk_del",
                ):
                    st.session_state[f"{lang}_bulk_delete_active"] = True
                    st.rerun()

            st.caption(
                f"Zobrazeno **{len(df_filtered)}** z celkem {len(df_lang)} kartiček | Označeno pro smazání: **{pocet_oznacenych}**"
            )

            # Tabulka s přehledem kartiček
            if len(df_lang) == 0:
                st.info(
                    f"Slovník pro jazyk **{cfg['nazev']}** je zatím prázdný. "
                    "Přidejte první slovíčko tlačítkem **Přidat slovo** nebo nahrajte soubor přes **Importovat z CSV**.",
                    icon=":material/info:",
                )
            elif len(df_filtered) == 0:
                st.info("Zadaným filtrům neodpovídá žádná kartička.")
            else:
                df_zobrazit = df_filtered.copy()
                df_zobrazit["Upravit"] = ":material/edit:"
                df_zobrazit["Smazat"] = ":material/delete:"
                sloupce = ["Upravit", "Smazat"] + cfg["sloupce_tabulka"]
                df_zobrazit = df_zobrazit[sloupce]

                col_config = {
                    "Upravit": st.column_config.ButtonColumn(
                        "Upravit",
                        help="Upravit kartičku",
                        width="small",
                        pinned=True,
                        on_click=on_admin_edit_click,
                        args=(lang,),
                        key=f"{lang}_btn_admin_edit",
                    ),
                    "Smazat": st.column_config.ButtonColumn(
                        "Smazat",
                        help="Smazat kartičku",
                        width="small",
                        pinned=True,
                        on_click=on_admin_delete_click,
                        args=(lang,),
                        key=f"{lang}_btn_admin_delete",
                    ),
                    "Lekce": st.column_config.TextColumn("Lekce", width="small"),
                    col_cizi: st.column_config.TextColumn(col_cizi, width="medium"),
                    "Čeština": st.column_config.TextColumn("Čeština", width="medium"),
                    "Slovní druh": st.column_config.TextColumn("Slovní druh", width="medium"),
                }
                if ma_gramatiku:
                    col_config["konj./dekl."] = st.column_config.TextColumn("konj./dekl.", width="small")

                st.dataframe(
                    df_zobrazit,
                    key=table_key,
                    selection_default={"selection": {"rows": st.session_state[f"{lang}_admin_selected_rows"]}},
                    on_select="rerun",
                    selection_mode="multi-row",
                    column_config=col_config,
                    hide_index=True,
                    width="stretch",
                    alt=cfg["alt_admin_slovnik"],
                )

        with tab_zadosti:
            filtr_stav = st.segmented_control(
                "Filtr stavu:",
                options=["Všechny", "Čekající", "Vyřízené", "Zamítnuté"],
                default="Všechny",
                key=f"{lang}_filtr_stav_zadosti",
            )

            stav_map = {"Čekající": "cekajici", "Vyřízené": "vyrizeno", "Zamítnuté": "zamitnuto"}
            if filtr_stav and filtr_stav != "Všechny":
                zobrazene_zadosti = [z for z in zadosti if z.get("stav") == stav_map.get(filtr_stav)]
            else:
                zobrazene_zadosti = zadosti

            if not zobrazene_zadosti:
                if not zadosti:
                    st.info("Zatím nebyly podány žádné žádosti o přidání slova.", icon=":material/inbox:")
                else:
                    st.info("Žádné žádosti neodpovídají vybranému filtru.", icon=":material/filter_list:")
            else:
                st.caption(f"Zobrazeno **{len(zobrazene_zadosti)}** žádostí")
                for z in zobrazene_zadosti:
                    stav = z.get("stav", "cekajici")
                    stav_ikona = {"cekajici": "⏳", "vyrizeno": "✅", "zamitnuto": "❌"}.get(stav, "❓")
                    stav_text = {"cekajici": "Čekající", "vyrizeno": "Vyřízeno", "zamitnuto": "Zamítnuto"}.get(
                        stav, stav
                    )
                    zadane_slovo = z.get("slovo") or z.get("latina", "?")

                    with st.container(border=True):
                        col_info, col_akce = st.columns([3, 1])
                        with col_info:
                            st.markdown(
                                f"**{zadane_slovo}**" + (f" — {z['cestina']}" if z.get("cestina") else "")
                            )
                            detaily = []
                            if z.get("lekce"):
                                detaily.append(f"Lekce: {z['lekce']}")
                            detaily.append(f"Odesláno: {z.get('datum', '?')}")
                            detaily.append(f"Stav: {stav_ikona} {stav_text}")
                            st.caption(" · ".join(detaily))
                            if z.get("poznamka"):
                                st.caption(f"💬 {z['poznamka']}")
                        with col_akce:
                            zadost_id = z.get("id", "")
                            if stav == "cekajici":
                                if st.button(
                                    "Rychle přidat",
                                    icon=":material/rocket_launch:",
                                    key=f"{lang}_btn_rychle_{zadost_id}",
                                    width="stretch",
                                ):
                                    slovo_val = str(zadane_slovo).strip()
                                    ces = z.get("cestina", "").strip()
                                    lek = z.get("lekce", "").strip() or "1"
                                    if ma_gramatiku:
                                        druh, konj, dekl = analyzuj_slovicko(slovo_val)
                                        novy_dict = {
                                            "Lekce": lek,
                                            col_cizi: slovo_val,
                                            "Čeština": ces if ces else slovo_val,
                                            "Slovní druh": druh,
                                            "Konjugace": konj if druh == "Sloveso" else "",
                                            "Deklinace": dekl if druh in ["Podstatné jméno", "Přídavné jméno"] else "",
                                        }
                                    else:
                                        novy_dict = {
                                            "Lekce": lek,
                                            col_cizi: slovo_val,
                                            "Čeština": ces if ces else slovo_val,
                                            "Slovní druh": "",
                                            "Rod": "",
                                        }
                                    novy_zaznam = pd.DataFrame([novy_dict])
                                    df_nove = pd.concat([df_lang, novy_zaznam], ignore_index=True)
                                    if uloz_data(df_nove, lang=lang):
                                        zmen_stav_zadosti(zadost_id, "vyrizeno", lang=lang)
                                        st.session_state[f"{lang}_admin_zprava"] = (
                                            f"Slovíčko \u201e{slovo_val}\u201c bylo přidáno do lekce {lek} a žádost označena jako vyřízená."
                                        )
                                        st.rerun()

                                if st.button(
                                    "Upravit a přidat",
                                    icon=":material/edit:",
                                    key=f"{lang}_btn_upravit_{zadost_id}",
                                    width="stretch",
                                ):
                                    st.session_state[f"{lang}_pridat_slovo_aktivni"] = True
                                    st.session_state[f"{lang}_dlg_add_slovo"] = zadane_slovo
                                    st.session_state[f"{lang}_dlg_add_cestina"] = z.get("cestina", "")
                                    if z.get("lekce"):
                                        st.session_state[f"{lang}_dlg_add_nova_lekce"] = z.get("lekce", "")
                                    st.session_state[f"{lang}_schvalovana_zadost_id"] = zadost_id
                                    st.rerun()

                                if st.button(
                                    "Zamítnout",
                                    icon=":material/close:",
                                    key=f"{lang}_btn_zamitnout_{zadost_id}",
                                    width="stretch",
                                ):
                                    zmen_stav_zadosti(zadost_id, "zamitnuto", lang=lang)
                                    st.session_state[f"{lang}_admin_zprava_zadost"] = (
                                        f"Žádost o \u201e{zadane_slovo}\u201c byla zamítnuta."
                                    )
                                    st.rerun()

                            if st.button(
                                "Smazat",
                                icon=":material/delete:",
                                key=f"{lang}_btn_smaz_zadost_{zadost_id}",
                                width="stretch",
                            ):
                                smaz_zadost(zadost_id, lang=lang)
                                st.session_state[f"{lang}_admin_zprava_zadost"] = (
                                    f"Žádost o \u201e{zadane_slovo}\u201c byla smazána."
                                )
                                st.rerun()

        # Otevření příslušného modálního dialogu
        if st.session_state.get(f"{lang}_pridat_slovo_aktivni"):
            dialog_pridat_slovo(lang, df_lang)
        elif st.session_state.get(f"{lang}_import_csv_aktivni"):
            dialog_import_csv(lang, df_lang)
        elif st.session_state.get(f"{lang}_edit_row_idx") is not None:
            cr = st.session_state[f"{lang}_edit_row_idx"]
            if 0 <= cr < len(df_filtered):
                dialog_upravit_slovo(lang, df_filtered.index[cr], df_lang)
            else:
                st.session_state[f"{lang}_edit_row_idx"] = None
        elif st.session_state.get(f"{lang}_delete_row_idx") is not None:
            cr = st.session_state[f"{lang}_delete_row_idx"]
            if 0 <= cr < len(df_filtered):
                dialog_smazat_slovo(lang, df_filtered.index[cr], df_lang)
            else:
                st.session_state[f"{lang}_delete_row_idx"] = None
        elif st.session_state.get(f"{lang}_bulk_delete_active"):
            sel_indices = [df_filtered.index[r] for r in selected_rows if r < len(df_filtered)]
            if sel_indices:
                dialog_hromadne_smazani(lang, sel_indices, df_lang)
            else:
                st.session_state[f"{lang}_bulk_delete_active"] = False


# ==============================================================================
# STRÁNKA 4: PŘIHLÁŠENÍ SPRÁVCE
# ==============================================================================
def stranka_prihlaseni():
    zobraz_sidebar_admin_status()
    st.sidebar.header("Statistiky databáze")
    for lang_kod, cfg in JAZYKY.items():
        df_l = nacti_data(lang_kod)
        pocet_karet = len(df_l)
        pocet_lekci = len(set(df_l["Lekce"]) - {""})
        st.sidebar.metric(f"Kartičky – {cfg['nazev']}", pocet_karet)
        st.sidebar.metric(f"Aktivní lekce – {cfg['nazev']}", pocet_lekci)

    admin_heslo = nacti_admin_heslo()

    if not st.session_state.admin_prihlasen:
        st.title("🔐 Přihlášení správce")
        st.write(
            "Pro správu slovníků (přidávání, úpravy a mazání kartiček) a publikování balíčků do veřejné knihovny zadejte administrátorské heslo."
        )

        if not admin_heslo:
            st.warning(
                "⚠️ Administrátorské heslo není nastaveno v proměnných prostředí ani v souboru `.env`.",
                icon=":material/warning:",
            )
            st.info(
                "Pro nastavení hesla zadejte do souboru `.env`:\n\n"
                "```bash\nADMIN_PASSWORD=vase_tajne_heslo\n```\n\n"
                "a restartujte aplikaci nebo kontejner."
            )
            return

        with st.container(border=True):
            st.subheader("Přihlašovací formulář")
            with st.form("admin_login_page_form"):
                zadane_heslo = st.text_input("Heslo správce:", type="password", placeholder="Zadejte heslo")
                tlacitko_login = st.form_submit_button("Přihlásit se", type="primary", icon=":material/lock_open:")
                if tlacitko_login:
                    if over_heslo(zadane_heslo, admin_heslo):
                        st.session_state.admin_prihlasen = True
                        st.toast("Byl jste úspěšně přihlášen jako správce.", icon="✅")
                        st.rerun()
                    else:
                        st.error("Nesprávné heslo. Zkuste to prosím znovu.")
    else:
        st.title("🛡️ Správa přihlášení")
        st.success("Aktuálně jste přihlášen jako správce systému.", icon=":material/verified_user:")

        st.markdown(
            """
            Jako přihlášený správce máte k dispozici následující oprávnění pro oba jazyky (**Latina** i **Řečtina**):
- **Generátor kartiček:** Možnost publikovat vytvořené balíčky do veřejné knihovny.
- **Knihovna kartiček:** Možnost mazat uložené balíčky.
- **Slovník:** Přidávání nových slovíček, úprava existujících kartiček, CSV import/export a vyřizování žádostí o slova.
            """
        )

        st.markdown("---")
        if st.button("Odhlásit se ze systému", icon=":material/logout:", type="secondary", key="btn_logout_page"):
            st.session_state.admin_prihlasen = False
            for lang_kod in JAZYKY:
                st.session_state[f"{lang_kod}_admin_selected_rows"] = []
            st.toast("Byl jste odhlášen.", icon="👋")
            st.rerun()


# ==============================================================================
# STRÁNKA 5: NÁVOD K IMPORTU DO ANKI
# ==============================================================================
def stranka_navod():
    st.title("❓ Jak používat kartičky v Anki aplikaci")
    st.caption("Podrobný průvodce stažením a importem vygenerovaných balíčků (.apkg) do telefonu, tabletu i počítače.")

    zobraz_sidebar_admin_status()

    st.sidebar.header("Rychlé odkazy ke stažení")
    st.sidebar.link_button(
        "AnkiDroid (Android)",
        "https://play.google.com/store/apps/details?id=com.ichi2.anki",
        icon=":material/android:",
        width="stretch",
    )
    st.sidebar.link_button(
        "AnkiMobile (iOS)",
        "https://apps.apple.com/app/ankimobile-flashcards/id373493387",
        icon=":material/phone_iphone:",
        width="stretch",
    )
    st.sidebar.link_button(
        "Anki pro PC / Mac",
        "https://apps.ankiweb.net/",
        icon=":material/desktop_windows:",
        width="stretch",
    )
    st.sidebar.link_button(
        "AnkiWeb (online)",
        "https://ankiweb.net/",
        icon=":material/cloud:",
        width="stretch",
    )

    st.info(
        "💡 **Co je soubor `.apkg`?**\n\n"
        "Jde o standardní balíček kartiček Anki. Po vygenerování v Generátoru kartiček "
        "([Latina](latina-generator) / [Řečtina](rectina-generator)) "
        "nebo stažení z [Knihovny kartiček](knihovna) získáte soubor s příponou `.apkg`, "
        "který jednoduše otevřete ve své aplikaci Anki.",
        icon=":material/info:",
    )

    st.subheader("1. Kde získat aplikaci Anki?")
    col_d1, col_d2, col_d3 = st.columns(3)
    with col_d1:
        with st.container(border=True):
            st.markdown("#### 📱 Android")
            st.markdown("**Aplikace:** AnkiDroid")
            st.caption("Zcela zdarma, open-source.")
            st.link_button(
                "Google Play",
                "https://play.google.com/store/apps/details?id=com.ichi2.anki",
                icon=":material/android:",
                type="primary",
                width="stretch",
            )
    with col_d2:
        with st.container(border=True):
            st.markdown("#### 🍏 iOS (iPhone / iPad)")
            st.markdown("**Aplikace:** AnkiMobile / AnkiWeb")
            st.caption("Oficiální iOS app (placená) nebo zdarma přes web.")
            st.link_button(
                "App Store",
                "https://apps.apple.com/app/ankimobile-flashcards/id373493387",
                icon=":material/phone_iphone:",
                type="primary",
                width="stretch",
            )
    with col_d3:
        with st.container(border=True):
            st.markdown("#### 💻 Počítač (PC / Mac)")
            st.markdown("**Aplikace:** Anki Desktop")
            st.caption("Windows, macOS i Linux. Zcela zdarma.")
            st.link_button(
                "Stáhnout pro PC",
                "https://apps.ankiweb.net/",
                icon=":material/desktop_windows:",
                type="primary",
                width="stretch",
            )

    st.markdown("---")
    st.subheader("2. Návod k importu krok za krokem")

    tab_android, tab_ios, tab_pc, tab_sync = st.tabs([
        "📱 Android (AnkiDroid)",
        "🍏 iPhone / iPad (AnkiMobile)",
        "💻 Počítač (Windows, Mac, Linux)",
        "☁️ Synchronizace (AnkiWeb)",
    ])

    with tab_android:
        st.markdown("### Postup pro zařízení s Androidem (AnkiDroid)")
        st.markdown(
            """
1. **Nainstalujte si AnkiDroid** z [Obchodu Google Play](https://play.google.com/store/apps/details?id=com.ichi2.anki) a aplikaci alespoň jednou spusťte.
2. **Stáhněte si balíček:**
  - V této aplikaci přejděte na Generátor kartiček ([Latina](latina-generator) / [Řečtina](rectina-generator)) nebo do [Knihovny kartiček](knihovna).
  - Vyberte si lekce nebo existující balíček a klepněte na tlačítko pro stažení balíčku (`.apkg`).
3. **Otevřete a naimportujte soubor:**
  - **Způsob A (nejrychlejší):** Stáhněte lištu oznámení v telefonu a klepněte na oznámení o dokončeném stažení souboru. Telefon vám nabídne aplikaci pro otevření – vyberte **AnkiDroid**. Import proběhne okamžitě.
  - **Způsob B (přes správce souborů):** Otevřete aplikaci *Správce souborů* (nebo *Stažené soubory / Downloads*), najděte soubor s příponou `.apkg` a klepněte na něj.
  - **Způsob C (přímo v AnkiDroidu):** Spusťte AnkiDroid, klepněte na tři tečky v pravém horním rohu (nebo na modré tlačítko `+` vpravo dole) -> zvolte **Importovat** -> vyhledejte stažený soubor ve složce *Download*.
4. **Hotovo!** Na hlavní obrazovce AnkiDroidu uvidíte nový balíček kartiček připravený k procvičování.
            """
        )
        st.link_button(
            "Otevřít AnkiDroid v Google Play",
            "https://play.google.com/store/apps/details?id=com.ichi2.anki",
            icon=":material/android:",
            type="secondary",
        )

    with tab_ios:
        st.markdown("### Postup pro iPhone a iPad (iOS)")
        st.markdown(
            """
            Na zařízeních Apple máte dvě skvělé možnosti:

            #### Možnost 1: Oficiální aplikace AnkiMobile (App Store)
1. Nainstalujte si aplikaci **AnkiMobile Flashcards** z [App Store](https://apps.apple.com/app/ankimobile-flashcards/id373493387).
2. V prohlížeči Safari na iPhonu si stáhněte balíček `.apkg` z Generátoru ([Latina](latina-generator) / [Řečtina](rectina-generator)) nebo [Knihovny](knihovna).
3. V Safari klepněte v adresním řádku na ikonu stahování (modrý kroužek se šipkou dolů) a klepněte na stažený soubor `.apkg`.
4. Klepněte na ikonu **Sdílení** (čtvereček se šipkou nahoru) a v řádku aplikací zvolte **Anki** (případně *Otevřít v aplikaci AnkiMobile*).
5. Balíček se automaticky přidá do vaší sbírky.

            #### Možnost 2: Bezplatné využití přes AnkiWeb (prohlížeč)
            Pokud nechcete kupovat placenou aplikaci pro iOS:
1. Vytvořte si bezplatný účet na [AnkiWeb.net](https://ankiweb.net/).
2. Na svém počítači (kde je Anki zdarma) importujte stažený `.apkg` balíček a stiskněte tlačítko **Synchronizovat**.
3. Na iPhonu otevřete Safari, přihlaste se na [AnkiWeb.net](https://ankiweb.net/) a můžete se kartičky pohodlně učit přímo v prohlížeči s plnou synchronizací postupu.
            """
        )
        col_ios1, col_ios2 = st.columns(2)
        with col_ios1:
            st.link_button(
                "AnkiMobile v App Store",
                "https://apps.apple.com/app/ankimobile-flashcards/id373493387",
                icon=":material/phone_iphone:",
                type="secondary",
                width="stretch",
            )
        with col_ios2:
            st.link_button(
                "Přejít na AnkiWeb",
                "https://ankiweb.net/",
                icon=":material/cloud:",
                type="secondary",
                width="stretch",
            )

    with tab_pc:
        st.markdown("### Postup pro počítač (Windows, Mac, Linux)")
        st.markdown(
            """
            Počítačová verze Anki je nejvýkonnější a nabízí nejpohodlnější správu kartiček.

1. **Stáhněte a nainstalujte Anki:**
  - Přejděte na oficiální web [apps.ankiweb.net](https://apps.ankiweb.net/) a stáhněte si verzi pro svůj systém (Windows, macOS nebo Linux).
  - Dokončete standardní instalaci.
2. **Stáhněte balíček:**
  - Zde v Generátoru kartiček ([Latina](latina-generator) / [Řečtina](rectina-generator)) nebo [Knihovně kartiček](knihovna) klikněte na stažení vybraného balíčku.
3. **Importujte do Anki:**
  - **Způsob A (nejjednodušší):** Dvakrát klikněte na stažený soubor `.apkg` ve složce *Stažené soubory*. Program Anki se sám otevře a zobrazí zprávu: *Import dokončen*.
  - **Způsob B (z menu programu):** Spusťte Anki -> v horní liště zvolte **Soubor (File)** -> **Importovat... (Import...)** (nebo klávesová zkratka `Ctrl + I` na Windows či `Cmd + I` na Macu) -> vyberte soubor `.apkg`.
4. **Hotovo!** Balíček se zařadí mezi vaše balíčky a můžete ihned kliknout na *Studovat*.
            """
        )
        st.link_button(
            "Přejít na oficiální web ke stažení Anki pro PC",
            "https://apps.ankiweb.net/",
            icon=":material/desktop_windows:",
            type="secondary",
        )

    with tab_sync:
        st.markdown("### Jak synchronizovat kartičky mezi telefonem a počítačem?")
        st.markdown(
            """
            Díky bezplatné službě **AnkiWeb** se nemusíte starat o přenášení souborů – jednou naimportujete balíček na počítači a automaticky ho máte i v mobilu.

1. **Registrace:** Založte si bezplatný účet na [ankiweb.net/account/register](https://ankiweb.net/account/register).
2. **Přihlášení v Anki na počítači:**
  - Klikněte na tlačítko **Synchronizovat** (ikona dvou šipek v kruhu v horní liště).
  - Zadejte svůj e-mail a heslo z AnkiWebu.
3. **Přihlášení v telefonu (AnkiDroid / AnkiMobile):**
  - V nastavení aplikace otevřete sekci *AnkiWeb účet* a zadejte stejné přihlašovací údaje.
  - Klepněte na ikonu synchronizace.
4. **Výsledek:** Všechny kartičky, vaše naplánovaná opakování i statistiky se odteď automaticky synchronizují na všech vašich zařízeních.
            """
        )
        st.link_button(
            "Registrovat bezplatný účet na AnkiWeb",
            "https://ankiweb.net/account/register",
            icon=":material/sync:",
            type="secondary",
        )


# ==============================================================================
# STRÁNKA 6: O PROJEKTU
# ==============================================================================
def stranka_o_projektu():
    st.title("ℹ️ O projektu")

    zobraz_sidebar_admin_status()

    st.sidebar.header("Rychlé odkazy")
    st.sidebar.link_button(
        "Github repozitář",
        "https://github.com/JedlaTypek/latin-flashcard-gen",
        icon=":material/code:",
        width="stretch",
    )
    st.sidebar.link_button(
        "Mé další projekty",
        "https://jedlickaf.cz",
        icon=":material/work:",
        width="stretch",
    )
    st.sidebar.link_button(
        "Napište mi!",
        "mailto:jedlicka.filip@seznam.cz",
        icon=":material/mail:",
        width="stretch",
    )

    st.markdown("""
    ### 📚 K čemu aplikace slouží?
    Tento nástroj vznikl pro usnadnění studia latiny a tvorbu studijních kartiček (např. pro **AnkiWeb / AnkiMobile / AnkiDroid**). 
    Slovíčka a jejich zpracování vycházejí z učebnice **Základy latinského jazyka pro posluchače teologie** od **Milana Šlesingera**.

- 📖 **Učebnice ke stažení:** Pokud učebnici nemáte po ruce v tištěné podobě, její PDF verzi si můžete stáhnout například [zde v knihovně Libri Nostri](https://librinostri.catholica.cz/download/ZaklLatJPrPoThSl-OCRpcb.pdf).

    ---

    ### 🚀 Plány do budoucna
    Projekt se dále vyvíjí. V plánu je přidat do slovníčku všechny lekce z již zmíněné učebnice latiny a rozšířit funkčnost této aplikace také o podporu pro učebnici **Uvedení do novozákonní řečtiny** od **Josefa Bartoně**, aby sloužila jako komplexní pomůcka pro studium obou biblických/klasických jazyků.
    """)

    st.divider()
    col1, col2 = st.columns([3, 1])

    with col1:
        st.markdown("""
        ### 💻 Open Source a podpora projektu
        Tuto aplikaci programuji, spravuji a hostuji ve svém volném čase a **na vlastní náklady**. 
        
- **Projekt byl, je a vždy bude zcela zdarma pro všechny návštěvníky.**
- Zdrojový kód je veřejně dostupný jako **open source** dostupný na [Githubu](https://github.com/JedlaTypek/latin-flashcard-gen/).

        Pokud vám aplikace šetří čas při přípravě na zkoušky nebo při studiu jazyků a chcete podpořit její další vývoj a provoz serveru, můžete dobrovolně přispět libovolnou částkou přes platební QR kód. 
        
        Každá podpora pomůže pokrýt náklady na hosting a motivuje k přidávání dalších funkcí (včetně podpory novozákonní řečtiny). Děkuji!
        """)

    with col2:
        if qr_path.is_file():
            st.image(str(qr_path), caption="QR platba (CZK)", width="stretch")
        else:
            st.info("QR kód pro platbu momentálně není k dispozici.")


# ==============================================================================
# HORNÍ MENU BAR (NAVIGACE)
# ==============================================================================
def stranka_latina_generator():
    stranka_generator("latina")


def stranka_latina_slovnik():
    stranka_slovnik("latina")


def stranka_rectina_generator():
    stranka_generator("rectina")


def stranka_rectina_slovnik():
    stranka_slovnik("rectina")


stranky = {
    "Latina": [
        st.Page(
            stranka_latina_generator,
            title="Generátor kartiček",
            icon=":material/style:",
            url_path="latina-generator",
            default=True,
        ),
        st.Page(
            stranka_latina_slovnik,
            title="Slovník",
            icon=":material/menu_book:",
            url_path="latina-slovnik",
        ),
    ],
    "Řečtina": [
        st.Page(
            stranka_rectina_generator,
            title="Generátor kartiček",
            icon=":material/style:",
            url_path="rectina-generator",
        ),
        st.Page(
            stranka_rectina_slovnik,
            title="Slovník",
            icon=":material/menu_book:",
            url_path="rectina-slovnik",
        ),
    ],
    "": [
        st.Page(
            stranka_knihovna,
            title="Knihovna kartiček",
            icon=":material/local_library:",
            url_path="knihovna",
        ),
        st.Page(
            stranka_navod,
            title="Jak používat kartičky",
            icon=":material/help:",
            url_path="navod",
        ),
        st.Page(
            stranka_o_projektu,
            title="O projektu",
            icon=":material/info:",
            url_path="o-projektu",
        ),
        st.Page(
            stranka_prihlaseni,
            title="Odhlásit se" if st.session_state.admin_prihlasen else "Přihlásit se",
            icon=":material/verified_user:" if st.session_state.admin_prihlasen else ":material/login:",
            url_path="prihlaseni",
        ),
    ],
}

pg = st.navigation(stranky, position="top")
pg.run()

aktivuj_ctrl_f_pro_sidebar("Hledat výraz:")