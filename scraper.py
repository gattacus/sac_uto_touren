"""
scraper.py – Python-Äquivalent von scraper.js
Scraper für SAC UTO Touren (https://sac-uto.ch)
Ursprünglich für morph.io entwickelt.

Abhängigkeiten:
    pip install requests beautifulsoup4
"""

import os
import sys
import sqlite3
import time
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

import sacdateparser

# ---------------------------------------------------------------------------
# Globale Zähler
# ---------------------------------------------------------------------------
num_tours_total = 0
num_tours_done = 0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/65.0.3325.183 Safari/537.36 Vivaldi/1.96.1147.36"
    )
}

DB_PATH = os.environ.get("SCRAPER_DB_PATH", "data.sqlite")

# ---------------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------------

def init_database(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Richtet die SQLite-Datenbank ein und gibt die Verbindung zurück."""
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    print(f"Nutze SQLite-Datei: {db_path}")
    db = sqlite3.connect(db_path, check_same_thread=False)
    cur = db.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS data (
            id                        INTEGER PRIMARY KEY,
            active                    INTEGER,
            lastSeen                  INTEGER,
            date_from                 TEXT,
            date_to                   TEXT,
            status                    TEXT,
            type                      TEXT,
            level                     TEXT,
            grp                       TEXT,
            title                     TEXT,
            leiter                    TEXT,
            url                       TEXT,
            altitude                  TEXT,
            mtype                     TEXT,
            type_ext                  TEXT,
            level2                    TEXT,
            arrival                   TEXT,
            text                      TEXT,
            equipment                 TEXT,
            subscription_period_start TEXT,
            subscription_period_end   TEXT,
            extra_info                TEXT
        )
    """)
    # Spalte extra_info nachträglich hinzufügen, falls sie fehlt (für bestehende DBs)
    try:
        cur.execute("ALTER TABLE data ADD COLUMN extra_info TEXT")
    except sqlite3.OperationalError:
        pass  # Spalte existiert bereits

    db.execute("UPDATE data SET active=0")
    db.commit()
    return db


def update_row(db: sqlite3.Connection | None, tour: dict) -> None:
    """Schreibt einen Tour-Datensatz in die Datenbank (oder gibt ihn auf der Konsole aus)."""
    if db is None:
        print("REC:", tour)
        return

    db.execute("""
        INSERT OR REPLACE INTO data (
            id, active, lastSeen,
            date_from, date_to,
            status, type, level, grp,
            title, leiter, url,
            altitude, mtype, type_ext, level2,
            arrival, text, extra_info,
            equipment,
            subscription_period_start,
            subscription_period_end
        ) VALUES (
            :id, :active, :lastSeen,
            :date_from, :date_to,
            :status, :type, :level, :group,
            :title, :leiter, :url,
            :altitude, :mtype, :type_ext, :level2,
            :arrival, :text, :extra_info,
            :equipment,
            :subscription_period_start,
            :subscription_period_end
        )
    """, tour)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch_page(url: str, retries: int = 3) -> str | None:
    """Lädt eine Seite und gibt den HTML-Body zurück."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            print(f"Fehler beim Laden von {url} (Versuch {attempt + 1}): {e}")
            time.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------------------
# Detailseite
# ---------------------------------------------------------------------------

def update_detail(db: sqlite3.Connection | None, tour: dict, retry: int = 1) -> bool:
    """
    Lädt die Detailseite einer Tour, extrahiert alle Felder
    und schreibt den Datensatz in die DB.
    Gibt True bei Erfolg zurück.
    """
    global num_tours_done

    body = fetch_page(tour["url"])
    if body is None:
        print(f"Konnte Seite nicht laden: {tour['url']}")
        if retry > 0:
            print("Wiederholung …")
            return update_detail(db, tour, retry - 1)
        print("Abbruch.")
        sys.exit(1)

    soup = BeautifulSoup(body, "html.parser")

    # Fehlerbehandlung
    title_tag = soup.find("title")
    page_title = title_tag.get_text().strip() if title_tag else ""
    load_error = False

    if page_title == "Oops, an error occurred!":
        callout = soup.select_one(".callout-body")
        msg = callout.get_text().strip() if callout else ""
        print(f"Fehler bei Tour {tour.get('id')}: <{page_title}> <{msg}>")
        load_error = True

    if page_title in ("500 Internal Server Error", "502 Bad Gateway", "504 Gateway Time-out"):
        body_text = soup.get_text().strip()[:200]
        print(f"Fehler bei Tour {tour.get('id')}: <{page_title}> <{body_text}>")
        load_error = True

    if load_error:
        if retry > 0:
            print("Wiederholung …")
            return update_detail(db, tour, retry - 1)
        print("Abbruch.")
        sys.exit(1)

    num_tours_done += 1
    print(
        f"Verarbeite Tour {tour.get('id')}, "
        f"{num_tours_done} von {num_tours_total}\t\t{tour['url']}"
    )

    # Titel und Leiter
    h2 = soup.find("h2")
    tour["title"] = h2.get_text().strip() if h2 else tour.get("title", "")
    leiter_el = soup.select_one(".droptours-address-name")
    tour["leiter"] = leiter_el.get_text().strip() if leiter_el else ""

    # Key-Value-Tabelle
    kv: dict[str, str] = {}
    for row in soup.select("table#droptours-detail tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        if cells[0].get("colspan"):
            continue
        key = cells[0].get_text().strip()
        value = cells[1].get_text().strip()
        kv[key] = value

    if "Datum" not in kv:
        print("Seiten-Dump vor Fehler:", body[:500])

    datum = kv.get("Datum", "")

    # Sonderfall: Server-Fehler mit Datum "Do 0."
    if datum.startswith("Do 0."):
        if retry > 0:
            print(f"Wiederholung wegen merkwürdigem Startdatum '{datum}'")
            return update_detail(db, tour, retry - 1)
        print(f"Tour mit merkwürdigem Startdatum übersprungen '{datum}': {tour['url']}")
        return False

    dd = sacdateparser.parse_date2(datum)
    tour["date_from"] = dd["from"]
    tour["date_to"] = dd["to"]

    tour["group"] = kv.get("Gruppe", tour.get("group", ""))
    tour["mtype"] = kv.get("Anlasstyp", "")
    tour["type_ext"] = kv.get("Typ/Zusatz:", "")
    tour["level2"] = kv.get("Anforderungen", "")
    tour["altitude"] = kv.get("Auf-, Abstieg/Marschzeit", "")
    tour["arrival"] = kv.get("Reiseroute", "")
    tour["text"] = kv.get("Route / Details", "")
    tour["extra_info"] = kv.get("Zusatzinfo", "")
    tour["equipment"] = kv.get("Ausrüstung", "")

    dd2 = sacdateparser.parse_date3(kv.get("Anmeldung", ""))
    tour["subscription_period_start"] = dd2["from"]
    tour["subscription_period_end"] = dd2["to"]

    update_row(db, tour)
    return True


# ---------------------------------------------------------------------------
# Hauptseite (Listenansicht) – paginiert
# ---------------------------------------------------------------------------

def run(db: sqlite3.Connection, offset: int = 0) -> None:
    """
    Verarbeitet die paginierte Tourenliste und ruft für jeden Eintrag
    die Detailseite ab.
    """
    global num_tours_total

    list_url = (
        "https://sac-uto.ch/de/aktivitaeten/touren-und-kurse/"
        f"?page=touren&year=&typ=&gruppe=&anlasstyp=&suchstring=&offset={offset}"
    )

    body = fetch_page(list_url)
    if body is None:
        print(f"Konnte Hauptseite nicht laden: {list_url}")
        sys.exit(1)

    print(f"Verarbeite Hauptliste {list_url}")
    soup = BeautifulSoup(body, "html.parser")

    rows = soup.select("table.table tr")

    if offset == 0 and not rows:
        print("Keine Daten auf der Indexseite gefunden.")
        sys.exit(1)

    detail_tours: list[dict] = []

    for row in rows:
        cells = row.find_all(True, recursive=False)
        if not cells:
            continue
        first = cells[0]

        # Überspringe Kopfzeilen (th) oder colspan-Zellen
        if first.name != "td":
            continue
        if first.get("colspan"):
            continue

        tour: dict = {}
        tour["active"] = 1
        tour["lastSeen"] = int(time.time() * 1000)

        # Spalte 0: Datum / Status
        tour["rawDate"] = first.get_text().strip()
        classes = first.get("class", [])
        if "status_3" in classes:
            tour["status"] = "full"
        elif "status_2" in classes:
            tour["status"] = "cancelled"
        elif "without_register" in classes:
            tour["status"] = "ok"
        elif "status_1" in classes or "status_0" in classes:
            tour["status"] = "open"
        else:
            tour["status"] = ""

        tds = row.find_all("td")
        if len(tds) < 8:
            continue

        tour["type"] = tds[1].get_text().strip()
        # tds[2] = Icon (übersprungen)
        tour["level"] = tds[3].get_text().strip()
        tour["rawDuration"] = tds[4].get_text().strip()
        tour["group"] = tds[5].get_text().strip()
        # tds[6] = ? (übersprungen)
        title_td = tds[7]
        tour["title"] = title_td.get_text().strip()

        link = title_td.find("a")
        if link:
            tour["url"] = link.get("href", "")
        else:
            tour["url"] = ""

        # Tour-ID aus Query-String
        parsed = urlparse(tour["url"])
        qs = parse_qs(parsed.query)
        tour["id"] = qs.get("touren_nummer", [None])[0]

        if len(tds) > 8:
            tour["leiter"] = tds[8].get_text().strip()
        else:
            tour["leiter"] = ""

        num_tours_total += 1
        detail_tours.append(tour)

    # Detailseiten parallel abrufen (max. 2 gleichzeitig, wie im Original)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(update_detail, db, t): t
            for t in detail_tours
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                t = futures[future]
                print(f"Fehler bei Tour {t.get('id')}: {exc}")

    # Commit nach jeder Seite
    db.commit()

    # Nächste Seite laden, wenn genug Ergebnisse
    if len(detail_tours) > 40:
        run(db, offset + 50)
    else:
        print("Commit und Datenbankverbindung schliessen.")
        db.close()


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]

    if args:
        # Einzelne Tour-URL direkt verarbeiten (wie im Original)
        update_detail(None, {"url": args[0]})
    else:
        database = init_database()
        run(database)
