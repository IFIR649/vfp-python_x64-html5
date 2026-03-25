from __future__ import annotations

from dataclasses import dataclass, field


def _slug(value: object, prefix: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return prefix
    chars = []
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


def _clean_filters(filters: object) -> list[dict]:
    out = []
    for item in filters or []:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "").strip()
        value = item.get("value")
        if not column or value in (None, ""):
            continue
        out.append(
            {
                "column": column,
                "operator": str(item.get("operator") or "eq").strip() or "eq",
                "value": value,
            }
        )
    return out


@dataclass(frozen=True)
class ChartWidget:
    x_column: str
    y_column: str = ""
    title: str = ""
    id: str = ""
    chart_type: str = "bar"
    analysis_mode: str = "categorias"
    date_column: str = ""
    aggregation: str = "sum"
    date_granularity: str = "month"
    top_n: int = 12
    point_limit: int = 150
    filters: list[dict] = field(default_factory=list)
    cell_id: str = ""

    def to_legacy_widget(self, cell_id: str | None = None) -> dict:
        resolved_cell = str(cell_id or self.cell_id or "").strip()
        if not resolved_cell:
            raise ValueError("ChartWidget requiere un cell_id para exportarse.")
        mode = str(self.analysis_mode or "categorias").strip().lower() or "categorias"
        chart_type = str(self.chart_type or "bar").strip() or "bar"
        widget_id = str(self.id or _slug(self.title or f"chart-{chart_type}", "chart")).strip()
        title = str(self.title or "Grafica").strip()
        aggregation = str(self.aggregation or ("count" if mode == "scatter" else "sum")).strip().lower()
        return {
            "id": widget_id,
            "cell_id": resolved_cell,
            "type": "chart",
            "title": title,
            "analysis_mode": mode,
            "chart_type": chart_type,
            "x_column": str(self.x_column or "").strip(),
            "y_column": str(self.y_column or "").strip(),
            "date_column": str(self.date_column or "").strip(),
            "aggregation": aggregation or "sum",
            "date_granularity": str(self.date_granularity or "month").strip() or "month",
            "top_n": max(int(self.top_n or 1), 1),
            "point_limit": max(int(self.point_limit or 1), 1),
            "filters": _clean_filters(self.filters),
        }


def _build_chart(
    factory_name: str,
    default_chart_type: str,
    default_mode: str,
    x_column: str,
    y_column: str | None = None,
    title: str | None = None,
    cell_id: str | None = None,
    **kwargs,
) -> ChartWidget:
    widget_id = kwargs.pop("id", "") or kwargs.pop("widget_id", "")
    date_column = kwargs.pop("date_column", "")
    aggregation = kwargs.pop("aggregation", "")
    date_granularity = kwargs.pop("date_granularity", "month")
    top_n = kwargs.pop("top_n", kwargs.pop("limit", 12))
    point_limit = kwargs.pop("point_limit", 150)
    filters = kwargs.pop("filters", [])
    analysis_mode = kwargs.pop("analysis_mode", "")
    chart_type = kwargs.pop("chart_type", "")

    if factory_name == "linea" and not analysis_mode and date_column:
        analysis_mode = "tendencia"
    if factory_name == "puntos":
        analysis_mode = "scatter"
        chart_type = "scatter"

    resolved_mode = str(analysis_mode or default_mode).strip().lower() or default_mode
    resolved_type = str(chart_type or default_chart_type).strip() or default_chart_type
    resolved_date = str(date_column or "").strip()
    if resolved_mode == "tendencia" and not resolved_date:
        resolved_date = str(x_column or "").strip()
    resolved_aggregation = str(aggregation or ("count" if resolved_mode == "scatter" else "sum")).strip().lower()

    return ChartWidget(
        x_column=str(x_column or "").strip(),
        y_column=str(y_column or "").strip(),
        title=str(title or "").strip(),
        id=str(widget_id or "").strip(),
        chart_type=resolved_type,
        analysis_mode=resolved_mode,
        date_column=resolved_date,
        aggregation=resolved_aggregation,
        date_granularity=str(date_granularity or "month").strip() or "month",
        top_n=max(int(top_n or 1), 1),
        point_limit=max(int(point_limit or 1), 1),
        filters=_clean_filters(filters),
        cell_id=str(cell_id or "").strip(),
    )


def bar(x_column: str, y_column: str | None = None, title: str | None = None, cell_id: str | None = None, **kwargs) -> ChartWidget:
    return _build_chart("bar", "bar", "categorias", x_column, y_column, title, cell_id, **kwargs)


def barra_lateral(
    x_column: str,
    y_column: str | None = None,
    title: str | None = None,
    cell_id: str | None = None,
    **kwargs,
) -> ChartWidget:
    return _build_chart("barra_lateral", "horizontalBar", "categorias", x_column, y_column, title, cell_id, **kwargs)


def linea(x_column: str, y_column: str | None = None, title: str | None = None, cell_id: str | None = None, **kwargs) -> ChartWidget:
    return _build_chart("linea", "line", "categorias", x_column, y_column, title, cell_id, **kwargs)


def puntos(x_column: str, y_column: str | None = None, title: str | None = None, cell_id: str | None = None, **kwargs) -> ChartWidget:
    return _build_chart("puntos", "scatter", "scatter", x_column, y_column, title, cell_id, **kwargs)


def pie(x_column: str, y_column: str | None = None, title: str | None = None, cell_id: str | None = None, **kwargs) -> ChartWidget:
    return _build_chart("pie", "pie", "composicion", x_column, y_column, title, cell_id, **kwargs)


def dona(x_column: str, y_column: str | None = None, title: str | None = None, cell_id: str | None = None, **kwargs) -> ChartWidget:
    return _build_chart("dona", "doughnut", "composicion", x_column, y_column, title, cell_id, **kwargs)


def polar(x_column: str, y_column: str | None = None, title: str | None = None, cell_id: str | None = None, **kwargs) -> ChartWidget:
    return _build_chart("polar", "polarArea", "composicion", x_column, y_column, title, cell_id, **kwargs)


def radar(x_column: str, y_column: str | None = None, title: str | None = None, cell_id: str | None = None, **kwargs) -> ChartWidget:
    return _build_chart("radar", "radar", "categorias", x_column, y_column, title, cell_id, **kwargs)


class Graficos:
    bar = staticmethod(bar)
    barra_lateral = staticmethod(barra_lateral)
    linea = staticmethod(linea)
    puntos = staticmethod(puntos)
    pie = staticmethod(pie)
    dona = staticmethod(dona)
    polar = staticmethod(polar)
    radar = staticmethod(radar)

## principales tablas en V1


