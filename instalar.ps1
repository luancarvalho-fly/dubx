# DUBX - instalador (Windows + placa NVIDIA). Chamado pelo INSTALAR.bat.
$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot
function Passo($t) { Write-Host ""; Write-Host "  - $t" -ForegroundColor Cyan }
# 06/11: comando de fora (pip, python) que falha NAO para o PowerShell sozinho - o instalador dizia "Pronto" com o
# ClearerVoice quebrado. Agora cada um e conferido e, se falhar, para com o aviso em vermelho.
function Conferir($oque) {
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "  [ERRO] falhou: $oque" -ForegroundColor Red
        Write-Host "  Tire um print desta janela e mande pro Luan." -ForegroundColor Red
        exit 1
    }
}

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
& $vpy -m pip install --upgrade pip -q; Conferir "atualizar o pip do Sidon"
& $vpy -m pip install torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128 -q; Conferir "torch do Sidon"
# trava o torch da placa: nenhum pacote depois pode trocar por um torch sem CUDA
$trava = "$env:TEMP\dubx_trava_sidon.txt"
Set-Content $trava "torch==2.9.1+cu128`r`ntorchaudio==2.9.1+cu128" -Encoding ascii
& $vpy -m pip install -r "$BASE\requirements-melhorar.txt" -c $trava -q; Conferir "pacotes do Sidon"
# 08/11: detector de voz do Nivelador (Silero VAD, MIT, o modelo vem dentro do pacote); --no-deps pra nao mexer no torch
& $vpy -m pip install --no-deps silero-vad==6.2.3 packaging -q; Conferir "detector de voz do Nivelador (Sidon)"
& $vpy -c "import torch;assert '+cu' in torch.__version__, 'torch sem CUDA: '+torch.__version__;print('    placa:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NAO ACHEI A NVIDIA (vai ficar lento)')"; Conferir "torch do Sidon com CUDA"

# 4. modelos do Sidon (baixa agora pra primeira vez nao demorar)
Passo "modelos do Sidon"
& $vpy -c "import sys;sys.path.insert(0,r'$BASE\motor');import sidon_cli;sidon_cli.load_models('cpu');from transformers import AutoFeatureExtractor;AutoFeatureExtractor.from_pretrained('facebook/w2v-bert-2.0');print('    ok')"; Conferir "modelos do Sidon"

# 4b. ClearerVoice (so na versao que traz motor\clearervoice) - ambiente proprio, Python 3.12
$cv = "$BASE\motor\clearervoice"
if (Test-Path "$cv\rodar_clearervoice_se.py") {
    Passo "ClearerVoice (outro motor do Melhorar, baixa mais uns 3 GB)"
    $py12 = $null
    try { $py12 = (& py -3.12 -c "import sys;print(sys.executable)") 2>$null } catch {}
    if (-not $py12) {
        winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent
        $py12 = (& py -3.12 -c "import sys;print(sys.executable)")
    }
    if (-not (Test-Path "$cv\.venv\Scripts\python.exe")) { & $py12 -m venv "$cv\.venv"; Conferir "criar o ambiente do ClearerVoice" }
    $cpy = "$cv\.venv\Scripts\python.exe"
    & $cpy -m pip install --upgrade pip -q; Conferir "atualizar o pip do ClearerVoice"
    # 06/11: o clearvoice pede torchvision sem versao e o pip puxava um torch novo SEM CUDA por cima (o libtorchaudio
    # nao carregava). Agora os tres vem juntos da NVIDIA e ficam travados (as versoes que rodam no PC do Luan).
    & $cpy -m pip install torch==2.11.0 torchaudio==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128 -q; Conferir "torch do ClearerVoice"
    $travaCv = "$env:TEMP\dubx_trava_clearervoice.txt"
    Set-Content $travaCv "torch==2.11.0+cu128`r`ntorchaudio==2.11.0+cu128`r`ntorchvision==0.26.0+cu128" -Encoding ascii
    & $cpy -m pip install clearvoice==0.1.2 -c $travaCv -q; Conferir "pacotes do ClearerVoice"
    & $cpy -m pip install --no-deps silero-vad==6.2.3 -q; Conferir "detector de voz do Nivelador (ClearerVoice)"
    Push-Location $cv
    & $cpy -c "import torch,torchaudio;assert '+cu' in torch.__version__, 'torch sem CUDA: '+torch.__version__;from clearvoice import ClearVoice;ClearVoice(task='speech_enhancement',model_names=['MossFormer2_SE_48K']);print('    ok (torch', torch.__version__+')')"
    $r = $LASTEXITCODE; Pop-Location; $global:LASTEXITCODE = $r; Conferir "ClearerVoice (carregar o modelo)"
}

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
