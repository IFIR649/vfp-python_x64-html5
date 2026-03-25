from __future__ import annotations

from dataclasses import dataclass


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


@dataclass(frozen=True)
class KPIWidget:
    column: str
    aggregation: str
    title: str = ""
    id: str = ""
    format: str = ""
    accent_color: str = "#1d4ed8"
    cell_id: str = ""

    def to_legacy_widget(self, cell_id: str | None = None) -> dict:
        resolved_cell = str(cell_id or self.cell_id or "").strip()
        if not resolved_cell:
            raise ValueError("KPIWidget requiere un cell_id para exportarse.")
        aggregation = str(self.aggregation or "sum").strip().lower() or "sum"
        widget_id = str(self.id or _slug(self.title or f"kpi-{aggregation}-{self.column}", "kpi")).strip()
        title = str(self.title or f"KPI {aggregation.upper()}").strip()
        widget_format = str(self.format or ("integer" if aggregation == "count" else "number")).strip()
        return {
            "id": widget_id,
            "cell_id": resolved_cell,
            "type": "kpi",
            "title": title,
            "column": str(self.column or "").strip(),
            "aggregation": aggregation,
            "format": widget_format,
            "accent_color": str(self.accent_color or "#1d4ed8").strip() or "#1d4ed8",
        }


def _build_kpi(
    aggregation: str,
    column: str,
    title: str | None = None,
    cell_id: str | None = None,
    **kwargs,
) -> KPIWidget:
    widget_id = kwargs.pop("id", "") or kwargs.pop("widget_id", "")
    widget_format = kwargs.pop("format", "") or kwargs.pop("value_format", "")
    accent_color = kwargs.pop("accent_color", "#1d4ed8")
    return KPIWidget(
        column=str(column or "").strip(),
        aggregation=str(aggregation or "sum").strip().lower() or "sum",
        title=str(title or "").strip(),
        id=str(widget_id or "").strip(),
        format=str(widget_format or "").strip(),
        accent_color=str(accent_color or "#1d4ed8").strip() or "#1d4ed8",
        cell_id=str(cell_id or "").strip(),
    )


def kpi_sum(column: str, title: str | None = None, cell_id: str | None = None, **kwargs) -> KPIWidget:
    return _build_kpi("sum", column, title, cell_id, **kwargs)


def kpi_avg(column: str, title: str | None = None, cell_id: str | None = None, **kwargs) -> KPIWidget:
    return _build_kpi("avg", column, title, cell_id, **kwargs)


def kpi_count(column: str, title: str | None = None, cell_id: str | None = None, **kwargs) -> KPIWidget:
    return _build_kpi("count", column, title, cell_id, **kwargs)


def kpi_min(column: str, title: str | None = None, cell_id: str | None = None, **kwargs) -> KPIWidget:
    return _build_kpi("min", column, title, cell_id, **kwargs)


def kpi_max(column: str, title: str | None = None, cell_id: str | None = None, **kwargs) -> KPIWidget:
    return _build_kpi("max", column, title, cell_id, **kwargs)


class KPIs:
    kpi_sum = staticmethod(kpi_sum)
    kpi_avg = staticmethod(kpi_avg)
    kpi_count = staticmethod(kpi_count)
    kpi_min = staticmethod(kpi_min)
    kpi_max = staticmethod(kpi_max)

