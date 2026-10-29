import csv
import os
import re

def urci_deklinaci(parts):
    if len(parts) < 2:
        return ""
    
    lemma = parts[0].strip()
    gen = parts[1].strip()
    
    if "neskl" in gen: return "nesklonná"
    if gen in ["ae", "arum"] or gen.endswith("ae"): return "1"
    if gen in ["us", "u"]: return "4"
    if gen in ["ei", "rei", "spei", "fidei"]: return "5"
    
    # 2. deklinace (např. i, orum, ale i stažené jako bri, stri, agri...)
    if gen in ["i", "ii", "orum"] or (gen.endswith("i") and not gen in ["ei", "ui"]): 
        return "2"
        
    # 3. deklinace (např. is, um, ium, nebo slova jako pater, mater)
    if lemma in ["pater", "mater", "frater", "venter", "october"]: return "3"
    if gen.endswith("is") or gen in ["um", "ium"] or "ium" in gen: return "3"
    
    # Pokud se tvar vymyká, naprostá většina anomálií spadá do 3. deklinace (např. vis)
    return "3"

def urci_konjugaci(parts):
    if len(parts) < 2:
        return ""
        
    t1 = parts[0].strip()
    t2 = parts[1].strip()
    
    # Nepravidelná slovesa
    neprav = ["sum", "possum", "fio", "fero", "volo", "nolo", "malo", "eo"]
    if t1 in neprav or t2 in ["esse", "fieri", "ferre"]:
        return "nepravidelná"
        
    if t2 in ["are", "ari", "dare"]: return "1"
    if t2 in ["ire", "iri"]: return "4"
    if t2 == "eri": return "2"
    
    if t2 == "ere":
        # Pokud 1. os. končí na -eo (debeo, video), je to 2. konj. 
        # Jinak (lego, accipio, facio) je to 3. konj.
        if t1.endswith("eo"):
            return "2"
        else:
            return "3"
            
    # Deponentní slovesa 3. konjugace končící na "i" (sequi, loqui, mori...)
    if t2 in ["i", "sequi", "loqui", "mori", "nasci", "pati"]:
        return "3"
        
    return ""

def analyzuj_slovicko(latina):
    # Očištění od závorek a poznámek
    latina = str(latina).strip()
    clean_lat = re.sub(r'\(.*?\)|\[.*?\]', '', latina).strip()
    clean_lat = re.sub(r'\balicuius\b|\baliquid\b', '', clean_lat).strip()
    
    druh = 'Ostatní'
    konjugace = ''
    deklinace = ''
    
    if not clean_lat:
        return druh, konjugace, deklinace
    
    # Rozdělení podle čárek
    parts = [p.strip().lower() for p in clean_lat.split(',')]
    comma_count = len(parts) - 1
    last_part = parts[-1]
    first_part = parts[0]
    
    # 1. Podstatná jména
    if last_part in ['m', 'f', 'n', 'm/f', 'f/m']:
        druh = 'Podstatné jméno'
        deklinace = urci_deklinaci(parts)
        
    # 2. Zájmena (kontrola před slovesy, aby 'ipsum' nebylo chápáno jako 'sum')
    elif first_part in ['qui', 'ille', 'is', 'quis', 'quisque', 'ipse', 'ego', 'tu', 'nos', 'vos', 'se']:
        druh = 'Zájmeno'

    # 3. Slovesa
    elif comma_count >= 3 or last_part.endswith(' sum') or last_part == 'sum':
        druh = 'Sloveso'
        konjugace = urci_konjugaci(parts)
        
    # 4. Přídavná jména
    elif last_part == 'um' or last_part.endswith(' um') or last_part == 'e':
        druh = 'Přídavné jméno'
    elif comma_count == 2 and last_part.endswith('e'):
        druh = 'Přídavné jméno'
    elif comma_count == 1 and (first_part.endswith('ns') or first_part.endswith('ax') or first_part.endswith('er')):
        druh = 'Přídavné jméno'
    elif 'vetus' in first_part:
        druh = 'Přídavné jméno'
        
    return druh, konjugace, deklinace

def main():
    vstupni_soubor = os.path.join('data', 'filtered.csv') if os.path.exists(os.path.join('data', 'filtered.csv')) else 'filtered.csv'
    vystupni_soubor = os.path.join('data', 'slovicka_komplet.csv') if os.path.exists('data') else 'slovicka_komplet.csv'
    
    vysledna_data = []
    
    print("Načítám a analyzuji data...")
    
    with open(vstupni_soubor, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter=';')
        
        for row in reader:
            if not row or len(row) < 4:
                continue
                
            lekce = row[1].strip()
            latina = row[2].strip()
            cestina = row[3].strip()
            
            # Získáme druh, konjugaci i deklinaci z jedné funkce
            druh, konj, dekl = analyzuj_slovicko(latina)
            
            # Přidáme 2 nové sloupce na konec
            vysledna_data.append([lekce, latina, cestina, druh, konj, dekl])

    # Zápis do nového CSV
    with open(vystupni_soubor, mode='w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(['Lekce', 'Latina', 'Čeština', 'Slovní druh', 'Konjugace', 'Deklinace'])
        writer.writerows(vysledna_data)
        
    print(f"\nHotovo! Zpracováno {len(vysledna_data)} slovíček.")
    print(f"Výsledek byl uložen do souboru '{vystupni_soubor}'.")

if __name__ == "__main__":
    main()
