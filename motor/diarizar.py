# -*- coding: utf-8 -*-
"""
DUBX — SEPARAR POR LOCUTOR (diarizacao): quem fala quando, uma faixa por voz.

Pedido do Luan (19/08/2026): "o modelo nao reconhece vozes diferentes de forma
automatica, tipo so separar as vozes? e pra isso que eu quero o separador, ate
pra tratar as faixas como stems".

⚠️ NAO CONFUNDIR com o separar.py (SepFormer). Sao problemas diferentes:
  · SepFormer  = duas pessoas falando AO MESMO TEMPO, uma por cima da outra.
                 Em dialogo alternado ele nao tem o que separar e devolve lixo
                 (medido em 19/08: a "voz 1" saia com a transcricao IGUAL a da
                 mistura).
  · Este aqui  = dialogo ALTERNADO, que e o material real dele.

VANTAGEM: isto so RECORTA o original (nao reconstroi nada), entao os stems saem
na qualidade cheia — 48 kHz, estereo, sem passar por modelo. O SepFormer
devolvia 16 kHz chapado.

⚠️ POR QUE JANELA DESLIZANTE, e nao "um embedding por trecho de fala"
(primeira tentativa, 19/08 — nao funcionou, e vale registrar o porque):
medir o timbre de um TRECHO inteiro so serve se o trecho tiver UMA voz. Num
dialogo rapido isso e falso: o detector de fala emenda turnos separados por
pausa curta e um "trecho" de 10 s contem os DOIS. Embedding de mistura nao
agrupa — deu 30,8 s contra 0,4 s, tudo no mesmo balaio. A janela deslizante
curta e independente das pausas: cada janela quase sempre tem uma voz so, e a
troca de locutor aparece entre janelas vizinhas.

Uso:
    .venv-sep/Scripts/python.exe diarizar.py -i mistura.wav -o pasta [--vozes 2]
--vozes 0 (padrao) descobre sozinho quantas pessoas falam.
"""
import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.simplefilter("ignore")   # lazy imports do speechbrain explodem no py3.13

MODELO_VOZ = "speechbrain/spkrec-ecapa-voxceleb"
TAXA_ANALISE = 16000

QUADRO = 0.025       # 25 ms de janela pra medir energia
PULO = 0.010         # 10 ms de passo — e a resolucao de tudo daqui pra frente
FECHA_BURACO = 0.20  # so respiracao; maior que isto emenda TURNO com TURNO
MIN_FALA = 0.20

JANELA_EMB = 1.5     # janela de timbre: curta o bastante pra ter 1 voz so
PULO_EMB = 0.375     # 4 janelas cobrindo cada ponto = voto com folga
COBERTURA_MIN = 0.6  # janela com menos fala que isso nao vota

# 2a passada: resolve turno curto que a janela de 1,5 s borra.
# ⚠️ 1,2 s NAO e chute — foi medido (19/08) contra gabarito escrito na mao no
# dialogo Plexus x Pedaleira, comparando 0,6 / 0,9 / 1,2 s:
#   so 1a passada  13/17 frases inteiras · 13/16 no personagem certo
#   fina 0,6 s     12/17 · 14/16
#   fina 0,9 s     14/17 · 12/16
#   fina 1,2 s     13/17 · 14/16   <- melhor acerto, com fronteira boa
# Encurtar mais piora: abaixo de ~1 s o ECAPA fica instavel e a fronteira
# ganha ruido em vez de precisao. ⚠️ Calibrado num arquivo so.
JANELA_FINA = 1.2
PULO_FINO = 0.3
SUAVIZA = 0.25       # mediana movel no rotulo: mata tremulacao de 1 janela
MIN_FALA_SAIDA = 0.30  # pedaco de locutor menor que isso vira do vizinho
FADE = 0.015         # 15 ms nas bordas: sem isso a emenda estala
MAX_VOZES = 6


def mascara_de_fala(np, x, sr):
    """Vetor booleano por quadro de 10 ms: onde tem fala. Limiar adaptativo
    tirado do proprio arquivo."""
    n = int(QUADRO * sr)
    h = int(PULO * sr)
    if len(x) < n:
        return np.zeros(0, dtype=bool)
    quadros = 1 + (len(x) - n) // h
    rms = np.empty(quadros, dtype="float32")
    for i in range(quadros):
        p = x[i * h:i * h + n]
        rms[i] = np.sqrt((p * p).mean()) + 1e-9
    db = 20 * np.log10(rms)
    piso = np.percentile(db, 10)
    teto = np.percentile(db, 95)
    limiar = max(piso + 10.0, teto - 25.0)
    ativo = db > limiar

    def preenche(v, alvo, maximo):
        """Troca corridas curtas de `alvo` pelo oposto (fecha buraco / mata ilha)."""
        i = 0
        while i < len(v):
            if v[i] == alvo:
                j = i
                while j < len(v) and v[j] == alvo:
                    j += 1
                if (j - i) <= maximo and (i > 0 or alvo):
                    v[i:j] = not alvo
                i = j
            else:
                i += 1
        return v

    ativo = preenche(ativo, False, int(FECHA_BURACO / PULO))   # respiracao
    ativo = preenche(ativo, True, int(MIN_FALA / PULO))        # estalo isolado
    return ativo


def janelas_de_timbre(np, fala, dur):
    """Janelas fixas deslizando pelo arquivo. Fica so com as que tem fala de
    verdade — janela quase vazia gera embedding de ruido e suja o agrupamento."""
    jan = []
    t = 0.0
    while t + JANELA_EMB <= dur + 1e-6:
        i0, i1 = int(t / PULO), int((t + JANELA_EMB) / PULO)
        trecho = fala[i0:min(i1, len(fala))]
        if len(trecho) and trecho.mean() >= COBERTURA_MIN:
            jan.append((t, t + JANELA_EMB))
        t += PULO_EMB
    return jan


def silhueta(np, D, rot):
    """Silhueta media a partir da matriz de distancia — mede se a divisao em
    N grupos e boa. Sem sklearn: e umas 10 linhas."""
    n = len(rot)
    grupos = np.unique(rot)
    if len(grupos) < 2 or n <= len(grupos):
        return -1.0
    s = np.zeros(n)
    for i in range(n):
        mesmos = rot == rot[i]
        mesmos[i] = False
        if not mesmos.any():
            s[i] = 0.0
            continue
        a = D[i, mesmos].mean()
        b = min(D[i, rot == g].mean() for g in grupos if g != rot[i])
        s[i] = (b - a) / max(a, b)
    return float(s.mean())


def agrupa(np, E, n_vozes):
    """Agrupamento hierarquico por cosseno. Com n_vozes=0, testa 1..MAX_VOZES
    e fica com o que tiver a melhor silhueta (com piso: silhueta fraca = uma
    voz so, nao invente locutor)."""
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import pdist, squareform

    if len(E) < 2:
        return np.ones(len(E), dtype=int), E, np.zeros((1, E.shape[1]), dtype="float32")

    # ⚠️ CENTRAR e o que faz isto funcionar (medido em 19/08). Embedding cru
    # carrega o vies da GRAVACAO — microfone, sala, codec — e esse vies domina
    # a distancia: no material dele a MESMA voz em dois momentos deu 0,633,
    # tao longe quanto duas pessoas diferentes (0,614). Subtrair a media do
    # proprio arquivo tira o que e comum a todo mundo e sobra o timbre.
    # Sem centrar: 2 x 68 janelas (lixo). Centrado: 18 x 52, alternando em
    # blocos que batem com os turnos do dialogo.
    media = E.mean(axis=0, keepdims=True)
    Ec = E - media
    Ec = Ec / (np.linalg.norm(Ec, axis=1, keepdims=True) + 1e-9)

    d = pdist(Ec, metric="cosine")
    D = squareform(d)
    lig = linkage(d, method="average")

    if n_vozes and n_vozes >= 1:
        return fcluster(lig, t=n_vozes, criterion="maxclust"), Ec, media

    # ⚠️ A silhueta sozinha PREMIA divisao degenerada: separar 2 janelas
    # esquisitas das outras 68 da silhueta 0,408 e nao serve pra nada. Entao
    # so vale k cujo menor grupo tenha uma fatia de verdade da fala.
    melhor, melhor_s = np.ones(len(E), dtype=int), -1.0
    for k in range(2, min(MAX_VOZES, len(E)) + 1):
        rot = fcluster(lig, t=k, criterion="maxclust")
        if len(np.unique(rot)) < k:
            continue
        menor = min((rot == g).sum() for g in np.unique(rot)) / len(rot)
        if menor < 0.10:                     # grupo de migalha: nao e locutor
            continue
        s = silhueta(np, D, rot)
        if s > melhor_s:
            melhor, melhor_s = rot, s
    if melhor_s < 0.10:                      # nada se distingue: uma voz so
        return np.ones(len(E), dtype=int), Ec, media
    return melhor, Ec, media


def refina_fino(np, torch, enc, ana, fala, Ec, rotulos, media, device):
    """SEGUNDA PASSADA — e daqui que sai a precisao de fronteira.

    A janela de 1,5 s da 1a passada e confiavel pra DESCOBRIR quem sao as
    pessoas, mas grossa demais pra dizer ONDE cada turno comeca: resposta
    curta ("Ah, sei la") tem menos de 1 s e fica borrada. Medido em 19/08:
    24% das frases saiam PARTIDAS entre as duas faixas.

    Aqui os centroides ja estao prontos e confiaveis, entao da pra reetiquetar
    com janela de 0,6 s: cada janelinha vai pro centroide mais parecido. So a
    RESOLUCAO muda — quem sao os locutores continua vindo da 1a passada.
    ⚠️ Centrar com a MESMA media da 1a passada, senao os dois espacos nao
    conversam e a comparacao com o centroide nao quer dizer nada."""
    n_vozes = int(rotulos.max())
    centroides = np.vstack([Ec[rotulos == k].mean(axis=0) for k in range(1, n_vozes + 1)])
    centroides /= (np.linalg.norm(centroides, axis=1, keepdims=True) + 1e-9)

    finas, votos = [], []
    t = 0.0
    dur = len(ana) / TAXA_ANALISE
    while t + JANELA_FINA <= dur + 1e-6:
        i0, i1 = int(t / PULO), int((t + JANELA_FINA) / PULO)
        trecho = fala[i0:min(i1, len(fala))]
        if len(trecho) and trecho.mean() >= COBERTURA_MIN:
            finas.append((t, t + JANELA_FINA))
        t += PULO_FINO
    if not finas:
        return None

    with torch.inference_mode():
        for ini, fim in finas:
            x = torch.from_numpy(ana[int(ini * TAXA_ANALISE):int(fim * TAXA_ANALISE)])
            e = enc.encode_batch(x.unsqueeze(0).to(device)).squeeze().detach().cpu().numpy()
            v = e - media[0]
            v /= (np.linalg.norm(v) + 1e-9)
            votos.append(int(np.argmax(centroides @ v)) + 1)
    return finas, np.array(votos)


def rotulo_por_quadro(np, fala, janelas, rotulos, n_vozes):
    """Cada quadro de 10 ms recebe o locutor mais votado entre as janelas que
    o cobrem; depois uma mediana movel tira a tremulacao."""
    votos = np.zeros((len(fala), n_vozes + 1), dtype="float32")
    for (ini, fim), r in zip(janelas, rotulos):
        votos[int(ini / PULO):int(fim / PULO), int(r)] += 1.0
    quem = votos.argmax(axis=1)
    quem[votos.max(axis=1) == 0] = 0          # ninguem votou

    # buraco de fala sem voto (janela nao cobriu): herda o vizinho anterior
    ultimo = 0
    for i in range(len(quem)):
        if fala[i] and quem[i] == 0:
            quem[i] = ultimo
        elif quem[i]:
            ultimo = quem[i]

    # mediana movel: um pico solitario nao muda o dono do trecho
    jan = max(3, int(SUAVIZA / PULO) | 1)
    meio = jan // 2
    est = quem.copy()
    for i in range(len(quem)):
        a, b = max(0, i - meio), min(len(quem), i + meio + 1)
        janela = quem[a:b]
        janela = janela[janela > 0]
        if len(janela):
            vals, cont = np.unique(janela, return_counts=True)
            est[i] = vals[cont.argmax()]
    est[~fala] = 0
    return est


def blocos(np, quem, k):
    """Trechos contiguos do locutor k, em segundos, jogando fora migalha."""
    saida = []
    i = 0
    while i < len(quem):
        if quem[i] == k:
            j = i
            while j < len(quem) and quem[j] == k:
                j += 1
            if (j - i) * PULO >= MIN_FALA_SAIDA:
                saida.append((i * PULO, j * PULO))
            i = j
        else:
            i += 1
    return saida


def main():
    p = argparse.ArgumentParser(description="Separa as vozes por locutor")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--vozes", type=int, default=0,
                   help="quantas pessoas falam (0 = descobrir sozinho)")
    a = p.parse_args()

    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio
    from speechbrain.inference.speaker import EncoderClassifier
    from speechbrain.utils.fetching import LocalStrategy

    pasta = Path(a.output)
    pasta.mkdir(parents=True, exist_ok=True)

    # ORIGINAL intacto: e dele que os stems sao recortados (qualidade cheia)
    orig, sr = sf.read(a.input, dtype="float32", always_2d=True)
    dur = len(orig) / sr

    mono = orig.mean(axis=1)
    if sr != TAXA_ANALISE:
        ana = torchaudio.functional.resample(
            torch.from_numpy(mono).unsqueeze(0), sr, TAXA_ANALISE)[0].numpy()
    else:
        ana = mono

    fala = mascara_de_fala(np, ana, TAXA_ANALISE)
    if not fala.any():
        raise RuntimeError("nao achei fala nenhuma no arquivo")
    print(f"  {dur:.1f}s · {fala.mean()*100:.0f}% com fala", flush=True)

    janelas = janelas_de_timbre(np, fala, dur)
    if len(janelas) < 2:
        raise RuntimeError("fala de menos pra distinguir vozes (precisa de ~3 s)")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    enc = EncoderClassifier.from_hparams(
        source=MODELO_VOZ,
        savedir=str(Path(__file__).parent / ".modelos-voz"),
        run_opts={"device": device},
        local_strategy=LocalStrategy.COPY)

    embs = []
    with torch.inference_mode():
        for ini, fim in janelas:
            t = torch.from_numpy(ana[int(ini * TAXA_ANALISE):int(fim * TAXA_ANALISE)])
            e = enc.encode_batch(t.unsqueeze(0).to(device))
            embs.append(e.squeeze().detach().cpu().numpy())
    E = np.vstack(embs)
    print(f"  {len(E)} janela(s) de timbre medida(s)", flush=True)

    rotulos, Ec, media = agrupa(np, E, a.vozes)
    n_vozes = int(rotulos.max())
    print(f"  {n_vozes} voz(es) reconhecida(s)", flush=True)

    # 2a passada: mesma gente, fronteira mais fina
    fino = None
    if n_vozes > 1:
        fino = refina_fino(np, torch, enc, ana, fala, Ec, rotulos, media, device)
    if fino:
        jan_f, rot_f = fino
        print(f"  {len(jan_f)} janela(s) finas pra ajustar as trocas de turno", flush=True)
        quem = rotulo_por_quadro(np, fala, jan_f, rot_f, n_vozes)
    else:
        quem = rotulo_por_quadro(np, fala, janelas, rotulos, n_vozes)

    fade = max(1, int(FADE * sr))
    saidas, tempos = [], []
    for k in range(1, n_vozes + 1):
        mascara = np.zeros(len(orig), dtype="float32")
        falado = 0.0
        for ini, fim in blocos(np, quem, k):
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
                      "segundos": tempos, "taxa": sr}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        sys.exit(1)
