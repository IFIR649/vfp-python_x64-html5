# Auditoría Técnica

## Resumen ejecutivo

Este repositorio implementa un dashboard local para Visual FoxPro (VFP) con una arquitectura híbrida. El flujo recomendado y actualmente más consistente es:

`VFP -> vfp_dashboard_bridge.prg -> scripts/start_backend.bat -> backend.main (FastAPI) -> backend/web -> dotnet/bridge (WebView2 COM)`

Además del flujo moderno, el repositorio conserva un pipeline legado basado en Python embebido y generación de HTML estático. Ese pipeline no debe tratarse como ruta principal de operación, pero tampoco conviene eliminarlo sin una validación funcional adicional porque todavía existen rastros de integración histórica.

## Propósito del sistema y estado actual del repo

El proyecto busca abrir un dashboard analítico desde VFP usando archivos CSV como fuente de datos y una interfaz web embebida mediante WebView2.

Estado actual observado:

- La ruta principal hoy es un backend FastAPI en `backend/` que sirve la UI en `/app` y expone endpoints JSON para sesiones, consultas de dashboard, tablas y filtros.
- La integración con VFP se realiza desde [`vfp_dashboard_bridge.prg`](/c:/sistemas/python_integracion_bloques/vfp_dashboard_bridge.prg), que apunta a `http://127.0.0.1:8766`, arranca el backend local y embebe la UI mediante el ProgID `VfpWebViewBridge.Host`.
- Existe una implementación previa en raíz basada en `main.py`, `dashboard_runtime.py` y `graficador_*.html/js`, orientada a generar HTML estático desde Python.
- El repo mezcla código fuente real, dependencias embebidas, binarios compilados, respaldos de formularios y artefactos generados.

## Arquitectura real y flujo de ejecución vigente

### Flujo moderno recomendado

1. VFP carga [`vfp_dashboard_bridge.prg`](/c:/sistemas/python_integracion_bloques/vfp_dashboard_bridge.prg).
2. El bridge configura:
   - `cBaseUrl = http://127.0.0.1:8766`
   - `cBackendBat = scripts\start_backend.bat`
   - `cWebViewProgId = VfpWebViewBridge.Host`
3. Si el backend no responde en `/health`, VFP ejecuta [`scripts/start_backend.bat`](/c:/sistemas/python_integracion_bloques/scripts/start_backend.bat).
4. Ese script levanta `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8766`.
5. VFP abre sesión con `POST /api/session/open`.
6. La UI se abre en `/app?session_id=...`.
7. El frontend en [`backend/web/app.js`](/c:/sistemas/python_integracion_bloques/backend/web/app.js) consume la API para renderizar widgets, filtros y tablas.

### Componentes del flujo moderno

- `backend/main.py`
  Servicio FastAPI y publicación de endpoints.
- `backend/engine.py`
  Motor de sesiones, lectura con Polars, filtros, agregaciones, tablas y payloads de widgets.
- `backend/legacy_config.py`
  Traducción y normalización de configuración, incluyendo el dashboard modular.
- `backend/web/`
  Frontend estático servido por FastAPI.
- `dotnet/bridge/`
  Bridge COM/WebView2 para incrustar la UI dentro de VFP.
- `scripts/start_backend.bat`
  Punto de arranque operativo más directo.
- `scripts/start_demo.ps1`
  Script auxiliar para levantar el backend y, opcionalmente, abrir un host demo.

## Inventario de componentes

### VFP

- [`vfp_dashboard_bridge.prg`](/c:/sistemas/python_integracion_bloques/vfp_dashboard_bridge.prg)
  Bridge principal entre VFP y el backend HTTP.
- [`FORMS/vista_py.scx`](/c:/sistemas/python_integracion_bloques/FORMS/vista_py.scx)
  Formulario activo parcheado para usar el bridge moderno.
- [`FORMS/vista_py.SCT`](/c:/sistemas/python_integracion_bloques/FORMS/vista_py.SCT)
  Memo asociado al formulario.
- `LIBS/`
  Recursos VFP del proyecto.

### Python moderno

- [`backend/main.py`](/c:/sistemas/python_integracion_bloques/backend/main.py)
- [`backend/engine.py`](/c:/sistemas/python_integracion_bloques/backend/engine.py)
- [`backend/legacy_config.py`](/c:/sistemas/python_integracion_bloques/backend/legacy_config.py)
- [`backend/kpi.py`](/c:/sistemas/python_integracion_bloques/backend/kpi.py)
- [`backend/graficos.py`](/c:/sistemas/python_integracion_bloques/backend/graficos.py)
- [`backend/tablas.py`](/c:/sistemas/python_integracion_bloques/backend/tablas.py)
- [`backend/requirements.txt`](/c:/sistemas/python_integracion_bloques/backend/requirements.txt)

### Frontend web

- [`backend/web/index.html`](/c:/sistemas/python_integracion_bloques/backend/web/index.html)
- [`backend/web/app.js`](/c:/sistemas/python_integracion_bloques/backend/web/app.js)
- [`backend/web/styles.css`](/c:/sistemas/python_integracion_bloques/backend/web/styles.css)
- `backend/web/vendor/chart.umd.min.js`

### .NET / WebView2

- [`dotnet/bridge/VfpWebViewBridge.csproj`](/c:/sistemas/python_integracion_bloques/dotnet/bridge/VfpWebViewBridge.csproj)
- [`dotnet/bridge/VfpWebViewBridgeHost.cs`](/c:/sistemas/python_integracion_bloques/dotnet/bridge/VfpWebViewBridgeHost.cs)
- [`dotnet/host/VfpWebViewHost.csproj`](/c:/sistemas/python_integracion_bloques/dotnet/host/VfpWebViewHost.csproj)
- [`dotnet/host/MainForm.cs`](/c:/sistemas/python_integracion_bloques/dotnet/host/MainForm.cs)

### Scripts y operación

- [`scripts/start_backend.bat`](/c:/sistemas/python_integracion_bloques/scripts/start_backend.bat)
- [`scripts/start_demo.ps1`](/c:/sistemas/python_integracion_bloques/scripts/start_demo.ps1)
- [`scripts/register_vfp_webview_bridge.ps1`](/c:/sistemas/python_integracion_bloques/scripts/register_vfp_webview_bridge.ps1)
- [`scripts/patch_vista_py_form.ps1`](/c:/sistemas/python_integracion_bloques/scripts/patch_vista_py_form.ps1)

### Configuración y datos

- [`config.json`](/c:/sistemas/python_integracion_bloques/config.json)
  Configuración principal del dashboard.
- `CONEXIONES.INI`
  Archivo de configuración sensible; no debe documentarse con valores concretos.
- `csv/`
  Carpeta de datos de ejemplo y fuentes de prueba.

### Pipeline legado en raíz

- [`main.py`](/c:/sistemas/python_integracion_bloques/main.py)
- [`dashboard_runtime.py`](/c:/sistemas/python_integracion_bloques/dashboard_runtime.py)
- [`graficador_app.js`](/c:/sistemas/python_integracion_bloques/graficador_app.js)
- [`graficador_head.html`](/c:/sistemas/python_integracion_bloques/graficador_head.html)
- [`graficador_body.html`](/c:/sistemas/python_integracion_bloques/graficador_body.html)
- [`kpi.py`](/c:/sistemas/python_integracion_bloques/kpi.py)
- [`graficos.py`](/c:/sistemas/python_integracion_bloques/graficos.py)
- [`tablas.py`](/c:/sistemas/python_integracion_bloques/tablas.py)
- `python310_embed/`
- `VFP-Embedded-Python-master/`

## Diferencias entre flujo moderno y flujo legado

### Flujo moderno

- Usa FastAPI.
- Usa Polars para lectura y consulta.
- Maneja sesiones en memoria.
- Sirve una UI web moderna desde `backend/web`.
- Integra WebView2 vía COM con `dotnet/bridge`.
- Opera sobre `127.0.0.1:8766` y UI en `/app`.

### Flujo legado

- Usa Python embebido invocado desde VFP mediante `PythonFunctionCall(...)`.
- Genera un HTML estático y un sidecar JS con payload.
- Usa `pandas` y, en parte, `sqlite` como cache.
- Se apoya en `python310_embed/` y `VFP-Embedded-Python-master/`.
- Tiene restos documentados en `_inspect_*` y en la lógica anterior de botones VFP.

## Interfaces públicas activas del backend

Las interfaces activas detectadas son:

- `GET /health`
- `GET /app`
- `POST /api/session/open`
- `GET /api/session/{session_id}`
- `DELETE /api/session/{session_id}`
- `POST /api/dashboard/query`
- `POST /api/table/page`
- `POST /api/filter/values`

Puntos de entrada de integración:

- [`vfp_dashboard_bridge.prg`](/c:/sistemas/python_integracion_bloques/vfp_dashboard_bridge.prg)
- [`scripts/start_backend.bat`](/c:/sistemas/python_integracion_bloques/scripts/start_backend.bat)
- [`scripts/start_demo.ps1`](/c:/sistemas/python_integracion_bloques/scripts/start_demo.ps1)

## Hallazgos técnicos

### 1. Duplicidad entre módulos Python de raíz y `backend/`

Los archivos `kpi.py`, `graficos.py` y `tablas.py` en raíz son equivalentes a sus pares en `backend/`. Esto aumenta el riesgo de:

- corregir un bug en un solo sitio
- documentar una ruta equivocada
- confundir el flujo vigente con el legado

### 2. Coexistencia de pipeline estático legado

`main.py` y `dashboard_runtime.py` siguen presentes y funcionalmente relacionados con una generación HTML anterior. No son la ruta principal actual, pero sí representan una implementación histórica todavía legible y potencialmente utilizable.

### 3. Inconsistencia en `dotnet/host`

El host WinForms en `dotnet/host/MainForm.cs` espera:

- backend en `127.0.0.1:8765`
- UI en `/ui`

Pero la API real del repo expone:

- backend en `127.0.0.1:8766`
- UI en `/app`

Conclusión: `dotnet/host/` no debe documentarse como ruta operativa principal. Hoy encaja mejor como demo, experimento o componente desalineado.

### 4. Ausencia de pruebas automatizadas visibles

No se observaron suites activas de pruebas para:

- backend FastAPI
- bridge VFP
- frontend web
- integración .NET

Esto obliga a validar el sistema por flujo manual.

### 5. Repo mezclado con artefactos generados

El árbol contiene binarios, `obj/`, `bin/`, `__pycache__/`, respaldos de formularios, `.FXP` y dumps de inspección. Esto complica mantenimiento, revisiones y control de cambios.

## Dependencias y comandos operativos reales

### Python

Dependencias visibles en [`backend/requirements.txt`](/c:/sistemas/python_integracion_bloques/backend/requirements.txt):

- `fastapi`
- `uvicorn`
- `polars`

### Comando principal de arranque

```bat
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8766
```

Script equivalente:

```bat
scripts\start_backend.bat
```

### Verificación del backend

```text
GET http://127.0.0.1:8766/health
```

### Script auxiliar

```powershell
scripts\start_demo.ps1
```

Ese script levanta el backend y puede abrir un host demo opcional, pero no debe asumirse como flujo principal hasta alinear `dotnet/host/` con los endpoints reales.

## Riesgos y deuda técnica

- Duplicación de lógica Python entre raíz y `backend/`.
- Mezcla de flujo moderno y legado sin una frontera clara en el árbol del repo.
- Componente `dotnet/host/` desfasado respecto a la API activa.
- Ausencia de pruebas automatizadas.
- Presencia de artefactos generados dentro del repositorio.
- Dependencias embebidas y carpetas grandes que dificultan identificar el código fuente real.

## Limpieza sugerida

La siguiente clasificación es conservadora. En esta pasada no se recomienda borrar automáticamente fuera del grupo de artefactos claramente generados.

### Borrado seguro

Artefactos generados, temporales o de respaldo:

- `__pycache__/`
- `dotnet/**/bin/`
- `dotnet/**/obj/`
- `FORMS/*.bak_*`
- `_inspect_*`
- `*.FXP`
- `FORMS/vista_py.ERR`

### Validar antes de borrar

Código o recursos asociados al flujo legado o con ambigüedad funcional:

- [`main.py`](/c:/sistemas/python_integracion_bloques/main.py)
- [`dashboard_runtime.py`](/c:/sistemas/python_integracion_bloques/dashboard_runtime.py)
- [`graficador_app.js`](/c:/sistemas/python_integracion_bloques/graficador_app.js)
- [`graficador_head.html`](/c:/sistemas/python_integracion_bloques/graficador_head.html)
- [`graficador_body.html`](/c:/sistemas/python_integracion_bloques/graficador_body.html)
- módulos duplicados en raíz: [`kpi.py`](/c:/sistemas/python_integracion_bloques/kpi.py), [`graficos.py`](/c:/sistemas/python_integracion_bloques/graficos.py), [`tablas.py`](/c:/sistemas/python_integracion_bloques/tablas.py)
- `python310_embed/`
- `VFP-Embedded-Python-master/`
- `dotnet/host/`

### Conservar

Código y recursos alineados con el flujo moderno:

- `backend/`
- [`vfp_dashboard_bridge.prg`](/c:/sistemas/python_integracion_bloques/vfp_dashboard_bridge.prg)
- `dotnet/bridge/`
- `scripts/`
- [`config.json`](/c:/sistemas/python_integracion_bloques/config.json)
- `FORMS/vista_py.scx`
- `FORMS/vista_py.SCT`
- `LIBS/`
- `csv/`

## Recomendación de siguiente paso

El siguiente trabajo razonable sería una segunda pasada enfocada únicamente en:

1. limpiar artefactos de `Borrado seguro`
2. decidir oficialmente si el flujo legado seguirá soportado o se retira
3. alinear o retirar `dotnet/host/`
4. agregar una prueba mínima de backend y una guía de smoke test manual
