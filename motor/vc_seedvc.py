# -*- coding: utf-8 -*-
"""
DUBX — troca de voz pelo SEED-VC (diffusion transformer, Plachtaa/seed-vc).

SEGUNDO motor de voice conversion, ao lado do RVC treinado. Existe para o A/B
cego de 17/08 (ver Nova pasta\\PLANO_SEEDVC.md): NAO substitui o RVC.

Mesma interface do vc.py, que ja e o padrao da casa:
    .venv-seedvc\\Scripts\\python.exe vc_seedvc.py -i <fala.wav> -a <voz.wav> -o <saida.wav>

Extras:
    --modelo <ckpt fine-tunado>  (junto com --config; sem eles, baixa o zero-shot da HF)
    --passos <diffusion steps>   (25 padrao; 30-50 = melhor, 4-10 = rapido)

Duas armadilhas da casa ja resolvidas aqui dentro:
  1. `torchaudio.save` no torch 2.9 exige torchcodec — o inference.py do repo usa
     ele na hora de gravar. Trocado por soundfile em tempo de execucao (mesma
     licao paga no vc.py em 14/08).
  2. O repo resolve config/campplus por caminho RELATIVO — dai o chdir.
"""
import argparse
import json
import os
import shutil
import sys
from argparse import Namespace
from pathlib import Path

BASE = Path(__file__).resolve().parent
SEEDVC = BASE / "seedvc"


def main():
    p = argparse.ArgumentParser(description="Troca a voz com o Seed-VC.")
    p.add_argument("-i", "--input", required=True, help="fala a preservar")
    p.add_argument("-a", "--alvo", required=True, help="WAV da voz que deve ENTRAR")
    p.add_argument("-o", "--output", required=True, help="wav de saida")
    p.add_argument("--modelo", default=None, help="checkpoint fine-tunado (.pth)")
    p.add_argument("--config", default=None, help="yml do checkpoint fine-tunado")
    p.add_argument("--passos", type=int, default=25, help="diffusion steps")
    p.add_argument("--cfg-rate", type=float, default=0.7)
    p.add_argument("--tamanho", type=float, default=1.0, help="length-adjust")
    p.add_argument("--f0", action="store_true", help="condicionar ao pitch (canto)")
    # AUTO-F0 (24/08): sem isto o Seed-VC mantem a ALTURA DA FALA DE ENTRADA e
    # so troca o timbre — dai a "voz fina" quando o alvo era um monstro. Com o
    # auto ligado ele move a altura pra faixa da voz-alvo.
    p.add_argument("--auto-f0", action="store_true", dest="auto_f0",
                   help="mover a altura pra faixa da voz-alvo (exige --f0)")
    p.add_argument("--tom", type=int, default=0, help="semitons a mais/menos (exige --f0)")
    p.add_argument("--fp16", default="True")
    a = p.parse_args()

    src, alvo = Path(a.input), Path(a.alvo)
    for rotulo, caminho in (("entrada", src), ("voz-alvo", alvo)):
        if not caminho.exists():
            print(json.dumps({"ok": False, "error": f"{rotulo} nao encontrada: {caminho}"}),
                  file=sys.stderr)
            sys.exit(1)
    if a.modelo and not a.config:
        print(json.dumps({"ok": False, "error": "--modelo exige --config"}), file=sys.stderr)
        sys.exit(1)
    # P36 (29/10): --f0 com um modelo SEM altura (o afinado de 22 kHz) nao converte: o inference.py faz a
    # janela em 44 kHz com o audio em 22 kHz e, com referencia longa, a fala vira 0 quadros e o vocoder
    # quebra com «Kernel size can't be greater than actual input size». Barra aqui com a causa na cara.
    if a.f0 and a.config:
        import yaml
        with open(a.config, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if not (cfg.get("model_params", {}).get("DiT", {}) or {}).get("f0_condition"):
            print(json.dumps({"ok": False, "error": "puxar a altura (--f0) precisa de um modelo com altura; "
                              f"este config nao tem ({Path(a.config).name}) — rode sem --modelo"}),
                  file=sys.stderr)
            sys.exit(1)

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")   # WinError 1314 sem Developer Mode
    os.chdir(SEEDVC)
    sys.path.insert(0, str(SEEDVC))

    import soundfile as sf
    import torch
    import torchaudio

    # ---- conserto 1: gravar com soundfile, nao com torchaudio.save ----------
    def salva(caminho, tensor, taxa, **_):
        dados = tensor.squeeze().detach().cpu().numpy()
        if dados.ndim > 1:
            dados = dados.T
        sf.write(str(caminho), dados, int(taxa), subtype="PCM_24")

    torchaudio.save = salva

    import inference

    saida = Path(a.output).resolve()
    saida.parent.mkdir(parents=True, exist_ok=True)
    pasta_tmp = saida.parent / f".seedvc_{saida.stem}"
    pasta_tmp.mkdir(parents=True, exist_ok=True)

    args = Namespace(
        source=str(src.resolve()),
        target=str(alvo.resolve()),
        output=str(pasta_tmp),
        diffusion_steps=a.passos,
        length_adjust=a.tamanho,
        inference_cfg_rate=a.cfg_rate,
        f0_condition=bool(a.f0),
        auto_f0_adjust=bool(a.auto_f0),
        semi_tone_shift=int(a.tom),
        checkpoint=str(Path(a.modelo).resolve()) if a.modelo else None,
        config=str(Path(a.config).resolve()) if a.config else None,
        fp16=str(a.fp16).lower() in ("1", "true", "yes", "sim"),
    )

    with torch.inference_mode():
        inference.main(args)

    gerados = sorted(pasta_tmp.glob("*.wav"), key=lambda f: f.stat().st_mtime)
    if not gerados:
        raise RuntimeError("o Seed-VC nao gerou arquivo nenhum")
    shutil.move(str(gerados[-1]), str(saida))
    shutil.rmtree(pasta_tmp, ignore_errors=True)

    taxa = 44100 if a.f0 else 22050   # o repo fixa isso conforme o modelo
    print(json.dumps({"ok": True, "sr": taxa, "files": [str(saida)]}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        sys.exit(2)
