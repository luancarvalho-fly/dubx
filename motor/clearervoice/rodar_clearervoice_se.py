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
"""
import sys, os, json, argparse, warnings, shutil, uuid
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
    a = ap.parse_args()

    trab = AQUI / "trabalho" / ("se_" + uuid.uuid4().hex[:8]); trab.mkdir(parents=True, exist_ok=True)
    try:
        ent = trab / "entrada.wav"
        shutil.copy2(a.input, ent)
        info = sf.info(str(ent))
        cv = ClearVoice(task="speech_enhancement", model_names=["MossFormer2_SE_48K"])
        out = cv(input_path=str(ent), online_write=False)
        out = np.asarray(out, dtype=np.float64)
        canais = info.channels
        if out.ndim == 1:
            out = out[:, None]
        elif out.shape[0] == canais and out.shape[1] != canais:
            out = out.T                      # o clearvoice devolve (canais, amostras)
        if out.shape[1] != canais:
            out = out[:, :1].repeat(canais, axis=1) if out.shape[1] == 1 else out[:, :canais]
        # o clearvoice reamostra pra 48 kHz por dentro e só voltava à taxa original no write_audio (que não usamos mais)
        sr = info.samplerate if abs(len(out) - info.frames) <= 1 else 48000
        refeitos = []
        if not a.sem_declip:
            x, srx = sf.read(str(ent), always_2d=True, dtype="float64")
            if srx == sr and abs(len(x) - len(out)) <= 1:
                n = min(len(x), len(out)); x = x[:n]; out = out[:n]
                orig = x.copy()
                for c in range(canais):
                    m = platos(x[:, c])
                    if m.any():
                        out[:, c], nb = janssen(out[:, c], m)
                        orig[:, c], _ = janssen(x[:, c], m)
                        refeitos.append(int(m.sum()))
                    else:
                        refeitos.append(0)
                if a.declipado:
                    Path(a.declipado).parent.mkdir(parents=True, exist_ok=True)
                    sf.write(a.declipado, orig, srx, subtype="FLOAT")
        if a.declipado and not Path(a.declipado).exists():
            shutil.copy2(a.input, a.declipado)   # sem declip (ou taxa diferente): o lado original fica o próprio arquivo
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        sf.write(a.output, out, sr, subtype="FLOAT")
    finally:
        shutil.rmtree(trab, ignore_errors=True)
    print(json.dumps({"ok": True, "saida": a.output, "pico": round(float(np.abs(out).max()), 4),
                      "amostras_refeitas": refeitos}), flush=True)


if __name__ == "__main__":
    main()
