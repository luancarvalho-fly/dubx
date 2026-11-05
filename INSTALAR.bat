@echo off
REM DUBX - instala tudo: Python, ffmpeg, o motor do Melhorar (Sidon) e o painel do Premiere.
REM Dois cliques e espera. Pode rodar de novo quando atualizar (git pull): ele so completa o que falta.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
pause
