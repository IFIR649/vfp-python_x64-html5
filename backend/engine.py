from __future__ import annotations

import json
import math
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import polars as pl

from .legacy_config import (
    ColumnResolver,
    build_column_rename_map,
    build_dashboard_config,
    load_config,
    normalize_column_names,
    resolve_source_path,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _dtype_name(dtype: Any) -> str:
    return str(dtype)


def _is_numeric_dtype(dtype: Any) -> bool:
    text = _dtype_name(dtype)
    return text.startswith(("Int", "UInt", "Float", "Decimal"))


def _is_temporal_dtype(dtype: Any) -> bool:
    text = _dtype_name(dtype)
    return text.startswith("Date") or text.startswith("Datetime") or text == "Time"


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat(sep=" ")
    return str(value)


def _normalize_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat(sep=" ")
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, (datetime, date, time)):
        return value.isoformat(sep=" ")
    return value


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace("\u00a0", "")
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if text.startswith("-"):
        negative = True
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]

    if "." in text and "," in text:
        if text.rfind(".") > text.rfind(","):
            text = text.replace(",", "")
        else:
            text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1 and "," not in text:
        text = text.replace(".", "")
    elif text.count(",") > 1 and "." not in text:
        text = text.replace(",", "")
    elif "," in text and "." not in text:
        text = text.replace(",", ".")

    try:
        number = float(text)
        return -number if negative else number
    except Exception:
        return None


def _date_formats(dayfirst: bool) -> list[str]:
    primary = [
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
    ]
    secondary = [
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%m-%d-%Y %H:%M:%S",
        "%m-%d-%Y %H:%M",
        "%m-%d-%Y",
    ]
    if not dayfirst:
        primary, secondary = secondary, primary
    return [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        *primary,
        *secondary,
    ]


def _parse_datetime_value(value: Any, dayfirst: bool, end_of_day: bool = False) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if isinstance(value, date):
        if end_of_day:
            return datetime.combine(value, time(23, 59, 59))
        return datetime.combine(value, time(0, 0, 0))

    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        text = text.replace("T", " ")

    try:
        parsed = datetime.fromisoformat(text)
        if len(text) == 10 and end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        return parsed.replace(microsecond=0)
    except Exception:
        pass

    for fmt in _date_formats(dayfirst):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt.endswith("%Y") and end_of_day:
                parsed = parsed.replace(hour=23, minute=59, second=59)
            return parsed
        except Exception:
            continue

    return None


def _polars_encoding(raw_encoding: str) -> str:
    text = str(raw_encoding or "").strip().lower()
    if text in {"utf8", "utf-8", "utf-8-sig", "utf8-sig"}:
        return "utf8"
    return "utf8-lossy"


def _scan_source(path_obj: Path, csv_options: dict[str, Any]) -> pl.LazyFrame:
    suffix = path_obj.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return pl.scan_parquet(path_obj)

    separator = str(csv_options.get("delimiter", ",") or ",")
    decimal = str(csv_options.get("decimal", ".") or ".")
    encoding = _polars_encoding(str(csv_options.get("encoding", "utf-8-sig")))

    return pl.scan_csv(
        path_obj,
        separator=separator,
        decimal_comma=(decimal == ","),
        encoding=encoding,
        infer_schema_length=5000,
        ignore_errors=True,
    )


def _normalized_lazy_frame(lazy_frame: pl.LazyFrame) -> pl.LazyFrame:
    schema = lazy_frame.collect_schema()
    rename_map = build_column_rename_map(list(schema.names()))
    if rename_map:
        return lazy_frame.rename(rename_map)
    return lazy_frame


def _row_count(lazy_frame: pl.LazyFrame) -> int:
    frame = lazy_frame.select(pl.len().alias("__count")).collect()
    return int(frame.item(0, 0)) if frame.height else 0


def _sample_frame(lazy_frame: pl.LazyFrame, size: int = 400) -> pl.DataFrame:
    return lazy_frame.head(size).collect()


def _date_expr(column: str, schema: dict[str, str], dayfirst: bool) -> pl.Expr:
    dtype_name = schema.get(column, "")
    base = pl.col(column)
    if dtype_name.startswith("Datetime"):
        return base.cast(pl.Datetime)
    if dtype_name.startswith("Date"):
        return base.cast(pl.Date).cast(pl.Datetime)

    text_expr = base.cast(pl.Utf8).str.strip_chars()
    expressions = [text_expr.str.strptime(pl.Datetime, fmt, strict=False, exact=False) for fmt in _date_formats(dayfirst)]
    return pl.coalesce(expressions)


def _infer_date_columns(sample: pl.DataFrame, schema: dict[str, str], dayfirst: bool) -> list[str]:
    out: list[str] = []
    for column, dtype_name in schema.items():
        if _is_temporal_dtype(dtype_name):
            out.append(column)
            continue
        if _is_numeric_dtype(dtype_name):
            continue
        try:
            values = sample[column].to_list()
        except Exception:
            continue
        non_empty = [value for value in values if value not in (None, "", " ")]
        if not non_empty:
            continue
        parsed = 0
        sample_values = non_empty[:200]
        for value in sample_values:
            if _parse_datetime_value(value, dayfirst) is not None:
                parsed += 1
        if sample_values and (parsed / len(sample_values)) >= 0.6:
            out.append(column)
    return out


def _infer_numeric_columns(sample: pl.DataFrame, schema: dict[str, str], date_columns: list[str]) -> list[str]:
    out: list[str] = []
    date_set = set(date_columns)
    for column, dtype_name in schema.items():
        if column in date_set:
            continue
        if _is_numeric_dtype(dtype_name):
            out.append(column)
            continue
        if _is_temporal_dtype(dtype_name):
            continue
        try:
            values = sample[column].to_list()
        except Exception:
            continue
        non_empty = [value for value in values if value not in (None, "", " ")]
        if not non_empty:
            continue
        parsed = 0
        sample_values = non_empty[:200]
        for value in sample_values:
            if _parse_number(value) is not None:
                parsed += 1
        if sample_values and (parsed / len(sample_values)) >= 0.6:
            out.append(column)
    return out


def _column_types(schema: dict[str, str], date_columns: list[str], numeric_columns: list[str]) -> dict[str, str]:
    date_set = set(date_columns)
    numeric_set = set(numeric_columns)
    out: dict[str, str] = {}
    for column, dtype_name in schema.items():
        if column in date_set:
            out[column] = "date"
        elif column in numeric_set or _is_numeric_dtype(dtype_name):
            out[column] = "number"
        else:
            out[column] = "text"
    return out


def _distinct_preview(sample: pl.DataFrame, column_types: dict[str, str], limit: int = 20) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for column in sample.columns:
        values: list[Any] = []
        seen: set[str] = set()
        for value in sample[column].to_list():
            normalized = _normalize_scalar(value)
            key = json.dumps(normalized, default=_json_default, ensure_ascii=False)
            if normalized in (None, "") or key in seen:
                continue
            seen.add(key)
            values.append(normalized)
            if len(values) > limit:
                values = []
                break
        if values and column_types.get(column) != "number":
            out[column] = values
    return out


def _safe_int(value: Any, default: int, minimum: int = 1, maximum: int = 5000) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return min(max(parsed, minimum), maximum)


def _signature(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)


@dataclass
class SessionState:
    session_id: str
    source_path: Path
    source_kind: str
    config: dict[str, Any]
    dashboard: dict[str, Any]
    csv_options: dict[str, Any]
    ui: dict[str, Any]
    lazy_frame: pl.LazyFrame
    schema: dict[str, str]
    resolver: ColumnResolver
    metadata: dict[str, Any]
    query_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    table_cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def templates(self) -> list[dict[str, Any]]:
        dashboard = self.dashboard.get("dashboard", {}) if isinstance(self.dashboard.get("dashboard"), dict) else {}
        templates = dashboard.get("templates", [])
        return templates if isinstance(templates, list) else []

    @property
    def active_template_id(self) -> str:
        dashboard = self.dashboard.get("dashboard", {}) if isinstance(self.dashboard.get("dashboard"), dict) else {}
        return str(dashboard.get("active_template_id") or "")

    def find_template(self, template_id: str | None = None) -> dict[str, Any]:
        wanted = str(template_id or self.active_template_id or "").strip()
        for template in self.templates:
            if str(template.get("id") or "").strip() == wanted:
                return template
        if self.templates:
            return self.templates[0]
        raise KeyError("No hay plantillas disponibles para la sesion.")

    def shrink_caches(self) -> None:
        if len(self.query_cache) > 16:
            oldest = next(iter(self.query_cache))
            self.query_cache.pop(oldest, None)
        if len(self.table_cache) > 32:
            oldest = next(iter(self.table_cache))
            self.table_cache.pop(oldest, None)


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionState] = {}

    def open_session(self, source_path: str, config_path: str | None = None, config_json: dict[str, Any] | None = None) -> dict[str, Any]:
        incoming: Any = None
        if config_json:
            incoming = config_json
        elif config_path:
            incoming = config_path

        base_config = load_config(incoming)
        csv_options = base_config.get("csv_options", {}) if isinstance(base_config.get("csv_options"), dict) else {}
        resolved_path = resolve_source_path(source_path, csv_options)
        if not resolved_path.exists():
            raise FileNotFoundError(f"No se encontro el archivo fuente: {resolved_path}")

        dashboard = build_dashboard_config(source_path, incoming)
        lazy_frame = _normalized_lazy_frame(_scan_source(resolved_path, csv_options))
        collected_schema = lazy_frame.collect_schema()
        normalized_columns = normalize_column_names(list(collected_schema.names()))
        schema = {name: _dtype_name(collected_schema[name]) for name in normalized_columns}
        sample = _sample_frame(lazy_frame)
        dayfirst = bool(csv_options.get("dayfirst", True))
        date_columns = _infer_date_columns(sample, schema, dayfirst)
        numeric_columns = _infer_numeric_columns(sample, schema, date_columns)
        column_types = _column_types(schema, date_columns, numeric_columns)
        row_count = _row_count(lazy_frame)

        metadata = {
            "source_name": resolved_path.name,
            "source_path": str(resolved_path),
            "source_kind": "parquet" if resolved_path.suffix.lower() in {".parquet", ".pq"} else "csv",
            "row_count": row_count,
            "all_columns": normalized_columns,
            "numeric_columns": numeric_columns,
            "date_columns": date_columns,
            "column_types": column_types,
            "distinct_values": _distinct_preview(sample, column_types),
            "runtime": dashboard.get("dashboard", {}).get("runtime", {}),
        }

        session_id = uuid.uuid4().hex
        session = SessionState(
            session_id=session_id,
            source_path=resolved_path,
            source_kind=metadata["source_kind"],
            config=base_config,
            dashboard=dashboard,
            csv_options=csv_options,
            ui=dashboard.get("ui", {}),
            lazy_frame=lazy_frame,
            schema=schema,
            resolver=ColumnResolver(metadata["all_columns"]),
            metadata=metadata,
        )

        with self._lock:
            self._sessions[session_id] = session

        return self.session_snapshot(session_id)

    def session_snapshot(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        return {
            "ok": True,
            "session_id": session.session_id,
            "ui_url": f"/app?session_id={session.session_id}",
            "ui": session.ui,
            "metadata": deepcopy(session.metadata),
            "dashboard": deepcopy(session.dashboard.get("dashboard", {})),
            "operators": [
                {"value": "eq", "label": "Igual"},
                {"value": "neq", "label": "Diferente"},
                {"value": "contains", "label": "Contiene"},
                {"value": "gt", "label": "Mayor que"},
                {"value": "gte", "label": "Mayor o igual"},
                {"value": "lt", "label": "Menor que"},
                {"value": "lte", "label": "Menor o igual"},
                {"value": "in", "label": "En lista"},
            ],
        }

    def get(self, session_id: str) -> SessionState:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError("La sesion solicitada no existe o ya fue cerrada.")
        return session

    def close(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


STORE = SessionStore()


def _normalized_date_range(session: SessionState, template: dict[str, Any], incoming: dict[str, Any] | None) -> dict[str, Any]:
    raw = incoming if isinstance(incoming, dict) else {}
    fallback = template.get("date_range", {}) if isinstance(template.get("date_range"), dict) else {}
    dashboard_runtime = session.dashboard.get("dashboard", {}).get("runtime", {})
    runtime_scope = dashboard_runtime.get("query_scope", {}) if isinstance(dashboard_runtime.get("query_scope"), dict) else {}
    resolver = session.resolver
    requested_column = resolver.resolve(raw.get("column") or fallback.get("column") or runtime_scope.get("date_column"))
    if requested_column not in session.metadata["all_columns"]:
        requested_column = session.metadata["date_columns"][0] if session.metadata["date_columns"] else ""
    start = str(raw.get("start") or fallback.get("start") or runtime_scope.get("start") or "").strip()
    end = str(raw.get("end") or fallback.get("end") or runtime_scope.get("end") or "").strip()
    enabled = bool(raw.get("enabled", fallback.get("enabled", bool(start or end))))
    return {"enabled": enabled, "column": requested_column, "start": start, "end": end}


def _resolved_filters(session: SessionState, template: dict[str, Any], filters: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    effective: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    for raw in _as_list(template.get("global_filters")) + _as_list(filters):
        if not isinstance(raw, dict):
            continue
        column = session.resolver.resolve(raw.get("column"))
        value = raw.get("value")
        if not column or column not in session.metadata["all_columns"] or value in (None, ""):
            invalid.append({"raw": raw, "reason": "Columna inexistente o valor vacio."})
            continue
        effective.append(
            {
                "column": column,
                "operator": str(raw.get("operator") or "eq").strip().lower() or "eq",
                "value": value,
            }
        )

    return effective, invalid


def _apply_filters(
    lazy_frame: pl.LazyFrame,
    session: SessionState,
    filters: list[dict[str, Any]],
    date_range: dict[str, Any],
) -> pl.LazyFrame:
    out = lazy_frame
    for item in filters:
        column = item["column"]
        operator = item["operator"]
        value = item["value"]
        column_type = session.metadata["column_types"].get(column, "text")

        if column_type == "number":
            parsed_numbers = [_parse_number(part) for part in _as_list(value)]
            parsed_numbers = [part for part in parsed_numbers if part is not None]
            if not parsed_numbers:
                continue
            expr = pl.col(column).cast(pl.Float64, strict=False)
            match operator:
                case "eq":
                    out = out.filter(expr == parsed_numbers[0])
                case "neq":
                    out = out.filter(expr != parsed_numbers[0])
                case "gt":
                    out = out.filter(expr > parsed_numbers[0])
                case "gte":
                    out = out.filter(expr >= parsed_numbers[0])
                case "lt":
                    out = out.filter(expr < parsed_numbers[0])
                case "lte":
                    out = out.filter(expr <= parsed_numbers[0])
                case "in":
                    out = out.filter(expr.is_in(parsed_numbers))
        elif column_type == "date":
            expr = _date_expr(column, session.schema, bool(session.csv_options.get("dayfirst", True)))
            parsed_start = _parse_datetime_value(value, bool(session.csv_options.get("dayfirst", True)))
            if parsed_start is None:
                continue
            match operator:
                case "eq":
                    out = out.filter(expr == pl.lit(parsed_start))
                case "neq":
                    out = out.filter(expr != pl.lit(parsed_start))
                case "gt":
                    out = out.filter(expr > pl.lit(parsed_start))
                case "gte":
                    out = out.filter(expr >= pl.lit(parsed_start))
                case "lt":
                    out = out.filter(expr < pl.lit(parsed_start))
                case "lte":
                    out = out.filter(expr <= pl.lit(parsed_start))
        else:
            expr = pl.col(column).cast(pl.Utf8).fill_null("").str.strip_chars().str.to_lowercase()
            text_values = [str(part).strip().lower() for part in _as_list(value) if str(part).strip()]
            if not text_values:
                continue
            match operator:
                case "eq":
                    out = out.filter(expr == text_values[0])
                case "neq":
                    out = out.filter(expr != text_values[0])
                case "contains":
                    out = out.filter(expr.str.contains(text_values[0], literal=True))
                case "in":
                    out = out.filter(expr.is_in(text_values))
                case _:
                    out = out.filter(expr == text_values[0])

    if date_range.get("enabled") and date_range.get("column"):
        date_column = str(date_range.get("column") or "").strip()
        if date_column in session.metadata["all_columns"]:
            dayfirst = bool(session.csv_options.get("dayfirst", True))
            start = _parse_datetime_value(date_range.get("start"), dayfirst, end_of_day=False)
            end = _parse_datetime_value(date_range.get("end"), dayfirst, end_of_day=True)
            expr = _date_expr(date_column, session.schema, dayfirst)
            if start is not None:
                out = out.filter(expr >= pl.lit(start))
            if end is not None:
                out = out.filter(expr <= pl.lit(end))

    return out


def _aggregate_expr(aggregation: str, target_column: str | None = None) -> pl.Expr:
    agg = str(aggregation or "sum").strip().lower()
    if agg == "count":
        return pl.len()

    if not target_column:
        raise ValueError("La agregacion requiere una columna numerica objetivo.")

    expr = pl.col(target_column).cast(pl.Float64, strict=False)
    if agg == "sum":
        return expr.sum()
    if agg == "avg":
        return expr.mean()
    if agg == "min":
        return expr.min()
    if agg == "max":
        return expr.max()
    raise ValueError(f"Agregacion no soportada: {aggregation}")


def _widget_error(widget: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "id": widget.get("id", ""),
        "cell_id": widget.get("cell_id", ""),
        "type": widget.get("type", ""),
        "title": widget.get("title", ""),
        "valid": False,
        "error": message,
    }


def _render_kpi(session: SessionState, lazy_frame: pl.LazyFrame, widget: dict[str, Any]) -> dict[str, Any]:
    column = session.resolver.resolve(widget.get("column"))
    if column not in session.metadata["all_columns"]:
        return _widget_error(widget, f"La columna '{widget.get('column')}' no existe en la fuente.")

    aggregation = str(widget.get("aggregation") or "sum").strip().lower()
    if aggregation != "count" and session.metadata["column_types"].get(column) != "number":
        return _widget_error(widget, f"La columna '{column}' no es numerica para KPI {aggregation}.")

    frame = lazy_frame.select(_aggregate_expr(aggregation, column).alias("value")).collect()
    value = _normalize_scalar(frame.item(0, 0)) if frame.height else None
    return {
        "id": widget.get("id", ""),
        "cell_id": widget.get("cell_id", ""),
        "type": "kpi",
        "title": widget.get("title", ""),
        "valid": True,
        "format": widget.get("format", "number"),
        "accent_color": widget.get("accent_color", "#1d4ed8"),
        "data": {"value": value, "aggregation": aggregation, "column": column},
    }


def _render_chart(session: SessionState, lazy_frame: pl.LazyFrame, widget: dict[str, Any]) -> dict[str, Any]:
    x_column = session.resolver.resolve(widget.get("x_column"))
    y_column = session.resolver.resolve(widget.get("y_column"))
    date_column = session.resolver.resolve(widget.get("date_column"))
    mode = str(widget.get("analysis_mode") or "categorias").strip().lower()
    chart_type = str(widget.get("chart_type") or "bar").strip() or "bar"
    aggregation = str(widget.get("aggregation") or ("count" if mode == "scatter" else "sum")).strip().lower()
    top_n = _safe_int(widget.get("top_n"), 12, 1, 200)
    point_limit = _safe_int(widget.get("point_limit"), 150, 1, 2000)

    if mode == "scatter":
        if x_column not in session.metadata["all_columns"] or y_column not in session.metadata["all_columns"]:
            return _widget_error(widget, "La grafica scatter requiere dos columnas validas.")
        if session.metadata["column_types"].get(x_column) != "number" or session.metadata["column_types"].get(y_column) != "number":
            return _widget_error(widget, "La grafica scatter requiere columnas numericas.")

        points = (
            lazy_frame
            .select(
                pl.col(x_column).cast(pl.Float64, strict=False).alias("x"),
                pl.col(y_column).cast(pl.Float64, strict=False).alias("y"),
            )
            .drop_nulls()
            .limit(point_limit)
            .collect()
            .to_dicts()
        )
        data_points = [{"x": _normalize_scalar(item.get("x")), "y": _normalize_scalar(item.get("y"))} for item in points]
        return {
            "id": widget.get("id", ""),
            "cell_id": widget.get("cell_id", ""),
            "type": "chart",
            "title": widget.get("title", ""),
            "valid": True,
            "data": {
                "mode": mode,
                "chart_type": "scatter",
                "labels": [],
                "datasets": [{"label": widget.get("title", ""), "data": data_points}],
                "meta": {"x_column": x_column, "y_column": y_column},
            },
        }

    if mode == "tendencia":
        if date_column not in session.metadata["all_columns"]:
            return _widget_error(widget, "La grafica de tendencia requiere una columna de fecha valida.")
        dayfirst = bool(session.csv_options.get("dayfirst", True))
        date_expr = _date_expr(date_column, session.schema, dayfirst).alias("__trend_date")
        granularity = str(widget.get("date_granularity") or "day").strip().lower()
        if granularity == "year":
            bucket_expr = pl.col("__trend_date").dt.strftime("%Y")
        elif granularity == "month":
            bucket_expr = pl.col("__trend_date").dt.strftime("%Y-%m")
        else:
            bucket_expr = pl.col("__trend_date").dt.strftime("%Y-%m-%d")

        if aggregation != "count" and y_column not in session.metadata["all_columns"]:
            return _widget_error(widget, f"La grafica requiere una columna numerica valida: {widget.get('y_column')}.")
        if aggregation != "count" and session.metadata["column_types"].get(y_column) != "number":
            return _widget_error(widget, f"La columna '{y_column}' no es numerica para agregacion {aggregation}.")

        data_frame = (
            lazy_frame
            .with_columns(date_expr)
            .drop_nulls(["__trend_date"])
            .group_by(bucket_expr.alias("label"))
            .agg(_aggregate_expr(aggregation, y_column if aggregation != "count" else date_column).alias("value"))
            .sort("label")
            .collect()
        )
        labels = data_frame["label"].to_list() if "label" in data_frame.columns else []
        values = [_normalize_scalar(item) for item in (data_frame["value"].to_list() if "value" in data_frame.columns else [])]
        return {
            "id": widget.get("id", ""),
            "cell_id": widget.get("cell_id", ""),
            "type": "chart",
            "title": widget.get("title", ""),
            "valid": True,
            "data": {
                "mode": mode,
                "chart_type": chart_type,
                "labels": labels,
                "datasets": [{"label": widget.get("title", ""), "data": values}],
                "meta": {"date_column": date_column, "granularity": granularity, "aggregation": aggregation},
            },
        }

    if x_column not in session.metadata["all_columns"]:
        return _widget_error(widget, f"La columna '{widget.get('x_column')}' no existe en la fuente.")
    if aggregation != "count":
        if y_column not in session.metadata["all_columns"]:
            return _widget_error(widget, f"La columna '{widget.get('y_column')}' no existe en la fuente.")
        if session.metadata["column_types"].get(y_column) != "number":
            return _widget_error(widget, f"La columna '{y_column}' no es numerica para agregacion {aggregation}.")

    grouped = (
        lazy_frame
        .with_columns(pl.col(x_column).cast(pl.Utf8).fill_null("(Sin valor)").str.strip_chars().alias("__label"))
        .group_by("__label")
        .agg(_aggregate_expr(aggregation, y_column if aggregation != "count" else x_column).alias("value"))
        .sort("value", descending=True, nulls_last=True)
        .head(top_n)
        .collect()
    )
    labels = grouped["__label"].to_list() if "__label" in grouped.columns else []
    values = [_normalize_scalar(item) for item in (grouped["value"].to_list() if "value" in grouped.columns else [])]
    return {
        "id": widget.get("id", ""),
        "cell_id": widget.get("cell_id", ""),
        "type": "chart",
        "title": widget.get("title", ""),
        "valid": True,
        "data": {
            "mode": mode,
            "chart_type": chart_type,
            "labels": labels,
            "datasets": [{"label": widget.get("title", ""), "data": values}],
            "meta": {"x_column": x_column, "y_column": y_column, "aggregation": aggregation, "top_n": top_n},
        },
    }


def _sort_frame(lazy_frame: pl.LazyFrame, session: SessionState, column: str, direction: str) -> pl.LazyFrame:
    if column not in session.metadata["all_columns"]:
        return lazy_frame
    descending = str(direction or "desc").strip().lower() != "asc"
    if session.metadata["column_types"].get(column) == "number":
        return lazy_frame.with_columns(pl.col(column).cast(pl.Float64, strict=False).alias("__sort_number")).sort(
            "__sort_number", descending=descending, nulls_last=True
        ).drop("__sort_number")
    if session.metadata["column_types"].get(column) == "date":
        dayfirst = bool(session.csv_options.get("dayfirst", True))
        return lazy_frame.with_columns(_date_expr(column, session.schema, dayfirst).alias("__sort_date")).sort(
            "__sort_date", descending=descending, nulls_last=True
        ).drop("__sort_date")
    return lazy_frame.with_columns(pl.col(column).cast(pl.Utf8).fill_null("").str.strip_chars().alias("__sort_text")).sort(
        "__sort_text", descending=descending, nulls_last=True
    ).drop("__sort_text")


def _table_page(
    session: SessionState,
    lazy_frame: pl.LazyFrame,
    widget: dict[str, Any],
    page: int,
    page_size: int | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
) -> dict[str, Any]:
    columns = session.resolver.resolve_many(widget.get("columns") or [])
    columns = [column for column in columns if column in session.metadata["all_columns"]]
    if not columns:
        columns = session.metadata["all_columns"][: min(8, len(session.metadata["all_columns"]))]

    limit = _safe_int(page_size or widget.get("limit"), _safe_int(widget.get("limit"), 100, 1, 1000), 1, 1000)
    page_index = _safe_int(page, 1, 1, 999999)
    order_column = session.resolver.resolve(sort_by or widget.get("sort_by"))
    order_dir = str(sort_dir or widget.get("sort_dir") or "desc").strip().lower() or "desc"
    working = _sort_frame(lazy_frame, session, order_column, order_dir) if order_column else lazy_frame
    total_rows = _row_count(working)
    offset = (page_index - 1) * limit

    rows = working.select([pl.col(column) for column in columns]).slice(offset, limit).collect().to_dicts()
    rows = [{key: _normalize_scalar(value) for key, value in row.items()} for row in rows]
    total_pages = max(1, math.ceil(total_rows / limit)) if total_rows else 1

    return {
        "id": widget.get("id", ""),
        "cell_id": widget.get("cell_id", ""),
        "type": "table",
        "title": widget.get("title", ""),
        "valid": True,
        "data": {
            "columns": columns,
            "rows": rows,
            "page": page_index,
            "page_size": limit,
            "sort_by": order_column,
            "sort_dir": order_dir,
            "total_rows": total_rows,
            "total_pages": total_pages,
        },
    }


def _render_widget(session: SessionState, lazy_frame: pl.LazyFrame, widget: dict[str, Any]) -> dict[str, Any]:
    widget_type = str(widget.get("type") or "").strip().lower()
    if widget_type == "kpi":
        return _render_kpi(session, lazy_frame, widget)
    if widget_type == "chart":
        return _render_chart(session, lazy_frame, widget)
    if widget_type == "table":
        return _table_page(session, lazy_frame, widget, 1)
    return _widget_error(widget, f"Tipo de widget no soportado: {widget_type}")


def query_dashboard(
    session_id: str,
    template_id: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    date_range: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = STORE.get(session_id)
    template = session.find_template(template_id)
    effective_date_range = _normalized_date_range(session, template, date_range)
    effective_filters, invalid_filters = _resolved_filters(session, template, filters)

    cache_key = _signature(
        {
            "template_id": template.get("id"),
            "filters": effective_filters,
            "date_range": effective_date_range,
        }
    )
    if cache_key in session.query_cache:
        return deepcopy(session.query_cache[cache_key])

    filtered = _apply_filters(session.lazy_frame, session, effective_filters, effective_date_range)
    selected_row_count = _row_count(filtered)
    widgets = [_render_widget(session, filtered, widget) for widget in template.get("widgets", []) if isinstance(widget, dict)]

    response = {
        "ok": True,
        "session_id": session.session_id,
        "template_id": template.get("id", ""),
        "template": deepcopy(template),
        "filters": effective_filters,
        "invalid_filters": invalid_filters,
        "date_range": effective_date_range,
        "summary": {
            "selected_row_count": selected_row_count,
            "total_row_count": session.metadata["row_count"],
            "source_name": session.metadata["source_name"],
        },
        "widgets": widgets,
    }
    session.query_cache[cache_key] = deepcopy(response)
    session.shrink_caches()
    return response


def query_table_page(
    session_id: str,
    widget_id: str,
    template_id: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    date_range: dict[str, Any] | None = None,
    page: int = 1,
    page_size: int | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
) -> dict[str, Any]:
    session = STORE.get(session_id)
    template = session.find_template(template_id)
    widget = next((item for item in template.get("widgets", []) if isinstance(item, dict) and str(item.get("id") or "") == str(widget_id)), None)
    if widget is None:
        raise KeyError(f"No se encontro el widget de tabla '{widget_id}'.")

    effective_date_range = _normalized_date_range(session, template, date_range)
    effective_filters, invalid_filters = _resolved_filters(session, template, filters)
    filtered = _apply_filters(session.lazy_frame, session, effective_filters, effective_date_range)

    cache_key = _signature(
        {
            "widget_id": widget_id,
            "template_id": template.get("id"),
            "filters": effective_filters,
            "date_range": effective_date_range,
            "page": page,
            "page_size": page_size,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        }
    )
    if cache_key in session.table_cache:
        return deepcopy(session.table_cache[cache_key])

    table_payload = _table_page(session, filtered, widget, page, page_size=page_size, sort_by=sort_by, sort_dir=sort_dir)
    response = {
        "ok": True,
        "session_id": session.session_id,
        "widget_id": widget_id,
        "template_id": template.get("id", ""),
        "filters": effective_filters,
        "invalid_filters": invalid_filters,
        "date_range": effective_date_range,
        "table": table_payload,
    }
    session.table_cache[cache_key] = deepcopy(response)
    session.shrink_caches()
    return response


def filter_values(
    session_id: str,
    column: str,
    search: str = "",
    limit: int = 30,
    filters: list[dict[str, Any]] | None = None,
    date_range: dict[str, Any] | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    session = STORE.get(session_id)
    template = session.find_template(template_id)
    resolved_column = session.resolver.resolve(column)
    if resolved_column not in session.metadata["all_columns"]:
        raise KeyError(f"La columna '{column}' no existe en la sesion.")

    effective_date_range = _normalized_date_range(session, template, date_range)
    effective_filters, _ = _resolved_filters(session, template, filters)
    filtered = _apply_filters(session.lazy_frame, session, effective_filters, effective_date_range)

    text_search = str(search or "").strip().lower()
    working = filtered.select(pl.col(resolved_column).cast(pl.Utf8).fill_null("").str.strip_chars().alias("value"))
    if text_search:
        working = working.filter(pl.col("value").str.to_lowercase().str.contains(text_search, literal=True))

    data = working.filter(pl.col("value") != "").unique().sort("value").limit(_safe_int(limit, 30, 1, 100)).collect()
    values = [str(value) for value in data["value"].to_list()] if "value" in data.columns else []
    return {"ok": True, "column": resolved_column, "values": values}
