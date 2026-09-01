@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PYTHON_EXE=python"
set "ONEFILE=0"

:parse_args
if "%~1"=="" goto after_args
if /I "%~1"=="--onefile" (
    set "ONEFILE=1"
    shift
    goto parse_args
)
if /I "%~1"=="-onefile" (
    set "ONEFILE=1"
    shift
    goto parse_args
)
if /I "%~1"=="--python" (
    set "PYTHON_EXE=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="-python" (
    set "PYTHON_EXE=%~2"
    shift
    shift
    goto parse_args
)
echo Bilinmeyen parametre: %~1
echo Kullanim: build_exe.cmd [--onefile] [--python "C:\Path\python.exe"]
exit /b 1

:after_args
if not exist "cctv_dual_view_simulator.py" (
    echo cctv_dual_view_simulator.py bulunamadi.
    exit /b 1
)

if not exist "camera_library_from_excel.json" (
    echo camera_library_from_excel.json bulunamadi.
    exit /b 1
)

"%PYTHON_EXE%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo PyInstaller bulunamadi.
    echo Kurulum: "%PYTHON_EXE%" -m pip install pyinstaller pillow
    exit /b 1
)

if "%ONEFILE%"=="1" (
    "%PYTHON_EXE%" -m PyInstaller --noconfirm --clean --windowed --onefile --name "CCTV Dual View Simulator" --add-data "camera_library_from_excel.json;." --add-data "assets;assets" --collect-data ttkbootstrap --collect-submodules ttkbootstrap --collect-data reportlab --collect-submodules reportlab "cctv_dual_view_simulator.py"
) else (
    "%PYTHON_EXE%" -m PyInstaller --noconfirm --clean "cctv_dual_view_simulator.spec"
)

if errorlevel 1 (
    echo PyInstaller build basarisiz oldu.
    exit /b 1
)

echo.
echo Build tamamlandi.
echo Cikti klasoru: %SCRIPT_DIR%dist
echo EXE ilk calistiginda kamera veritabani kullanici klasorune kopyalanir:
echo %%APPDATA%%\CCTV Dual View Simulator\camera_library_from_excel.json

endlocal
