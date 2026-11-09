"""NIVELADOR (tipo Levelator) do Melhorar — 08/11/2026.

Pedido dele (08/11, o amigo testou o DUBX: «sinto que não fica nivelado 100% o áudio... alguns momentos o áudio não
compensa e fica tipo baixo»; ele: «falta um tratamento tipo aquele Levelator... adiciona um botão tipo de nivelador»).

O que faz (o mesmo princípio do Levelator — não é modelo de IA, é um «técnico com a mão no fader»):
1. mede o volume percebido (curva K da norma de loudness) a cada 50 ms e em janelas de 400 ms;
2. separa FALA de PAUSA pelo piso de ruído do próprio arquivo;
3. na fala, puxa cada trecho em direção ao volume típico da fala do arquivo (força leve/forte, com teto de ganho);
4. na pausa curta segura o ganho; na pausa longa volta pra 0 dB — assim não sobe ruído nem respiração no silêncio;
5. o ganho anda suave (abaixa rápido com antecipação de 300 ms, sobe devagar), sem bombear.
Quem chama renivela pro alvo de LUFS depois (voz.nivelar com picos suaves) — este arquivo só tira a diferença ENTRE trechos.
Só numpy + soundfile (roda no venv do motor, no PC do amigo também, sem baixar nada).
"""
import os
import numpy as np
import soundfile as sf

FORCAS = {
    # forca = quanto da diferença pro volume típico ele corrige; teto de subida/descida em dB
    # 08/11: teto do forte 12 → 20 dB (a fala baixa do teste estava 22 dB abaixo e o ClearerVoice apagava)
    "leve": dict(forca=0.6, sobe=8.0, desce=6.0, solta=1.2),
    "forte": dict(forca=0.9, sobe=20.0, desce=9.0, solta=0.8),
}
QUADRO = 0.05          # 50 ms
JANELA = 8             # 8 quadros = 400 ms (janela "momentânea" da norma)


def _peso_k(n, sr):
    """|H(f)|² aproximado da curva K (passa-alta em ~38 Hz + prateleira +4 dB acima de ~1,7 kHz)."""
    f = np.fft.rfftfreq(n, 1.0 / sr)
    pa = f ** 4 / (f ** 4 + 38.0 ** 4 + 1e-12)
    prat = 1.0 + (10 ** 0.4 - 1.0) * (f ** 2 / (f ** 2 + 1681.0 ** 2))
    return pa * prat


def potencia_quadros(x, sr):
    """Potência ponderada (curva K) de cada quadro de 50 ms, somando os canais."""
    n = int(round(QUADRO * sr))
    q = len(x) // n
    w = _peso_k(n, sr)
    pot = np.zeros(q)
    for a in range(0, q, 2000):
        b = min(q, a + 2000)
        bloco = x[a * n:b * n].reshape(b - a, n, -1)
        for c in range(bloco.shape[2]):
            X = np.fft.rfft(bloco[:, :, c], axis=1)
            pot[a:b] += (np.abs(X) ** 2 * w).sum(axis=1) / (n * n / 2.0)
    return pot, n


def mascara_fala(x, sr, quadros):
    """Onde tem FALA, por quadro de 50 ms, pelo Silero VAD (08/11: antes do ClearerVoice o fundo é contínuo — música,
    sala — e o volume sozinho não separa fala de fundo; o Silero separa). None = Silero não instalado."""
    try:
        import torch
        from silero_vad import load_silero_vad, get_speech_timestamps
    except Exception:
        return None
    m = x.mean(axis=1)
    # 16 kHz pro Silero: média de 3 em 3 a 48 kHz (passa-baixa simples), senão interpolação
    if sr == 48000:
        m16 = m[:len(m) // 3 * 3].reshape(-1, 3).mean(axis=1)
    else:
        t = np.arange(0, len(m) / sr, 1 / 16000.0)
        m16 = np.interp(t, np.arange(len(m)) / sr, m)
    # 08/11: o Silero perde fala BAIXA (teste: fala 22 dB abaixo + ruído de sala → não detectou). Só pra detectar,
    # cada pedaço vai pro mesmo volume (envelope de 1 s, até +30 dB) — o Silero decide pelo DESENHO da fala, não pelo nível.
    c = np.concatenate([[0.0], np.cumsum(m16.astype(np.float64) ** 2)])     # média móvel de 1 s por soma acumulada
    i = np.arange(len(m16)); a = np.clip(i - 8000, 0, len(m16)); b = np.clip(i + 8000, 0, len(m16))
    env = np.sqrt(np.maximum((c[b] - c[a]) / np.maximum(b - a, 1), 0.0))
    ref = np.percentile(env, 95)
    if ref > 0:
        m16 = m16 * np.minimum(ref / np.maximum(env, 1e-9), 10 ** 1.5)
        m16 = m16 / max(np.abs(m16).max(), 1e-9) * 0.9
    torch.set_num_threads(max(1, (os.cpu_count() or 4) // 2))
    modelo = load_silero_vad()
    trechos = get_speech_timestamps(torch.from_numpy(m16.astype(np.float32)), modelo, sampling_rate=16000,
                                    threshold=0.4, min_silence_duration_ms=250, speech_pad_ms=120)
    fala = np.zeros(quadros, bool)
    for t in trechos:
        a = int(t["start"] / 16000 / QUADRO); b = int(np.ceil(t["end"] / 16000 / QUADRO))
        fala[a:min(b, quadros)] = True
    return fala


def _db(p):
    return 10 * np.log10(np.maximum(p, 1e-12)) - 0.691


def curva(x, sr, forca="forte"):
    """Devolve (ganho_db por quadro, n, info)."""
    cfg = FORCAS[forca]
    pot, n = potencia_quadros(x, sr)
    if len(pot) < JANELA * 3:
        return np.zeros(len(pot)), n, {"fala_s": 0.0}
    k = np.ones(JANELA) / JANELA
    curto = _db(np.convolve(pot, k, mode="same"))            # 400 ms centrado
    fala = mascara_fala(x, sr, len(pot))
    detector = "silero"
    if fala is None:                                         # sem o Silero: reserva pelo piso de ruído
        detector = "volume"
        piso = np.percentile(curto, 5)
        fala = (curto > piso + 10.0) & (curto > -60.0)
    fala &= curto > -70.0
    if fala.mean() < 0.02:                                   # quase nada de fala: não mexe
        return np.zeros(len(pot)), n, {"fala_s": float(fala.sum() * QUADRO), "detector": detector}
    tipico = float(np.median(curto[fala]))
    desejo = np.clip(cfg["forca"] * (tipico - curto), -cfg["desce"], cfg["sobe"])

    # pausa: segura o ganho por até 0,6 s; depois vai pra min(ganho, 0) — não sobe o silêncio
    alvo = np.zeros(len(pot))
    ultimo, pausa = 0.0, 0
    segura = int(0.6 / QUADRO)
    for i in range(len(pot)):
        if fala[i]:
            ultimo, pausa = desejo[i], 0
            alvo[i] = ultimo
        else:
            pausa += 1
            alvo[i] = ultimo if pausa <= segura else min(ultimo, 0.0)

    # antecipação: abaixar já olhando 300 ms à frente (não deixa o começo de uma frase alta passar com ganho alto)
    frente = int(0.3 / QUADRO)
    adiante = np.array([alvo[i:i + frente + 1].min() for i in range(len(alvo))])
    a_desce = 1 - np.exp(-QUADRO / 0.12)
    a_sobe = 1 - np.exp(-QUADRO / cfg["solta"])
    g = np.zeros(len(pot))
    atual = adiante[0]
    for i in range(len(pot)):
        d = adiante[i]
        atual += (d - atual) * (a_desce if d < atual else a_sobe)
        g[i] = atual
    g = np.convolve(g, np.ones(5) / 5, mode="same")          # 250 ms: tira o degrau entre quadros

    def faixa(v):
        s = v[fala]
        return float(np.percentile(s, 95) - np.percentile(s, 10)) if len(s) > 10 else 0.0
    depois = curto + g
    info = {"detector": detector, "tipico_lufs": round(tipico, 1), "fala_s": round(float(fala.sum() * QUADRO), 1),
            "faixa_antes_db": round(faixa(curto), 1), "faixa_depois_db": round(faixa(depois), 1),
            "ganho_min_db": round(float(g.min()), 1), "ganho_max_db": round(float(g.max()), 1),
            "pausa_antes_db": round(float(np.median(curto[~fala])), 1) if (~fala).any() else None,
            "pausa_depois_db": round(float(np.median(depois[~fala])), 1) if (~fala).any() else None}
    return g, n, info


def nivelar_fala(src, dst, forca="forte"):
    """Aplica o nivelador em `src` e grava `dst` em float (o pico pode passar de 0 dBFS — quem chama renivela)."""
    x, sr = sf.read(str(src), always_2d=True, dtype="float64")
    g, n, info = curva(x, sr, forca)
    centros = (np.arange(len(g)) + 0.5) * n
    ganho = 10 ** (np.interp(np.arange(len(x)), centros, g) / 20.0)
    sf.write(str(dst), x * ganho[:, None], sr, subtype="FLOAT")
    return info


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", required=True); ap.add_argument("-o", required=True)
    ap.add_argument("--forca", default="forte", choices=list(FORCAS))
    a = ap.parse_args()
    print(json.dumps(nivelar_fala(a.i, a.o, a.forca), ensure_ascii=False))
