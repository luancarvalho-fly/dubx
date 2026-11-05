# Sobe o servidor do DUBX (se preciso) e abre em JANELA DE APLICATIVO.
# Chamado pelo DUBX.vbs, que roda isto escondido.

$BASE  = $PSScriptRoot   # a pasta deste script (23/09/2026: antes era fixo no PC do Luan)
$PORTA = 7788
$URL   = "http://127.0.0.1:$PORTA"
# Usa o Python do motor (que mora dentro do proprio DUBX). O servidor so usa
# biblioteca padrao, entao qualquer Python serve — mas este e garantido.
$PY    = "$BASE\motor\.venv\Scripts\python.exe"

function NoAr {
    try {
        $r = Invoke-WebRequest -Uri "$URL/api/vozes" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch { return $false }
}

# 30/10/2026 (P41): primeiro a JANELA do DUBX (dubx_janela.pyw) — icone proprio na barra de tarefas, com miniatura,
# igual o Kakashi. E ela que sobe o servidor (solto: fechar a janela nao derruba). Ja aberta = so vem pra frente.
# Sem o pythonw do motor, cai no jeito antigo (servidor escondido, logo abaixo).
$PYW = "$BASE\motor\.venv\Scripts\pythonw.exe"
if (Test-Path $PYW) {
    Start-Process -FilePath $PYW -ArgumentList "`"$BASE\dubx_janela.pyw`"" -WorkingDirectory $BASE
    for ($i = 0; $i -lt 40; $i++) {
        if (NoAr) { break }
        Start-Sleep -Milliseconds 500
    }
}

if (-not (NoAr)) {
    $si = New-Object System.Diagnostics.ProcessStartInfo
    $si.FileName = $PY
    $si.Arguments = "`"$BASE\servidor.py`""
    $si.WorkingDirectory = $BASE
    $si.UseShellExecute = $false
    $si.CreateNoWindow = $true
    $si.RedirectStandardOutput = $true
    $si.RedirectStandardError = $true
    $proc = [System.Diagnostics.Process]::Start($si)

    # Grava o que o servidor diz. Sem isto, quando algo falha nao ha como saber.
    # Consumir os fluxos tambem e obrigatorio: buffer cheio trava o processo.
    $log = "$BASE\servidor.log"
    "=== inicio $(Get-Date -Format 'dd/MM HH:mm:ss') ===" | Out-File $log -Encoding utf8
    $acao = { if ($EventArgs.Data) { $EventArgs.Data | Out-File $Event.MessageData -Append -Encoding utf8 } }
    Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action $acao -MessageData $log | Out-Null
    Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived  -Action $acao -MessageData $log | Out-Null
    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()

    for ($i = 0; $i -lt 40; $i++) {
        if (NoAr) { break }
        Start-Sleep -Milliseconds 500
    }
}

if (-not (NoAr)) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "O DUBX nao subiu.`n`nVeja o motivo em:`n$BASE\servidor.log",
        "DUBX", 0, 16) | Out-Null
    exit 1
}

$navegadores = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
)
$nav = $navegadores | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($nav) {
    # --app= tira barra de endereco, abas e menu: vira janela de programa.
    #
    # --user-data-dir e o que faz o tamanho FUNCIONAR. Sem ele, o Chrome reaproveita
    # o perfil normal e restaura o tamanho da ultima janela que voce abriu — ignorando
    # o --window-size. Com perfil proprio, o DUBX lembra o TAMANHO DELE, e nao herda
    # nada do seu navegador (nem abas, nem sessao, nem extensao).
    $perfil = "$BASE\.janela"
    if (-not (Test-Path $perfil)) { New-Item -ItemType Directory -Force $perfil | Out-Null }

    Start-Process $nav -ArgumentList @(
        "--app=$URL",
        "--user-data-dir=$perfil",
        "--window-size=520,900",
        "--window-position=140,60",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,AutofillServerCommunication"
    )
} else {
    Start-Process $URL
}
