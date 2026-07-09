"""
sacdateparser.py – Python-Äquivalent von sacdateparser.js
Parst deutschsprachige Datumsstrings des SAC-UTO-Tourenportals.
"""

import re

MONTH_NAMES = [None, 'Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun',
               'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez']


def parse_month(s: str) -> int:
    """Wandelt einen Monatsnamen oder eine Monatszahl in eine Ganzzahl (1–12) um."""
    s = s.strip()
    try:
        n = int(s)
        assert 1 <= n <= 12
        return n
    except ValueError:
        pass
    if s == "MÃ¤rz":  # Charset-Fehler in Seite um Dez 2024
        return 3
    # Nur die ersten 3 Zeichen vergleichen (z. B. "Sept" → "Sep")
    prefix = s[:3]
    try:
        n = MONTH_NAMES.index(prefix)
    except ValueError:
        raise ValueError(f"Unbekannter Monatsname: {s!r}")
    assert 1 <= n <= 12
    return n


def _parse_date2_int(d: str, m: str, y: str, original: str) -> str:
    """Baut einen ISO-Datumsstring (YYYY-MM-DD) aus Tag, Monat, Jahr."""
    n_month = parse_month(m)
    y_int = int(y)
    d_int = int(d)
    assert y_int >= 2000, f"Could not parse date in {original!r} ({d},{m},{y})"
    assert d_int >= 1,    f"Could not parse date in {original!r} ({d},{m},{y})"
    return f"{y_int}-{n_month:02d}-{d_int:02d}"


def _parse_date2_range_start_year(start_month: int, end_month: int, end_year: int) -> int:
    """Infers the start year for ranges that only print the end year."""
    if start_month > end_month:
        return end_year - 1
    return end_year


def parse_date2(s: str) -> dict:
    """
    Parst Datumsstrings der Form:
      "Mi 15. Aug. 2018 1 Tag"
      "Fr 30. Mär.  bis Mo 2. Apr. 2018"

    Gibt {'from': 'YYYY-MM-DD', 'to': 'YYYY-MM-DD'} zurück.
    """
    assert s != '', "parse_date2: leerer String"

    # Alle Nicht-Wort-Zeichen (außer ö/ä/ü) durch Leerzeichen ersetzen, dann splitten
    block = re.sub(r'[^\dA-Za-zöäü]+', ' ', s).strip().split()

    # block[3] == 'bis'  →  Datumsbereich
    # z. B. "Fr 30. Mär.  bis Mo 2. Apr. 2018"
    # nach Bereinigung: ['Fr', '30', 'Mär', 'bis', 'Mo', '2', 'Apr', '2018']
    if block[3] == 'bis':
        end_year = int(block[7])
        start_month = parse_month(block[2])
        end_month = parse_month(block[6])
        start_year = _parse_date2_range_start_year(start_month, end_month, end_year)
        return {
            'from': _parse_date2_int(block[1], block[2], str(start_year), s),
            'to':   _parse_date2_int(block[5], block[6], block[7], s),
        }
    else:
        return {
            'from': _parse_date2_int(block[1], block[2], block[3], s),
            'to':   _parse_date2_int(block[1], block[2], block[3], s),
        }


def _parse_date3_datestr(s: str, token: str) -> str:
    """
    Parst einen Teilstring der Form "von Mi 22. Mai 2019" oder "bis Sa 25. Mai 2019".
    """
    # Alle Kommas, Punkte und mehrfache Leerzeichen durch ein Leerzeichen ersetzen
    block = re.sub(r'[,.\s]+', ' ', s).strip().split()
    assert block[0] == token, f"no '{token}' in date '{s}'"
    assert len(block) == 5,   f"bogus number of elements in date '{s}'"
    # block: [token, weekday, day, month, year]
    month = parse_month(block[3])
    year  = int(block[4])
    day   = int(block[2])
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_date3(s) -> dict:
    """
    Parst Anmeldezeitraum-Strings, z. B.:
      "Schriftlich, Internet von Mi 22. Mai 2019 bis Sa 25. Mai 2019, Max. TN 15"
      "Internet von Mi 22. Mai 2019 bis Sa 25. Mai 2019"
      "von Mi 1. Jan. 2025 bis So 2. März 2025"
      "Internet von Do 1. Nov. 2018, Max. TN 4"
      "von So 2. Jun. 2019 bis Fr 28. Jun. 2019, Max. TN 6"
      "bis Fr 16. Sept. 2022, Max. TN 8"

    Gibt ein dict mit optionalen Keys 'from' und/oder 'to' zurück.
    """
    if s is None:
        return {}

    res = {}

    # ", Max. TN …" am Ende abschneiden
    i = s.find(', Max. TN ')
    if i > 0:
        s = s[:i]

    # "bis …" zuerst verarbeiten (von hinten)
    i = s.find('bis ')
    if i >= 0:
        res['to'] = _parse_date3_datestr(s[i:], 'bis')
        s = s[:i]

    # "von …" verarbeiten
    i = s.find('von ')
    if i >= 0:
        res['from'] = _parse_date3_datestr(s[i:], 'von')

    return res
