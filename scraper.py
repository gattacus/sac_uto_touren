from __future__ import annotations

"""
scraper.py – Python-Äquivalent von scraper.js
Scraper für SAC UTO Touren (https://sac-uto.ch)
Ursprünglich für morph.io entwickelt.

Abhängigkeiten:
    pip install requests beautifulsoup4
"""

import argparse
import logging
import os
import sys
import sqlite3
import time
import threading
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
DEFAULT_LOG_LEVEL = os.environ.get("SCRAPER_LOG_LEVEL", "INFO")
LOGGER = logging.getLogger("sac_uto_touren")
DB_WRITE_LOCK = threading.Lock()
LEITER_SEPARATOR = " | "
LIST_BASE_URL = "https://sac-uto.ch/de/aktivitaeten/touren-und-kurse/"


def configure_logging(level_name: str) -> str:
    """Konfiguriert das zentrale Logging und gibt das normalisierte Level zurück."""
    normalized_level = level_name.upper()
    numeric_level = getattr(logging, normalized_level, None)
    if not isinstance(numeric_level, int):
        raise ValueError(
            f"Ungültiges Log-Level '{level_name}'. Erlaubt sind: "
            "DEBUG, INFO, WARNING, ERROR, CRITICAL."
        )

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return normalized_level


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parst CLI-Argumente für normalen Lauf oder Single-Tour-Debugging."""
    parser = argparse.ArgumentParser(
        description="Scraped SAC UTO Touren und speichert sie in SQLite."
    )
    parser.add_argument(
        "tour_url",
        nargs="?",
        help="Optional: einzelne Tour-URL direkt verarbeiten.",
    )
    parser.add_argument(
        "--log-level",
        default=DEFAULT_LOG_LEVEL,
        metavar="LEVEL",
        help=(
            "Log-Level für stderr (DEBUG, INFO, WARNING, ERROR, CRITICAL). "
            f"Default: {DEFAULT_LOG_LEVEL}."
        ),
    )
    parser.add_argument(
        "--historical",
        action="store_true",
        help=(
            "Scrape all year-specific archive pages before scraping the "
            "current listing. Historical-only rows remain inactive."
        ),
    )
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help=(
            "With --historical, re-fetch and overwrite tours that already "
            "exist in SQLite. Without this flag, historical archive pages "
            "skip existing tour IDs."
        ),
    )
    args = parser.parse_args(argv)
    try:
        args.log_level = configure_logging(args.log_level)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def _join_leiter_names(names: list[str]) -> str:
    """Normalisiert und verknüpft mehrere Leiter-Namen deterministisch."""
    normalized: list[str] = []
    seen: set[str] = set()

    for name in names:
        clean = " ".join(name.split())
        if not clean or clean in seen:
            continue
        normalized.append(clean)
        seen.add(clean)

    return LEITER_SEPARATOR.join(normalized)


def _extract_leiter_from_detail(soup: BeautifulSoup, kv: dict[str, str]) -> str:
    """Liest alle verfügbaren Leiter-Namen aus der Detailseite."""
    names = [
        element.get_text(" ", strip=True)
        for element in soup.select(".droptours-address-name")
    ]
    if names:
        return _join_leiter_names(names)

    fallback_names = [
        value
        for key, value in kv.items()
        if key.startswith("Tourenleiter")
    ]
    return _join_leiter_names(fallback_names)


def _extract_leiter_from_list_row(row: BeautifulSoup) -> str:
    """Liest Leiter-Namen robust aus einer Listenzeile."""
    leader_cell = row.select_one("td.droptours-leitung")
    if leader_cell is None:
        tds = row.find_all("td", recursive=False)
        leader_cell = tds[-1] if tds else None
    if leader_cell is None:
        return ""

    anchor_names = [
        anchor.get_text(" ", strip=True)
        for anchor in leader_cell.find_all("a")
    ]
    if anchor_names:
        return _join_leiter_names(anchor_names)

    return _join_leiter_names([leader_cell.get_text(" ", strip=True)])

# ---------------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------------

def init_database(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Richtet die SQLite-Datenbank ein und gibt die Verbindung zurück."""
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    LOGGER.info("Nutze SQLite-Datei: %s", db_path)
    db = sqlite3.connect(db_path, check_same_thread=False)
    cur = db.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS data (
            id                        INTEGER PRIMARY KEY,
            active                    INTEGER,
            lastSeen                  INTEGER,
            date_from                 TEXT,
            date_to                   TEXT,
            duration                  TEXT,
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
    try:
        cur.execute("ALTER TABLE data ADD COLUMN duration TEXT")
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

    # A single SQLite connection is shared across worker threads, so writes
    # must be serialized to avoid concurrent API misuse.
    with DB_WRITE_LOCK:
        db.execute("""
            INSERT OR REPLACE INTO data (
                id, active, lastSeen,
                date_from, date_to, duration,
                status, type, level, grp,
                title, leiter, url,
                altitude, mtype, type_ext, level2,
                arrival, text, extra_info,
                equipment,
                subscription_period_start,
                subscription_period_end
            ) VALUES (
                :id, :active, :lastSeen,
                :date_from, :date_to, :duration,
                :status, :type, :level, :group,
                :title, :leiter, :url,
                :altitude, :mtype, :type_ext, :level2,
                :arrival, :text, :extra_info,
                :equipment,
                :subscription_period_start,
                :subscription_period_end
            )
        """, tour)


def tour_exists(db: sqlite3.Connection, tour_id: object) -> bool:
    """Prüft, ob eine Tour-ID bereits in SQLite vorhanden ist."""
    if tour_id is None:
        return False
    row = db.execute(
        "SELECT 1 FROM data WHERE id = ? LIMIT 1",
        (tour_id,),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch_page(url: str, retries: int = 3) -> str | None:
    """Lädt eine Seite und gibt den HTML-Body zurück."""
    for attempt in range(retries):
        try:
            LOGGER.debug("Lade URL: %s", url)
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            LOGGER.warning(
                "Fehler beim Laden von %s (Versuch %s/%s): %s",
                url,
                attempt + 1,
                retries,
                e,
            )
            time.sleep(2 ** attempt)
    LOGGER.error("Laden von %s nach %s Versuchen fehlgeschlagen.", url, retries)
    return None


def build_list_url(year: int | str | None = None, offset: int = 0) -> str:
    """Baut die URL für die paginierte Tourenliste."""
    year_value = "" if year is None else str(year)
    return (
        f"{LIST_BASE_URL}"
        f"?page=touren&year={year_value}&typ=&gruppe=&anlasstyp=&suchstring=&offset={offset}"
    )


def extract_years_from_html(body: str) -> list[int]:
    """Liest die angebotenen Jahresfilter aus der aktuellen Listenansicht."""
    soup = BeautifulSoup(body, "html.parser")
    year_select = soup.find("select", attrs={"name": "year"})
    if year_select is None:
        return []

    years: list[int] = []
    seen: set[int] = set()
    for option in year_select.find_all("option"):
        value = option.get("value", "").strip()
        if not value.isdigit():
            continue
        year = int(value)
        if year in seen:
            continue
        years.append(year)
        seen.add(year)
    return years


def discover_years() -> list[int]:
    """Lädt die aktuelle Liste und gibt die vom SAC angebotenen Jahre zurück."""
    list_url = build_list_url()
    body = fetch_page(list_url)
    if body is None:
        LOGGER.error("Konnte Hauptseite nicht laden: %s", list_url)
        sys.exit(1)

    years = extract_years_from_html(body)
    if not years:
        LOGGER.error("Keine Jahresauswahl auf der Hauptseite gefunden.")
        sys.exit(1)

    LOGGER.info("Gefundene Jahre für historischen Lauf: %s", years)
    return years


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
        LOGGER.error("Konnte Seite nicht laden: %s", tour["url"])
        if retry > 0:
            LOGGER.info("Wiederholung ...")
            return update_detail(db, tour, retry - 1)
        LOGGER.error("Tour wird übersprungen, Hauptlauf läuft weiter.")
        return False

    soup = BeautifulSoup(body, "html.parser")

    # Fehlerbehandlung
    title_tag = soup.find("title")
    page_title = title_tag.get_text().strip() if title_tag else ""
    load_error = False

    if page_title == "Oops, an error occurred!":
        callout = soup.select_one(".callout-body")
        msg = callout.get_text().strip() if callout else ""
        LOGGER.warning(
            "Fehler bei Tour %s: <%s> <%s>",
            tour.get("id"),
            page_title,
            msg,
        )
        load_error = True

    if page_title in ("500 Internal Server Error", "502 Bad Gateway", "504 Gateway Time-out"):
        body_text = soup.get_text().strip()[:200]
        LOGGER.warning(
            "Fehler bei Tour %s: <%s> <%s>",
            tour.get("id"),
            page_title,
            body_text,
        )
        load_error = True

    if load_error:
        if retry > 0:
            LOGGER.info("Wiederholung ...")
            return update_detail(db, tour, retry - 1)
        LOGGER.error("Tour wird übersprungen, Hauptlauf läuft weiter.")
        return False

    num_tours_done += 1
    LOGGER.debug(
        "Verarbeite Tour %s, %s von %s %s",
        tour.get("id"),
        num_tours_done,
        num_tours_total,
        tour["url"],
    )

    # Titel
    h2 = soup.find("h2")
    tour["title"] = h2.get_text().strip() if h2 else tour.get("title", "")

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
        LOGGER.debug("Seiten-Dump vor Fehler: %s", body[:500])

    datum = kv.get("Datum", "")

    # Sonderfall: Server-Fehler mit Datum "Do 0."
    if datum.startswith("Do 0."):
        if retry > 0:
            LOGGER.warning(
                "Wiederholung wegen merkwürdigem Startdatum '%s'",
                datum,
            )
            return update_detail(db, tour, retry - 1)
        LOGGER.error(
            "Tour mit merkwürdigem Startdatum übersprungen '%s': %s",
            datum,
            tour["url"],
        )
        return False

    dd = sacdateparser.parse_date2(datum)
    tour["date_from"] = dd["from"]
    tour["date_to"] = dd["to"]
    tour["leiter"] = _extract_leiter_from_detail(soup, kv) or tour.get("leiter", "")

    tour["group"] = kv.get("Gruppe", tour.get("group", ""))
    tour["mtype"] = kv.get("Anlasstyp", "")
    tour["type_ext"] = kv.get("Typ/Zusatz:", "")
    tour["level2"] = kv.get("Anforderungen", "")
    tour["altitude"] = kv.get("Auf-, Abstieg / Zeit", "")
    tour["arrival"] = kv.get("Reiseroute", "")
    tour["text"] = kv.get("Route / Details", "")
    tour["extra_info"] = kv.get("Zusatzinfo", "")
    tour["equipment"] = kv.get("Ausrüstung", "")

    dd2 = sacdateparser.parse_date3(kv.get("Anmeldung", ""))
    tour["subscription_period_start"] = dd2.get("from")
    tour["subscription_period_end"] = dd2.get("to")

    update_row(db, tour)
    return True


# ---------------------------------------------------------------------------
# Hauptseite (Listenansicht) – paginiert
# ---------------------------------------------------------------------------

def run(
    db: sqlite3.Connection,
    year: int | str | None = None,
    offset: int = 0,
    active: int = 1,
    allow_empty: bool = False,
    refresh_existing: bool = True,
) -> None:
    """
    Verarbeitet die paginierte Tourenliste und ruft für jeden Eintrag
    die Detailseite ab.
    """
    global num_tours_total

    current_offset = offset
    run_label = f"Jahr {year}" if year is not None else "aktive Liste"
    LOGGER.info("Starte Listenlauf für %s ab Offset %s.", run_label, offset)

    while True:
        list_url = build_list_url(year=year, offset=current_offset)

        LOGGER.info("Lade Hauptliste für %s, Offset %s.", run_label, current_offset)
        body = fetch_page(list_url)
        if body is None:
            LOGGER.error("Konnte Hauptseite nicht laden: %s", list_url)
            sys.exit(1)

        LOGGER.info("Verarbeite Hauptliste %s", list_url)
        soup = BeautifulSoup(body, "html.parser")

        rows = soup.select("table.table tr")

        if current_offset == 0 and not rows:
            if allow_empty:
                LOGGER.warning("Keine Daten auf der Indexseite gefunden, überspringe: %s", list_url)
                break
            LOGGER.error("Keine Daten auf der Indexseite gefunden.")
            sys.exit(1)

        detail_tours: list[dict] = []
        list_tours_found = 0
        skipped_existing = 0

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
            tour["active"] = active
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
            tour["duration"] = tds[4].get_text().strip()
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

            tour["leiter"] = _extract_leiter_from_list_row(row)
            list_tours_found += 1

            if not refresh_existing and tour_exists(db, tour["id"]):
                skipped_existing += 1
                continue

            num_tours_total += 1
            detail_tours.append(tour)

        LOGGER.info(
            "Hauptliste für %s, Offset %s: %s Touren gefunden, %s neu, %s übersprungen.",
            run_label,
            current_offset,
            list_tours_found,
            len(detail_tours),
            skipped_existing,
        )

        # Detailseiten parallel abrufen (max. 2 gleichzeitig, wie im Original)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(update_detail, db, t): t
                for t in detail_tours
            }
            page_tours_done = 0
            for future in as_completed(futures):
                page_tours_done += 1
                try:
                    success = future.result()
                    if not success:
                        t = futures[future]
                        LOGGER.warning("Tour %s wurde nicht gespeichert.", t.get("id"))
                except Exception:
                    t = futures[future]
                    LOGGER.exception("Fehler bei Tour %s", t.get("id"))
                finally:
                    if page_tours_done % 10 == 0 or page_tours_done == len(detail_tours):
                        LOGGER.info(
                            "Detailfortschritt für %s, Offset %s: %s/%s abgeschlossen.",
                            run_label,
                            current_offset,
                            page_tours_done,
                            len(detail_tours),
                        )

        # Commit nach jeder Seite
        db.commit()
        LOGGER.info(
            "Commit für %s, Offset %s abgeschlossen.",
            run_label,
            current_offset,
        )

        # Nächste Seite laden, wenn genug Ergebnisse
        if list_tours_found <= 40:
            LOGGER.info("Listenlauf für %s abgeschlossen.", run_label)
            break
        current_offset += 50


def run_historical(db: sqlite3.Connection, refresh_existing: bool = False) -> None:
    """Scraped alle angebotenen Jahresarchive und danach die aktuelle Liste."""
    years = discover_years()
    for index, year in enumerate(years, start=1):
        LOGGER.info(
            "Starte historischen Lauf %s/%s für Jahr %s.",
            index,
            len(years),
            year,
        )
        run(
            db,
            year=year,
            active=0,
            allow_empty=True,
            refresh_existing=refresh_existing,
        )
        LOGGER.info(
            "Historischer Lauf %s/%s für Jahr %s abgeschlossen.",
            index,
            len(years),
            year,
        )

    LOGGER.info("Historischer Lauf abgeschlossen, aktualisiere aktive Liste.")
    run(db, active=1)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    LOGGER.debug("Logging konfiguriert mit Level %s", args.log_level)
    start_time = time.monotonic()

    try:
        if args.tour_url:
            # Einzelne Tour-URL direkt verarbeiten (wie im Original)
            ok = update_detail(None, {"url": args.tour_url})
            return 0 if ok else 1

        database = init_database()
        try:
            if args.historical:
                run_historical(database, refresh_existing=args.refresh_existing)
            else:
                run(database)
        finally:
            database.close()
        return 0
    finally:
        LOGGER.debug("Gesamtlaufzeit: %.3f Sekunden", time.monotonic() - start_time)


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
