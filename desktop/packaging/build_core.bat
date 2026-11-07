@echo off
setlocal
cd /d "%~dp0"
REM Build the Python core sidecar into desktop\src-tauri\core\ (Tauri bundles it as a resource).
set "PY=..\..\.venv-build\Scripts\python.exe"
if not exist "%PY%" set "PY=py -3.13"
%PY% -m PyInstaller --noconfirm --clean --distpath ..\src-tauri --workpath ..\..\build\cctv-core cctv_core.spec
if errorlevel 1 (
    echo [HATA] cctv-core derlenemedi.
    exit /b 1
)
echo {"jsonrpc":"2.0","id":1,"method":"system.info"} | ..\src-tauri\core\cctv-core.exe
if errorlevel 1 (
    echo [HATA] cctv-core calismadi.
    exit /b 1
)
echo cctv-core hazir: desktop\src-tauri\core\cctv-core.exe
