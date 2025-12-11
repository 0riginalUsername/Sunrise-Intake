@echo off
cd /d "%~dp0"
pyinstaller --onefile --name "Data-Inktake-EDIT" --icon="Intake-icon.ico" --windowed "Data-Intake-PyQt5-CLEAN.py"

