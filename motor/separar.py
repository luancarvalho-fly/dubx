# -*- coding: utf-8 -*-
"""
DUBX — SEPARAR: duas vozes falando UMA EM CIMA DA OUTRA viram dois arquivos.

Pedido do Luan (16/08/2026): "o Plexus e a Pedaleira falando um em cima do
outro — dividir essas vozes", inclusive pra trocar a voz de cada um depois.

Motor: SepFormer 16 kHz (SpeechBrain, treinado com ruido e reverberacao).
A saida e banda-estreita DE PROPOSITO — quem devolve o corpo de 48 kHz e o
Sidon, no servidor (a corrente da casa: separar 16k -> Sidon reconstroi).

⚠️ POR QUE ISTO PROCESSA EM PEDACOS (bug pago em 19/08/2026: TRAVOU O PC).
A versao antiga mandava o audio INTEIRO num tensor so. A memoria do SepFormer
cresce MUITO rapido com a duracao — medido nesta maquina (5070, 12 GB):
10 s ok · 18 s ok · 30 s ok · **46,5 s travou a maquina inteira**.
E travou em vez de dar erro porque no Windows, quando a VRAM acaba, o driver
NAO levanta "out of memory": ele DERRAMA pra RAM do sistema (shared GPU
memory do WDDM) e a maquina entra em thrash — mouse parado, nada responde.
Entao aqui o audio e cortado em janelas, cada uma separada sozinha, e a
memoria fica limitada pelo TAMANHO DA JANELA, nunca pelo tamanho do arquivo.

A costura tem uma pegadinha: o SepFormer NAO garante a ordem das vozes entre
uma janela e outra — a voz 1 de um pedaco pode sair como voz 2 do pedaco
seguinte. Se emendar na ingenuidade, as vozes trocam de lugar no meio do
arquivo. Por isso cada janela e ALINHADA com a anterior por correlacao na
regiao de sobreposicao antes de entrar na soma.

Uso:
    .venv-sep/Scripts/python.exe separar.py -i mistura.wav -o pasta_saida
Entrada: wav 16 kHz MONO (o servidor prepara). Saida: voz1.wav e voz2.wav.
"""
import argparse
import json
import sys
import warnings
from pathlib import Path

# Os warnings do torchaudio acionam inspect sobre os modulos LAZY do
# speechbrain (k2_fsa etc.) e o import lazy explode no Windows/py3.13.
# Silenciar e usar separate_batch (sem o loader do separate_file) contorna.
warnings.simplefilter("ignore")

MODELO = "speechbrain/sepformer-whamr16k"
TAXA = 16000

# 15 s com 2 s de sobreposicao. 30 s passou nesta maquina com a VRAM LIVRE —
# 15 deixa folga pro caso comum dele, que e o Premiere aberto comendo VRAM.
# Mexer aqui pra cima e reabrir a porta do travamento: nao mexa sem medir.
JANELA_S = 15.0
SOBREPOSICAO_S = 2.0


def separa_janela(modelo, torch, pedaco, device):
    """Uma janela pelo modelo. Devolve numpy [amostras, 2]."""
    mix = torch.from_numpy(pedaco).unsqueeze(0).to(device)
    with torch.inference_mode():
        fontes = modelo.separate_batch(mix)        # [1, tempo, 2]
    saida = fontes[0].detach().cpu().numpy()
    del mix, fontes
    if device == "cuda":
        torch.cuda.empty_cache()                   # nao acumula entre janelas
    return saida


def alinha(np, atual, cauda_anterior):
    """O SepFormer nao mantem a ordem das vozes entre janelas. Compara o
    comeco desta janela com o fim da anterior e troca as colunas se a
    correlacao cruzada for mais forte que a direta."""
    n = min(len(cauda_anterior), len(atual))
    if n < 8:
        return atual
    a, b = atual[:n], cauda_anterior[:n]

    def corr(x, y):
        x = x - x.mean()
        y = y - y.mean()
        d = (np.linalg.norm(x) * np.linalg.norm(y)) or 1e-9
        return abs(float(np.dot(x, y)) / d)

    direto = corr(a[:, 0], b[:, 0]) + corr(a[:, 1], b[:, 1])
    cruzado = corr(a[:, 0], b[:, 1]) + corr(a[:, 1], b[:, 0])
    return atual[:, ::-1] if cruzado > direto else atual


def separa_longo(modelo, torch, np, dados, device, janela_s):
    """Janela deslizante com soma ponderada (overlap-add). A memoria depende
    so da JANELA — arquivo de 5 min gasta o mesmo que um de 15 s."""
    total = len(dados)
    janela = int(janela_s * TAXA)
    over = int(SOBREPOSICAO_S * TAXA)
    if total <= janela:
        return separa_janela(modelo, torch, dados, device), 1

    passo = janela - over
    soma = np.zeros((total, 2), dtype="float32")
    peso = np.zeros(total, dtype="float32")
    cauda = None
    ini = 0
    n_janelas = 0
    while ini < total:
        fim = min(ini + janela, total)
        if fim - ini < TAXA // 2 and n_janelas:      # sobra minuscula: junta
            break
        parcial = separa_janela(modelo, torch, dados[ini:fim], device)
        n_janelas += 1
        if cauda is not None:
            parcial = alinha(np, parcial, cauda)

        # rampas: entra subindo, sai descendo — emenda sem clique
        w = np.ones(fim - ini, dtype="float32")
        if ini > 0:
            k = min(over, len(w))
            w[:k] = np.linspace(0.0, 1.0, k, dtype="float32")
        if fim < total:
            k = min(over, len(w))
            w[-k:] = np.linspace(1.0, 0.0, k, dtype="float32")

        soma[ini:fim] += parcial * w[:, None]
        peso[ini:fim] += w
        cauda = parcial[-over:] if fim < total else None
        if fim >= total:
            break
        ini += passo

    peso[peso < 1e-6] = 1.0
    return soma / peso[:, None], n_janelas


def main():
    p = argparse.ArgumentParser(description="Separa 2 vozes sobrepostas")
    p.add_argument("-i", "--input", required=True, help="wav 16k mono da mistura")
    p.add_argument("-o", "--output", required=True, help="pasta das vozes")
    p.add_argument("--janela", type=float, default=JANELA_S,
                   help="segundos por pedaco (subir arrisca travar a maquina)")
    a = p.parse_args()

    import numpy as np
    import soundfile as sf
    import torch
    from speechbrain.inference.separation import SepformerSeparation
    from speechbrain.utils.fetching import LocalStrategy

    janela_s = max(4.0, min(30.0, float(a.janela)))   # teto duro: 30 s

    device = "cuda" if torch.cuda.is_available() else "cpu"
    pasta = Path(a.output)
    pasta.mkdir(parents=True, exist_ok=True)

    # LocalStrategy.COPY: o padrao (symlink) exige privilegio que o Windows
    # nao da — WinError 1314, pago em 16/08.
    modelo = SepformerSeparation.from_hparams(
        source=MODELO,
        savedir=str(Path(__file__).parent / ".modelos-sep"),
        run_opts={"device": device},
        local_strategy=LocalStrategy.COPY)

    dados, sr = sf.read(a.input, dtype="float32", always_2d=False)
    if getattr(dados, "ndim", 1) > 1:
        dados = dados.mean(axis=1)
    if sr != TAXA:
        raise RuntimeError(f"esperava wav {TAXA} Hz, veio {sr}")

    dur = len(dados) / TAXA
    print(f"  {dur:.1f}s de mistura, janelas de {janela_s:.0f}s", flush=True)
    fontes, n = separa_longo(modelo, torch, np, dados, device, janela_s)
    print(f"  separado em {n} janela(s)", flush=True)

    saidas = []
    for i in range(fontes.shape[1]):
        canal = fontes[:, i]
        pico = float(abs(canal).max()) or 1.0
        if pico > 0.99:
            canal = canal * (0.99 / pico)
        destino = pasta / f"voz{i + 1}.wav"
        sf.write(str(destino), canal, TAXA, subtype="PCM_16")
        saidas.append(str(destino))

    print(json.dumps({"ok": True, "vozes": saidas, "janelas": n}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        sys.exit(1)
