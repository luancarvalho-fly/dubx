# Sobe o servidor do DUBX (se preciso) e abre em JANELA DE APLICATIVO.
# Chamado pelo DUBX.vbs, que roda isto escondido.
#
# 07/11/2026 (Kakashi 16:36): «Esse DubX aqui, ele fica eterno aqui... Se ele ja ta ligado rodando em segundo plano, deixa ele
# escondido, mano. Deixa so o iconezinho do app aqui, pra quando eu quiser usar» / «Nao consigo fechar».
# A janelinha tk de 30/10 (dubx_janela.pyw: X = minimizar) SAIU do caminho: era ela que ficava eterna na barra.
# Agora: servidor escondido e solto (fechar a tela nao derruba ele) + a tela do DUBX numa janela de app com o ID
# Luan.DUBX (cai no icone fixado, igual o Kakashi). Ja aberta = so vem pra frente. Backup: abrir.ps1.bak-antes-app-0711.

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

if (-not (NoAr)) {
    # Saida direto em arquivo (nao em pipe): o servidor segue vivo quando este script termina.
    $log = "$BASE\servidor.log"
    $logErro = "$BASE\servidor.err.log"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $proc = Start-Process -FilePath $PY `
        -ArgumentList @("`"$BASE\servidor.py`"") `
        -WorkingDirectory $BASE `
        -RedirectStandardOutput $log `
        -RedirectStandardError $logErro `
        -WindowStyle Hidden -PassThru
    for ($i = 0; $i -lt 60; $i++) {
        if (NoAr) { break }
        if ($proc -and $proc.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
}

if (-not (NoAr)) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "O DUBX nao subiu.`n`nVeja o motivo em:`n$BASE\servidor.err.log",
        "DUBX", 0, 16) | Out-Null
    exit 1
}

# A janela: perfil proprio (.janela, guarda o tamanho dela), ID Luan.DUBX, uma so.
$PYW  = "C:\Users\Luan\AppData\Local\Programs\Python\Python313\pythonw.exe"
$JAPP = "$env:USERPROFILE\CLAUDE\ferramentas\janela_app.py"
if ((Test-Path $PYW) -and (Test-Path $JAPP)) {
    Start-Process $PYW -ArgumentList @("`"$JAPP`"", "abrir", "dubx") -WindowStyle Hidden
} else {
    Start-Process $URL
}
