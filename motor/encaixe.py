# -*- coding: utf-8 -*-
"""
DUBX — ENCAIXE: texto vira fala e depois se encaixa no tempo do original.

O problema que isto resolve: o TTS acerta a fonetica mas inventa o proprio ritmo,
entao a fala nova nao bate com a imagem. A troca de voz respeita o tempo mas
herda os defeitos da gravacao.

A saida: gerar cada frase por TTS (fonetica perfeita, voz clonada) e depois
ESTICAR ou COMPRIMIR cada uma para o tempo exato que ela ocupa no original,
colocando cada uma no segundo certo. O silencio entre as frases e preservado.

O esticamento usa rubberband, que muda a duracao SEM mexer na altura da voz —
sem isso viraria voz de esquilo. O --formant preserva o timbre no processo.

Uso:
    .venv-vc\\Scripts\\python.exe encaixe.py -s ref.srt -a voz.wav -o saida.wav
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SEM_JANELA = 0x08000000 if os.name == "nt" else 0
BIN = Path(r"C:\Users\Luan\AppData\Roaming\Adobe\CEP\extensions"
           r"\com.luan.beatmatch\bin\win")
RUBBER = BIN / "rubberband.exe"
FFMPEG = BIN / "ffmpeg.exe"

# TETO DE DEFORMACAO (16/08/2026 — queixa: "a dublagem sai acelerada").
# Encaixar na marra no tempo EXATO da legenda era o erro: o Whisper entrega
# blocos colados (0->2, 2->3.5), a traducao pro ingles fica mais longa, e a
# frase era comprimida a 0,6x — isso e o "acelerado". Agora a frase so pode
# ser deformada dentro desta faixa; o que nao couber invade a pausa seguinte
# (que e silencio de verdade no original) em vez de virar fala de desenho.
APERTO_MAX = 0.90    # no maximo 10% mais rapida que o TTS gerou
ESTICO_MAX = 1.15    # no maximo 15% mais lenta (senao vira arrastado)
ZONA_MORTA = 0.04    # diferenca menor que 4%: nem passa pelo rubberband


def log(m):
    print(m, flush=True)


def seg(t):
    """'00:00:01,620' -> 1.62"""
    h, m, resto = t.split(":")
    s, ms = resto.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def le_srt(caminho):
    blocos = re.split(r"\n\s*\n", Path(caminho).read_text(encoding="utf-8-sig").strip())
    falas = []
    for b in blocos:
        linhas = [l for l in b.strip().split("\n") if l.strip()]
        if len(linhas) < 2:
            continue
        tempo = next((l for l in linhas if "-->" in l), None)
        if not tempo:
            continue
        ini, fim = [x.strip() for x in tempo.split("-->")]
        texto = " ".join(l.strip() for l in linhas[linhas.index(tempo) + 1:]).strip()
        if texto:
            falas.append({"ini": seg(ini), "fim": seg(fim), "texto": texto})
    return falas


# Alucinacoes classicas do Whisper em trecho MUDO: ele "ouve" uma despedida
# curtinha. Elas nao sao fala de verdade e ainda quebram o TTS (texto de uma
# palavra), entao saem antes de gerar.
FANTASMAS = {"you", "thank you", "thanks for watching", "bye", "bye bye",
             "obrigado", "obrigada", "valeu", "tchau", "legendas pela comunidade",
             "amara org", "subtitles by the amara org community", "the"}


def eh_fantasma(texto, dur):
    limpo = "".join(c for c in texto.lower() if c.isalnum() or c.isspace()).strip()
    return dur < 0.6 and limpo in FANTASMAS


def roda(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       errors="replace", creationflags=SEM_JANELA)
    if r.returncode != 0:
        det = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError(det[-1] if det else f"codigo {r.returncode}")


def estica(entrada, saida, alvo_seg):
    """Leva o arquivo para a duracao exata, sem alterar a altura da voz."""
    roda([RUBBER, "--duration", f"{alvo_seg:.4f}", "--formant", "--crisp", "5",
          str(entrada), str(saida)])


def apara(dados, sr, limiar_db=-42.0, margem_ms=25):
    """Corta o silencio do comeco e do fim.

    O TTS entrega uma folga de silencio em cada geracao. Sem cortar, esse silencio
    e esticado junto com a fala — e ele e quem faz a razao parecer pior do que e.
    Deixa uma margem pequena pra nao decepar o ataque da primeira consoante.
    """
    import numpy as np
    if len(dados) == 0:
        return dados
    janela = max(1, int(sr * 0.01))
    quadros = len(dados) // janela
    if quadros < 3:
        return dados
    energia = np.abs(dados[:quadros * janela]).reshape(quadros, janela).max(axis=1)
    pico = float(energia.max()) or 1.0
    db = 20 * np.log10(np.maximum(energia / pico, 1e-6))
    vivos = np.where(db > limiar_db)[0]
    if len(vivos) == 0:
        return dados
    margem = int(sr * margem_ms / 1000)
    ini = max(0, vivos[0] * janela - margem)
    fim = min(len(dados), (vivos[-1] + 1) * janela + margem)
    return dados[ini:fim]


def ataque(dados, sr, ini_s, fim_s, piso_s=0.0, limiar_db=-38.0, folga_s=0.30):
    """Onde a voz REALMENTE comeca, dentro da janela daquela legenda.

    O Whisper marca o inicio do SEGMENTO, e o segmento comeca no silencio
    ANTES da voz. Medido no arquivo dele em 23/08: o SRT dizia 0,000 s e a
    fala so atacava em 0,24 s — a dublagem entrava 200 ms adiantada e o
    lipsync furava.

    Procura na janela [ini - folga, fim]: a folga cobre o caso oposto (o
    Whisper atrasar e a voz ja ter comecado antes da marca). Devolve None
    quando nao acha nada acima do limiar — ai o chamador fica com o tempo
    do Whisper, que e o melhor palpite que sobra.

    ⚠️ `piso_s` = fim da frase ANTERIOR, e nao e opcional na pratica: sem ele a
    folga entra no rabo da frase de tras e o detector trava no som que ja
    estava tocando. Peguei isso no proprio teste (a frase 2 vinha 300 ms pra
    tras, exatamente a borda da janela — o cheiro classico do defeito).
    """
    import numpy as np
    ini_j = max(0.0, piso_s, ini_s - folga_s)
    a, b = int(ini_j * sr), min(len(dados), int(fim_s * sr))
    if b - a < int(sr * 0.05):
        return None
    janela = dados[a:b]
    passo = max(1, int(sr * 0.01))          # 10 ms
    q = len(janela) // passo
    if q < 2:
        return None
    e = np.abs(janela[:q * passo].reshape(q, passo)).max(axis=1)
    pico = float(e.max())
    if pico <= 0:
        return None
    db = 20 * np.log10(np.maximum(e / pico, 1e-6))
    vivos = np.where(db > limiar_db)[0]
    if not len(vivos):
        return None
    # exige 60 ms seguidos de som pra nao morder um estalo
    seguidos, alvo = 0, None
    for i in vivos:
        if alvo is None or i == anterior + 1:
            seguidos = seguidos + 1 if alvo is not None else 1
            alvo = i if alvo is None else alvo
        else:
            seguidos, alvo = 1, i
        anterior = i
        if seguidos * 0.01 >= 0.06:
            break
    else:
        alvo = int(vivos[0])
    return ini_j + alvo * 0.01


def main():
    p = argparse.ArgumentParser(description="Encaixa fala gerada no tempo do original.")
    p.add_argument("-s", "--srt", required=True, help="SRT do audio de referencia")
    p.add_argument("-a", "--alvo", required=True, help="WAV da voz a usar")
    p.add_argument("--fonte", default="",
                   help="WAV do audio ORIGINAL: cada frase clona do proprio "
                        "trecho dela (estilo Eleven Dubbing) em vez da "
                        "referencia fixa — transfere parte da emocao")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--idioma", default="pt")
    p.add_argument("--manter", default="",
                   help="indices (1-based) das frases que saem do audio "
                        "ORIGINAL em vez do TTS — interjeicao/onomatopeia, "
                        "que o tradutor moi e o TTS nao sabe atuar")
    p.add_argument("--exagero", type=float, default=0.3)
    p.add_argument("--cfg", type=float, default=0.5)
    p.add_argument("--device", default="cuda")
    a = p.parse_args()

    if not RUBBER.exists():
        print(json.dumps({"ok": False, "error": f"rubberband nao encontrado em {RUBBER}"}),
              file=sys.stderr)
        sys.exit(1)

    falas = le_srt(a.srt)
    fantasmas = [f for f in falas if eh_fantasma(f["texto"], f["fim"] - f["ini"])]
    if fantasmas:
        log(f"{len(fantasmas)} bloco(s) descartado(s) por serem alucinação do "
            f"Whisper em silêncio: " + ", ".join(f'"{f["texto"]}"' for f in fantasmas))
        falas = [f for f in falas if f not in fantasmas]
    if not falas:
        print(json.dumps({"ok": False, "error": "SRT sem falas"}), file=sys.stderr)
        sys.exit(1)

    import numpy as np
    import soundfile as sf
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    device = a.device
    if device.startswith("cuda") and not torch.cuda.is_available():
        device = "cpu"

    log(f"{len(falas)} frases · carregando o modelo…")
    modelo = ChatterboxMultilingualTTS.from_pretrained(device=device)
    sr = int(modelo.sr)

    # Referencia POR SEGMENTO (pesquisa de 16/08): clonar cada frase do
    # proprio trecho original transfere parte da entrega emocional — e o que
    # o Eleven Dubbing faz. Trecho curto demais nao segura clone: expande a
    # janela pra >=3 s em volta da frase; sem fonte, cai na referencia fixa.
    fonte_dados, fonte_sr = None, 0
    if a.fonte and Path(a.fonte).exists():
        fonte_dados, fonte_sr = sf.read(a.fonte, dtype="float32",
                                        always_2d=False)
        if getattr(fonte_dados, "ndim", 1) > 1:
            fonte_dados = fonte_dados.mean(axis=1)

    manter = set()
    for x in str(a.manter).replace(";", ",").split(","):
        x = x.strip()
        if x.isdigit():
            manter.add(int(x))
    if manter:
        log(f"{len(manter)} frase(s) saem do audio ORIGINAL (interjeicao)")

    total = max(f["fim"] for f in falas)
    linha = np.zeros(int(total * sr) + 4 * sr, dtype=np.float32)
    relatorio = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for i, f in enumerate(falas, 1):
            alvo_dur = f["fim"] - f["ini"]

            # ⭐ INTERJEICAO: copia o trecho ORIGINAL e nao gera nada. A voz do
            # dublado e clonada do proprio arquivo, entao a emenda e invisivel —
            # e e o que dublagem de verdade faz com vocalizacao nao-verbal.
            if i in manter and fonte_dados is not None:
                ini_s = int(f["ini"] * fonte_sr)
                fim_s = min(len(fonte_dados), int(f["fim"] * fonte_sr))
                pedaco_o = fonte_dados[ini_s:fim_s]
                if len(pedaco_o):
                    if fonte_sr != sr:      # taxas diferentes: reamostra simples
                        idx = np.linspace(0, len(pedaco_o) - 1,
                                          int(len(pedaco_o) * sr / fonte_sr))
                        pedaco_o = np.interp(idx, np.arange(len(pedaco_o)),
                                             pedaco_o).astype(np.float32)
                    ini = int(f["ini"] * sr)
                    fim = min(ini + len(pedaco_o), len(linha))
                    linha[ini:fim] += pedaco_o[:fim - ini]
                    log(f"  [{i}/{len(falas)}] ORIGINAL (interjeicao): "
                        f"\"{f['texto'][:30]}\"")
                    relatorio.append({"frase": i, "original": True,
                                      "texto": f["texto"][:60]})
                    continue

            ref = a.alvo
            if fonte_dados is not None:
                MIN_REF = 3.0
                ini_r, fim_r = f["ini"], f["fim"]
                falta = MIN_REF - (fim_r - ini_r)
                if falta > 0:
                    ini_r = max(0.0, ini_r - falta / 2)
                    fim_r = min(len(fonte_dados) / fonte_sr, ini_r + MIN_REF)
                pedaco_ref = fonte_dados[int(ini_r * fonte_sr):int(fim_r * fonte_sr)]
                if len(pedaco_ref) >= fonte_sr:      # >=1 s ou nem tenta
                    ref_seg = tmp / f"ref{i:02d}.wav"
                    sf.write(str(ref_seg), pedaco_ref, fonte_sr, subtype="PCM_16")
                    ref = str(ref_seg)

            # UMA frase ruim NAO pode derrubar a dublagem inteira (16/08: 28
            # frases perdidas por causa de um "you" de 0,24 s — alucinacao do
            # Whisper em silencio, que faz o Chatterbox estourar em
            # "max(): Expected reduction dim 1 to have non-zero size").
            # Tenta com pontuacao (texto curtissimo confunde o modelo) e, se
            # ainda assim falhar, deixa a frase em silencio e SEGUE.
            wav = None
            for tentativa, texto in enumerate((f["texto"], f["texto"].strip() + ".")):
                try:
                    with torch.inference_mode():
                        wav = modelo.generate(texto, language_id=a.idioma,
                                              audio_prompt_path=ref,
                                              exaggeration=a.exagero,
                                              cfg_weight=a.cfg)
                    break
                except Exception as exc:
                    log(f"  [{i}] tentativa {tentativa + 1} falhou ({exc})")
            if wav is None:
                log(f"  [{i}/{len(falas)}] PULADA — o modelo nao gerou audio "
                    f"pra \"{f['texto'][:40]}\" (fica silencio aqui)")
                relatorio.append({"frase": i, "pulada": True,
                                  "texto": f["texto"][:60]})
                continue
            dados = wav.squeeze().detach().cpu().numpy()
            if dados.ndim > 1:
                dados = dados.mean(axis=0)
            bruto_seg = len(dados) / sr
            dados = apara(dados, sr)

            bruto = tmp / f"f{i:02d}.wav"
            sf.write(str(bruto), dados, sr, subtype="FLOAT")
            dur_tts = len(dados) / sr

            # ⭐ LIPSYNC: entrar no ATAQUE da voz, nao na marca do Whisper.
            # O segmento do Whisper comeca no silencio anterior a fala — no
            # arquivo dele (23/08) isso punha a dublagem 200 ms adiantada.
            ini_real = f["ini"]
            if fonte_dados is not None:
                piso = falas[i - 2]["fim"] if i >= 2 else 0.0
                achado = ataque(fonte_dados, fonte_sr, f["ini"], f["fim"], piso)
                if achado is not None:
                    ini_real = achado

            # Quanto espaco a frase REALMENTE tem: ate o comeco da proxima.
            # A pausa entre as falas e silencio no original — usar ela e o que
            # evita comprimir a voz. A ultima pode passar 1 s do fim.
            proximo = falas[i]["ini"] if i < len(falas) else f["fim"] + 1.0
            espaco = max(alvo_dur, proximo - ini_real)

            destino = dur_tts
            if dur_tts > espaco:                      # nao coube: aperta ate o teto
                destino = max(espaco, dur_tts * APERTO_MAX)
            elif dur_tts < alvo_dur:                  # sobrou: estica ate o teto
                destino = min(alvo_dur, dur_tts * ESTICO_MAX)
            razao = destino / dur_tts if dur_tts > 0 else 1.0

            pedaco = dados
            if abs(razao - 1.0) > ZONA_MORTA:
                ajustado = tmp / f"f{i:02d}_ok.wav"
                try:
                    estica(bruto, ajustado, destino)
                    pedaco, _ = sf.read(str(ajustado), dtype="float32", always_2d=False)
                except Exception as exc:
                    log(f"  [{i}] esticamento falhou ({exc}) — usando sem ajuste")
                    pedaco = dados

            ini = int(ini_real * sr)
            fim = min(ini + len(pedaco), len(linha))
            linha[ini:fim] += pedaco[:fim - ini]

            sobra = (ini_real + len(pedaco) / sr) - proximo
            aviso = ""
            if sobra > 0.15:
                aviso = (f"  ⚠ invadiu {sobra:.2f}s da pausa seguinte "
                         f"(traducao mais longa que o original)")
            desvio = (ini_real - f["ini"]) * 1000
            ajuste = f" · ataque {desvio:+.0f}ms" if abs(desvio) >= 20 else ""
            log(f"  [{i}/{len(falas)}] gerou {bruto_seg:.2f}s, aparado {dur_tts:.2f}s "
                f"-> {destino:.2f}s (x{razao:.2f}) · legenda {alvo_dur:.2f}s, "
                f"espaço {espaco:.2f}s{ajuste}{aviso}")
            relatorio.append({"frase": i, "ataque_ms": round(desvio),
                              "razao": round(razao, 2),
                              "alvo": round(alvo_dur, 2), "espaco": round(espaco, 2),
                              "destino": round(destino, 2),
                              "gerado": round(dur_tts, 2),
                              "bruto": round(bruto_seg, 2)})

    pico = float(np.max(np.abs(linha))) or 1.0
    if pico > 0.99:
        linha = linha * (0.99 / pico)

    dst = Path(a.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), linha, sr, subtype="PCM_24")

    print(json.dumps({"ok": True, "sr": sr, "duracao": round(len(linha) / sr, 3),
                      "frases": relatorio, "arquivo": str(dst)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        sys.exit(2)
