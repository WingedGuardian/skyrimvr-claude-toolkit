@echo off
rem skyguard-unlock.cmd -- undo the Layer 2 lock with NOTHING but cmd, attrib and icacls (no scripting runtime needed).
rem Double-click it, or run it from a prompt. It reads ..\.skyguard\locked.txt (one full path per line, written
rem by "skyguard lock lock") and, for each file: removes this user's deny ACE, then clears the read-only bit.
rem A file that was ALREADY read-only before the lock carries the tag (DENY)(WD,AD,WA) instead of the full
rem (DENY)(WD,AD,WEA,WA); for those the ACE is removed and the bit is left alone.
rem Order matters: with write-attributes denied, attrib -R is itself refused, so the ACE goes first.
rem Last resort by hand, for one file F:   icacls "F" /remove:d *<your SID>    then    attrib -R "F"
rem Not supported: an install path containing a percent sign. SKYGUARD_NO_PAUSE=1 skips the final pause.
rem SKYGUARD_LOCK_SANDBOX (tests only): refuse any line that is not under that folder.
setlocal EnableExtensions DisableDelayedExpansion
set "S32=%SystemRoot%\System32"
set "SID="
for /f "tokens=2 delims=," %%a in ('"%S32%\whoami.exe" /user /fo csv /nh') do set "SID=%%~a"
if not defined SID (
  echo Could not read your user id with whoami; nothing was changed.
  set "RC=2"
  goto finish
)
for %%I in ("%~dp0..\.skyguard\locked.txt") do set "LIST=%%~fI"
if not exist "%LIST%" (
  echo No list of locked files at "%LIST%"; nothing was changed.
  echo Run the two commands in the README by hand for each file, or: tools\skyguard lock unlock
  set "RC=1"
  goto finish
)
set "RC=0"
set "N=0"
set "SB="
set "L=0"
if not defined SKYGUARD_LOCK_SANDBOX goto lendone
set "SB=%SKYGUARD_LOCK_SANDBOX%\"
:len
call set "CH=%%SB:~%L%,1%%"
if not defined CH goto lendone
set /a L+=1
goto len
:lendone
for /f "usebackq delims=" %%L in ("%LIST%") do call :one "%%L"
if "%N%"=="0" echo The list is empty: nothing is locked.
goto finish

:one
set "F=%~1"
if "%F%"=="" exit /b 0
if not defined SB goto go
call set "HEAD=%%F:~0,%L%%%"
if /i not "%HEAD%"=="%SB%" goto refuse
:go
if not exist "%F%" goto missing
set "PRE="
"%S32%\icacls.exe" "%F%" | "%S32%\findstr.exe" /l /c:"(DENY)(WD,AD,WA)" >nul
if not errorlevel 1 set "PRE=1"
"%S32%\icacls.exe" "%F%" /remove:d *%SID% >nul
if defined PRE goto keepbit
"%S32%\attrib.exe" -R "%F%"
echo unlocked "%F%"
set /a N+=1
exit /b 0
:keepbit
echo unlocked "%F%" - it was read-only before the lock, so it stays read-only
set /a N+=1
exit /b 0
:missing
echo skipped, not found: "%F%"
exit /b 0
:refuse
echo REFUSED, outside the sandbox: "%F%"
set "RC=3"
exit /b 0

:finish
if "%SKYGUARD_NO_PAUSE%"=="" echo %cmdcmdline% | "%S32%\find.exe" /i "/c" >nul && pause
exit /b %RC%
