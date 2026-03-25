var CHARTS = {};
var STATE = {
  templates: [],
  active_template_id: '',
  active_cell_id: '',
  layout_columns_draft: [],
  render_timer: null,
  source_timer: null,
  serial: 1,
  last_export_stamp: ''
};
var DATA = typeof window.DATA !== 'undefined' ? window.DATA : [];
var META = typeof window.META !== 'undefined' ? window.META : {};
var CFG = typeof window.CFG !== 'undefined' ? window.CFG : {};

/* === UTILIDADES Y ESTADO === */
function setView(name){
  var builder = name === 'builder';
  el('viewBuilder').className = builder ? 'view active' : 'view';
  el('viewDashboard').className = builder ? 'view' : 'view active';
  el('btnViewBuilder').className = builder ? 'tab-btn active' : 'tab-btn';
  el('btnViewDashboard').className = builder ? 'tab-btn' : 'tab-btn active';
  if(!builder){ window.setTimeout(function(){ renderDashboard(); }, 30); }
}

function el(id){ return document.getElementById(id); }
function arr(value){ return Object.prototype.toString.call(value) === '[object Array]' ? value : []; }
function trimText(v){ return String(v == null ? '' : v).replace(/^\s+|\s+$/g, ''); }
function lower(v){ return trimText(v).toLowerCase(); }
function esc(v){
  return String(v == null ? '' : v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function deepClone(obj){ return JSON.parse(JSON.stringify(obj == null ? null : obj)); }
function nextId(prefix){ var out = prefix + '_' + STATE.serial; STATE.serial += 1; return out; }
function pad(num){ return num < 10 ? '0' + num : String(num); }
function asArray(value){ return Object.prototype.toString.call(value) === '[object Array]'; }
function runtimeDefaults(){
  return {
    strategy:'sidecar_js',
    mode:'dual',
    dashboard_only:true,
    data_backend:'sqlite',
    sqlite_cache:{enabled:true,dir:'<temp>/graficador_cache',table:'rows'},
    query_scope:{date_column:'',start:'',end:'',order:'desc'},
    max_rows:50000,
    force_safe_template:false
  };
}
function normalizeRuntime(runtimeObj){
  var base = runtimeDefaults();
  var raw = runtimeObj && typeof runtimeObj === 'object' ? runtimeObj : {};
  var cache = raw.sqlite_cache && typeof raw.sqlite_cache === 'object' ? raw.sqlite_cache : {};
  var scope = raw.query_scope && typeof raw.query_scope === 'object' ? raw.query_scope : {};
  var out = {
    strategy:trimText(raw.strategy || base.strategy).toLowerCase() || base.strategy,
    mode:trimText(raw.mode || base.mode).toLowerCase() || base.mode,
    dashboard_only:raw.dashboard_only == null ? base.dashboard_only : !!raw.dashboard_only,
    data_backend:trimText(raw.data_backend || base.data_backend).toLowerCase() || base.data_backend,
    sqlite_cache:{
      enabled:cache.enabled == null ? base.sqlite_cache.enabled : !!cache.enabled,
      dir:trimText(cache.dir || base.sqlite_cache.dir) || base.sqlite_cache.dir,
      table:trimText(cache.table || base.sqlite_cache.table) || base.sqlite_cache.table
    },
    query_scope:{
      date_column:trimText(scope.date_column || base.query_scope.date_column),
      start:trimText(scope.start || base.query_scope.start),
      end:trimText(scope.end || base.query_scope.end),
      order:trimText(scope.order || base.query_scope.order).toLowerCase() || base.query_scope.order
    },
    max_rows:positiveIntValue(raw.max_rows, base.max_rows, 1, 2000000),
    force_safe_template:raw.force_safe_template == null ? base.force_safe_template : !!raw.force_safe_template
  };
  if(out.strategy !== 'sidecar_js'){ out.strategy = base.strategy; }
  if(out.mode !== 'dual' && out.mode !== 'inline' && out.mode !== 'sidecar'){ out.mode = base.mode; }
  if(out.data_backend !== 'sqlite' && out.data_backend !== 'memory'){ out.data_backend = base.data_backend; }
  if(out.query_scope.order !== 'asc' && out.query_scope.order !== 'desc'){ out.query_scope.order = base.query_scope.order; }
  return out;
}
function ensureRuntimeCfg(){
  var shell = CFG && CFG.dashboard_shell ? CFG.dashboard_shell : null;
  var runtime = shell && shell.runtime ? shell.runtime : (CFG ? CFG.runtime : null);
  if(!CFG || typeof CFG !== 'object'){ CFG = {}; }
  CFG.runtime = normalizeRuntime(runtime);
  if(shell){ CFG.dashboard_shell.runtime = CFG.runtime; }
}
function payloadError(payload){
  if(!payload || typeof payload !== 'object'){ return 'Payload vacio o invalido.'; }
  if(!asArray(payload.data)){ return 'payload.data debe ser arreglo.'; }
  if(!payload.meta || typeof payload.meta !== 'object'){ return 'payload.meta debe ser objeto.'; }
  if(!asArray(payload.meta.all_columns)){ return 'payload.meta.all_columns debe ser arreglo.'; }
  if(!payload.cfg || typeof payload.cfg !== 'object'){ return 'payload.cfg debe ser objeto.'; }
  return '';
}
function applyPayload(payload){
  var err = payloadError(payload);
  if(err){ return {ok:false,error:err}; }
  DATA = payload.data;
  META = payload.meta;
  CFG = payload.cfg;
  ensureRuntimeCfg();
  return {ok:true,error:''};
}
function hasLegacyGlobals(){
  return typeof window.DATA !== 'undefined' && typeof window.META !== 'undefined' && typeof window.CFG !== 'undefined';
}
function runtimeDashboardOnly(){
  var flag = CFG && CFG.runtime ? !!CFG.runtime.dashboard_only : false;
  return flag || !!(CFG && CFG.allow_user_builder === false);
}
function applyRuntimeUiMode(){
  var onlyDashboard = runtimeDashboardOnly();
  if(el('btnViewBuilder')){ el('btnViewBuilder').style.display = onlyDashboard ? 'none' : ''; }
  if(el('viewBuilder')){ el('viewBuilder').style.display = onlyDashboard ? 'none' : ''; }
  if(onlyDashboard){ setView('dashboard'); }
}
function showBootError(message){
  var msg = trimText(message) || 'No se pudo cargar la configuracion del dashboard.';
  if(el('heroTitle')){ el('heroTitle').textContent = 'Error de carga'; }
  if(el('heroDesc')){ el('heroDesc').textContent = msg; }
  if(el('chips')){ el('chips').innerHTML = ''; }
  if(el('btnViewBuilder')){ el('btnViewBuilder').className = 'tab-btn'; }
  if(el('btnViewDashboard')){ el('btnViewDashboard').className = 'tab-btn active'; }
  if(el('viewBuilder')){ el('viewBuilder').className = 'view'; }
  if(el('viewDashboard')){ el('viewDashboard').className = 'view active'; }
  if(el('dashboardTitle')){ el('dashboardTitle').textContent = 'Dashboard no disponible'; }
  if(el('dashboardDesc')){ el('dashboardDesc').textContent = msg; }
  if(el('dashboardMeta')){ el('dashboardMeta').textContent = 'Error de carga'; }
  if(el('dashboardRows')){ el('dashboardRows').innerHTML = '<section class="card"><div class="empty">' + esc(msg) + '</div></section>'; }
}
function positiveIntValue(value, fallback, minValue, maxValue){
  var out = parseInt(value, 10);
  if(isNaN(out)){ out = fallback; }
  if(minValue != null && out < minValue){ out = minValue; }
  if(maxValue != null && out > maxValue){ out = maxValue; }
  return out;
}
function positiveInt(id, fallback, minValue, maxValue){ return positiveIntValue(el(id).value, fallback, minValue, maxValue); }
function n(v){
  if(v == null || v === ''){ return null; }
  if(typeof v === 'number'){ return isNaN(v) ? null : v; }
  var t = trimText(v).replace(/\s/g, '').replace(/,/g, '.');
  var x = Number(t);
  return isNaN(x) ? null : x;
}
function makeDate(year, month, day, hour, minute, second){
  var dt = new Date(year, month - 1, day, hour || 0, minute || 0, second || 0, 0);
  if(dt.getFullYear() !== year || dt.getMonth() !== month - 1 || dt.getDate() !== day){ return null; }
  return dt;
}
function d(v){
  var t = trimText(v);
  var m, dateOnly, timePart, hh, mm, ss, dateBits, timeBits;
  if(!t){ return null; }
  if(/^\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$/.test(t)){
    dateOnly = t.split(/[ T]/)[0].split('-');
    timePart = t.indexOf(' ') >= 0 ? t.split(' ')[1] : (t.indexOf('T') >= 0 ? t.split('T')[1] : '');
    hh = 0; mm = 0; ss = 0;
    if(timePart){
      timeBits = timePart.split(':');
      hh = parseInt(timeBits[0], 10) || 0;
      mm = parseInt(timeBits[1], 10) || 0;
      ss = parseInt(timeBits[2], 10) || 0;
    }
    return makeDate(parseInt(dateOnly[0], 10), parseInt(dateOnly[1], 10), parseInt(dateOnly[2], 10), hh, mm, ss);
  }
  m = t.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
  if(m){ return makeDate(parseInt(m[3], 10), parseInt(m[2], 10), parseInt(m[1], 10), parseInt(m[4] || '0', 10), parseInt(m[5] || '0', 10), parseInt(m[6] || '0', 10)); }
  dateBits = new Date(t);
  return isNaN(dateBits.getTime()) ? null : dateBits;
}
function dInput(v, endMode){
  var t = trimText(v);
  var m;
  if(!t){ return null; }
  m = t.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if(!m){ return null; }
  return makeDate(parseInt(m[3], 10), parseInt(m[2], 10), parseInt(m[1], 10), endMode ? 23 : 0, endMode ? 59 : 0, endMode ? 59 : 0);
}
function fmt(v, mode){
  var x = typeof v === 'number' ? v : n(v);
  if(x == null){ return '-'; }
  if(mode === 'currency'){
    try{ return new Intl.NumberFormat('es-MX',{style:'currency',currency:'MXN',maximumFractionDigits:2}).format(x); }
    catch(e){ return '$' + x.toFixed(2); }
  }
  if(mode === 'integer'){
    try{ return new Intl.NumberFormat('es-MX',{maximumFractionDigits:0}).format(x); }
    catch(errInt){ return String(Math.round(x)); }
  }
  try{ return new Intl.NumberFormat('es-MX',{minimumFractionDigits:x % 1 === 0 ? 0 : 2,maximumFractionDigits:2}).format(x); }
  catch(errNum){ return String(x); }
}
function fillSelect(id, items, value, blankLabel){
  var node = el(id);
  var html = '';
  var i, item, val, label, disabled;
  if(!node){ return; }
  if(blankLabel != null){ html += '<option value="">' + esc(blankLabel) + '</option>'; }
  for(i = 0; i < items.length; i++){
    item = items[i];
    val = typeof item === 'string' ? item : item.value;
    label = typeof item === 'string' ? item : item.label;
    disabled = item && item.disabled ? ' disabled="disabled"' : '';
    html += '<option value="' + esc(val) + '"' + (String(val) === String(value) ? ' selected' : '') + disabled + '>' + esc(label) + '</option>';
  }
  node.innerHTML = html;
}
function uniqueTemplateId(seed){
  var base = lower(seed).replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'plantilla';
  var out = base;
  var idx = 2;
  while(findTemplate(out)){ out = base + '-' + idx; idx += 1; }
  return out;
}
function colorOptions(){ return [{value:'#1d4ed8',label:'Azul'},{value:'#0f766e',label:'Verde'},{value:'#ea580c',label:'Naranja'},{value:'#9333ea',label:'Violeta'},{value:'#dc2626',label:'Rojo'},{value:'#0891b2',label:'Cian'}]; }
function widgetFormats(){ return [{value:'number',label:'Numero'},{value:'currency',label:'Moneda'},{value:'integer',label:'Entero'}]; }
function destroyCharts(){
  var key;
  for(key in CHARTS){ if(Object.prototype.hasOwnProperty.call(CHARTS, key) && CHARTS[key]){ try{ CHARTS[key].destroy(); }catch(ignoreErr){} } }
  CHARTS = {};
}
function clockStamp(){
  var now = new Date();
  return pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
}

/* === METADATOS Y DEFAULTS === */
function modeMeta(key){ var i; for(i = 0; i < CFG.analysis_modes.length; i++){ if(CFG.analysis_modes[i].key === key){ return CFG.analysis_modes[i]; } } return null; }
function modeAvailable(key){ var mode = modeMeta(key); if(!mode){ return false; } if(mode.requires_date && !META.date_columns.length){ return false; } if(mode.min_numeric_columns && META.numeric_columns.length < mode.min_numeric_columns){ return false; } return true; }
function firstAvailableMode(){ var requested = CFG.defaults.analysis_mode || 'categorias'; var i; if(modeAvailable(requested)){ return requested; } for(i = 0; i < CFG.analysis_modes.length; i++){ if(modeAvailable(CFG.analysis_modes[i].key)){ return CFG.analysis_modes[i].key; } } return 'categorias'; }
function modeChartType(modeKey, wanted){ var mode = modeMeta(modeKey); if(!mode){ return wanted || 'bar'; } if(mode.chart_types.indexOf(wanted) >= 0){ return wanted; } return mode.chart_types[0]; }
function defaultXColumn(){ return META.all_columns.indexOf(CFG.defaults.x_column) >= 0 ? CFG.defaults.x_column : (META.all_columns[0] || ''); }
function defaultYColumn(){ if(META.numeric_columns.indexOf(CFG.defaults.y_column) >= 0){ return CFG.defaults.y_column; } return META.numeric_columns[0] || META.all_columns[0] || ''; }
function defaultDateColumn(){ if(META.date_columns.indexOf(CFG.defaults.date_column) >= 0){ return CFG.defaults.date_column; } return META.date_columns[0] || ''; }
function defaultTableColumns(){ var cols = arr(CFG.default_table_columns); return cols.length ? cols.slice() : META.all_columns.slice(0, Math.min(6, META.all_columns.length)); }
function typeLabel(typeKey){ if(typeKey === 'number'){ return 'Numero'; } if(typeKey === 'date'){ return 'Fecha'; } return 'Texto'; }

/* === TEMPLATES Y LAYOUT === */
function buildLayout(columnsPerRow){
  var rows = [];
  var i, j, cols, cells;
  for(i = 0; i < columnsPerRow.length; i++){
    cols = positiveIntValue(columnsPerRow[i], 1, 1, 4);
    cells = [];
    for(j = 1; j <= cols; j++){ cells.push({id:'cell_' + (i + 1) + '_' + j}); }
    rows.push({id:'row_' + (i + 1), columns:cols, cells:cells});
  }
  return {rows:rows};
}
function layoutCellIds(layout){ var out = []; var rows = layout && layout.rows ? layout.rows : []; var i, j; for(i = 0; i < rows.length; i++){ for(j = 0; j < rows[i].cells.length; j++){ out.push(rows[i].cells[j].id); } } return out; }
function firstCellId(template){ var cells = layoutCellIds(template.layout); return cells.length ? cells[0] : ''; }
function findTemplate(id){ var i; for(i = 0; i < STATE.templates.length; i++){ if(STATE.templates[i].id === id){ return STATE.templates[i]; } } return null; }
function activeTemplate(){ return findTemplate(STATE.active_template_id); }
function widgetIndexByCell(template, cellId){ var i; for(i = 0; i < template.widgets.length; i++){ if(template.widgets[i].cell_id === cellId){ return i; } } return -1; }
function widgetByCell(template, cellId){ var idx = widgetIndexByCell(template, cellId); return idx >= 0 ? template.widgets[idx] : null; }
function setActiveTemplate(id){
  var template = findTemplate(id);
  if(!template){ return; }
  STATE.active_template_id = id;
  STATE.active_cell_id = firstCellId(template);
  syncDraftFromActiveTemplate();
  renderStaticTemplateUi();
  renderWireframe();
  renderGlobalFilterEditor();
  renderWidgetEditor();
  renderAll();
}
function createBlankTemplate(){
  var id = uniqueTemplateId('plantilla-nueva');
  var template = {id:id,name:'Plantilla nueva',title:'Dashboard nuevo',description:'Plantilla creada desde el constructor.',layout:buildLayout([2]),widgets:[],global_filters:[],date_range:{enabled:false,column:defaultDateColumn(),start:'',end:''}};
  STATE.templates.push(template);
  setActiveTemplate(template.id);
}
function cloneActiveTemplate(){
  var current = activeTemplate();
  var clone;
  if(!current){ return; }
  clone = deepClone(current);
  clone.id = uniqueTemplateId(current.name || current.id || 'plantilla');
  clone.name = (trimText(current.name) || 'Plantilla') + ' copia';
  clone.title = trimText(current.title) || clone.name;
  STATE.templates.push(clone);
  setActiveTemplate(clone.id);
}
function syncDraftFromActiveTemplate(){
  var template = activeTemplate();
  var rows = template && template.layout ? template.layout.rows : [];
  var i;
  STATE.layout_columns_draft = [];
  for(i = 0; i < rows.length; i++){ STATE.layout_columns_draft.push(rows[i].columns); }
  if(!STATE.layout_columns_draft.length){ STATE.layout_columns_draft = [2]; }
}
function renderStaticTemplateUi(){
  var template = activeTemplate();
  var options = [];
  var i;
  if(!template){ return; }
  for(i = 0; i < STATE.templates.length; i++){ options.push({value:STATE.templates[i].id,label:STATE.templates[i].name || STATE.templates[i].id}); }
  fillSelect('templateSelect', options, STATE.active_template_id, null);
  el('templateName').value = template.name || '';
  el('templateTitle').value = template.title || '';
  el('templateDesc').value = template.description || '';
  el('layoutRowCount').value = STATE.layout_columns_draft.length;
  el('templateMeta').textContent = STATE.templates.length + ' plantilla(s) en el catalogo';
  el('layoutStatus').innerHTML = 'La plantilla activa tiene <strong>' + layoutCellIds(template.layout).length + '</strong> celda(s). Usa "Aplicar layout" para confirmar cambios.';
  renderLayoutConfigurator();
}
function renderLayoutConfigurator(){
  var html = '';
  var i, cols;
  for(i = 0; i < STATE.layout_columns_draft.length; i++){
    cols = STATE.layout_columns_draft[i];
    html += '<div class="layout-editor-row row-box"><div class="field-table"><div><label>Fila ' + (i + 1) + '</label><span class="layout-chip">Fila ' + (i + 1) + '</span></div><div><label>Columnas</label><select class="layout-row-cols" data-row="' + i + '"><option value="1"' + (cols === 1 ? ' selected' : '') + '>1 columna</option><option value="2"' + (cols === 2 ? ' selected' : '') + '>2 columnas</option><option value="3"' + (cols === 3 ? ' selected' : '') + '>3 columnas</option><option value="4"' + (cols === 4 ? ' selected' : '') + '>4 columnas</option></select></div></div></div>';
  }
  el('layoutConfigurator').innerHTML = html;
  bindLayoutDraftEvents();
}
function bindLayoutDraftEvents(){
  var nodes = el('layoutConfigurator').querySelectorAll('.layout-row-cols');
  var i;
  for(i = 0; i < nodes.length; i++){
    nodes[i].onchange = function(){ var idx = parseInt(this.getAttribute('data-row'), 10); STATE.layout_columns_draft[idx] = positiveIntValue(this.value, 1, 1, 4); };
  }
}
function syncDraftRowCount(){
  var desired = positiveInt('layoutRowCount', STATE.layout_columns_draft.length || 1, 1, 12);
  while(STATE.layout_columns_draft.length < desired){ STATE.layout_columns_draft.push(1); }
  while(STATE.layout_columns_draft.length > desired){ STATE.layout_columns_draft.pop(); }
  renderStaticTemplateUi();
}
function applyLayoutDraft(){
  var template = activeTemplate();
  var nextLayout = buildLayout(STATE.layout_columns_draft);
  var nextCellIds = layoutCellIds(nextLayout);
  var removed = [];
  var keptWidgets = [];
  var i, widget;
  for(i = 0; i < template.widgets.length; i++){
    widget = template.widgets[i];
    if(nextCellIds.indexOf(widget.cell_id) >= 0){ keptWidgets.push(widget); }
    else{ removed.push(widget); }
  }
  if(removed.length && !window.confirm('Se perderan ' + removed.length + ' widget(s) porque esas celdas desaparecen. Deseas continuar?')){ return; }
  template.layout = nextLayout;
  template.widgets = keptWidgets;
  if(nextCellIds.indexOf(STATE.active_cell_id) < 0){ STATE.active_cell_id = nextCellIds[0] || ''; }
  renderStaticTemplateUi();
  renderWireframe();
  renderWidgetEditor();
  renderAll();
}

/* === WIREFRAME Y SELECCION DE CELDA === */
function selectedCellMeta(){
  var template = activeTemplate();
  var rows = template && template.layout ? template.layout.rows : [];
  var i, j;
  for(i = 0; i < rows.length; i++){
    for(j = 0; j < rows[i].cells.length; j++){
      if(rows[i].cells[j].id === STATE.active_cell_id){ return {row:i + 1,col:j + 1}; }
    }
  }
  return {row:0,col:0};
}
function renderWireframe(){
  var template = activeTemplate();
  var html = '';
  var rows = template && template.layout ? template.layout.rows : [];
  var i, j, row, widget, cls, info, title, kind;
  if(!rows.length){
    el('layoutWireframe').innerHTML = '<div class="empty">La plantilla aun no tiene celdas.</div>';
    return;
  }
  for(i = 0; i < rows.length; i++){
    row = rows[i];
    html += '<div class="wireframe-row row-box"><div class="wireframe-grid">';
    for(j = 0; j < row.cells.length; j++){
      widget = widgetByCell(template, row.cells[j].id);
      cls = 'wf-btn';
      kind = 'Sin configurar';
      title = 'Celda libre';
      info = 'Haz clic para asignar un widget';
      if(row.cells[j].id === STATE.active_cell_id){ cls += ' active'; }
      if(widget){
        cls += ' ' + widget.type;
        kind = widget.type === 'kpi' ? 'KPI' : (widget.type === 'table' ? 'Tabla' : 'Grafica');
        title = widget.title || kind;
        info = widget.type === 'chart' ? modeLabel(widget.analysis_mode) : kind;
      }
      html += '<div class="wireframe-cell"><button type="button" class="' + cls + '" data-cell="' + esc(row.cells[j].id) + '">';
      html += '<b>Fila ' + (i + 1) + ' / Columna ' + (j + 1) + '</b>';
      html += '<span>' + esc(title) + '</span>';
      html += '<em>' + esc(kind + ' - ' + info) + '</em>';
      html += '</button></div>';
    }
    html += '</div></div>';
  }
  el('layoutWireframe').innerHTML = html;
  bindWireframeEvents();
  updateSelectedCellSummary();
  el('wireframeMeta').textContent = layoutCellIds(template.layout).length + ' celda(s) en la plantilla';
}
function bindWireframeEvents(){
  var buttons = el('layoutWireframe').getElementsByTagName('button');
  var i;
  for(i = 0; i < buttons.length; i++){
    buttons[i].onclick = function(){
      STATE.active_cell_id = this.getAttribute('data-cell');
      renderWireframe();
      renderWidgetEditor();
    };
  }
}
function updateSelectedCellSummary(){
  var meta = selectedCellMeta();
  var template = activeTemplate();
  var widget = widgetByCell(template, STATE.active_cell_id);
  var html = '';
  if(!STATE.active_cell_id){
    el('selectedCellSummary').innerHTML = '<span class="chip soft">No hay celda seleccionada.</span>';
    return;
  }
  html += '<span class="selected-pill">Celda: fila ' + meta.row + ', columna ' + meta.col + '</span>';
  if(widget){ html += '<span class="selected-pill">' + esc(widget.title || widget.type) + '</span>'; }
  else{ html += '<span class="chip soft">Sin configurar</span>'; }
  el('selectedCellSummary').innerHTML = html;
}

/* === WIDGETS Y FORMULARIOS === */
function modeLabel(key){ var mode = modeMeta(key); return mode ? mode.label : key; }
function defaultWidgetForType(typeKey, cellId){
  var out = {id:nextId(typeKey), cell_id:cellId, type:typeKey, filters:[]};
  if(typeKey === 'kpi'){
    out.title = 'Nuevo KPI';
    out.column = defaultYColumn();
    out.aggregation = META.numeric_columns.length ? (CFG.defaults.aggregation || 'sum') : 'count';
    out.format = out.aggregation === 'count' ? 'integer' : 'number';
    out.accent_color = colorOptions()[0].value;
    return out;
  }
  if(typeKey === 'table'){
    out.title = 'Nueva tabla';
    out.columns = defaultTableColumns();
    out.limit = CFG.defaults.table_limit || 50;
    out.sort_by = '';
    out.sort_dir = CFG.defaults.sort_dir || 'desc';
    return out;
  }
  out.title = 'Nueva grafica';
  out.analysis_mode = firstAvailableMode();
  out.x_column = defaultXColumn();
  out.y_column = defaultYColumn();
  out.date_column = defaultDateColumn();
  out.aggregation = CFG.defaults.aggregation || 'sum';
  out.chart_type = modeChartType(out.analysis_mode, CFG.defaults.chart_type || 'bar');
  out.date_granularity = CFG.defaults.date_granularity || 'day';
  out.top_n = CFG.defaults.top_n || 12;
  out.point_limit = CFG.defaults.point_limit || 150;
  return out;
}
function upsertWidget(widget){
  var template = activeTemplate();
  var idx = widgetIndexByCell(template, widget.cell_id);
  if(idx >= 0){ template.widgets[idx] = widget; }
  else{ template.widgets.push(widget); }
}
function clearSelectedCell(){
  var template = activeTemplate();
  var idx = widgetIndexByCell(template, STATE.active_cell_id);
  if(idx >= 0){ template.widgets.splice(idx, 1); }
  renderWireframe();
  renderWidgetEditor();
  renderAll();
}
function setWidgetType(typeKey){
  var template = activeTemplate();
  var widget;
  if(!STATE.active_cell_id){ return; }
  if(typeKey === 'clear'){ clearSelectedCell(); return; }
  widget = widgetByCell(template, STATE.active_cell_id);
  if(!widget || widget.type !== typeKey){ widget = defaultWidgetForType(typeKey, STATE.active_cell_id); upsertWidget(widget); }
  renderWireframe();
  renderWidgetEditor();
  renderAll();
}
function selectedWidget(){ var template = activeTemplate(); return template ? widgetByCell(template, STATE.active_cell_id) : null; }
function setTypeChooser(activeType){
  var nodes = el('widgetTypeChooser').getElementsByTagName('button');
  var i, btnType;
  for(i = 0; i < nodes.length; i++){
    btnType = nodes[i].getAttribute('data-type');
    nodes[i].className = btnType === activeType ? 'type-btn active' : 'type-btn';
  }
}
function hideWidgetForms(){
  el('widgetEditorKpi').className = 'widget-form';
  el('widgetEditorTable').className = 'widget-form';
  el('widgetEditorChart').className = 'widget-form';
}
function renderWidgetEditor(){
  var widget = selectedWidget();
  var targetText = '';
  if(!STATE.active_cell_id){
    el('widgetTargetBox').innerHTML = '<span class="chip soft">No hay celda seleccionada.</span>';
    el('widgetEditorEmpty').className = 'empty';
    hideWidgetForms();
    setTypeChooser('');
    return;
  }
  targetText = 'Editando ' + STATE.active_cell_id;
  if(widget){ targetText += ' - ' + (widget.title || widget.type); }
  el('widgetTargetBox').innerHTML = '<span class="selected-pill">' + esc(targetText) + '</span>';
  if(!widget){
    el('widgetMeta').textContent = 'Elige una categoria para esta celda.';
    el('widgetEditorEmpty').className = 'empty';
    hideWidgetForms();
    setTypeChooser('');
    return;
  }
  el('widgetEditorEmpty').className = 'empty hidden';
  setTypeChooser(widget.type);
  if(widget.type === 'kpi'){ renderKpiEditor(widget); }
  else if(widget.type === 'table'){ renderTableEditor(widget); }
  else{ renderChartEditor(widget); }
}
function renderKpiEditor(widget){
  var options = [];
  var i, colorList;
  hideWidgetForms();
  el('widgetEditorKpi').className = 'widget-form active';
  el('widgetMeta').textContent = 'Categoria KPI';
  fillSelect('kpiColumn', META.numeric_columns.length ? META.numeric_columns : META.all_columns, widget.column, null);
  fillSelect('kpiAggregation', CFG.aggregations, widget.aggregation, null);
  fillSelect('kpiFormat', widgetFormats(), widget.format || 'number', null);
  colorList = colorOptions();
  for(i = 0; i < colorList.length; i++){ options.push({value:colorList[i].value,label:colorList[i].label}); }
  fillSelect('kpiColor', options, widget.accent_color || colorList[0].value, null);
  el('kpiTitle').value = widget.title || '';
}
function tableColumnsForWidget(widget){ return arr(widget.columns).length ? widget.columns : defaultTableColumns(); }
function renderTableEditor(widget){
  hideWidgetForms();
  el('widgetEditorTable').className = 'widget-form active';
  el('widgetMeta').textContent = 'Categoria Tabla';
  el('tableWidgetTitle').value = widget.title || '';
  el('tableWidgetLimit').value = widget.limit || CFG.defaults.table_limit || 50;
  fillSelect('tableWidgetSortBy', [{value:'',label:'Sin orden'}].concat(META.all_columns), widget.sort_by || '', null);
  fillSelect('tableWidgetSortDir', [{value:'asc',label:'Ascendente'},{value:'desc',label:'Descendente'}], widget.sort_dir || 'desc', null);
  renderTableWidgetColumns(widget);
  renderFilterEditor('table', 'tableWidgetFiltersBox');
}
function renderTableWidgetColumns(widget){
  var selected = tableColumnsForWidget(widget);
  var groupOrder = ['text','number','date'];
  var groupNames = {text:'Columnas de texto',number:'Columnas numericas',date:'Columnas de fecha'};
  var html = '';
  var i, j, col, rowsHtml, groupCols, typeKey, visible, checked;
  el('tableWidgetColsMeta').textContent = selected.length ? selected.length + ' columna(s) seleccionada(s)' : 'Sin columnas';
  for(i = 0; i < groupOrder.length; i++){
    typeKey = groupOrder[i];
    groupCols = [];
    for(j = 0; j < META.all_columns.length; j++){
      col = META.all_columns[j];
      if((META.column_types[col] || 'text') === typeKey){ groupCols.push(col); }
    }
    rowsHtml = '';
    visible = 0;
    for(j = 0; j < groupCols.length; j++){
      col = groupCols[j];
      visible += 1;
      checked = selected.indexOf(col) >= 0;
      rowsHtml += '<div class="col-item"><label><input type="checkbox" class="table-widget-col" data-col="' + esc(col) + '"' + (checked ? ' checked' : '') + '> ' + esc(col) + '<span class="type-pill">' + esc(typeLabel(typeKey)) + '</span></label></div>';
    }
    if(visible){ html += '<div class="group-title">' + esc(groupNames[typeKey]) + '</div>' + rowsHtml; }
  }
  if(!html){ html = '<div class="empty">No hay columnas disponibles.</div>'; }
  el('tableWidgetColsGroups').innerHTML = html;
  bindTableColumnToggles();
}
function bindTableColumnToggles(){
  var nodes = el('tableWidgetColsGroups').querySelectorAll('.table-widget-col');
  var i;
  for(i = 0; i < nodes.length; i++){
    nodes[i].onclick = function(){
      var widget = selectedWidget();
      var col = this.getAttribute('data-col');
      var idx;
      if(!widget || widget.type !== 'table'){ return; }
      if(!widget.columns){ widget.columns = []; }
      idx = widget.columns.indexOf(col);
      if(this.checked && idx < 0){ widget.columns.push(col); }
      if(!this.checked && idx >= 0){ widget.columns.splice(idx, 1); }
      upsertWidget(widget);
      renderTableEditor(widget);
      renderWireframe();
      renderAll();
    };
  }
}
function renderChartEditor(widget){
  var modeOptions = [];
  var i, mode;
  hideWidgetForms();
  el('widgetEditorChart').className = 'widget-form active';
  el('widgetMeta').textContent = 'Categoria Grafica';
  el('chartTitle').value = widget.title || '';
  for(i = 0; i < CFG.analysis_modes.length; i++){
    mode = CFG.analysis_modes[i];
    if(modeAvailable(mode.key)){ modeOptions.push({value:mode.key,label:mode.label}); }
  }
  fillSelect('chartAnalysisMode', modeOptions, widget.analysis_mode || firstAvailableMode(), null);
  fillSelect('chartCatXCol', META.all_columns, widget.x_column || defaultXColumn(), null);
  fillSelect('chartCatYCol', META.numeric_columns.length ? META.numeric_columns : META.all_columns, widget.y_column || defaultYColumn(), null);
  fillSelect('chartCatAggType', CFG.aggregations, widget.aggregation || 'sum', null);
  fillSelect('chartCatChartType', modeMeta('categorias').chart_types, modeChartType('categorias', widget.chart_type), null);
  el('chartCatTopN').value = widget.top_n || CFG.defaults.top_n || 12;
  fillSelect('chartTrendDateCol', META.date_columns, widget.date_column || defaultDateColumn(), null);
  fillSelect('chartTrendGranularity', CFG.date_granularities, widget.date_granularity || 'day', null);
  fillSelect('chartTrendYCol', META.numeric_columns.length ? META.numeric_columns : META.all_columns, widget.y_column || defaultYColumn(), null);
  fillSelect('chartTrendAggType', CFG.aggregations, widget.aggregation || 'sum', null);
  fillSelect('chartTrendChartType', modeMeta('tendencia').chart_types, modeChartType('tendencia', widget.chart_type), null);
  fillSelect('chartCompXCol', META.all_columns, widget.x_column || defaultXColumn(), null);
  fillSelect('chartCompYCol', META.numeric_columns.length ? META.numeric_columns : META.all_columns, widget.y_column || defaultYColumn(), null);
  fillSelect('chartCompAggType', CFG.aggregations, widget.aggregation || 'sum', null);
  fillSelect('chartCompChartType', modeMeta('composicion').chart_types, modeChartType('composicion', widget.chart_type), null);
  el('chartCompTopN').value = widget.top_n || CFG.defaults.top_n || 12;
  fillSelect('chartScatterXCol', META.numeric_columns, widget.x_column || defaultYColumn(), null);
  fillSelect('chartScatterYCol', META.numeric_columns, widget.y_column || defaultYColumn(), null);
  el('chartScatterPointLimit').value = widget.point_limit || CFG.defaults.point_limit || 150;
  updateChartModeUi(widget.analysis_mode || firstAvailableMode());
  renderFilterEditor('chart', 'chartWidgetFiltersBox');
}
function updateChartModeUi(modeKey){
  var ids = ['categorias','tendencia','composicion','scatter'];
  var i, key;
  for(i = 0; i < ids.length; i++){
    key = ids[i];
    el('chartForm_' + key).className = key === modeKey ? 'widget-form active' : 'widget-form';
  }
  el('chartCatMetricHint').innerHTML = el('chartCatAggType').value !== 'count' ? 'Usa una columna numerica para calcular la medida.' : 'Con COUNT se cuentan filas y la columna Y ya no interviene.';
  el('chartCompMetricHint').innerHTML = el('chartCompAggType').value !== 'count' ? 'Usa una columna numerica para calcular la medida.' : 'Con COUNT se cuentan filas y la columna Y ya no interviene.';
}
function syncSelectedWidgetFromForm(skipRender){
  var widget = selectedWidget();
  if(!widget){ return; }
  if(widget.type === 'kpi'){
    widget.title = trimText(el('kpiTitle').value) || 'KPI';
    widget.column = el('kpiColumn').value;
    widget.aggregation = el('kpiAggregation').value;
    widget.format = el('kpiFormat').value;
    widget.accent_color = el('kpiColor').value;
  }else if(widget.type === 'table'){
    widget.title = trimText(el('tableWidgetTitle').value) || 'Tabla';
    widget.limit = positiveInt('tableWidgetLimit', CFG.defaults.table_limit || 50, 1, 100000);
    widget.sort_by = el('tableWidgetSortBy').value;
    widget.sort_dir = el('tableWidgetSortDir').value || 'desc';
    widget.filters = ownerFilters('table');
    if(!widget.columns || !widget.columns.length){ widget.columns = defaultTableColumns(); }
  }else if(widget.type === 'chart'){
    widget.title = trimText(el('chartTitle').value) || 'Grafica';
    widget.analysis_mode = el('chartAnalysisMode').value || firstAvailableMode();
    if(widget.analysis_mode === 'tendencia'){
      widget.date_column = el('chartTrendDateCol').value;
      widget.y_column = el('chartTrendYCol').value;
      widget.aggregation = el('chartTrendAggType').value;
      widget.chart_type = el('chartTrendChartType').value;
      widget.date_granularity = el('chartTrendGranularity').value;
    }else if(widget.analysis_mode === 'composicion'){
      widget.x_column = el('chartCompXCol').value;
      widget.y_column = el('chartCompYCol').value;
      widget.aggregation = el('chartCompAggType').value;
      widget.chart_type = el('chartCompChartType').value;
      widget.top_n = positiveInt('chartCompTopN', CFG.defaults.top_n || 12, 1, 1000);
    }else if(widget.analysis_mode === 'scatter'){
      widget.x_column = el('chartScatterXCol').value;
      widget.y_column = el('chartScatterYCol').value;
      widget.chart_type = 'scatter';
      widget.point_limit = positiveInt('chartScatterPointLimit', CFG.defaults.point_limit || 150, 1, 5000);
    }else{
      widget.analysis_mode = 'categorias';
      widget.x_column = el('chartCatXCol').value;
      widget.y_column = el('chartCatYCol').value;
      widget.aggregation = el('chartCatAggType').value;
      widget.chart_type = el('chartCatChartType').value;
      widget.top_n = positiveInt('chartCatTopN', CFG.defaults.top_n || 12, 1, 1000);
    }
    widget.filters = ownerFilters('chart');
  }
  upsertWidget(widget);
  if(skipRender){ return; }
  renderWireframe();
  renderAll();
}

function syncActiveTemplateFromForm(){
  var template = activeTemplate();
  if(!template){ return; }
  template.name = trimText(el('templateName').value);
  template.title = trimText(el('templateTitle').value);
  template.description = trimText(el('templateDesc').value);
}

function syncDateRangeFromForm(){
  var template = activeTemplate();
  if(!template){ return; }
  if(!template.date_range){ template.date_range = {enabled:false,column:defaultDateColumn(),start:'',end:''}; }
  template.date_range.enabled = !!el('dateEnabled').checked;
  template.date_range.column = el('filterDateCol').value;
  template.date_range.start = trimText(el('dateStart').value);
  template.date_range.end = trimText(el('dateEnd').value);
}

function syncBuilderStateBeforeExport(){
  syncActiveTemplateFromForm();
  syncDateRangeFromForm();
  syncSelectedWidgetFromForm(true);
}

/* === FILTROS GLOBALES Y LOCALES === */
function ownerFilters(owner){
  var template = activeTemplate();
  var widget = selectedWidget();
  if(owner === 'global'){
    if(!template.global_filters){ template.global_filters = []; }
    return template.global_filters;
  }
  if(!widget || widget.type !== owner){ return []; }
  if(!widget.filters){ widget.filters = []; }
  return widget.filters;
}
function operatorOptions(typeKey){
  if(typeKey === 'number'){
    return [{value:'eq',label:'Igual a'},{value:'neq',label:'Distinto de'},{value:'gt',label:'Mayor que'},{value:'gte',label:'Mayor o igual'},{value:'lt',label:'Menor que'},{value:'lte',label:'Menor o igual'}];
  }
  if(typeKey === 'date'){
    return [{value:'eq',label:'Igual a'},{value:'gte',label:'Desde'},{value:'lte',label:'Hasta'}];
  }
  return [{value:'eq',label:'Es exactamente'},{value:'neq',label:'No es'},{value:'contains',label:'Contiene'}];
}
function operatorLabel(op){ var labels = {eq:'=',neq:'!=',contains:'contiene',gt:'>',gte:'>=',lt:'<',lte:'<='}; return labels[op] || op; }
function optionHtml(items, selectedValue){
  var html = '';
  var i, item, value, label;
  for(i = 0; i < items.length; i++){
    item = items[i];
    value = typeof item === 'string' ? item : item.value;
    label = typeof item === 'string' ? item : item.label;
    html += '<option value="' + esc(value) + '"' + (String(value) === String(selectedValue) ? ' selected' : '') + '>' + esc(label) + '</option>';
  }
  return html;
}
function filterRowHtml(owner, idx, filterDef){
  var column = filterDef.column || '';
  var typeKey = column ? (META.column_types[column] || 'text') : 'text';
  var distinct = column ? META.distinct_values[column] : null;
  var html = '<div class="filter-card"><div class="filter-head"><span class="filter-name">Filtro</span><button type="button" class="btn-danger filter-remove" data-owner="' + esc(owner) + '" data-idx="' + idx + '">Quitar</button></div><div class="filter-grid">';
  html += '<div><label>Columna</label><select class="filter-col" data-owner="' + esc(owner) + '" data-idx="' + idx + '"><option value="">Selecciona una columna</option>' + optionHtml(META.all_columns, column) + '</select></div>';
  html += '<div><label>Operador</label><select class="filter-op" data-owner="' + esc(owner) + '" data-idx="' + idx + '">' + optionHtml(operatorOptions(typeKey), filterDef.operator || 'eq') + '</select></div><div><label>Valor</label><div class="value-shell">';
  if(distinct && distinct.length){
    html += '<select class="filter-value-select" data-owner="' + esc(owner) + '" data-idx="' + idx + '"><option value="">Selecciona un valor</option>' + optionHtml(distinct, filterDef.value) + '</select>';
  }else{
    html += '<input class="filter-value-input" data-owner="' + esc(owner) + '" data-idx="' + idx + '" type="text" value="' + esc(filterDef.value || '') + '"' + (typeKey === 'date' ? ' placeholder="dd/mm/aaaa"' : '') + '>';
  }
  html += '</div></div></div></div>';
  return html;
}
function renderFilterEditor(owner, boxId){
  var filters = ownerFilters(owner);
  var html = '';
  var i;
  if(!filters.length){ el(boxId).innerHTML = '<div class="empty">No hay filtros configurados.</div>'; return; }
  for(i = 0; i < filters.length; i++){ html += filterRowHtml(owner, i, filters[i]); }
  el(boxId).innerHTML = html;
  bindFilterEditor(owner, boxId);
}
function bindFilterEditor(owner, boxId){
  var box = el(boxId);
  var cols = box.querySelectorAll('.filter-col');
  var ops = box.querySelectorAll('.filter-op');
  var inputs = box.querySelectorAll('.filter-value-input');
  var selects = box.querySelectorAll('.filter-value-select');
  var removeBtns = box.querySelectorAll('.filter-remove');
  var i;
  for(i = 0; i < cols.length; i++){
    cols[i].onchange = function(){ var list = ownerFilters(owner); var idx = parseInt(this.getAttribute('data-idx'), 10); list[idx].column = this.value; list[idx].operator = 'eq'; list[idx].value = ''; rerenderFilterOwner(owner); renderAll(); };
  }
  for(i = 0; i < ops.length; i++){
    ops[i].onchange = function(){ var list = ownerFilters(owner); var idx = parseInt(this.getAttribute('data-idx'), 10); list[idx].operator = this.value; renderAll(); };
  }
  for(i = 0; i < inputs.length; i++){
    inputs[i].onkeyup = function(){ var list = ownerFilters(owner); var idx = parseInt(this.getAttribute('data-idx'), 10); list[idx].value = this.value; renderAll(); };
    inputs[i].onchange = inputs[i].onkeyup;
  }
  for(i = 0; i < selects.length; i++){
    selects[i].onchange = function(){ var list = ownerFilters(owner); var idx = parseInt(this.getAttribute('data-idx'), 10); list[idx].value = this.value; renderAll(); };
  }
  for(i = 0; i < removeBtns.length; i++){
    removeBtns[i].onclick = function(){ var list = ownerFilters(owner); var idx = parseInt(this.getAttribute('data-idx'), 10); list.splice(idx, 1); rerenderFilterOwner(owner); renderAll(); };
  }
}
function rerenderFilterOwner(owner){
  if(owner === 'global'){ renderGlobalFilterEditor(); }
  else if(owner === 'table'){ renderTableEditor(selectedWidget()); }
  else if(owner === 'chart'){ renderChartEditor(selectedWidget()); }
}
function addFilter(owner){ ownerFilters(owner).push({column:'',operator:'eq',value:''}); rerenderFilterOwner(owner); renderAll(); }
function renderGlobalFilterEditor(){ renderFilterEditor('global', 'globalFiltersBox'); }
function validateDateRange(){
  var template = activeTemplate();
  var dateState = template.date_range || {enabled:false,column:'',start:'',end:''};
  var startDate = dateState.start ? dInput(dateState.start, false) : null;
  var endDate = dateState.end ? dInput(dateState.end, true) : null;
  var msg = '';
  el('dateStart').className = startDate || !trimText(dateState.start) ? '' : 'input-error';
  el('dateEnd').className = endDate || !trimText(dateState.end) ? '' : 'input-error';
  if(!dateState.enabled){ msg = 'Rango global inactivo.'; }
  else if((trimText(dateState.start) && !startDate) || (trimText(dateState.end) && !endDate)){ msg = 'Usa el formato dd/mm/aaaa.'; }
  else if(startDate && endDate && startDate.getTime() > endDate.getTime()){ msg = 'La fecha inicial no puede ser mayor a la final.'; }
  else if(dateState.column){ msg = 'Filtrando por ' + dateState.column + '.'; }
  el('dateHint').textContent = msg;
  return {enabled:!!dateState.enabled,column:dateState.column,start_text:dateState.start || '',end_text:dateState.end || '',start_date:startDate,end_date:endDate};
}
function compareDates(leftDate, rightDate, op){
  var lv = leftDate.getTime();
  var rv = rightDate.getTime();
  if(op === 'eq'){ return lv === rv; }
  if(op === 'gt'){ return lv > rv; }
  if(op === 'gte'){ return lv >= rv; }
  if(op === 'lt'){ return lv < rv; }
  if(op === 'lte'){ return lv <= rv; }
  if(op === 'neq'){ return lv !== rv; }
  return true;
}
function okFilter(cellValue, filterDef){
  var typeKey = META.column_types[filterDef.column] || 'text';
  var leftText = trimText(cellValue);
  var rightText = trimText(filterDef.value);
  var leftNum, rightNum, leftDate, rightDate;
  if(typeKey === 'number'){
    leftNum = n(cellValue); rightNum = n(filterDef.value);
    if(rightNum == null){ return true; }
    if(leftNum == null){ return false; }
    if(filterDef.operator === 'eq'){ return leftNum === rightNum; }
    if(filterDef.operator === 'neq'){ return leftNum !== rightNum; }
    if(filterDef.operator === 'gt'){ return leftNum > rightNum; }
    if(filterDef.operator === 'gte'){ return leftNum >= rightNum; }
    if(filterDef.operator === 'lt'){ return leftNum < rightNum; }
    if(filterDef.operator === 'lte'){ return leftNum <= rightNum; }
    return true;
  }
  if(typeKey === 'date'){
    leftDate = d(cellValue);
    rightDate = d(filterDef.value) || dInput(filterDef.value, filterDef.operator === 'lte');
    if(!rightDate){ return true; }
    if(!leftDate){ return false; }
    return compareDates(leftDate, rightDate, filterDef.operator);
  }
  if(filterDef.operator === 'contains'){ return lower(leftText).indexOf(lower(rightText)) >= 0; }
  if(filterDef.operator === 'neq'){ return lower(leftText) !== lower(rightText); }
  return lower(leftText) === lower(rightText);
}
function applyFilters(rows, filters){
  var out = [];
  var i, j, row, ok, fs;
  fs = arr(filters);
  if(!fs.length){ return rows.slice(); }
  for(i = 0; i < rows.length; i++){
    row = rows[i]; ok = true;
    for(j = 0; j < fs.length; j++){
      if(!fs[j].column || trimText(fs[j].value) === ''){ continue; }
      if(!okFilter(row[fs[j].column], fs[j])){ ok = false; break; }
    }
    if(ok){ out.push(row); }
  }
  return out;
}
function filteredState(){
  var template = activeTemplate();
  var fs = arr(template.global_filters);
  var dateState = validateDateRange();
  var out = [];
  var i, j, row, rowDate, ok;
  for(i = 0; i < DATA.length; i++){
    row = DATA[i]; ok = true;
    if(dateState.enabled && dateState.column && (dateState.start_text || dateState.end_text)){
      rowDate = d(row[dateState.column]);
      if(!rowDate){ ok = false; }
      else{
        if(dateState.start_date && rowDate.getTime() < dateState.start_date.getTime()){ ok = false; }
        if(dateState.end_date && rowDate.getTime() > dateState.end_date.getTime()){ ok = false; }
      }
    }
    if(!ok){ continue; }
    for(j = 0; j < fs.length; j++){
      if(!fs[j].column || trimText(fs[j].value) === ''){ continue; }
      if(!okFilter(row[fs[j].column], fs[j])){ ok = false; break; }
    }
    if(ok){ out.push(row); }
  }
  return {rows:out,filters:fs,date_filter:dateState};
}
function renderActiveFilters(stateObj){
  var chips = [];
  var heroHtml = '';
  var panelHtml = '';
  var i, filterItem, count;
  if(stateObj.date_filter.enabled && stateObj.date_filter.column && (stateObj.date_filter.start_text || stateObj.date_filter.end_text)){
    chips.push({text:stateObj.date_filter.column + ': ' + (stateObj.date_filter.start_text || '...') + ' a ' + (stateObj.date_filter.end_text || '...')});
  }
  for(i = 0; i < stateObj.filters.length; i++){
    filterItem = stateObj.filters[i];
    if(filterItem.column && trimText(filterItem.value) !== ''){ chips.push({text:filterItem.column + ' ' + operatorLabel(filterItem.operator) + ' ' + filterItem.value}); }
  }
  if(!chips.length){
    heroHtml = '<span class="chip soft">Sin filtros globales activos</span>';
    panelHtml = '<div class="empty">Todavia no hay filtros globales activos para la plantilla.</div>';
    count = 0;
  }else{
    for(i = 0; i < chips.length; i++){
      heroHtml += '<span class="chip">' + esc(chips[i].text) + '</span>';
      panelHtml += '<span class="chip">' + esc(chips[i].text) + '</span>';
    }
    count = chips.length;
  }
  el('chips').innerHTML = heroHtml;
  el('builderChips').innerHTML = panelHtml;
  el('activeFilterMeta').textContent = count ? count + ' activo(s)' : 'Sin filtros';
}

/* === AGREGACIONES === */
function kpiValue(rows, def){
  var vals = [];
  var i, x, sum, minVal, maxVal;
  if(def.aggregation === 'count'){ return rows.length; }
  for(i = 0; i < rows.length; i++){
    x = n(rows[i][def.column]);
    if(x != null){ vals.push(x); }
  }
  if(!vals.length){ return 0; }
  sum = 0; minVal = vals[0]; maxVal = vals[0];
  for(i = 0; i < vals.length; i++){
    sum += vals[i];
    if(vals[i] < minVal){ minVal = vals[i]; }
    if(vals[i] > maxVal){ maxVal = vals[i]; }
  }
  if(def.aggregation === 'sum'){ return sum; }
  if(def.aggregation === 'avg'){ return sum / vals.length; }
  if(def.aggregation === 'min'){ return minVal; }
  if(def.aggregation === 'max'){ return maxVal; }
  return sum;
}
function updateBucket(bucket, value){
  bucket.count += 1;
  if(value != null){
    bucket.sum += value;
    bucket.num += 1;
    if(bucket.min == null || value < bucket.min){ bucket.min = value; }
    if(bucket.max == null || value > bucket.max){ bucket.max = value; }
  }
}
function bucketValue(bucket, aggregation){
  if(aggregation === 'count'){ return bucket.count; }
  if(aggregation === 'sum'){ return bucket.num ? bucket.sum : 0; }
  if(aggregation === 'avg'){ return bucket.num ? bucket.sum / bucket.num : 0; }
  if(aggregation === 'min'){ return bucket.num ? bucket.min : 0; }
  if(aggregation === 'max'){ return bucket.num ? bucket.max : 0; }
  return bucket.count;
}
function newBucket(label, sortValue){ return {label:label,sort_value:sortValue,count:0,sum:0,min:null,max:null,num:0}; }
function pluck(items, key){ var out = []; var i; for(i = 0; i < items.length; i++){ out.push(items[i][key]); } return out; }
function buildCategorical(rows, cfg){
  var map = {};
  var list = [];
  var i, key, rawKey, bucket, val, item;
  for(i = 0; i < rows.length; i++){
    rawKey = cfg.x_column ? rows[i][cfg.x_column] : 'Total';
    key = rawKey == null || rawKey === '' ? '(Sin valor)' : String(rawKey);
    if(!map[key]){ map[key] = newBucket(key, key); }
    bucket = map[key];
    val = cfg.aggregation === 'count' ? null : n(rows[i][cfg.y_column]);
    updateBucket(bucket, val);
  }
  for(key in map){ if(Object.prototype.hasOwnProperty.call(map, key)){ bucket = map[key]; item = {label:bucket.label,value:bucketValue(bucket, cfg.aggregation),count:bucket.count}; list.push(item); } }
  list.sort(function(a, b){ return b.value - a.value; });
  if(cfg.top_n && list.length > cfg.top_n){ list = list.slice(0, cfg.top_n); }
  return {type:cfg.chart_type,mode:cfg.analysis_mode,x_label:cfg.x_column || 'Grupo',value_label:cfg.aggregation === 'count' ? 'Conteo' : (cfg.y_column || 'Valor'),aggregation:cfg.aggregation,labels:pluck(list, 'label'),values:pluck(list, 'value'),rows:list,points:[]};
}
function dateBucket(dateObj, granularity){
  var year = dateObj.getFullYear();
  var month = dateObj.getMonth() + 1;
  var day = dateObj.getDate();
  var key = String(year);
  var label = String(year);
  var sortValue = year * 10000;
  if(granularity === 'month'){ key = year + '-' + pad(month); label = pad(month) + '/' + year; sortValue = year * 100 + month; }
  else if(granularity === 'day'){ key = year + '-' + pad(month) + '-' + pad(day); label = pad(day) + '/' + pad(month) + '/' + year; sortValue = year * 10000 + month * 100 + day; }
  return {key:key,label:label,sort_value:sortValue};
}
function buildTrend(rows, cfg){
  var map = {};
  var list = [];
  var i, parsedDate, bucketKey, bucket, val, key, item;
  for(i = 0; i < rows.length; i++){
    parsedDate = d(rows[i][cfg.date_column]);
    if(!parsedDate){ continue; }
    bucketKey = dateBucket(parsedDate, cfg.date_granularity);
    if(!map[bucketKey.key]){ map[bucketKey.key] = newBucket(bucketKey.label, bucketKey.sort_value); }
    bucket = map[bucketKey.key];
    val = cfg.aggregation === 'count' ? null : n(rows[i][cfg.y_column]);
    updateBucket(bucket, val);
  }
  for(key in map){ if(Object.prototype.hasOwnProperty.call(map, key)){ bucket = map[key]; item = {label:bucket.label,value:bucketValue(bucket, cfg.aggregation),count:bucket.count,sort_value:bucket.sort_value}; list.push(item); } }
  list.sort(function(a, b){ return a.sort_value - b.sort_value; });
  return {type:cfg.chart_type,mode:cfg.analysis_mode,x_label:cfg.date_column,value_label:cfg.aggregation === 'count' ? 'Conteo' : (cfg.y_column || 'Valor'),aggregation:cfg.aggregation,labels:pluck(list, 'label'),values:pluck(list, 'value'),rows:list,points:[]};
}
function buildScatter(rows, cfg){
  var points = [];
  var i, px, py;
  for(i = 0; i < rows.length; i++){
    px = n(rows[i][cfg.x_column]);
    py = n(rows[i][cfg.y_column]);
    if(px != null && py != null){ points.push({x:px,y:py}); }
  }
  points.sort(function(a, b){ return a.x - b.x; });
  if(cfg.point_limit && points.length > cfg.point_limit){ points = points.slice(0, cfg.point_limit); }
  return {type:'scatter',mode:'scatter',x_label:cfg.x_column,value_label:cfg.y_column,aggregation:'scatter',labels:[],values:[],rows:[],points:points};
}
function analyzeChart(rows, widget){
  if(widget.analysis_mode === 'scatter'){ return buildScatter(rows, widget); }
  if(widget.analysis_mode === 'tendencia'){ return buildTrend(rows, widget); }
  return buildCategorical(rows, widget);
}
function palette(size){ var base = ['#2563eb','#0f766e','#9333ea','#ea580c','#dc2626','#0891b2','#4f46e5','#65a30d','#d97706','#be123c']; var out = []; var i; for(i = 0; i < size; i++){ out.push(base[i % base.length]); } return out; }
function sortRowsForTable(rows, sortBy, sortDir){
  var copy = rows.slice();
  var typeKey = META.column_types[sortBy] || 'text';
  if(!sortBy){ return copy; }
  copy.sort(function(a, b){
    var left = a[sortBy];
    var right = b[sortBy];
    var lv, rv;
    if(typeKey === 'number'){ lv = n(left); rv = n(right); lv = lv == null ? 0 : lv; rv = rv == null ? 0 : rv; return sortDir === 'asc' ? lv - rv : rv - lv; }
    if(typeKey === 'date'){ lv = d(left); rv = d(right); lv = lv ? lv.getTime() : 0; rv = rv ? rv.getTime() : 0; return sortDir === 'asc' ? lv - rv : rv - lv; }
    lv = lower(left); rv = lower(right);
    if(lv === rv){ return 0; }
    if(sortDir === 'asc'){ return lv > rv ? 1 : -1; }
    return lv < rv ? 1 : -1;
  });
  return copy;
}
function widgetRows(globalRows, widget){ if(widget.type === 'kpi'){ return globalRows; } return applyFilters(globalRows, arr(widget.filters)); }

/* === RENDER DE WIDGETS === */
function renderKpiCard(widget, rows){
  var value = kpiValue(rows, widget);
  var format = widget.format || (widget.aggregation === 'count' ? 'integer' : 'number');
  var color = widget.accent_color || '#1d4ed8';
  return '<section class="dashboard-cell"><div class="widget-card kpi-card"><span class="accent" style="background:' + esc(color) + '"></span><span class="kpi-label">' + esc(widget.title || 'Indicador') + '</span><span class="kpi-value">' + esc(fmt(value, format)) + '</span><div class="widget-desc">' + esc(String(widget.aggregation || 'sum').toUpperCase()) + (widget.column ? ' de ' + widget.column : '') + '</div></div></section>';
}
function renderTableCard(widget, rows){
  var localRows = widgetRows(rows, widget);
  var sortedRows = sortRowsForTable(localRows, widget.sort_by || '', widget.sort_dir || 'desc');
  var cols = arr(widget.columns).length ? widget.columns : defaultTableColumns();
  var limit = positiveIntValue(widget.limit, CFG.defaults.table_limit || 50, 1, 100000);
  var show = sortedRows.slice(0, limit);
  var html = '<section class="dashboard-cell"><div class="widget-card"><div class="head"><h2 class="widget-title">' + esc(widget.title || 'Tabla') + '</h2><span class="widget-meta">' + show.length + ' de ' + sortedRows.length + ' fila(s)</span></div>';
  var r, c;
  if(!cols.length){ return html + '<div class="empty">Selecciona al menos una columna para esta tabla.</div></div></section>'; }
  if(!sortedRows.length){ return html + '<div class="empty">No hay filas para los filtros aplicados.</div></div></section>'; }
  html += '<div class="table-wrap"><table><thead><tr>';
  for(c = 0; c < cols.length; c++){ html += '<th>' + esc(cols[c]) + '</th>'; }
  html += '</tr></thead><tbody>';
  for(r = 0; r < show.length; r++){
    html += '<tr>';
    for(c = 0; c < cols.length; c++){ html += '<td>' + esc(show[r][cols[c]]) + '</td>'; }
    html += '</tr>';
  }
  html += '</tbody></table></div></div></section>';
  return html;
}
function renderChartCard(widget, rows, chartJobs){
  var localRows = widgetRows(rows, widget);
  var analysis = analyzeChart(localRows, widget);
  var canvasId = 'chartCanvas_' + widget.id;
  var html = '<section class="dashboard-cell"><div class="widget-card"><div class="head"><h2 class="widget-title">' + esc(widget.title || 'Grafica') + '</h2><span class="widget-meta">' + esc(modeLabel(widget.analysis_mode || 'categorias')) + '</span></div>';
  if((analysis.type === 'scatter' && !analysis.points.length) || (analysis.type !== 'scatter' && !analysis.labels.length)){
    return html + '<div class="empty">No hay datos para esta grafica.</div></div></section>';
  }
  html += '<div class="widget-desc">' + esc((widget.filters && widget.filters.length ? widget.filters.length + ' filtro(s) local(es).' : 'Sin filtros locales.') + ' ' + localRows.length + ' fila(s) consideradas.') + '</div>';
  html += '<div class="canvas-wrap"><canvas id="' + esc(canvasId) + '"></canvas></div></div></section>';
  chartJobs.push({widget_id:widget.id,canvas_id:canvasId,analysis:analysis});
  return html;
}
function drawChart(job){
  var canvas = el(job.canvas_id);
  var ar = job.analysis;
  var colors, data, labelText, ctx;
  if(!canvas){ return; }
  ctx = canvas.getContext('2d');
  colors = palette(ar.type === 'scatter' ? ar.points.length : ar.labels.length);
  labelText = ar.type === 'scatter' ? (ar.value_label + ' vs ' + ar.x_label) : (String(ar.aggregation).toUpperCase() + (ar.value_label ? ' de ' + ar.value_label : ''));
  data = ar.type === 'scatter'
    ? {datasets:[{label:labelText,data:ar.points,backgroundColor:'rgba(37,99,235,.55)',borderColor:'#1d4ed8',pointRadius:5}]}
    : {labels:ar.labels,datasets:[{label:labelText,data:ar.values,backgroundColor:ar.type === 'line' ? 'rgba(37,99,235,.15)' : colors,borderColor:ar.type === 'line' ? '#1d4ed8' : colors,borderWidth:2,fill:false}]};
  CHARTS[job.widget_id] = new Chart(ctx,{type:ar.type,data:data,options:{responsive:true,maintainAspectRatio:false,legend:{labels:{fontColor:'#334155'}},scales:(ar.type === 'pie' || ar.type === 'doughnut' || ar.type === 'polarArea') ? {} : {xAxes:[{ticks:{fontColor:'#334155'},gridLines:{color:'rgba(148,163,184,.12)'}}],yAxes:[{ticks:{beginAtZero:true,fontColor:'#334155'},gridLines:{color:'rgba(148,163,184,.16)'}}]}}});
}
function renderDashboard(){
  var template = activeTemplate();
  var stateObj = filteredState();
  var rows = stateObj.rows;
  var html = '';
  var chartJobs = [];
  var rowDefs = template && template.layout ? template.layout.rows : [];
  var i, j, row, widget;
  destroyCharts();
  renderActiveFilters(stateObj);
  if(!template){ el('dashboardRows').innerHTML = '<section class="card"><div class="empty">No hay plantilla activa.</div></section>'; return; }
  el('dashboardTitle').textContent = trimText(template.title) || CFG.title;
  el('dashboardDesc').textContent = trimText(template.description) || CFG.description;
  var sourceCount = META && META.source_row_count != null ? META.source_row_count : null;
  var selectedCount = META && META.selected_row_count != null ? META.selected_row_count : (META ? META.row_count : null);
  var scope = META && META.query_scope_applied ? META.query_scope_applied : {};
  var metaText = rows.length + ' fila(s) despues de filtros globales';
  if(sourceCount != null && selectedCount != null){
    metaText += ' · origen ' + fmt(sourceCount, 'integer') + ' -> cargadas ' + fmt(selectedCount, 'integer');
  }
  if(scope && (scope.start || scope.end)){
    metaText += ' · rango ' + (scope.start || '...') + ' a ' + (scope.end || '...');
  }
  if(META && META.cache_hit === true){ metaText += ' · cache sqlite'; }
  else if(META && META.cache_hit === false){ metaText += ' · sin cache'; }
  el('dashboardMeta').textContent = metaText;
  for(i = 0; i < rowDefs.length; i++){
    row = rowDefs[i];
    html += '<section class="dashboard-row">';
    for(j = 0; j < row.cells.length; j++){
      widget = widgetByCell(template, row.cells[j].id);
      if(!widget){ html += '<section class="dashboard-cell"><div class="widget-card"><div class="empty">Sin configurar</div></div></section>'; }
      else if(widget.type === 'kpi'){ html += renderKpiCard(widget, rows); }
      else if(widget.type === 'table'){ html += renderTableCard(widget, rows); }
      else{ html += renderChartCard(widget, rows, chartJobs); }
    }
    html += '</section>';
  }
  if(!html){ html = '<section class="card"><div class="empty">La plantilla no tiene layout definido.</div></section>'; }
  el('dashboardRows').innerHTML = html;
  for(i = 0; i < chartJobs.length; i++){ drawChart(chartJobs[i]); }
}

/* === HERO, TABLA FUENTE Y EXPORTACION === */
function renderSourceTable(){
  var cols = META.all_columns;
  var search = lower(el('srcSearch').value);
  var limit = positiveInt('srcLimit', 250, 1, 100000);
  var rows = [];
  var i, j, text, match, show, html;
  for(i = 0; i < DATA.length; i++){
    if(!search){ rows.push(DATA[i]); continue; }
    match = false;
    for(j = 0; j < cols.length; j++){
      text = lower(DATA[i][cols[j]]);
      if(text.indexOf(search) >= 0){ match = true; break; }
    }
    if(match){ rows.push(DATA[i]); }
  }
  if(!rows.length){ el('sourceTableBox').innerHTML = '<div class="empty">No hay filas para el filtro aplicado.</div>'; el('sourceMeta').textContent = '0 fila(s)'; return; }
  show = rows.slice(0, limit);
  html = '<table><thead><tr>';
  for(i = 0; i < cols.length; i++){ html += '<th>' + esc(cols[i]) + '</th>'; }
  html += '</tr></thead><tbody>';
  for(i = 0; i < show.length; i++){
    html += '<tr>';
    for(j = 0; j < cols.length; j++){ html += '<td>' + esc(show[i][cols[j]]) + '</td>'; }
    html += '</tr>';
  }
  html += '</tbody></table>';
  el('sourceTableBox').innerHTML = html;
  el('sourceMeta').textContent = show.length + ' de ' + rows.length + ' fila(s)';
}
function serializeFilters(filters){
  var source = arr(filters);
  var out = [];
  var i, item;
  for(i = 0; i < source.length; i++){
    item = source[i] || {};
    out.push({
      column:item.column || '',
      operator:item.operator || 'eq',
      value:item.value == null ? '' : item.value
    });
  }
  return out;
}
function serializeDateRange(dateRange){
  var item = dateRange || {};
  return {
    enabled:!!item.enabled,
    column:item.column || defaultDateColumn(),
    start:item.start || '',
    end:item.end || ''
  };
}
function serializeLayout(layout){
  var rows = layout && layout.rows ? layout.rows : [];
  var outRows = [];
  var i, j, row, cells, cellOut, totalCols;
  for(i = 0; i < rows.length; i++){
    row = rows[i] || {};
    cells = arr(row.cells);
    totalCols = positiveIntValue(row.columns, cells.length || 1, 1, 4);
    cellOut = [];
    for(j = 0; j < cells.length; j++){ cellOut.push({id:(cells[j] && cells[j].id) ? cells[j].id : ('cell_' + (i + 1) + '_' + (j + 1))}); }
    if(!cellOut.length){
      for(j = 1; j <= totalCols; j++){ cellOut.push({id:'cell_' + (i + 1) + '_' + j}); }
    }
    outRows.push({
      id:row.id || ('row_' + (i + 1)),
      columns:totalCols,
      cells:cellOut
    });
  }
  if(!outRows.length){ return buildLayout([1]); }
  return {rows:outRows};
}
function serializeWidget(widget){
  var item = widget || {};
  var modeKey = item.analysis_mode || firstAvailableMode();
  var out = {
    id:item.id || nextId(item.type || 'widget'),
    cell_id:item.cell_id || '',
    type:item.type || 'chart'
  };
  if(item.type === 'kpi'){
    out.title = trimText(item.title) || 'KPI';
    out.column = item.column || defaultYColumn();
    out.aggregation = item.aggregation || (META.numeric_columns.length ? (CFG.defaults.aggregation || 'sum') : 'count');
    out.format = item.format || (out.aggregation === 'count' ? 'integer' : 'number');
    out.accent_color = item.accent_color || colorOptions()[0].value;
    if(Object.prototype.hasOwnProperty.call(item, 'filters')){ out.filters = serializeFilters(item.filters); }
    return out;
  }
  if(item.type === 'table'){
    out.title = trimText(item.title) || 'Tabla';
    out.columns = arr(item.columns).length ? arr(item.columns).slice() : defaultTableColumns();
    out.limit = positiveIntValue(item.limit, CFG.defaults.table_limit || 50, 1, 100000);
    out.sort_by = item.sort_by || '';
    out.sort_dir = item.sort_dir || CFG.defaults.sort_dir || 'desc';
    out.filters = serializeFilters(item.filters);
    return out;
  }
  out.title = trimText(item.title) || 'Grafica';
  out.analysis_mode = modeKey;
  out.chart_type = modeKey === 'scatter' ? 'scatter' : modeChartType(modeKey, item.chart_type || CFG.defaults.chart_type || 'bar');
  out.x_column = item.x_column || defaultXColumn();
  out.y_column = item.y_column || defaultYColumn();
  out.date_column = item.date_column || defaultDateColumn();
  out.aggregation = item.aggregation || CFG.defaults.aggregation || 'sum';
  out.date_granularity = item.date_granularity || CFG.defaults.date_granularity || 'day';
  out.top_n = positiveIntValue(item.top_n, CFG.defaults.top_n || 12, 1, 1000);
  out.point_limit = positiveIntValue(item.point_limit, CFG.defaults.point_limit || 150, 1, 5000);
  out.filters = serializeFilters(item.filters);
  return out;
}
function serializeTemplate(template){
  var item = template || {};
  var layout = serializeLayout(item.layout || buildLayout([1]));
  var cellIds = layoutCellIds(layout);
  var widgets = [];
  var i, widget;
  for(i = 0; i < cellIds.length; i++){
    widget = widgetByCell(item, cellIds[i]);
    if(widget){ widgets.push(serializeWidget(widget)); }
  }
  return {
    id:item.id || uniqueTemplateId(item.name || 'plantilla'),
    name:trimText(item.name) || 'Plantilla',
    title:trimText(item.title) || trimText(item.name) || CFG.title,
    description:trimText(item.description) || '',
    layout:layout,
    widgets:widgets,
    global_filters:serializeFilters(item.global_filters),
    date_range:serializeDateRange(item.date_range)
  };
}
function serializeDefaults(){
  var defaults = CFG.defaults || {};
  return {
    analysis_mode:defaults.analysis_mode || 'categorias',
    x_column:defaults.x_column || '',
    y_column:defaults.y_column || '',
    date_column:defaults.date_column || '',
    aggregation:defaults.aggregation || 'sum',
    chart_type:defaults.chart_type || 'bar',
    date_granularity:defaults.date_granularity || 'day',
    top_n:positiveIntValue(defaults.top_n, 12, 1, 1000),
    point_limit:positiveIntValue(defaults.point_limit, 150, 1, 5000),
    table_limit:positiveIntValue(defaults.table_limit, 50, 1, 100000),
    sort_dir:defaults.sort_dir || 'desc'
  };
}
function serializeDashboard(){
  var shell = CFG.dashboard_shell || {};
  var out = {
    title:shell.title || CFG.title,
    description:shell.description || CFG.description,
    allow_user_builder:shell.allow_user_builder == null ? !!CFG.allow_user_builder : !!shell.allow_user_builder,
    runtime:deepClone(CFG.runtime || runtimeDefaults()),
    defaults:serializeDefaults(),
    active_template_id:STATE.active_template_id || (STATE.templates.length ? STATE.templates[0].id : ''),
    templates:[]
  };
  var i;
  for(i = 0; i < STATE.templates.length; i++){ out.templates.push(serializeTemplate(STATE.templates[i])); }
  return out;
}
function serializeConfig(){
  var base = CFG.export_base_config || {};
  var out = {
    version:base.version == null ? 1 : base.version,
    ui:deepClone(base.ui || {}),
    csv_options:deepClone(base.csv_options || {}),
    dashboard:serializeDashboard()
  };
  var key;
  for(key in base){
    if(!Object.prototype.hasOwnProperty.call(base, key)){ continue; }
    if(key === 'version' || key === 'ui' || key === 'csv_options' || key === 'dashboard'){ continue; }
    out[key] = deepClone(base[key]);
  }
  return out;
}
function buildDashboardExport(){ return serializeDashboard(); }
function updateExportBox(forceSync){
  var out;
  if(forceSync){
    syncBuilderStateBeforeExport();
    STATE.last_export_stamp = clockStamp();
  }
  out = serializeConfig();
  el('exportText').value = JSON.stringify(out, null, 2);
  el('exportMeta').textContent = STATE.last_export_stamp
    ? ('Actualizado ' + STATE.last_export_stamp + ' · ' + STATE.templates.length + ' plantilla(s)')
    : (STATE.templates.length + ' plantilla(s) listas para exportar');
}

/* === RENDER GENERAL === */
function renderHero(){
  var template = activeTemplate();
  el('heroTitle').textContent = template ? (trimText(template.title) || CFG.title) : CFG.title;
  el('heroDesc').textContent = template ? (trimText(template.description) || CFG.description) : CFG.description;
}
function renderAll(){
  var template = activeTemplate();
  renderHero();
  if(template){
    el('dateEnabled').checked = !!template.date_range.enabled;
    fillSelect('filterDateCol', META.date_columns, template.date_range.column || defaultDateColumn(), null);
    el('dateStart').value = template.date_range.start || '';
    el('dateEnd').value = template.date_range.end || '';
    el('dateRangeMeta').textContent = META.date_columns.length ? META.date_columns.length + ' columna(s) de fecha' : 'Sin columnas de fecha';
  }
  renderDashboard();
  updateExportBox();
}

/* === EVENTOS ESTATICOS === */
function bindStaticEvents(){
  el('templateSelect').onchange = function(){ setActiveTemplate(this.value); };
  el('btnNewTemplate').onclick = function(){ createBlankTemplate(); };
  el('btnCloneTemplate').onclick = function(){ cloneActiveTemplate(); };
  el('btnSyncLayoutDraft').onclick = function(){ syncDraftRowCount(); };
  el('btnApplyLayout').onclick = function(){ applyLayoutDraft(); };
  el('templateName').onkeyup = function(){ syncActiveTemplateFromForm(); renderStaticTemplateUi(); updateExportBox(); };
  el('templateName').onchange = el('templateName').onkeyup;
  el('templateTitle').onkeyup = function(){ syncActiveTemplateFromForm(); renderHero(); renderWireframe(); renderDashboard(); updateExportBox(); };
  el('templateTitle').onchange = el('templateTitle').onkeyup;
  el('templateDesc').onkeyup = function(){ syncActiveTemplateFromForm(); renderHero(); renderDashboard(); updateExportBox(); };
  el('templateDesc').onchange = el('templateDesc').onkeyup;
  bindWidgetTypeButtons();
  bindWidgetFormEvents();
  el('btnAddGlobalFilter').onclick = function(){ addFilter('global'); };
  el('btnAddTableFilter').onclick = function(){ addFilter('table'); };
  el('btnAddChartFilter').onclick = function(){ addFilter('chart'); };
  el('dateEnabled').onclick = function(){ syncDateRangeFromForm(); renderAll(); };
  el('filterDateCol').onchange = function(){ syncDateRangeFromForm(); renderAll(); };
  el('dateStart').onkeyup = function(){ syncDateRangeFromForm(); renderAll(); };
  el('dateStart').onchange = el('dateStart').onkeyup;
  el('dateEnd').onkeyup = function(){ syncDateRangeFromForm(); renderAll(); };
  el('dateEnd').onchange = el('dateEnd').onkeyup;
  el('btnRefreshExport').onclick = function(){ updateExportBox(true); };
  el('btnSelectExport').onclick = function(){ el('exportText').focus(); el('exportText').select(); };
  el('srcSearch').onkeyup = function(){ renderSourceTable(); };
  el('srcLimit').onchange = function(){ renderSourceTable(); };
}
function bindWidgetTypeButtons(){
  var nodes = el('widgetTypeChooser').getElementsByTagName('button');
  var i;
  for(i = 0; i < nodes.length; i++){ nodes[i].onclick = function(){ setWidgetType(this.getAttribute('data-type')); }; }
}
function bindWidgetInput(ids){
  var i, node;
  for(i = 0; i < ids.length; i++){
    node = el(ids[i]);
    if(node){ node.onkeyup = function(){ syncSelectedWidgetFromForm(); }; node.onchange = function(){ syncSelectedWidgetFromForm(); }; }
  }
}
function bindWidgetFormEvents(){
  bindWidgetInput(['kpiTitle','kpiColumn','kpiAggregation','kpiFormat','kpiColor']);
  bindWidgetInput(['tableWidgetTitle','tableWidgetLimit','tableWidgetSortBy','tableWidgetSortDir']);
  bindWidgetInput(['chartTitle','chartAnalysisMode','chartCatXCol','chartCatYCol','chartCatAggType','chartCatChartType','chartCatTopN','chartTrendDateCol','chartTrendGranularity','chartTrendYCol','chartTrendAggType','chartTrendChartType','chartCompXCol','chartCompYCol','chartCompAggType','chartCompChartType','chartCompTopN','chartScatterXCol','chartScatterYCol','chartScatterPointLimit']);
  el('chartAnalysisMode').onchange = function(){ syncSelectedWidgetFromForm(); renderChartEditor(selectedWidget()); };
  el('chartCatAggType').onchange = function(){ syncSelectedWidgetFromForm(); updateChartModeUi('categorias'); };
  el('chartCompAggType').onchange = function(){ syncSelectedWidgetFromForm(); updateChartModeUi('composicion'); };
}

/* === INICIALIZACION === */
function initState(){
  ensureRuntimeCfg();
  STATE.templates = deepClone(arr(CFG.templates));
  if(!STATE.templates.length){ createBlankTemplate(); return; }
  STATE.active_template_id = CFG.active_template_id || STATE.templates[0].id;
  if(!findTemplate(STATE.active_template_id)){ STATE.active_template_id = STATE.templates[0].id; }
  STATE.active_cell_id = firstCellId(activeTemplate());
  syncDraftFromActiveTemplate();
}
function startApp(){
  el('stRows').textContent = fmt(META.row_count, 'integer');
  el('stCols').textContent = fmt(META.all_columns.length, 'integer');
  el('stCsv').textContent = META.csv_name;
  bindStaticEvents();
  initState();
  renderStaticTemplateUi();
  renderWireframe();
  renderGlobalFilterEditor();
  renderWidgetEditor();
  renderSourceTable();
  renderAll();
  applyRuntimeUiMode();
}
window.onload = function(){
  var boot = typeof APP_BOOT !== 'undefined' && APP_BOOT ? APP_BOOT : {};
  var bootRuntime = normalizeRuntime(boot.runtime || null);
  var result = {ok:false,error:'No se encontro un payload utilizable.'};
  if(bootRuntime.mode === 'inline'){
    if(boot.inline_payload){ result = applyPayload(boot.inline_payload); }
  }else if(bootRuntime.mode === 'sidecar'){
    if(typeof window.DASHBOARD_PAYLOAD !== 'undefined'){ result = applyPayload(window.DASHBOARD_PAYLOAD); }
  }else{
    if(typeof window.DASHBOARD_PAYLOAD !== 'undefined'){
      result = applyPayload(window.DASHBOARD_PAYLOAD);
    }
    if(!result.ok && boot.inline_payload){
      result = applyPayload(boot.inline_payload);
    }
  }
  if(!result.ok && hasLegacyGlobals()){
    result = applyPayload({data:window.DATA,meta:window.META,cfg:window.CFG});
  }
  if(!result.ok){ showBootError(result.error); return; }
  startApp();
};
