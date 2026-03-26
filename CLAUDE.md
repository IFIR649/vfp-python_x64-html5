# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

A hybrid dashboard system that integrates **Visual FoxPro 9** with a modern web UI. VFP hosts a WebView2 browser control; a FastAPI backend serves session-based dashboards powered by **DuckDB** (query engine) + **Polars** (CSV parsing / Parquet writing). Data flows: VFP → FastAPI → DuckDB SQL on Parquet → Chart.js frontend.

## Running the Backend

```bash
# From repo root
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8766

# Or via script
scripts/start_backend.bat
```

Health check: `GET http://127.0.0.1:8766/health`

## Installing Dependencies

```bash
pip install -r backend/requirements.txt
# Key deps: fastapi, uvicorn, polars, duckdb, psutil
```

## Architecture

### Request Flow
1. **VFP** (`vfp_dashboard_bridge.prg`) checks `/health`, POSTs `/api/session/open` with CSV path + config JSON
2. **Backend** parses CSV with Polars, writes a cached Parquet file (first load only), opens a DuckDB in-memory connection with a VIEW over the Parquet, stores session in `STORE` dict (`engine.py`)
3. **VFP** navigates WebView2 to `http://127.0.0.1:8766/app?session_id=<id>`
4. **Frontend SPA** (`backend/web/app.js`) queries `/api/dashboard/query` with filters/date range
5. **Backend** builds a SQL WHERE clause and runs DuckDB queries directly against the Parquet VIEW — no DataFrame materialization. For large filtered datasets (>200K rows, ≥3 widgets), a TEMP TABLE is materialized once and all widgets query against it.
6. **Frontend** renders KPIs, charts (Chart.js), and paginated tables

### Key Backend Files

| File | Role |
|------|------|
| `backend/main.py` | FastAPI app, all endpoint definitions |
| `backend/engine.py` | Session store, DuckDB query engine, CSV→Parquet pipeline, widget rendering |
| `backend/legacy_config.py` | Config merging, column resolution, widget factory registry, dashboard builder |

### engine.py — Core Data Layer

The entire query pipeline runs through DuckDB:

- **`_duck_resource_config()`** — detects available RAM (via `psutil`) and CPU count; returns `(threads, memory_limit)`. Threads capped at 8, memory = 50% of available RAM (floor 512MB, ceiling 16GB). Falls back to `"1GB"` if `psutil` is unavailable.
- **`_create_duck_session(accelerated)`** — opens `duckdb.connect(":memory:", config={"threads": N, "memory_limit": M})` using `_duck_resource_config()`, creates `CREATE VIEW session_data AS SELECT * FROM read_parquet('...')`.
- **`_numeric_sql(field)` / `_timestamp_sql(field)`** — return `"{field}"` directly if it's a typed helper column (`__opt_num_*` / `__opt_date_*`), otherwise `TRY_CAST(...)`. Avoids redundant casting on already-typed Parquet columns.
- **`_build_where_sql(session, context)`** — returns `(where_clause, params)` for DuckDB parameterized queries (`?` placeholders). Uses `_numeric_sql` / `_timestamp_sql` for type-aware casting.
- **`_duck_batch_kpis(session, widgets, where, params, table_name)`** — single SQL query with `COUNT(*) AS __total_count` + one aggregate alias per KPI widget. Returns a dict used by all `_render_kpi` calls.
- **`_render_kpi / _render_chart / _table_payload`** — each runs one SQL query via `session.conn.execute(sql, params).fetchall()`. All accept `table_name` parameter for TEMP TABLE support.
- **`_agg_sql(aggregation, field)`** — returns SQL fragment: `SUM("__opt_num_001_col")` for helpers, `SUM(TRY_CAST("col" AS DOUBLE))` for raw columns.
- **`_date_bucket_sql(field, granularity)`** — returns `STRFTIME("__opt_date_001_col", '%Y-%m')` for helpers, `STRFTIME(TRY_CAST("col" AS TIMESTAMP), '%Y-%m')` for raw columns.
- **`_warm_session(session_id)`** — background thread fired on session open. Pre-populates `query_cache` with default dashboard and `distinct_cache` for first 5 text columns.

### Conditional TEMP TABLE Optimization

In `query_dashboard`, when all conditions are met:
- Active filters (non-empty WHERE clause)
- Dataset > 200K rows (`TEMP_TABLE_MIN_ROWS`)
- Dashboard has ≥ 3 widgets (`TEMP_TABLE_MIN_WIDGETS`)

A `CREATE TEMP TABLE __filtered AS SELECT * FROM session_data WHERE ...` is executed once, and all widget queries run against `__filtered` instead of re-scanning the Parquet. The TEMP TABLE is dropped in a `finally` block.

### Concurrency — `_query_lock`

Each `SessionState` has a `_query_lock` (`threading.Lock`). DuckDB connections are not thread-safe, so the lock serializes access in:
- `query_dashboard` — protects the non-cached execution path
- `query_table_page` — protects table pagination queries
- `filter_values` — protects distinct value queries

Cache checks happen **before** acquiring the lock (cheap read). A double-check after acquiring prevents redundant queries when the warm-up thread populates the cache first.

### Parquet Cache Strategy

On first open, CSV is parsed by Polars + helper columns are added (date parsing, number normalization for Spanish locale), written to Parquet at `{cache_dir}/{source_signature}/source.parquet`. The Parquet is sorted by the primary date column for DuckDB row-group skipping. On subsequent opens, the manifest is read and DuckDB points directly to the cached Parquet — no CSV parse.

Helper column naming (via `_helper_column_name`):
- Date helpers: `__opt_date_NNN_<slug>` (stored as `pl.Datetime` → Parquet TIMESTAMP)
- Number helpers: `__opt_num_NNN_<slug>` (stored as `pl.Float64` → Parquet DOUBLE)

### Pre-distributing Parquet Files

For large deployments (100+ machines), eliminate the first-load CSV parsing time by pre-generating the Parquet cache:

1. **Generate**: On the build machine, open the CSV once via `POST /api/session/open`. This creates the Parquet cache at `{optimizer.source_cache_dir}/{sha1_signature}/source.parquet` along with `manifest.json`.
2. **Locate**: Default cache directory is `%LOCALAPPDATA%\vfp_dashboard_cache\sources\`. Subdirectory name is a SHA1 hash of (source_path, file_size, mtime_ns, csv_options, cache_version).
3. **Distribute**: Copy the entire `{sha1}/` folder to the same cache directory on each target machine. The source CSV must exist at the same path with the same size and modification time, or the cache will be invalidated.
4. **Alternative — `parquet_source` mode**: If data is already in Parquet format, configure `csv_options.source_format: "parquet"` in the config JSON. The backend skips CSV parsing entirely.

### SessionState Fields

```python
session.conn          # duckdb.DuckDBPyConnection (in-memory, per session)
session._temp_parquet # Path | None — temp Parquet for non-cached sessions, deleted on close
session._query_lock   # threading.Lock — serializes DuckDB access for thread safety
session.open_timing   # dict: config_ms, accelerated_ms, duck_ms, preview_ms, total_ms, cache_hit
session.query_cache   # OrderedDict — full query_dashboard responses
session.table_cache   # OrderedDict — paginated table responses
session.distinct_cache # OrderedDict — filter autocomplete values
```

### API Endpoints

- `POST /api/session/open` — Load CSV + config, create session; returns `performance.open_timing` with per-phase ms. Fires background warm-up thread.
- `GET /api/session/{id}` — Get session snapshot (columns, row count, templates)
- `DELETE /api/session/{id}` — Close session, closes DuckDB conn, deletes temp Parquet
- `POST /api/dashboard/query` — Query widgets; returns `performance.phases` with `where_ms`, `kpi_ms`, `widgets_ms`
- `POST /api/table/page` — Paginated table rows
- `POST /api/filter/values` — Autocomplete values for a filter column

### Config (`config.json`)

Two dashboard modes:
- **`dashboard.templates`**: Explicit layout with cell placement per widget
- **`dashboard_modular`**: Declarative factory pattern — specify factory name + columns, layout is auto-built

CSV options default to Spanish locale: `delimiter=";"`, `decimal=","`, `encoding="utf-8-sig"`, `dayfirst=true`.

### Frontend (`backend/web/`)

Pure vanilla JS SPA. On load, extracts `session_id` from URL params, fetches snapshot, then queries dashboard. After each query a **timing toast** appears (bottom-right, 8s) showing per-phase ms breakdown — visible for debugging without opening DevTools. Charts rendered with Chart.js (bundled vendor file).

### VFP Integration

- `vfp_dashboard_bridge.prg` — Main VFP entry point, manages backend lifecycle
- `FORMS/vista_py.scx` — VFP form hosting WebView2 control via COM
- `dotnet/bridge/VfpWebViewBridgeHost.cs` — .NET host for WebView2 COM bridge
- `scripts/register_vfp_webview_bridge.ps1` — Registers COM bridge (run once on install)

## Known Technical Debt

- Root-level `kpi.py`, `graficos.py`, `tablas.py` are **legacy duplicates** — `backend/` versions are canonical
- `dotnet/host/` expects port 8765 and endpoint `/ui` — misaligned with current API (port 8766, `/app`)
- No automated test suite — manual validation only
- `python310_embed/` is the old x32 embedded Python runtime (superseded by FastAPI + x64 Python)
- `polars` still required for CSV parsing and Parquet writing even though query layer is 100% DuckDB
