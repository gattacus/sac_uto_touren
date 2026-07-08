from __future__ import annotations

import argparse
import html
import mimetypes
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import ceil
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse


DEFAULT_DB_PATH = Path("data/data.sqlite")
DEFAULT_DATA_DIR = Path("data")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8085
DEFAULT_LIMIT = 200
DISPLAY_COLUMNS = [
    "id",
    "active",
    "date_from",
    "date_to",
    "duration",
    "status",
    "type",
    "level",
    "grp",
    "title",
    "leiter",
    "mtype",
    "type_ext",
    "level2",
    "altitude",
    "arrival",
    "subscription_period_start",
    "subscription_period_end",
    "extra_info",
    "equipment",
    "text",
    "url",
    "lastSeen",
]
NUMERIC_COLUMNS = {"id", "active", "lastSeen"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a read-only web browser for the SAC Uto SQLite database."
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"SQLite database path. Default: {DEFAULT_DB_PATH}",
    )
    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
        help=f"Directory to expose as the file server. Default: {DEFAULT_DATA_DIR}",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Bind host. Default: {DEFAULT_HOST}",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Bind port. Default: {DEFAULT_PORT}",
    )
    return parser.parse_args()


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only = ON")
    return db


def table_columns(db_path: Path) -> list[str]:
    with connect_readonly(db_path) as db:
        available = [
            row["name"]
            for row in db.execute("PRAGMA table_info(data)").fetchall()
        ]
    return [column for column in DISPLAY_COLUMNS if column in available]


def normalize_sort(columns: list[str], sort: str, direction: str) -> tuple[str, str]:
    sort_column = sort if sort in columns else "date_from"
    if sort_column not in columns:
        sort_column = columns[0]
    sort_direction = "desc" if direction.lower() == "desc" else "asc"
    return sort_column, sort_direction


def normalize_offset(value: str) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def clamp_offset(offset: int, filtered_count: int, limit: int) -> int:
    if filtered_count <= 0 or limit <= 0:
        return 0
    last_page_offset = ((filtered_count - 1) // limit) * limit
    return min(offset, last_page_offset)


def parse_column_filters(
    params: dict[str, list[str]],
    columns: list[str],
) -> dict[str, str]:
    filters = {}
    for column in columns:
        value = params.get(f"filter_{column}", [""])[0].strip()
        if value:
            filters[column] = value
    return filters


def query_tours(
    db_path: Path,
    columns: list[str],
    query: str,
    sort_column: str,
    sort_direction: str,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    column_filters: dict[str, str] | None = None,
) -> tuple[int, int, int, list[sqlite3.Row]]:
    where_clauses: list[str] = []
    params: list[Any] = []
    if query:
        like = f"%{query}%"
        clauses = [
            f"COALESCE(CAST({column} AS TEXT), '') LIKE ?"
            for column in columns
        ]
        where_clauses.append(f"({' OR '.join(clauses)})")
        params.extend([like] * len(columns))

    for column, value in (column_filters or {}).items():
        if column not in columns:
            continue
        where_clauses.append(f"COALESCE(CAST({column} AS TEXT), '') LIKE ?")
        params.append(f"%{value}%")

    where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    order_expr = sort_column
    if sort_column in NUMERIC_COLUMNS:
        order_expr = f"CAST({sort_column} AS INTEGER)"

    column_sql = ", ".join(columns)
    sql = (
        f"SELECT {column_sql} FROM data {where} "
        f"ORDER BY {order_expr} {sort_direction.upper()}, id ASC "
        "LIMIT ? OFFSET ?"
    )
    with connect_readonly(db_path) as db:
        count = db.execute("SELECT COUNT(*) FROM data").fetchone()[0]
        filtered_count = db.execute(
            f"SELECT COUNT(*) FROM data {where}",
            params,
        ).fetchone()[0]
        offset = clamp_offset(offset, filtered_count, limit)
        rows = db.execute(sql, [*params, limit, offset]).fetchall()
    return count, filtered_count, offset, rows


def page_url(
    query: str,
    column_filters: dict[str, str],
    sort: str,
    direction: str,
) -> str:
    params = {
        "sort": sort,
        "dir": direction,
    }
    if query:
        params["q"] = query
    for column, value in column_filters.items():
        if value:
            params[f"filter_{column}"] = value
    return f"/browser?{urlencode(params)}"


def render_cell(column: str, value: Any, row: sqlite3.Row | None = None) -> str:
    text = "" if value is None else str(value)
    escaped = html.escape(text)
    if column == "active":
        label = "active" if text == "1" else "old"
        class_name = "active-1" if text == "1" else "active-0"
        return f'<span class="badge {class_name}">{label}</span>'
    if column == "title" and text and row is not None and "url" in row.keys() and row["url"]:
        href = html.escape(str(row["url"]), quote=True)
        return f'<a href="{href}" target="_blank" rel="noreferrer">{escaped}</a>'
    if column == "status" and text:
        status_class = "".join(char for char in text.lower() if char.isalnum() or char in "_-")
        return f'<span class="badge status-{status_class}">{escaped}</span>'
    if column == "url" and text:
        href = html.escape(text, quote=True)
        return f'<a href="{href}" target="_blank" rel="noreferrer">detail</a>'
    if column in {"text", "equipment", "extra_info"}:
        title = html.escape(text, quote=True)
        return f'<div class="clip" title="{title}">{escaped}</div>'
    return escaped


def render_rows(columns: list[str], rows: list[sqlite3.Row]) -> str:
    body_rows = []
    for row in rows:
        cells = [
            f'<td data-column="{html.escape(column, quote=True)}">{render_cell(column, row[column], row)}</td>'
            for column in columns
        ]
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    if not body_rows:
        return (
            f'<tr><td class="empty" colspan="{len(columns)}">'
            "No tours match the filter.</td></tr>"
        )
    return "".join(body_rows)


def format_count(
    offset: int,
    rendered_count: int,
    filtered_count: int,
    total_count: int,
) -> str:
    if filtered_count == 0:
        return f"0 / {total_count}" if total_count else "0"
    start = offset + 1
    end = offset + rendered_count
    if filtered_count == total_count:
        return f"{start}-{end} / {total_count}"
    return f"{start}-{end} / {filtered_count} / {total_count}"


def format_page(offset: int, filtered_count: int, limit: int) -> str:
    if filtered_count <= 0:
        return "Page 1 / 1"
    page = (offset // limit) + 1
    pages = max(1, ceil(filtered_count / limit))
    return f"Page {page} / {pages}"


def render_page(
    *,
    db_path: Path,
    columns: list[str],
    rows: list[sqlite3.Row],
    total_count: int,
    filtered_count: int,
    offset: int,
    limit: int,
    query: str,
    column_filters: dict[str, str],
    sort_column: str,
    sort_direction: str,
) -> bytes:
    next_direction = {
        column: "desc" if sort_column == column and sort_direction == "asc" else "asc"
        for column in columns
    }
    header_cells = []
    filter_cells = []
    for column in columns:
        indicator = ""
        if sort_column == column:
            indicator = "▲" if sort_direction == "asc" else "▼"
        href = page_url(query, column_filters, column, next_direction[column])
        header_cells.append(
            '<th><a href="{href}" data-sort="{sort}" data-next-dir="{direction}">'
            '<span>{label}</span><span class="sort">{indicator}</span></a></th>'.format(
                href=html.escape(href, quote=True),
                sort=html.escape(column, quote=True),
                direction=html.escape(next_direction[column], quote=True),
                label=html.escape(column),
                indicator=indicator,
            )
        )
        filter_value = html.escape(column_filters.get(column, ""), quote=True)
        filter_label = html.escape(f"Filter {column}", quote=True)
        filter_name = html.escape(f"filter_{column}", quote=True)
        filter_cells.append(
            '<th><input class="column-filter" form="controls" type="search" '
            'name="{name}" value="{value}" aria-label="{label}" autocomplete="off"></th>'.format(
                name=filter_name,
                value=filter_value,
                label=filter_label,
            )
        )

    current_query = html.escape(query, quote=True)
    count_text = format_count(offset, len(rows), filtered_count, total_count)
    page_text = format_page(offset, filtered_count, limit)
    has_previous = offset > 0
    has_next = offset + len(rows) < filtered_count
    rendered = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SAC Uto Tours</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f8;
      --panel: #ffffff;
      --line: #d8dde3;
      --line-strong: #b6c0cb;
      --text: #17202a;
      --muted: #5e6b78;
      --accent: #0f766e;
      --accent-soft: #d8f3ef;
      --warn-soft: #ffe6c7;
      --full-soft: #fde2e1;
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.4;
    }}

    header {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      align-items: end;
      padding: 18px 20px 12px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 5;
    }}

    h1 {{
      margin: 0 0 2px;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0;
    }}

    .meta {{
      color: var(--muted);
      font-size: 13px;
    }}

    .controls {{
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: flex-end;
      min-width: min(520px, 100%);
    }}

    input[type="search"] {{
      width: min(420px, 48vw);
      min-width: 220px;
      height: 38px;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      padding: 0 12px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }}

    .count {{
      min-width: 150px;
      color: var(--muted);
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}

    .pager {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }}

    .pager button {{
      width: 34px;
      height: 34px;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
      cursor: pointer;
    }}

    .pager button:disabled {{
      cursor: default;
      opacity: 0.45;
    }}

    .pager span {{
      min-width: 86px;
      text-align: center;
      font-variant-numeric: tabular-nums;
    }}

    main {{ padding: 14px 20px 24px; }}

    .table-wrap {{
      overflow: auto;
      border: 1px solid var(--line);
      background: var(--panel);
      max-height: calc(100vh - 106px);
    }}

    table {{
      width: max-content;
      min-width: 100%;
      border-collapse: collapse;
    }}

    th,
    td {{
      border-bottom: 1px solid var(--line);
      padding: 7px 9px;
      text-align: left;
      vertical-align: top;
      max-width: 340px;
    }}

    th {{
      position: sticky;
      top: 0;
      z-index: 2;
      background: #eef2f5;
      color: #27323d;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
      border-bottom-color: var(--line-strong);
    }}

    th a {{
      display: inline-flex;
      gap: 6px;
      width: 100%;
      color: inherit;
      text-decoration: none;
    }}

    .sort {{
      width: 1.2em;
      color: var(--accent);
      font-variant-numeric: tabular-nums;
    }}

    thead tr:first-child th {{
      top: 0;
      z-index: 3;
    }}

    thead tr:nth-child(2) th {{
      top: 34px;
      z-index: 3;
      padding: 4px 6px;
      background: #f8fafb;
    }}

    input.column-filter[type="search"] {{
      width: 100%;
      min-width: 92px;
      height: 28px;
      border-color: var(--line);
      padding: 0 7px;
      font-size: 12px;
    }}

    tbody tr:nth-child(even) {{ background: #fafbfc; }}
    tbody tr:hover {{ background: #edf7f5; }}

    td {{ white-space: nowrap; }}

    td[data-column="title"],
    td[data-column="leiter"],
    td[data-column="arrival"],
    td[data-column="extra_info"],
    td[data-column="equipment"],
    td[data-column="text"],
    td[data-column="url"] {{
      white-space: normal;
      min-width: 180px;
    }}

    td[data-column="text"] {{ max-width: 520px; }}

    .clip {{
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}

    .badge {{
      display: inline-block;
      min-width: 42px;
      border-radius: 999px;
      padding: 2px 8px;
      text-align: center;
      font-size: 12px;
      font-weight: 650;
    }}

    .active-1 {{ background: var(--accent-soft); color: #07534e; }}
    .active-0 {{ background: #e8ebee; color: #5b6570; }}
    .status-open {{ background: var(--accent-soft); color: #07534e; }}
    .status-full {{ background: var(--full-soft); color: #8c231e; }}
    .status-cancelled {{ background: var(--warn-soft); color: #7a4800; }}
    .status-ok {{ background: #e7eef9; color: #244f8f; }}

    a {{ color: #075e9f; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}

    .empty {{
      padding: 30px;
      color: var(--muted);
      text-align: center;
    }}

    @media (max-width: 760px) {{
      header {{
        grid-template-columns: 1fr;
        align-items: stretch;
      }}

      .controls {{
        justify-content: stretch;
        min-width: 0;
      }}

      input[type="search"] {{
        width: 100%;
        min-width: 0;
      }}

      .count {{ min-width: 96px; }}

      .pager {{
        width: 100%;
        justify-content: flex-end;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>SAC Uto Tours</h1>
      <div class="meta">Read-only view of {html.escape(str(db_path))}</div>
    </div>
    <form id="controls" class="controls" method="get" action="/browser">
      <input type="hidden" name="sort" value="{html.escape(sort_column, quote=True)}">
      <input type="hidden" name="dir" value="{html.escape(sort_direction, quote=True)}">
      <input type="hidden" name="offset" value="{offset}">
      <input id="filter" name="q" type="search" value="{current_query}" placeholder="Filter tours" autocomplete="off">
      <div class="count" id="count">{html.escape(count_text)}</div>
      <div class="pager" aria-label="Table pages">
        <button id="previous-page" type="button" aria-label="Previous page" {"disabled" if not has_previous else ""}>‹</button>
        <span id="page">{html.escape(page_text)}</span>
        <button id="next-page" type="button" aria-label="Next page" {"disabled" if not has_next else ""}>›</button>
      </div>
    </form>
  </header>
  <main>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>{''.join(header_cells)}</tr>
          <tr>{''.join(filter_cells)}</tr>
        </thead>
        <tbody>{render_rows(columns, rows)}</tbody>
      </table>
    </div>
  </main>
  <script>
    const form = document.querySelector("form");
    const input = document.querySelector("#filter");
    const columnFilters = Array.from(document.querySelectorAll(".column-filter"));
    const sortLinks = Array.from(document.querySelectorAll("thead a[data-sort]"));
    const count = document.querySelector("#count");
    const tbody = document.querySelector("tbody");
    const sortInput = form.querySelector('input[name="sort"]');
    const dirInput = form.querySelector('input[name="dir"]');
    const offsetInput = form.querySelector('input[name="offset"]');
    const previousPage = document.querySelector("#previous-page");
    const nextPage = document.querySelector("#next-page");
    const page = document.querySelector("#page");
    const limit = {limit};
    let filterTimer = null;
    let filterRequest = null;
    let pageState = {{
      offset: {offset},
      rendered: {len(rows)},
      filtered: {filtered_count},
      total: {total_count}
    }};

    function collectParams() {{
      const params = new URLSearchParams({{
        q: input.value.trim(),
        sort: sortInput.value,
        dir: dirInput.value,
        offset: offsetInput.value
      }});
      columnFilters.forEach(filter => {{
        const value = filter.value.trim();
        if (value) params.set(filter.name, value);
      }});
      return params;
    }}

    function countText(offset, rendered, filtered, total) {{
      if (filtered === 0) return total ? `0 / ${{total}}` : "0";
      const start = offset + 1;
      const end = offset + rendered;
      if (filtered === total) return `${{start}}-${{end}} / ${{total}}`;
      return `${{start}}-${{end}} / ${{filtered}} / ${{total}}`;
    }}

    function pageText(offset, filtered) {{
      if (filtered <= 0) return "Page 1 / 1";
      return `Page ${{Math.floor(offset / limit) + 1}} / ${{Math.max(1, Math.ceil(filtered / limit))}}`;
    }}

    function updatePaging() {{
      offsetInput.value = String(pageState.offset);
      count.textContent = countText(
        pageState.offset,
        pageState.rendered,
        pageState.filtered,
        pageState.total
      );
      page.textContent = pageText(pageState.offset, pageState.filtered);
      previousPage.disabled = pageState.offset <= 0;
      nextPage.disabled = pageState.offset + pageState.rendered >= pageState.filtered;
    }}

    function updateSortIndicators() {{
      sortLinks.forEach(link => {{
        const column = link.dataset.sort;
        const indicator = link.querySelector(".sort");
        const selected = sortInput.value === column;
        const nextDirection = selected && dirInput.value === "asc" ? "desc" : "asc";
        link.dataset.nextDir = nextDirection;
        if (indicator) indicator.textContent = selected ? (dirInput.value === "asc" ? "▲" : "▼") : "";
      }});
    }}

    async function loadRows() {{
      if (filterRequest) filterRequest.abort();
      filterRequest = new AbortController();
      const params = collectParams();
      const response = await fetch(`/browser/rows?${{params.toString()}}`, {{
        signal: filterRequest.signal,
        cache: "no-store"
      }});
      if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
      tbody.innerHTML = await response.text();
      pageState = {{
        offset: Number(response.headers.get("X-Offset") || 0),
        rendered: Number(response.headers.get("X-Rendered-Count") || 0),
        filtered: Number(response.headers.get("X-Filtered-Count") || 0),
        total: Number(response.headers.get("X-Total-Count") || 0)
      }};
      updatePaging();
      updateSortIndicators();
    }}

    function scheduleFilterLoad() {{
      clearTimeout(filterTimer);
      filterTimer = setTimeout(() => {{
        offsetInput.value = "0";
        loadRows().catch(error => {{
          if (error.name !== "AbortError") console.error(error);
        }});
      }}, 180);
    }}

    input.addEventListener("input", () => {{
      scheduleFilterLoad();
    }});
    columnFilters.forEach(filter => filter.addEventListener("input", scheduleFilterLoad));
    form.addEventListener("submit", event => {{
      event.preventDefault();
      clearTimeout(filterTimer);
      offsetInput.value = "0";
      loadRows().catch(error => console.error(error));
    }});
    previousPage.addEventListener("click", () => {{
      offsetInput.value = String(Math.max(0, pageState.offset - limit));
      loadRows().catch(error => console.error(error));
    }});
    nextPage.addEventListener("click", () => {{
      offsetInput.value = String(pageState.offset + limit);
      loadRows().catch(error => console.error(error));
    }});
    sortLinks.forEach(link => link.addEventListener("click", event => {{
      event.preventDefault();
      sortInput.value = link.dataset.sort || sortInput.value;
      dirInput.value = link.dataset.nextDir || "asc";
      offsetInput.value = "0";
      loadRows().catch(error => console.error(error));
    }}));
    updatePaging();
    updateSortIndicators();
  </script>
</body>
</html>
"""
    return rendered.encode("utf-8")


def render_missing_page(db_path: Path) -> bytes:
    rendered = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SAC Uto Tours</title>
  <style>
    body {{
      margin: 0;
      background: #f6f7f8;
      color: #17202a;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 15px;
      line-height: 1.5;
    }}
    main {{
      max-width: 760px;
      margin: 48px auto;
      padding: 0 20px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 22px;
      letter-spacing: 0;
    }}
    p {{ color: #5e6b78; }}
    code {{
      background: #e8ebee;
      border: 1px solid #d8dde3;
      border-radius: 4px;
      padding: 2px 5px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>SAC Uto Tours</h1>
    <p>The SQLite database was not found at <code>{html.escape(str(db_path))}</code>.</p>
    <p>Run the scraper first, then reload this page.</p>
  </main>
</body>
</html>
"""
    return rendered.encode("utf-8")


class BrowserHandler(BaseHTTPRequestHandler):
    server_version = "SacUtoDbBrowser/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/browser/rows", "/browser/rows/"}:
            self.serve_rows(parsed)
            return
        if parsed.path in {"/browser", "/browser/"}:
            self.serve_browser(parsed)
            return

        self.serve_file(parsed)

    def serve_browser(self, parsed: object) -> None:
        if not self.server.db_path.exists():
            body = render_missing_page(self.server.db_path)
            self.send_html(body)
            return

        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0].strip()
        offset = normalize_offset(params.get("offset", ["0"])[0])

        try:
            columns = table_columns(self.server.db_path)
            if not columns:
                raise RuntimeError("The data table has no known display columns.")
            sort_column, sort_direction = normalize_sort(
                columns,
                params.get("sort", ["date_from"])[0],
                params.get("dir", ["asc"])[0],
            )
            column_filters = parse_column_filters(params, columns)
            total_count, filtered_count, offset, rows = query_tours(
                self.server.db_path,
                columns,
                query,
                sort_column,
                sort_direction,
                offset=offset,
                column_filters=column_filters,
            )
            body = render_page(
                db_path=self.server.db_path,
                columns=columns,
                rows=rows,
                total_count=total_count,
                filtered_count=filtered_count,
                offset=offset,
                limit=DEFAULT_LIMIT,
                query=query,
                column_filters=column_filters,
                sort_column=sort_column,
                sort_direction=sort_direction,
            )
        except Exception as exc:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, explain=str(exc))
            return

        self.send_html(body)

    def serve_rows(self, parsed: object) -> None:
        if not self.server.db_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0].strip()
        offset = normalize_offset(params.get("offset", ["0"])[0])

        try:
            columns = table_columns(self.server.db_path)
            if not columns:
                raise RuntimeError("The data table has no known display columns.")
            sort_column, sort_direction = normalize_sort(
                columns,
                params.get("sort", ["date_from"])[0],
                params.get("dir", ["asc"])[0],
            )
            column_filters = parse_column_filters(params, columns)
            total_count, filtered_count, offset, rows = query_tours(
                self.server.db_path,
                columns,
                query,
                sort_column,
                sort_direction,
                offset=offset,
                column_filters=column_filters,
            )
            body = render_rows(columns, rows).encode("utf-8")
        except Exception as exc:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, explain=str(exc))
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Total-Count", str(total_count))
        self.send_header("X-Filtered-Count", str(filtered_count))
        self.send_header("X-Rendered-Count", str(len(rows)))
        self.send_header("X-Offset", str(offset))
        self.send_header("X-Limit", str(DEFAULT_LIMIT))
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, parsed: object) -> None:
        requested_path = unquote(parsed.path)
        relative_path = requested_path.lstrip("/") or "."
        data_dir = self.server.data_dir.resolve()
        candidate = (self.server.data_dir / relative_path).resolve()

        if candidate != data_dir and data_dir not in candidate.parents:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if candidate.is_dir():
            self.serve_directory(candidate, requested_path)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        body = candidate.read_bytes()
        content_type, _ = mimetypes.guess_type(candidate.name)
        if candidate.name.endswith(".sqlite"):
            content_type = "application/vnd.sqlite3"
        if content_type is None:
            content_type = "application/octet-stream"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_directory(self, directory: Path, requested_path: str) -> None:
        entries = sorted(
            directory.iterdir(),
            key=lambda path: (not path.is_dir(), path.name.lower()),
        )
        rows = ['<li><a href="/browser">Browse SQLite database</a></li>']
        if directory.resolve() != self.server.data_dir.resolve():
            rows.append('<li><a href="../">../</a></li>')

        base = requested_path
        if not base.endswith("/"):
            base = f"{base}/"
        for entry in entries:
            name = f"{entry.name}/" if entry.is_dir() else entry.name
            href = quote(f"{base}{name}")
            rows.append(
                '<li><a href="{href}">{name}</a></li>'.format(
                    href=html.escape(href, quote=True),
                    name=html.escape(name),
                )
            )

        body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Directory listing</title>
  <style>
    body {{
      margin: 0;
      background: #f6f7f8;
      color: #17202a;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 15px;
      line-height: 1.5;
    }}
    main {{
      max-width: 860px;
      margin: 36px auto;
      padding: 0 20px;
    }}
    h1 {{
      margin: 0 0 16px;
      font-size: 22px;
      letter-spacing: 0;
    }}
    ul {{
      margin: 0;
      padding: 0;
      list-style: none;
      border: 1px solid #d8dde3;
      background: #fff;
    }}
    li + li {{ border-top: 1px solid #d8dde3; }}
    a {{
      display: block;
      padding: 9px 12px;
      color: #075e9f;
      text-decoration: none;
    }}
    a:hover {{ background: #edf7f5; text-decoration: underline; }}
  </style>
</head>
<body>
  <main>
    <h1>Directory listing for {html.escape(requested_path or "/")}</h1>
    <ul>{''.join(rows)}</ul>
  </main>
</body>
</html>
""".encode("utf-8")
        self.send_html(body)

    def send_html(self, body: bytes) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class DbBrowserServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], db_path: Path, data_dir: Path):
        super().__init__(server_address, BrowserHandler)
        self.db_path = db_path
        self.data_dir = data_dir


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    data_dir = Path(args.data_dir)
    server = DbBrowserServer((args.host, args.port), db_path, data_dir)
    print(
        f"Serving {data_dir} at http://{args.host}:{args.port}/ "
        f"and {db_path} at /browser",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
