from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd


BASE_CONFIG = {
    "version": 1,
    "ui": {
        "app_title": "Dashboard CSV Builder",
        "subtitle": "Explora y construye un dashboard desde CSV",
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
        "description": "Constructor flexible para CSV",
        "allow_user_builder": True,
        "runtime": {
            "strategy": "sidecar_js",
            "mode": "dual",
            "dashboard_only": True,
            "data_backend": "sqlite",
            "sqlite_cache": {
                "enabled": True,
                "dir": "<temp>/graficador_cache",
                "table": "rows",
            },
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


def root() -> Path:
    return Path(__file__).resolve().parent


def _merge(a, b):
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
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return path_obj.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path_obj.read_text()


def asset_text(filename: str) -> str:
    return (root() / filename).read_text(encoding="utf-8")


def load_config(config_source=None):
    config = _merge(BASE_CONFIG, {})
    default_path = root() / "config.json"
    if default_path.exists():
        config = _merge(config, json.loads(_read_text(default_path)))
    if config_source is None:
        return config
    if isinstance(config_source, dict):
        return _merge(config, config_source)
    raw = str(config_source).strip()
    if not raw:
        return config
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (root() / candidate).resolve()
    incoming = json.loads(_read_text(candidate)) if candidate.exists() else json.loads(raw)
    return _merge(config, incoming)


def resolve_csv(csv_source, csv_options):
    raw = Path(str(csv_source))
    if raw.is_absolute():
        return raw
    first = (root() / raw).resolve()
    if first.exists():
        return first
    base_dir = str(csv_options.get("base_dir", "")).strip()
    if base_dir and (not raw.parts or raw.parts[0].lower() != Path(base_dir).name.lower()):
        second = (root() / base_dir / raw).resolve()
        if second.exists():
            return second
    return first


def resolve_output(output_path):
    if output_path is None:
        return (root() / "dashboard_csv_builder.html").resolve()
    path_obj = Path(str(output_path))
    return path_obj if path_obj.is_absolute() else (root() / path_obj).resolve()


def read_csv(csv_path, csv_options):
    delimiters = []
    for item in (csv_options.get("delimiter", ","), ";", ",", "\t", "|"):
        item = str(item)
        if item not in delimiters:
            delimiters.append(item)
    encodings = []
    for item in (csv_options.get("encoding", "utf-8-sig"), "utf-8-sig", "utf-8", "cp1252", "latin1"):
        item = str(item)
        if item not in encodings:
            encodings.append(item)
    decimal = str(csv_options.get("decimal", "."))
    last_error = None
    for enc in encodings:
        for sep in delimiters:
            try:
                df = pd.read_csv(csv_path, sep=sep, decimal=decimal, encoding=enc, low_memory=False)
                df.columns = [str(col).strip() for col in df.columns]
                return df
            except Exception as exc:
                last_error = exc
    raise last_error


def _date_columns(df, dayfirst):
    cols = []
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        sample = df[col].dropna().head(200)
        if sample.empty:
            continue
        parsed = pd.to_datetime(sample, errors="coerce", dayfirst=dayfirst)
        if not parsed.empty and parsed.notna().mean() >= 0.6:
            cols.append(str(col))
    return cols


def _browser_value(value):
    if pd.isna(value):
        return None
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    return value


def _column_types(df, date_columns):
    date_set = set(date_columns)
    out = {}
    for col in df.columns:
        if str(col) in date_set:
            out[str(col)] = "date"
        elif pd.api.types.is_numeric_dtype(df[col]):
            out[str(col)] = "number"
        else:
            out[str(col)] = "text"
    return out


def _distinct_values(df, max_values=20):
    out = {}
    for col in df.columns:
        sample = df[col].dropna().drop_duplicates().head(max_values + 1)
        if sample.empty or len(sample.index) > max_values:
            continue
        values = [_browser_value(value) for value in sample.tolist()]
        values = [value for value in values if value not in (None, "")]
        if values:
            out[str(col)] = values
    return out


def _records(df):
    out = df.copy().where(pd.notnull(df), None)
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            out[col] = out[col].map(_browser_value)
    return out.to_dict(orient="records")


def _analysis_modes():
    return [
        {
            "key": "categorias",
            "label": "Categorias",
            "description": "Compara grupos y resume una medida por categoria.",
            "short_help": "Reutiliza la logica general para barras, lineas y comparaciones extendidas.",
            "chart_types": ["bar", "line", "horizontalBar", "radar"],
            "requires_date": False,
            "min_numeric_columns": 0,
        },
        {
            "key": "tendencia",
            "label": "Tendencia",
            "description": "Agrupa por fecha para leer comportamiento en el tiempo.",
            "short_help": "Necesita una columna de fecha.",
            "chart_types": ["line", "bar"],
            "requires_date": True,
            "min_numeric_columns": 0,
        },
        {
            "key": "composicion",
            "label": "Composicion",
            "description": "Mide participacion entre categorias con graficas circulares.",
            "short_help": "Ideal para pocas categorias con alto contraste.",
            "chart_types": ["pie", "doughnut", "polarArea"],
            "requires_date": False,
            "min_numeric_columns": 0,
        },
        {
            "key": "scatter",
            "label": "Scatter",
            "description": "Cruza dos columnas numericas sin agregacion intermedia.",
            "short_help": "Formulario exclusivo para correlacion.",
            "chart_types": ["scatter"],
            "requires_date": False,
            "min_numeric_columns": 2,
        },
    ]


def _safe_int(value, fallback, minimum=None, maximum=None):
    try:
        out = int(value)
    except Exception:
        out = fallback
    if minimum is not None and out < minimum:
        out = minimum
    if maximum is not None and out > maximum:
        out = maximum
    return out


def _runtime_raw(config):
    dash = config.get("dashboard", {}) if isinstance(config.get("dashboard"), dict) else {}
    runtime_cfg = dash.get("runtime", {}) if isinstance(dash.get("runtime"), dict) else {}
    return _merge(BASE_CONFIG["dashboard"]["runtime"], runtime_cfg)


def _normalize_runtime(runtime_raw):
    runtime_raw = runtime_raw if isinstance(runtime_raw, dict) else {}
    strategy = str(runtime_raw.get("strategy") or "sidecar_js").strip().lower()
    mode = str(runtime_raw.get("mode") or "dual").strip().lower()
    data_backend = str(runtime_raw.get("data_backend") or "sqlite").strip().lower()
    if strategy != "sidecar_js":
        strategy = "sidecar_js"
    if mode not in ("dual", "inline", "sidecar"):
        mode = "dual"
    if data_backend not in ("sqlite", "memory"):
        data_backend = "sqlite"
    sqlite_cache_raw = runtime_raw.get("sqlite_cache", {}) if isinstance(runtime_raw.get("sqlite_cache"), dict) else {}
    query_scope_raw = runtime_raw.get("query_scope", {}) if isinstance(runtime_raw.get("query_scope"), dict) else {}
    cache_dir = str(sqlite_cache_raw.get("dir") or "<temp>/graficador_cache").strip()
    table_name = str(sqlite_cache_raw.get("table") or "rows").strip()
    order = str(query_scope_raw.get("order") or "desc").strip().lower()
    if order not in ("asc", "desc"):
        order = "desc"
    return {
        "strategy": strategy,
        "mode": mode,
        "dashboard_only": bool(runtime_raw.get("dashboard_only", True)),
        "data_backend": data_backend,
        "sqlite_cache": {
            "enabled": bool(sqlite_cache_raw.get("enabled", True)),
            "dir": cache_dir or "<temp>/graficador_cache",
            "table": table_name or "rows",
        },
        "query_scope": {
            "date_column": str(query_scope_raw.get("date_column") or "").strip(),
            "start": str(query_scope_raw.get("start") or "").strip(),
            "end": str(query_scope_raw.get("end") or "").strip(),
            "order": order,
        },
        "max_rows": _safe_int(runtime_raw.get("max_rows"), 50000, 1),
        "force_safe_template": bool(runtime_raw.get("force_safe_template", False)),
    }


def _expand_cache_dir(raw_dir):
    text = str(raw_dir or "<temp>/graficador_cache").strip()
    temp_dir = str(Path(tempfile.gettempdir()).resolve())
    text = text.replace("<temp>", temp_dir)
    path_obj = Path(text)
    if not path_obj.is_absolute():
        path_obj = (root() / path_obj).resolve()
    return path_obj


def _sqlite_signature(csv_path, csv_options, table_name):
    stat = csv_path.stat()
    payload = {
        "csv_path": str(csv_path.resolve()),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "csv_options": csv_options,
        "table_name": str(table_name),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _slug(value, prefix):
    text = str(value or "").strip().lower()
    if not text:
        return prefix
    chunks = []
    current = []
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


def _safe_table_name(value):
    out = _slug(value, "rows").replace("-", "_")
    if not out:
        out = "rows"
    if out[0].isdigit():
        out = "t_" + out
    return out


def _quote_ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def _is_date_only_text(value):
    text = str(value or "").strip()
    if not text:
        return False
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return True
    return bool(re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", text))


def _parse_scope_datetime(value, dayfirst, end_mode=False):
    text = str(value or "").strip()
    parsed = None
    if not text:
        return ""
    for dayfirst_flag in (dayfirst, not dayfirst):
        try:
            parsed = pd.to_datetime(text, errors="coerce", dayfirst=dayfirst_flag)
        except Exception:
            parsed = None
        if parsed is not None and not pd.isna(parsed):
            break
    if parsed is None or pd.isna(parsed):
        return ""
    dt = parsed.to_pydatetime().replace(microsecond=0)
    if _is_date_only_text(text):
        if end_mode:
            dt = dt.replace(hour=23, minute=59, second=59)
        else:
            dt = dt.replace(hour=0, minute=0, second=0)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_query_scope(runtime_cfg, all_columns, date_columns, dayfirst):
    scope = runtime_cfg.get("query_scope", {}) if isinstance(runtime_cfg.get("query_scope"), dict) else {}
    date_column = str(scope.get("date_column") or "").strip()
    if date_column not in all_columns:
        date_column = date_columns[0] if date_columns else ""
    order = str(scope.get("order") or "desc").strip().lower()
    if order not in ("asc", "desc"):
        order = "desc"
    start_text = _parse_scope_datetime(scope.get("start"), dayfirst, end_mode=False)
    end_text = _parse_scope_datetime(scope.get("end"), dayfirst, end_mode=True)
    return {
        "date_column": date_column,
        "start": start_text,
        "end": end_text,
        "order": order,
        "max_rows": _safe_int(runtime_cfg.get("max_rows"), 50000, 1),
    }


def _sort_and_cap_df(df, scope):
    if df.empty:
        return df
    date_column = scope.get("date_column") or ""
    order = scope.get("order") or "desc"
    max_rows = _safe_int(scope.get("max_rows"), 50000, 1)
    if date_column and date_column in df.columns:
        temp = df.copy()
        if pd.api.types.is_datetime64_any_dtype(temp[date_column]):
            temp["_ord_date"] = temp[date_column]
        else:
            temp["_ord_date"] = temp[date_column].map(lambda value: "" if pd.isna(value) else str(value)[:19])
        temp = temp.sort_values("_ord_date", ascending=(order == "asc"), na_position="last").drop(columns=["_ord_date"])
    else:
        temp = df.copy()
        if order == "desc":
            temp = temp.iloc[::-1]
    return temp.head(max_rows)


def _apply_scope_memory(df, runtime_cfg, dayfirst):
    all_columns = [str(col) for col in df.columns]
    date_columns = _date_columns(df, dayfirst)
    scope = _normalize_query_scope(runtime_cfg, all_columns, date_columns, dayfirst)
    if not df.empty and scope["date_column"] and scope["date_column"] in df.columns and (scope["start"] or scope["end"]):
        col = scope["date_column"]
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            parsed = df[col]
        else:
            parsed = df[col].map(lambda value: "" if pd.isna(value) else str(value)[:19])
        mask = pd.Series([True] * len(df), index=df.index)
        if scope["start"]:
            mask &= parsed >= scope["start"]
        if scope["end"]:
            mask &= parsed <= scope["end"]
        selected = df[mask].copy()
    else:
        selected = df.copy()
    return _sort_and_cap_df(selected, scope), int(len(df.index)), scope


def _ensure_sqlite_cache(csv_path, csv_options, runtime_cfg):
    cache_cfg = runtime_cfg.get("sqlite_cache", {}) if isinstance(runtime_cfg.get("sqlite_cache"), dict) else {}
    cache_enabled = bool(cache_cfg.get("enabled", True))
    cache_dir = _expand_cache_dir(cache_cfg.get("dir"))
    table_name = _safe_table_name(cache_cfg.get("table") or "rows")
    cache_key = _sqlite_signature(csv_path, csv_options, table_name)
    db_path = cache_dir / (cache_key + ".sqlite3")
    cache_hit = cache_enabled and db_path.exists()
    if (not cache_enabled) and db_path.exists():
        db_path.unlink()
    if cache_hit:
        return db_path, table_name, True
    cache_dir.mkdir(parents=True, exist_ok=True)
    df = read_csv(csv_path, csv_options)
    date_col = str(runtime_cfg.get("query_scope", {}).get("date_column") or "").strip()
    with sqlite3.connect(str(db_path)) as conn:
        df.to_sql(table_name, conn, if_exists="replace", index=False)
        if date_col and date_col in [str(col) for col in df.columns]:
            idx_name = _safe_table_name("idx_" + table_name + "_" + date_col)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                + _quote_ident(idx_name)
                + " ON "
                + _quote_ident(table_name)
                + "("
                + _quote_ident(date_col)
                + ")"
            )
    return db_path, table_name, False


def _apply_scope_sqlite(csv_path, csv_options, runtime_cfg, dayfirst):
    db_path, table_name, cache_hit = _ensure_sqlite_cache(csv_path, csv_options, runtime_cfg)
    with sqlite3.connect(str(db_path)) as conn:
        cols_df = pd.read_sql_query("PRAGMA table_info(" + _quote_ident(table_name) + ")", conn)
        all_columns = [str(value) for value in cols_df["name"].tolist()] if not cols_df.empty else []
        sample_query = "SELECT * FROM " + _quote_ident(table_name) + " LIMIT 2000"
        sample_df = pd.read_sql_query(sample_query, conn)
        date_columns = _date_columns(sample_df, dayfirst)
        scope = _normalize_query_scope(runtime_cfg, all_columns, date_columns, dayfirst)
        where = []
        params = []
        if scope["date_column"] and (scope["start"] or scope["end"]):
            date_expr = "substr(" + _quote_ident(scope["date_column"]) + ",1,19)"
            if scope["start"]:
                where.append(date_expr + " >= ?")
                params.append(scope["start"])
            if scope["end"]:
                where.append(date_expr + " <= ?")
                params.append(scope["end"])
        source_row_count = pd.read_sql_query("SELECT COUNT(1) AS c FROM " + _quote_ident(table_name), conn)["c"].iloc[0]
        sql = "SELECT * FROM " + _quote_ident(table_name)
        if where:
            sql += " WHERE " + " AND ".join(where)
        if scope["date_column"]:
            sql += " ORDER BY substr(" + _quote_ident(scope["date_column"]) + ",1,19) " + scope["order"].upper()
        elif scope["order"] == "desc":
            sql += " ORDER BY rowid DESC"
        sql += " LIMIT ?"
        params.append(scope["max_rows"])
        selected_df = pd.read_sql_query(sql, conn, params=params)
    return selected_df, int(source_row_count), scope, bool(cache_hit)


def _build_meta(df, csv_path, dayfirst, source_row_count, scope, cache_hit):
    date_columns = _date_columns(df, dayfirst)
    scope_date_column = str(scope.get("date_column") or "").strip()
    if scope_date_column and scope_date_column in [str(col) for col in df.columns] and scope_date_column not in date_columns:
        date_columns.append(scope_date_column)
    selected_count = int(len(df.index))
    return {
        "csv_name": csv_path.name,
        "relative_csv_path": str(csv_path.relative_to(root())) if csv_path.is_relative_to(root()) else str(csv_path),
        "row_count": selected_count,
        "source_row_count": int(source_row_count),
        "selected_row_count": selected_count,
        "query_scope_applied": {
            "date_column": scope.get("date_column") or "",
            "start": scope.get("start") or "",
            "end": scope.get("end") or "",
            "order": scope.get("order") or "desc",
            "max_rows": _safe_int(scope.get("max_rows"), 50000, 1),
        },
        "cache_hit": bool(cache_hit),
        "all_columns": [str(col) for col in df.columns],
        "numeric_columns": [str(col) for col in df.columns if pd.api.types.is_numeric_dtype(df[col])],
        "date_columns": date_columns,
        "column_types": _column_types(df, date_columns),
        "distinct_values": _distinct_values(df, max_values=20),
    }


def _browser_config(config, meta):
    dash = config.get("dashboard", {}) if isinstance(config.get("dashboard"), dict) else {}
    runtime = _normalize_runtime(_runtime_raw(config))
    defaults = dict(dash.get("defaults", {})) if isinstance(dash.get("defaults"), dict) else {}
    return {
        "title": str(dash.get("title") or meta["csv_name"]),
        "description": str(dash.get("description") or "Exploracion local del CSV."),
        "allow_user_builder": bool(dash.get("allow_user_builder", True)),
        "runtime": runtime,
        "defaults": defaults,
        "active_template_id": str(dash.get("active_template_id") or ""),
        "templates": dash.get("templates", []) if isinstance(dash.get("templates"), list) else [],
        "default_table_columns": dash.get("default_table_columns", []) if isinstance(dash.get("default_table_columns"), list) else [],
        "legacy_migration": False,
        "export_base_config": config,
        "dashboard_shell": {
            "title": str(dash.get("title") or meta["csv_name"]),
            "description": str(dash.get("description") or "Exploracion local del CSV."),
            "allow_user_builder": bool(dash.get("allow_user_builder", True)),
            "runtime": runtime,
        },
        "chart_types": ["bar", "line", "pie", "doughnut", "polarArea", "scatter", "horizontalBar", "radar"],
        "aggregations": ["sum", "avg", "count", "min", "max"],
        "date_granularities": [
            {"value": "day", "label": "Dia"},
            {"value": "month", "label": "Mes"},
            {"value": "year", "label": "Anio"},
        ],
        "analysis_modes": _analysis_modes(),
    }


def _j(value):
    return json.dumps(value, ensure_ascii=False, default=str).replace("</", "<\\/")


def _dashboard_payload_object(rows, meta, cfg):
    return {"data": rows, "meta": meta, "cfg": cfg}


def _sidecar_js(payload_obj):
    return "window.DASHBOARD_PAYLOAD=" + _j(payload_obj) + ";"


def _html(page_title, cfg, sidecar_name, inline_payload):
    return "".join(
        [
            asset_text("graficador_head.html").replace("__PAGE_TITLE__", str(page_title)),
            asset_text("graficador_body.html"),
            '<script src="' + str(sidecar_name).replace('"', "&quot;") + '"></script>',
            "<script>",
            "var APP_BOOT=",
            _j(
                {
                    "runtime": cfg.get("runtime", {}),
                    "inline_payload": inline_payload,
                }
            ),
            ";",
            asset_text("graficador_app.js"),
            "</script></body></html>",
        ]
    )


def build_dashboard_payload(ruta_csv, config_source=None):
    config = load_config(config_source)
    csv_options = config.get("csv_options", {})
    csv_path = resolve_csv(ruta_csv, csv_options)
    if not csv_path.exists():
        raise FileNotFoundError("No se encontro el CSV: " + str(csv_path))

    runtime_cfg = _normalize_runtime(_runtime_raw(config))
    dayfirst = bool(csv_options.get("dayfirst", True))
    if runtime_cfg.get("data_backend") == "sqlite":
        df, source_row_count, scope, cache_hit = _apply_scope_sqlite(csv_path, csv_options, runtime_cfg, dayfirst)
    else:
        source_df = read_csv(csv_path, csv_options)
        df, source_row_count, scope = _apply_scope_memory(source_df, runtime_cfg, dayfirst)
        cache_hit = False
    meta = _build_meta(df, csv_path, dayfirst, source_row_count, scope, cache_hit)
    browser_cfg = _browser_config(config, meta)
    page_title = config.get("ui", {}).get("app_title", browser_cfg["title"])
    rows = _records(df)
    payload_obj = _dashboard_payload_object(rows, meta, browser_cfg)
    return {
        "config": config,
        "csv_path": csv_path,
        "dataframe": df,
        "meta": meta,
        "browser_cfg": browser_cfg,
        "rows": rows,
        "payload_obj": payload_obj,
        "page_title": page_title,
    }


def construir_html_dashboard_csv(ruta_csv, config_source=None):
    try:
        payload = build_dashboard_payload(ruta_csv, config_source)
        runtime = payload["browser_cfg"].get("runtime", {})
        inline_payload = payload["payload_obj"] if runtime.get("mode") in ("dual", "inline") else None
        return _html(
            payload["page_title"],
            payload["browser_cfg"],
            "dashboard_payload.js",
            inline_payload,
        )
    except Exception as exc:
        print("Error en Python:", str(exc))
        return False


def generar_dashboard_csv(ruta_csv, config_source=None, ruta_salida=None):
    try:
        payload = build_dashboard_payload(ruta_csv, config_source)
        output_path = resolve_output(ruta_salida)
        sidecar_name = output_path.name + ".payload.js"
        sidecar_path = output_path.with_name(sidecar_name)
        runtime = payload["browser_cfg"].get("runtime", {})
        inline_payload = payload["payload_obj"] if runtime.get("mode") in ("dual", "inline") else None
        html = _html(payload["page_title"], payload["browser_cfg"], sidecar_name, inline_payload)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        sidecar_path.write_text(_sidecar_js(payload["payload_obj"]), encoding="utf-8")
        return True
    except Exception as exc:
        print("Error en Python:", str(exc))
        return False

