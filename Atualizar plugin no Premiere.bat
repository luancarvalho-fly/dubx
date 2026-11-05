@echo off
REM ====================================================================
REM  DUBX - atualiza o plugin INSTALADO no Premiere (23/08/2026)
REM
REM  Por que existe: o plugin instalado e uma COPIA CONGELADA. Mexer em
REM  painel\main.js nao muda o que o Premiere carrega — tem que empacotar
REM  e reinstalar. Isto faz as duas coisas num duplo-clique.
REM
REM  Nao precisa de admin. Nao precisa fechar o Premiere pra INSTALAR,
REM  mas precisa REINICIAR o Premiere pra ver a mudanca.
REM ====================================================================
setlocal
cd /d "%~dp0"
title DUBX - atualizar plugin

set "UPIA=C:\Program Files\Common Files\Adobe\Adobe Desktop Common\RemoteComponents\UPI\UnifiedPluginInstallerAgent\UnifiedPluginInstallerAgent.exe"
set "STAGE=%TEMP%\dubx_pacote"
set "ZIP=%TEMP%\DUBX_plugin.zip"
set "CCX=%TEMP%\DUBX.ccx"

cls
echo.
echo   ==========================================
echo        DUBX - ATUALIZAR PLUGIN NO PREMIERE
echo   ==========================================
echo.

if not exist "%UPIA%" (
    echo   [X] Nao achei o instalador da Adobe:
    echo       %UPIA%
    echo.
    pause
    exit /b 1
)
if not exist "painel\manifest.json" (
    echo   [X] Rode este arquivo de dentro da pasta DUBX.
    echo.
    pause
    exit /b 1
)

echo   - Empacotando o painel...
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%"
REM SO os tres arquivos que o plugin precisa. node_modules NAO entra: o
REM `require("premierepro")` e modulo embutido do UXP, o node_modules ali
REM so serve pros tipos durante o desenvolvimento.
copy /y "painel\manifest.json" "%STAGE%\" >NUL
copy /y "painel\index.html"    "%STAGE%\" >NUL
copy /y "painel\main.js"       "%STAGE%\" >NUL

REM ⚠️ O Compress-Archive SO escreve .zip — mandar direto pro .ccx
REM devolve "nao e um formato com suporte". Compacta em .zip e renomeia.
if exist "%ZIP%" del /q "%ZIP%"
if exist "%CCX%" del /q "%CCX%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Compress-Archive -Path '%STAGE%\*' -DestinationPath '%ZIP%' -Force } catch { exit 1 }"
if errorlevel 1 goto :falhou
move /y "%ZIP%" "%CCX%" >NUL
if not exist "%CCX%" goto :falhou

REM ⚠️ DESINSTALAR ANTES. O UPIA vê o mesmo id + mesma versao (0.1.0) e
REM PULA a substituicao dos arquivos — dizendo "Installation Successful"
REM do mesmo jeito. Descoberto em 23/08 depois de tres reinstalacoes que
REM nao trocaram nada: os arquivos instalados continuavam os das 18:44 e
REM ele ficou olhando um painel velho achando que eu nao tinha consertado.
echo   - Removendo a versao anterior...
"%UPIA%" /remove "DUBX" >NUL 2>&1

echo   - Instalando no Premiere...
"%UPIA%" /install "%CCX%"
if errorlevel 1 (
    echo.
    echo   [X] A instalacao falhou. Se o Premiere estiver travando o
    echo       plugin, feche ele e rode de novo.
    pause
    exit /b 1
)

echo.
echo   - Conferindo o registro da Adobe:
"%UPIA%" /list "Premiere Pro"

rmdir /s /q "%STAGE%" 2>NUL
del /q "%CCX%" 2>NUL
del /q "%ZIP%" 2>NUL

echo.
echo   [OK] Plugin atualizado.
echo.
echo   AGORA: FECHE e ABRA o Premiere. O DUBX aparece em
echo          Janela ^> Extensoes ^> DUBX  (sem modo desenvolvedor).
echo.
pause
goto :fim

:falhou
echo.
echo   [X] Falha ao empacotar o plugin.
echo.
pause
exit /b 1

:fim
endlocal
