from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import STORE, filter_values, query_dashboard, query_table_page


WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="VFP Dashboard Bridge", version="1.0.0")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

STATE: dict[str, Any] = {
    "started_at_utc": datetime.now(timezone.utc).isoformat(),
}


class OpenSessionPayload(BaseModel):
    source_path: str
    config_path: str | None = None
    config_json: dict[str, Any] | None = None


class DashboardQueryPayload(BaseModel):
    session_id: str
    template_id: str | None = None
    filters: list[dict[str, Any]] = Field(default_factory=list)
    date_range: dict[str, Any] = Field(default_factory=dict)


class TablePagePayload(BaseModel):
    session_id: str
    widget_id: str
    template_id: str | None = None
    filters: list[dict[str, Any]] = Field(default_factory=list)
    date_range: dict[str, Any] = Field(default_factory=dict)
    page: int = 1
    page_size: int | None = None
    sort_by: str | None = None
    sort_dir: str | None = None


class FilterValuesPayload(BaseModel):
    session_id: str
    column: str
    template_id: str | None = None
    search: str = ""
    limit: int = 30
    filters: list[dict[str, Any]] = Field(default_factory=list)
    date_range: dict[str, Any] = Field(default_factory=dict)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/app")


@app.get("/app")
def dashboard_ui() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "vfp-dashboard-bridge",
        "started_at_utc": STATE["started_at_utc"],
        "session_count": STORE.count(),
    }


@app.post("/api/session/open")
def open_session(payload: OpenSessionPayload) -> dict[str, Any]:
    try:
        return STORE.open_session(payload.source_path, config_path=payload.config_path, config_json=payload.config_json)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/session/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    try:
        return STORE.session_snapshot(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/session/{session_id}")
def close_session(session_id: str) -> dict[str, Any]:
    removed = STORE.close(session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="La sesion no existe.")
    return {"ok": True, "session_id": session_id}


@app.post("/api/dashboard/query")
def dashboard_query(payload: DashboardQueryPayload) -> dict[str, Any]:
    try:
        return query_dashboard(payload.session_id, template_id=payload.template_id, filters=payload.filters, date_range=payload.date_range)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/table/page")
def table_page(payload: TablePagePayload) -> dict[str, Any]:
    try:
        return query_table_page(
            payload.session_id,
            payload.widget_id,
            template_id=payload.template_id,
            filters=payload.filters,
            date_range=payload.date_range,
            page=payload.page,
            page_size=payload.page_size,
            sort_by=payload.sort_by,
            sort_dir=payload.sort_dir,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/filter/values")
def filter_value_lookup(payload: FilterValuesPayload) -> dict[str, Any]:
    try:
        return filter_values(
            payload.session_id,
            payload.column,
            search=payload.search,
            limit=payload.limit,
            filters=payload.filters,
            date_range=payload.date_range,
            template_id=payload.template_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
