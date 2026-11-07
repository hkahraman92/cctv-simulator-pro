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
REM Every heavy path once through the FROZEN core (numpy, PIL, reportlab, openpyxl, spec engine).
%PY% smoke_core.py ..\src-tauri\core\cctv-core.exe
if errorlevel 1 (
    echo [HATA] cctv-core duman testi basarisiz.
    exit /b 1
)
echo cctv-core hazir: desktop\src-tauri\core\cctv-core.exe
