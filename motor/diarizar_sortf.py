# -*- coding: utf-8 -*-
"""
DUBX — SEPARAR POR LOCUTOR, motor NVIDIA Sortformer (o terceiro).

⚠️⚠️ LICENCA CC BY-NC 4.0 — **USO NAO COMERCIAL**. Esta aqui porque o Luan
pediu (19/08) pra manter pro uso pessoal dele enquanto nao vende o plugin.
**QUANDO O PLUGIN VIRAR PRODUTO, ESTA OPCAO SAI.** Ver [[licenca-modelos-nc]]
na memoria e a nota no CONTEXTO.md. Os outros dois motores sao livres:
diarizar.py (caseiro) e diarizar_pya.py (pyannote, MIT).

Diferencial tecnico: e end-to-end e o unico dos tres com deteccao explicita de
fala SOBREPOSTA. Teto de 4 pessoas por audio (irrelevante pro caso dele, que e
dialogo de 2 personagens).

Nao precisa de token: o modelo e publico (`gated: False`, conferido em 19/08).

Entrada do modelo: 16 kHz MONO. Mas a SAIDA sai recortada do ORIGINAL, na
qualidade cheia — igual aos outros dois motores, de proposito: so muda quem
decide "quem fala quando", e e isso que torna a comparacao honesta.

Uso:
    .venv-sortf/Scripts/python.exe diarizar_sortf.py -i mistura.wav -o pasta
"""
import argparse
import json
import re
import sys
import warnings
from pathlib import Path

warnings.simplefilter("ignore")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diarizar as D   # noqa: E402  — so as partes puras (blocos, constantes)

MODELO = "nvidia/diar_sortformer_4spk-v1"
TAXA_MODELO = 16000


def turnos_de(saida):
    """A saida do NeMo vem como 'inicio fim locutor' em texto (ou lista).
    Normaliza pra (ini, fim, locutor) — defensivo porque o formato ja mudou
    entre versoes do NeMo."""
    if saida and isinstance(saida[0], list):
        saida = saida[0]
    turnos = []
    for item in saida:
        if isinstance(item, str):
            partes = re.split(r"[\s,]+", item.strip())
        else:
            partes = [str(x) for x in item]
        if len(partes) < 3:
            continue
        try:
            turnos.append((float(partes[0]), float(partes[1]), str(partes[2])))
        except ValueError:
            continue
    return turnos


def main():
    p = argparse.ArgumentParser(description="Diarizacao com NVIDIA Sortformer")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--vozes", type=int, default=0,
                   help="ignorado: o Sortformer decide sozinho (teto de 4)")
    a = p.parse_args()

    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio
    from nemo.collections.asr.models import SortformerEncLabelModel

    pasta = Path(a.output)
    pasta.mkdir(parents=True, exist_ok=True)

    orig, sr = sf.read(a.input, dtype="float32", always_2d=True)
    dur = len(orig) / sr

    # o modelo exige 16 k mono; a saida NAO usa esta copia, so a analise
    tmp16 = pasta / "_analise16k.wav"
    mono = orig.mean(axis=1)
    if sr != TAXA_MODELO:
        mono = torchaudio.functional.resample(
            torch.from_numpy(mono).unsqueeze(0), sr, TAXA_MODELO)[0].numpy()
    sf.write(str(tmp16), mono, TAXA_MODELO, subtype="PCM_16")

    print(f"  {dur:.1f}s · carregando o Sortformer…", flush=True)
    modelo = SortformerEncLabelModel.from_pretrained(MODELO)
    modelo.eval()
    if torch.cuda.is_available():
        modelo = modelo.to(torch.device("cuda"))

    print("  ouvindo quem fala quando…", flush=True)
    with torch.inference_mode():
        bruto = modelo.diarize(audio=str(tmp16), batch_size=1)
    turnos = turnos_de(bruto)
    try:
        tmp16.unlink()
    except OSError:
        pass
    if not turnos:
        raise RuntimeError("o Sortformer nao devolveu turno nenhum")

    n_quadros = int(dur / D.PULO) + 1
    quem = np.zeros(n_quadros, dtype=int)
    nomes = {}
    for ini, fim, locutor in turnos:
        if locutor not in nomes:
            nomes[locutor] = len(nomes) + 1
        k = nomes[locutor]
        i0 = max(0, int(ini / D.PULO))
        i1 = min(n_quadros, int(fim / D.PULO))
        alvo = quem[i0:i1]
        alvo[alvo == 0] = k        # sobreposicao: o primeiro fica com o quadro
    n_vozes = len(nomes)
    print(f"  {n_vozes} voz(es) reconhecida(s)", flush=True)

    fade = max(1, int(D.FADE * sr))
    saidas, tempos = [], []
    for k in range(1, n_vozes + 1):
        mascara = np.zeros(len(orig), dtype="float32")
        falado = 0.0
        for ini, fim in D.blocos(np, quem, k):
            i0 = int(ini * sr)
            i1 = min(len(orig), int(fim * sr))
            if i1 <= i0:
                continue
            mascara[i0:i1] = 1.0
            f = min(fade, (i1 - i0) // 2)
            if f > 0:
                mascara[i0:i0 + f] = np.linspace(0, 1, f, dtype="float32")
                mascara[i1 - f:i1] = np.linspace(1, 0, f, dtype="float32")
            falado += fim - ini
        destino = pasta / f"voz{k}.wav"
        sf.write(str(destino), orig * mascara[:, None], sr, subtype="PCM_24")
        saidas.append(str(destino))
        tempos.append(round(falado, 2))
        print(f"  voz {k}: {falado:.1f}s de fala", flush=True)

    print(json.dumps({"ok": True, "vozes": saidas, "n": n_vozes,
                      "segundos": tempos, "taxa": sr, "motor": "sortformer"},
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        sys.exit(1)
