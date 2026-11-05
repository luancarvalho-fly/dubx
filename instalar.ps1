# DUBX - instalador (Windows + placa NVIDIA). Chamado pelo INSTALAR.bat.
$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot
function Passo($t) { Write-Host ""; Write-Host "  - $t" -ForegroundColor Cyan }

# 1. Python 3.13
Passo "Python 3.13"
$py = $null
try { $py = (& py -3.13 -c "import sys;print(sys.executable)") 2>$null } catch {}
if (-not $py) {
    winget install -e --id Python.Python.3.13 --accept-source-agreements --accept-package-agreements --silent
    $py = (& py -3.13 -c "import sys;print(sys.executable)")
}
Write-Host "    $py"

# 2. ffmpeg
Passo "ffmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements --silent
    Write-Host "    instalado (se o DUBX nao achar o ffmpeg, reinicie o PC uma vez)"
} else { Write-Host "    ja tem" }

# 3. motor do Melhorar (Sidon) - ambiente proprio em motor\.venv
Passo "motor do Melhorar (baixa uns 3 GB na primeira vez)"
$venv = "$BASE\motor\.venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) { & $py -m venv $venv }
$vpy = "$venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip -q
& $vpy -m pip install torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128 -q
& $vpy -m pip install -r "$BASE\requirements-melhorar.txt" -q
& $vpy -c "import torch;print('    placa:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NAO ACHEI A NVIDIA (vai ficar lento)')"

# 4. modelos do Sidon (baixa agora pra primeira vez nao demorar)
Passo "modelos do Sidon"
& $vpy -c "import sys;sys.path.insert(0,r'$BASE\motor');import sidon_cli;sidon_cli.load_models('cpu');from transformers import AutoFeatureExtractor;AutoFeatureExtractor.from_pretrained('facebook/w2v-bert-2.0');print('    ok')"

# 5. painel no Premiere
Passo "painel do Premiere"
$UPIA = "C:\Program Files\Common Files\Adobe\Adobe Desktop Common\RemoteComponents\UPI\UnifiedPluginInstallerAgent\UnifiedPluginInstallerAgent.exe"
if (Test-Path $UPIA) {
    $S = "$env:TEMP\dubx_pacote"; $Z = "$env:TEMP\DUBX_plugin.zip"; $C = "$env:TEMP\DUBX.ccx"
    Remove-Item $S, $Z, $C -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory $S | Out-Null
    Copy-Item "$BASE\painel\manifest.json", "$BASE\painel\index.html", "$BASE\painel\main.js" $S
    Compress-Archive -Path "$S\*" -DestinationPath $Z -Force; Move-Item $Z $C -Force
    & $UPIA /remove "DUBX" | Out-Null
    & $UPIA /install $C
    Write-Host "    feche e abra o Premiere: Janela > Extensoes > DUBX"
} else { Write-Host "    nao achei o Creative Cloud - o painel do Premiere ficou de fora (o app funciona igual)" }

# 6. atalho na area de trabalho
Passo "atalho DUBX na area de trabalho"
$sh = New-Object -ComObject WScript.Shell
$l = $sh.CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\DUBX.lnk")
$l.TargetPath = "$env:SystemRoot\System32\wscript.exe"
$l.Arguments = "`"$BASE\DUBX.vbs`""
$l.IconLocation = "$BASE\dubx.ico,0"
$l.WorkingDirectory = $BASE
$l.Save()

Write-Host ""
Write-Host "  [OK] Pronto. Abra o DUBX pelo atalho (ele precisa estar aberto pro painel do Premiere funcionar)." -ForegroundColor Green
