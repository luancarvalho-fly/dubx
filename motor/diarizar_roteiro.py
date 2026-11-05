# -*- coding: utf-8 -*-
"""
DUBX — SEPARAR POR ROTEIRO: uma faixa por personagem, usando o TEXTO.

⚠️ POR QUE ESTE EXISTE (19/08/2026). Os tres motores acusticos empataram no
CHAO DO ACASO no material dele (12/16 = chutar "e sempre o Plexus"), porque o
Seedance gera os DOIS personagens com a MESMA VOZ — provado por tres medicoes:
embedding com margem negativa (mesma voz 0,633 x pessoas diferentes 0,614), o
Sortformer devolvendo UM locutor so, e o mesmo Sortformer acertando 2 num
arquivo de controle com vozes de verdade diferentes.

Nao existe informacao de TIMBRE pra separar. Mas existe informacao de TEXTO: o
roteiro do Fly X-tudio diz exatamente quem fala o que. Entao:
    Whisper (palavra a palavra, do AUDIO)  ->  QUANDO
    roteiro (personagem de cada fala)      ->  QUEM

⭐ O LIPSYNC NAO CORRE RISCO, e isso e estrutural, nao promessa: o roteiro so
ROTULA intervalos. Os tempos saem do audio real (Whisper) e o corte e feito no
ORIGINAL, amostra por amostra. Nada e esticado, deslocado ou regerado — somar
as faixas de volta reconstroi o arquivo. O script confere isso no fim.

Uso:
    .venv/Scripts/python.exe diarizar_roteiro.py -i audio.wav -o pasta \\
        --srt palavras.srt --roteiro roteiro.txt
"""
import argparse
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path

PULO = 0.01          # grade de 10 ms, igual aos outros motores
FADE = 0.015
MIN_BLOCO = 0.12     # bloco menor que isso vira do vizinho
COLA = 0.30          # buraco menor que isso dentro da MESMA fala nao corta


def normaliza(txt):
    txt = unicodedata.normalize("NFD", txt.lower())
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    return [p for p in re.sub(r"[^a-z0-9\s]", " ", txt).split() if p]


def le_srt(caminho):
    """Blocos do SRT -> (inicio, fim, texto). Com max_len=1 cada bloco e ~1 palavra."""
    def seg(t):
        h, m, r = t.split(":")
        s, ms = r.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    itens = []
    bruto = Path(caminho).read_text(encoding="utf-8-sig", errors="ignore").strip()
    for bloco in re.split(r"\n\s*\n", bruto):
        linhas = [l for l in bloco.strip().split("\n") if l.strip()]
        tempo = next((l for l in linhas if "-->" in l), None)
        if not tempo:
            continue
        a, b = [x.strip() for x in tempo.split("-->")]
        texto = " ".join(linhas[linhas.index(tempo) + 1:]).strip()
        if texto:
            itens.append((seg(a), seg(b), texto))
    return itens


def le_roteiro(caminho):
    """Falas do roteiro -> [(personagem, [palavras])].

    Aceita os formatos que saem do Fly X-tudio e o jeito que ele colaria na mao:
        Plexus: fala...        |  Plexus — fala...  |  PLEXUS\\n fala...
    Linha sem personagem conhecido gruda na fala anterior (continuacao)."""
    falas = []
    texto = Path(caminho).read_text(encoding="utf-8-sig", errors="ignore")
    pendente = None
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        # "Nome: fala" / "Nome - fala" / "Nome — fala"
        m = re.match(r"^([A-Za-zÀ-ÿ][\w À-ÿ'\.]{1,28}?)\s*[:\-—–]\s*(.+)$", linha)
        if m and len(normaliza(m.group(1))) <= 3:
            falas.append((m.group(1).strip(), normaliza(m.group(2))))
            pendente = None
            continue
        # linha so com o nome (fala vem na proxima)
        if len(linha) <= 28 and not re.search(r"[.!?]", linha) and len(normaliza(linha)) <= 3:
            pendente = linha.strip()
            continue
        if pendente:
            falas.append((pendente, normaliza(linha)))
            pendente = None
        elif falas:
            falas[-1][1].extend(normaliza(linha))   # continuacao da fala anterior
    return [(p, w) for p, w in falas if w]


def main():
    p = argparse.ArgumentParser(description="Separa por personagem usando o roteiro")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--srt", required=True, help="SRT palavra a palavra (max_len=1)")
    p.add_argument("--roteiro", required=True, help="txt com 'Personagem: fala'")
    a = p.parse_args()

    import numpy as np
    import soundfile as sf

    pasta = Path(a.output)
    pasta.mkdir(parents=True, exist_ok=True)

    orig, sr = sf.read(a.input, dtype="float32", always_2d=True)
    dur = len(orig) / sr

    blocos_srt = le_srt(a.srt)
    falas = le_roteiro(a.roteiro)
    if not blocos_srt:
        raise RuntimeError("a transcrição veio vazia")
    if not falas:
        raise RuntimeError("não achei fala nenhuma no roteiro (use 'Personagem: fala')")

    personagens = []
    for nome, _ in falas:
        if nome not in personagens:
            personagens.append(nome)
    print(f"  {dur:.1f}s · {len(blocos_srt)} palavra(s) ouvida(s) · "
          f"{len(falas)} fala(s) no roteiro · {len(personagens)} personagem(ns)", flush=True)

    # ---- alinhamento: palavra ouvida  <->  palavra do roteiro -------------
    # Os dois vem NA MESMA ORDEM (o roteiro e a ordem em que foi gravado),
    # entao um alinhamento monotonico resolve. O SequenceMatcher aguenta o
    # Whisper errar/pular palavra: o que casa ancora, o resto interpola.
    palavras_audio = []          # (ini, fim, palavra)
    for ini, fim, txt in blocos_srt:
        ws = normaliza(txt)
        if not ws:
            continue
        passo = (fim - ini) / len(ws)
        for i, w in enumerate(ws):
            palavras_audio.append((ini + i * passo, ini + (i + 1) * passo, w))

    dono_roteiro = []            # personagem de cada palavra do roteiro
    for nome, ws in falas:
        dono_roteiro.extend([nome] * len(ws))
    palavras_roteiro = [w for _, ws in falas for w in ws]

    sm = difflib.SequenceMatcher(
        None, [w for _, _, w in palavras_audio], palavras_roteiro, autojunk=False)
    dono_audio = [None] * len(palavras_audio)
    casadas = 0
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            dono_audio[i + k] = dono_roteiro[j + k]
            casadas += 1

    # palavra que nao casou herda a vizinha (o Whisper erra, o dono nao muda)
    ultimo = None
    for i in range(len(dono_audio)):
        if dono_audio[i] is None:
            dono_audio[i] = ultimo
        else:
            ultimo = dono_audio[i]
    for i in range(len(dono_audio) - 1, -1, -1):     # o comeco, de tras pra frente
        if dono_audio[i] is None:
            dono_audio[i] = dono_audio[i + 1] if i + 1 < len(dono_audio) else personagens[0]
    print(f"  {casadas}/{len(palavras_audio)} palavras casaram com o roteiro "
          f"({casadas/len(palavras_audio):.0%})", flush=True)

    # ---- palavras -> intervalos por personagem ---------------------------
    trechos = {nome: [] for nome in personagens}
    atual, ini_atual, fim_atual = None, None, None
    for (ini, fim, _), nome in zip(palavras_audio, dono_audio):
        if nome == atual and ini - fim_atual <= COLA:
            fim_atual = max(fim_atual, fim)
        else:
            if atual is not None and fim_atual - ini_atual >= MIN_BLOCO:
                trechos[atual].append((ini_atual, fim_atual))
            atual, ini_atual, fim_atual = nome, ini, fim
    if atual is not None and fim_atual - ini_atual >= MIN_BLOCO:
        trechos[atual].append((ini_atual, fim_atual))

    # ---- recorta o ORIGINAL ---------------------------------------------
    fade = max(1, int(FADE * sr))
    saidas, tempos, soma = [], [], np.zeros_like(orig)
    mascara_total = np.zeros(len(orig), dtype="float32")
    for nome in personagens:
        mascara = np.zeros(len(orig), dtype="float32")
        falado = 0.0
        for ini, fim in trechos[nome]:
            i0 = max(0, int(ini * sr))
            i1 = min(len(orig), int(fim * sr))
            if i1 <= i0:
                continue
            mascara[i0:i1] = 1.0
            f = min(fade, (i1 - i0) // 2)
            if f > 0:
                mascara[i0:i0 + f] = np.linspace(0, 1, f, dtype="float32")
                mascara[i1 - f:i1] = np.linspace(1, 0, f, dtype="float32")
            falado += fim - ini
        seguro = "".join(c for c in nome if c.isalnum() or c in " _-").strip() or "voz"
        destino = pasta / f"{seguro}.wav"
        faixa = orig * mascara[:, None]
        soma += faixa
        mascara_total += mascara
        sf.write(str(destino), faixa, sr, subtype="PCM_24")
        saidas.append(str(destino))
        tempos.append(round(falado, 2))
        print(f"  {nome}: {falado:.1f}s de fala", flush=True)

    # ---- prova de que o lipsync ficou intacto ----------------------------
    # A soma das faixas tem que reconstruir o original nos trechos de fala.
    # Isso e verificacao, nao promessa: se alguem mexer no tempo um dia, quebra.
    # So onde a mascara vale 1 CHEIO. Nas rampas de fade o sinal e atenuado de
    # proposito, entao comparar ali acusaria diferenca que nao e desalinhamento.
    cheio = mascara_total >= 0.999
    if cheio.any():
        res = float(np.abs(orig[cheio] - soma[cheio]).max())
        print(f"  conferencia de lipsync: diferenca maxima vs original = {res:.2e} "
              f"({cheio.sum()/sr:.1f}s conferidos · 0 = nada saiu do lugar)", flush=True)
    else:
        res = 0.0

    print(json.dumps({"ok": True, "vozes": saidas, "n": len(personagens),
                      "personagens": personagens, "segundos": tempos,
                      "taxa": sr, "casadas": round(casadas / len(palavras_audio), 3),
                      "residuo": float(res), "motor": "roteiro"}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        sys.exit(1)
