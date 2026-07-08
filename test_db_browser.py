import sqlite3
import tempfile
import unittest
from pathlib import Path

import db_browser


def _create_db(path: Path) -> None:
    db = sqlite3.connect(path)
    try:
        db.execute("""
            CREATE TABLE data (
                id INTEGER PRIMARY KEY,
                active INTEGER,
                lastSeen INTEGER,
                date_from TEXT,
                date_to TEXT,
                duration TEXT,
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
                subscription_period_end TEXT,
                extra_info TEXT
            )
        """)
        db.executemany(
            """
            INSERT INTO data (
                id, active, lastSeen, date_from, date_to, duration, status,
                type, level, grp, title, leiter, url, altitude, mtype,
                type_ext, level2, arrival, text, equipment,
                subscription_period_start, subscription_period_end, extra_info
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    10,
                    1,
                    1000,
                    "2026-05-01",
                    "2026-05-01",
                    "1 Tag",
                    "open",
                    "Wa",
                    "T2",
                    "Senior/innen",
                    "Alpha Tour",
                    "Ada",
                    "https://example.invalid/10",
                    "",
                    "Tour",
                    "",
                    "",
                    "Zürich",
                    "Forest walk",
                    "Boots",
                    None,
                    None,
                    "",
                ),
                (
                    2,
                    0,
                    2000,
                    "2025-01-01",
                    "2025-01-01",
                    "1 Tag",
                    "full",
                    "S",
                    "WT2",
                    "Alle",
                    "Beta Snow",
                    "Bert",
                    "https://example.invalid/2",
                    "",
                    "Tour",
                    "",
                    "",
                    "Bern",
                    "Snow route",
                    "Skis",
                    None,
                    None,
                    "",
                ),
            ],
        )
        db.commit()
    finally:
        db.close()


class TestDbBrowser(unittest.TestCase):

    def test_table_columns_uses_known_display_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data.sqlite"
            _create_db(db_path)

            columns = db_browser.table_columns(db_path)

        self.assertEqual("id", columns[0])
        self.assertIn("title", columns)
        self.assertIn("extra_info", columns)

    def test_query_tours_filters_against_database_directly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data.sqlite"
            _create_db(db_path)
            columns = db_browser.table_columns(db_path)

            total, filtered, offset, rows = db_browser.query_tours(
                db_path,
                columns,
                "snow",
                "date_from",
                "asc",
            )

        self.assertEqual(2, total)
        self.assertEqual(1, filtered)
        self.assertEqual(0, offset)
        self.assertEqual([2], [row["id"] for row in rows])

    def test_query_tours_sorts_numeric_columns_numerically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data.sqlite"
            _create_db(db_path)
            columns = db_browser.table_columns(db_path)

            _, _, _, rows = db_browser.query_tours(
                db_path,
                columns,
                "",
                "id",
                "asc",
            )

        self.assertEqual([2, 10], [row["id"] for row in rows])

    def test_query_tours_limits_rendered_rows_but_reports_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data.sqlite"
            _create_db(db_path)
            columns = db_browser.table_columns(db_path)

            total, filtered, offset, rows = db_browser.query_tours(
                db_path,
                columns,
                "",
                "id",
                "asc",
                limit=1,
            )

        self.assertEqual(2, total)
        self.assertEqual(2, filtered)
        self.assertEqual(0, offset)
        self.assertEqual([2], [row["id"] for row in rows])

    def test_query_tours_offsets_rows_and_clamps_to_last_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data.sqlite"
            _create_db(db_path)
            columns = db_browser.table_columns(db_path)

            _, _, offset, rows = db_browser.query_tours(
                db_path,
                columns,
                "",
                "id",
                "asc",
                limit=1,
                offset=1,
            )
            _, _, clamped_offset, clamped_rows = db_browser.query_tours(
                db_path,
                columns,
                "",
                "id",
                "asc",
                limit=1,
                offset=99,
            )

        self.assertEqual(1, offset)
        self.assertEqual([10], [row["id"] for row in rows])
        self.assertEqual(1, clamped_offset)
        self.assertEqual([10], [row["id"] for row in clamped_rows])

    def test_render_page_contains_filter_and_sort_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data.sqlite"
            _create_db(db_path)
            columns = db_browser.table_columns(db_path)
            total, filtered, offset, rows = db_browser.query_tours(
                db_path,
                columns,
                "",
                "date_from",
                "asc",
            )

            page = db_browser.render_page(
                db_path=db_path,
                columns=columns,
                rows=rows,
                total_count=total,
                filtered_count=filtered,
                offset=offset,
                limit=db_browser.DEFAULT_LIMIT,
                query="",
                sort_column="date_from",
                sort_direction="asc",
            ).decode("utf-8")

        self.assertIn('name="q"', page)
        self.assertIn("sort=id", page)
        self.assertIn("Alpha Tour", page)
        self.assertIn("/browser/rows", page)
        self.assertIn('id="previous-page"', page)
        self.assertIn('id="next-page"', page)
        self.assertIn('name="offset"', page)
        self.assertNotIn("data-filter=", page)
        self.assertNotIn("requestSubmit", page)
        self.assertNotIn("applyClientFilter", page)


if __name__ == "__main__":
    unittest.main()
