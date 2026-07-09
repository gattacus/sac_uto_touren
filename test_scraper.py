import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, call, patch

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


def _tour_record(tour_id: int = 12345, title: str = "Fixture Tour", active: int = 1) -> dict:
    return {
        "id": tour_id,
        "active": active,
        "lastSeen": 123,
        "date_from": "2026-04-01",
        "date_to": "2026-04-02",
        "duration": "2 Tage",
        "status": "open",
        "type": "Wa",
        "level": "T2",
        "group": "Senior/innen",
        "title": title,
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


def _list_page(tour_ids: list[int]) -> str:
    rows = "\n".join(
        f"""
        <tr>
          <td class="status_1">So 7. Jun. 2026</td>
          <td>Wa</td>
          <td></td>
          <td>T2</td>
          <td>1 Tag</td>
          <td>Senior/innen</td>
          <td></td>
          <td>
            <a href="https://example.invalid/detail?page=detail&touren_nummer={tour_id}">
              Tour {tour_id}
            </a>
          </td>
        </tr>
        """
        for tour_id in tour_ids
    )
    return f"""
    <html>
      <body>
        <table class="table">
          {rows}
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
        tour = _tour_record()

        try:
            scraper.update_row(db, tour)
            row = db.execute(
                "SELECT duration FROM data WHERE id = ?",
                (tour["id"],),
            ).fetchone()
        finally:
            db.close()

        self.assertEqual(("2 Tage",), row)

    def test_update_row_replaces_existing_tour_by_id(self):
        db = scraper.init_database(":memory:")
        original = _tour_record(title="Original Title", active=0)
        updated = _tour_record(title="Updated Title", active=1)

        try:
            scraper.update_row(db, original)
            scraper.update_row(db, updated)
            row = db.execute(
                "SELECT COUNT(*), title, active FROM data WHERE id = ?",
                (updated["id"],),
            ).fetchone()
        finally:
            db.close()

        self.assertEqual((1, "Updated Title", 1), row)


class TestHistoricalScrapeHelpers(unittest.TestCase):

    def test_parse_args_accepts_historical_flag(self):
        args = scraper.parse_args(["--historical"])

        self.assertTrue(args.historical)
        self.assertFalse(args.refresh_existing)
        self.assertIsNone(args.tour_url)

    def test_parse_args_accepts_refresh_existing_flag(self):
        args = scraper.parse_args(["--historical", "--refresh-existing"])

        self.assertTrue(args.historical)
        self.assertTrue(args.refresh_existing)

    def test_build_list_url_supports_current_and_year_specific_lists(self):
        current_url = scraper.build_list_url(offset=50)
        year_url = scraper.build_list_url(year=2026, offset=100)

        self.assertEqual(
            "https://sac-uto.ch/de/aktivitaeten/touren-und-kurse/"
            "?page=touren&year=&typ=&gruppe=&anlasstyp=&suchstring=&offset=50",
            current_url,
        )
        self.assertEqual(
            "https://sac-uto.ch/de/aktivitaeten/touren-und-kurse/"
            "?page=touren&year=2026&typ=&gruppe=&anlasstyp=&suchstring=&offset=100",
            year_url,
        )

    def test_extract_years_from_html_preserves_dropdown_order_and_skips_noise(self):
        html = """
        <html>
          <body>
            <select name="year">
              <option value="">- Jahr -</option>
              <option value="2029">2029</option>
              <option value="2028">2028</option>
              <option value="archive">Archiv</option>
              <option value="2028">2028 duplicate</option>
              <option value="2002">2002</option>
            </select>
          </body>
        </html>
        """

        years = scraper.extract_years_from_html(html)

        self.assertEqual([2029, 2028, 2002], years)

    def test_run_historical_scrapes_years_inactive_then_current_active(self):
        db = object()

        with (
            patch("scraper.discover_years", return_value=[2029, 2028]),
            patch("scraper.run") as run,
        ):
            scraper.run_historical(db)

        self.assertEqual(
            [
                call(
                    db,
                    year=2029,
                    active=0,
                    allow_empty=True,
                    refresh_existing=False,
                ),
                call(
                    db,
                    year=2028,
                    active=0,
                    allow_empty=True,
                    refresh_existing=False,
                ),
                call(db, active=1),
            ],
            run.call_args_list,
        )

    def test_run_historical_can_refresh_existing_archive_rows(self):
        db = object()

        with (
            patch("scraper.discover_years", return_value=[2029]),
            patch("scraper.run") as run,
        ):
            scraper.run_historical(db, refresh_existing=True)

        self.assertEqual(
            [
                call(
                    db,
                    year=2029,
                    active=0,
                    allow_empty=True,
                    refresh_existing=True,
                ),
                call(db, active=1),
            ],
            run.call_args_list,
        )

    def test_run_can_skip_empty_historical_year(self):
        db = Mock()

        with (
            patch("scraper.fetch_page", return_value="<html><body>No rows</body></html>") as fetch_page,
            patch("scraper.time.sleep") as sleep,
        ):
            scraper.run(db, year=2029, active=0, allow_empty=True)

        fetch_page.assert_called_once()
        sleep.assert_not_called()
        db.commit.assert_not_called()

    def test_run_retries_empty_active_index_before_processing_rows(self):
        db = Mock()

        with (
            patch(
                "scraper.fetch_page",
                side_effect=[
                    "<html><body>No rows yet</body></html>",
                    "<html><body>No rows still</body></html>",
                    _list_page([12345]),
                ],
            ) as fetch_page,
            patch("scraper.time.sleep") as sleep,
            patch("scraper.update_detail", return_value=True) as update_detail,
            patch("scraper.EMPTY_INDEX_RETRIES", 3),
            patch("scraper.EMPTY_INDEX_RETRY_DELAY_SECONDS", 10),
        ):
            scraper.run(db)

        self.assertEqual(3, fetch_page.call_count)
        self.assertEqual([call(10), call(20)], sleep.call_args_list)
        update_detail.assert_called_once()
        db.commit.assert_called_once()

    def test_incremental_historical_run_skips_existing_tours_but_keeps_paging(self):
        db = scraper.init_database(":memory:")
        existing_ids = list(range(1, 51))

        try:
            for tour_id in existing_ids:
                scraper.update_row(db, _tour_record(tour_id=tour_id))

            with (
                patch("scraper.fetch_page", side_effect=[_list_page(existing_ids), _list_page([51])]),
                patch("scraper.update_detail", return_value=True) as update_detail,
            ):
                scraper.run(
                    db,
                    year=2026,
                    active=0,
                    allow_empty=True,
                    refresh_existing=False,
                )
        finally:
            db.close()

        update_detail.assert_called_once()
        self.assertEqual("51", update_detail.call_args.args[1]["id"])

    def test_refresh_existing_historical_run_fetches_existing_tours(self):
        db = scraper.init_database(":memory:")

        try:
            scraper.update_row(db, _tour_record(tour_id=12345))

            with (
                patch("scraper.fetch_page", return_value=_list_page([12345])),
                patch("scraper.update_detail", return_value=True) as update_detail,
            ):
                scraper.run(
                    db,
                    year=2026,
                    active=0,
                    allow_empty=True,
                    refresh_existing=True,
                )
        finally:
            db.close()

        update_detail.assert_called_once()
        self.assertEqual("12345", update_detail.call_args.args[1]["id"])


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
