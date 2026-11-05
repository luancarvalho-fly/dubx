# -*- coding: utf-8 -*-
"""
VOZ STUDIO — o motor. Trata audio de dublagem sem sair da maquina.

Duas frentes, e a dose entre elas:
  NIVELAR      → todo clipe termina no mesmo volume PERCEBIDO (LUFS), nunca por pico
  RECONSTRUIR  → Sidon (rede neural) regrava a voz e salva gravacao estragada
  DOSE         → 0 = so nivela · 50 = meio-termo · 100 = Sidon puro

Uso:
    venv\\Scripts\\python.exe voz.py -i <arquivo-ou-pasta> [-o saida]
                                    [--dose 50] [--lufs -16] [--sufixo _tratado]

Saida: WAV 48 kHz, 24-bit, DURACAO IDENTICA a entrada (nao quebra sincronia).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
AUDIO_EXT = {".wav", ".flac", ".aiff", ".aif", ".mp3", ".m4a", ".ogg"}
SEM_JANELA = 0x08000000 if os.name == "nt" else 0

# Presets combinados com o Luan em 14/08/2026.
# Historico que importa: no 1o teste ele preferiu o meio-termo, mas os arquivos de 50%
# estavam 3-5 dB mais BAIXOS (esqueci de renivelar depois da mistura incoerente).
# Com o volume igualado ele preferiu o Sidon completo em TODOS. Por isso 100 e o padrao.
# Licao: comparacao de audio sem volume igualado leva a decisao errada.
PRESETS = {
    "nivelar":  0,    # so acerta o volume — gravacao que ja esta boa
    "meio":     50,   # metade seco, metade reconstruido
    "completo": 100,  # o padrao — reconstroi tudo
}

FFMPEG_CANDIDATOS = [
    r"C:\Users\Luan\AppData\Roaming\Adobe\CEP\extensions\com.luan.beatmatch\bin\win\ffmpeg.exe",
    "ffmpeg",
]


def acha_ffmpeg():
    for c in FFMPEG_CANDIDATOS:
        if os.path.isabs(c) and os.path.exists(c):
            return c
        achado = shutil.which(c)
        if achado:
            return achado
    raise RuntimeError("ffmpeg nao encontrado")


FFMPEG = acha_ffmpeg()


def log(msg):
    print(msg, flush=True)


def roda(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          creationflags=SEM_JANELA)


def medir_loudness(arq, alvo):
    """Primeira passada do loudnorm: descobre onde o arquivo esta hoje."""
    r = roda([FFMPEG, "-hide_banner", "-i", str(arq), "-af",
              f"loudnorm=I={alvo}:TP=-1.5:LRA=11:print_format=json", "-f", "null", os.devnull])
    saida = r.stderr or ""
    ini = saida.rfind("{")
    fim = saida.rfind("}")
    if ini < 0 or fim < ini:
        raise RuntimeError(f"loudnorm nao devolveu medicao para {arq}")
    return json.loads(saida[ini:fim + 1])


def nivelar(src, dst, alvo):
    """Segunda passada: aplica o ganho exato para cair no alvo de LUFS.

    O `aresample=48000` no fim NAO e opcional: quando o clipe e curto o loudnorm
    cai no modo dinamico, que trabalha internamente a 192 kHz, e sem o resample
    o arquivo sai a 192 kHz sem avisar.
    """
    m = medir_loudness(src, alvo)
    af = (f"loudnorm=I={alvo}:TP=-1.5:LRA=11"
          f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
          f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
          f":offset={m['target_offset']}:linear=true,aresample=48000")
    r = roda([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
              "-af", af, "-c:a", "pcm_s24le", str(dst)])
    if r.returncode != 0:
        raise RuntimeError(f"falha ao nivelar {src}: {r.stderr.strip()[:300]}")
    return float(m["input_i"])


def misturar(a, b, dose, dst):
    """Mistura seco (a) e reconstruido (b) na proporcao da dose.

    ATENCAO: o Sidon regrava a voz, entao as duas ondas NAO estao em fase.
    A soma e incoerente e perde ~3 dB — quem chama isto TEM que renivelar depois,
    senao a dose vira mudanca de volume e o ouvido julga errado.
    """
    ga = 1.0 - dose / 100.0
    gb = dose / 100.0
    fc = (f"[0:a]volume={ga:.4f}[a];[1:a]volume={gb:.4f}[b];"
          f"[a][b]amix=inputs=2:normalize=0")
    r = roda([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
              "-i", str(a), "-i", str(b), "-filter_complex", fc,
              "-c:a", "pcm_s24le", str(dst)])
    if r.returncode != 0:
        raise RuntimeError(f"falha ao misturar: {r.stderr.strip()[:300]}")


def alinhar(ref, alvo, dst):
    """Poe o reconstruido (alvo) no mesmo tempo do seco (ref) e com a MESMA duracao.

    05/11 (ele: «vem a faixa de antes somada com a de depois... voz dupla horrivel»): o Sidon devolve a voz alguns ms
    deslocada, e a mistura soava como duas pessoas falando junto. O atraso sai da correlacao cruzada (FFT) dos
    primeiros 60 s; limite de ±250 ms pra nunca casar com outra silaba."""
    import numpy as np
    import soundfile as sf

    a, sr = sf.read(str(ref), always_2d=True)
    b, srb = sf.read(str(alvo), always_2d=True)
    if sr != srb:
        raise RuntimeError(f"taxas diferentes no alinhamento: {sr} x {srb}")
    n = min(len(a), len(b), 60 * sr)
    x, y = a[:n].mean(axis=1), b[:n].mean(axis=1)
    tam = 1 << (2 * n - 1).bit_length()
    cc = np.fft.irfft(np.fft.rfft(y, tam) * np.conj(np.fft.rfft(x, tam)), tam)
    lim = int(0.25 * sr)
    cand = np.concatenate([cc[:lim + 1], cc[-lim:]])
    k = int(np.argmax(cand))
    atraso = k if k <= lim else k - len(cand)   # >0 = o Sidon veio atrasado
    if atraso > 0:
        b = b[atraso:]
    elif atraso < 0:
        b = np.concatenate([np.zeros((-atraso, b.shape[1])), b])
    if len(b) < len(a):
        b = np.concatenate([b, np.zeros((len(a) - len(b), b.shape[1]))])
    b = b[:len(a)]
    if b.shape[1] != a.shape[1]:
        b = np.repeat(b[:, :1], a.shape[1], axis=1)
    sf.write(str(dst), b, sr, subtype="PCM_24")
    return atraso * 1000.0 / sr


def remisturar(partes, dst, dose, alvo):
    """Slider de reconstrucao SEM rodar o Sidon de novo: mistura o seco e o reconstruido guardados em `partes`."""
    partes = Path(partes)
    seco, molhado = partes / "seco.wav", partes / "molhado.wav"
    if not seco.exists() or not molhado.exists():
        raise RuntimeError(f"partes do melhorar nao encontradas em {partes}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dose <= 0 or dose >= 100:
        shutil.copy2(str(seco if dose <= 0 else molhado), str(dst))
        return
    with tempfile.TemporaryDirectory() as tmp:
        bruto = Path(tmp) / "mix.wav"
        misturar(seco, molhado, dose, bruto)
        nivelar(bruto, dst, alvo)


def carrega_sidon(device="cuda"):
    """Carrega o modelo UMA vez. Em lote isso e a diferenca entre 9s e <1s por arquivo."""
    sys.path.insert(0, str(BASE))
    import sidon_cli
    import torch
    from transformers import AutoFeatureExtractor

    if device.startswith("cuda") and not torch.cuda.is_available():
        log("  ⚠ CUDA indisponivel — caindo para CPU (vai ficar lento)")
        device = "cpu"
    fe, dec = sidon_cli.load_models(device)
    pre = AutoFeatureExtractor.from_pretrained("facebook/w2v-bert-2.0")
    return sidon_cli, (fe, dec, pre, device)


def trata_um(src, dst, dose, alvo, sidon, partes=None):
    """A ordem aqui foi comprada com erro e nao deve ser mexida:
    1. Sidon PRIMEIRO (ele renormaliza o pico por dentro — nivelar antes e trabalho perdido)
    2. os dois lados no MESMO LUFS (senao a dose vira volume)
    3. mistura
    4. renivela (a soma incoerente perdeu ~3 dB)
    """
    import torch

    dst.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        seco = tmp / "seco.wav"
        antes = nivelar(src, seco, alvo)

        if dose <= 0 and not partes:
            shutil.move(str(seco), str(dst))
            return antes, None

        mod, (fe, dec, pre, device) = sidon
        cru = tmp / "sidon.wav"
        with torch.inference_mode():
            # "SIDON 3/18" e lido pelo servidor (roda_vendo) e vira a barra
            # do painel — formato combinado com _RE_SIDON no servidor.py
            mod.process_one(src, cru, fe, dec, pre, device, True,
                            lambda feito, total: log(f"SIDON {feito}/{total}"))

        nivelado = tmp / "molhado_nivelado.wav"
        nivelar(cru, nivelado, alvo)
        molhado = tmp / "molhado.wav"
        log(f"  alinhamento do Sidon: {alinhar(seco, nivelado, molhado):+.1f} ms")

        if partes:
            # guarda as duas pontas: o slider remistura depois sem rodar o Sidon de novo
            partes = Path(partes)
            partes.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(seco), str(partes / "seco.wav"))
            shutil.copy2(str(molhado), str(partes / "molhado.wav"))
            if dose <= 0:
                shutil.move(str(seco), str(dst))
                return antes, None

        if dose >= 100:
            shutil.move(str(molhado), str(dst))
            return antes, 100

        bruto = tmp / "mix.wav"
        misturar(seco, molhado, dose, bruto)
        nivelar(bruto, dst, alvo)   # <- o passo que eu esqueci no teste manual
        return antes, dose


def main():
    p = argparse.ArgumentParser(description="VOZ STUDIO — nivela e reconstroi voz.")
    p.add_argument("-i", "--input", required=True, help="arquivo ou pasta")
    p.add_argument("-o", "--output", default=None, help="arquivo ou pasta de saida")
    p.add_argument("--dose", default="completo",
                   help="0-100, ou um preset: " + " / ".join(PRESETS))
    p.add_argument("--lufs", type=float, default=-16.0,
                   help="alvo de volume percebido (padrao -16; o export final do video mira -14)")
    p.add_argument("--sufixo", default="_tratado")
    p.add_argument("--device", default="cuda")
    p.add_argument("--partes", default=None,
                   help="pasta onde guardar seco + reconstruido (um arquivo so) pro slider remisturar depois")
    p.add_argument("--remisturar", default=None,
                   help="pasta de partes de um melhorar anterior: so mistura na dose nova, sem Sidon")
    a = p.parse_args()

    dose = PRESETS.get(str(a.dose).lower())
    if dose is None:
        try:
            dose = max(0, min(100, int(float(a.dose))))
        except ValueError:
            print(json.dumps({"ok": False, "error": f"dose invalida: {a.dose}"}), file=sys.stderr)
            sys.exit(1)

    if a.remisturar:
        dst = Path(a.output)
        remisturar(a.remisturar, dst, dose, a.lufs)
        print(json.dumps({"ok": True, "dose": dose, "lufs": a.lufs, "files": [str(dst)]}, ensure_ascii=False))
        return

    src = Path(a.input)
    if not src.exists():
        print(json.dumps({"ok": False, "error": f"nao encontrado: {src}"}), file=sys.stderr)
        sys.exit(1)

    arquivos = (sorted(f for f in src.iterdir() if f.suffix.lower() in AUDIO_EXT)
                if src.is_dir() else [src])
    if not arquivos:
        print(json.dumps({"ok": False, "error": f"nenhum audio em {src}"}), file=sys.stderr)
        sys.exit(1)

    sidon = None
    if dose > 0 or a.partes:
        log("carregando o modelo (uma vez so)…")
        sidon = carrega_sidon(a.device)

    if a.output:
        saida_base = Path(a.output)
    else:
        saida_base = src if src.is_dir() else src.parent

    log(f"dose {dose}%  ·  alvo {a.lufs} LUFS  ·  {len(arquivos)} arquivo(s)")
    feitos = []
    for n, f in enumerate(arquivos, 1):
        if src.is_dir() or saida_base.is_dir():
            dst = saida_base / f"{f.stem}{a.sufixo}.wav"
        else:
            dst = saida_base
        try:
            partes = None
            if a.partes:
                partes = Path(a.partes) / f.stem if len(arquivos) > 1 else Path(a.partes)
            antes, _ = trata_um(f, dst, dose, a.lufs, sidon, partes)
            log(f"  [{n}/{len(arquivos)}] {f.name}: {antes:.1f} → {a.lufs:.1f} LUFS")
            feitos.append(str(dst))
        except Exception as exc:  # noqa: BLE001
            log(f"  [{n}/{len(arquivos)}] {f.name}: ERRO — {exc}")

    print(json.dumps({"ok": True, "dose": dose, "lufs": a.lufs, "files": feitos},
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        sys.exit(2)
