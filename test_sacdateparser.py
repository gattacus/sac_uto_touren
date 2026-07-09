"""
test_sacdateparser.py – Python-Port von sacdateparser_test.js

Ausführen mit pytest:
    pytest test_sacdateparser.py -v

Oder ohne pytest (nur stdlib):
    python -m unittest test_sacdateparser -v
"""

import unittest
from sacdateparser import parse_date2, parse_date3


class TestParseDate2(unittest.TestCase):

    def test_single_day(self):
        self.assertEqual(
            parse_date2('Mi 15. Aug. 2018 1 Tag'),
            {'from': '2018-08-15', 'to': '2018-08-15'},
        )

    def test_range_same_year(self):
        self.assertEqual(
            parse_date2('Fr 30. Mär.  bis Mo 2. Apr. 2018'),
            {'from': '2018-03-30', 'to': '2018-04-02'},
        )

    def test_range_crosses_year_boundary(self):
        self.assertEqual(
            parse_date2('Di 30. Dez.  bis Fr 2. Jan. 2026'),
            {'from': '2025-12-30', 'to': '2026-01-02'},
        )


class TestParseDate3(unittest.TestCase):

    def test_complete(self):
        self.assertEqual(
            parse_date3('Schriftlich, Internet von Mi 22. Mai 2019 bis Sa 25. Mai 2019, Max. TN 15'),
            {'from': '2019-05-22', 'to': '2019-05-25'},
        )

    def test_mixed(self):
        self.assertEqual(
            parse_date3('Internet von Mi 22. Mai 2019 bis Sa 25. Mai 2019'),
            {'from': '2019-05-22', 'to': '2019-05-25'},
        )

    def test_from_only(self):
        self.assertEqual(
            parse_date3('Internet von Do 1. Nov. 2018, Max. TN 4'),
            {'from': '2018-11-01'},
        )

    def test_full(self):
        self.assertEqual(
            parse_date3('von So 2. Jun. 2019 bis Fr 28. Jun. 2019, Max. TN 6'),
            {'from': '2019-06-02', 'to': '2019-06-28'},
        )

    def test_to_only(self):
        self.assertEqual(
            parse_date3('bis Fr 16. Sept. 2022, Max. TN 8'),
            {'to': '2022-09-16'},
        )
    def test_standard_line_2026(self):
        self.assertEqual(
            parse_date3('Online von Fr 27. März 2026 bis Di 31. März 2026, Max. TN 8'),
            {'from': '2026-03-27', 'to': '2026-03-31'},
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
