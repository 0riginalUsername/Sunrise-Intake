@echo off
cd /d "%~dp0"
REM Prompts for .py path. EXE name, icon, onefile, windowed from build_config.ini.
where py >nul 2>nul && (py build.py & goto :done)
where python >nul 2>nul && (python build.py & goto :done)
echo Python not found in PATH.
echo Add Python to PATH, or run from a terminal where "py" or "python" works.
pause
exit /b 1
:done
if errorlevel 1 pause

