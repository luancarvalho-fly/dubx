# -*- coding: utf-8 -*-
"""
DUBX — TTS Qwen3 (instalado 16/08/2026, o upgrade #1 da pesquisa de dublagem).

Clonagem zero-shot com 3-10 s de referencia + A TRANSCRICAO da referencia
(exigencia do modelo; na dublagem ela vem de graca do Whisper). Apache-2.0,
portugues entre as 10 linguas oficiais.

Windows: attn_implementation="sdpa" SEMPRE — o flash_attention_2 do README
oficial so compila em Linux.

Uso:
  .venv-qwen\\Scripts\\python.exe tts_qwen.py -t "texto" -a ref.wav
      --ref-texto "transcricao da ref" -o out.wav --idioma pt
"""
import argparse
import json
import sys

IDIOMAS = {"pt": "Portuguese", "en": "English", "es": "Spanish",
           "fr": "French", "de": "German", "it": "Italian", "ru": "Russian",
           "ja": "Japanese", "ko": "Korean", "zh": "Chinese"}


def main():
    p = argparse.ArgumentParser(description="TTS Qwen3 com clonagem zero-shot")
    p.add_argument("-t", "--texto", required=True)
    p.add_argument("-a", "--referencia", required=True, help="wav de 3-10 s da voz")
    p.add_argument("--ref-texto", required=True, help="o que a referencia FALA")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--idioma", default="pt")
    p.add_argument("--modelo", default="Qwen/Qwen3-TTS-12Hz-1.7B-Base")
    p.add_argument("--xvector", action="store_true",
                   help="so embedding do falante (ignora ref-texto) — mais robusto")
    p.add_argument("--rep", type=float, default=0.0,
                   help="repetition_penalty (>1 combate loop 'e ai eu vou...')")
    p.add_argument("--max-tokens", type=int, default=0)
    a = p.parse_args()

    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel

    modelo = Qwen3TTSModel.from_pretrained(
        a.modelo, device_map="cuda:0", dtype=torch.bfloat16,
        attn_implementation="sdpa")
    extras = {}
    if a.rep > 0:
        extras["repetition_penalty"] = a.rep
    if a.max_tokens > 0:
        extras["max_new_tokens"] = a.max_tokens
    wavs, sr = modelo.generate_voice_clone(
        text=a.texto, language=IDIOMAS.get(a.idioma, a.idioma),
        ref_audio=a.referencia, ref_text=a.ref_texto,
        x_vector_only_mode=a.xvector, **extras)
    sf.write(a.output, wavs[0], sr)
    print(json.dumps({"ok": True, "sr": int(sr), "arquivo": a.output}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        sys.exit(1)
