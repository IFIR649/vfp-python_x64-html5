param()

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$formPath = Join-Path $rootDir "FORMS\vista_py.scx"
$memoPath = Join-Path $rootDir "FORMS\vista_py.sct"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"

Copy-Item -Force $formPath ($formPath + ".bak_" + $stamp)
Copy-Item -Force $memoPath ($memoPath + ".bak_" + $stamp)

$tempPrg = Join-Path $env:TEMP ("patch_vista_py_" + [guid]::NewGuid().ToString("N") + ".prg")

$prgContent = @'
LOCAL lcFormPath, lcFormMethods, lcButtonMethods, lcCrLf

lcFormPath = FULLPATH("FORMS\vista_py.scx")
lcCrLf = CHR(13) + CHR(10)

lcFormMethods = ;
    "PROCEDURE Init" + lcCrLf + ;
    "SET PROCEDURE TO vfp_dashboard_bridge ADDITIVE" + lcCrLf + ;
    "=DashboardBridgeInitForm(This, FULLPATH(""config.json""))" + lcCrLf + ;
    "ENDPROC" + lcCrLf + lcCrLf + ;
    "PROCEDURE Activate" + lcCrLf + ;
    "SET PROCEDURE TO vfp_dashboard_bridge ADDITIVE" + lcCrLf + ;
    "=DashboardBridgeActivateForm(This)" + lcCrLf + ;
    "ENDPROC" + lcCrLf + lcCrLf + ;
    "PROCEDURE Resize" + lcCrLf + ;
    "SET PROCEDURE TO vfp_dashboard_bridge ADDITIVE" + lcCrLf + ;
    "=DashboardBridgeResize(This)" + lcCrLf + ;
    "ENDPROC" + lcCrLf + lcCrLf + ;
    "PROCEDURE Destroy" + lcCrLf + ;
    "SET PROCEDURE TO vfp_dashboard_bridge ADDITIVE" + lcCrLf + ;
    "=DashboardBridgeDestroyForm(This)" + lcCrLf + ;
    "ENDPROC"

lcButtonMethods = ;
    "PROCEDURE Click" + lcCrLf + ;
    "SET PROCEDURE TO vfp_dashboard_bridge ADDITIVE" + lcCrLf + ;
    "=DashboardBridgeOpen(Thisform)" + lcCrLf + ;
    "ENDPROC"

SET SAFETY OFF
USE (m.lcFormPath) SHARED ALIAS vistaform
SELECT vistaform

GO 3
IF !RLOCK()
    ERROR "No se pudo bloquear el registro del formulario."
ENDIF
REPLACE methods WITH m.lcFormMethods
UNLOCK

GO 5
IF !RLOCK()
    ERROR "No se pudo bloquear Command1."
ENDIF
REPLACE methods WITH m.lcButtonMethods
UNLOCK

GO 7
IF !RLOCK()
    ERROR "No se pudo bloquear Command2."
ENDIF
REPLACE methods WITH m.lcButtonMethods
UNLOCK

FLUSH
USE
COMPILE FORM (m.lcFormPath)
'@

[System.IO.File]::WriteAllText($tempPrg, $prgContent)

try {
    $vfp = New-Object -ComObject VisualFoxPro.Application
    $vfp.DoCmd("CD " + '"' + $rootDir + '"')
    $vfp.DoCmd("DO " + '"' + $tempPrg + '"')
}
finally {
    if ($null -ne $vfp) {
        try {
            $vfp.Quit()
        }
        catch {}
        try {
            [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($vfp)
        }
        catch {}
    }
    Remove-Item -Force $tempPrg -ErrorAction SilentlyContinue
}

Write-Host "vista_py.scx actualizado y recompilado."
