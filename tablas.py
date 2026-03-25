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
class TableWidget:
    columns: list[str]
    title: str = ""
    id: str = ""
    limit: int = 100
    sort_by: str = ""
    sort_dir: str = "desc"
    filters: list[dict] = field(default_factory=list)
    cell_id: str = ""

    def to_legacy_widget(self, cell_id: str | None = None) -> dict:
        resolved_cell = str(cell_id or self.cell_id or "").strip()
        if not resolved_cell:
            raise ValueError("TableWidget requiere un cell_id para exportarse.")
        widget_id = str(self.id or _slug(self.title or "tabla", "table")).strip()
        return {
            "id": widget_id,
            "cell_id": resolved_cell,
            "type": "table",
            "title": str(self.title or "Tabla").strip(),
            "columns": [str(column).strip() for column in self.columns if str(column).strip()],
            "limit": max(int(self.limit or 1), 1),
            "sort_by": str(self.sort_by or "").strip(),
            "sort_dir": str(self.sort_dir or "desc").strip().lower() or "desc",
            "filters": _clean_filters(self.filters),
        }


def tabla(columns: list[str], title: str | None = None, cell_id: str | None = None, **kwargs) -> TableWidget:
    widget_id = kwargs.pop("id", "") or kwargs.pop("widget_id", "")
    limit = kwargs.pop("limit", 100)
    sort_by = kwargs.pop("sort_by", "")
    sort_dir = kwargs.pop("sort_dir", "desc")
    filters = kwargs.pop("filters", [])
    return TableWidget(
        columns=[str(column).strip() for column in (columns or []) if str(column).strip()],
        title=str(title or "").strip(),
        id=str(widget_id or "").strip(),
        limit=max(int(limit or 1), 1),
        sort_by=str(sort_by or "").strip(),
        sort_dir=str(sort_dir or "desc").strip().lower() or "desc",
        filters=_clean_filters(filters),
        cell_id=str(cell_id or "").strip(),
    )


class Tablas:
    tabla = staticmethod(tabla)

