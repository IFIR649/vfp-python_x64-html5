from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import polars as pl

from .graficos import bar, barra_lateral, dona, linea, pie, polar, puntos, radar
from .kpi import kpi_avg, kpi_count, kpi_max, kpi_min, kpi_sum
from .tablas import tabla


Factory = Callable[..., Any]
PROJECT_ROOT = Path(__file__).resolve().parent.parent

BASE_CONFIG = {
    "version": 1,
    "ui": {
        "app_title": "Dashboard CSV Builder",
        "subtitle": "Explora y construye un dashboard desde VFP con WebView2",
    },
    "csv_options": {
        "base_dir": "csv",
        "delimiter": ",",
        "decimal": ".",
        "encoding": "utf-8-sig",
        "dayfirst": True,
    },
    "dashboard": {
        "title": "Dashboard CSV",
        "description": "Dashboard local optimizado para VFP + WebView2",
        "allow_user_builder": True,
        "runtime": {
            "strategy": "service_api",
            "mode": "server",
            "dashboard_only": True,
            "data_backend": "polars",
            "query_scope": {
                "date_column": "",
                "start": "",
                "end": "",
                "order": "desc",
            },
            "max_rows": 50000,
            "force_safe_template": False,
        },
        "defaults": {
            "analysis_mode": "categorias",
            "x_column": "",
            "y_column": "",
            "date_column": "",
            "aggregation": "sum",
            "chart_type": "bar",
            "date_granularity": "day",
            "top_n": 12,
            "point_limit": 150,
            "table_limit": 50,
            "sort_dir": "desc",
        },
        "active_template_id": "",
        "templates": [],
    },
}


def _merge(a: Any, b: Any) -> Any:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return b
    out = dict(a)
    for key, value in b.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_text(path_obj: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return path_obj.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path_obj.read_text()


def load_config(config_source: Any = None) -> dict[str, Any]:
    config = _merge(BASE_CONFIG, {})
    default_path = PROJECT_ROOT / "config.json"
    if default_path.exists():
        config = _merge(config, json.loads(_read_text(default_path)))

    if config_source is None:
        return config

    if isinstance(config_source, dict):
        return _merge(config, config_source)

    raw = str(config_source or "").strip()
    if not raw:
        return config

    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()

    incoming = json.loads(_read_text(candidate)) if candidate.exists() else json.loads(raw)
    return _merge(config, incoming)


def resolve_source_path(source_path: object, csv_options: dict[str, Any]) -> Path:
    raw = Path(str(source_path or "").strip())
    if raw.is_absolute():
        return raw

    first = (PROJECT_ROOT / raw).resolve()
    if first.exists():
        return first

    base_dir = str(csv_options.get("base_dir") or "").strip()
    if base_dir and (not raw.parts or raw.parts[0].lower() != Path(base_dir).name.lower()):
        second = (PROJECT_ROOT / base_dir / raw).resolve()
        if second.exists():
            return second

    return first


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


def normalize_column_names(columns: list[object]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(columns, start=1):
        base = str(raw or "").strip() or f"column_{index}"
        candidate = base
        suffix = 2
        while candidate.lower() in seen:
            candidate = f"{base}_{suffix}"
            suffix += 1
        seen.add(candidate.lower())
        out.append(candidate)
    return out


def build_column_rename_map(columns: list[object]) -> dict[str, str]:
    raw_names = [str(column) for column in columns]
    normalized = normalize_column_names(raw_names)
    return {
        raw_name: normalized_name
        for raw_name, normalized_name in zip(raw_names, normalized)
        if raw_name != normalized_name
    }


def _resolve_source_headers(source_path: object, config: dict[str, Any]) -> list[str]:
    csv_options = config.get("csv_options", {}) if isinstance(config.get("csv_options"), dict) else {}
    path_obj = resolve_source_path(source_path, csv_options)
    schema = _scan_source(path_obj, csv_options).collect_schema()
    return normalize_column_names(schema.names())


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "si", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _slug(value: object, prefix: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return prefix
    chars: list[str] = []
    last_dash = False
    for char in text:
        if char.isalnum():
            chars.append(char)
            last_dash = False
        elif not last_dash:
            chars.append("-")
            last_dash = True
    out = "".join(chars).strip("-")
    return out or prefix


class ColumnResolver:
    def __init__(self, columns: list[object]):
        self.columns = [str(column).strip() for column in columns if str(column).strip()]
        self.lookup: dict[str, str] = {}
        for column in self.columns:
            key = column.lower()
            if key not in self.lookup:
                self.lookup[key] = column

    def resolve(self, value: object, default: str = "") -> str:
        text = str(value or "").strip()
        if not text:
            return default
        return self.lookup.get(text.lower(), text)

    def resolve_many(self, values: object) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            resolved = self.resolve(value)
            if not resolved or resolved in seen:
                continue
            seen.add(resolved)
            out.append(resolved)
        return out

    def resolve_filter(self, raw_filter: object) -> dict[str, Any] | None:
        if not isinstance(raw_filter, dict):
            return None
        column = self.resolve(raw_filter.get("column"))
        value = raw_filter.get("value")
        if not column or value in (None, ""):
            return None
        return {
            "column": column,
            "operator": str(raw_filter.get("operator") or "eq").strip() or "eq",
            "value": value,
        }

    def resolve_filters(self, filters: object) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw_filter in filters or []:
            resolved = self.resolve_filter(raw_filter)
            if resolved:
                out.append(resolved)
        return out


class DashboardBuilder:
    def __init__(
        self,
        template_id: str,
        template_name: str,
        title: str,
        description: str,
        layout_rows: list[object],
        resolver: ColumnResolver,
    ):
        self.resolver = resolver
        self.template_id = str(template_id or _slug(template_name or title, "dashboard-modular")).strip()
        self.template_name = str(template_name or title or "Dashboard Modular").strip()
        self.title = str(title or self.template_name).strip()
        self.description = str(description or "Dashboard modular generado desde Python.").strip()
        self.layout_rows = self._normalize_layout_rows(layout_rows)
        self.layout = self._build_layout(self.layout_rows)
        self.valid_cells = {cell["id"] for row in self.layout["rows"] for cell in row["cells"]}
        self.used_cells: set[str] = set()
        self.widgets: list[dict[str, Any]] = []
        self.global_filters: list[dict[str, Any]] = []
        self.date_range = {"enabled": False, "column": "", "start": "", "end": ""}

    @staticmethod
    def _normalize_layout_rows(values: list[object]) -> list[int]:
        out: list[int] = []
        for value in values or []:
            try:
                columns = int(value)
            except Exception:
                continue
            out.append(min(max(columns, 1), 4))
        return out or [4, 3, 2, 1]

    @staticmethod
    def _build_layout(layout_rows: list[int]) -> dict[str, Any]:
        rows = []
        for row_index, columns in enumerate(layout_rows, start=1):
            rows.append(
                {
                    "id": f"row_{row_index}",
                    "columns": columns,
                    "cells": [{"id": f"cell_{row_index}_{col_index}"} for col_index in range(1, columns + 1)],
                }
            )
        return {"rows": rows}

    def cell(self, row: int, col: int) -> str:
        row_index = int(row)
        col_index = int(col)
        if row_index < 1 or row_index > len(self.layout_rows):
            raise ValueError(f"Fila invalida para el dashboard: {row_index}")
        expected_columns = self.layout_rows[row_index - 1]
        if col_index < 1 or col_index > expected_columns:
            raise ValueError(f"Columna invalida para la fila {row_index}: {col_index}")
        return f"cell_{row_index}_{col_index}"

    def add(self, widget: object, cell_id: str | None = None) -> object:
        resolved_cell = str(cell_id or getattr(widget, "cell_id", "") or "").strip()
        if resolved_cell not in self.valid_cells:
            raise ValueError(f"Celda invalida o fuera del layout: {resolved_cell}")
        if resolved_cell in self.used_cells:
            raise ValueError(f"La celda {resolved_cell} ya esta ocupada.")
        if not hasattr(widget, "to_legacy_widget"):
            raise TypeError("El widget no expone to_legacy_widget(cell_id).")
        self.widgets.append(widget.to_legacy_widget(resolved_cell))
        self.used_cells.add(resolved_cell)
        return widget

    def set_global_filters(self, filters: object) -> None:
        self.global_filters = self.resolver.resolve_filters(filters)

    def set_date_range(self, date_range: object, fallback_column: str = "") -> None:
        raw = date_range if isinstance(date_range, dict) else {}
        resolved_column = self.resolver.resolve(raw.get("column") or fallback_column)
        if resolved_column not in self.resolver.columns:
            resolved_column = fallback_column if fallback_column in self.resolver.columns else ""
        self.date_range = {
            "enabled": _as_bool(raw.get("enabled"), False),
            "column": resolved_column,
            "start": str(raw.get("start") or "").strip(),
            "end": str(raw.get("end") or "").strip(),
        }

    def build_template(self) -> dict[str, Any]:
        return {
            "id": self.template_id,
            "name": self.template_name,
            "title": self.title,
            "description": self.description,
            "layout": self.layout,
            "widgets": self.widgets,
            "global_filters": self.global_filters,
            "date_range": self.date_range,
        }


DEFAULT_TEMPLATE_CONFIG = {
    "template_id": "ventas-modular",
    "template_name": "Ventas Modular",
    "title": "Dashboard Ventas Modular",
    "description": "Dashboard construido con objetos lego para ventas.csv",
    "layout_rows": [4, 3, 2, 1],
}

DEFAULT_DATE_CANDIDATES = ("apertura", "fecha", "date")

DEFAULT_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "apertura": ("apertura", "fecha"),
    "total": ("total", "subtotal"),
    "subtotal": ("subtotal", "total"),
    "personas": ("personas",),
    "total_articulos": ("totalarticulos", "total"),
    "pagado": ("pagado", "cancelado"),
    "empresa": ("empresa",),
    "cancelado": ("cancelado",),
}

FACTORY_REGISTRY: dict[str, Factory] = {
    "kpi_sum": kpi_sum,
    "kpi_avg": kpi_avg,
    "kpi_count": kpi_count,
    "kpi_min": kpi_min,
    "kpi_max": kpi_max,
    "bar": bar,
    "linea": linea,
    "puntos": puntos,
    "barra_lateral": barra_lateral,
    "pie": pie,
    "dona": dona,
    "polar": polar,
    "radar": radar,
    "tabla": tabla,
}


def _pick_column(resolver: ColumnResolver, *candidates: object) -> str:
    for candidate in candidates:
        resolved = resolver.resolve(candidate)
        if resolved in resolver.columns:
            return resolved
    return resolver.columns[0] if resolver.columns else ""


def _default_columns(resolver: ColumnResolver) -> dict[str, str]:
    columns: dict[str, str] = {}
    for key, candidates in DEFAULT_COLUMN_CANDIDATES.items():
        columns[key] = _pick_column(resolver, *candidates)
    return columns


def _resolve_widget_kwargs(raw_widget: dict[str, Any], resolver: ColumnResolver) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in raw_widget.items():
        if key in {"factory", "cell"}:
            continue
        if key in {"column", "x_column", "y_column", "date_column", "sort_by"}:
            resolved[key] = resolver.resolve(value)
        elif key == "columns":
            resolved[key] = resolver.resolve_many(value)
        elif key == "filters":
            resolved[key] = resolver.resolve_filters(value)
        else:
            resolved[key] = value
    return resolved


def _preferred_date_column(template: dict[str, Any], resolver: ColumnResolver) -> str:
    date_range = template.get("date_range", {}) if isinstance(template.get("date_range"), dict) else {}
    resolved = resolver.resolve(date_range.get("column"))
    if resolved in resolver.columns:
        return resolved
    for widget in template.get("widgets", []):
        if not isinstance(widget, dict):
            continue
        resolved = resolver.resolve(widget.get("date_column"))
        if resolved in resolver.columns:
            return resolved
    return _pick_column(resolver, *DEFAULT_DATE_CANDIDATES)


def _first_chart(template: dict[str, Any]) -> dict[str, Any] | None:
    for widget in template.get("widgets", []):
        if isinstance(widget, dict) and widget.get("type") == "chart":
            return widget
    return None


def bloque_fila_1_kpis(builder: DashboardBuilder, resolver: ColumnResolver) -> None:
    columns = _default_columns(resolver)
    builder.add(
        kpi_sum(columns["total"], "Venta total", builder.cell(1, 1), id="kpi_venta_total", format="currency", accent_color="#1543c8")
    )
    builder.add(
        kpi_count(columns["apertura"], "Registros", builder.cell(1, 2), id="kpi_registros", format="integer", accent_color="#12736a")
    )
    builder.add(
        kpi_avg(
            columns["personas"],
            "Promedio personas",
            builder.cell(1, 3),
            id="kpi_promedio_personas",
            format="number",
            accent_color="#d97706",
        )
    )
    builder.add(
        kpi_max(columns["total"], "Venta maxima", builder.cell(1, 4), id="kpi_venta_maxima", format="currency", accent_color="#6d28d9")
    )


def bloque_fila_2_grafica_izq(builder: DashboardBuilder, resolver: ColumnResolver) -> None:
    columns = _default_columns(resolver)
    builder.add(
        bar(columns["personas"], columns["total"], "Venta por personas", builder.cell(2, 1), id="chart_venta_por_personas", aggregation="sum", top_n=10)
    )


def bloque_fila_2_kpi_centro(builder: DashboardBuilder, resolver: ColumnResolver) -> None:
    columns = _default_columns(resolver)
    builder.add(
        kpi_sum(
            columns["total_articulos"],
            "Articulos vendidos",
            builder.cell(2, 2),
            id="kpi_articulos_vendidos",
            format="integer",
            accent_color="#0f8aa5",
        )
    )


def bloque_fila_2_grafica_der(builder: DashboardBuilder, resolver: ColumnResolver) -> None:
    columns = _default_columns(resolver)
    builder.add(
        linea(
            columns["apertura"],
            columns["total"],
            "Tendencia diaria",
            builder.cell(2, 3),
            id="chart_tendencia_diaria",
            analysis_mode="tendencia",
            date_column=columns["apertura"],
            aggregation="sum",
            date_granularity="day",
        )
    )


def bloque_fila_3_grafica_izq(builder: DashboardBuilder, resolver: ColumnResolver) -> None:
    columns = _default_columns(resolver)
    builder.add(
        barra_lateral(
            columns["personas"],
            columns["subtotal"],
            "Subtotal por personas",
            builder.cell(3, 1),
            id="chart_subtotal_personas",
            aggregation="sum",
            top_n=10,
        )
    )


def bloque_fila_3_grafica_der(builder: DashboardBuilder, resolver: ColumnResolver) -> None:
    columns = _default_columns(resolver)
    builder.add(
        dona(
            columns["pagado"],
            columns["apertura"],
            "Estado de pago",
            builder.cell(3, 2),
            id="chart_estado_pago",
            aggregation="count",
            date_column=columns["apertura"],
            top_n=10,
        )
    )


def bloque_fila_4_tabla_detalle(builder: DashboardBuilder, resolver: ColumnResolver) -> None:
    columns = _default_columns(resolver)
    builder.add(
        tabla(
            [
                columns["empresa"],
                columns["apertura"],
                columns["personas"],
                columns["subtotal"],
                columns["total_articulos"],
                columns["total"],
                columns["pagado"],
                columns["cancelado"],
            ],
            "Detalle ventas",
            builder.cell(4, 1),
            id="tabla_detalle_ventas",
            sort_by=columns["apertura"],
            sort_dir="desc",
            limit=100,
        )
    )


DEFAULT_BLOCKS = [
    bloque_fila_1_kpis,
    bloque_fila_2_grafica_izq,
    bloque_fila_2_kpi_centro,
    bloque_fila_2_grafica_der,
    bloque_fila_3_grafica_izq,
    bloque_fila_3_grafica_der,
    bloque_fila_4_tabla_detalle,
]


def _apply_default_blocks(builder: DashboardBuilder, resolver: ColumnResolver) -> None:
    for block in DEFAULT_BLOCKS:
        block(builder, resolver)


def _build_default_template(resolver: ColumnResolver) -> dict[str, Any]:
    builder = DashboardBuilder(
        template_id=str(DEFAULT_TEMPLATE_CONFIG.get("template_id") or "ventas-modular").strip(),
        template_name=str(DEFAULT_TEMPLATE_CONFIG.get("template_name") or "Ventas Modular").strip(),
        title=str(DEFAULT_TEMPLATE_CONFIG.get("title") or "Dashboard Ventas Modular").strip(),
        description=str(DEFAULT_TEMPLATE_CONFIG.get("description") or "Dashboard construido con objetos lego para ventas.csv").strip(),
        layout_rows=list(DEFAULT_TEMPLATE_CONFIG.get("layout_rows") or [4, 3, 2, 1]),
        resolver=resolver,
    )
    columns = _default_columns(resolver)
    _apply_default_blocks(builder, resolver)
    builder.set_date_range({"enabled": False, "column": columns["apertura"], "start": "", "end": ""}, fallback_column=columns["apertura"])
    return builder.build_template()


def _build_template_from_modular_spec(modular_spec: dict[str, Any], resolver: ColumnResolver) -> dict[str, Any]:
    builder = DashboardBuilder(
        template_id=str(modular_spec.get("template_id") or "ventas-modular").strip(),
        template_name=str(modular_spec.get("template_name") or modular_spec.get("title") or "Ventas Modular").strip(),
        title=str(modular_spec.get("title") or "Dashboard Ventas Modular").strip(),
        description=str(modular_spec.get("description") or "Dashboard construido con objetos lego").strip(),
        layout_rows=modular_spec.get("layout_rows") or [4, 3, 2, 1],
        resolver=resolver,
    )

    for index, raw_widget in enumerate(modular_spec.get("widgets", []), start=1):
        if not isinstance(raw_widget, dict):
            raise ValueError(f"Widget modular invalido en la posicion {index}.")
        factory_name = str(raw_widget.get("factory") or "").strip()
        if factory_name not in FACTORY_REGISTRY:
            raise ValueError(f"Factory modular no soportada: {factory_name}")
        cell_id = str(raw_widget.get("cell") or "").strip()
        if not cell_id:
            raise ValueError(f"El widget {factory_name} en posicion {index} no define 'cell'.")
        widget_kwargs = _resolve_widget_kwargs(raw_widget, resolver)
        if not str(widget_kwargs.get("id") or "").strip():
            widget_kwargs["id"] = f"{factory_name}_{index}"
        widget = FACTORY_REGISTRY[factory_name](cell_id=cell_id, **widget_kwargs)
        builder.add(widget)

    builder.set_global_filters(modular_spec.get("global_filters"))
    builder.set_date_range(modular_spec.get("date_range"), fallback_column=_pick_column(resolver, *DEFAULT_DATE_CANDIDATES))
    return builder.build_template()


def build_dashboard_config(source_path: object, config_source: object = None) -> dict[str, Any]:
    base_config = load_config(config_source)
    resolver = ColumnResolver(_resolve_source_headers(source_path, base_config))
    modular_spec = base_config.get("dashboard_modular", {}) if isinstance(base_config.get("dashboard_modular"), dict) else {}
    modular_query_scope = modular_spec.get("query_scope", {}) if isinstance(modular_spec.get("query_scope"), dict) else {}

    if _as_bool(modular_spec.get("enabled"), False):
        template = _build_template_from_modular_spec(modular_spec, resolver)
    else:
        template = _build_default_template(resolver)

    preferred_date = _preferred_date_column(template, resolver)
    first_chart = _first_chart(template)
    translated = deepcopy(base_config)
    dashboard_cfg = translated.setdefault("dashboard", {})
    defaults_cfg = dashboard_cfg.setdefault("defaults", {})
    runtime_cfg = dashboard_cfg.setdefault("runtime", {})
    query_scope_cfg = runtime_cfg.setdefault("query_scope", {})

    runtime_cfg["strategy"] = "service_api"
    runtime_cfg["mode"] = "server"
    runtime_cfg["dashboard_only"] = True
    runtime_cfg["data_backend"] = "polars"
    runtime_cfg.pop("sqlite_cache", None)

    dashboard_cfg["title"] = template["title"]
    dashboard_cfg["description"] = template["description"]
    dashboard_cfg["active_template_id"] = template["id"]
    dashboard_cfg["templates"] = [template]

    if first_chart:
        defaults_cfg["x_column"] = first_chart.get("x_column", "")
        defaults_cfg["y_column"] = first_chart.get("y_column", "")
        defaults_cfg["analysis_mode"] = first_chart.get("analysis_mode", "categorias")
        defaults_cfg["chart_type"] = first_chart.get("chart_type", "bar")

    if preferred_date:
        defaults_cfg["date_column"] = preferred_date
        query_scope_cfg["date_column"] = preferred_date

    query_scope_cfg["start"] = str(modular_query_scope.get("start") or query_scope_cfg.get("start") or "").strip()
    query_scope_cfg["end"] = str(modular_query_scope.get("end") or query_scope_cfg.get("end") or "").strip()
    query_scope_cfg["order"] = str(modular_query_scope.get("order") or query_scope_cfg.get("order") or "desc").strip().lower() or "desc"
    return translated
