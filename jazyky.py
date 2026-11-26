import csv
import io
import os
import random
import unicodedata
import genanki
import pandas as pd
from druhy import analyzuj_slovicko

DATA_DIR = os.environ.get("DATA_DIR", "data")

JAZYKY = {
    "latina": {
        "kod": "latina",
        "nazev": "Latina",
        "sloupec": "Latina",
        "adresar": os.path.join(DATA_DIR, "slovniky", "latina"),
        "ma_gramatiku": True,
        "sloupce_csv": ["Lekce", "Latina", "Čeština", "Slovní druh", "Konjugace", "Deklinace"],
        "sloupce_tabulka": ["Lekce", "Latina", "Čeština", "Slovní druh", "konj./dekl."],
        "druhy_vychozi": ["Podstatné jméno", "Přídavné jméno", "Sloveso", "Zájmeno", "Ostatní"],
        "titulek_generator": "📚 Generátor latinských kartiček pro Anki",
        "titulek_slovnik": "📖 Slovník latinských slovíček",
        "vychozi_balicek": "Latina - Můj výběr",
        "placeholder_slovo": "např. victoria, ae, f",
        "help_slovo": "Latinské slovo nebo fráze včetně gramatických koncovek",
        "placeholder_hledani": "Latina nebo čeština...",
        "alt_generator": "Seznam filtrovaných latinských slovíček",
        "alt_slovnik": "Slovník latinských slovíček",
        "alt_admin_slovnik": "Přehled a správa latinských kartiček",
        "url_generator": "latina-generator",
        "url_slovnik": "latina-slovnik",
        "anki_model_id": 1607392319,
        "badge_color": "blue",
    },
    "rectina": {
        "kod": "rectina",
        "nazev": "Řečtina",
        "sloupec": "Řečtina",
        "adresar": os.path.join(DATA_DIR, "slovniky", "rectina"),
        "ma_gramatiku": False,
        "sloupce_csv": ["Lekce", "Řečtina", "Čeština", "Slovní druh", "Rod"],
        "sloupce_tabulka": ["Lekce", "Řečtina", "Čeština", "Slovní druh"],
        "druhy_vychozi": ["podst. jm.", "sloveso", "adj.", "zájmeno", "před.", "spoj.", "příslovce", "částice"],
        "titulek_generator": "🏺 Generátor řeckých kartiček pro Anki",
        "titulek_slovnik": "📖 Slovník řeckých slovíček",
        "vychozi_balicek": "Řečtina - Můj výběr",
        "placeholder_slovo": "např. ὁ λόγος, τοῦ λόγου",
        "help_slovo": "Řecké slovo ve slovníkovém tvaru",
        "placeholder_hledani": "Řečtina nebo čeština...",
        "alt_generator": "Seznam filtrovaných řeckých slovíček",
        "alt_slovnik": "Slovník řeckých slovíček",
        "alt_admin_slovnik": "Přehled a správa řeckých kartiček",
        "url_generator": "rectina-generator",
        "url_slovnik": "rectina-slovnik",
        "anki_model_id": 1607392320,
        "badge_color": "violet",
    },
}


def get_lang_dir(lang: str) -> str:
    """Vrátí datový adresář pro daný jazyk."""
    cfg = JAZYKY[lang]
    adresar = cfg["adresar"]
    if os.path.exists(adresar):
        return adresar
    fallback = os.path.join("slovniky", lang)
    if os.path.exists(fallback):
        return fallback
    return adresar


def get_csv_path(lang: str) -> str:
    """Najde hlavní CSV soubor slovníku v adresáři daného jazyka."""
    adresar = get_lang_dir(lang)
    for kandidat in ("slovicka_komplet.csv", "1-5lekce.csv"):
        cesta = os.path.join(adresar, kandidat)
        if os.path.exists(cesta):
            return cesta
    if os.path.isdir(adresar):
        csv_soubory = sorted(
            f
            for f in os.listdir(adresar)
            if f.lower().endswith(".csv") and os.path.isfile(os.path.join(adresar, f))
        )
        if csv_soubory:
            return os.path.join(adresar, csv_soubory[0])
    return os.path.join(adresar, "slovicka_komplet.csv")


def get_zalohy_dir(lang: str) -> str:
    return os.path.join(get_lang_dir(lang), "zalohy")


def get_importy_dir(lang: str) -> str:
    return os.path.join(get_lang_dir(lang), "importy")


def get_zadosti_path(lang: str) -> str:
    return os.path.join(get_lang_dir(lang), "zadosti_o_slova.json")


def klic_serazeni_lekci(hodnota: str):
    """Přirozené řazení lekcí (čísla číselně, ostatní abecedně)."""
    return (int(hodnota) if hodnota.isdigit() else 9999, hodnota)


def odstran_diakritiku(text: str) -> str:
    """Odstraní diakritiku včetně háčků, čárek, makrónů i řeckých akcentů a přídechů."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", str(text))
    filtered = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", filtered)


def aplikuj_filtry(
    data: pd.DataFrame,
    vybrane_lekce: list,
    vybrane_druhy: list,
    vybrane_konjugace: list | None = None,
    vybrane_deklinace: list | None = None,
    hledany_text: str = "",
    citlive_na_diakritiku: bool = False,
    lang: str = "latina",
) -> pd.DataFrame:
    """Vyfiltruje DataFrame slovíček podle lekcí, slovních druhů, mluvnických kategorií a hledaného textu."""
    cfg = JAZYKY[lang]
    col_cizi = cfg["sloupec"]
    ma_gramatiku = cfg["ma_gramatiku"]

    vsechny_lekce = set(l for l in data["Lekce"].unique() if l)
    if set(vybrane_lekce) >= vsechny_lekce:
        df_f = data[data["Lekce"].isin(vybrane_lekce) | (data["Lekce"] == "")].copy()
    else:
        df_f = data[data["Lekce"].isin(vybrane_lekce)].copy()

    if ma_gramatiku:
        vybrane_konjugace = vybrane_konjugace or []
        vybrane_deklinace = vybrane_deklinace or []
        vsechny_konj = set(k for k in data["Konjugace"].unique() if k)
        vsechny_dekl = set(d for d in data["Deklinace"].unique() if d)

        podminky = []
        for druh in vybrane_druhy:
            if druh == "Sloveso":
                mask_slovesa = df_f["Slovní druh"] == "Sloveso"
                if set(vybrane_konjugace) >= vsechny_konj:
                    podminky.append(
                        mask_slovesa
                        & (df_f["Konjugace"].isin(vybrane_konjugace) | (df_f["Konjugace"] == ""))
                    )
                else:
                    podminky.append(mask_slovesa & df_f["Konjugace"].isin(vybrane_konjugace))
            elif druh in ["Podstatné jméno", "Přídavné jméno"]:
                mask_dekl = df_f["Slovní druh"] == druh
                if set(vybrane_deklinace) >= vsechny_dekl:
                    podminky.append(
                        mask_dekl
                        & (df_f["Deklinace"].isin(vybrane_deklinace) | (df_f["Deklinace"] == ""))
                    )
                else:
                    podminky.append(mask_dekl & df_f["Deklinace"].isin(vybrane_deklinace))
            else:
                podminky.append(df_f["Slovní druh"] == druh)

        if podminky:
            finalni_maska = podminky[0]
            for m in podminky[1:]:
                finalni_maska = finalni_maska | m
            df_f = df_f[finalni_maska]
        else:
            df_f = pd.DataFrame(columns=data.columns)
    else:
        vsechny_druhy = set(d for d in data["Slovní druh"].unique() if d)
        if not vybrane_druhy and vsechny_druhy:
            df_f = pd.DataFrame(columns=data.columns)
        elif set(vybrane_druhy) >= vsechny_druhy:
            df_f = df_f[df_f["Slovní druh"].isin(vybrane_druhy) | (df_f["Slovní druh"] == "")]
        else:
            df_f = df_f[df_f["Slovní druh"].isin(vybrane_druhy)]

    if hledany_text and hledany_text.strip():
        q_raw = hledany_text.strip()
        if citlive_na_diakritiku:
            q = q_raw.lower()
            df_f = df_f[
                df_f[col_cizi].astype(str).str.lower().str.contains(q, na=False, regex=False)
                | df_f["Čeština"].astype(str).str.lower().str.contains(q, na=False, regex=False)
            ]
        else:
            q_clean = odstran_diakritiku(q_raw.lower())
            if "_Cizi_norm" in df_f.columns and "_Cestina_norm" in df_f.columns:
                df_f = df_f[
                    df_f["_Cizi_norm"].str.contains(q_clean, na=False, regex=False)
                    | df_f["_Cestina_norm"].str.contains(q_clean, na=False, regex=False)
                ]
            else:
                cizi_norm = df_f[col_cizi].fillna("").apply(lambda s: odstran_diakritiku(str(s).lower()))
                ces_norm = df_f["Čeština"].fillna("").apply(lambda s: odstran_diakritiku(str(s).lower()))
                df_f = df_f[
                    cizi_norm.str.contains(q_clean, na=False, regex=False)
                    | ces_norm.str.contains(q_clean, na=False, regex=False)
                ]

    return df_f


def parsuj_import_csv(
    file_bytes: bytes, vychozi_lekce: str = "1", lang: str = "latina"
) -> pd.DataFrame:
    """
    Načte a naparsuje CSV soubor se slovíčky pro zvolený jazyk.
    Podporuje oddělovače (; nebo , nebo tab), volitelné hlavičky i různé uspořádání sloupců.
    Pro latinu automaticky doplní mluvnické kategorie pomocí druhy.py; pro řečtinu analýzu přeskakuje.
    """
    cfg = JAZYKY[lang]
    col_cizi = cfg["sloupec"]
    ma_gramatiku = cfg["ma_gramatiku"]
    prazdne_sloupce = cfg["sloupce_csv"]

    text = file_bytes.decode("utf-8", errors="replace").strip()
    if not text:
        return pd.DataFrame(columns=prazdne_sloupce)

    first_line = text.splitlines()[0]
    sep = ";" if ";" in first_line else ("," if "," in first_line else "\t")

    reader = csv.reader(io.StringIO(text), delimiter=sep)
    rows = [r for r in reader if r and any(cell.strip() for cell in r)]
    if not rows:
        return pd.DataFrame(columns=prazdne_sloupce)

    def norm(s):
        s_nfd = unicodedata.normalize("NFD", str(s).strip().lower())
        return "".join(c for c in s_nfd if unicodedata.category(c) != "Mn")

    def matches_any(s, substrings):
        s_norm = norm(s)
        return any(sub in s_norm for sub in substrings)

    is_cizi = lambda s: matches_any(s, ["lat", "rectin", "reck", "greek", "tvar", "slovo", "word"])
    is_ces = lambda s: matches_any(s, ["ces", "preklad", "vyznam", "czech"])
    is_lek = lambda s: matches_any(s, ["lekc", "less", "cislo"])
    is_rod = lambda s: norm(s) == "rod" or "gender" in norm(s)

    first_row = [str(c).strip() for c in rows[0]]
    ma_hlavicku = any(is_cizi(c) or is_ces(c) or is_lek(c) for c in first_row)

    data_rows = rows[1:] if ma_hlavicku else rows
    vysledek = []

    for r in data_rows:
        cizi, ces, lek, druh, konj, dekl, rod = "", "", "", "", "", "", ""
        if ma_hlavicku:
            col_dict = {first_row[i]: r[i].strip() for i in range(min(len(first_row), len(r)))}
            for col_name, val in col_dict.items():
                col_norm = norm(col_name)
                if is_cizi(col_name) and not cizi:
                    cizi = val
                elif is_ces(col_name) and not ces:
                    ces = val
                elif is_lek(col_name) and not lek:
                    lek = val
                elif "druh" in col_norm or "pos" in col_norm:
                    druh = val
                elif "konj" in col_norm:
                    konj = val
                elif "dekl" in col_norm:
                    dekl = val
                elif is_rod(col_name):
                    rod = val
        else:
            vals = [str(c).strip() for c in r]
            if len(vals) == 1:
                if "||" in vals[0]:
                    parts = vals[0].split("||", 1)
                    cizi, ces = parts[0].strip(), parts[1].strip()
                else:
                    continue
            elif len(vals) == 2:
                if "||" in vals[0]:
                    parts = vals[0].split("||", 1)
                    cizi, ces = parts[0].strip(), parts[1].strip()
                    lek = vals[1]
                elif vals[0].isdigit() and not vals[1].isdigit():
                    lek, cizi = vals[0], vals[1]
                else:
                    cizi, ces = vals[0], vals[1]
            elif len(vals) >= 3:
                if (vals[0].isdigit() or vals[0].lower().startswith("lekce")) and not vals[2].isdigit():
                    lek, cizi, ces = vals[0], vals[1], vals[2]
                else:
                    cizi, ces, lek = vals[0], vals[1], vals[2]
                if len(vals) >= 4:
                    druh = vals[3]
                if ma_gramatiku:
                    if len(vals) >= 5:
                        konj = vals[4]
                    if len(vals) >= 6:
                        dekl = vals[5]
                else:
                    if len(vals) >= 5:
                        rod = vals[4]

        cizi = str(cizi).strip()
        ces = str(ces).strip()
        lek = str(lek).strip() or str(vychozi_lekce).strip()

        if not cizi or not ces:
            continue

        if ma_gramatiku:
            if not druh:
                a_druh, a_konj, a_dekl = analyzuj_slovicko(cizi)
                druh = a_druh
                if not konj:
                    konj = a_konj
                if not dekl:
                    dekl = a_dekl

            vysledek.append({
                "Lekce": lek,
                col_cizi: cizi,
                "Čeština": ces,
                "Slovní druh": druh,
                "Konjugace": konj,
                "Deklinace": dekl,
                "konj./dekl.": konj if konj else dekl,
            })
        else:
            vysledek.append({
                "Lekce": lek,
                col_cizi: cizi,
                "Čeština": ces,
                "Slovní druh": druh,
                "Rod": rod,
            })

    return pd.DataFrame(vysledek) if vysledek else pd.DataFrame(columns=prazdne_sloupce)


def vytvor_anki_balicek(
    data: pd.DataFrame,
    nazev_balicku: str,
    smer_cizi_ces: bool,
    smer_ces_cizi: bool,
    lang: str = "latina",
) -> io.BytesIO:
    """Vygeneruje Anki .apkg balíček pro zadaný jazyk."""
    cfg = JAZYKY[lang]
    nazev_jazyka = cfg["nazev"]
    col_cizi = cfg["sloupec"]
    model_id = cfg["anki_model_id"]

    sablony = []
    if smer_cizi_ces:
        sablony.append({
            "name": f"{nazev_jazyka} ➔ Čeština",
            "qfmt": (
                '<div style="font-family: Arial; font-size: 26px; text-align: center; margin-top: 50px;">'
                f"{{{{{col_cizi}}}}}</div>"
            ),
            "afmt": (
                '{{FrontSide}}<hr id="answer">'
                '<div style="font-family: Arial; font-size: 22px; text-align: center;">{{Čeština}}</div>'
            ),
        })
    if smer_ces_cizi:
        sablony.append({
            "name": f"Čeština ➔ {nazev_jazyka}",
            "qfmt": '<div style="font-family: Arial; font-size: 26px; text-align: center; margin-top: 50px;">{{Čeština}}</div>',
            "afmt": (
                '{{FrontSide}}<hr id="answer">'
                '<div style="font-family: Arial; font-size: 22px; text-align: center;">'
                f"{{{{{col_cizi}}}}}</div>"
            ),
        })

    model = genanki.Model(
        model_id,
        f"Základní {nazev_jazyka} (Dvousměrná)",
        fields=[
            {"name": col_cizi},
            {"name": "Čeština"},
        ],
        templates=sablony,
    )

    deck_id = random.randrange(1 << 30, 1 << 31)
    deck = genanki.Deck(deck_id, nazev_balicku)

    for _, row in data.iterrows():
        cizi_val = str(row[col_cizi]).strip()
        cestina = str(row["Čeština"]).strip()

        if not cizi_val or not cestina:
            continue

        note = genanki.Note(
            model=model,
            fields=[cizi_val, cestina],
        )
        deck.add_note(note)

    package = genanki.Package(deck)
    buffer = io.BytesIO()
    package.write_to_file(buffer)
    buffer.seek(0)
    return buffer

