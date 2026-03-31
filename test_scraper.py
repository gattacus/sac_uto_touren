import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import scraper


def _detail_page(rows: list[tuple[str, str]]) -> str:
    cells = "\n".join(
        f"<tr><td>{key}</td><td>{value}</td></tr>"
        for key, value in rows
    )
    return f"""
    <html>
      <head><title>Tour Detail</title></head>
      <body>
        <h2>Fixture Tour</h2>
        <table id="droptours-detail">
          {cells}
        </table>
      </body>
    </html>
    """


def _detail_page_with_leaders(rows: list[tuple[str, str]], leader_names: list[str]) -> str:
    leaders_html = "".join(
        (
            '<div class="droptours-address-name">'
            f'<a href="https://example.invalid/person/{index}">{name}</a>'
            "</div>"
        )
        for index, name in enumerate(leader_names, start=1)
    )
    cells = "\n".join(
        f"<tr><td>{key}</td><td>{value}</td></tr>"
        for key, value in rows
    )
    return f"""
    <html>
      <head><title>Tour Detail</title></head>
      <body>
        <h2>Fixture Tour</h2>
        {leaders_html}
        <table id="droptours-detail">
          {cells}
        </table>
      </body>
    </html>
    """


class TestDatabaseDurationMigration(unittest.TestCase):

    def test_init_database_adds_duration_column_for_existing_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "existing.sqlite")
            db = sqlite3.connect(db_path)
            db.execute("""
                CREATE TABLE data (
                    id INTEGER PRIMARY KEY,
                    active INTEGER,
                    lastSeen INTEGER,
                    date_from TEXT,
                    date_to TEXT,
                    status TEXT,
                    type TEXT,
                    level TEXT,
                    grp TEXT,
                    title TEXT,
                    leiter TEXT,
                    url TEXT,
                    altitude TEXT,
                    mtype TEXT,
                    type_ext TEXT,
                    level2 TEXT,
                    arrival TEXT,
                    text TEXT,
                    equipment TEXT,
                    subscription_period_start TEXT,
                    subscription_period_end TEXT
                )
            """)
            db.commit()
            db.close()

            migrated = scraper.init_database(db_path)
            try:
                columns = [
                    row[1]
                    for row in migrated.execute("PRAGMA table_info(data)")
                ]
            finally:
                migrated.close()

        self.assertIn("duration", columns)

    def test_update_row_persists_duration_into_duration_column(self):
        db = scraper.init_database(":memory:")
        tour = {
            "id": 12345,
            "active": 1,
            "lastSeen": 123,
            "date_from": "2026-04-01",
            "date_to": "2026-04-02",
            "duration": "2 Tage",
            "status": "open",
            "type": "Wa",
            "level": "T2",
            "group": "Senior/innen",
            "title": "Fixture Tour",
            "leiter": "Max Muster",
            "url": "https://example.invalid/detail",
            "altitude": "",
            "mtype": "Tour",
            "type_ext": "",
            "level2": "",
            "arrival": "",
            "text": "",
            "extra_info": "",
            "equipment": "",
            "subscription_period_start": None,
            "subscription_period_end": None,
        }

        try:
            scraper.update_row(db, tour)
            row = db.execute(
                "SELECT duration FROM data WHERE id = ?",
                (tour["id"],),
            ).fetchone()
        finally:
            db.close()

        self.assertEqual(("2 Tage",), row)


class TestUpdateDetailSubscriptionPeriod(unittest.TestCase):

    def setUp(self):
        self.base_tour = {
            "id": "fixture",
            "url": "https://example.invalid/detail",
            "active": 1,
            "lastSeen": 0,
            "status": "open",
            "type": "Wa",
            "level": "T2",
            "group": "Senior/innen",
        }

    def test_update_detail_handles_missing_anmeldung(self):
        body = _detail_page([
            ("Datum", "So 7. Jun. 2026 1 Tag"),
            ("Gruppe", "Alpinist/innen"),
            ("Anlasstyp", "Tour"),
            ("Typ/Zusatz:", "Tr (Trailrun)"),
        ])
        tour = dict(self.base_tour)

        with patch("scraper.fetch_page", return_value=body), patch("scraper.update_row") as update_row:
            result = scraper.update_detail(None, tour)

        self.assertTrue(result)
        self.assertIsNone(tour["subscription_period_start"])
        self.assertIsNone(tour["subscription_period_end"])
        update_row.assert_called_once_with(None, tour)

    def test_update_detail_handles_von_only_anmeldung(self):
        body = _detail_page([
            ("Datum", "Mi 1. Apr. 2026 1 Tag"),
            ("Gruppe", "Senior/innen"),
            ("Anlasstyp", "Tour"),
            ("Typ/Zusatz:", "Sk (Skitour)"),
            ("Anmeldung", "Online von Mo 15. Dez. 2025, Max. TN 7"),
        ])
        tour = dict(self.base_tour)

        with patch("scraper.fetch_page", return_value=body), patch("scraper.update_row") as update_row:
            result = scraper.update_detail(None, tour)

        self.assertTrue(result)
        self.assertEqual("2025-12-15", tour["subscription_period_start"])
        self.assertIsNone(tour["subscription_period_end"])
        update_row.assert_called_once_with(None, tour)

    def test_update_detail_reads_current_altitude_label(self):
        body = _detail_page([
            ("Datum", "Mi 1. Apr. 2026 1 Tag"),
            ("Gruppe", "Senior/innen"),
            ("Anlasstyp", "Tour"),
            ("Typ/Zusatz:", "Wa (Wandern)"),
            ("Auf-, Abstieg / Zeit", "+1300m, -800m / 5.5h"),
        ])
        tour = dict(self.base_tour)

        with patch("scraper.fetch_page", return_value=body), patch("scraper.update_row") as update_row:
            result = scraper.update_detail(None, tour)

        self.assertTrue(result)
        self.assertEqual("+1300m, -800m / 5.5h", tour["altitude"])
        update_row.assert_called_once_with(None, tour)

    def test_update_detail_fetch_failure_is_non_fatal(self):
        tour = dict(self.base_tour)

        with patch("scraper.fetch_page", return_value=None), patch("scraper.update_row") as update_row:
            result = scraper.update_detail(None, tour, retry=0)

        self.assertFalse(result)
        update_row.assert_not_called()

    def test_update_detail_concatenates_multiple_leiter_names(self):
        body = _detail_page_with_leaders(
            [
                ("Datum", "Mi 1. Apr. 2026 1 Tag"),
                ("Gruppe", "Senior/innen"),
                ("Anlasstyp", "Tour"),
                ("Typ/Zusatz:", "Wa (Wandern)"),
            ],
            ["Brigitta Schweri", "Sekretariat SAC-Uto"],
        )
        tour = dict(self.base_tour)

        with patch("scraper.fetch_page", return_value=body), patch("scraper.update_row") as update_row:
            result = scraper.update_detail(None, tour)

        self.assertTrue(result)
        self.assertEqual("Brigitta Schweri | Sekretariat SAC-Uto", tour["leiter"])
        update_row.assert_called_once_with(None, tour)


class TestLeaderExtraction(unittest.TestCase):

    def test_extract_leiter_from_list_row_joins_multiple_anchor_names(self):
        row_html = """
        <tr>
          <td class="status_0">Mi 1. Apr.</td>
          <td class="status_0 droptours-typ">Wa</td>
          <td class="status_0 droptours-typ-icon"></td>
          <td class="status_0 droptours-anforderungen">T2</td>
          <td class="status_0 droptours-dauer">1 Tag</td>
          <td class="status_0 droptours-gruppe">Senior/innen</td>
          <td class="status_0 droptours-anmeldungen"></td>
          <td class="status_0 dropapp-tours-title"><a href="https://example.invalid/detail">Fixture Tour</a></td>
          <td class="status_0 droptours-bericht"></td>
          <td class="status_0 droptours-chat"></td>
          <td class="status_0 droptours-leitung">
            <a href="https://example.invalid/p1">Brigitta Schweri</a>,
            <a href="https://example.invalid/p2">Sekretariat SAC-Uto</a>
          </td>
        </tr>
        """
        row = scraper.BeautifulSoup(row_html, "html.parser").find("tr")

        leiter = scraper._extract_leiter_from_list_row(row)

        self.assertEqual("Brigitta Schweri | Sekretariat SAC-Uto", leiter)

    def test_join_leiter_names_deduplicates_and_normalizes(self):
        joined = scraper._join_leiter_names([
            "  Brigitta   Schweri  ",
            "Sekretariat SAC-Uto",
            "Brigitta Schweri",
            "",
        ])

        self.assertEqual("Brigitta Schweri | Sekretariat SAC-Uto", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
