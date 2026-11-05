# -*- coding: utf-8 -*-
"""
DUBX — SEPARAR POR LOCUTOR, motor pyannote (o segundo).

Existe pra ele ALTERNAR entre os dois (pedido de 19/08): o motor "timbre"
(diarizar.py, speechbrain caseiro) e este. A pyannote e a referencia do ramo —
tem segmentacao propria e enxerga fala sobreposta, coisa que o caseiro nao faz.

LICENCA — foi o que decidiu qual seria o motor "de verdade":
  · pyannote/speaker-diarization-3.1  = MIT        -> comercial LIBERADO
  · pyannote/speaker-diarization-community-1 = CC BY 4.0 -> comercial liberado
  · NVIDIA Sortformer                = CC BY-NC 4.0 -> comercial PROIBIDO
Ele vende plugin e produz pra Fly, entao o Sortformer ficou fora apesar de nao
exigir conta. Os modelos da pyannote sao "gated: auto": basta ter conta e
aceitar as condicoes na pagina, a liberacao e na hora.

⚠️ PRECISA DE TOKEN. Ele NAO opera terminal, entao o token entra pelo campo do
proprio DUBX (POST /api/hf-token), que grava em ~/.cache/huggingface/token —
o mesmo arquivo do `huggingface-cli login`. Aqui a gente so LE dali.

A SAIDA E IDENTICA a do motor caseiro de proposito (voz1.wav, voz2.wav... no
formato e taxa do ORIGINAL, recortando em vez de reconstruir). So muda quem
decide "quem fala quando" — e e isso que torna a comparacao honesta.

Uso:
    .venv-pya/Scripts/python.exe diarizar_pya.py -i mistura.wav -o pasta [--vozes 2]
"""
import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.simplefilter("ignore")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diarizar as D   # noqa: E402  — so as partes puras (blocos, constantes)

MODELO = "pyannote/speaker-diarization-3.1"
ARQ_TOKEN = Path.home() / ".cache" / "huggingface" / "token"


def le_token():
    tok = ARQ_TOKEN.read_text(encoding="utf-8").strip() if ARQ_TOKEN.exists() else ""
    if not tok:
        raise RuntimeError(
            "falta o token do HuggingFace — cole no campo do DUBX (aba Separar). "
            "E aceite as condicoes em huggingface.co/pyannote/speaker-diarization-3.1 "
            "e /pyannote/segmentation-3.0")
    return tok


def main():
    p = argparse.ArgumentParser(description="Diarizacao com pyannote")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--vozes", type=int, default=0,
                   help="quantas pessoas falam (0 = a pyannote decide)")
    p.add_argument("--modelo", default=MODELO)
    a = p.parse_args()

    import numpy as np
    import soundfile as sf
    import torch
    from pyannote.audio import Pipeline

    pasta = Path(a.output)
    pasta.mkdir(parents=True, exist_ok=True)
    token = le_token()

    orig, sr = sf.read(a.input, dtype="float32", always_2d=True)
    dur = len(orig) / sr
    print(f"  {dur:.1f}s · carregando a pyannote…", flush=True)

    # 4.x renomeou use_auth_token -> token; aceita os dois nomes conforme a versao
    try:
        pipe = Pipeline.from_pretrained(a.modelo, token=token)
    except TypeError:
        pipe = Pipeline.from_pretrained(a.modelo, use_auth_token=token)
    if pipe is None:
        raise RuntimeError(
            "a pyannote devolveu vazio — normalmente e condicao nao aceita na "
            "pagina do modelo, ou token sem permissao de leitura")
    if torch.cuda.is_available():
        pipe.to(torch.device("cuda"))

    args_diar = {}
    if a.vozes and a.vozes > 0:
        args_diar["num_speakers"] = int(a.vozes)
    print("  ouvindo quem fala quando…", flush=True)
    # ⚠️ NAO passar o caminho do arquivo: a pyannote 4.x le audio pelo
    # torchcodec, que nao roda aqui ("torchcodec is not available"). Ela
    # aceita a forma de onda pronta, e a gente ja leu tudo com soundfile —
    # mesma licao do `torchaudio.save` (14/08) e do inference.py do Seed-VC.
    onda = torch.from_numpy(orig.mean(axis=1)).unsqueeze(0)   # (canal, tempo)
    saida = pipe({"waveform": onda, "sample_rate": sr}, **args_diar)

    # A 4.x devolve DiarizeOutput; a 3.x devolvia a Annotation direto.
    anotacao = getattr(saida, "speaker_diarization", saida)

    # Turnos POR LOCUTOR. Diferente do motor caseiro, aqui NAO se decide um
    # dono unico por instante: a pyannote marca fala SOBREPOSTA, e num stem o
    # certo e o trecho sobreposto aparecer NAS DUAS faixas — separar quem fala
    # junto e impossivel no recorte, e sumir com o trecho seria pior.
    por_locutor = {}
    for turno, _, locutor in anotacao.itertracks(yield_label=True):
        por_locutor.setdefault(locutor, []).append((turno.start, turno.end))
    if not por_locutor:
        raise RuntimeError("a pyannote nao achou fala nenhuma")

    def junta(faixas):
        """Une turnos colados/sobrepostos do MESMO locutor e joga fora migalha."""
        faixas = sorted(faixas)
        saida_ = []
        for ini, fim in faixas:
            if saida_ and ini <= saida_[-1][1] + 0.05:
                saida_[-1] = (saida_[-1][0], max(saida_[-1][1], fim))
            else:
                saida_.append((ini, fim))
        return [(a, b) for a, b in saida_ if b - a >= D.MIN_FALA_SAIDA]

    ordem = sorted(por_locutor, key=lambda k: min(x[0] for x in por_locutor[k]))
    trechos = {i + 1: junta(por_locutor[k]) for i, k in enumerate(ordem)}
    n_vozes = len(trechos)
    print(f"  {n_vozes} voz(es) reconhecida(s)", flush=True)

    fade = max(1, int(D.FADE * sr))
    saidas, tempos = [], []
    for k in range(1, n_vozes + 1):
        mascara = np.zeros(len(orig), dtype="float32")
        falado = 0.0
        for ini, fim in trechos[k]:
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
                      "segundos": tempos, "taxa": sr, "motor": "pyannote"},
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        sys.exit(1)
