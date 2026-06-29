@echo off
echo Installing project dependencies...
echo.

pip install PyQt5 Pillow pywinauto piexif pyinstaller

echo.
if %ERRORLEVEL% == 0 (
    echo All dependencies installed successfully.
) else (
    echo One or more packages failed to install. Check output above.
)

pause
