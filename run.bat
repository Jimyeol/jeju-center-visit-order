@echo off
rem Center Visit Order - Windows launcher.
rem ASCII only on purpose: this file must run under any console code page.
setlocal EnableExtensions
title Center Visit Order

pushd "%~dp0"
if errorlevel 1 goto :cd_failed

rem Double-clicking run.bat inside the zip viewer runs it alone from a temp
rem folder, without the app folder. Say so instead of a Python error.
if not exist "app\server.py" goto :not_extracted

set "PYTHONUTF8=1"
rem Ignore a PORT variable left by other tools: a free port is picked.
set "PORT="
set "PYTHONDONTWRITEBYTECODE=1"

set "PY="
set "VERN="
set "FOUND_OLD="

rem Look for Python 3.9+ : the py launcher first, then python on PATH.
rem Each candidate must actually run and print its version. The Microsoft
rem Store alias (a stub python.exe that only opens the Store) prints nothing
rem on stdout, so it is skipped.
call :try_python py -3
if not defined PY call :try_python python
if not defined PY call :try_python python3
if not defined PY goto :no_python

echo Python found: %PY%
echo Starting... the browser opens by itself. Close this window to stop.
echo.
%PY% -m app.server %*
set "RC=%ERRORLEVEL%"
popd
if "%RC%"=="0" goto :done
echo.
echo The program stopped with an error. Exit code: %RC%
echo Read the messages above, then press a key to close this window.
pause >nul
:done
endlocal & exit /b %RC%

:try_python
set "VER="
for /f "usebackq delims=" %%V in (`%* -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" 2^>nul`) do set "VER=%%V"
if not defined VER exit /b 1
set "VERN=0"
set /a "VERN=%VER%" 2>nul
if %VERN% LSS 309 goto :too_old
set "PY=%*"
exit /b 0

:too_old
set "FOUND_OLD=1"
exit /b 1

:no_python
echo.
echo Python 3.9 or newer was not found on this PC.
if defined FOUND_OLD echo An older Python is installed, but version 3.9 or newer is required.
echo.
echo   1. Install Python 3 from https://www.python.org/downloads/
echo      and tick "Add python.exe to PATH" in the installer.
echo   2. Run run.bat again.
echo.
echo No other package is needed.
popd
pause
endlocal & exit /b 1

:not_extracted
echo.
echo This folder is incomplete: the "app" folder is missing next to run.bat.
echo It usually means run.bat was started from inside the zip file.
echo.
echo   1. Right-click the zip file and choose "Extract All".
echo   2. Open the extracted folder and run run.bat there.
echo.
popd
pause
endlocal & exit /b 1

:cd_failed
echo Could not open the program folder: "%~dp0"
echo Copy the whole folder to a local drive, for example C:\center-order, and run run.bat again.
pause
endlocal & exit /b 1
