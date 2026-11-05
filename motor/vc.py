# -*- coding: utf-8 -*-
"""
VOZ STUDIO — troca de voz (ChatterboxVC, MIT).

Mantem o CONTEUDO e a INTERPRETACAO do audio de entrada (tempo, entonacao,
emocao, sincronia com a imagem) e troca so o TIMBRE pelo da voz-alvo.

E diferente de TTS: aqui nao ha texto. Entra fala, sai a MESMA fala com
outra voz — que e o unico jeito de manter sincronia com video ja montado.

Uso:
    .venv-vc\\Scripts\\python.exe vc.py -i <fala.wav|pasta> -a <voz_alvo.wav> [-o saida]
"""
import argparse
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
AUDIO_EXT = {".wav", ".flac", ".aiff", ".aif", ".mp3", ".m4a", ".ogg"}


def log(msg):
    print(msg, flush=True)


def main():
    p = argparse.ArgumentParser(description="Troca a voz mantendo a interpretacao.")
    p.add_argument("-i", "--input", required=True, help="fala a preservar (arquivo ou pasta)")
    p.add_argument("-a", "--alvo", required=True, help="WAV da voz que deve ENTRAR no lugar")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--sufixo", default="_vc")
    p.add_argument("--device", default="cuda")
    a = p.parse_args()

    import soundfile as sf   # torchaudio.save no 2.9 exige torchcodec; soundfile nao
    import torch
    from chatterbox.vc import ChatterboxVC

    device = a.device
    if device.startswith("cuda") and not torch.cuda.is_available():
        log("  ⚠ CUDA indisponivel — CPU (lento)")
        device = "cpu"

    src = Path(a.input)
    alvo = Path(a.alvo)
    if not src.exists():
        print(json.dumps({"ok": False, "error": f"entrada nao encontrada: {src}"}),
              file=sys.stderr)
        sys.exit(1)
    if not alvo.exists():
        print(json.dumps({"ok": False, "error": f"voz-alvo nao encontrada: {alvo}"}),
              file=sys.stderr)
        sys.exit(1)

    arquivos = (sorted(f for f in src.iterdir() if f.suffix.lower() in AUDIO_EXT)
                if src.is_dir() else [src])

    log("carregando o modelo (uma vez so)…")
    modelo = ChatterboxVC.from_pretrained(device)

    saida_base = Path(a.output) if a.output else (src if src.is_dir() else src.parent)
    feitos = []
    for n, f in enumerate(arquivos, 1):
        if src.is_dir() or (a.output and Path(a.output).is_dir()):
            dst = saida_base / f"{f.stem}{a.sufixo}.wav"
        else:
            dst = saida_base
        try:
            with torch.inference_mode():
                wav = modelo.generate(audio=str(f), target_voice_path=str(alvo))
            dst.parent.mkdir(parents=True, exist_ok=True)
            # (canais, amostras) -> (amostras,) que e o que o soundfile espera
            dados = wav.squeeze().detach().cpu().numpy()
            if dados.ndim > 1:
                dados = dados.T
            sf.write(str(dst), dados, int(modelo.sr), subtype="PCM_24")
            log(f"  [{n}/{len(arquivos)}] {f.name} → {dst.name}")
            feitos.append(str(dst))
        except Exception as exc:  # noqa: BLE001
            log(f"  [{n}/{len(arquivos)}] {f.name}: ERRO — {type(exc).__name__}: {exc}")

    print(json.dumps({"ok": True, "sr": int(modelo.sr), "files": feitos}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        sys.exit(2)
