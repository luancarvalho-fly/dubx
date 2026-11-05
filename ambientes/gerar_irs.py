# -*- coding: utf-8 -*-
"""Gera as respostas de impulso (IR) sinteticas dos ambientes do DUBX.

Cada IR e um "retrato acustico" de um espaco: pre-delay (distancia da parede
mais proxima), reflexoes iniciais esparsas (paredes individuais) e uma cauda
difusa de ruido com decaimento exponencial (RT60) filtrada por um one-pole
lowpass (superficie dura = cauda brilhante; estofado/terra = cauda escura).
O ffmpeg aplica com afir (convolucao) — reverb honesto, sem plugin.

So biblioteca padrao, de proposito (mesma regra do servidor.py). Rodar uma
vez: py gerar_irs.py — grava em irs/*.wav (48 kHz mono 16-bit). Deterministico
(seed fixa) pra regenerar identico em qualquer maquina.
"""
import math
import random
import struct
import wave
from pathlib import Path

FS = 48000
AQUI = Path(__file__).resolve().parent
SAIDA = AQUI / "irs"

# nome: (rt60 s, pre-delay ms, brilho Hz (lowpass da cauda), refl. iniciais)
ESPACOS = {
    "banheiro": (0.45,  4, 6000, 14),   # azulejo: curto, denso e brilhante
    "quarto":   (0.22,  3, 3000,  8),   # comodo pequeno com moveis: quase seco
    "hall":     (2.40, 25, 4200, 10),   # igreja/teatro: longo e cheio
    "galpao":   (1.10, 15, 3400, 12),   # estacionamento/galpao: medio e oco
    "caverna":  (3.20, 45, 2400,  6),   # enorme, escuro, reflexoes espacadas
    "carro":    (0.10,  1, 1500, 10),   # cabine estofada: minusculo e abafado
}


def one_pole(amostras, corte):
    """Lowpass de um polo — escurece a cauda como o ar e as superficies fazem."""
    a = 1.0 - math.exp(-2.0 * math.pi * corte / FS)
    y, saida = 0.0, []
    for x in amostras:
        y += a * (x - y)
        saida.append(y)
    return saida


def gera_ir(rt60, pre_ms, brilho, n_refl, rng):
    n = int(FS * (rt60 * 1.15 + pre_ms / 1000.0)) + 1
    ir = [0.0] * n
    pre = int(FS * pre_ms / 1000.0)

    # reflexoes iniciais: impulsos esparsos ate ~80 ms, decaindo
    for i in range(n_refl):
        pos = pre + int(FS * (0.004 + 0.076 * (i / max(1, n_refl - 1)) ** 1.5))
        if pos < n:
            ir[pos] += rng.choice((-1.0, 1.0)) * (0.9 ** i) * 0.6

    # cauda difusa: ruido gaussiano com decaimento exponencial (RT60)
    queda = math.log(1000.0) / (rt60 * FS)      # -60 dB em rt60 segundos
    cauda = [rng.gauss(0.0, 1.0) * math.exp(-queda * i) for i in range(n - pre)]
    cauda = one_pole(cauda, brilho)
    for i, v in enumerate(cauda):
        ir[pre + i] += v * 0.35

    # energia total = 1: o peso do wet nos presets fica comparavel entre IRs
    energia = math.sqrt(sum(v * v for v in ir)) or 1.0
    return [v / energia for v in ir]


def salva(nome, canais):
    """canais: lista de 1 (mono) ou 2 (estereo) listas de amostras."""
    SAIDA.mkdir(parents=True, exist_ok=True)
    pico = max(abs(v) for c in canais for v in c) or 1.0
    ganho = min(1.0, 0.97 / pico)      # nunca clipa o 16-bit
    n = min(len(c) for c in canais)
    with wave.open(str(SAIDA / f"{nome}.wav"), "wb") as w:
        w.setnchannels(len(canais))
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, c[i] * ganho)) * 32767))
            for i in range(n) for c in canais))


if __name__ == "__main__":
    for nome, (rt60, pre, brilho, refl) in ESPACOS.items():
        # Duas IRs MONO independentes (mesmas reflexoes no tempo, caudas de
        # ruido diferentes): em arquivo estereo o servidor convolui cada canal
        # com a sua (channelsplit -> afir x2 -> join) = sala de verdade, nao
        # "no meio da cabeca". IR estereo NUM arquivo so nao serve: o afir
        # desta build ignora o segundo canal (medido em 15/08: L-R -54 dB).
        esq = gera_ir(rt60, pre, brilho, refl, random.Random(f"dubx-{nome}"))
        dir_ = gera_ir(rt60, pre, brilho, refl, random.Random(f"dubx-{nome}-R"))
        salva(nome, [esq])
        salva(f"{nome}_R", [dir_])
        print(f"{nome}: {len(esq) / FS:.2f} s (L + R)")
    print(f"IRs em {SAIDA}")
