import io
import os
import csv
import unicodedata
import random
import hmac
import json
import time
from datetime import datetime
import pandas as pd
import genanki
import streamlit as st
from druhy import analyzuj_slovicko

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

def klic_serazeni_lekci(hodnota: str):
    """Přirozené řazení lekcí (čísla číselně, ostatní abecedně)."""
    return (int(hodnota) if hodnota.isdigit() else 9999, hodnota)

# --- NASTAVENÍ STRÁNKY A SESSION STATE ---
st.set_page_config(page_title="Generátor karet do AnkiDroidu", page_icon="📚", layout="wide")

if "nazev_balicku" not in st.session_state:
    st.session_state.nazev_balicku = "Latina - Můj výběr"

if "admin_prihlasen" not in st.session_state:
    st.session_state.admin_prihlasen = False

DATA_DIR = os.environ.get("DATA_DIR", "data")
if os.path.exists(os.path.join(DATA_DIR, "slovicka_komplet.csv")):
    CSV_SOUBOR = os.path.join(DATA_DIR, "slovicka_komplet.csv")
elif os.path.exists("slovicka_komplet.csv"):
    CSV_SOUBOR = "slovicka_komplet.csv"
else:
    CSV_SOUBOR = os.path.join(DATA_DIR, "slovicka_komplet.csv")

# --- NAČTENÍ A UKLÁDÁNÍ DAT ---
@st.cache_data
def nacti_data() -> pd.DataFrame:
    df = pd.read_csv(CSV_SOUBOR, sep=";", dtype=str)
    df.fillna("", inplace=True)
    return df

def uloz_data(novy_df: pd.DataFrame) -> bool:
    """Uloží DataFrame do CSV a zneplatní cache, aby se změny ihned projevily."""
    try:
        adresar = os.path.dirname(CSV_SOUBOR)
        if adresar:
            os.makedirs(adresar, exist_ok=True)
        novy_df.to_csv(CSV_SOUBOR, sep=";", index=False, encoding="utf-8")
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Chyba při ukládání do souboru '{CSV_SOUBOR}': {e}")
        return False

try:
    df = nacti_data()
except FileNotFoundError:
    st.error(f"Chyba: Soubor '{CSV_SOUBOR}' nebyl nalezen. Ujisti se, že je umístěn ve složce 'data/'.")
    st.stop()

# --- FUNKCE PRO GENEROVÁNÍ ANKI BALÍČKU ---
def vytvor_anki_balicek(data, nazev_balicku, smer_lat_ces, smer_ces_lat):
    model_id = 1607392319 
    
    sablony = []
    if smer_lat_ces:
        sablony.append({
            'name': 'Latina ➔ Čeština',
            'qfmt': '<div style="font-family: Arial; font-size: 26px; text-align: center; margin-top: 50px;">{{Latina}}</div>',
            'afmt': '{{FrontSide}}<hr id="answer"><div style="font-family: Arial; font-size: 22px; text-align: center;">{{Čeština}}</div>',
        })
    if smer_ces_lat:
        sablony.append({
            'name': 'Čeština ➔ Latina',
            'qfmt': '<div style="font-family: Arial; font-size: 26px; text-align: center; margin-top: 50px;">{{Čeština}}</div>',
            'afmt': '{{FrontSide}}<hr id="answer"><div style="font-family: Arial; font-size: 22px; text-align: center;">{{Latina}}</div>',
        })

    model = genanki.Model(
        model_id,
        'Základní Latina (Dvousměrná)',
        fields=[
            {'name': 'Latina'},
            {'name': 'Čeština'},
        ],
        templates=sablony
    )

    deck_id = random.randrange(1 << 30, 1 << 31)
    deck = genanki.Deck(deck_id, nazev_balicku)

    for _, row in data.iterrows():
        latina = str(row['Latina']).strip()
        cestina = str(row['Čeština']).strip()
        
        if not latina or not cestina:
            continue
            
        note = genanki.Note(
            model=model,
            fields=[latina, cestina]
        )
        deck.add_note(note)

    package = genanki.Package(deck)
    buffer = io.BytesIO()
    package.write_to_file(buffer)
    buffer.seek(0)
    return buffer

# ==============================================================================
# SPRÁVA KNIHOVNY BALÍČKŮ A ADMIN STATUS
# ==============================================================================
KNIHOVNA_JSON_PATH = os.path.join(DATA_DIR, "knihovna_balicku.json")
BALICKY_DIR = os.path.join(DATA_DIR, "balicky")
IMPORTY_DIR = os.path.join(DATA_DIR, "importy")
ZALOHY_DIR = os.path.join(DATA_DIR, "zalohy")
os.makedirs(IMPORTY_DIR, exist_ok=True)
os.makedirs(ZALOHY_DIR, exist_ok=True)

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

def publikuj_balicek(nazev: str, popis: str, smer_lat_ces: bool, smer_ces_lat: bool, df_data: pd.DataFrame) -> tuple[bool, str, str]:
    """Vygeneruje .apkg, uloží jej do data/balicky a zapíše metadata do knihovny."""
    os.makedirs(BALICKY_DIR, exist_ok=True)
    balicek_id = f"pkg_{int(time.time())}"
    bezpecny_nazev = "".join(c for c in nazev if c.isalnum() or c in (" ", "_", "-")).strip()
    if not bezpecny_nazev:
        bezpecny_nazev = "balicek"
    nazev_souboru = f"{bezpecny_nazev.replace(' ', '_')}_{balicek_id}.apkg"
    cesta_soubor = os.path.join(BALICKY_DIR, nazev_souboru)
    
    apkg_buf = vytvor_anki_balicek(df_data, nazev, smer_lat_ces, smer_ces_lat)
    try:
        with open(cesta_soubor, "wb") as f:
            f.write(apkg_buf.getvalue())
    except Exception as e:
        return False, f"Nepodařilo se uložit soubor balíčku: {e}", ""
        
    pocet_slov = len(df_data)
    pocet_karet = pocet_slov * (int(smer_lat_ces) + int(smer_ces_lat))
    datum_str = datetime.now().strftime("%d. %m. %Y %H:%M")
    
    karticky_list = []
    for _, r in df_data.iterrows():
        karticky_list.append({
            "Lekce": str(r.get("Lekce", "")),
            "Latina": str(r.get("Latina", "")),
            "Čeština": str(r.get("Čeština", "")),
            "Slovní druh": str(r.get("Slovní druh", ""))
        })
        
    zaznam = {
        "id": balicek_id,
        "nazev": nazev,
        "popis": popis.strip(),
        "datum": datum_str,
        "pocet_slovicek": pocet_slov,
        "pocet_karticek": pocet_karet,
        "smer_lat_ces": smer_lat_ces,
        "smer_ces_lat": smer_ces_lat,
        "soubor": cesta_soubor,
        "nazev_souboru": f"{bezpecny_nazev.replace(' ', '_')}.apkg",
        "karticky": karticky_list
    }
    
    knihovna = nacti_knihovnu()
    knihovna.insert(0, zaznam)
    if uloz_knihovnu(knihovna):
        return True, "Balíček byl úspěšně publikován do knihovny.", cesta_soubor
    return False, "Chyba při zápisu do databáze knihovny.", ""

def zobraz_sidebar_admin_status():
    """Zobrazí v postranním panelu informaci o přihlášení správce, pokud je aktivní."""
    if st.session_state.admin_prihlasen:
        st.sidebar.success("Přihlášen jako správce", icon=":material/verified_user:")
        if st.sidebar.button("Odhlásit se", icon=":material/logout:", key="global_sidebar_logout"):
            st.session_state.admin_prihlasen = False
            st.session_state.admin_selected_rows = []
            st.rerun()
        st.sidebar.markdown("---")

# ==============================================================================
# FILTROVÁNÍ DAT (SDÍLENÁ LOGIKA)
# ==============================================================================
def aplikuj_filtry(
    data: pd.DataFrame,
    vybrane_lekce: list,
    vybrane_druhy: list,
    vybrane_konjugace: list,
    vybrane_deklinace: list,
    hledany_text: str = ""
) -> pd.DataFrame:
    vsechny_lekce = set(l for l in data['Lekce'].unique() if l)
    vsechny_konj = set(k for k in data['Konjugace'].unique() if k)
    vsechny_dekl = set(d for d in data['Deklinace'].unique() if d)

    if set(vybrane_lekce) >= vsechny_lekce:
        df_f = data[data['Lekce'].isin(vybrane_lekce) | (data['Lekce'] == '')].copy()
    else:
        df_f = data[data['Lekce'].isin(vybrane_lekce)].copy()

    podminky = []

    for druh in vybrane_druhy:
        if druh == 'Sloveso':
            mask_slovesa = (df_f['Slovní druh'] == 'Sloveso')
            if set(vybrane_konjugace) >= vsechny_konj:
                podminky.append(mask_slovesa & (df_f['Konjugace'].isin(vybrane_konjugace) | (df_f['Konjugace'] == '')))
            else:
                podminky.append(mask_slovesa & df_f['Konjugace'].isin(vybrane_konjugace))
        elif druh == 'Podstatné jméno':
            mask_subst = (df_f['Slovní druh'] == 'Podstatné jméno')
            if set(vybrane_deklinace) >= vsechny_dekl:
                podminky.append(mask_subst & (df_f['Deklinace'].isin(vybrane_deklinace) | (df_f['Deklinace'] == '')))
            else:
                podminky.append(mask_subst & df_f['Deklinace'].isin(vybrane_deklinace))
        else:
            podminky.append(df_f['Slovní druh'] == druh)

    if podminky:
        finalni_maska = podminky[0]
        for m in podminky[1:]:
            finalni_maska = finalni_maska | m
        df_f = df_f[finalni_maska]
    else:
        df_f = pd.DataFrame(columns=data.columns)

    if hledany_text and hledany_text.strip():
        q = hledany_text.strip().lower()
        df_f = df_f[
            df_f['Latina'].str.lower().str.contains(q, na=False) |
            df_f['Čeština'].str.lower().str.contains(q, na=False)
        ]

    return df_f

# ==============================================================================
# POMOCNÉ FUNKCE PRO CALLBACKY A STAVY DIALOGŮ
# ==============================================================================
def reset_pridat_state():
    st.session_state.pridat_slovo_aktivni = False
    for k in ["dlg_add_latina", "dlg_add_cestina", "dlg_add_nova_lekce", "dlg_add_vyber_lekce", "dlg_add_druh", "dlg_add_konj", "dlg_add_dekl", "dlg_add_msg"]:
        st.session_state.pop(k, None)

def reset_edit_state():
    st.session_state.edit_row_idx = None
    for k in ["dlg_edit_latina", "dlg_edit_cestina", "dlg_edit_nova_lekce", "dlg_edit_vyber_lekce", "dlg_edit_druh", "dlg_edit_konj", "dlg_edit_dekl", "dlg_edit_msg", "dlg_edit_initialized_idx"]:
        st.session_state.pop(k, None)

def reset_delete_state():
    st.session_state.delete_row_idx = None

def reset_bulk_delete_state():
    st.session_state.bulk_delete_active = False

def reset_generator_balicek_state():
    st.session_state.generator_balicek_dialog = False

def reset_import_csv_state():
    st.session_state.import_csv_aktivni = False
    for k in [
        "csv_import_data",
        "csv_import_last_file_id",
        "csv_import_uploader",
        "csv_import_vychozi_lekce",
        "csv_import_dupl_rezim",
        "csv_import_data_editor"
    ]:
        st.session_state.pop(k, None)

def parsuj_import_csv(file_bytes: bytes, vychozi_lekce: str = "1") -> pd.DataFrame:
    """
    Načte a naparsuje CSV soubor se slovíčky (latinský slovníkový tvar; český překlad; číslo lekce).
    Podporuje oddělovače (; nebo , nebo tab), volitelné hlavičky i různé uspořádání sloupců.
    Pomocí druhy.py automaticky doplní mluvnické kategorie (druh, konjugace, deklinace).
    """
    text = file_bytes.decode('utf-8', errors='replace').strip()
    if not text:
        return pd.DataFrame(columns=['Lekce', 'Latina', 'Čeština', 'Slovní druh', 'Konjugace', 'Deklinace'])

    first_line = text.splitlines()[0]
    sep = ';' if ';' in first_line else (',' if ',' in first_line else '\t')

    reader = csv.reader(io.StringIO(text), delimiter=sep)
    rows = [r for r in reader if r and any(cell.strip() for cell in r)]
    if not rows:
        return pd.DataFrame(columns=['Lekce', 'Latina', 'Čeština', 'Slovní druh', 'Konjugace', 'Deklinace'])

    def norm(s):
        s_nfd = unicodedata.normalize('NFD', str(s).strip().lower())
        return ''.join(c for c in s_nfd if unicodedata.category(c) != 'Mn')

    def matches_any(s, substrings):
        s_norm = norm(s)
        return any(sub in s_norm for sub in substrings)

    is_lat = lambda s: matches_any(s, ['lat', 'tvar', 'slovo', 'word'])
    is_ces = lambda s: matches_any(s, ['ces', 'preklad', 'vyznam', 'czech'])
    is_lek = lambda s: matches_any(s, ['lekc', 'less', 'cislo'])

    first_row = [str(c).strip() for c in rows[0]]
    ma_hlavicku = any(is_lat(c) or is_ces(c) or is_lek(c) for c in first_row)

    data_rows = rows[1:] if ma_hlavicku else rows
    vysledek = []

    for r in data_rows:
        lat, ces, lek, druh, konj, dekl = '', '', '', '', '', ''
        if ma_hlavicku:
            col_dict = {first_row[i]: r[i].strip() for i in range(min(len(first_row), len(r)))}
            for col_name, val in col_dict.items():
                if is_lat(col_name) and not lat: lat = val
                elif is_ces(col_name) and not ces: ces = val
                elif is_lek(col_name) and not lek: lek = val
                elif 'druh' in col_name or 'pos' in col_name: druh = val
                elif 'konj' in col_name: konj = val
                elif 'dekl' in col_name: dekl = val
        else:
            vals = [str(c).strip() for c in r]
            if len(vals) == 1:
                if '||' in vals[0]:
                    parts = vals[0].split('||', 1)
                    lat, ces = parts[0].strip(), parts[1].strip()
                else:
                    continue
            elif len(vals) == 2:
                if '||' in vals[0]:
                    parts = vals[0].split('||', 1)
                    lat, ces = parts[0].strip(), parts[1].strip()
                    lek = vals[1]
                elif vals[0].isdigit() and not vals[1].isdigit():
                    lek, lat = vals[0], vals[1]
                else:
                    lat, ces = vals[0], vals[1]
            elif len(vals) >= 3:
                # 3 nebo více sloupců
                if (vals[0].isdigit() or vals[0].lower().startswith('lekce')) and not vals[2].isdigit():
                    lek, lat, ces = vals[0], vals[1], vals[2]
                    if len(vals) >= 4: druh = vals[3]
                    if len(vals) >= 5: konj = vals[4]
                    if len(vals) >= 6: dekl = vals[5]
                else:
                    lat, ces, lek = vals[0], vals[1], vals[2]
                    if len(vals) >= 4: druh = vals[3]
                    if len(vals) >= 5: konj = vals[4]
                    if len(vals) >= 6: dekl = vals[5]

        lat = str(lat).strip()
        ces = str(ces).strip()
        lek = str(lek).strip() or str(vychozi_lekce).strip()

        if not lat or not ces:
            continue

        if not druh:
            a_druh, a_konj, a_dekl = analyzuj_slovicko(lat)
            druh = a_druh
            if not konj: konj = a_konj
            if not dekl: dekl = a_dekl

        vysledek.append({
            'Lekce': lek,
            'Latina': lat,
            'Čeština': ces,
            'Slovní druh': druh,
            'Konjugace': konj,
            'Deklinace': dekl
        })

    return pd.DataFrame(vysledek)

def dopln_mluvnicke_kategorie_do_state(latina_key: str, druh_key: str, konj_key: str, dekl_key: str, msg_key: str):
    lat = st.session_state.get(latina_key, "").strip()
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
        detaily.append(f"{dekl}. deklinace" if dekl != "nesklonná" else "nesklonné podst. jméno")
    st.session_state[msg_key] = ("success", f"Mluvnické kategorie doplněny: {', '.join(detaily)}")

def on_add_doplnit_kategorie():
    dopln_mluvnicke_kategorie_do_state(
        "dlg_add_latina", "dlg_add_druh", "dlg_add_konj", "dlg_add_dekl", "dlg_add_msg"
    )

def on_edit_doplnit_kategorie():
    dopln_mluvnicke_kategorie_do_state(
        "dlg_edit_latina", "dlg_edit_druh", "dlg_edit_konj", "dlg_edit_dekl", "dlg_edit_msg"
    )

def on_admin_edit_click():
    click = st.session_state.get("btn_admin_edit")
    if click is not None and hasattr(click, "row"):
        st.session_state.edit_row_idx = click.row
    elif click is not None and isinstance(click, dict) and "row" in click:
        st.session_state.edit_row_idx = click["row"]

def on_admin_delete_click():
    click = st.session_state.get("btn_admin_delete")
    if click is not None and hasattr(click, "row"):
        st.session_state.delete_row_idx = click.row
    elif click is not None and isinstance(click, dict) and "row" in click:
        st.session_state.delete_row_idx = click["row"]

# ==============================================================================
# MODÁLNÍ DIALOGY ADMINISTRACE
# ==============================================================================
@st.dialog("Přidat nové slovíčko", on_dismiss=reset_pridat_state, width="large")
def dialog_pridat_slovo(aktualni_df: pd.DataFrame):
    existujici_lekce = sorted(list(set(aktualni_df['Lekce'].unique()) - {''}), key=klic_serazeni_lekci)
    if not existujici_lekce:
        existujici_lekce = ["1"]

    st.session_state.setdefault("dlg_add_druh", "Podstatné jméno")
    st.session_state.setdefault("dlg_add_konj", "")
    st.session_state.setdefault("dlg_add_dekl", "")

    if "dlg_add_msg" in st.session_state:
        m_t, m_v = st.session_state.pop("dlg_add_msg")
        if m_t == "success":
            st.success(m_v, icon=":material/auto_fix_high:")
        else:
            st.warning(m_v)

    col_lekce1, col_lekce2 = st.columns(2)
    with col_lekce1:
        vyber_lekce = st.selectbox("Existující lekce:", options=existujici_lekce, key="dlg_add_vyber_lekce")
    with col_lekce2:
        nova_lekce = st.text_input(
            "Nebo zadejte nové číslo/název lekce:",
            placeholder="např. 6 (ponechte prázdné pro existující)",
            key="dlg_add_nova_lekce"
        )

    col_lat, col_ces = st.columns(2)
    with col_lat:
        nova_latina = st.text_input(
            "Latina *",
            placeholder="např. victoria, ae, f",
            help="Latinské slovo nebo fráze včetně gramatických koncovek",
            key="dlg_add_latina"
        )
    with col_ces:
        nova_cestina = st.text_input(
            "Čeština *",
            placeholder="např. vítězství",
            help="Český překlad",
            key="dlg_add_cestina"
        )

    # Tlačítko pro automatické doplnění mluvnických kategorií
    st.button(
        "Doplnit mluvnické kategorie",
        icon=":material/auto_fix_high:",
        on_click=on_add_doplnit_kategorie,
        key="btn_add_autofill",
        help="Automaticky určí slovní druh, konjugaci nebo deklinaci ze zadaného latinského tvaru podle pravidel v druhy.py"
    )

    druhy_moznosti = ["Podstatné jméno", "Přídavné jméno", "Sloveso", "Zájmeno", "Ostatní"]
    konj_moznosti = ["", "1", "2", "3", "4", "nepravidelná"]
    dekl_moznosti = ["", "1", "2", "3", "4", "5", "nesklonná"]

    col_druh, col_gram1, col_gram2 = st.columns(3)
    with col_druh:
        novy_druh = st.selectbox(
            "Slovní druh *",
            options=druhy_moznosti,
            key="dlg_add_druh"
        )
    with col_gram1:
        nova_konj = st.selectbox(
            "Konjugace (pro slovesa):",
            options=konj_moznosti,
            help="Vyplňte pouze u sloves",
            key="dlg_add_konj"
        )
    with col_gram2:
        nova_dekl = st.selectbox(
            "Deklinace (pro podstatná jména):",
            options=dekl_moznosti,
            help="Vyplňte pouze u podstatných jmen",
            key="dlg_add_dekl"
        )

    st.markdown("<small>* Označená pole jsou povinná.</small>", unsafe_allow_html=True)
    with st.container(horizontal=True):
        tlacitko_ulozit = st.button(
            "Uložit kartičku",
            type="primary",
            icon=":material/add_circle:",
            key="btn_add_save"
        )
        tlacitko_zrusit = st.button("Zrušit", key="btn_add_cancel")

    if tlacitko_zrusit:
        reset_pridat_state()
        st.rerun()

    if tlacitko_ulozit:
        finalni_lekce = nova_lekce.strip() if nova_lekce.strip() else vyber_lekce.strip()
        latina_clean = nova_latina.strip()
        cestina_clean = nova_cestina.strip()

        if not finalni_lekce:
            st.error("Vyberte nebo zadejte číslo lekce.")
        elif not latina_clean:
            st.error("Pole 'Latina' je povinné.")
        elif not cestina_clean:
            st.error("Pole 'Čeština' je povinné.")
        else:
            duplicita = aktualni_df[
                (aktualni_df['Latina'].str.lower() == latina_clean.lower()) &
                (aktualni_df['Lekce'] == finalni_lekce)
            ]
            if len(duplicita) > 0:
                st.warning(f"Upozornění: Slovíčko '{latina_clean}' v lekci {finalni_lekce} již v databázi existuje.")

            novy_zaznam = pd.DataFrame([{
                "Lekce": finalni_lekce,
                "Latina": latina_clean,
                "Čeština": cestina_clean,
                "Slovní druh": novy_druh,
                "Konjugace": nova_konj if novy_druh == "Sloveso" else "",
                "Deklinace": nova_dekl if novy_druh == "Podstatné jméno" else ""
            }])

            df_nove = pd.concat([aktualni_df, novy_zaznam], ignore_index=True)
            if uloz_data(df_nove):
                st.session_state["admin_zprava"] = f"Kartička '{latina_clean}' byla úspěšně přidána do lekce {finalni_lekce}."
                reset_pridat_state()
                st.rerun()

@st.dialog("Importovat slovíčka z CSV", on_dismiss=reset_import_csv_state, width="large")
def dialog_import_csv(aktualni_df: pd.DataFrame):
    st.write(
        "Nahrajte CSV soubor obsahující **latinský slovníkový tvar; český překlad; číslo lekce**. "
        "Mluvnické kategorie (slovní druh, deklinace, konjugace) budou automaticky určeny a doplněny. "
        "Před přidáním do slovníku můžete v tabulce libovolná slovíčka zkontrolovat a upravit."
    )

    col_cfg1, col_cfg2 = st.columns([1, 1])
    with col_cfg1:
        vychozi_lekce = st.text_input(
            "Výchozí číslo lekce (pokud v souboru chybí):",
            value=st.session_state.get("csv_import_vychozi_lekce", "1"),
            key="csv_import_vychozi_lekce"
        )
    with col_cfg2:
        duplicity_rezim = st.selectbox(
            "Pokud slovíčko již v dané lekci ve slovníku existuje:",
            options=["Přeskočit existující v dané lekci", "Povolit duplicity"],
            key="csv_import_dupl_rezim"
        )

    uploaded_file = st.file_uploader(
        "Vyberte CSV soubor:",
        type=["csv", "txt"],
        key="csv_import_uploader",
        help="Podporované oddělovače: středník (;), čárka (,), tabulátor."
    )

    if uploaded_file is not None:
        file_id = f"{uploaded_file.name}_{uploaded_file.size}_{vychozi_lekce}"
        if st.session_state.get("csv_import_last_file_id") != file_id:
            df_parsed = parsuj_import_csv(uploaded_file.getvalue(), vychozi_lekce)
            st.session_state.csv_import_data = df_parsed
            st.session_state.csv_import_last_file_id = file_id

    df_k_uprave = st.session_state.get("csv_import_data")

    if df_k_uprave is not None:
        if df_k_uprave.empty:
            st.warning("V nahraném souboru nebyla nalezena žádná platná slovíčka.")
        else:
            st.info(
                f"📋 Načteno **{len(df_k_uprave)}** slovíček. V tabulce můžete libovolnou hodnotu upravit "
                "nebo nevhodné řádky smazat před přidáním do slovníku:"
            )

            druhy_moznosti = ["Podstatné jméno", "Přídavné jméno", "Sloveso", "Zájmeno", "Ostatní"]
            konj_moznosti = ["", "1", "2", "3", "4", "nepravidelná"]
            dekl_moznosti = ["", "1", "2", "3", "4", "5", "nesklonná"]

            edited_df = st.data_editor(
                df_k_uprave,
                num_rows="dynamic",
                width="stretch",
                column_config={
                    "Lekce": st.column_config.TextColumn("Lekce", required=True, width="small"),
                    "Latina": st.column_config.TextColumn("Latina", required=True, width="medium"),
                    "Čeština": st.column_config.TextColumn("Čeština", required=True, width="medium"),
                    "Slovní druh": st.column_config.SelectboxColumn("Slovní druh", options=druhy_moznosti, required=True, width="medium"),
                    "Konjugace": st.column_config.SelectboxColumn("Konjugace (slovesa)", options=konj_moznosti, width="small"),
                    "Deklinace": st.column_config.SelectboxColumn("Deklinace (podst. jména)", options=dekl_moznosti, width="small"),
                },
                key="csv_import_data_editor"
            )

            mask_valid = (edited_df['Latina'].astype(str).str.strip() != "") & (edited_df['Čeština'].astype(str).str.strip() != "")
            pocet_platnych = int(mask_valid.sum())

            with st.container(horizontal=True):
                tlacitko_import = st.button(
                    f"Přidat do slovníku ({pocet_platnych} slov)",
                    type="primary",
                    icon=":material/library_add:",
                    key="btn_csv_import_save",
                    disabled=(pocet_platnych == 0)
                )
                tlacitko_zrusit = st.button("Zrušit", key="btn_csv_import_cancel")

            if tlacitko_zrusit:
                reset_import_csv_state()
                st.rerun()

            if tlacitko_import:
                df_ciste = edited_df[mask_valid].copy()
                df_ciste['Latina'] = df_ciste['Latina'].astype(str).str.strip()
                df_ciste['Čeština'] = df_ciste['Čeština'].astype(str).str.strip()
                df_ciste['Lekce'] = df_ciste['Lekce'].astype(str).str.strip()
                df_ciste['Slovní druh'] = df_ciste['Slovní druh'].astype(str).str.strip()
                df_ciste['Konjugace'] = df_ciste['Konjugace'].astype(str).str.strip()
                df_ciste['Deklinace'] = df_ciste['Deklinace'].astype(str).str.strip()

                preskoceno = 0
                if duplicity_rezim == "Přeskočit existující v dané lekci":
                    existujici_pary = set(zip(aktualni_df['Latina'].str.lower(), aktualni_df['Lekce'].astype(str)))
                    mask_nova = [
                        (row['Latina'].lower(), str(row['Lekce'])) not in existujici_pary
                        for _, row in df_ciste.iterrows()
                    ]
                    preskoceno = len(df_ciste) - sum(mask_nova)
                    df_k_pridani = df_ciste[mask_nova]
                else:
                    df_k_pridani = df_ciste

                if len(df_k_pridani) == 0:
                    st.warning("Nebyla přidána žádná nová slovíčka (všechna již v databázi existují).")
                else:
                    # 1. Automatická záloha stávající databáze
                    cas_razitko = datetime.now().strftime("%Y%m%d_%H%M%S")
                    cesta_zaloha = os.path.join(ZALOHY_DIR, f"slovicka_zaloha_{cas_razitko}.csv")
                    aktualni_df.to_csv(cesta_zaloha, sep=";", index=False, encoding="utf-8")

                    # 2. Uložení samostatného CSV importu do složky data/importy/
                    lekce_list = sorted(list(set(df_k_pridani['Lekce'])))
                    lekce_tag = f"lekce_{lekce_list[0]}-{lekce_list[-1]}" if len(lekce_list) > 1 else f"lekce_{lekce_list[0]}" if lekce_list else "import"
                    cesta_import = os.path.join(IMPORTY_DIR, f"{lekce_tag}_{cas_razitko}.csv")
                    df_k_pridani.to_csv(cesta_import, sep=";", index=False, encoding="utf-8")

                    # 3. Přidání do master databáze slovíček
                    df_nove = pd.concat([aktualni_df, df_k_pridani], ignore_index=True)
                    if uloz_data(df_nove):
                        msg = f"Úspěšně přidáno {len(df_k_pridani)} slovíček do slovníku!"
                        if preskoceno > 0:
                            msg += f" (Přeskočeno {preskoceno} duplicitních slovíček.)"
                        msg += f" Samostatné CSV bylo uloženo do '{cesta_import}'."
                        st.session_state["admin_zprava"] = msg
                        reset_import_csv_state()
                        st.rerun()
    else:
        with st.container(horizontal=True):
            if st.button("Zavřít", key="btn_csv_import_close"):
                reset_import_csv_state()
                st.rerun()

@st.dialog("Upravit slovíčko", on_dismiss=reset_edit_state, width="large")
def dialog_upravit_slovo(real_idx: int, aktualni_df: pd.DataFrame):
    if real_idx not in aktualni_df.index:
        reset_edit_state()
        st.rerun()

    karta = aktualni_df.loc[real_idx]
    existujici_lekce = sorted(list(set(aktualni_df['Lekce'].unique()) - {''}), key=klic_serazeni_lekci)
    if not existujici_lekce:
        existujici_lekce = ["1"]

    druhy_moznosti = ["Podstatné jméno", "Přídavné jméno", "Sloveso", "Zájmeno", "Ostatní"]
    konj_moznosti = ["", "1", "2", "3", "4", "nepravidelná"]
    dekl_moznosti = ["", "1", "2", "3", "4", "5", "nesklonná"]

    # Inicializace hodnot při prvním otevření editace pro danou kartu
    if st.session_state.get("dlg_edit_initialized_idx") != real_idx:
        lekce_val = str(karta['Lekce']).strip()
        if lekce_val.endswith('.0'): lekce_val = lekce_val[:-2]
        if lekce_val == 'nan': lekce_val = ''

        druh_val = str(karta['Slovní druh']).strip()
        if druh_val not in druhy_moznosti: druh_val = "Ostatní"

        konj_val = str(karta['Konjugace']).strip()
        if konj_val.endswith('.0'): konj_val = konj_val[:-2]
        if konj_val == 'nan' or konj_val not in konj_moznosti: konj_val = ''

        dekl_val = str(karta['Deklinace']).strip()
        if dekl_val.endswith('.0'): dekl_val = dekl_val[:-2]
        if dekl_val == 'nan' or dekl_val not in dekl_moznosti: dekl_val = ''

        st.session_state.dlg_edit_initialized_idx = real_idx
        st.session_state.dlg_edit_vyber_lekce = lekce_val if lekce_val in existujici_lekce else existujici_lekce[0]
        st.session_state.dlg_edit_nova_lekce = "" if lekce_val in existujici_lekce else lekce_val
        st.session_state.dlg_edit_latina = str(karta['Latina'])
        st.session_state.dlg_edit_cestina = str(karta['Čeština'])
        st.session_state.dlg_edit_druh = druh_val
        st.session_state.dlg_edit_konj = konj_val
        st.session_state.dlg_edit_dekl = dekl_val
        st.session_state.pop("dlg_edit_msg", None)

    if "dlg_edit_msg" in st.session_state:
        m_t, m_v = st.session_state.pop("dlg_edit_msg")
        if m_t == "success":
            st.success(m_v, icon=":material/auto_fix_high:")
        else:
            st.warning(m_v)

    col_lekce1, col_lekce2 = st.columns(2)
    with col_lekce1:
        vyber_lekce = st.selectbox("Existující lekce:", options=existujici_lekce, key="dlg_edit_vyber_lekce")
    with col_lekce2:
        nova_lekce = st.text_input(
            "Nebo zadejte nové číslo/název lekce:",
            placeholder="např. 6 (ponechte prázdné pro existující)",
            key="dlg_edit_nova_lekce"
        )

    col_lat, col_ces = st.columns(2)
    with col_lat:
        nova_latina = st.text_input(
            "Latina *",
            placeholder="např. victoria, ae, f",
            help="Latinské slovo nebo fráze včetně gramatických koncovek",
            key="dlg_edit_latina"
        )
    with col_ces:
        nova_cestina = st.text_input(
            "Čeština *",
            placeholder="např. vítězství",
            help="Český překlad",
            key="dlg_edit_cestina"
        )

    # Tlačítko pro automatické doplnění mluvnických kategorií
    st.button(
        "Doplnit mluvnické kategorie",
        icon=":material/auto_fix_high:",
        on_click=on_edit_doplnit_kategorie,
        key="btn_edit_autofill",
        help="Automaticky určí slovní druh, konjugaci nebo deklinaci ze zadaného latinského tvaru podle pravidel v druhy.py"
    )

    col_druh, col_gram1, col_gram2 = st.columns(3)
    with col_druh:
        novy_druh = st.selectbox(
            "Slovní druh *",
            options=druhy_moznosti,
            key="dlg_edit_druh"
        )
    with col_gram1:
        nova_konj = st.selectbox(
            "Konjugace (pro slovesa):",
            options=konj_moznosti,
            help="Vyplňte pouze u sloves",
            key="dlg_edit_konj"
        )
    with col_gram2:
        nova_dekl = st.selectbox(
            "Deklinace (pro podstatná jména):",
            options=dekl_moznosti,
            help="Vyplňte pouze u podstatných jmen",
            key="dlg_edit_dekl"
        )

    st.markdown("<small>* Označená pole jsou povinná.</small>", unsafe_allow_html=True)
    with st.container(horizontal=True):
        tlacitko_ulozit = st.button(
            "Uložit změny",
            type="primary",
            icon=":material/save:",
            key="btn_edit_save"
        )
        tlacitko_zrusit = st.button("Zrušit", key="btn_edit_cancel")

    if tlacitko_zrusit:
        reset_edit_state()
        st.rerun()

    if tlacitko_ulozit:
        finalni_lekce = nova_lekce.strip() if nova_lekce.strip() else vyber_lekce.strip()
        latina_clean = nova_latina.strip()
        cestina_clean = nova_cestina.strip()

        if not finalni_lekce:
            st.error("Vyberte nebo zadejte číslo lekce.")
        elif not latina_clean:
            st.error("Pole 'Latina' je povinné.")
        elif not cestina_clean:
            st.error("Pole 'Čeština' je povinné.")
        else:
            duplicita = aktualni_df[
                (aktualni_df.index != real_idx) &
                (aktualni_df['Latina'].str.lower() == latina_clean.lower()) &
                (aktualni_df['Lekce'] == finalni_lekce)
            ]
            if len(duplicita) > 0:
                st.warning(f"Upozornění: Slovíčko '{latina_clean}' v lekci {finalni_lekce} již v databázi existuje.")

            aktualni_df.loc[real_idx, "Lekce"] = finalni_lekce
            aktualni_df.loc[real_idx, "Latina"] = latina_clean
            aktualni_df.loc[real_idx, "Čeština"] = cestina_clean
            aktualni_df.loc[real_idx, "Slovní druh"] = novy_druh
            aktualni_df.loc[real_idx, "Konjugace"] = nova_konj if novy_druh == "Sloveso" else ""
            aktualni_df.loc[real_idx, "Deklinace"] = nova_dekl if novy_druh == "Podstatné jméno" else ""

            if uloz_data(aktualni_df):
                st.session_state["admin_zprava"] = f"Kartička '{latina_clean}' byla úspěšně upravena."
                reset_edit_state()
                st.rerun()

@st.dialog("Smazat kartičku", on_dismiss=reset_delete_state)
def dialog_smazat_slovo(real_idx: int, aktualni_df: pd.DataFrame):
    if real_idx not in aktualni_df.index:
        reset_delete_state()
        st.rerun()

    karta = aktualni_df.loc[real_idx]
    st.warning("Opravdu chcete trvale smazat tuto kartičku?", icon=":material/warning:")
    with st.container(border=True):
        st.markdown(f"**Latina:** `{karta['Latina']}`")
        st.markdown(f"**Čeština:** {karta['Čeština']}")
        st.caption(f"Lekce: {karta['Lekce']} | Slovní druh: {karta['Slovní druh']}")

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        if st.button("Smazat kartičku", type="primary", icon=":material/delete:"):
            lat = karta['Latina']
            df_novy = aktualni_df.drop(index=real_idx).reset_index(drop=True)
            if uloz_data(df_novy):
                st.session_state["admin_zprava"] = f"Kartička '{lat}' byla úspěšně smazána."
            reset_delete_state()
            st.session_state.admin_selected_rows = []
            st.session_state.admin_table_ver += 1
            st.rerun()
    with col_d2:
        if st.button("Zrušit"):
            reset_delete_state()
            st.rerun()

@st.dialog("Hromadné smazání kartiček", on_dismiss=reset_bulk_delete_state)
def dialog_hromadne_smazani(selected_indices: list, aktualni_df: pd.DataFrame):
    st.warning(f"Opravdu chcete trvale smazat **{len(selected_indices)}** vybraných kartiček?", icon=":material/warning:")
    
    ukazka = aktualni_df.loc[selected_indices, ['Lekce', 'Latina', 'Čeština', 'Slovní druh']].head(5)
    st.dataframe(ukazka, hide_index=True)
    if len(selected_indices) > 5:
        st.caption(f"... a dalších {len(selected_indices) - 5} kartiček.")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Potvrdit smazání", type="primary", icon=":material/delete_forever:"):
            df_novy = aktualni_df.drop(index=selected_indices).reset_index(drop=True)
            if uloz_data(df_novy):
                st.session_state["admin_zprava"] = f"Úspěšně smazáno {len(selected_indices)} kartiček."
            reset_bulk_delete_state()
            st.session_state.admin_selected_rows = []
            st.session_state.admin_table_ver += 1
            st.rerun()
    with col2:
        if st.button("Zrušit"):
            reset_bulk_delete_state()
            st.rerun()

@st.dialog("Vytvořit balíček pro Anki", on_dismiss=reset_generator_balicek_state, width="medium")
def dialog_vytvorit_balicek(df_filtered: pd.DataFrame):
    st.write(f"Vybráno slovíček k exportu: **{len(df_filtered)}**")

    nazev = st.text_input(
        "Název balíčku (jak se bude jmenovat v Anki):",
        value=st.session_state.get("nazev_balicku", "Latina - Můj výběr"),
        key="dlg_gen_nazev_balicku"
    )

    popis = st.text_area(
        "Popis balíčku (volitelný, zobrazí se v knihovně):",
        placeholder="Např. Výběr sloves a podstatných jmen z 1. a 2. lekce...",
        key="dlg_gen_popis_balicku"
    )

    st.markdown("##### Typy kartiček (Směr učení)")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        smer_lat_ces = st.checkbox("Latina ➔ Čeština", value=True, key="dlg_gen_smer_lat_ces")
    with col_s2:
        smer_ces_lat = st.checkbox("Čeština ➔ Latina", value=True, key="dlg_gen_smer_ces_lat")

    if not smer_lat_ces and not smer_ces_lat:
        st.warning("⚠️ Vyberte alespoň jeden směr kartiček (např. Latina ➔ Čeština).")
    else:
        aktualni_nazev = nazev.strip() or "Latina - Můj výběr"
        st.session_state.nazev_balicku = aktualni_nazev
        pocet_karet = len(df_filtered) * (int(smer_lat_ces) + int(smer_ces_lat))
        st.info(f"📊 Celkem bude vygenerováno **{pocet_karet}** karet pro Anki.")

        apkg_soubor = vytvor_anki_balicek(df_filtered, aktualni_nazev, smer_lat_ces, smer_ces_lat)

        st.markdown("---")
        st.markdown("##### Možnosti uložení")


        col_b1, col_b2 = st.columns(2)
        with col_b1:
            st.download_button(
                label=f"📥 Stáhnout do počítače ({pocet_karet} karet)",
                data=apkg_soubor,
                file_name=f"{aktualni_nazev.replace(' ', '_')}.apkg",
                mime="application/octet-stream",
                key="btn_dl_direct",
                help="Stáhne balíček do zařízení bez uložení do veřejné knihovny",
                width="stretch"
            )

        with col_b2:
            if st.session_state.admin_prihlasen:
                if st.button("🚀 Publikovat a stáhnout balíček", type="primary", icon=":material/publish:", key="btn_pub_admin", width="stretch"):
                    ok, msg, cesta = publikuj_balicek(aktualni_nazev, popis, smer_lat_ces, smer_ces_lat, df_filtered)
                    if ok:
                        st.session_state["pub_success"] = (aktualni_nazev, apkg_soubor.getvalue())
                        st.rerun()
                    else:
                        st.error(msg)
            else:
                with st.popover("🚀 Publikovat a stáhnout balíček", icon=":material/publish:", type="primary", width="stretch"):
                    st.caption("Pro publikování balíčku do veřejné knihovny zadejte heslo správce:")
                    heslo_pub = st.text_input("Administrátorské heslo:", type="password", key="dlg_pub_heslo")
                    if st.button("Ověřit a publikovat", type="primary", icon=":material/publish:", key="btn_pub_guest", width="stretch"):
                        spravne = nacti_admin_heslo()
                        if over_heslo(heslo_pub, spravne):
                            st.session_state.admin_prihlasen = True
                            ok, msg, cesta = publikuj_balicek(aktualni_nazev, popis, smer_lat_ces, smer_ces_lat, df_filtered)
                            if ok:
                                st.session_state["pub_success"] = (aktualni_nazev, apkg_soubor.getvalue())
                                st.rerun()
                            else:
                                st.error(msg)
                        else:
                            st.error("Nesprávné administrátorské heslo.")

        if "pub_success" in st.session_state:
            p_nazev, p_bytes = st.session_state.pop("pub_success")
            st.success(f"🎉 Balíček '{p_nazev}' byl úspěšně publikován do Knihovny kartiček!", icon=":material/check_circle:")
            st.download_button(
                label=f"📥 Stáhnout publikovaný balíček ({pocet_karet} karet)",
                data=p_bytes,
                file_name=f"{p_nazev.replace(' ', '_')}.apkg",
                mime="application/octet-stream",
                type="primary",
                key="btn_dl_pub_done"
            )

# ==============================================================================
# STRÁNKA 1: GENERÁTOR KARTIČEK
# ==============================================================================
def stranka_generator():
    st.title("📚 Generátor latinských kartiček pro Anki")

    zobraz_sidebar_admin_status()

    # Boční panel pro filtry generátoru
    st.sidebar.header("Nastavení filtru")

    # 1. Filtr Lekcí
    dostupne_lekce = sorted([l for l in df['Lekce'].unique() if l], key=klic_serazeni_lekci)
    vybrane_lekce = st.sidebar.multiselect(
        "Vyber lekce:", 
        options=dostupne_lekce, 
        default=dostupne_lekce,
        key="gen_lekce"
    )

    # 2. Filtr Slovních druhů
    dostupne_druhy = sorted([d for d in df['Slovní druh'].unique() if d])
    vybrane_druhy = st.sidebar.multiselect(
        "Vyber slovní druhy:", 
        options=dostupne_druhy, 
        default=dostupne_druhy,
        key="gen_druhy"
    )

    chci_slovesa = 'Sloveso' in vybrane_druhy
    chci_substantiva = 'Podstatné jméno' in vybrane_druhy

    # 3. Dodatečné filtry (Konjugace / Deklinace)
    vybrane_konjugace = []
    if chci_slovesa:
        dostupne_konj = sorted([k for k in df['Konjugace'].unique() if k])
        vybrane_konjugace = st.sidebar.multiselect(
            "Vyber konjugace (jen pro slovesa):", 
            options=dostupne_konj, 
            default=dostupne_konj,
            key="gen_konj"
        )

    vybrane_deklinace = []
    if chci_substantiva:
        dostupne_dekl = sorted([d for d in df['Deklinace'].unique() if d])
        vybrane_deklinace = st.sidebar.multiselect(
            "Vyber deklinace (jen pro podst. jména):", 
            options=dostupne_dekl, 
            default=dostupne_dekl,
            key="gen_dekl"
        )

    # Aplikování filtrů na data pomocí sdílené funkce
    df_filtered = aplikuj_filtry(df, vybrane_lekce, vybrane_druhy, vybrane_konjugace, vybrane_deklinace)

    # Hlavní plocha (zobrazení a export)
    st.subheader(f"Nalezeno slovíček: {len(df_filtered)}")

    st.dataframe(
        df_filtered[['Lekce', 'Latina', 'Čeština', 'Slovní druh']],
        alt="Seznam filtrovaných latinských slovíček"
    )

    # Tlačítko pro vytvoření balíčku
    col_btn, _ = st.columns([1, 2])
    with col_btn:
        tlacitko_vytvorit = st.button(
            "Vytvořit balíček",
            type="primary",
            icon=":material/style:",
            disabled=(len(df_filtered) == 0),
            help="Otevře nastavení a umožní stáhnout balíček pro Anki ze zobrazených slovíček"
        )
        if tlacitko_vytvorit:
            st.session_state.generator_balicek_dialog = True
            st.rerun()

    if st.session_state.get("generator_balicek_dialog", False):
        if len(df_filtered) > 0:
            dialog_vytvorit_balicek(df_filtered)
        else:
            st.session_state.generator_balicek_dialog = False

# ==============================================================================
# STRÁNKA 2: KNIHOVNA KARTIČEK
# ==============================================================================
def stranka_knihovna():
    st.title("📚 Knihovna kartiček")

    zobraz_sidebar_admin_status()

    knihovna = nacti_knihovnu()

    st.sidebar.header("Knihovna")
    hledat_text = st.sidebar.text_input(
        "Hledat v knihovně:",
        placeholder="Název nebo popis...",
        icon=":material/search:",
        key="lib_search"
    )
    st.sidebar.metric("Celkem balíčků v knihovně", len(knihovna))

    if hledat_text and hledat_text.strip():
        q = hledat_text.strip().lower()
        knihovna = [
            b for b in knihovna
            if q in b.get("nazev", "").lower() or q in b.get("popis", "").lower()
        ]

    if not knihovna:
        if hledat_text:
            st.info(f"Žádný balíček neodpovídá hledanému výrazu '{hledat_text}'.", icon=":material/search_off:")
        else:
            st.info(
                "V knihovně zatím nejsou žádné balíčky kartiček. "
                "Vytvořit a publikovat balíček můžete v záložce **Generátor kartiček**.",
                icon=":material/info:"
            )
        return

    st.subheader(f"Dostupné balíčky ({len(knihovna)})")

    for b in knihovna:
        b_id = b.get("id", "")
        with st.container(border=True):
            st.subheader(f"🎴 {b.get('nazev', 'Bez názvu')}")
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
                        key=f"dl_btn_{b_id}"
                    )

                if st.session_state.admin_prihlasen:
                    if st.button("Smazat balíček", icon=":material/delete:", type="secondary", key=f"del_btn_{b_id}"):
                        st.session_state[f"confirm_del_{b_id}"] = True
                        st.rerun()

            if st.session_state.get(f"confirm_del_{b_id}", False):
                st.warning(f"Opravdu si přejete trvale smazat balíček **{b.get('nazev')}** z knihovny?")
                with st.container(horizontal=True):
                    if st.button("Ano, smazat", type="primary", icon=":material/delete_forever:", key=f"conf_yes_{b_id}"):
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
                    st.dataframe(df_kart, hide_index=True, width="stretch")

# ==============================================================================
# STRÁNKA 3: SLOVNÍK (PŘEJMENOVANÁ ADMINISTRACE)
# ==============================================================================
def stranka_slovnik():
    st.title("📖 Slovník latinských slovíček")

    zobraz_sidebar_admin_status()

    # Inicializace session state pro slovník
    if "admin_selected_rows" not in st.session_state:
        st.session_state.admin_selected_rows = []
    if "admin_table_ver" not in st.session_state:
        st.session_state.admin_table_ver = 0
    if "pridat_slovo_aktivni" not in st.session_state:
        st.session_state.pridat_slovo_aktivni = False
    if "import_csv_aktivni" not in st.session_state:
        st.session_state.import_csv_aktivni = False
    if "edit_row_idx" not in st.session_state:
        st.session_state.edit_row_idx = None
    if "delete_row_idx" not in st.session_state:
        st.session_state.delete_row_idx = None
    if "bulk_delete_active" not in st.session_state:
        st.session_state.bulk_delete_active = False

    # Zobrazení případné uložené zprávy z předchozí akce
    if "admin_zprava" in st.session_state:
        st.success(st.session_state.pop("admin_zprava"), icon=":material/check_circle:")

    # Filtry v postranním panelu
    st.sidebar.header("Nastavení filtru")
    st.sidebar.metric("Celkem kartiček v databázi", len(df))

    dostupne_lekce = sorted([l for l in df['Lekce'].unique() if l], key=klic_serazeni_lekci)
    vybrane_lekce = st.sidebar.multiselect(
        "Vyber lekce:", 
        options=dostupne_lekce, 
        default=dostupne_lekce,
        key="admin_lekce"
    )

    dostupne_druhy = sorted([d for d in df['Slovní druh'].unique() if d])
    vybrane_druhy = st.sidebar.multiselect(
        "Vyber slovní druhy:", 
        options=dostupne_druhy, 
        default=dostupne_druhy,
        key="admin_druhy"
    )

    chci_slovesa = 'Sloveso' in vybrane_druhy
    chci_substantiva = 'Podstatné jméno' in vybrane_druhy

    vybrane_konjugace = []
    if chci_slovesa:
        dostupne_konj = sorted([k for k in df['Konjugace'].unique() if k])
        vybrane_konjugace = st.sidebar.multiselect(
            "Vyber konjugace (jen pro slovesa):", 
            options=dostupne_konj, 
            default=dostupne_konj,
            key="admin_konj"
        )

    vybrane_deklinace = []
    if chci_substantiva:
        dostupne_dekl = sorted([d for d in df['Deklinace'].unique() if d])
        vybrane_deklinace = st.sidebar.multiselect(
            "Vyber deklinace (jen pro podst. jména):", 
            options=dostupne_dekl, 
            default=dostupne_dekl,
            key="admin_dekl"
        )

    hledany_text = st.sidebar.text_input(
        "Hledat výraz:",
        placeholder="Latina nebo čeština...",
        icon=":material/search:",
        key="admin_search"
    )

    df_filtered = aplikuj_filtry(df, vybrane_lekce, vybrane_druhy, vybrane_konjugace, vybrane_deklinace, hledany_text)
    st.sidebar.metric("Vyfiltrováno kartiček", len(df_filtered))

    if not st.session_state.admin_prihlasen:
        st.info("💡 Pro přidávání slovíček, import z CSV a úpravu či mazání kartiček se přihlaste v záložce **Přihlásit se**.", icon=":material/lock:")
        st.caption(f"Zobrazeno **{len(df_filtered)}** z celkem {len(df)} kartiček")
        if len(df_filtered) == 0:
            st.info("Zadaným filtrům neodpovídá žádné slovíčko.")
        else:
            st.dataframe(
                df_filtered[['Lekce', 'Latina', 'Čeština', 'Slovní druh', 'Konjugace', 'Deklinace']],
                hide_index=True,
                width="stretch",
                alt="Slovník latinských slovíček"
            )
    else:
        # Reset označených řádků při změně filtrů
        filtr_sig = (tuple(vybrane_lekce), tuple(vybrane_druhy), tuple(vybrane_konjugace), tuple(vybrane_deklinace), hledany_text)
        if st.session_state.get("admin_last_filtr") != filtr_sig:
            st.session_state.admin_last_filtr = filtr_sig
            st.session_state.admin_selected_rows = []
            st.session_state.admin_table_ver += 1

        # Zjištění aktuálně označených řádků
        table_key = f"admin_table_{st.session_state.admin_table_ver}"
        table_state = st.session_state.get(table_key)
        if table_state is not None and hasattr(table_state, "selection"):
            selected_rows = [r for r in table_state.selection.rows if r < len(df_filtered)]
        else:
            selected_rows = [r for r in st.session_state.admin_selected_rows if r < len(df_filtered)]
        pocet_oznacenych = len(selected_rows)

        # Horní panel nástrojů (Toolbar)
        with st.container(horizontal=True):
            if st.button("Přidat slovo", icon=":material/add:", type="primary"):
                st.session_state.pridat_slovo_aktivni = True
                st.rerun()

            if st.button("Importovat z CSV", icon=":material/upload_file:"):
                st.session_state.import_csv_aktivni = True
                st.rerun()

            if st.button("Označit všechny vyfiltrované", icon=":material/select_all:", disabled=(len(df_filtered) == 0)):
                st.session_state.admin_selected_rows = list(range(len(df_filtered)))
                st.session_state.admin_table_ver += 1
                st.rerun()

            if st.button("Zrušit označení", icon=":material/deselect:", disabled=(pocet_oznacenych == 0)):
                st.session_state.admin_selected_rows = []
                st.session_state.admin_table_ver += 1
                st.rerun()

            if st.button(
                f"Smazat označené ({pocet_oznacenych})" if pocet_oznacenych > 0 else "Smazat označené",
                type="secondary",
                icon=":material/delete_sweep:",
                disabled=(pocet_oznacenych == 0)
            ):
                st.session_state.bulk_delete_active = True
                st.rerun()

        st.caption(f"Zobrazeno **{len(df_filtered)}** z celkem {len(df)} kartiček | Označeno pro smazání: **{pocet_oznacenych}**")

        # Tabulka s přehledem kartiček
        if len(df_filtered) == 0:
            st.info("Zadaným filtrům neodpovídá žádná kartička.")
        else:
            df_zobrazit = df_filtered.copy()
            df_zobrazit["Upravit"] = ":material/edit:"
            df_zobrazit["Smazat"] = ":material/delete:"
            sloupce = ["Upravit", "Smazat", "Lekce", "Latina", "Čeština", "Slovní druh", "Konjugace", "Deklinace"]
            df_zobrazit = df_zobrazit[sloupce]

            st.dataframe(
                df_zobrazit,
                key=table_key,
                selection_default={"selection": {"rows": st.session_state.admin_selected_rows}},
                on_select="rerun",
                selection_mode="multi-row",
                column_config={
                    "Upravit": st.column_config.ButtonColumn(
                        "Upravit",
                        help="Upravit kartičku",
                        width="small",
                        pinned=True,
                        on_click=on_admin_edit_click,
                        key="btn_admin_edit"
                    ),
                    "Smazat": st.column_config.ButtonColumn(
                        "Smazat",
                        help="Smazat kartičku",
                        width="small",
                        pinned=True,
                        on_click=on_admin_delete_click,
                        key="btn_admin_delete"
                    ),
                    "Lekce": st.column_config.TextColumn("Lekce", width="small"),
                    "Latina": st.column_config.TextColumn("Latina", width="medium"),
                    "Čeština": st.column_config.TextColumn("Čeština", width="medium"),
                    "Slovní druh": st.column_config.TextColumn("Slovní druh", width="medium"),
                    "Konjugace": st.column_config.TextColumn("Konjugace", width="small"),
                    "Deklinace": st.column_config.TextColumn("Deklinace", width="small"),
                },
                hide_index=True,
                width="stretch",
                alt="Přehled a správa latinských kartiček"
            )

        # Otevření příslušného modálního dialogu
        if st.session_state.get("pridat_slovo_aktivni"):
            dialog_pridat_slovo(df)
        elif st.session_state.get("import_csv_aktivni"):
            dialog_import_csv(df)
        elif st.session_state.get("edit_row_idx") is not None:
            cr = st.session_state.edit_row_idx
            if 0 <= cr < len(df_filtered):
                dialog_upravit_slovo(df_filtered.index[cr], df)
            else:
                st.session_state.edit_row_idx = None
        elif st.session_state.get("delete_row_idx") is not None:
            cr = st.session_state.delete_row_idx
            if 0 <= cr < len(df_filtered):
                dialog_smazat_slovo(df_filtered.index[cr], df)
            else:
                st.session_state.delete_row_idx = None
        elif st.session_state.get("bulk_delete_active"):
            sel_indices = [df_filtered.index[r] for r in selected_rows if r < len(df_filtered)]
            if sel_indices:
                dialog_hromadne_smazani(sel_indices, df)
            else:
                st.session_state.bulk_delete_active = False

# ==============================================================================
# STRÁNKA 4: PŘIHLÁŠENÍ SPRÁVCE
# ==============================================================================
def stranka_prihlaseni():
    zobraz_sidebar_admin_status()
    st.sidebar.metric("Celkem kartiček v databázi", len(df))
    st.sidebar.metric("Počet aktivních lekcí", len(set(df['Lekce']) - {''}))

    admin_heslo = nacti_admin_heslo()

    if not st.session_state.admin_prihlasen:
        st.title("🔐 Přihlášení správce")
        st.write("Pro správu slovníku (přidávání, úpravy a mazání kartiček) a publikování balíčků do veřejné knihovny zadejte administrátorské heslo.")

        if not admin_heslo:
            st.warning("⚠️ Administrátorské heslo není nastaveno v proměnných prostředí ani v souboru `.env`.", icon=":material/warning:")
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
            Jako přihlášený správce máte k dispozici následující oprávnění:
            - **Generátor kartiček:** Možnost publikovat vytvořené balíčky do veřejné knihovny.
            - **Knihovna kartiček:** Možnost mazat uložené balíčky.
            - **Slovník:** Přidávání nových slovíček, úprava existujících kartiček a hromadné mazání.
            """
        )
        
        st.markdown("---")
        if st.button("Odhlásit se ze systému", icon=":material/logout:", type="secondary", key="btn_logout_page"):
            st.session_state.admin_prihlasen = False
            st.session_state.admin_selected_rows = []
            st.toast("Byl jste odhlášen.", icon="👋")
            st.rerun()

# ==============================================================================
# HORNÍ MENU BAR (NAVIGACE)
# ==============================================================================
stranky = [
    st.Page(
        stranka_generator,
        title="Generátor kartiček",
        icon=":material/style:",
        url_path="generator",
        default=True,
    ),
    st.Page(
        stranka_knihovna,
        title="Knihovna kartiček",
        icon=":material/local_library:",
        url_path="knihovna",
    ),
    st.Page(
        stranka_slovnik,
        title="Slovník",
        icon=":material/menu_book:",
        url_path="slovnik",
    ),
    st.Page(
        stranka_prihlaseni,
        title="Odhlásit se" if st.session_state.admin_prihlasen else "Přihlásit se",
        icon=":material/verified_user:" if st.session_state.admin_prihlasen else ":material/login:",
        url_path="prihlaseni",
    ),
]

pg = st.navigation(stranky, position="top")
pg.run()