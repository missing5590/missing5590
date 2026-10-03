@echo off
rem receipt-bot ni ishga tushirish (oyna ochiq turgan paytda ishlaydi; to'xtatish - Ctrl+C).
cd /d "%~dp0..\.."
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m app.main
