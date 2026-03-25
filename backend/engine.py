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


def _frame_estimated_bytes(frame: pl.DataFrame) -> int:
    try:
        return int(frame.estimated_size())
    except Exception:
        return 0


@dataclass
class FrameCacheEntry:
    frame: pl.DataFrame
    projected_columns: tuple[str, ...]
    estimated_bytes: int = 0
    sorted_frames: OrderedDict[str, pl.DataFrame] = field(default_factory=OrderedDict)


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
    query_cache: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict)
    table_cache: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict)
    filtered_cache: OrderedDict[str, FrameCacheEntry] = field(default_factory=OrderedDict)
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
        self._shrink_ordered(self.filtered_cache, self.optimizer.session_filtered_entries)
        self._shrink_ordered(self.distinct_cache, self.optimizer.session_distinct_entries)

    def get_filtered_entry(self, predicate_key: str, required_columns: list[str]) -> FrameCacheEntry | None:
        entry = self.filtered_cache.get(predicate_key)
        if entry is None:
            return None
        if all(column in entry.projected_columns for column in required_columns):
            self.filtered_cache.move_to_end(predicate_key)
            return entry
        return None

    def put_filtered_entry(self, predicate_key: str, frame: pl.DataFrame, projected_columns: list[str]) -> FrameCacheEntry:
        entry = FrameCacheEntry(
            frame=frame,
            projected_columns=tuple(projected_columns),
            estimated_bytes=_frame_estimated_bytes(frame),
        )
        self.filtered_cache[predicate_key] = entry
        self.filtered_cache.move_to_end(predicate_key)
        self.shrink_caches()
        return entry

    def get_sorted_frame(self, predicate_key: str, sort_key: str) -> pl.DataFrame | None:
        entry = self.filtered_cache.get(predicate_key)
        if entry is None:
            return None
        sorted_frame = entry.sorted_frames.get(sort_key)
        if sorted_frame is None:
            return None
        entry.sorted_frames.move_to_end(sort_key)
        return sorted_frame

    def put_sorted_frame(self, predicate_key: str, sort_key: str, frame: pl.DataFrame) -> pl.DataFrame:
        entry = self.filtered_cache.get(predicate_key)
        if entry is None:
            return frame
        entry.sorted_frames[sort_key] = frame
        entry.sorted_frames.move_to_end(sort_key)
        while len(entry.sorted_frames) > self.optimizer.session_sorted_variants:
            entry.sorted_frames.popitem(last=False)
        return frame


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
        optimizer = _optimizer_config(base_config)
        csv_options = base_config.get("csv_options", {}) if isinstance(base_config.get("csv_options"), dict) else {}
        resolved_path = resolve_source_path(source_path, csv_options)
        if not resolved_path.exists():
            raise FileNotFoundError(f"No se encontro el archivo fuente: {resolved_path}")

        dashboard = build_dashboard_config(source_path, incoming)
        accelerated = _open_accelerated_source(resolved_path, csv_options, optimizer, dashboard)
        preview_columns = _ordered_columns(accelerated.all_columns, set(accelerated.all_columns[: min(8, len(accelerated.all_columns))]))
        preview_sample = _sample_frame(accelerated.lazy_frame.select(preview_columns), size=DISTINCT_PREVIEW_ROWS) if preview_columns else pl.DataFrame()
        metadata = {
            "source_name": resolved_path.name,
            "source_path": str(resolved_path),
            "source_kind": accelerated.source_kind,
            "row_count": accelerated.row_count,
            "all_columns": accelerated.all_columns,
            "numeric_columns": accelerated.numeric_columns,
            "date_columns": accelerated.date_columns,
            "column_types": accelerated.column_types,
            "distinct_values": _distinct_preview(preview_sample, accelerated.column_types),
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
            return self._sessions.pop(session_id, None) is not None

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


STORE = SessionStore()


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


def _projected_columns(session: SessionState, original_columns: list[str]) -> list[str]:
    projected = list(original_columns)
    for column in original_columns:
        helper = session.accelerated.helper_columns["date"].get(column)
        if helper and helper not in projected:
            projected.append(helper)
        helper = session.accelerated.helper_columns["number"].get(column)
        if helper and helper not in projected:
            projected.append(helper)
    return projected


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


def _apply_filters_to_lazy(lazy_frame: pl.LazyFrame, session: SessionState, context: QueryContext) -> pl.LazyFrame:
    out = lazy_frame
    for item in context.effective_filters:
        column = item["column"]
        operator = item["operator"]
        value = item["value"]
        column_type = session.metadata["column_types"].get(column, "text")

        if column_type == "number":
            numeric_field = _numeric_field(session, column)
            if not numeric_field:
                continue
            parsed_numbers = [_parse_number(part) for part in _as_list(value)]
            parsed_numbers = [part for part in parsed_numbers if part is not None]
            if not parsed_numbers:
                continue
            expr = pl.col(numeric_field).cast(pl.Float64, strict=False)
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
            date_field = _date_field(session, column)
            parsed_start = _parse_datetime_value(value, bool(session.csv_options.get("dayfirst", True)))
            if not date_field or parsed_start is None:
                continue
            expr = pl.col(date_field).cast(pl.Datetime, strict=False)
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
            expr = _text_expr(column)
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

    if context.effective_date_range.get("enabled") and context.effective_date_range.get("column"):
        date_column = str(context.effective_date_range.get("column") or "").strip()
        date_field = _date_field(session, date_column)
        if date_field:
            dayfirst = bool(session.csv_options.get("dayfirst", True))
            start = _parse_datetime_value(context.effective_date_range.get("start"), dayfirst, end_of_day=False)
            end = _parse_datetime_value(context.effective_date_range.get("end"), dayfirst, end_of_day=True)
            expr = pl.col(date_field).cast(pl.Datetime, strict=False)
            if start is not None:
                out = out.filter(expr >= pl.lit(start))
            if end is not None:
                out = out.filter(expr <= pl.lit(end))

    return out


def _collect_filtered_frame(session: SessionState, context: QueryContext, required_original_columns: list[str]) -> tuple[FrameCacheEntry, bool]:
    cached = session.get_filtered_entry(context.predicate_key, required_original_columns)
    if cached is not None:
        return cached, True

    projected = _projected_columns(session, required_original_columns)
    lazy = _apply_filters_to_lazy(session.accelerated.lazy_frame, session, context)
    frame = lazy.select(projected).collect(streaming=True)
    entry = session.put_filtered_entry(context.predicate_key, frame, required_original_columns)
    return entry, False


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


def _batch_kpi_values(session: SessionState, frame: pl.DataFrame, widgets: list[Any]) -> dict[str, Any]:
    """Pre-compute all non-count KPI values in a single frame.select() pass."""
    exprs: list[pl.Expr] = []
    widget_ids: list[str] = []
    for widget in widgets:
        if not isinstance(widget, dict) or str(widget.get("type", "")).strip().lower() != "kpi":
            continue
        aggregation = str(widget.get("aggregation") or "sum").strip().lower()
        if aggregation == "count":
            continue
        column = session.resolver.resolve(widget.get("column"))
        if column not in session.metadata["all_columns"]:
            continue
        numeric_field = _numeric_field(session, column)
        if not numeric_field:
            continue
        exprs.append(_aggregate_expr(aggregation, numeric_field).alias(f"__kpi_{len(exprs)}"))
        widget_ids.append(str(widget.get("id", "")))
    if not exprs:
        return {}
    row = frame.select(exprs).row(0)
    return {wid: _normalize_scalar(val) for wid, val in zip(widget_ids, row)}


def _render_kpi(session: SessionState, frame: pl.DataFrame, widget: dict[str, Any], precomputed_kpis: dict[str, Any] | None = None) -> dict[str, Any]:
    column = session.resolver.resolve(widget.get("column"))
    if column not in session.metadata["all_columns"]:
        return _widget_error(widget, f"La columna '{widget.get('column')}' no existe en la fuente.")

    aggregation = str(widget.get("aggregation") or "sum").strip().lower()
    widget_id = str(widget.get("id", ""))
    if aggregation == "count":
        value = frame.height
    elif precomputed_kpis is not None and widget_id in precomputed_kpis:
        value = precomputed_kpis[widget_id]
    else:
        numeric_field = _numeric_field(session, column)
        if not numeric_field:
            return _widget_error(widget, f"La columna '{column}' no es numerica para KPI {aggregation}.")
        result = frame.select(_aggregate_expr(aggregation, numeric_field).alias("value"))
        value = _normalize_scalar(result.item(0, 0)) if result.height else None

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


def _render_chart(session: SessionState, frame: pl.DataFrame, widget: dict[str, Any]) -> dict[str, Any]:
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
        points = (
            frame.lazy()
            .select(pl.col(x_field).alias("x"), pl.col(y_field).alias("y"))
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
        date_field = _date_field(session, date_column)
        if not date_field:
            return _widget_error(widget, "La grafica de tendencia requiere una columna de fecha valida.")

        granularity = str(widget.get("date_granularity") or "day").strip().lower()
        if granularity == "year":
            bucket_expr = pl.col(date_field).cast(pl.Datetime, strict=False).dt.strftime("%Y")
        elif granularity == "month":
            bucket_expr = pl.col(date_field).cast(pl.Datetime, strict=False).dt.strftime("%Y-%m")
        else:
            bucket_expr = pl.col(date_field).cast(pl.Datetime, strict=False).dt.strftime("%Y-%m-%d")

        if aggregation == "count":
            data_frame = (
                frame.lazy()
                .drop_nulls([date_field])
                .group_by(bucket_expr.alias("label"))
                .agg(pl.len().alias("value"))
                .sort("label")
                .collect()
            )
        else:
            numeric_field = _numeric_field(session, y_column)
            if not numeric_field:
                return _widget_error(widget, f"La columna '{y_column}' no es numerica para agregacion {aggregation}.")
            data_frame = (
                frame.lazy()
                .drop_nulls([date_field])
                .group_by(bucket_expr.alias("label"))
                .agg(_aggregate_expr(aggregation, numeric_field).alias("value"))
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

    if aggregation == "count":
        grouped = (
            frame.lazy()
            .with_columns(pl.col(x_column).cast(pl.Utf8).fill_null("(Sin valor)").str.strip_chars().alias("__label"))
            .group_by("__label")
            .agg(pl.len().alias("value"))
            .sort("value", descending=True, nulls_last=True)
            .head(top_n)
            .collect()
        )
    else:
        numeric_field = _numeric_field(session, y_column)
        if not numeric_field:
            return _widget_error(widget, f"La columna '{y_column}' no es numerica para agregacion {aggregation}.")
        grouped = (
            frame.lazy()
            .with_columns(pl.col(x_column).cast(pl.Utf8).fill_null("(Sin valor)").str.strip_chars().alias("__label"))
            .group_by("__label")
            .agg(_aggregate_expr(aggregation, numeric_field).alias("value"))
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


def _sort_frame(session: SessionState, frame: pl.DataFrame, column: str, direction: str) -> pl.DataFrame:
    if column not in session.metadata["all_columns"]:
        return frame

    descending = str(direction or "desc").strip().lower() != "asc"
    column_type = session.metadata["column_types"].get(column, "text")
    if column_type == "number":
        numeric_field = _numeric_field(session, column)
        if numeric_field and numeric_field in frame.columns:
            return frame.sort(numeric_field, descending=descending, nulls_last=True)
        return frame

    if column_type == "date":
        date_field = _date_field(session, column)
        if date_field and date_field in frame.columns:
            return frame.sort(date_field, descending=descending, nulls_last=True)
        return frame

    return (
        frame.lazy()
        .with_columns(pl.col(column).cast(pl.Utf8).fill_null("").str.strip_chars().alias("__sort_text"))
        .sort("__sort_text", descending=descending, nulls_last=True)
        .drop("__sort_text")
        .collect()
    )


def _table_payload(
    session: SessionState,
    predicate_key: str,
    frame: pl.DataFrame,
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

    working = frame
    if order_column:
        sort_key = f"{order_column}|{order_dir}"
        cached_sorted = session.get_sorted_frame(predicate_key, sort_key)
        if cached_sorted is not None:
            working = cached_sorted
        else:
            working = _sort_frame(session, frame, order_column, order_dir)
            session.put_sorted_frame(predicate_key, sort_key, working)

    total_rows = working.height
    offset = (page_index - 1) * limit
    rows = working.select(columns).slice(offset, limit).to_dicts()
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


def _render_widget(session: SessionState, predicate_key: str, frame: pl.DataFrame, widget: dict[str, Any], precomputed_kpis: dict[str, Any] | None = None) -> dict[str, Any]:
    widget_type = str(widget.get("type") or "").strip().lower()
    if widget_type == "kpi":
        return _render_kpi(session, frame, widget, precomputed_kpis)
    if widget_type == "chart":
        return _render_chart(session, frame, widget)
    if widget_type == "table":
        return _table_payload(session, predicate_key, frame, widget, 1)
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

    response_cache_key = _signature(
        {
            "template_id": context.template.get("id"),
            "filters": context.effective_filters,
            "date_range": context.effective_date_range,
        }
    )
    cached_response = session.query_cache.get(response_cache_key)
    if cached_response is not None:
        session.query_cache.move_to_end(response_cache_key)
        response = deepcopy(cached_response)
        response.setdefault("performance", {})
        response["performance"]["filtered_cache_hit"] = True
        response["performance"]["elapsed_ms"] = int((perf_time.perf_counter() - started) * 1000)
        return response

    required_columns = _required_original_columns_for_dashboard(context)
    frame_entry, filtered_cache_hit = _collect_filtered_frame(session, context, required_columns)
    all_widgets = [w for w in context.template.get("widgets", []) if isinstance(w, dict)]
    precomputed_kpis = _batch_kpi_values(session, frame_entry.frame, all_widgets)
    widgets = [
        _render_widget(session, context.predicate_key, frame_entry.frame, widget, precomputed_kpis)
        for widget in all_widgets
    ]

    response = {
        "ok": True,
        "session_id": session.session_id,
        "template_id": context.template.get("id", ""),
        "template": deepcopy(context.template),
        "filters": context.effective_filters,
        "invalid_filters": context.invalid_filters,
        "date_range": context.effective_date_range,
        "summary": {
            "selected_row_count": frame_entry.frame.height,
            "total_row_count": session.metadata["row_count"],
            "source_name": session.metadata["source_name"],
        },
        "widgets": widgets,
        "performance": {
            "filtered_cache_hit": filtered_cache_hit,
            "projected_columns": required_columns,
            "elapsed_ms": int((perf_time.perf_counter() - started) * 1000),
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

    response_cache_key = _signature(
        {
            "widget_id": widget_id,
            "template_id": context.template.get("id"),
            "filters": context.effective_filters,
            "date_range": context.effective_date_range,
            "page": page,
            "page_size": page_size,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        }
    )
    cached_response = session.table_cache.get(response_cache_key)
    if cached_response is not None:
        session.table_cache.move_to_end(response_cache_key)
        response = deepcopy(cached_response)
        response.setdefault("performance", {})
        response["performance"]["filtered_cache_hit"] = True
        response["performance"]["elapsed_ms"] = int((perf_time.perf_counter() - started) * 1000)
        return response

    required_columns = _required_original_columns_for_table(context, widget, sort_by=sort_by)
    frame_entry, filtered_cache_hit = _collect_filtered_frame(session, context, required_columns)
    table_payload = _table_payload(
        session,
        context.predicate_key,
        frame_entry.frame,
        widget,
        page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    response = {
        "ok": True,
        "session_id": session.session_id,
        "widget_id": widget_id,
        "template_id": context.template.get("id", ""),
        "filters": context.effective_filters,
        "invalid_filters": context.invalid_filters,
        "date_range": context.effective_date_range,
        "table": table_payload,
        "performance": {
            "filtered_cache_hit": filtered_cache_hit,
            "projected_columns": required_columns,
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
    distinct_key = _signature(
        {
            "predicate": context.predicate_key,
            "column": resolved_column,
            "search": text_search,
            "limit": _safe_int(limit, 30, 1, 100),
        }
    )
    cached_response = session.distinct_cache.get(distinct_key)
    if cached_response is not None:
        session.distinct_cache.move_to_end(distinct_key)
        response = deepcopy(cached_response)
        response.setdefault("performance", {})
        response["performance"]["filtered_cache_hit"] = True
        response["performance"]["elapsed_ms"] = int((perf_time.perf_counter() - started) * 1000)
        return response

    required_columns = _required_original_columns_for_distinct(context, resolved_column)
    frame_entry, filtered_cache_hit = _collect_filtered_frame(session, context, required_columns)
    working = frame_entry.frame.lazy().select(pl.col(resolved_column).cast(pl.Utf8).fill_null("").str.strip_chars().alias("value"))
    if text_search:
        working = working.filter(pl.col("value").str.to_lowercase().str.contains(text_search, literal=True))

    data = working.filter(pl.col("value") != "").unique().sort("value").limit(_safe_int(limit, 30, 1, 100)).collect()
    values = [str(value) for value in data["value"].to_list()] if "value" in data.columns else []
    response = {
        "ok": True,
        "column": resolved_column,
        "values": values,
        "performance": {
            "filtered_cache_hit": filtered_cache_hit,
            "projected_columns": required_columns,
            "elapsed_ms": int((perf_time.perf_counter() - started) * 1000),
        },
    }
    session.distinct_cache[distinct_key] = deepcopy(response)
    session.distinct_cache.move_to_end(distinct_key)
    session.shrink_caches()
    return response
