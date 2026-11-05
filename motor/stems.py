# -*- coding: utf-8 -*-
"""
DUBX - separador de STEMS (voz x resto do mundo).

Nao confundir com o `separar.py`, que e outra coisa: aquele parte DUAS VOZES
sobrepostas (SepFormer). Este aqui parte a trilha em

    voz.wav    -> so a fala
    resto.wav  -> tudo o que NAO e fala: sala, reverb, musica, passo, vento

e existe por um motivo unico: trocar a voz SEM matar o ambiente. O caminho
antigo (jogar a mixagem inteira no RVC) devolve voz de estudio seca colada num
video que tinha sala — e sobra a "engenharia de reambientar" na mao. Com os
stems, o RVC so ve a voz; o `resto` volta INTACTO por cima no fim.

O par e COMPLEMENTAR por construcao: os dois motores calculam o segundo stem
como `mistura - voz`. Somar os dois de volta reconstroi o original amostra a
amostra, entao remixar nao abre buraco onde a voz estava.

Uso:
    python stems.py -i entrada.wav -o pasta [--motor demucs|separator]
                    [--modelo htdemucs_ft]

Saida: <pasta>\voz.wav e <pasta>\resto.wav

⚠️ ENTRADA TEM QUE SER ESTEREO. O demucs 4.x estoura um bug de broadcast do
torch com mono (armadilha ja paga no Beat Match). Quem chama (o servidor)
converte com ffmpeg antes; aqui so conferimos e avisamos.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent

# Modelo padrao de cada motor.
#   demucs/htdemucs_ft  - 4 modelos em conjunto (4x mais lento que o htdemucs
#                         normal, e a versao boa). Treinado em MUSICA, mas o
#                         stem "vocals" pega fala muito bem — e o truque padrao.
#   separator/roformer  - MelBand Roformer, o estado da arte de isolamento de
#                         voz em 2026. Melhor que o demucs quando funciona;
#                         depende do audio-separator instalado na venv.
PADRAO = {
    "demucs": "htdemucs_ft",
    "separator": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
}


def _canais(arquivo):
    """Contagem de canais sem depender de ffprobe: le o cabecalho do wav."""
    try:
        import wave
        with wave.open(str(arquivo), "rb") as w:
            return w.getnchannels()
    except Exception:
        try:
            import soundfile as sf
            return int(sf.info(str(arquivo)).channels)
        except Exception:
            return 0     # nao deu pra saber: segue e deixa o motor reclamar


def por_demucs(entrada, tmp, modelo):
    """`python -m demucs --two-stems=vocals`. O no_vocals que ele grava JA E o
    residual (mistura menos voz) — nao e uma segunda predicao."""
    subprocess.run([sys.executable, "-m", "demucs",
                    "--two-stems=vocals", "-n", modelo,
                    "--float32", "-o", str(tmp), str(entrada)],
                   check=True)
    voz = next(tmp.rglob("vocals.wav"), None)
    resto = next(tmp.rglob("no_vocals.wav"), None)
    if not voz or not resto:
        raise RuntimeError("o demucs rodou mas nao gravou os dois stems")
    return voz, resto


def por_separator(entrada, tmp, modelo):
    """audio-separator (UVR/Roformer), na venv `.venv-rofo`.

    ⚠️ Chamar pelo `audio-separator.exe`, NAO por `-m audio_separator...`: o
    modulo nao tem guarda de __main__, entao `-m` sai com codigo 0 sem fazer
    nada (nem erro, nem arquivo) — 20 minutos pagos nisso em 17/08."""
    exe = Path(sys.executable).parent / "audio-separator.exe"
    if not exe.exists():
        raise RuntimeError(f"audio-separator.exe nao existe em {exe.parent}")
    subprocess.run([str(exe), str(entrada), "--output_dir", str(tmp),
                    "--output_format", "WAV",
                    "--model_filename", modelo],
                   check=True)
    arquivos = sorted(tmp.glob("*.wav"))
    voz = next((a for a in arquivos if "vocal" in a.name.lower()), None)
    resto = next((a for a in arquivos
                  if "instrumental" in a.name.lower()
                  or "no_vocal" in a.name.lower()), None)
    if not voz or not resto:
        raise RuntimeError(
            "o audio-separator rodou mas nao deu pra identificar os dois stems: "
            + ", ".join(a.name for a in arquivos))
    return voz, resto


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--entrada", required=True)
    ap.add_argument("-o", "--saida", required=True,
                    help="pasta onde caem voz.wav e resto.wav")
    ap.add_argument("--motor", default="demucs",
                    choices=["demucs", "separator"])
    ap.add_argument("--modelo", default=None)
    a = ap.parse_args()

    entrada = Path(a.entrada).resolve()
    if not entrada.exists():
        raise SystemExit(f"nao existe: {entrada}")
    if _canais(entrada) == 1:
        raise SystemExit(
            "entrada MONO: o demucs 4.x quebra com mono (bug de broadcast do "
            "torch). Converta pra estereo antes de chamar.")

    destino = Path(a.saida).resolve()
    destino.mkdir(parents=True, exist_ok=True)
    tmp = destino / "_bruto"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    modelo = a.modelo or PADRAO[a.motor]
    funcao = por_demucs if a.motor == "demucs" else por_separator
    voz, resto = funcao(entrada, tmp, modelo)

    shutil.copy2(voz, destino / "voz.wav")
    shutil.copy2(resto, destino / "resto.wav")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"ok {a.motor}/{modelo} -> {destino}")


if __name__ == "__main__":
    main()
