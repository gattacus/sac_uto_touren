# SAC Uto Tour List – Python Scraper

This is a scraper that runs on [Morph](https://morph.io). To get started [see the documentation](https://morph.io/documentation)

-> [https://morph.io/daald-docker/sac_uto_touren](https://morph.io/daald-docker/sac_uto_touren)

The original web page is awful, if you have to decide which tours you want to subscribe and when. I once wrote an alternative frontend, but I never finished and it doesn't work anymore with the current data structure.

Let me know if you are using this data for something.



## Description

A scraper for the tour list of [SAC Section Uto](https://sac-uto.ch/de/aktivitaeten/touren-und-kurse/).
Scraped data is stored in a local SQLite database.

This is a Python port of the original Node.js scraper at
[daald-docker/sac_uto_touren](https://github.com/daald-docker/sac_uto_touren). For maintainability,
I migrated it now to python. Thanks to Claude AI.

---

## Files

| File                    | Description                                                        |
|-------------------------|--------------------------------------------------------------------|
| `scraper.py`            | Main script – fetches tour list and detail pages, writes to SQLite |
| `sacdateparser.py`      | Helper functions for parsing German-language date strings          |
| `test_sacdateparser.py` | Unit tests for the date parser                                     |
| `test_scraper.py`       | Unit tests for scraper parsing, schema migration, and persistence  |

---

## Requirements

Python 3.10+ and the following packages:

```bash
pip install -r requirements.txt
```

---

## Usage

### Scrape all tours

```bash
python scraper.py
```

The scraper automatically paginates through all available tours and writes the
results to `data.sqlite` in the current directory.

To write to a different SQLite path, set `SCRAPER_DB_PATH`:

```bash
SCRAPER_DB_PATH=/tmp/sac_uto_touren/data.sqlite python scraper.py
```

To adjust verbosity, either pass `--log-level` or set `SCRAPER_LOG_LEVEL`.
Supported levels are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.

```bash
python scraper.py --log-level DEBUG
SCRAPER_LOG_LEVEL=WARNING python scraper.py
```

### Process a single tour URL directly

Useful for testing or debugging a specific tour:

```bash
python scraper.py "https://sac-uto.ch/de/aktivitaeten/touren-und-kurse/?page=detail&touren_nummer=5947"
```

In single-tour mode no database writes are performed – data is printed to stdout.
Operational logs continue to go to stderr, so stdout stays usable for the parsed
record output.

### Historical backfill

To fetch all year-specific archive pages offered by the SAC website, run:

```bash
python scraper.py --historical
```

In the Docker setup, run the same mode inside the scraper container:

```bash
docker compose exec sac-uto-scraper python scraper.py --historical
```

Historical mode first scrapes each year from the website's year dropdown, then
scrapes the normal current listing last. Rows found only in historical year
pages are retained with `active=0`; only tours still present in the current
listing are stored with `active=1`.

---

## Tests

```bash
# run the full test suite
python -m unittest -v

# or run individual modules
python -m unittest test_sacdateparser -v
python -m unittest test_scraper -v
```

---

## Docker / Ofelia deployment

The repository now includes a `Dockerfile` and a [`compose.yaml`](./compose.yaml)
for a homeserver setup where an existing
[Ofelia](https://github.com/mcuadros/ofelia) container triggers the scraper on a
schedule.

### What the compose stack does

- `sac-uto-scraper`: builds this repo into a small Python image and keeps one lightweight
  container running so Ofelia can `exec` the scraper on schedule while the same
  container serves the generated SQLite file over HTTP.

The SQLite file is stored on the host in `./data/data.sqlite`.

### Start the stack

```bash
docker compose up -d --build
```

Your existing Ofelia container must be able to see this `sac-uto-scraper`
container on the same Docker daemon and must not filter it out. The compose
service sets a fixed Docker container name and hostname of `sac-uto-scraper`
and exposes the required `ofelia.job-exec.*` labels directly.

### Schedule configuration

By default, the container labels expose this cron schedule:

```text
0 4 * * *
```

You can override it via an environment variable before starting the stack:

```bash
OFELIA_SCHEDULE="0 */6 * * *" docker compose up -d
```

Ofelia runs the command inside the `sac-uto-scraper` container:

```bash
python scraper.py
```

The job is configured with `no-overlap=true`, so a second run will not start
while a previous one is still active.

### Manual run

```bash
docker compose exec sac-uto-scraper python scraper.py
```

### Download the resulting SQLite file

The `sac-uto-scraper` container also runs a simple file server for the `/data`
directory, so the SQLite file is available over HTTP from the same container.

By default it binds to `0.0.0.0:8084`, so any device on your local network can
fetch it from the homeserver IP:

```bash
curl -fO http://YOUR-HOMESERVER-IP:8084/data.sqlite
```

If you want to limit it back to localhost only, override the bind address when
starting the stack:

```bash
SQLITE_DOWNLOAD_BIND="127.0.0.1:8084" docker compose up -d
```

You can also skip HTTP entirely and copy the file straight from the host because
it is persisted in `./data/data.sqlite`.

---

## Database schema

Results are written to `data.sqlite`, table `data`:

| Column                                                  | Description                                                          |
|---------------------------------------------------------|----------------------------------------------------------------------|
| `id`                                                    | Tour number (from the URL)                                           |
| `active`                                                | `1` if the tour was still present on the website during the last run |
| `lastSeen`                                              | Unix timestamp (ms) of the last successful fetch                     |
| `date_from` / `date_to`                                 | Tour date (ISO 8601)                                                 |
| `duration`                                              | Raw duration text from the overview list (e.g. `1 Tag`, `2 Tage`)   |
| `status`                                                | `open`, `full`, `cancelled`, or `ok`                                 |
| `type`                                                  | Tour type (e.g. `Ss`, `Hw`)                                          |
| `level`                                                 | Difficulty rating (e.g. `WT4`)                                       |
| `grp`                                                   | Group (e.g. `Senioren`)                                              |
| `title`                                                 | Tour name                                                            |
| `leiter`                                                | Tour leader name(s); multiple leaders are concatenated with ` | `   |
| `url`                                                   | Link to the detail page                                              |
| `altitude`                                              | Ascent/descent and hiking time                                       |
| `mtype`                                                 | Event type (e.g. `Tour`, `Kurs`)                                     |
| `type_ext`                                              | Type/supplement with long description                                |
| `level2`                                                | Technical requirements                                               |
| `arrival`                                               | Travel route (e.g. `ÖV`)                                             |
| `text`                                                  | Route / detailed description                                         |
| `extra_info`                                            | Additional information                                               |
| `equipment`                                             | Required equipment                                                   |
| `subscription_period_start` / `subscription_period_end` | Registration period (ISO 8601)                                       |

---

## Notes

- The scraper automatically retries `requests`-level fetch failures with exponential backoff.
- Tours with a clearly malformed date (`Do 0. …`) are skipped or retried – this is a
  known server-side race condition on the source website.
- At most 2 detail pages are fetched concurrently to avoid hammering the server.
- With `--log-level DEBUG`, the scraper also logs total runtime at the end of the run.
- Currently, the default process is to download all pages, even unchanged. The plan is to implement
  a pre-condition based on the overview list to avoid unnecessary reload of everything. For this, the
  python migration was the preparation step.
