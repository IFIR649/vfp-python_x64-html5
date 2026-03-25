# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

A hybrid dashboard system that integrates **Visual FoxPro 9** with a modern web UI. VFP hosts a WebView2 browser control; a FastAPI backend serves session-based dashboards powered by Polars. Data flows: VFP → FastAPI → Polars aggregations → Chart.js frontend.

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
# Key deps: fastapi, uvicorn, polars
```

## Architecture

### Request Flow
1. **VFP** (`vfp_dashboard_bridge.prg`) checks `/health`, POSTs `/api/session/open` with CSV path + config JSON
2. **Backend** loads CSV lazily with Polars, stores session in-memory (`STORE` dict in `engine.py`), returns session snapshot
3. **VFP** navigates WebView2 to `http://127.0.0.1:8766/app?session_id=<id>`
4. **Frontend SPA** (`backend/web/app.js`) queries `/api/dashboard/query` with filters/date range
5. **Backend** applies filters to LazyFrame, aggregates, returns rendered widget JSON
6. **Frontend** renders KPIs, charts (Chart.js), and paginated tables

### Key Backend Files

| File | Role |
|------|------|
| `backend/main.py` | FastAPI app, all endpoint definitions |
| `backend/engine.py` | Session store, Polars data loading, filtering, aggregation, widget rendering |
| `backend/legacy_config.py` | Config merging, column resolution, widget factory registry, dashboard builder |
| `backend/kpi.py` | KPI widget factories (`kpi_sum`, `kpi_avg`, `kpi_count`, etc.) |
| `backend/graficos.py` | Chart widget factories (`bar`, `linea`, `dona`, `pie`, etc.) |
| `backend/tablas.py` | Table widget factory with pagination |

### API Endpoints

- `POST /api/session/open` — Load CSV + config, create session
- `GET /api/session/{id}` — Get session snapshot (columns, row count, templates)
- `DELETE /api/session/{id}` — Close session
- `POST /api/dashboard/query` — Query widgets with `{session_id, template, filters, fecha_desde, fecha_hasta}`
- `POST /api/table/page` — Paginated table rows
- `POST /api/filter/values` — Autocomplete values for a filter column

### Config (`config.json`)

Two dashboard modes:
- **`dashboard.templates`**: Explicit layout with cell placement per widget
- **`dashboard_modular`**: Declarative factory pattern — specify factory name + columns, layout is auto-built

CSV options default to Spanish locale: `delimiter=";"`, `decimal=","`, `encoding="utf-8-sig"`, `dayfirst=true`.

### Frontend (`backend/web/`)

Pure vanilla JS SPA. State: `sessionId`, `snapshot`, `chartInstances`. On load, extracts `session_id` from URL params, fetches snapshot, then queries dashboard. Filter inputs use datalist autocomplete with 300ms debounce. Charts rendered with Chart.js (bundled vendor file).

### VFP Integration

- `vfp_dashboard_bridge.prg` — Main VFP entry point, manages backend lifecycle
- `FORMS/vista_py.scx` — VFP form hosting WebView2 control via COM
- `dotnet/bridge/VfpWebViewBridgeHost.cs` — .NET host for WebView2 COM bridge
- `scripts/register_vfp_webview_bridge.ps1` — Registers COM bridge (run once on install)

## Known Technical Debt

- Root-level `kpi.py`, `graficos.py`, `tablas.py` are **legacy duplicates** of `backend/` versions — the `backend/` versions are canonical
- `dotnet/host/` expects port 8765 and endpoint `/ui` — misaligned with current API (port 8766, `/app`)
- No automated test suite — manual validation only
- `python310_embed/` directory is the old embedded Python runtime for the legacy pipeline (now superseded by the FastAPI approach)
