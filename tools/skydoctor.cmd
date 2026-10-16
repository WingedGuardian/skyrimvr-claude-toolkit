@echo off
rem skydoctor.cmd -- the cmd twin of tools/skydoctor:  tools\skydoctor.cmd [--root R] [--json] [--no-toolchain]
rem Exit 0 every line live, 1 any line not live, 2 any line unknown (or no Python: nothing could be checked).
rem Python: %SKYGUARD_PYTHON%, else the one setup.sh recorded in skyrim-paths.env, else py -3, else python. Layer 0 finds Git for Windows' bash by itself.
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
set "HERE=%~dp0"
if defined SKYGUARD_PYTHON goto custom
rem No %SKYGUARD_PYTHON%: the interpreter setup.sh recorded in ..\skyrim-paths.env (Plan 8 T7), only when it exists.
set "SG_FILE_PY="
if exist "%HERE%..\skyrim-paths.env" for /f "usebackq tokens=1,* delims==" %%a in ("%HERE%..\skyrim-paths.env") do if "%%a"=="SKYGUARD_PYTHON" set "SG_FILE_PY=%%b"
if not defined SG_FILE_PY goto probe
if "%SG_FILE_PY:~0,1%%SG_FILE_PY:~-1%"=="''" set "SG_FILE_PY=%SG_FILE_PY:~1,-1%"
if defined SG_FILE_PY set "SG_FILE_PY=%SG_FILE_PY:'\''='%"
if defined SG_FILE_PY if exist "%SG_FILE_PY%" set "SKYGUARD_PYTHON=%SG_FILE_PY%"
if defined SKYGUARD_PYTHON goto custom
:probe
"%COMSPEC%" /d /c py -3 -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto usepy
"%COMSPEC%" /d /c python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 goto usepython
echo skydoctor: no working Python 3.10+ found, so nothing could be checked; for the lock, run tools\skyguard-unlock.cmd 1>&2
exit /b 2
:custom
"%COMSPEC%" /d /s /c ""%SKYGUARD_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)"" >nul 2>nul
if errorlevel 1 goto probe
"%SKYGUARD_PYTHON%" -B "%HERE%skyguard\doctor.py" %*
exit /b %errorlevel%
:usepy
py -3 -B "%HERE%skyguard\doctor.py" %*
exit /b %errorlevel%
:usepython
python -B "%HERE%skyguard\doctor.py" %*
exit /b %errorlevel%
