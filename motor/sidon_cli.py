#!/usr/bin/env python
"""
sidon_cli.py - CLI de restauracao de voz (Sidon v0.1, MIT) para chamada por plugin CEP.

Reconstroi a voz: w2v-BERT 2.0 (camada 8) -> decoder HiFi-GAN -> 48 kHz.
Trata em UMA passada: ruido, reverb, banda limitada, clipping, codec e packet loss.

Uso:
  python sidon_cli.py -i entrada.wav -o saida.wav
  python sidon_cli.py -i pasta_in -o pasta_out          (lote)
  python sidon_cli.py -i in.wav -o out.wav --stereo     (duplica p/ dual-mono)
  python sidon_cli.py --warmup                          (baixa pesos e sai)

Saida: WAV 48 kHz, PCM 24-bit. Codigo de saida 0 = ok, !=0 = erro (stderr em JSON).
"""
import argparse
import json
import os
import sys
from pathlib import Path

AUDIO_EXT = {".wav", ".flac", ".aiff", ".aif", ".mp3", ".m4a", ".ogg"}
REPO = "sarulab-speech/sidon-v0.1"
CHUNK_SECONDS = 96          # janela de analise em 16 kHz
TAIL_PAD = 24000            # padding do final, igual ao demo oficial
DECODER_TRIM = 960          # amostras descartadas por chunk (overlap do cache)


def fail(msg, code=1):
    print(json.dumps({"ok": False, "error": str(msg)}), file=sys.stderr)
    sys.exit(code)


def load_models(device):
    import torch
    from huggingface_hub import hf_hub_download

    tag = "cuda" if device.startswith("cuda") else "cpu"
    fe_path = hf_hub_download(REPO, filename=f"feature_extractor_{tag}.pt")
    dec_path = hf_hub_download(REPO, filename=f"decoder_{tag}.pt")
    fe = torch.jit.load(fe_path, map_location=device).to(device).eval()
    dec = torch.jit.load(dec_path, map_location=device).to(device).eval()
    return fe, dec


def restore(wav, sr, fe, dec, preprocessor, device, progresso=None):
    """wav: torch.Tensor 1-D float32 mono. Retorna 1-D float32 a 48 kHz.

    `progresso(feito, total)` e chamado a cada janela de CHUNK_SECONDS
    (29/09/2026): um clipe de 28 min levou 16 min e a barra do painel ficou
    cravada em "100% · falta menos de 1 min" o tempo todo. Sem callback, nada muda."""
    import torch
    import torchaudio

    n_out = int(48_000 / sr * wav.shape[-1])

    peak = wav.abs().max()
    if peak > 0:
        wav = 0.9 * (wav / peak)
    wav = wav.view(1, -1)

    wav = torchaudio.functional.highpass_biquad(wav, sr, 50)
    wav16 = torchaudio.functional.resample(wav, sr, 16_000)
    wav16 = torch.nn.functional.pad(wav16, (0, TAIL_PAD))

    out, cache = [], None
    pedacos = wav16.view(-1).split(16_000 * CHUNK_SECONDS)
    for n, chunk in enumerate(pedacos, 1):
        inputs = preprocessor(
            torch.nn.functional.pad(chunk, (160, 160)),
            sampling_rate=16_000,
            return_tensors="pt",
        )
        feat = fe(inputs["input_features"].to(device))["last_hidden_state"]
        if cache is not None:
            feat = torch.cat([cache, feat], dim=1)
        out.append(dec(feat.transpose(1, 2)).view(-1)[:-DECODER_TRIM])
        cache = feat[:, -1:]
        if progresso:
            progresso(n, len(pedacos))

    return torch.cat(out, dim=0)[:n_out].float().cpu()


def process_one(src, dst, fe, dec, preprocessor, device, stereo, progresso=None):
    import soundfile as sf
    import torch

    data, sr = sf.read(str(src), dtype="float32", always_2d=True)
    mono = torch.from_numpy(data.mean(axis=1))          # (samples,) -> mono
    y = restore(mono, sr, fe, dec, preprocessor, device, progresso).numpy()

    if stereo:
        y = y.repeat(2).reshape(-1, 2)                  # dual-mono

    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), y, 48_000, subtype="PCM_24")
    return dst


def main():
    p = argparse.ArgumentParser(description="Restauracao generativa de voz (Sidon).")
    p.add_argument("-i", "--input", help="arquivo ou pasta de entrada")
    p.add_argument("-o", "--output", help="arquivo ou pasta de saida")
    p.add_argument("-d", "--device", default="cuda", help="cuda | cuda:0 | cpu")
    p.add_argument("--stereo", action="store_true", help="grava dual-mono em vez de mono")
    p.add_argument("--suffix", default="_restored", help="sufixo no modo lote")
    p.add_argument("--warmup", action="store_true", help="so baixa os pesos e sai")
    a = p.parse_args()

    try:
        import torch
        from transformers import AutoFeatureExtractor
    except ImportError as e:
        fail(f"dependencia ausente: {e}")

    device = a.device
    if device.startswith("cuda") and not torch.cuda.is_available():
        print(json.dumps({"warn": "CUDA indisponivel, caindo para CPU"}), file=sys.stderr)
        device = "cpu"

    fe, dec = load_models(device)
    preprocessor = AutoFeatureExtractor.from_pretrained("facebook/w2v-bert-2.0")

    if a.warmup:
        print(json.dumps({"ok": True, "device": device, "warmup": True}))
        return

    if not a.input or not a.output:
        fail("-i e -o sao obrigatorios (ou use --warmup)")

    src, dst = Path(a.input), Path(a.output)
    if not src.exists():
        fail(f"entrada nao encontrada: {src}")

    written = []
    with torch.inference_mode():
        if src.is_dir():
            files = sorted(f for f in src.iterdir() if f.suffix.lower() in AUDIO_EXT)
            if not files:
                fail(f"nenhum audio em {src}")
            for f in files:
                written.append(str(process_one(
                    f, dst / f"{f.stem}{a.suffix}.wav",
                    fe, dec, preprocessor, device, a.stereo)))
        else:
            written.append(str(process_one(
                src, dst, fe, dec, preprocessor, device, a.stereo)))

    print(json.dumps({"ok": True, "device": device, "sr": 48000, "files": written}))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        fail(f"{type(exc).__name__}: {exc}", 2)
