@echo off
REM DB-GPT webserver starter
REM Launched manually or via Scheduled Task.

REM ============================================================
REM Environment Variables
REM ============================================================

REM Set these securely on your machine.
set TOKENHARBOR_API_KEY=YOUR_TOKENHARBOR_API_KEY
set SQL_SERVER_PASSWORD=YOUR_SQL_SERVER_PASSWORD
set GROQ_API_KEY=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
REM Liara AI gateway (OpenAI-compatible). Verified 2026-10-04:
REM correct format is https://ai.liara.ir/api/{workspaceID}/v1
set LIARA_API_BASE=https://ai.liara.ir/api/6aba44bb5d4e25a451926448/v1
set LIARA_API_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJrZXkiOiI2YWJhNDVhODg3ZDM0NmFiZGNkMGRkZmMiLCJ0eXBlIjoiYWlfa2V5IiwiaWF0IjoxNzkwNTkyNDI0fQ.ods8GSxBhf_Y8ulflhft8vhtZ2WY41FfB6eNlzkttpU
REM Liara model IDs (provider proxy/liara, see configs/dbgpt-proxy-ollama-qwen.toml):
REM  - deepseek/deepseek-v4.1-flash (DeepSeek: DeepSeek V4.1 Flash, 1M ctx)
REM  - xiaomi/mimo-v2.5-pro (Xiaomi: MiMo-V2.5-Pro, ~1M ctx)
REM  - xiaomi/mimo-v2.5 (Xiaomi: MiMo-V2.5, ~1M ctx)

set DBGPT_LANG=fa

REM ============================================================
REM Project paths
REM ============================================================

set PROJECT_DIR=C:\Users\m.yeganehpour\KavirProjects\DB_GPT
set VENV_PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe
set LOG_DIR=%PROJECT_DIR%\logs

cd /d "%PROJECT_DIR%"

REM Create logs directory if it doesn't exist
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM ============================================================
REM Timestamp
REM ============================================================

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "_stamp=%%I"

REM ============================================================
REM Start DB-GPT
REM ============================================================

echo Starting DB-GPT...
echo Project: %PROJECT_DIR%
echo Python:  %VENV_PYTHON%
echo Log:     %LOG_DIR%\webserver_task_%_stamp%.log
echo.

"%VENV_PYTHON%" -u -X utf8 launch_dbgpt.py >> "%LOG_DIR%\webserver_task_%_stamp%.log" 2>&1
