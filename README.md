# Generátor latinských kartiček pro Anki

Streamlit aplikace pro filtrování a export latinsko-českých slovíček do balíčků `.apkg` pro Anki / AnkiDroid.

## Spuštění pomocí Dockeru

### 1. Spuštění přes Docker Compose (doporučeno)

Nejjednodušší způsob spuštění:

```bash
docker compose up -d --build
```

Aplikace bude dostupná v prohlížeči na adrese: [http://localhost:8501](http://localhost:8501)

Zastavení aplikace:
```bash
docker compose down
```

### 2. Spuštění přes samotný Docker CLI

**Sestavení obrazu:**
```bash
docker build -t slovnicek-parser .
```

**Spuštění kontejneru:**
```bash
docker run -d --name slovnicek-parser -p 8501:8501 slovnicek-parser
```

---

## Administrace a správa kartiček

Aplikace obsahuje administrační rozhraní pro přidávání a odebírání kartiček.

### Nastavení administrátorského hesla
Heslo se konfiguruje v souboru `.env` v kořenovém adresáři:

```env
ADMIN_PASSWORD=moje_tajne_heslo
```

Pro rychlý začátek můžete zkopírovat vzorový soubor `.env.example`:
```bash
cp .env.example .env
```
*(Výchozí heslo v `.env` je nastaveno na `admin123`)*

V levém bočním panelu aplikace stačí přepnout na režim **Administrace**, zadat heslo a můžete:
- **Přidávat nové kartičky** (včetně lekce, slovního druhu, konjugace a deklinace).
- **Vyhledávat a odebírat kartičky** s potvrzením smazání.
- **Prohlížet celou databázi** a stáhnout si její záložní kopii v CSV.

---

## Poznámka k oprávněním (Linux)

Pokud vám Docker hlásí `permission denied while trying to connect to the docker API`:
1. Buď příkazy spouštějte se `sudo` (např. `sudo docker compose up -d`).
2. Nebo přidejte svého uživatele do skupiny `docker`:
   ```bash
   sudo usermod -aG docker $USER
   ```
   *Poté se stačí odhlásit a znovu přihlásit (nebo spustit `newgrp docker`).*
