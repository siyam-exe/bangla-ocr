@echo off
chcp 65001 >nul
setlocal
set "BANGLA_OCR_PYTHON=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%BANGLA_OCR_PYTHON%" (
  echo Bangla OCR is not installed. Run Install Bangla OCR.cmd first.
  pause
  exit /b 1
)
start "" /D "%~dp0" "%BANGLA_OCR_PYTHON%" -m bangla_ocr launcher
