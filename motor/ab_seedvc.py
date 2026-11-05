# -*- coding: utf-8 -*-
"""A/B cego RVC x Seed-VC — Etapa 5 do PLANO_SEEDVC.md.

Reaproveita as funcoes do PROPRIO servidor do DUBX: a corrente e identica pros
dois lados e so o motor muda. Mede o que o ouvido nao mede sozinho:
inteligibilidade em pt-BR (round-trip do Whisper, WER contra a transcricao do
ORIGINAL), duracao e LUFS.

Sidon em dose 0 DE PROPOSITO: em dose 100 ele RECONSTROI a voz e mascararia
justamente a diferenca entre os motores. Aqui queremos ouvir o motor cru.
"""
import json
import shutil
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, r"C:\Users\Luan\CLAUDE\apps\DUBX")
import servidor as s   # noqa: E402

# 28 s das 7 melhores tomadas dele, SO nivelada (a _limpa passou pelo Sidon e
# mascararia o motor). Fonte longa da WER com significado; o clipe de 4 s do
# teste_vc tem palavra de menos pra medir inteligibilidade.
FONTE = Path(r"C:\Users\Luan\CLAUDE\apps\DUBX\motor\referencia\REFERENCIA_dublador_seca.wav")
VOZ = "Plexus"
SAIDA = Path(__file__).resolve().parent / "ab"
SAIDA.mkdir(parents=True, exist_ok=True)


def normaliza(txt):
    txt = unicodedata.normalize("NFD", txt.lower())
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    return [p for p in "".join(c if c.isalnum() or c.isspace() else " "
                               for c in txt).split() if p]


def wer(ref, hip):
    """Word Error Rate classica (Levenshtein em palavras)."""
    r, h = normaliza(ref), normaliza(hip)
    if not r:
        return None, 0, 0
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            custo = 0 if r[i - 1] == h[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + custo)
    return d[len(r)][len(h)] / len(r), d[len(r)][len(h)], len(r)


def transcreve(rotulo, wav):
    srt = SAIDA / f"{rotulo}.srt"
    s.transcrever_srt(f"ab_{rotulo}", wav, srt, "pt")
    return " ".join(b["texto"] for b in s._le_srt_blocos(srt)).strip()


def duracao(wav):
    fp = s.achar_ffmpeg().parent / "ffprobe.exe"
    import subprocess
    r = subprocess.run([str(fp), "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(wav)], capture_output=True, text=True,
                       creationflags=s.SEM_JANELA)
    try:
        return float(r.stdout.strip())
    except Exception:
        return None


def roda_motor(motor):
    voz = next(v for v in s.listar_vozes() if v["nome"] == VOZ)
    t = f"ab{motor}"
    cru = s.TRAB / f"{t}_cru.wav"
    p = {"motor_vc": motor, "voz": VOZ, "sidon": 0, "lufs": -16, "passos": 30}
    s._troca_voz(t, voz, FONTE, cru, p)
    # mesmo acabamento dos dois lados: nivelar (dose 0) + cortar no tempo
    niv = s.TRAB / f"{t}_niv.wav"
    s.nivelar(cru, niv, "-16", dose="0")
    final = s.cortar_no_tempo(niv, FONTE)
    destino = SAIDA / f"{motor}.wav"
    shutil.copy2(final, destino)
    return destino


def roda_producao(motor):
    """A corrente REAL do app (preservar=True + Sidon 100): e o que ele ouviria
    no dia a dia. Serve pro julgamento de ouvido; a medicao de motor e a de
    cima, com dose 0."""
    voz = next(v for v in s.listar_vozes() if v["nome"] == VOZ)
    t = f"abp{motor}"
    p = {"motor_vc": motor, "voz": VOZ, "arquivo": str(FONTE), "preservar": True,
         "separador": "demucs", "sidon": 100, "lufs": -16, "passos": 30}
    final = s.modo_trocar(t, p)
    destino = SAIDA / f"producao_{motor}.wav"
    shutil.copy2(final, destino)
    return destino


def main():
    print(f"fonte: {FONTE.name}  ({duracao(FONTE):.2f}s, LUFS {s.medir_lufs(FONTE)})",
          flush=True)
    res = {}
    for motor in ("rvc", "seedvc"):
        print(f"\n--- rodando {motor} ---", flush=True)
        wav = roda_motor(motor)
        res[motor] = {"wav": str(wav), "dur": duracao(wav), "lufs": s.medir_lufs(wav)}
        print(f"  {motor}: {res[motor]['dur']:.2f}s, LUFS {res[motor]['lufs']}", flush=True)

    # ---- IGUALAR O LUFS antes de qualquer julgamento (licao de 14/08) -------
    alvo = -18.0
    ff = s.achar_ffmpeg()
    for motor, d in res.items():
        if d["lufs"] is None:
            continue
        delta = alvo - d["lufs"]
        igual = SAIDA / f"{motor}_igualado.wav"
        s.roda([ff, "-y", "-i", d["wav"], "-af", f"volume={delta:.2f}dB",
                "-c:a", "pcm_s24le", igual])
        d["igualado"] = str(igual)
        d["lufs_igualado"] = s.medir_lufs(igual)

    print("\n--- round-trip do Whisper (pt-BR) ---", flush=True)
    ref = transcreve("original", FONTE)
    print(f"original: {ref}", flush=True)
    for motor, d in res.items():
        txt = transcreve(motor, Path(d["wav"]))
        taxa, erros, total = wer(ref, txt)
        d["texto"] = txt
        d["wer"] = taxa
        d["erros"] = f"{erros}/{total}"
        print(f"{motor}: WER {taxa:.1%} ({erros}/{total})\n   {txt}", flush=True)

    if "--producao" in sys.argv:
        print("\n--- corrente de producao (stems + Sidon 100) ---", flush=True)
        for motor in ("rvc", "seedvc"):
            try:
                wav = roda_producao(motor)
                lufs = s.medir_lufs(wav)
                res[motor]["producao"] = str(wav)
                res[motor]["producao_lufs"] = lufs
                print(f"  {motor}: {wav.name}, {duracao(wav):.2f}s, LUFS {lufs}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  {motor}: FALHOU — {type(exc).__name__}: {exc}", flush=True)
                res[motor]["producao_erro"] = str(exc)

    (SAIDA / "resultado.json").write_text(
        json.dumps({"fonte": str(FONTE), "voz": VOZ,
                    "texto_original": ref, "resultados": res},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON em {SAIDA / 'resultado.json'}", flush=True)


if __name__ == "__main__":
    main()
