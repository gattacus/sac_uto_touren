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


if __name__ == "__main__":
    unittest.main(verbosity=2)
