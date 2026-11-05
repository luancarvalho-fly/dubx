# -*- coding: utf-8 -*-
"""
VOZ STUDIO — texto para fala com a voz clonada (Chatterbox Multilingual, MIT).

Diferente da troca de voz: aqui NAO ha audio de entrada. O modelo INVENTA a
interpretacao a partir do texto — que e justamente o que o RVC nao faz.

Uso:
    .venv-vc\\Scripts\\python.exe tts.py -t "sua frase" -a voz_alvo.wav -o saida.wav
                                        [--exagero 0.5] [--cfg 0.5] [--temp 0.8]

--exagero e o botao de atuacao: 0.3 contido · 0.5 padrao · 0.8+ dramatico.
"""
import argparse
import json
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description="Texto para fala com voz clonada.")
    p.add_argument("-t", "--texto", required=True)
    p.add_argument("-a", "--alvo", required=True, help="WAV de referencia da voz")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--idioma", default="pt")
    p.add_argument("--exagero", type=float, default=0.5, help="intensidade da emocao")
    p.add_argument("--cfg", type=float, default=0.5, help="adesao a referencia")
    p.add_argument("--temp", type=float, default=0.8, help="variacao entre geracoes")
    p.add_argument("--device", default="cuda")
    a = p.parse_args()

    import soundfile as sf   # torchaudio.save no 2.9 exige torchcodec; soundfile nao
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    device = a.device
    if device.startswith("cuda") and not torch.cuda.is_available():
        print("  ⚠ CUDA indisponivel — CPU (lento)", flush=True)
        device = "cpu"

    alvo = Path(a.alvo)
    if not alvo.exists():
        print(json.dumps({"ok": False, "error": f"voz-alvo nao encontrada: {alvo}"}),
              file=sys.stderr)
        sys.exit(1)

    print("carregando o modelo…", flush=True)
    modelo = ChatterboxMultilingualTTS.from_pretrained(device=device)

    with torch.inference_mode():
        wav = modelo.generate(
            a.texto,
            language_id=a.idioma,
            audio_prompt_path=str(alvo),
            exaggeration=a.exagero,
            cfg_weight=a.cfg,
            temperature=a.temp,
        )

    dados = wav.squeeze().detach().cpu().numpy()
    if dados.ndim > 1:
        dados = dados.T
    dst = Path(a.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), dados, int(modelo.sr), subtype="PCM_24")

    print(json.dumps({"ok": True, "sr": int(modelo.sr), "exagero": a.exagero,
                      "arquivo": str(dst)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        sys.exit(2)
