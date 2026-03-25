* Agrega esto en tu rutina de inicio para forzar la emulación de IE11
LOCAL WshShell
WshShell = CREATEOBJECT("WScript.Shell")

* Para el entorno de desarrollo (IDE)
WshShell.RegWrite("HKCU\Software\Microsoft\Internet Explorer\Main\FeatureControl\FEATURE_BROWSER_EMULATION\vfp9.exe", 11000, "REG_DWORD")

* Para tu ejecutable compilado (cambia hr-control.exe por el nombre real de tu EXE)
WshShell.RegWrite("HKCU\Software\Microsoft\Internet Explorer\Main\FeatureControl\FEATURE_BROWSER_EMULATION\hr-control.exe", 11000, "REG_DWORD")

RELEASE WshShell