# python_integracion_bloques

Proyecto para abrir dashboards analíticos desde Visual FoxPro usando una UI web embebida con WebView2 y un backend local en FastAPI.

La ruta recomendada hoy es:

`VFP -> vfp_dashboard_bridge.prg -> backend.main -> backend/web -> dotnet/bridge`

Para una auditoría más completa del repo, limpieza sugerida y diferencias con el flujo legado, revisa [`AUDITORIA_TECNICA.md`](/c:/sistemas/python_integracion_bloques/AUDITORIA_TECNICA.md).

## Qué hace

- abre una sesión de trabajo desde VFP
- carga un CSV o fuente compatible
- genera widgets de KPI, gráficas y tablas
- aplica filtros y rango de fechas desde una UI web local
- embebe esa UI dentro de VFP usando WebView2

## Arquitectura recomendada hoy

### Capa VFP

- [`vfp_dashboard_bridge.prg`](/c:/sistemas/python_integracion_bloques/vfp_dashboard_bridge.prg)
- [`FORMS/vista_py.scx`](/c:/sistemas/python_integracion_bloques/FORMS/vista_py.scx)

### Capa Python

- [`backend/main.py`](/c:/sistemas/python_integracion_bloques/backend/main.py)
- [`backend/engine.py`](/c:/sistemas/python_integracion_bloques/backend/engine.py)
- [`backend/legacy_config.py`](/c:/sistemas/python_integracion_bloques/backend/legacy_config.py)

### Capa web

- [`backend/web/index.html`](/c:/sistemas/python_integracion_bloques/backend/web/index.html)
- [`backend/web/app.js`](/c:/sistemas/python_integracion_bloques/backend/web/app.js)
- [`backend/web/styles.css`](/c:/sistemas/python_integracion_bloques/backend/web/styles.css)

### Capa .NET / WebView2

- [`dotnet/bridge/VfpWebViewBridgeHost.cs`](/c:/sistemas/python_integracion_bloques/dotnet/bridge/VfpWebViewBridgeHost.cs)

## Requisitos

- Windows
- Python disponible en `PATH`
- dependencias Python del backend
- WebView2 Runtime instalado
- Visual FoxPro 9 para la integración VFP
- .NET 8 SDK si se necesita compilar o registrar el bridge

## Instalación de dependencias Python

Instala al menos las dependencias declaradas en [`backend/requirements.txt`](/c:/sistemas/python_integracion_bloques/backend/requirements.txt):

```powershell
pip install -r backend\requirements.txt
```

Dependencias visibles:

- `fastapi`
- `uvicorn`
- `polars`

## Arranque del backend

La forma más directa es:

```powershell
scripts\start_backend.bat
```

Equivalente manual:

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8766
```

## Verificación rápida

Con el backend levantado, verifica:

```text
http://127.0.0.1:8766/health
```

La UI principal está en:

```text
http://127.0.0.1:8766/app
```

## Integración con VFP y WebView2

El flujo esperado es:

1. VFP inicializa [`vfp_dashboard_bridge.prg`](/c:/sistemas/python_integracion_bloques/vfp_dashboard_bridge.prg).
2. El bridge comprueba `GET /health`.
3. Si el backend no está activo, ejecuta [`scripts/start_backend.bat`](/c:/sistemas/python_integracion_bloques/scripts/start_backend.bat).
4. VFP abre sesión con `POST /api/session/open`.
5. El bridge navega a `/app?session_id=...` usando el ProgID `VfpWebViewBridge.Host`.

Si hace falta registrar o recompilar el bridge COM, revisa:

- [`scripts/register_vfp_webview_bridge.ps1`](/c:/sistemas/python_integracion_bloques/scripts/register_vfp_webview_bridge.ps1)
- [`dotnet/bridge/VfpWebViewBridge.csproj`](/c:/sistemas/python_integracion_bloques/dotnet/bridge/VfpWebViewBridge.csproj)

## Endpoints activos

- `GET /health`
- `GET /app`
- `POST /api/session/open`
- `GET /api/session/{session_id}`
- `DELETE /api/session/{session_id}`
- `POST /api/dashboard/query`
- `POST /api/table/page`
- `POST /api/filter/values`

## Estructura del proyecto

```text
backend/                 Backend FastAPI, motor de consultas y frontend web
dotnet/bridge/           Bridge COM/WebView2 usado por VFP
dotnet/host/             Host WinForms de prueba o desalineado
FORMS/                   Formularios VFP
LIBS/                    Librerías VFP
scripts/                 Arranque, registro y parcheo
csv/                     Archivos de datos de ejemplo
config.json              Configuración principal
vfp_dashboard_bridge.prg Bridge principal VFP -> backend
```

## Configuración

- [`config.json`](/c:/sistemas/python_integracion_bloques/config.json) define opciones de UI, CSV y dashboard.
- `CONEXIONES.INI` existe como archivo de configuración sensible. No conviene documentar ni versionar valores reales de conexión.

## Estado del flujo legado

El repo conserva una implementación anterior basada en:

- `main.py`
- `dashboard_runtime.py`
- `graficador_*.html/js`
- `python310_embed/`
- `VFP-Embedded-Python-master/`

Ese flujo debe considerarse histórico o en transición. No es la ruta recomendada para operación diaria mientras exista el backend FastAPI y el bridge moderno.

## Notas de mantenimiento

- `dotnet/host/` no coincide hoy con la API principal del repo: espera `8765` y `/ui`, mientras el backend activo usa `8766` y `/app`.
- El código Python de raíz y el de `backend/` no deben mantenerse en paralelo sin una decisión explícita.
- Antes de borrar archivos, revisa la clasificación conservadora en [`AUDITORIA_TECNICA.md`](/c:/sistemas/python_integracion_bloques/AUDITORIA_TECNICA.md).
