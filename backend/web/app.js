const state = {
  sessionId: "",
  snapshot: null,
  chartMap: new Map(),
  loading: false,
};

function byId(id) {
  return document.getElementById(id);
}

function availableTemplates() {
  const templates = state.snapshot?.dashboard?.templates;
  if (!Array.isArray(templates)) {
    return [];
  }
  return templates.filter((item) => item && typeof item === "object");
}

function activeTemplate() {
  if (!state.snapshot) {
    return null;
  }
  const dashboard = state.snapshot.dashboard || {};
  const templates = availableTemplates();
  const selector = byId("templateSelect");
  const wanted = selector ? (selector.value || dashboard.active_template_id || "") : (dashboard.active_template_id || "");
  return templates.find((item) => (item?.id || "") === wanted) || templates[0] || null;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtValue(value, mode = "number") {
  if (value == null || value === "") {
    return "-";
  }
  const numeric = typeof value === "number" ? value : Number(String(value).replaceAll(",", "."));
  if (!Number.isNaN(numeric)) {
    if (mode === "currency") {
      return new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 2 }).format(numeric);
    }
    if (mode === "integer") {
      return new Intl.NumberFormat("es-MX", { maximumFractionDigits: 0 }).format(numeric);
    }
    return new Intl.NumberFormat("es-MX", {
      minimumFractionDigits: Number.isInteger(numeric) ? 0 : 2,
      maximumFractionDigits: 2,
    }).format(numeric);
  }
  return String(value);
}

function setStatus(text, mode = "") {
  const node = byId("statusPill");
  node.textContent = text;
  node.className = "status-pill";
  if (mode) {
    node.classList.add(`is-${mode}`);
  }
}

function setBusy(flag) {
  state.loading = flag;
  byId("applyFiltersButton").disabled = flag;
  byId("refreshButton").disabled = flag;
  byId("templateSelect").disabled = flag;
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail || payload.message || "Error de comunicacion con el backend.";
    throw new Error(detail);
  }
  return payload;
}

function destroyCharts() {
  for (const chart of state.chartMap.values()) {
    try {
      chart.destroy();
    } catch {}
  }
  state.chartMap.clear();
}

function availableOperators() {
  return (state.snapshot?.operators || []).map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join("");
}

function availableColumns() {
  const metadata = state.snapshot?.metadata || {};
  return Array.isArray(metadata.all_columns) ? metadata.all_columns : [];
}

function filterRowTemplate(filter = {}) {
  const operators = availableOperators();
  const columns = availableColumns()
    .map((item) => `<option value="${escapeHtml(item)}"${item === (filter.column || "") ? " selected" : ""}>${escapeHtml(item)}</option>`)
    .join("");
  const uid = `datalist_${Math.random().toString(36).slice(2)}`;
  return `
    <div class="filter-row">
      <select class="filter-column">
        <option value="">Columna</option>
        ${columns}
      </select>
      <select class="filter-operator">
        ${operators}
      </select>
      <div>
        <input class="filter-value" type="text" list="${uid}" value="${escapeHtml(filter.value || "")}" placeholder="Valor">
        <datalist id="${uid}"></datalist>
      </div>
      <button class="remove-filter" type="button" title="Quitar filtro">×</button>
    </div>
  `;
}

function appendFilterRow(filter = {}) {
  const host = byId("filterRows");
  host.insertAdjacentHTML("beforeend", filterRowTemplate(filter));
  const row = host.lastElementChild;
  row.querySelector(".filter-operator").value = filter.operator || "eq";
  row.querySelector(".remove-filter").addEventListener("click", () => row.remove());
  row.querySelector(".filter-column").addEventListener("change", () => refreshFilterSuggestions(row));
  row.querySelector(".filter-value").addEventListener("input", () => refreshFilterSuggestions(row));
}

function currentFilters() {
  return Array.from(document.querySelectorAll(".filter-row"))
    .map((row) => ({
      column: row.querySelector(".filter-column").value,
      operator: row.querySelector(".filter-operator").value,
      value: row.querySelector(".filter-value").value.trim(),
    }))
    .filter((item) => item.column && item.value);
}

function currentDateRange() {
  const template = activeTemplate();
  const templateRange = template?.date_range || {};
  const column = templateRange.column || (state.snapshot?.metadata?.date_columns || [])[0] || "";
  const start = byId("dateStart").value.trim();
  const end = byId("dateEnd").value.trim();
  return {
    enabled: Boolean(column && (start || end)),
    column,
    start,
    end,
  };
}

function updateDateHint() {
  const template = activeTemplate();
  const range = template?.date_range || {};
  const column = range.column || (state.snapshot?.metadata?.date_columns || [])[0];
  byId("dateRangeHint").textContent = column ? `Fecha: ${column}` : "Sin fecha";
}

function resetFilterUiFromTemplate() {
  const template = activeTemplate();
  const templateRange = template?.date_range || {};
  byId("dateStart").value = templateRange.start || "";
  byId("dateEnd").value = templateRange.end || "";
  byId("filterRows").innerHTML = "";
  if (Array.isArray(template?.global_filters) && template.global_filters.length) {
    template.global_filters.forEach((item) => appendFilterRow(item));
  }
  updateDateHint();
}

async function loadSession() {
  state.snapshot = await api(`/api/session/${state.sessionId}`);
  const metadata = state.snapshot.metadata || {};
  const dashboard = state.snapshot.dashboard || {};
  const templates = availableTemplates();
  byId("heroTitle").textContent = state.snapshot.ui?.app_title || dashboard.title || "Dashboard local";
  byId("heroSubtitle").textContent = state.snapshot.ui?.subtitle || dashboard.description || "Analitica local dentro de VFP.";
  byId("dashboardTitle").textContent = dashboard.title || "Dashboard";
  byId("dashboardDescription").textContent = dashboard.description || "Sin descripcion.";
  byId("sourceName").textContent = metadata.source_name || "-";
  byId("sourceType").textContent = metadata.source_kind || "-";
  byId("totalRows").textContent = fmtValue(metadata.row_count, "integer");
  byId("totalColumns").textContent = fmtValue((metadata.all_columns || []).length, "integer");
  byId("selectedRows").textContent = fmtValue(metadata.row_count, "integer");

  const templateSelect = byId("templateSelect");
  templateSelect.innerHTML = templates
    .filter((template) => template.id)
    .map((template) => `<option value="${escapeHtml(template.id)}">${escapeHtml(template.title || template.name || template.id)}</option>`)
    .join("");
  if (!templateSelect.innerHTML) {
    throw new Error("El backend no devolvio plantillas validas para esta sesion.");
  }
  templateSelect.value = dashboard.active_template_id || templates[0]?.id || templateSelect.value;
  resetFilterUiFromTemplate();
}

async function refreshFilterSuggestions(row) {
  const column = row.querySelector(".filter-column").value;
  const input = row.querySelector(".filter-value");
  const datalist = row.querySelector("datalist");
  if (!column || input.value.trim().length < 1) {
    datalist.innerHTML = "";
    return;
  }
  try {
    const payload = await api("/api/filter/values", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.sessionId,
        template_id: activeTemplate()?.id || null,
        column,
        search: input.value.trim(),
        filters: currentFilters().filter((item) => item.column !== column || item.value !== input.value.trim()),
        date_range: currentDateRange(),
      }),
    });
    datalist.innerHTML = (payload.values || [])
      .map((item) => `<option value="${escapeHtml(item)}"></option>`)
      .join("");
  } catch {}
}

function chartPalette(count) {
  const colors = [
    "#005f73",
    "#0a9396",
    "#94d2bd",
    "#ee9b00",
    "#ca6702",
    "#bb3e03",
    "#9b2226",
  ];
  return Array.from({ length: count }, (_, index) => colors[index % colors.length]);
}

function renderKpi(card, widget) {
  const data = widget.data || {};
  card.innerHTML = `
    <div class="card-head">
      <h3>${escapeHtml(widget.title || "KPI")}</h3>
      <span class="chip">${escapeHtml(String(data.aggregation || "").toUpperCase())}</span>
    </div>
    <div class="card-body">
      <div class="kpi-value" style="color:${escapeHtml(widget.accent_color || "#1d4ed8")}">${escapeHtml(fmtValue(data.value, widget.format || "number"))}</div>
      <div class="kpi-caption">${escapeHtml(data.column || "")}</div>
    </div>
  `;
}

function buildChartConfig(widget) {
  const chartData = widget.data || {};
  const labels = Array.isArray(chartData.labels) ? chartData.labels : [];
  const dataset = (chartData.datasets || [])[0] || { data: [] };
  const colors = chartPalette(Math.max(labels.length, 1));
  const chartType = chartData.chart_type === "horizontalBar" ? "bar" : chartData.chart_type || "bar";
  return {
    type: chartType,
    data: {
      labels,
      datasets: [
        {
          label: dataset.label || widget.title || "Serie",
          data: dataset.data || [],
          borderColor: colors,
          backgroundColor: chartType === "line" ? "rgba(10, 147, 150, 0.18)" : colors,
          fill: chartType === "line",
          borderWidth: 2,
          tension: 0.25,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      parsing: chartType === "scatter" ? { xAxisKey: "x", yAxisKey: "y" } : true,
      indexAxis: chartData.chart_type === "horizontalBar" ? "y" : "x",
      plugins: {
        legend: { display: chartType === "pie" || chartType === "doughnut" || chartType === "polarArea" },
      },
      scales: chartType === "pie" || chartType === "doughnut" || chartType === "polarArea"
        ? {}
        : {
            x: { ticks: { color: "#6a7388" }, grid: { color: "rgba(23,32,51,0.06)" } },
            y: { ticks: { color: "#6a7388" }, grid: { color: "rgba(23,32,51,0.06)" } },
          },
    },
  };
}

function renderChart(card, widget) {
  const chartId = `chart_${widget.id}`;
  card.innerHTML = `
    <div class="card-head">
      <h3>${escapeHtml(widget.title || "Grafica")}</h3>
      <span class="chip">${escapeHtml(widget.data?.mode || "chart")}</span>
    </div>
    <div class="card-body">
      <div class="chart-wrap"><canvas id="${chartId}"></canvas></div>
    </div>
  `;
  const canvas = card.querySelector("canvas");
  if (!canvas) {
    card.innerHTML = `<div class="error-state">No se pudo crear el canvas de la grafica.</div>`;
    return;
  }
  if (!window.Chart) {
    card.innerHTML = `<div class="error-state">Chart.js no esta disponible en esta sesion.</div>`;
    return;
  }
  const chart = new window.Chart(canvas, buildChartConfig(widget));
  state.chartMap.set(widget.id, chart);
}

function renderTable(card, widget) {
  const table = widget.data || {};
  const columns = Array.isArray(table.columns) ? table.columns : [];
  const rows = Array.isArray(table.rows) ? table.rows : [];
  const header = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
  const body = rows.length
    ? rows
        .map(
          (row) =>
            `<tr>${columns.map((column) => `<td>${escapeHtml(row[column] ?? "")}</td>`).join("")}</tr>`,
        )
        .join("")
    : `<tr><td colspan="${Math.max(columns.length, 1)}">Sin filas para mostrar.</td></tr>`;

  card.innerHTML = `
    <div class="card-head">
      <h3>${escapeHtml(widget.title || "Tabla")}</h3>
      <span class="chip">${escapeHtml(fmtValue(table.total_rows || 0, "integer"))} filas</span>
    </div>
    <div class="card-body">
      <div class="table-wrap">
        <table>
          <thead><tr>${header}</tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
      <div class="table-footer">
        <span class="muted">Pagina ${escapeHtml(String(table.page || 1))} de ${escapeHtml(String(table.total_pages || 1))}</span>
        <div class="pager">
          <button class="ghost-button pager-button" data-widget-id="${escapeHtml(widget.id)}" data-page="${Math.max((table.page || 1) - 1, 1)}" type="button">Anterior</button>
          <button class="ghost-button pager-button" data-widget-id="${escapeHtml(widget.id)}" data-page="${Math.min((table.page || 1) + 1, table.total_pages || 1)}" type="button">Siguiente</button>
        </div>
      </div>
    </div>
  `;
}

function renderWidgetCard(card, widget) {
  if (!widget || typeof widget !== "object") {
    card.innerHTML = `<div class="empty-state">Widget no disponible.</div>`;
    return;
  }
  card.dataset.widgetId = widget.id || "";
  if (!widget.valid) {
    card.innerHTML = `<div class="error-state">${escapeHtml(widget.error || "Widget invalido.")}</div>`;
    return;
  }
  if (widget.type === "kpi") {
    renderKpi(card, widget);
    return;
  }
  if (widget.type === "chart") {
    renderChart(card, widget);
    return;
  }
  if (widget.type === "table") {
    renderTable(card, widget);
    return;
  }
  card.innerHTML = `<div class="empty-state">Tipo de widget no soportado.</div>`;
}

function renderDashboard(payload) {
  destroyCharts();
  const grid = byId("dashboardGrid");
  const template = payload.template || {};
  const layoutRows = Array.isArray(template.layout?.rows) ? template.layout.rows.filter((row) => row && typeof row === "object") : [];
  const widgets = new Map(
    (Array.isArray(payload.widgets) ? payload.widgets : [])
      .filter((item) => item && typeof item === "object" && item.cell_id)
      .map((item) => [item.cell_id, item]),
  );
  grid.innerHTML = "";

  if (!layoutRows.length) {
    grid.innerHTML = `<div class="empty-state">No hay layout configurado para esta plantilla.</div>`;
    return;
  }

  layoutRows.forEach((row) => {
    const rowNode = document.createElement("section");
    rowNode.className = "dashboard-row";
    rowNode.style.gridTemplateColumns = `repeat(${row.columns || 1}, minmax(0, 1fr))`;
    (Array.isArray(row.cells) ? row.cells : []).filter((cell) => cell && typeof cell === "object").forEach((cell) => {
      const card = document.createElement("article");
      card.className = "card";
      const widget = widgets.get(cell.id);
      if (widget) {
        renderWidgetCard(card, widget);
      } else {
        card.innerHTML = `<div class="empty-state">Celda libre</div>`;
      }
      rowNode.appendChild(card);
    });
    grid.appendChild(rowNode);
  });
}

async function loadDashboard() {
  setBusy(true);
  setStatus("Actualizando...", "");
  try {
    const payload = await api("/api/dashboard/query", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.sessionId,
        template_id: activeTemplate()?.id || null,
        filters: currentFilters(),
        date_range: currentDateRange(),
      }),
    });
    byId("selectedRows").textContent = fmtValue(payload.summary?.selected_row_count || 0, "integer");
    renderDashboard(payload);
    setStatus("Listo", "ok");
  } catch (error) {
    byId("dashboardGrid").innerHTML = `<div class="error-state">${escapeHtml(error.message || "No se pudo cargar el dashboard.")}</div>`;
    setStatus("Error", "error");
  } finally {
    setBusy(false);
  }
}

async function loadTablePage(widgetId, page) {
  try {
    const payload = await api("/api/table/page", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.sessionId,
        widget_id: widgetId,
        template_id: activeTemplate()?.id || null,
        filters: currentFilters(),
        date_range: currentDateRange(),
        page,
      }),
    });
    const card = document.querySelector(`[data-widget-id="${CSS.escape(widgetId)}"]`);
    if (card && payload.table) {
      renderTable(card, payload.table);
    }
  } catch (error) {
    setStatus(error.message || "No se pudo cambiar la pagina.", "error");
  }
}

function bindEvents() {
  byId("applyFiltersButton").addEventListener("click", () => loadDashboard());
  byId("refreshButton").addEventListener("click", () => loadDashboard());
  byId("clearFiltersButton").addEventListener("click", () => {
    byId("filterRows").innerHTML = "";
    byId("dateStart").value = "";
    byId("dateEnd").value = "";
    loadDashboard();
  });
  byId("addFilterButton").addEventListener("click", () => appendFilterRow());
  byId("templateSelect").addEventListener("change", () => {
    resetFilterUiFromTemplate();
    loadDashboard();
  });
  byId("dashboardGrid").addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || !target.classList.contains("pager-button")) {
      return;
    }
    loadTablePage(target.dataset.widgetId || "", Number(target.dataset.page || "1"));
  });
}

async function bootstrap() {
  bindEvents();
  const params = new URLSearchParams(window.location.search);
  state.sessionId = params.get("session_id") || "";
  if (!state.sessionId) {
    setStatus("Error", "error");
    byId("dashboardGrid").innerHTML = `<div class="error-state">La URL no incluye session_id.</div>`;
    return;
  }

  try {
    await loadSession();
    setStatus("Sesion cargada", "ok");
    await loadDashboard();
  } catch (error) {
    setStatus("Error", "error");
    byId("dashboardGrid").innerHTML = `<div class="error-state">${escapeHtml(error.message || "No se pudo inicializar la interfaz.")}</div>`;
  }
}

window.addEventListener("DOMContentLoaded", bootstrap);
