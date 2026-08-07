@echo off
rem Lance Lamapix Uploader depuis les sources, sans console.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" app.py
