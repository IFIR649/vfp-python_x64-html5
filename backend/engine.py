from __future__ import annotations

import hashlib
import json
import math
import tempfile
import threading
import time as perf_time
import uuid
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, time as clock_time
from pathlib import Path
from typing import Any

import duckdb
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
SOURCE_CACHE_VERSION = 1
DISTINCT_PREVIEW_ROWS = 400


def _dtype_name(dtype: Any) -> str:
    return str(dtype)


def _is_numeric_dtype(dtype: Any) -> bool:
    text = _dtype_name(dtype)
    return text.startswith(("Int", "UInt", "Float", "Decimal"))


def _is_temporal_dtype(dtype: Any) -> bool:
    text = _dtype_name(dtype)
    return text.startswith("Date") or text.startswith("Datetime") or text == "Time"


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, clock_time)):
        return value.isoformat(sep=" ")
    return str(value)


def _normalize_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date, clock_time)):
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
    if isinstance(value, (datetime, date, clock_time)):
        return value.isoformat(sep=" ")
    return value


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _safe_int(value: Any, default: int, minimum: int = 1, maximum: int = 5000) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return min(max(parsed, minimum), maximum)


def _signature(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)


def _slug(value: object, prefix: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return prefix
    chunks: list[str] = []
    current: list[str] = []
    for char in text:
        if char.isalnum():
            current.append(char)
        elif current:
            chunks.append("".join(current))
            current = []
    if current:
        chunks.append("".join(current))
    out = "-".join(chunks)
    return out or prefix


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
            return datetime.combine(value, clock_time(23, 59, 59))
        return datetime.combine(value, clock_time(0, 0, 0))

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


def _normalized_source_lazy(
    lazy_frame: pl.LazyFrame,
) -> tuple[pl.LazyFrame, dict[str, str], list[str], dict[str, str]]:
    schema = lazy_frame.collect_schema()
    raw_names = list(schema.names())
    rename_map = build_column_rename_map(raw_names)
    normalized_lazy = lazy_frame.rename(rename_map) if rename_map else lazy_frame
    normalized_names = normalize_column_names(raw_names)
    normalized_schema = {
        normalized_name: _dtype_name(schema[raw_name])
        for raw_name, normalized_name in zip(raw_names, normalized_names)
    }
    return normalized_lazy, rename_map, normalized_names, normalized_schema


def _row_count(lazy_frame: pl.LazyFrame) -> int:
    frame = lazy_frame.select(pl.len().alias("__count")).collect()
    return int(frame.item(0, 0)) if frame.height else 0


def _sample_frame(lazy_frame: pl.LazyFrame, size: int = DISTINCT_PREVIEW_ROWS) -> pl.DataFrame:
    return lazy_frame.head(size).collect()


def _date_expr(column: str, schema: dict[str, str], dayfirst: bool) -> pl.Expr:
    dtype_name = schema.get(column, "")
    base = pl.col(column)
    if dtype_name.startswith("Datetime"):
        return base.cast(pl.Datetime, strict=False)
    if dtype_name.startswith("Date"):
        return base.cast(pl.Date, strict=False).cast(pl.Datetime, strict=False)

    text_expr = base.cast(pl.Utf8).str.strip_chars()
    expressions = [text_expr.str.strptime(pl.Datetime, fmt, strict=False, exact=False) for fmt in _date_formats(dayfirst)]
    return pl.coalesce(expressions)


def _number_expr(column: str, schema: dict[str, str], decimal: str = ".") -> pl.Expr:
    dtype_name = schema.get(column, "")
    base = pl.col(column)
    if _is_numeric_dtype(dtype_name):
        return base.cast(pl.Float64, strict=False)
    # Vectorized path: strip whitespace/nbsp, handle accounting notation (123) → -123
    cleaned = (
        base.cast(pl.Utf8)
        .str.strip_chars()
        .str.replace_all(r"[\s\u00a0]", "")
        .str.replace(r"^\((.+)\)$", "-$1")
    )
    if decimal == ",":
        # Spanish locale: thousands='.', decimal=','  →  remove '.', replace ',' with '.'
        return (
            cleaned
            .str.replace_all(r"\.", "")
            .str.replace(",", ".", literal=True)
            .cast(pl.Float64, strict=False)
        )
    # English locale: thousands=',', decimal='.'  →  remove ','
    return (
        cleaned
        .str.replace_all(",", "")
        .cast(pl.Float64, strict=False)
    )


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


def _text_expr(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.Utf8).fill_null("").str.strip_chars().str.to_lowercase()


def _ordered_columns(reference: list[str], values: set[str]) -> list[str]:
    return [column for column in reference if column in values]


def _optimizer_raw(config: dict[str, Any]) -> dict[str, Any]:
    dashboard = config.get("dashboard", {}) if isinstance(config.get("dashboard"), dict) else {}
    runtime = dashboard.get("runtime", {}) if isinstance(dashboard.get("runtime"), dict) else {}
    optimizer = runtime.get("optimizer", {}) if isinstance(runtime.get("optimizer"), dict) else {}
    return optimizer


def _expand_optimizer_dir(raw_dir: object) -> Path:
    text = str(raw_dir or "<temp>/vfp_dashboard_engine").strip()
    text = text.replace("<temp>", str(Path(tempfile.gettempdir()).resolve()))
    path_obj = Path(text)
    if not path_obj.is_absolute():
        path_obj = (PROJECT_ROOT / path_obj).resolve()
    return path_obj


@dataclass(frozen=True)
class OptimizerConfig:
    enabled: bool
    source_cache_dir: Path
    source_cache_format: str
    session_filtered_entries: int
    session_distinct_entries: int
    session_sorted_variants: int
    suggestion_debounce_ms: int


def _optimizer_config(config: dict[str, Any]) -> OptimizerConfig:
    raw = _optimizer_raw(config)
    return OptimizerConfig(
        enabled=bool(raw.get("enabled", True)),
        source_cache_dir=_expand_optimizer_dir(raw.get("source_cache_dir")),
        source_cache_format=str(raw.get("source_cache_format") or "parquet").strip().lower() or "parquet",
        session_filtered_entries=_safe_int(raw.get("session_filtered_entries"), 8, 1, 64),
        session_distinct_entries=_safe_int(raw.get("session_distinct_entries"), 64, 1, 512),
        session_sorted_variants=_safe_int(raw.get("session_sorted_variants"), 2, 1, 12),
        suggestion_debounce_ms=_safe_int(raw.get("suggestion_debounce_ms"), 300, 50, 2000),
    )


def _source_signature(source_path: Path, csv_options: dict[str, Any]) -> str:
    stat = source_path.stat()
    payload = {
        "source_path": str(source_path.resolve()),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "csv_options": csv_options,
        "cache_version": SOURCE_CACHE_VERSION,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _helper_column_name(kind: str, index: int, column: str) -> str:
    return f"__opt_{kind}_{index:03d}_{_slug(column, kind)}"


def _hinted_source_columns(dashboard_config: dict[str, Any]) -> tuple[set[str], set[str]]:
    date_hints: set[str] = set()
    numeric_hints: set[str] = set()
    dashboard = dashboard_config.get("dashboard", {}) if isinstance(dashboard_config.get("dashboard"), dict) else {}
    runtime = dashboard.get("runtime", {}) if isinstance(dashboard.get("runtime"), dict) else {}
    query_scope = runtime.get("query_scope", {}) if isinstance(runtime.get("query_scope"), dict) else {}
    defaults = dashboard.get("defaults", {}) if isinstance(dashboard.get("defaults"), dict) else {}
    templates = dashboard.get("templates", []) if isinstance(dashboard.get("templates"), list) else []

    for value in (query_scope.get("date_column"), defaults.get("date_column")):
        if str(value or "").strip():
            date_hints.add(str(value).strip())

    for template in templates:
        if not isinstance(template, dict):
            continue
        date_range = template.get("date_range", {}) if isinstance(template.get("date_range"), dict) else {}
        if str(date_range.get("column") or "").strip():
            date_hints.add(str(date_range.get("column")).strip())
        for widget in template.get("widgets", []):
            if not isinstance(widget, dict):
                continue
            widget_type = str(widget.get("type") or "").strip().lower()
            analysis_mode = str(widget.get("analysis_mode") or "").strip().lower()
            aggregation = str(widget.get("aggregation") or "sum").strip().lower()
            date_column = str(widget.get("date_column") or "").strip()
            x_column = str(widget.get("x_column") or "").strip()
            y_column = str(widget.get("y_column") or "").strip()
            column = str(widget.get("column") or "").strip()

            if date_column:
                date_hints.add(date_column)
            if analysis_mode == "tendencia" and x_column:
                date_hints.add(date_column or x_column)
            if widget_type == "kpi" and aggregation != "count" and column:
                numeric_hints.add(column)
            if widget_type == "chart":
                if analysis_mode == "scatter":
                    if x_column:
                        numeric_hints.add(x_column)
                    if y_column:
                        numeric_hints.add(y_column)
                elif aggregation != "count" and y_column:
                    numeric_hints.add(y_column)

    return date_hints, numeric_hints


def _build_helper_columns(
    all_columns: list[str],
    schema: dict[str, str],
    date_columns: list[str],
    numeric_columns: list[str],
) -> dict[str, dict[str, str]]:
    date_set = set(date_columns)
    numeric_set = set(numeric_columns)
    helpers: dict[str, dict[str, str]] = {"date": {}, "number": {}}
    for index, column in enumerate(all_columns, start=1):
        dtype_name = schema.get(column, "")
        if column in date_set and not _is_temporal_dtype(dtype_name):
            helpers["date"][column] = _helper_column_name("date", index, column)
        if column in numeric_set and not _is_numeric_dtype(dtype_name):
            helpers["number"][column] = _helper_column_name("num", index, column)
    return helpers


def _build_accelerated_lazy(
    normalized_lazy: pl.LazyFrame,
    schema: dict[str, str],
    helper_columns: dict[str, dict[str, str]],
    dayfirst: bool,
    decimal: str = ".",
) -> pl.LazyFrame:
    expressions: list[pl.Expr] = []
    for column, helper in helper_columns.get("date", {}).items():
        expressions.append(_date_expr(column, schema, dayfirst).alias(helper))
    for column, helper in helper_columns.get("number", {}).items():
        expressions.append(_number_expr(column, schema, decimal).alias(helper))
    if not expressions:
        return normalized_lazy
    return normalized_lazy.with_columns(expressions)


def _write_parquet(lazy_frame: pl.LazyFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(lazy_frame, "sink_parquet"):
        try:
            lazy_frame.sink_parquet(output_path)
            return
        except Exception:
            pass
    frame = lazy_frame.collect(streaming=True)
    frame.write_parquet(output_path)


@dataclass(frozen=True)
class AcceleratedSource:
    source_path: Path
    source_kind: str
    source_signature: str
    accelerated_path: Path
    accelerated_kind: str
    cache_dir: Path
    cache_hit: bool
    all_columns: list[str]
    original_schema: dict[str, str]
    date_columns: list[str]
    numeric_columns: list[str]
    column_types: dict[str, str]
    helper_columns: dict[str, dict[str, str]]
    row_count: int
    lazy_frame: pl.LazyFrame


def _manifest_path(cache_dir: Path) -> Path:
    return cache_dir / "manifest.json"


def _parquet_cache_path(cache_dir: Path) -> Path:
    return cache_dir / "source.parquet"


def _read_manifest(path_obj: Path) -> dict[str, Any] | None:
    if not path_obj.exists():
        return None
    try:
        return json.loads(path_obj.read_text(encoding="utf-8"))
    except Exception:
        return None


def _manifest_supports_hints(manifest: dict[str, Any], date_hints: set[str], numeric_hints: set[str]) -> bool:
    schema = manifest.get("original_schema", {}) if isinstance(manifest.get("original_schema"), dict) else {}
    helper_columns = manifest.get("helper_columns", {}) if isinstance(manifest.get("helper_columns"), dict) else {}
    date_helpers = helper_columns.get("date", {}) if isinstance(helper_columns.get("date"), dict) else {}
    number_helpers = helper_columns.get("number", {}) if isinstance(helper_columns.get("number"), dict) else {}

    for column in date_hints:
        dtype_name = str(schema.get(column) or "")
        if column not in schema:
            continue
        if _is_temporal_dtype(dtype_name):
            continue
        if column not in date_helpers:
            return False

    for column in numeric_hints:
        dtype_name = str(schema.get(column) or "")
        if column not in schema:
            continue
        if _is_numeric_dtype(dtype_name):
            continue
        if column not in number_helpers:
            return False

    return True


def _accelerated_from_manifest(
    manifest: dict[str, Any],
    source_path: Path,
    source_signature: str,
    cache_dir: Path,
) -> AcceleratedSource:
    accelerated_path = Path(str(manifest.get("accelerated_path") or source_path))
    lazy_frame = pl.scan_parquet(accelerated_path)
    return AcceleratedSource(
        source_path=source_path,
        source_kind=str(manifest.get("source_kind") or source_path.suffix.lower().lstrip(".")),
        source_signature=source_signature,
        accelerated_path=accelerated_path,
        accelerated_kind=str(manifest.get("accelerated_kind") or "parquet_source"),
        cache_dir=cache_dir,
        cache_hit=True,
        all_columns=[str(value) for value in manifest.get("all_columns", [])],
        original_schema={str(k): str(v) for k, v in (manifest.get("original_schema", {}) or {}).items()},
        date_columns=[str(value) for value in manifest.get("date_columns", [])],
        numeric_columns=[str(value) for value in manifest.get("numeric_columns", [])],
        column_types={str(k): str(v) for k, v in (manifest.get("column_types", {}) or {}).items()},
        helper_columns={
            "date": {str(k): str(v) for k, v in ((manifest.get("helper_columns", {}) or {}).get("date", {}) or {}).items()},
            "number": {str(k): str(v) for k, v in ((manifest.get("helper_columns", {}) or {}).get("number", {}) or {}).items()},
        },
        row_count=int(manifest.get("row_count") or 0),
        lazy_frame=lazy_frame,
    )


def _build_transient_source(
    source_path: Path,
    csv_options: dict[str, Any],
    dashboard_config: dict[str, Any],
) -> AcceleratedSource:
    source_kind = "parquet" if source_path.suffix.lower() in {".parquet", ".pq"} else "csv"
    source_signature = _source_signature(source_path, csv_options)
    normalized_lazy, _, all_columns, schema = _normalized_source_lazy(_scan_source(source_path, csv_options))
    sample = _sample_frame(normalized_lazy)
    dayfirst = bool(csv_options.get("dayfirst", True))
    decimal = str(csv_options.get("decimal", ".")).strip() or "."
    inferred_date = set(_infer_date_columns(sample, schema, dayfirst))
    inferred_numeric = set(_infer_numeric_columns(sample, schema, list(inferred_date)))
    hinted_date, hinted_numeric = _hinted_source_columns(dashboard_config)
    inferred_date.update(column for column in hinted_date if column in schema)
    inferred_numeric.update(column for column in hinted_numeric if column in schema)
    date_columns = _ordered_columns(all_columns, inferred_date)
    numeric_columns = _ordered_columns(all_columns, inferred_numeric)
    column_types = _column_types(schema, date_columns, numeric_columns)
    helper_columns = _build_helper_columns(all_columns, schema, date_columns, numeric_columns)
    lazy_frame = _build_accelerated_lazy(normalized_lazy, schema, helper_columns, dayfirst, decimal)
    row_count = _row_count(normalized_lazy)
    return AcceleratedSource(
        source_path=source_path,
        source_kind=source_kind,
        source_signature=source_signature,
        accelerated_path=source_path,
        accelerated_kind="runtime_lazy",
        cache_dir=Path(),
        cache_hit=False,
        all_columns=all_columns,
        original_schema=schema,
        date_columns=date_columns,
        numeric_columns=numeric_columns,
        column_types=column_types,
        helper_columns=helper_columns,
        row_count=row_count,
        lazy_frame=lazy_frame,
    )


def _open_accelerated_source(
    source_path: Path,
    csv_options: dict[str, Any],
    optimizer: OptimizerConfig,
    dashboard_config: dict[str, Any],
) -> AcceleratedSource:
    if not optimizer.enabled:
        return _build_transient_source(source_path, csv_options, dashboard_config)

    source_signature = _source_signature(source_path, csv_options)
    cache_dir = optimizer.source_cache_dir / source_signature
    manifest_path = _manifest_path(cache_dir)
    manifest = _read_manifest(manifest_path)
    hinted_date, hinted_numeric = _hinted_source_columns(dashboard_config)

    if manifest:
        cached_signature = str(manifest.get("source_signature") or "")
        accelerated_path = Path(str(manifest.get("accelerated_path") or source_path))
        if (
            int(manifest.get("cache_version") or 0) == SOURCE_CACHE_VERSION
            and cached_signature == source_signature
            and accelerated_path.exists()
            and _manifest_supports_hints(manifest, hinted_date, hinted_numeric)
        ):
            return _accelerated_from_manifest(manifest, source_path, source_signature, cache_dir)

    cache_dir.mkdir(parents=True, exist_ok=True)
    source_kind = "parquet" if source_path.suffix.lower() in {".parquet", ".pq"} else "csv"
    normalized_lazy, rename_map, all_columns, schema = _normalized_source_lazy(_scan_source(source_path, csv_options))
    sample = _sample_frame(normalized_lazy)
    dayfirst = bool(csv_options.get("dayfirst", True))
    decimal = str(csv_options.get("decimal", ".")).strip() or "."

    inferred_date = set(_infer_date_columns(sample, schema, dayfirst))
    inferred_numeric = set(_infer_numeric_columns(sample, schema, list(inferred_date)))
    inferred_date.update(column for column in hinted_date if column in schema)
    inferred_numeric.update(column for column in hinted_numeric if column in schema)
    date_columns = _ordered_columns(all_columns, inferred_date)
    numeric_columns = _ordered_columns(all_columns, inferred_numeric)
    column_types = _column_types(schema, date_columns, numeric_columns)
    helper_columns = _build_helper_columns(all_columns, schema, date_columns, numeric_columns)

    accelerated_path = _parquet_cache_path(cache_dir)
    accelerated_kind = "parquet_source"

    needs_cached_parquet = source_kind == "csv" or bool(rename_map) or bool(helper_columns["date"]) or bool(helper_columns["number"])
    if needs_cached_parquet:
        accelerated_kind = "parquet_cache"
        accelerated_lazy = _build_accelerated_lazy(normalized_lazy, schema, helper_columns, dayfirst, decimal)
        _write_parquet(accelerated_lazy, accelerated_path)
        lazy_frame = pl.scan_parquet(accelerated_path)
    else:
        accelerated_path = source_path
        lazy_frame = pl.scan_parquet(source_path)

    row_count = _row_count(normalized_lazy)
    manifest_payload = {
        "cache_version": SOURCE_CACHE_VERSION,
        "source_path": str(source_path.resolve()),
        "source_kind": source_kind,
        "source_signature": source_signature,
        "accelerated_path": str(accelerated_path.resolve()),
        "accelerated_kind": accelerated_kind,
        "all_columns": all_columns,
        "original_schema": schema,
        "date_columns": date_columns,
        "numeric_columns": numeric_columns,
        "column_types": column_types,
        "helper_columns": helper_columns,
        "row_count": row_count,
    }
    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return AcceleratedSource(
        source_path=source_path,
        source_kind=source_kind,
        source_signature=source_signature,
        accelerated_path=accelerated_path,
        accelerated_kind=accelerated_kind,
        cache_dir=cache_dir,
        cache_hit=False,
        all_columns=all_columns,
        original_schema=schema,
        date_columns=date_columns,
        numeric_columns=numeric_columns,
        column_types=column_types,
        helper_columns=helper_columns,
        row_count=row_count,
        lazy_frame=lazy_frame,
    )


@dataclass
class SessionState:
    session_id: str
    source_path: Path
    source_kind: str
    config: dict[str, Any]
    dashboard: dict[str, Any]
    csv_options: dict[str, Any]
    ui: dict[str, Any]
    accelerated: AcceleratedSource
    resolver: ColumnResolver
    metadata: dict[str, Any]
    optimizer: OptimizerConfig
    conn: duckdb.DuckDBPyConnection
    _temp_parquet: Path | None = None
    open_timing: dict[str, Any] = field(default_factory=dict)
    query_cache: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict)
    table_cache: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict)
    distinct_cache: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict)

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

    def _shrink_ordered(self, store: OrderedDict[str, Any], maximum: int) -> None:
        while len(store) > maximum:
            store.popitem(last=False)

    def shrink_caches(self) -> None:
        self._shrink_ordered(self.query_cache, 16)
        self._shrink_ordered(self.table_cache, 32)
        self._shrink_ordered(self.distinct_cache, self.optimizer.session_distinct_entries)


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionState] = {}

    def open_session(self, source_path: str, config_path: str | None = None, config_json: dict[str, Any] | None = None) -> dict[str, Any]:
        t0 = perf_time.perf_counter()
        incoming: Any = None
        if config_json:
            incoming = config_json
        elif config_path:
            incoming = config_path

        base_config = load_config(incoming)
        optimizer = _optimizer_config(base_config)
        csv_options = base_config.get("csv_options", {}) if isinstance(base_config.get("csv_options"), dict) else {}
        resolved_path = resolve_source_path(source_path, csv_options)
        if not resolved_path.exists():
            raise FileNotFoundError(f"No se encontro el archivo fuente: {resolved_path}")

        dashboard = build_dashboard_config(source_path, incoming)
        t_config = perf_time.perf_counter()

        accelerated = _open_accelerated_source(resolved_path, csv_options, optimizer, dashboard)
        t_acc = perf_time.perf_counter()

        duck_conn, temp_parquet = _create_duck_session(accelerated)
        t_duck = perf_time.perf_counter()

        preview_columns = accelerated.all_columns[: min(8, len(accelerated.all_columns))]
        if preview_columns:
            cols_sql = ", ".join(f'"{c}"' for c in preview_columns)
            result = duck_conn.execute(f"SELECT {cols_sql} FROM session_data LIMIT {DISTINCT_PREVIEW_ROWS}")
            raw_rows = result.fetchall()
            col_data: dict[str, list[Any]] = {c: [] for c in preview_columns}
            for row in raw_rows:
                for col, val in zip(preview_columns, row):
                    col_data[col].append(val)
            preview_df = pl.DataFrame(col_data)
        else:
            preview_df = pl.DataFrame()

        t_preview = perf_time.perf_counter()
        open_timing = {
            "config_ms": int((t_config - t0) * 1000),
            "accelerated_ms": int((t_acc - t_config) * 1000),
            "duck_ms": int((t_duck - t_acc) * 1000),
            "preview_ms": int((t_preview - t_duck) * 1000),
            "total_ms": int((t_preview - t0) * 1000),
            "cache_hit": accelerated.cache_hit,
        }

        metadata = {
            "source_name": resolved_path.name,
            "source_path": str(resolved_path),
            "source_kind": accelerated.source_kind,
            "row_count": accelerated.row_count,
            "all_columns": accelerated.all_columns,
            "numeric_columns": accelerated.numeric_columns,
            "date_columns": accelerated.date_columns,
            "column_types": accelerated.column_types,
            "distinct_values": _distinct_preview(preview_df, accelerated.column_types),
            "runtime": dashboard.get("dashboard", {}).get("runtime", {}),
        }

        session_id = uuid.uuid4().hex
        session = SessionState(
            session_id=session_id,
            source_path=resolved_path,
            source_kind=accelerated.source_kind,
            config=base_config,
            dashboard=dashboard,
            csv_options=csv_options,
            ui=dashboard.get("ui", {}),
            accelerated=accelerated,
            resolver=ColumnResolver(accelerated.all_columns),
            metadata=metadata,
            optimizer=optimizer,
            conn=duck_conn,
            _temp_parquet=temp_parquet,
            open_timing=open_timing,
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
            "performance": {
                "source_cache_hit": session.accelerated.cache_hit,
                "source_signature": session.accelerated.source_signature,
                "accelerated_kind": session.accelerated.accelerated_kind,
                "open_timing": session.open_timing,
            },
        }

    def get(self, session_id: str) -> SessionState:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError("La sesion solicitada no existe o ya fue cerrada.")
        return session

    def close(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        try:
            session.conn.close()
        except Exception:
            pass
        if session._temp_parquet and session._temp_parquet.exists():
            try:
                session._temp_parquet.unlink()
            except Exception:
                pass
        return True

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


STORE = SessionStore()


def _create_duck_session(accelerated: AcceleratedSource) -> tuple[duckdb.DuckDBPyConnection, Path | None]:
    import os as _os
    threads = min(_os.cpu_count() or 2, 4)
    conn = duckdb.connect(":memory:", config={"threads": threads, "memory_limit": "2GB"})
    temp_parquet: Path | None = None
    if accelerated.accelerated_kind in ("parquet_cache", "parquet_source"):
        path_str = str(accelerated.accelerated_path).replace("\\", "/")
        conn.execute(f"CREATE VIEW session_data AS SELECT * FROM read_parquet('{path_str}')")
    else:
        tmp = Path(tempfile.mktemp(suffix=".parquet", prefix="vfp_dash_"))
        _write_parquet(accelerated.lazy_frame, tmp)
        temp_parquet = tmp
        path_str = str(tmp).replace("\\", "/")
        conn.execute(f"CREATE VIEW session_data AS SELECT * FROM read_parquet('{path_str}')")
    return conn, temp_parquet


def _add_where_condition(where: str, params: list[Any], clause: str, *values: Any) -> tuple[str, list[Any]]:
    new_params = list(params) + list(values)
    return (where + f" AND {clause}" if where else f"WHERE {clause}"), new_params


def _agg_sql(aggregation: str, field: str | None = None) -> str:
    agg = str(aggregation or "sum").strip().lower()
    if agg == "count":
        return "COUNT(*)"
    if not field:
        raise ValueError(f"La agregacion '{aggregation}' requiere un campo numerico.")
    q = f'TRY_CAST("{field}" AS DOUBLE)'
    if agg == "sum":
        return f"SUM({q})"
    if agg == "avg":
        return f"AVG({q})"
    if agg == "min":
        return f"MIN({q})"
    if agg == "max":
        return f"MAX({q})"
    raise ValueError(f"Agregacion no soportada: {aggregation}")


def _date_bucket_sql(field: str, granularity: str) -> str:
    cast = f'TRY_CAST("{field}" AS TIMESTAMP)'
    g = str(granularity or "day").strip().lower()
    if g == "year":
        return f"STRFTIME({cast}, '%Y')"
    if g == "month":
        return f"STRFTIME({cast}, '%Y-%m')"
    return f"STRFTIME({cast}, '%Y-%m-%d')"


@dataclass(frozen=True)
class QueryContext:
    session: SessionState
    template: dict[str, Any]
    effective_filters: list[dict[str, Any]]
    invalid_filters: list[dict[str, Any]]
    effective_date_range: dict[str, Any]
    predicate_key: str


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


def _build_query_context(
    session: SessionState,
    template_id: str | None,
    filters: list[dict[str, Any]] | None,
    date_range: dict[str, Any] | None,
) -> QueryContext:
    template = session.find_template(template_id)
    effective_date_range = _normalized_date_range(session, template, date_range)
    effective_filters, invalid_filters = _resolved_filters(session, template, filters)
    predicate_key = _signature({"filters": effective_filters, "date_range": effective_date_range})
    return QueryContext(
        session=session,
        template=template,
        effective_filters=effective_filters,
        invalid_filters=invalid_filters,
        effective_date_range=effective_date_range,
        predicate_key=predicate_key,
    )


def _required_original_columns_for_dashboard(context: QueryContext) -> list[str]:
    required: set[str] = set()
    for item in context.effective_filters:
        required.add(item["column"])

    if context.effective_date_range.get("column"):
        required.add(str(context.effective_date_range["column"]))

    for widget in context.template.get("widgets", []):
        if not isinstance(widget, dict):
            continue
        widget_type = str(widget.get("type") or "").strip().lower()
        if widget_type == "kpi":
            column = context.session.resolver.resolve(widget.get("column"))
            if column:
                required.add(column)
        elif widget_type == "chart":
            for key in ("x_column", "y_column", "date_column"):
                column = context.session.resolver.resolve(widget.get(key))
                if column:
                    required.add(column)
        elif widget_type == "table":
            required.update(context.session.resolver.resolve_many(widget.get("columns") or []))
            sort_by = context.session.resolver.resolve(widget.get("sort_by"))
            if sort_by:
                required.add(sort_by)

    return _ordered_columns(context.session.accelerated.all_columns, required)


def _required_original_columns_for_table(context: QueryContext, widget: dict[str, Any], sort_by: str | None = None) -> list[str]:
    required: set[str] = {item["column"] for item in context.effective_filters}
    if context.effective_date_range.get("column"):
        required.add(str(context.effective_date_range["column"]))
    required.update(context.session.resolver.resolve_many(widget.get("columns") or []))
    resolved_sort = context.session.resolver.resolve(sort_by or widget.get("sort_by"))
    if resolved_sort:
        required.add(resolved_sort)
    return _ordered_columns(context.session.accelerated.all_columns, required)


def _required_original_columns_for_distinct(context: QueryContext, column: str) -> list[str]:
    required: set[str] = {column, *(item["column"] for item in context.effective_filters)}
    if context.effective_date_range.get("column"):
        required.add(str(context.effective_date_range["column"]))
    return _ordered_columns(context.session.accelerated.all_columns, required)


def _numeric_field(session: SessionState, column: str) -> str | None:
    if column not in session.metadata["all_columns"]:
        return None
    helper = session.accelerated.helper_columns["number"].get(column)
    if helper:
        return helper
    if session.metadata["column_types"].get(column) == "number":
        return column
    return None


def _date_field(session: SessionState, column: str) -> str | None:
    if column not in session.metadata["all_columns"]:
        return None
    helper = session.accelerated.helper_columns["date"].get(column)
    if helper:
        return helper
    if session.metadata["column_types"].get(column) == "date":
        return column
    return None


def _build_where_sql(session: SessionState, context: QueryContext) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    dayfirst = bool(session.csv_options.get("dayfirst", True))

    for item in context.effective_filters:
        column = item["column"]
        operator = item["operator"]
        value = item["value"]
        column_type = session.metadata["column_types"].get(column, "text")

        if column_type == "number":
            field = _numeric_field(session, column)
            if not field:
                continue
            nums = [_parse_number(p) for p in _as_list(value)]
            nums = [n for n in nums if n is not None]
            if not nums:
                continue
            cast = f'TRY_CAST("{field}" AS DOUBLE)'
            if operator == "eq":
                clauses.append(f"{cast} = ?"); params.append(nums[0])
            elif operator == "neq":
                clauses.append(f"{cast} != ?"); params.append(nums[0])
            elif operator == "gt":
                clauses.append(f"{cast} > ?"); params.append(nums[0])
            elif operator == "gte":
                clauses.append(f"{cast} >= ?"); params.append(nums[0])
            elif operator == "lt":
                clauses.append(f"{cast} < ?"); params.append(nums[0])
            elif operator == "lte":
                clauses.append(f"{cast} <= ?"); params.append(nums[0])
            elif operator == "in":
                clauses.append(f"{cast} IN ({','.join('?' * len(nums))})"); params.extend(nums)

        elif column_type == "date":
            field = _date_field(session, column)
            parsed = _parse_datetime_value(value, dayfirst)
            if not field or parsed is None:
                continue
            cast = f'TRY_CAST("{field}" AS TIMESTAMP)'
            if operator == "eq":
                clauses.append(f"{cast} = ?"); params.append(parsed)
            elif operator == "neq":
                clauses.append(f"{cast} != ?"); params.append(parsed)
            elif operator == "gt":
                clauses.append(f"{cast} > ?"); params.append(parsed)
            elif operator == "gte":
                clauses.append(f"{cast} >= ?"); params.append(parsed)
            elif operator == "lt":
                clauses.append(f"{cast} < ?"); params.append(parsed)
            elif operator == "lte":
                clauses.append(f"{cast} <= ?"); params.append(parsed)

        else:
            text_expr = f"LOWER(TRIM(COALESCE(CAST(\"{column}\" AS VARCHAR), '')))"
            text_vals = [str(p).strip().lower() for p in _as_list(value) if str(p).strip()]
            if not text_vals:
                continue
            if operator == "eq":
                clauses.append(f"{text_expr} = ?"); params.append(text_vals[0])
            elif operator == "neq":
                clauses.append(f"{text_expr} != ?"); params.append(text_vals[0])
            elif operator == "contains":
                clauses.append(f"INSTR({text_expr}, ?) > 0"); params.append(text_vals[0])
            elif operator == "in":
                clauses.append(f"{text_expr} IN ({','.join('?' * len(text_vals))})"); params.extend(text_vals)
            else:
                clauses.append(f"{text_expr} = ?"); params.append(text_vals[0])

    if context.effective_date_range.get("enabled") and context.effective_date_range.get("column"):
        date_col = str(context.effective_date_range["column"]).strip()
        field = _date_field(session, date_col)
        if field:
            cast = f'TRY_CAST("{field}" AS TIMESTAMP)'
            start = _parse_datetime_value(context.effective_date_range.get("start"), dayfirst, end_of_day=False)
            end = _parse_datetime_value(context.effective_date_range.get("end"), dayfirst, end_of_day=True)
            if start is not None:
                clauses.append(f"{cast} >= ?"); params.append(start)
            if end is not None:
                clauses.append(f"{cast} <= ?"); params.append(end)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _widget_error(widget: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "id": widget.get("id", ""),
        "cell_id": widget.get("cell_id", ""),
        "type": widget.get("type", ""),
        "title": widget.get("title", ""),
        "valid": False,
        "error": message,
    }


def _duck_batch_kpis(session: SessionState, widgets: list[Any], where: str, params: list[Any]) -> dict[str, Any]:
    """Single SQL query for COUNT(*) + all non-count KPI aggregations."""
    selects = ["COUNT(*) AS __total_count"]
    kpi_ids: list[str] = []
    for widget in widgets:
        if not isinstance(widget, dict) or str(widget.get("type", "")).strip().lower() != "kpi":
            continue
        aggregation = str(widget.get("aggregation") or "sum").strip().lower()
        if aggregation == "count":
            continue
        column = session.resolver.resolve(widget.get("column"))
        if column not in session.metadata["all_columns"]:
            continue
        field = _numeric_field(session, column)
        if not field:
            continue
        selects.append(f"{_agg_sql(aggregation, field)} AS __kpi_{len(kpi_ids)}")
        kpi_ids.append(str(widget.get("id", "")))

    sql = f"SELECT {', '.join(selects)} FROM session_data {where}"
    row = session.conn.execute(sql, params).fetchone()
    result: dict[str, Any] = {"__total_count": int(row[0]) if row else 0}
    for i, wid in enumerate(kpi_ids):
        result[wid] = _normalize_scalar(row[i + 1] if row else None)
    return result


def _render_kpi(session: SessionState, widget: dict[str, Any], where: str, params: list[Any], batch: dict[str, Any]) -> dict[str, Any]:
    column = session.resolver.resolve(widget.get("column"))
    if column not in session.metadata["all_columns"]:
        return _widget_error(widget, f"La columna '{widget.get('column')}' no existe en la fuente.")
    aggregation = str(widget.get("aggregation") or "sum").strip().lower()
    widget_id = str(widget.get("id", ""))
    if aggregation == "count":
        value = batch.get("__total_count", 0)
    elif widget_id in batch:
        value = batch[widget_id]
    else:
        field = _numeric_field(session, column)
        if not field:
            return _widget_error(widget, f"La columna '{column}' no es numerica para KPI {aggregation}.")
        row = session.conn.execute(f"SELECT {_agg_sql(aggregation, field)} FROM session_data {where}", params).fetchone()
        value = _normalize_scalar(row[0] if row else None)
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


def _render_chart(session: SessionState, widget: dict[str, Any], where: str, params: list[Any]) -> dict[str, Any]:
    x_column = session.resolver.resolve(widget.get("x_column"))
    y_column = session.resolver.resolve(widget.get("y_column"))
    date_column = session.resolver.resolve(widget.get("date_column"))
    mode = str(widget.get("analysis_mode") or "categorias").strip().lower()
    chart_type = str(widget.get("chart_type") or "bar").strip() or "bar"
    aggregation = str(widget.get("aggregation") or ("count" if mode == "scatter" else "sum")).strip().lower()
    top_n = _safe_int(widget.get("top_n"), 12, 1, 200)
    point_limit = _safe_int(widget.get("point_limit"), 150, 1, 2000)

    if mode == "scatter":
        x_field = _numeric_field(session, x_column)
        y_field = _numeric_field(session, y_column)
        if not x_field or not y_field:
            return _widget_error(widget, "La grafica scatter requiere columnas numericas validas.")
        w2, p2 = _add_where_condition(where, list(params),
            f'TRY_CAST("{x_field}" AS DOUBLE) IS NOT NULL AND TRY_CAST("{y_field}" AS DOUBLE) IS NOT NULL')
        sql = f'SELECT TRY_CAST("{x_field}" AS DOUBLE), TRY_CAST("{y_field}" AS DOUBLE) FROM session_data {w2} LIMIT ?'
        rows = session.conn.execute(sql, p2 + [point_limit]).fetchall()
        data_points = [{"x": _normalize_scalar(r[0]), "y": _normalize_scalar(r[1])} for r in rows]
        return {
            "id": widget.get("id", ""), "cell_id": widget.get("cell_id", ""),
            "type": "chart", "title": widget.get("title", ""), "valid": True,
            "data": {"mode": mode, "chart_type": "scatter", "labels": [],
                     "datasets": [{"label": widget.get("title", ""), "data": data_points}],
                     "meta": {"x_column": x_column, "y_column": y_column}},
        }

    if mode == "tendencia":
        date_field = _date_field(session, date_column)
        if not date_field:
            return _widget_error(widget, "La grafica de tendencia requiere una columna de fecha valida.")
        granularity = str(widget.get("date_granularity") or "day").strip().lower()
        bucket = _date_bucket_sql(date_field, granularity)
        if aggregation != "count":
            y_field = _numeric_field(session, y_column)
            if not y_field:
                return _widget_error(widget, f"La columna '{y_column}' no es numerica para agregacion {aggregation}.")
        else:
            y_field = None
        agg_expr = _agg_sql(aggregation, y_field)
        w2, p2 = _add_where_condition(where, list(params), f'"{date_field}" IS NOT NULL')
        sql = f"SELECT {bucket} AS label, {agg_expr} AS value FROM session_data {w2} GROUP BY label ORDER BY label ASC"
        rows = session.conn.execute(sql, p2).fetchall()
        labels = [str(r[0]) if r[0] is not None else "" for r in rows]
        values = [_normalize_scalar(r[1]) for r in rows]
        return {
            "id": widget.get("id", ""), "cell_id": widget.get("cell_id", ""),
            "type": "chart", "title": widget.get("title", ""), "valid": True,
            "data": {"mode": mode, "chart_type": chart_type, "labels": labels,
                     "datasets": [{"label": widget.get("title", ""), "data": values}],
                     "meta": {"date_column": date_column, "granularity": granularity, "aggregation": aggregation}},
        }

    # categorias
    if x_column not in session.metadata["all_columns"]:
        return _widget_error(widget, f"La columna '{widget.get('x_column')}' no existe en la fuente.")
    if aggregation != "count":
        y_field = _numeric_field(session, y_column)
        if not y_field:
            return _widget_error(widget, f"La columna '{y_column}' no es numerica para agregacion {aggregation}.")
    else:
        y_field = None
    label_expr = f"COALESCE(TRIM(CAST(\"{x_column}\" AS VARCHAR)), '(Sin valor)')"
    sql = f"SELECT {label_expr} AS label, {_agg_sql(aggregation, y_field)} AS value FROM session_data {where} GROUP BY label ORDER BY value DESC NULLS LAST LIMIT ?"
    rows = session.conn.execute(sql, params + [top_n]).fetchall()
    labels = [str(r[0]) if r[0] is not None else "(Sin valor)" for r in rows]
    values = [_normalize_scalar(r[1]) for r in rows]
    return {
        "id": widget.get("id", ""), "cell_id": widget.get("cell_id", ""),
        "type": "chart", "title": widget.get("title", ""), "valid": True,
        "data": {"mode": mode, "chart_type": chart_type, "labels": labels,
                 "datasets": [{"label": widget.get("title", ""), "data": values}],
                 "meta": {"x_column": x_column, "y_column": y_column, "aggregation": aggregation, "top_n": top_n}},
    }


def _table_payload(
    session: SessionState,
    widget: dict[str, Any],
    where: str,
    params: list[Any],
    page: int,
    page_size: int | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
) -> dict[str, Any]:
    columns = session.resolver.resolve_many(widget.get("columns") or [])
    columns = [c for c in columns if c in session.metadata["all_columns"]]
    if not columns:
        columns = session.metadata["all_columns"][: min(8, len(session.metadata["all_columns"]))]

    limit = _safe_int(page_size or widget.get("limit"), _safe_int(widget.get("limit"), 100, 1, 1000), 1, 1000)
    page_index = _safe_int(page, 1, 1, 999999)
    offset = (page_index - 1) * limit
    order_column = session.resolver.resolve(sort_by or widget.get("sort_by"))
    order_dir = str(sort_dir or widget.get("sort_dir") or "desc").strip().lower() or "desc"

    order_clause = ""
    if order_column and order_column in session.metadata["all_columns"]:
        desc = "DESC NULLS LAST" if order_dir != "asc" else "ASC NULLS LAST"
        col_type = session.metadata["column_types"].get(order_column, "text")
        if col_type == "number":
            f = _numeric_field(session, order_column)
            if f:
                order_clause = f'ORDER BY TRY_CAST("{f}" AS DOUBLE) {desc}'
        elif col_type == "date":
            f = _date_field(session, order_column)
            if f:
                order_clause = f'ORDER BY TRY_CAST("{f}" AS TIMESTAMP) {desc}'
        if not order_clause:
            order_clause = f"ORDER BY LOWER(TRIM(COALESCE(CAST(\"{order_column}\" AS VARCHAR), ''))) {desc}"

    count_row = session.conn.execute(f"SELECT COUNT(*) FROM session_data {where}", params).fetchone()
    total_rows = int(count_row[0]) if count_row else 0

    cols_sql = ", ".join(f'"{c}"' for c in columns)
    data_sql = f"SELECT {cols_sql} FROM session_data {where} {order_clause} LIMIT ? OFFSET ?"
    raw_rows = session.conn.execute(data_sql, params + [limit, offset]).fetchall()
    rows = [{col: _normalize_scalar(val) for col, val in zip(columns, row)} for row in raw_rows]
    total_pages = max(1, math.ceil(total_rows / limit)) if total_rows else 1

    return {
        "id": widget.get("id", ""), "cell_id": widget.get("cell_id", ""),
        "type": "table", "title": widget.get("title", ""), "valid": True,
        "data": {"columns": columns, "rows": rows, "page": page_index, "page_size": limit,
                 "sort_by": order_column, "sort_dir": order_dir,
                 "total_rows": total_rows, "total_pages": total_pages},
    }


def _render_widget(session: SessionState, widget: dict[str, Any], where: str, params: list[Any], batch: dict[str, Any]) -> dict[str, Any]:
    widget_type = str(widget.get("type") or "").strip().lower()
    if widget_type == "kpi":
        return _render_kpi(session, widget, where, params, batch)
    if widget_type == "chart":
        return _render_chart(session, widget, where, params)
    if widget_type == "table":
        return _table_payload(session, widget, where, params, 1)
    return _widget_error(widget, f"Tipo de widget no soportado: {widget_type}")


def query_dashboard(
    session_id: str,
    template_id: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    date_range: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = perf_time.perf_counter()
    session = STORE.get(session_id)
    context = _build_query_context(session, template_id, filters, date_range)

    response_cache_key = _signature({
        "template_id": context.template.get("id"),
        "filters": context.effective_filters,
        "date_range": context.effective_date_range,
    })
    cached_response = session.query_cache.get(response_cache_key)
    if cached_response is not None:
        session.query_cache.move_to_end(response_cache_key)
        response = deepcopy(cached_response)
        response.setdefault("performance", {})
        response["performance"]["filtered_cache_hit"] = True
        response["performance"]["elapsed_ms"] = int((perf_time.perf_counter() - started) * 1000)
        return response

    t_where_start = perf_time.perf_counter()
    where, params = _build_where_sql(session, context)
    t_kpi_start = perf_time.perf_counter()
    all_widgets = [w for w in context.template.get("widgets", []) if isinstance(w, dict)]
    batch = _duck_batch_kpis(session, all_widgets, where, params)
    t_widgets_start = perf_time.perf_counter()
    widgets = [_render_widget(session, widget, where, params, batch) for widget in all_widgets]
    t_done = perf_time.perf_counter()

    response = {
        "ok": True,
        "session_id": session.session_id,
        "template_id": context.template.get("id", ""),
        "template": deepcopy(context.template),
        "filters": context.effective_filters,
        "invalid_filters": context.invalid_filters,
        "date_range": context.effective_date_range,
        "summary": {
            "selected_row_count": batch.get("__total_count", 0),
            "total_row_count": session.metadata["row_count"],
            "source_name": session.metadata["source_name"],
        },
        "widgets": widgets,
        "performance": {
            "filtered_cache_hit": False,
            "elapsed_ms": int((t_done - started) * 1000),
            "phases": {
                "where_ms": int((t_kpi_start - t_where_start) * 1000),
                "kpi_ms": int((t_widgets_start - t_kpi_start) * 1000),
                "widgets_ms": int((t_done - t_widgets_start) * 1000),
            },
        },
    }
    session.query_cache[response_cache_key] = deepcopy(response)
    session.query_cache.move_to_end(response_cache_key)
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
    started = perf_time.perf_counter()
    session = STORE.get(session_id)
    context = _build_query_context(session, template_id, filters, date_range)
    widget = next((item for item in context.template.get("widgets", []) if isinstance(item, dict) and str(item.get("id") or "") == str(widget_id)), None)
    if widget is None:
        raise KeyError(f"No se encontro el widget de tabla '{widget_id}'.")

    response_cache_key = _signature({
        "widget_id": widget_id,
        "template_id": context.template.get("id"),
        "filters": context.effective_filters,
        "date_range": context.effective_date_range,
        "page": page, "page_size": page_size, "sort_by": sort_by, "sort_dir": sort_dir,
    })
    cached_response = session.table_cache.get(response_cache_key)
    if cached_response is not None:
        session.table_cache.move_to_end(response_cache_key)
        response = deepcopy(cached_response)
        response.setdefault("performance", {})
        response["performance"]["filtered_cache_hit"] = True
        response["performance"]["elapsed_ms"] = int((perf_time.perf_counter() - started) * 1000)
        return response

    where, params = _build_where_sql(session, context)
    table_result = _table_payload(session, widget, where, params, page, page_size=page_size, sort_by=sort_by, sort_dir=sort_dir)
    response = {
        "ok": True,
        "session_id": session.session_id,
        "widget_id": widget_id,
        "template_id": context.template.get("id", ""),
        "filters": context.effective_filters,
        "invalid_filters": context.invalid_filters,
        "date_range": context.effective_date_range,
        "table": table_result,
        "performance": {
            "filtered_cache_hit": False,
            "elapsed_ms": int((perf_time.perf_counter() - started) * 1000),
        },
    }
    session.table_cache[response_cache_key] = deepcopy(response)
    session.table_cache.move_to_end(response_cache_key)
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
    started = perf_time.perf_counter()
    session = STORE.get(session_id)
    context = _build_query_context(session, template_id, filters, date_range)
    resolved_column = session.resolver.resolve(column)
    if resolved_column not in session.metadata["all_columns"]:
        raise KeyError(f"La columna '{column}' no existe en la sesion.")

    text_search = str(search or "").strip().lower()
    effective_limit = _safe_int(limit, 30, 1, 100)
    distinct_key = _signature({"predicate": context.predicate_key, "column": resolved_column, "search": text_search, "limit": effective_limit})
    cached_response = session.distinct_cache.get(distinct_key)
    if cached_response is not None:
        session.distinct_cache.move_to_end(distinct_key)
        response = deepcopy(cached_response)
        response.setdefault("performance", {})
        response["performance"]["filtered_cache_hit"] = True
        response["performance"]["elapsed_ms"] = int((perf_time.perf_counter() - started) * 1000)
        return response

    where, params = _build_where_sql(session, context)
    val_expr = f"LOWER(TRIM(COALESCE(CAST(\"{resolved_column}\" AS VARCHAR), '')))"
    if text_search:
        where, params = _add_where_condition(where, params, f"INSTR({val_expr}, ?) > 0", text_search)
    where, params = _add_where_condition(where, params, f"TRIM(COALESCE(CAST(\"{resolved_column}\" AS VARCHAR), '')) != ''")
    sql = f"SELECT DISTINCT TRIM(COALESCE(CAST(\"{resolved_column}\" AS VARCHAR), '')) AS value FROM session_data {where} ORDER BY value LIMIT ?"
    rows = session.conn.execute(sql, params + [effective_limit]).fetchall()
    values = [str(r[0]) for r in rows if r[0]]

    response = {
        "ok": True,
        "column": resolved_column,
        "values": values,
        "performance": {
            "filtered_cache_hit": False,
            "elapsed_ms": int((perf_time.perf_counter() - started) * 1000),
        },
    }
    session.distinct_cache[distinct_key] = deepcopy(response)
    session.distinct_cache.move_to_end(distinct_key)
    session.shrink_caches()
    return response
