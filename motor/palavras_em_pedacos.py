# -*- coding: utf-8 -*-
"""Transcreve um wav longo PALAVRA A PALAVRA cortando em pedacos nos silencios.

Por que em pedacos: mandar 10 minutos de uma vez pro whisper-server faz o
modelo entrar em loop de repeticao (no EP02 saiu "suco de 10 do Morro da Cache
do Sul" 70 vezes seguidas). Em pedacos de ~40s cortados no silencio isso nao
acontece. Mesma receita que salvou os ditados do OpenWhispr.

Uso: python palavras_em_pedacos.py <wav> <porta> <alvo_seg> <saida.srt>
"""
import io
import os
import re
import subprocess
import sys
import urllib.request
import uuid

wav, porta, alvo, saida = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
tmp = os.path.join(os.path.dirname(os.path.abspath(saida)), "pedacos")
os.makedirs(tmp, exist_ok=True)

dur = float(subprocess.check_output([
    "ffprobe", "-v", "error", "-show_entries", "format=duration",
    "-of", "default=nw=1:nk=1", wav]).decode().strip())

# ---- silencios -----------------------------------------------------------
r = subprocess.run(["ffmpeg", "-hide_banner", "-i", wav, "-af",
                    "silencedetect=noise=-32dB:d=0.35", "-f", "null", "-"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
log = r.stderr or ""
sil = []
for e, d in re.findall(r"silence_end: ([0-9.]+) \| silence_duration: ([0-9.]+)", log):
    e, d = float(e), float(d)
    sil.append((e - d, e, d))
print("duracao=%.2fs silencios=%d" % (dur, len(sil)))

# ---- escolher os cortes --------------------------------------------------
cortes = [0.0]
while dur - cortes[-1] > alvo * 1.25:
    ant = cortes[-1]
    lo, hi = ant + alvo * 0.6, ant + alvo * 1.25
    cands = [c for c in sil if lo <= (c[0] + c[1]) / 2 <= hi and c[0] > ant + 1]
    if not cands:
        cortes.append(ant + alvo)
        continue
    def nota(c):
        meio = (c[0] + c[1]) / 2
        return abs(meio - (ant + alvo)) / alvo - min(c[2], 2.0) * 0.6
    melhor = min(cands, key=nota)
    cortes.append((melhor[0] + melhor[1]) / 2)
cortes.append(dur)
pedacos = [(cortes[i], cortes[i + 1]) for i in range(len(cortes) - 1)]
print("pedacos=%d" % len(pedacos))


def manda(caminho):
    """POST multipart no /inference do whisper-server, pedindo SRT por palavra."""
    lim = "----" + uuid.uuid4().hex
    campos = {"response_format": "srt", "language": "pt", "max_len": "1",
              "split_on_word": "true", "temperature": "0.0"}
    corpo = io.BytesIO()
    for k, v in campos.items():
        corpo.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                     % (lim, k, v)).encode("utf-8"))
    corpo.write(("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
                 "filename=\"a.wav\"\r\nContent-Type: audio/wav\r\n\r\n" % lim).encode("utf-8"))
    corpo.write(open(caminho, "rb").read())
    corpo.write(("\r\n--%s--\r\n" % lim).encode("utf-8"))
    req = urllib.request.Request(
        "http://127.0.0.1:%d/inference" % porta, data=corpo.getvalue(),
        headers={"Content-Type": "multipart/form-data; boundary=" + lim})
    return urllib.request.urlopen(req, timeout=1800).read().decode("utf-8", "replace")


def seg(t):
    h, m, resto = t.split(":")
    s, ms = resto.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def texto_srt(t):
    h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
    return "%02d:%02d:%02d,%03d" % (h, m, s, int(round((t - int(t)) * 1000)))


palavras = []
for i, (a, b) in enumerate(pedacos):
    corte = os.path.join(tmp, "p%03d.wav" % i)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % a,
                    "-t", "%.3f" % (b - a), "-i", wav, "-c:a", "pcm_s16le", corte],
                   check=True)
    bruto = manda(corte)
    achou = 0
    for bloco in re.split(r"\n\s*\n", bruto.strip()):
        linhas = [x for x in bloco.strip().split("\n") if x.strip()]
        tempo = next((x for x in linhas if "-->" in x), None)
        if not tempo:
            continue
        txt = " ".join(linhas[linhas.index(tempo) + 1:]).strip()
        if not txt:
            continue
        ini, _, fim = tempo.partition("-->")
        palavras.append((seg(ini.strip()) + a, seg(fim.strip()) + a, txt))
        achou += 1
    print("pedaco %d/%d (%.1f-%.1f) -> %d palavras" % (i + 1, len(pedacos), a, b, achou))

palavras.sort(key=lambda x: x[0])
with io.open(saida, "w", encoding="utf-8") as f:
    for n, (ini, fim, txt) in enumerate(palavras, 1):
        f.write("%d\n%s --> %s\n%s\n\n" % (n, texto_srt(ini), texto_srt(fim), txt))
print("OK: %d palavras em %s" % (len(palavras), saida))
