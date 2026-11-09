"""ClearerVoice (MossFormer2_SE_48K): LIMPA a voz (tira ruído/reverb) a 48 kHz — candidato a substituto/parceiro do
Sidon no Melhorar do DUBX (05/11, pesquisa: nenhum aberto bate o Sidon em reconstrução; este só limpa, mas ele quer
ouvir). Uso: rodar_clearervoice_se.py -i entrada.wav -o saida.wav
⚠️ o clearvoice le pelo pydub e quebra com ESPACO no caminho — a entrada vai pra uma pasta de trabalho sem espaco.

08/11 (Kakashi 21:48, ele: «tá dando uns pipocos, tá clipando o áudio... vê se tem alguma forma de você recuperar a
informação desses áudios clipados... melhorando a gente já adota isso como default aqui pro Clear Voice»). Dois consertos:
1. PIPOCO = bug do clearvoice: ele grava com `result.astype(np.int16)` SEM limitar — amostra que passa de 1,0 DÁ A VOLTA
   (−1,0001 vira +1,0), estalo de fundo de escala (1.119 no «audio fix» de 16 min). Agora a saída sai em FLOAT
   (online_write=False + soundfile), nada dá a volta. A entrada passa intacta: fora dos pontos consertados, a saída é a
   mesma de antes.
2. TOPO CORTADO herdado da entrada (gravação clipada: platôs no 0 dBFS): depois do ClearerVoice, as amostras que eram
   platô na entrada são refeitas pelo modelo AR da voz em volta (Janssen com restrição: passa do teto, mesmo sinal). Medido
   em clipe artificial da voz dele: +18 dB nas amostras refeitas (SPADE +9, spline +10). Só essas amostras mudam.
   A saída pode passar de 1,0 (é float); quem nivela (voz.py --picos-suaves) segura os picos sem estalo.
3. 08/11 ~23h (ele: «coloca o Levelator na pré-etapa... nivela antes, pra depois passar o Clear Voice... o modelo
   ignora umas falas que estão muito baixas»): ORDEM NOVA = refaz o topo cortado NA ENTRADA → nivelador (opcional,
   DUBX\\motor\\nivelador.py, detector de voz Silero) → ClearerVoice em float direto pelo decode (sem arquivo de 16 bits).
   Teste controlado (fala 22 dB abaixo + ruído de sala): sem nivelador o ClearerVoice APAGOU a fala (−40 dB); com o
   forte ela ficou (−7,5 dB). `--declipado` = o mesmo áudio antes do modelo (lado original do slider).
"""
import sys, os, json, argparse, warnings, shutil
warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np
import soundfile as sf

AQUI = Path(__file__).parent
os.chdir(AQUI)                             # o clearvoice baixa checkpoints relativo ao cwd


def platos(x, minimo=2):
    """Máscara das amostras clipadas: platôs (>= `minimo` amostras seguidas) no pico do arquivo (ou −pico)."""
    teto = float(np.abs(x).max())
    m = np.zeros(len(x), bool)
    if teto <= 0:
        return m
    lim = teto * 0.9995
    for alto in (x >= lim, x <= -lim):
        d = np.diff(np.concatenate([[0], alto.astype(np.int8), [0]]))
        for a, b in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
            if b - a >= minimo:
                m[a:b] = True
    return m


def janssen(y, m, p=64, ctx=2048, iters=4):
    """Refaz as amostras `m` de `y` pelo modelo AR (fala = ressonâncias) estimado em volta (Janssen 1986), com a restrição
    de clipe: o valor refeito tem o MESMO sinal e pelo menos o módulo que já tinha (o platô era um piso, não o valor)."""
    from scipy.linalg import solve_toeplitz
    import scipy.sparse as sp
    import scipy.sparse.linalg as spl
    y = np.asarray(y, np.float64).copy()
    piso = np.abs(y)
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    blocos = []
    for a, b in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
        if blocos and a - blocos[-1][1] < ctx // 2:
            blocos[-1][1] = b
        else:
            blocos.append([a, b])
    for a, b in blocos:
        s0, s1 = max(0, a - ctx), min(len(y), b + ctx)
        seg = y[s0:s1].copy()
        mm = m[s0:s1]
        sinal = np.sign(seg)
        pi = piso[s0:s1]
        n = len(seg)
        if n <= 2 * p:
            continue
        for _ in range(iters):
            r = np.correlate(seg, seg, "full")[n - 1:n + p]
            r[0] *= 1.0 + 1e-6
            coef = np.r_[1.0, solve_toeplitz(r[:p], -r[1:p + 1])]
            E = sp.diags([coef[p - j] * np.ones(n - p) for j in range(p + 1)], list(range(p + 1)),
                         shape=(n - p, n)).tocsc()
            livres = mm.copy()
            for _q in range(6):      # conjunto ativo: quem fura o piso fica preso no piso
                u = np.where(livres)[0]
                k = np.where(~livres)[0]
                if len(u) == 0:
                    break
                Eu, Ek = E[:, u], E[:, k]
                seg[u] = spl.spsolve((Eu.T @ Eu).tocsc(), Eu.T @ (-(Ek @ seg[k])))
                viol = mm & ((np.abs(seg) < pi) | (np.sign(seg) != sinal))
                if not viol.any():
                    break
                seg[viol] = sinal[viol] * pi[viol]
                livres = livres & ~viol
        y[s0:s1][mm] = seg[mm]
    return y, len(blocos)


def main():
    from clearvoice import ClearVoice
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", required=True)
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--sem-declip", action="store_true", help="não refaz o topo cortado da entrada (só a saída em float)")
    ap.add_argument("--declipado", default=None,
                    help="08/11 (ele: «Sim, os dois lados»): grava aqui a ENTRADA com o topo refeito (float) — vira o lado "
                         "original do slider, senão abaixo de 100%% o clipe da gravação volta")
    ap.add_argument("--nivelador", default="desligado", choices=["desligado", "leve", "medio", "forte"],
                    help="08/11 (ele: «coloca o Levelator na pré-etapa... nivela antes, pra depois passar o Clear Voice»)")
    ap.add_argument("--nivelador-dir", default=None, help="pasta do nivelador.py (o motor do DUBX)")
    a = ap.parse_args()

    # ORDEM (08/11, 23h): 1) refaz o topo cortado da gravação  2) nivelador (fala baixa sobe ANTES do modelo, senão o
    # ClearerVoice trata como ruído e apaga)  3) ClearerVoice em float, direto pelo decode dele (sem arquivo de 16 bits:
    # o topo refeito passa de 1,0 e nada dá a volta). O lado original do slider = o mesmo áudio dos passos 1+2.
    x, sr = sf.read(a.input, always_2d=True, dtype="float64")
    if sr != 48000:                      # o MossFormer2_SE_48K trabalha a 48 kHz (o clearvoice reamostrava por dentro)
        import librosa
        x = np.stack([librosa.resample(x[:, c], orig_sr=sr, target_sr=48000) for c in range(x.shape[1])], axis=1)
        sr = 48000
    canais = x.shape[1]
    refeitos = []
    if not a.sem_declip:
        for c in range(canais):
            m = platos(x[:, c])
            refeitos.append(int(m.sum()))
            if m.any():
                x[:, c], _ = janssen(x[:, c], m)
    nivel = None
    if a.nivelador != "desligado":
        sys.path.insert(0, str(a.nivelador_dir or (AQUI.parent / "DUBX" / "motor")))
        import nivelador as NV
        g, n, nivel = NV.curva(x, sr, a.nivelador)
        centros = (np.arange(len(g)) + 0.5) * n
        x = x * (10 ** (np.interp(np.arange(len(x)), centros, g) / 20.0))[:, None]
    if a.declipado:
        Path(a.declipado).parent.mkdir(parents=True, exist_ok=True)
        sf.write(a.declipado, x, sr, subtype="FLOAT")

    from clearvoice import ClearVoice
    import torch
    cv = ClearVoice(task="speech_enhancement", model_names=["MossFormer2_SE_48K"])
    rede = cv.models[0]
    rede.data = {"audio": [x[:, c].astype(np.float32).reshape(1, -1) for c in range(canais)], "audio_len": len(x)}
    with torch.no_grad():
        out = np.asarray(rede.decode(), dtype=np.float64)       # (canais, amostras), sem normalizar (use_norm=False no SE_48K)
    out = out.reshape(canais, -1).T[:len(x)]
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    sf.write(a.output, out, sr, subtype="FLOAT")
    print(json.dumps({"ok": True, "saida": a.output, "pico": round(float(np.abs(out).max()), 4),
                      "amostras_refeitas": refeitos, "nivelador": nivel}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
