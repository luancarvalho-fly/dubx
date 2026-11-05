# -*- coding: utf-8 -*-
"""
DUBX - o servidor.

Nao processa audio: ele CHAMA os motores que vivem dentro da propria pasta.
    melhorar   -> motor\\voz.py         (nivela por LUFS + reconstroi com Sidon)
    trocar voz -> treino\\core.py infer (RVC treinado)
    falar      -> motor\\tts.py         (Chatterbox Multilingual)
    treinar    -> treino\\core.py       (preprocess + extract + train)

Cada motor vive na propria venv, de proposito: as versoes de torch brigam entre si
(2.9.1 no motor, 2.11.0 no treino). So biblioteca padrao aqui - nada a instalar.
"""
import base64
import datetime
import hashlib
import json
import math
import mimetypes
import re
import urllib.error
import urllib.request

try:
    import winsound     # biblioteca padrao no Windows; o plim de conclusao
except ImportError:
    winsound = None
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import pathlib
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

BASE = Path(__file__).resolve().parent
UI = BASE / "ui"
VOZES = BASE / "vozes"
TRAB = BASE / "trabalho"
FLUXOS = BASE / "fluxos"   # os fluxos prontos da esteira de nodes
PORTA = 7788

# Tudo mora dentro do DUBX. Os dois motores tem venv propria de proposito:
# as versoes de torch que eles exigem brigam entre si (2.9.1 x 2.11.0).
MOTOR = BASE / "motor"     # nivelamento, Sidon e Chatterbox
TREINO = BASE / "treino"   # RVC: treinar e trocar voz

# --- portabilidade (23/09/2026, pacote pra Fly/Mac) --------------------------
# No Windows nada muda: os caminhos abaixo continuam os mesmos de sempre. Fora
# dele (Mac/Linux) a venv guarda o python em bin/, o ffprobe nao tem .exe, e as
# ferramentas externas (curl, whisper-server) entram por variavel de ambiente
# (DUBX_WHISPER_BIN, DUBX_WHISPER_MODELO) ou pelo PATH. Nunca rodou num Mac de
# verdade — e o maximo que da pra preparar sem um.
WIN = os.name == "nt"


def _venv_py(venv):
    """python de uma venv: Scripts\\python.exe no Windows, bin/python fora."""
    return venv / ("Scripts" if WIN else "bin") / ("python.exe" if WIN else "python")


def _ffprobe():
    return achar_ffmpeg().parent / ("ffprobe.exe" if WIN else "ffprobe")


PY_VOZ = _venv_py(MOTOR / ".venv")      # Sidon + nivelamento
PY_TTS = _venv_py(MOTOR / ".venv-vc")   # Chatterbox
PY_RVC = _venv_py(TREINO / ".venv")     # RVC
PY_SEP = _venv_py(MOTOR / ".venv-sep")  # SepFormer (separar)
PY_STEM = _venv_py(MOTOR / ".venv-stem")  # Demucs (stems)
PY_ROFO = _venv_py(MOTOR / ".venv-rofo")  # Roformer (stems)
# Cada separador tem venv PROPRIA: o audio-separator exige librosa 0.10 (a 1.0
# renomeou get_duration(filename=) e ele quebra na hora de gravar), e o demucs
# nao tem nada a ver com essa briga.
PY_SEPARADOR = {"demucs": PY_STEM, "separator": PY_ROFO}

# Motores de SEPARAR POR LOCUTOR. Ele escolhe na aba (pedido de 19/08:
# "deixa esses modelos la pra eu alternar entre um e outro").
# ⚠️ LICENCA: o sortformer e CC BY-NC — uso pessoal dele por decisao propria;
# QUANDO FOR VENDER O PLUGIN, esta opcao SAI. Ver [[licenca-modelos-nc]].
# Separador de CINEMA (Bandit v2), montado em 19/08 pro Beat Match e
# reaproveitado aqui: parte fala x musica x efeitos. Os modelos de "vocal"
# comuns foram treinados em MUSICA (vocal = canto); o Bandit foi treinado em
# DIALOGO DE FILME, que e exatamente o material dele.
# 23/09/2026: no pacote o separador-cinema e IRMAO da pasta do DUBX; no PC do Luan
# continua em ~/CLAUDE/apps (que e o mesmo irmao). DUBX_CINEMA vence os dois.
CINEMA = Path(os.environ.get("DUBX_CINEMA") or (
    BASE.parent / "separador-cinema" if (BASE.parent / "separador-cinema").is_dir()
    else Path.home() / "CLAUDE" / "apps" / "separador-cinema"))
PY_CINEMA = _venv_py(CINEMA / "venv")

PY_PYA = _venv_py(MOTOR / ".venv-pya")      # pyannote (MIT)
PY_SORTF = _venv_py(MOTOR / ".venv-sortf")  # Sortformer (NC!)
# 07/10: separadores de SOBREPOSICAO (duas vozes falando juntas), instalados em
# CLAUDE/apps/separadores (venv propria, py3.12). Entregam a saida CRUA em 16 kHz:
# nada de Sidon nem de limpeza antes — ele quer ouvir o que o motor devolve.
SEPARADORES = BASE.parent / "separadores"
PY_SEPNOVO = _venv_py(SEPARADORES / ".venv")
MOTORES_CRUS = ("mossformer2", "tiger", "sam", "sam_risada")

MOTORES_DIAR = {
    "timbre": (lambda: PY_SEP, "diarizar.py"),
    "pyannote": (lambda: PY_PYA, "diarizar_pya.py"),
    "sortformer": (lambda: PY_SORTF, "diarizar_sortf.py"),
    "mossformer2": (lambda: PY_SEPNOVO, SEPARADORES / "rodar_mossformer2.py"),
    "tiger": (lambda: PY_SEPNOVO, SEPARADORES / "rodar_tiger.py"),
    # SAM Audio acha as ancoras sozinho (passada do MossFormer2) e sai em 48 kHz
    "sam": (lambda: PY_SEPNOVO, SEPARADORES / "rodar_sam_auto.py"),
    # 08/10 (dublagem do Gorila, problema da Ana): risadas numa faixa propria
    "sam_risada": (lambda: PY_SEPNOVO, SEPARADORES / "rodar_sam_risada.py"),
}

# Seed-VC: SEGUNDO motor de troca de voz, para o A/B de 17/08 (PLANO_SEEDVC.md).
# Nao substitui o RVC — quem escolhe e o parametro motor_vc.
PY_SEEDVC = _venv_py(MOTOR / ".venv-seedvc")
SEEDVC_CONFIG = (MOTOR / "seedvc" / "configs" / "presets"
                 / "config_dit_mel_seed_uvit_whisper_small_wavenet.yml")

# Onde o huggingface_hub procura o token — o mesmo arquivo que o
# `huggingface-cli login` escreve. Gravando aqui, todo motor acha sozinho.
ARQ_HF_TOKEN = Path.home() / ".cache" / "huggingface" / "token"

SEM_JANELA = 0x08000000 if os.name == "nt" else 0
TAREFAS = {}   # id -> {estado, mensagem, saida}
# ------------------------------------------------------------ CANCELAR
# Ate 20/09/2026 o DUBX nao tinha como parar um trabalho. O /api/parar so
# fazia winsound.SND_PURGE (cortava o som da previa) e nao encostava na
# tarefa. Naquele dia ele mandou uma legenda sem querer, o separador
# (bandit_v2) pegou a GPU inteira — 99% de uso, 11,2 dos 12,2 GB de VRAM —
# e o PC dele travou. Nao existia saida pela interface: so matar o processo
# na mao. Dai estes tres registros.
TAREFA_DA_THREAD = {}   # ident da thread -> id da tarefa (cada tarefa e uma)
PROCESSOS = {}          # id da tarefa -> processos filhos vivos agora
CANCELADAS = set()      # ids que ele mandou parar


class Cancelado(Exception):
    """O filho morreu porque ELE mandou parar — nao e erro, nao alarma."""
# avisos de uma tarefa que NAO sao fatais (ex.: o nivelamento falhou e o
# treino seguiu com o corte cru). Vao pro REGISTRO no fim.
AVISOS = {}
AUDIO_FONTE = {}   # id -> wav tocavel do ORIGINAL (o "antes" da comparacao)
# id -> TODAS as faixas que a separacao gerou, na ordem. O modo_separar
# devolve so a primeira (o resto vai pro historico); o Fluxo precisa da
# lista inteira pra ligar um node na "voz2" ou na "musica".
FAIXAS_DA_TAREFA = {}
# id -> {pico_entrada, pico_saida_antes, ganho_aplicado} do _casar_pico (P32). O trabalhar() leva pro historico e pra mensagem.
PICO_DA_TAREFA = {}

# Piso de material pra treinar uma voz. Abaixo disso o RVC ate roda (8 s viram
# 3 fatias, medido em 15/08), mas o que nasce nao e uma voz utilizavel — melhor
# recusar com instrucao do que entregar lixo. O painel usa o MESMO valor no
# pre-check (MINIMO_TREINO_S em painel\main.js): mudou aqui, mude la.
MINIMO_TREINO_S = 30

# TRES NIVEIS DE TREINO (24/08). O que muda entre eles e QUANTAS das tres
# pecas da voz sao feitas — nao a qualidade do material.
NIVEIS_TREINO = ("rapida", "boa", "maxima")
NIVEL_TREINO_PADRAO = "boa"
# O nivel "rapida" NAO TEM PISO (24/08, a pedido dele: "e com o que tem, e nao
# com o minimo"): ele nao treina nada, so guarda a amostra pro Seed-VC
# zero-shot, que le no maximo 25 s dela. Basta existir audio.
# Piso ABSOLUTO dos niveis que treinam. Nao e capricho: o dataset do ajuste
# fino descarta pedaco com menos de 1 s (ft_dataset.py:10-12 e :59) — material
# menor que isso faria ele pular TUDO e entrar em recursao. Abaixo do minimo
# normal (30 s) o material passa a ser REPETIDO ate dar, em vez de recusado.
MINIMO_ABSOLUTO_S = 1.5
# Teto de repeticoes: 2 s viram 30 s com 15 voltas. Mais que isso e so encher
# disco com a mesma coisa.
MAX_VOLTAS = 40
# 1000 passos = o que rodou na mao em 17/08 (runs\Plexus: 02:04 -> 02:09).
# save-every igual ao maximo de proposito: o break acontece ANTES do save
# (train.py:357-360), entao nao sobra checkpoint de 1,2 GB no disco — e um
# trabalho de 5 min nao precisa de rede de seguranca.
SEEDVC_PASSOS = 1000

# ------------------------------------------------------------------ historico
# Pedido do Luan (16/08): acesso rapido ao que ja foi gerado, sem cacar no
# Explorer. Toda tarefa concluida entra em historico.json (sobrevive a
# restart — TAREFAS e memoria e ja se perdeu uma vez). Teto de 200 entradas.
HISTORICO = BASE / "historico.json"
_TRAVA_HIST = threading.Lock()


def _agora():
    return datetime.datetime.now().strftime("%d/%m %H:%M")


def _rotulo_de(p):
    """Nome humano da geracao pro historico. Upload chega como <8hex>_nome —
    o hash sai da frente."""
    modo = p.get("modo", "")
    arq = Path(str(p.get("arquivo_original") or p.get("arquivo") or "")).name
    if len(arq) > 9 and arq[8] == "_" and all(c in "0123456789abcdef" for c in arq[:8]):
        arq = arq[9:]
    if modo.startswith("melhorar"):
        return f"{arq} — melhorado"
    if modo.startswith("trocar"):
        if str(p.get("motor_vc", "")) == "elevenlabs":
            return f"{arq} — voz {p.get('el_nome') or p.get('voz') or 'ElevenLabs'}"
        return f"{arq} — voz {p.get('voz', '?')}"
    if modo.startswith("ambientar"):
        return f"{arq} — ambiente {p.get('ambiente', '?')}"
    if modo.startswith("dublar"):
        return f"{arq} — dublado {p.get('origem', 'pt')}→{p.get('alvo', 'en')}"
    if modo.startswith("separar"):
        m = str(p.get("motor_diar", ""))
        if m:                      # 07/10: comparando motores, o nome dele tem que aparecer
            return f"{arq} — {m} · voz 1 (separada)"
        return f"{arq} — voz 1 (separada)"
    if modo == "falar":
        texto = str(p.get("texto", ""))
        curto = texto[:42] + ("…" if len(texto) > 42 else "")
        return f"falado ({p.get('voz', '?')}): {curto}"
    if modo.startswith("treinar"):
        return f"voz \"{p.get('nome', '?')}\" treinada"
    if modo == "fluxo":
        return f"{arq} — fluxo {p.get('fluxo', '')}".strip().rstrip("—").strip()
    return arq or modo


def _modelo_de(p):
    """Nome do MODELO/motor que gerou — vai no cartao das Geracoes, do lado da
    feature (pedido dele 07/10: "do lado, da cor da feature, o nome do modelo")."""
    modo = str(p.get("modo", "")).split("_")[0]
    if modo == "separar":
        m = str(p.get("motor_diar", "roteiro"))
        return {"roteiro": "Roteiro", "nenhum": "Roformer + Bandit", "timbre": "Timbre (ECAPA)",
                "pyannote": "pyannote", "sortformer": "Sortformer", "mossformer2": "MossFormer2",
                "tiger": "TIGER", "sam": "SAM Audio",
                "sam_risada": "SAM Audio + risadas"}.get(m, m)
    if modo == "trocar":
        mv = str(p.get("motor_vc", MOTOR_VC_PADRAO))
        if mv == "elevenlabs":
            return f"ElevenLabs · {p.get('el_nome') or p.get('voz') or p.get('el_voice_id', '?')}"
        return f"{'Seed-VC' if mv == 'seedvc' else 'RVC'} · {p.get('voz', '?')}"
    if modo == "melhorar":
        return "Sidon"
    if modo == "dublar":
        return f"Chatterbox · {p.get('origem', 'pt')}→{p.get('alvo', 'en')}"
    if modo == "falar":
        return str(p.get("voz", ""))
    if modo == "legenda":
        return "Whisper"
    if modo == "ambientar":
        return str(p.get("ambiente", ""))
    if modo == "fluxo":
        return str(p.get("fluxo", ""))
    return ""


def _nome_amigavel(rotulo, caminho):
    """Nome de ARQUIVO pro download (pedido de 16/08: "não quero arquivo com
    nome esquisito"). Vira "plexus vs spider v1 - dublado pt-en.mp4" a partir
    do rotulo do historico. Os arquivos de trabalho continuam com hash — o
    hash e o que garante que uma geracao nao pisa na outra."""
    ext = Path(str(caminho)).suffix or ".wav"
    base = str(rotulo or "dubx").replace("—", "-").replace("→", "-")
    base = re.sub(r"[\\/:*?\"<>|]", "-", base)     # proibidos no Windows
    base = re.sub(r"\.(mp4|mov|mkv|avi|mxf|wav|mp3|m4a|ogg|flac)\b", "", base,
                  flags=re.IGNORECASE)             # extensao no meio do rotulo
    base = re.sub(r"\s+", " ", base).strip(" .-") or "dubx"
    return base[:110] + ext


def _nome_da_faixa(caminho, i):
    """Nome humano de UMA faixa da separacao, ANTES do batismo em disco
    (o stem original ainda diz o papel: voz1/Music/Sfx/Fala/Personagem)."""
    cru = Path(str(caminho)).stem
    s = cru.lower()
    bonito = {"music": "Música", "musica": "Música", "sfx": "Efeitos",
              "speech": "Fala", "fala": "Fala"}
    if s in bonito:
        return bonito[s]
    if s.startswith("voz"):
        return f"Voz {i}"
    return cru                     # nome do personagem (motor por roteiro)


def _batiza_entrega(t, caminho, rotulo):
    """BATISMO (24/08, pedido do Luan): a entrega vai pro DISCO com o nome
    humano — o Premiere mostra o nome do arquivo, e "music.wav"/"{hash}.wav"
    viravam mídia duplicada/ilegível na timeline. O arquivo muda pra
    TRAB/{t}_entrega/<nome amigavel> — mesmo nome do download, e o prefixo
    {t} no primeiro segmento mantém a faxina por tarefa funcionando.
    Originais fora da TRAB (modos _no_lugar) voltam intactos; erro de disco
    nunca derruba a entrega (devolve o caminho velho)."""
    try:
        c = Path(str(caminho))
        if not c.exists() or TRAB not in c.parents:
            return str(caminho)
        if c.parent.name.endswith("_entrega"):
            return str(c)                        # ja batizado (ex.: separar)
        pasta = TRAB / f"{t}_entrega"
        pasta.mkdir(parents=True, exist_ok=True)
        novo = pasta / _nome_amigavel(rotulo, c)
        base = novo
        k = 2
        while novo.exists() and novo != c:
            novo = pasta / f"{base.stem} ({k}){base.suffix}"
            k += 1
        c.replace(novo)
        return str(novo)
    except OSError:
        return str(caminho)


AJUSTES_TROCA = ("voz", "motor_vc", "sidon", "presenca", "atuacao", "semelhanca", "tom", "f0", "preservar", "puxar_tom", "lufs",
                 # 29/10 (P30): Sidon antes/depois/sem + os ajustes do motor ElevenLabs
                 "sidon_quando", "el_voice_id", "el_nome", "el_modelo", "el_estabilidade", "el_semelhanca", "el_estilo",
                 "el_speaker_boost", "el_remover_ruido",
                 # 29/10 (P32): o arquivo entregue sai com o mesmo pico do trecho de entrada
                 "casar_pico")
PRESETS =BASE / "presets.json"
# 29/10 (Kakashi 16:35 [08:38] «Salva esse preset do pedaleiro aqui... Botar um preset aqui também» + chat «quando clicar no
# pedaleira, coloca aquele preset como padrão»): um preset por voz; o painel aplica ao escolher a voz. O da Pedaleira veio do
# print 00:03 da gravação (a prévia 96f0e50804 que ele aprovou) — o histórico de então não guardava os valores: confirmar de ouvido.
PRESETS_SEMENTE = {"Pedaleira": {"motor_vc": "rvc", "sidon": 0, "presenca": 0.50, "atuacao": 0.0, "semelhanca": 0.0, "tom": -12,
                                 "f0": "rmvpe", "preservar": True, "puxar_tom": False,
                                 "nota": "o que ele aprovou em 29/10 16:35 (provável; lido do print, confirmar de ouvido)"}}
_TRAVA_PRESETS = threading.Lock()


def le_presets():
    with _TRAVA_PRESETS:
        try:
            d = json.loads(PRESETS.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        mudou = False
        for k, v in PRESETS_SEMENTE.items():
            if k not in d:
                d[k] = dict(v); mudou = True
        if mudou:
            PRESETS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        return d


def grava_preset(voz, ajustes):
    with _TRAVA_PRESETS:
        try:
            d = json.loads(PRESETS.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        d[voz] = {k: ajustes.get(k) for k in AJUSTES_TROCA if k in ajustes and k != "voz"}
        d[voz]["nota"] = "salvo pelo painel em " + _agora()
        PRESETS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        return d[voz]


def registra_historico(entrada):
    if entrada.get("saida") and not entrada.get("amigavel"):
        entrada["amigavel"] = _nome_amigavel(entrada.get("rotulo"),
                                             entrada.get("saida"))
    with _TRAVA_HIST:
        try:
            itens = json.loads(HISTORICO.read_text(encoding="utf-8"))
        except Exception:
            itens = []
        itens.insert(0, entrada)
        del itens[200:]
        HISTORICO.write_text(json.dumps(itens, ensure_ascii=False, indent=1),
                             encoding="utf-8")


def le_historico():
    with _TRAVA_HIST:
        try:
            itens = json.loads(HISTORICO.read_text(encoding="utf-8"))
        except Exception:
            return []
    for i in itens:
        i["existe"] = bool(i.get("saida")) and Path(i["saida"]).exists()
    return itens


# --------------------------------------------------------------- biblioteca
def listar_vozes():
    """Cada subpasta de vozes/ e uma voz. referencia.wav = obrigatorio (serve pros
    dois modos). modelo.pth + indice.index = opcionais, so quem tem pode trocar voz."""
    VOZES.mkdir(parents=True, exist_ok=True)
    saida = []
    for p in sorted(VOZES.iterdir()):
        if not p.is_dir():
            continue
        ref = p / "referencia.wav"
        modelo = p / "modelo.pth"
        indice = p / "indice.index"
        seedvc = p / "seedvc.pth"      # 2o motor (opcional, so quem foi fine-tunado)
        if not ref.exists():
            continue
        saida.append({
            "nome": p.name,
            "referencia": str(ref),
            "modelo": str(modelo) if modelo.exists() else None,
            "indice": str(indice) if indice.exists() else None,
            "pode_trocar": modelo.exists(),
            "seedvc": str(seedvc) if seedvc.exists() else None,
            "pode_trocar_seedvc": seedvc.exists(),
        })
    return saida


# ------------------------------------------------------------------ cancelar
def _tarefa_atual():
    return TAREFA_DA_THREAD.get(threading.get_ident())


def _cancelada():
    t = _tarefa_atual()
    return bool(t) and t in CANCELADAS


def _registra_filho(pr):
    t = _tarefa_atual()
    if t:
        PROCESSOS.setdefault(t, set()).add(pr)
        if t in CANCELADAS:      # cancelou enquanto este subia: mata ja
            _mata_arvore(pr)
    return pr


def _esquece_filho(pr):
    for vivos in PROCESSOS.values():
        vivos.discard(pr)


def _mata_arvore(pr):
    """Mata o filho E OS NETOS.

    ⚠️ Nao basta pr.kill(): o separador-cinema sobe um python que sobe OUTRO
    python (foi o par 2764/27212 que travou o PC em 20/09). Matando so o pai,
    o neto continua com a GPU na mao. No Windows quem resolve e o /T."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pr.pid)],
                           capture_output=True, creationflags=SEM_JANELA)
        else:
            # fora do Windows: primeiro os filhos diretos (pkill -P), depois o pai.
            # Netos de netos podem sobrar — o /T do taskkill nao tem igual aqui.
            try:
                subprocess.run(["pkill", "-9", "-P", str(pr.pid)], capture_output=True)
            except Exception:
                pass
            pr.kill()
    except Exception:
        pass


def cancelar(t=None):
    """Para UMA tarefa (t) ou TODAS (t=None). Devolve os ids parados."""
    alvos = [t] if t else [k for k, v in TAREFAS.items()
                           if (v or {}).get("estado") in ("rodando", "na fila")]
    parados = []
    for alvo in alvos:
        if not alvo:
            continue
        CANCELADAS.add(alvo)
        for pr in list(PROCESSOS.get(alvo, ())):
            _mata_arvore(pr)
        estado = (TAREFAS.get(alvo) or {}).get("estado")
        if estado in ("rodando", "na fila", None):
            TAREFAS[alvo] = {"estado": "cancelado",
                             "mensagem": "cancelado por você"}
        parados.append(alvo)
    return parados


# ------------------------------------------------------------------ motores
def roda(cmd, cwd=None):
    # UTF-8 NOS DOIS LADOS, e nao e detalhe (17/08): sem isso o filho escreve
    # no stdout com a codepage do Windows (cp1252) e QUALQUER print com
    # acento/simbolo estoura UnicodeEncodeError NELE — a dublagem inteira
    # morria por causa de um "⚠ invadiu a pausa" do encaixe.py. PYTHONIOENCODING
    # conserta o lado que escreve; encoding="utf-8" aqui conserta o que le.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    # ⚠️ Popen no lugar do subprocess.run (20/09/2026): o run() nao devolve
    # handle nenhum enquanto o filho trabalha, e sem handle nao tem como
    # CANCELAR. O comportamento e o mesmo — communicate() espera igual.
    pr = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace",
                          cwd=str(cwd) if cwd else None, env=env,
                          creationflags=SEM_JANELA)
    _registra_filho(pr)
    try:
        saida, erro = pr.communicate()
    finally:
        _esquece_filho(pr)
    if _cancelada():
        raise Cancelado("cancelado por você")
    if pr.returncode != 0:
        # a ultima linha costuma ser a barra do tqdm ("71%|... 13.42s/it"), que
        # esconde o erro de verdade — pula linhas de progresso ao reportar
        linhas = [l for l in (erro or saida or "").strip().splitlines()
                  if l.strip() and "%|" not in l and "it/s" not in l and "s/it" not in l]
        raise RuntimeError(linhas[-1] if linhas else f"codigo {pr.returncode}")
    return saida


# progresso que os dois motores ja imprimem (nao inventei formato nenhum):
#   Seed-VC  motor\seedvc\train.py:354       "epoch 0, step 340, loss: 1.23"
#   RVC      treino\rvc\train\train.py:884  "Nome | epoch=12 | step=980 | ..."
_RE_PASSO_SEEDVC = re.compile(r"\bstep (\d+)")
_RE_EPOCA_RVC = re.compile(r"\bepoch=(\d+)")


def roda_vendo(cmd, cwd=None, olho=None):
    """Igual ao roda(), mas LENDO a saida enquanto o filho trabalha.

    Por que existe (25/08): ele mandou treinar dentro do Premiere e ficou sem
    saber se estava indo - "nao ta mostrando nada, mas minha placa de video
    ta fazendo barulho". Estava indo; o servidor e que so avisava no comeco e
    no fim de cada etapa, e as etapas caras duram de 5 minutos a horas.

    O roda() nao serve pra isso: capture_output=True so entrega o texto
    DEPOIS que o processo morre. Aqui cada linha passa pelo `olho`, que
    transforma em passo na barra do painel.

    PYTHONUNBUFFERED nao e enfeite: com a saida indo pra um cano, o print()
    do filho vira bloco-bufferizado e as noticias chegariam em lotes de 8 KB
    (ou seja, no fim). O tqdm escreve com \r, que o modo texto do Python ja
    trata como quebra de linha - por isso da pra ler a barrinha dele tambem.
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONUNBUFFERED="1")
    pr = subprocess.Popen(
        [str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env,
        creationflags=SEM_JANELA, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", bufsize=1)
    _registra_filho(pr)
    linhas = []
    for linha in pr.stdout:
        linha = linha.rstrip("\r\n")
        linhas.append(linha)
        # um treino longo cospe dezenas de milhares de linhas de tqdm; guardo
        # so o rabo, que e o que interessa quando da errado
        if len(linhas) > 400:
            del linhas[:200]
        if olho:
            try:
                olho(linha)
            except Exception:
                pass          # noticia quebrada nao pode derrubar o treino
    pr.wait()
    _esquece_filho(pr)
    if _cancelada():
        raise Cancelado("cancelado por você")
    if pr.returncode != 0:
        uteis = [l for l in linhas if l.strip() and "%|" not in l
                 and "it/s" not in l and "s/it" not in l]
        raise RuntimeError(uteis[-1] if uteis else f"codigo {pr.returncode}")
    return "\n".join(linhas)


def nivelar(entrada, saida, lufs, dose="nivelar"):
    """⚠️ O voz.py SAI COM CODIGO 0 mesmo quando falha num item: ele imprime
    "ERRO —" e devolve {"files": []}. Como o roda() so levanta em returncode
    != 0, ninguem percebia que o arquivo nao nasceu.

    Foi assim que a voz "Criatura 1" virou uma PASTA VAZIA em 24/08: o treino
    disse "voz pronta", o historico registrou, e ela nunca apareceu na lista —
    porque listar_vozes pula qualquer pasta sem referencia.wav. Ele passou a
    noite achando que nao conseguia ESCOLHER a voz; ela e que nao existia.

    Agora: se o nivelamento nao entregar, entrega o audio CRU convertido.
    Melhor uma amostra sem nivelar do que voz nenhuma."""
    entrada, saida = Path(entrada), Path(saida)
    motivo = ""
    try:
        roda([PY_VOZ, MOTOR / "voz.py", "-i", entrada, "-o", saida,
              "--dose", dose, "--lufs", lufs])
    except Exception as exc:
        motivo = str(exc)
    if saida.exists():
        return saida
    # plano B: o proprio audio, so convertido — sem nivelar
    try:
        ff = achar_ffmpeg()
        roda([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", entrada,
              "-vn", "-acodec", "pcm_f32le", "-ar", "48000", saida])
    except Exception as exc:
        motivo = motivo or str(exc)
    if not saida.exists():
        raise RuntimeError(
            f"nao consegui gerar {saida.name} a partir de {entrada.name}"
            + (f" ({motivo})" if motivo else ""))
    return saida


def modo_melhorar(t, p):
    # _passo: sem isto a barrinha do painel ficava parada em 30% (o
    # fallback do barraTarefa quando a tarefa nao conta etapa). O
    # melhorar_no_lugar sempre contou; este, que virou o padrao do
    # painel em 24/08, nao contava.
    dose = str(int(p.get("dose", 100)))
    lufs = str(p.get("lufs", -16))
    _passo(t, 1, 2, "preparando o audio...")
    fonte = _audio_de(t, Path(p["arquivo"]))   # video: extrai o audio antes
    destino = TRAB / f"{t}_melhorado.wav"
    _passo(t, 2, 2, "melhorando...", fracao=0.02)
    _melhora_vendo(t, 2, 2, fonte, destino, dose, lufs, base=0.02, peso=0.96,
                   partes=TRAB / f"{t}_partes")
    return destino


def remistura_geracao(base, dose, arquivo=None, lufs=-16):
    """05/11 (ele: «melhorou o audio, beleza, e ai deixa o slider de reconstrucao pra eu ficar brincando»): o Sidon
    roda UMA vez no melhorar e guarda seco + reconstruido (alinhados) em trabalho/<tarefa>_partes; aqui so remistura
    na dose nova (~1 s, sem GPU) e regrava o MESMO arquivo da geracao — wav e, se a saida for video, o video tambem.
    `arquivo` = o painel do Premiere manda o wav que esta na timeline."""
    base = re.sub(r"[^0-9a-zA-Z_-]", "", str(base or ""))
    partes = TRAB / f"{base}_partes"
    if not base or not (partes / "molhado.wav").exists():
        raise RuntimeError("esse áudio foi melhorado antes da reconstrução ajustável — melhore de novo uma vez")
    dose = max(0, min(100, int(dose)))
    item = next((i for i in le_historico() if i.get("id") == base), {}) or {}
    wav = Path(arquivo or item.get("previa") or item.get("saida") or "")
    if not str(wav) or wav.suffix.lower() != ".wav":
        raise RuntimeError("não achei o áudio dessa geração pra regravar")
    novo = TRAB / f"{base}_remisturado.wav"
    roda([PY_VOZ, MOTOR / "voz.py", "-i", partes / "seco.wav", "-o", novo, "--remisturar", partes,
          "--dose", str(dose), "--lufs", str(lufs)])
    shutil.copy2(novo, wav)
    novo.unlink(missing_ok=True)
    saida = Path(str(item.get("saida") or ""))
    if item.get("previa") and saida.suffix.lower() in COM_VIDEO and item.get("antes"):
        v = _com_video(base + "_remix", item["antes"], wav)
        if v:
            shutil.move(str(v), str(saida))
    with _TRAVA_HIST:
        try:
            itens = json.loads(HISTORICO.read_text(encoding="utf-8"))
            for it in itens:
                if it.get("id") == base:
                    it["reconstrucao"] = dose
            HISTORICO.write_text(json.dumps(itens, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
    return {"ok": True, "dose": dose, "arquivo": str(wav)}


# "SIDON 3/18" — o voz.py imprime a cada janela de 96 s que o Sidon termina
_RE_SIDON = re.compile(r"\bSIDON (\d+)/(\d+)")


def _melhora_vendo(t, etapa, de, fonte, destino, dose, lufs, base, peso, partes=None):
    """voz.py pelo roda_vendo, com a barra andando pedaco a pedaco.

    Por que existe (29/09/2026): o painel fazia "etapa 2 de 2" = 100% no
    instante em que o Sidon COMECAVA. Clipe de 28 min: 16 min cravado em
    "100% · falta menos de 1 min", e ele achou que tinha travado.
    `base`/`peso` = que fatia da barra o Sidon ocupa (o resto e rapido)."""
    est = [None]

    def olho(linha):
        m = _RE_SIDON.search(linha)
        if not m:
            return
        feito, total = int(m.group(1)), int(m.group(2))
        if est[0] is None:
            est[0] = _Estimador(total)
        falta = est[0].falta(feito)
        _passo(t, etapa, de,
               f"melhorando… pedaço {feito} de {total}" + _quanto_falta(falta),
               fracao=base + peso * feito / total, restante_s=falta)

    roda_vendo([PY_VOZ, MOTOR / "voz.py", "-i", fonte, "-o", destino,
                "--dose", str(dose), "--lufs", str(lufs)]
               + (["--partes", partes] if partes else []), olho=olho)


def _dose_sidon(p):
    """Barra 0-100 do Sidon (pedido de 16/08, substitui o com/sem):
    0 = cru so nivelado, 100 = reconstrucao completa. true/false da versao
    de algumas horas antes viram 100/0."""
    v = p.get("sidon", 100)
    if isinstance(v, bool):
        v = 100 if v else 0
    return max(0, min(100, int(v)))


# SIDON ANTES / DEPOIS / SEM (29/10, Kakashi 16:35 [00:56]: «se eu ativo o Sidon, ele entra antes ou depois? não
# poderia ter uma opção... antes, depois ou sem»). Ate aqui ele SO entrava DEPOIS: no nivelar() que recebe a voz ja
# trocada. "antes" limpa a fala de ENTRADA e o acabamento vira so nivelar (dose 0); "sem" nao roda o Sidon em lugar
# nenhum (o nivelar continua: e ele que reamostra pra 48 kHz e acerta o LUFS). A dose (sidon 0-100) vale pros dois.
SIDON_QUANDO = ("antes", "depois", "sem")
SIDON_QUANDO_PADRAO = "depois"   # o comportamento de antes de 29/10


def _sidon_quando(p):
    q = str(p.get("sidon_quando") or SIDON_QUANDO_PADRAO).strip().lower()
    return q if q in SIDON_QUANDO else SIDON_QUANDO_PADRAO


def _dose_sidon_depois(p):
    """Dose do acabamento (o nivelar depois da troca): a de sempre so quando o Sidon entra DEPOIS."""
    return _dose_sidon(p) if _sidon_quando(p) == "depois" else 0


def _sidon_antes(t, entrada, p):
    """Com sidon_quando="antes", passa a fala de entrada pelo Sidon (na dose escolhida) antes da troca.
    Devolve o arquivo que vai pro motor de voz — o proprio entrada quando nao e "antes" ou a dose e 0."""
    dose = _dose_sidon(p)
    if _sidon_quando(p) != "antes" or dose <= 0:
        return entrada
    limpa = TRAB / f"{t}_sidon_antes.wav"
    nivelar(entrada, limpa, "-16", dose=str(dose))
    return limpa


def _rvc(t, voz, entrada, saida, p):
    """A inferencia do RVC, a mesma pros dois caminhos da troca."""
    roda([PY_RVC, TREINO / "core.py", "infer",
          "--input-path", entrada, "--output-path", saida,
          "--pth-path", voz["modelo"], "--index-path", voz["indice"],
          "--f0-method", p.get("f0", "rmvpe"),
          "--protect", str(p.get("presenca", 0.15)),
          "--index-rate", str(p.get("semelhanca", 0.5)),
          "--volume-envelope", str(p.get("atuacao", 1.0)),
          "--pitch", str(int(p.get("tom", 0)))], cwd=TREINO)


def _seedvc(t, voz, entrada, saida, p):
    """A inferencia do Seed-VC. Mesma assinatura do _rvc de proposito: e o
    UNICO ponto que muda entre os dois motores, e e isso que torna o A/B
    honesto (uma variavel so). Sem fine-tune da voz, cai no zero-shot usando
    a referencia.wav como alvo."""
    cmd = [PY_SEEDVC, MOTOR / "vc_seedvc.py",
           "-i", entrada, "-a", voz["referencia"], "-o", saida,
           "--passos", str(int(p.get("passos", 30))),
           # "Semelhanca" tambem vale aqui: no Seed-VC ela e o cfg-rate, o
           # quanto a geracao e puxada pra voz-alvo. Ate 24/08 so o RVC usava
           # esse slider e o Seed-VC ficava preso no 0.7 de fabrica.
           "--cfg-rate", str(round(float(p.get("semelhanca", 0.7)), 3))]
    # P36 (29/10): o seedvc.pth da voz e afinado no modelo de 22 kHz SEM altura (SEEDVC_CONFIG, f0_condition
    # false). Junto com --f0 o inference.py carrega o audio a 22 kHz mas faz as contas da janela como 44 kHz:
    # com a referencia da Pedaleira (243 s) a janela da fala ficava NEGATIVA (-1723 quadros), o pedaco da fala
    # saia com 0 quadros e o vocoder quebrava («Kernel size can't be greater than actual input size», 6 < 7).
    # E mesmo sem quebrar o afinado nao tem entrada de altura — o botao nao faria nada. Entao: com «puxar a
    # altura» vai o modelo 44 kHz com altura (zero-shot pela referencia.wav); sem ele, o afinado como sempre.
    if voz.get("seedvc") and not p.get("puxar_tom"):
        cmd += ["--modelo", voz["seedvc"], "--config", SEEDVC_CONFIG]

    # PUXAR A ALTURA (24/08). Sem isto o Seed-VC troca o TIMBRE e mantem a
    # ALTURA da fala de entrada — e foi exatamente o que ele ouviu: amostra de
    # monstro, resultado com voz fina. Ligando, entra o modelo condicionado a
    # pitch (44 kHz) e o auto ajuste move a altura pra faixa da voz-alvo.
    # Fica opcional porque e outro modelo: baixa na primeira vez e e mais lento.
    if p.get("puxar_tom"):
        cmd += ["--f0", "--auto-f0"]
        if int(p.get("tom", 0)):
            cmd += ["--tom", str(int(p.get("tom", 0)))]
    roda(cmd, cwd=MOTOR / "seedvc")


# ------------------------------------------------------- motor ElevenLabs (nuvem)
# 29/10 (Kakashi: «coloca o motor Eleven Labs aqui também, aí coloca só as opções que o Eleven Labs tem»). Terceiro
# motor da troca, o UNICO que nao roda local: Speech-to-Speech da API, na conta PESSOAL dele. Gasta credito a cada
# troca. So substitui o passo da troca — separar, Sidon, LUFS e devolver o fundo continuam iguais aos outros motores.
# A chave e lida AQUI, na hora, do arquivo dele; nunca vai pra log, historico, resposta ou mensagem de erro.
ARQ_EL_CHAVE = Path.home() / ".elevenlabs" / "chave-api.txt"
EL_API = "https://api.elevenlabs.io/v1"
EL_MODELO_PADRAO = "eleven_multilingual_sts_v2"
# mp3 e nao pcm_44100 de proposito: o PCM 44,1 kHz e restrito a plano Pro na API; o mp3 vale em qualquer plano e o
# ffmpeg devolve em wav 48 kHz logo depois.
EL_FORMATO_SAIDA = "mp3_44100_128"


def _chave_elevenlabs():
    try:
        chave = ARQ_EL_CHAVE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        chave = ""
    if not chave:
        raise RuntimeError(f"sem a chave do ElevenLabs em {ARQ_EL_CHAVE}")
    return chave


def _el_pede(caminho, corpo=None, tipo=None, timeout=60):
    """Uma chamada a API do ElevenLabs (so stdlib). Devolve os bytes da resposta.
    Erro HTTP vira RuntimeError com o motivo que a API mandou — sem a chave."""
    req = urllib.request.Request(EL_API + caminho, data=corpo, method="POST" if corpo is not None else "GET")
    req.add_header("xi-api-key", _chave_elevenlabs())
    if tipo:
        req.add_header("Content-Type", tipo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        motivo = ""
        try:
            d = json.loads(exc.read().decode("utf-8", "replace")).get("detail")
            if isinstance(d, dict):
                motivo = d.get("message") or d.get("status") or ""
            elif isinstance(d, list) and d:
                motivo = "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in d)
            elif d:
                motivo = str(d)
        except Exception:
            pass
        raise RuntimeError(f"ElevenLabs recusou ({exc.code}): {motivo or exc.reason}"[:300])
    except urllib.error.URLError as exc:
        raise RuntimeError(f"sem conexao com o ElevenLabs ({exc.reason})"[:300])


def el_vozes():
    """As vozes da conta dele, pro dropdown do painel: [{id, nome, categoria}]."""
    d = json.loads(_el_pede("/voices", timeout=30).decode("utf-8"))
    vozes = [{"id": v.get("voice_id", ""), "nome": v.get("name", ""), "categoria": v.get("category", "")}
             for v in d.get("voices", []) if v.get("voice_id")]
    return sorted(vozes, key=lambda v: v["nome"].lower())


def _el_bool(v, padrao):
    if v is None or v == "":
        return padrao
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "sim", "on", "yes")
    return bool(v)


def _el_0a1(v, padrao):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return padrao


def _elevenlabs(t, voz, entrada, saida, p):
    """Speech-to-Speech do ElevenLabs. Mesma assinatura do _rvc/_seedvc; a voz-alvo vem de el_voice_id (a voz
    local `voz` nao e usada). Sai em wav 48 kHz mono, como os outros motores entregam pro acabamento."""
    voice_id = str(p.get("el_voice_id") or "").strip()
    if not voice_id:
        raise RuntimeError("escolha uma voz do ElevenLabs (el_voice_id vazio)")
    ff = achar_ffmpeg()
    # Sobe enxuto: mono 16 bit 44,1 kHz (o f32/48 k da corrente dobra o upload e nao melhora nada do lado de la).
    envio = TRAB / f"{t}_el_envio.wav"
    roda([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", entrada,
          "-vn", "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", envio])
    ajustes = {"stability": _el_0a1(p.get("el_estabilidade"), 0.5),
               "similarity_boost": _el_0a1(p.get("el_semelhanca"), 0.75),
               "style": _el_0a1(p.get("el_estilo"), 0.0),
               "use_speaker_boost": _el_bool(p.get("el_speaker_boost"), True)}
    campos = {"model_id": str(p.get("el_modelo") or EL_MODELO_PADRAO),
              "voice_settings": json.dumps(ajustes),
              "remove_background_noise": "true" if _el_bool(p.get("el_remover_ruido"), False) else "false"}
    # multipart na mao (stdlib nao tem): um campo de texto por ajuste + o arquivo em "audio"
    fronteira = "dubx" + uuid.uuid4().hex
    partes = []
    for nome, valor in campos.items():
        partes.append(f"--{fronteira}\r\nContent-Disposition: form-data; name=\"{nome}\"\r\n\r\n{valor}\r\n"
                      .encode("utf-8"))
    partes.append(f"--{fronteira}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"entrada.wav\"\r\n"
                  f"Content-Type: audio/wav\r\n\r\n".encode("utf-8"))
    partes.append(envio.read_bytes())
    partes.append(f"\r\n--{fronteira}--\r\n".encode("utf-8"))
    audio = _el_pede(f"/speech-to-speech/{quote(voice_id, safe='')}?output_format={EL_FORMATO_SAIDA}",
                     corpo=b"".join(partes), tipo=f"multipart/form-data; boundary={fronteira}", timeout=600)
    if _cancelada():
        raise Cancelado("cancelado por você")
    if len(audio) < 200:
        raise RuntimeError("o ElevenLabs devolveu um audio vazio")
    volta = TRAB / f"{t}_el_volta.mp3"
    volta.write_bytes(audio)
    roda([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", volta,
          "-ac", "1", "-ar", "48000", "-c:a", "pcm_f32le", saida])


# O A/B vive aqui: quem chama pede o motor pelo nome e nao sabe mais nada.
MOTORES_VC = {"rvc": _rvc, "seedvc": _seedvc, "elevenlabs": _elevenlabs}


# PADRAO = seedvc desde 23/08. Veredito de ouvido dele no teste cego 2 (o
# primeiro com fonte de voz DIFERENTE do alvo — o de 17/08 era Plexus->Plexus e
# nao testava timbre). As tres medidas bateram: ouvido, altura da voz (217 Hz
# contra 219 do alvo; o RVC foi pra 161, pro lado contrario) e palavra
# corrompida (1/43 contra 3/43). O RVC continua disponivel em motor_vc="rvc".
MOTOR_VC_PADRAO = "seedvc"


def _troca_voz(t, voz, entrada, saida, p):
    motor = str(p.get("motor_vc", MOTOR_VC_PADRAO))
    if motor not in MOTORES_VC:
        raise RuntimeError(f"motor_vc '{motor}' nao existe "
                           f"(use: {', '.join(MOTORES_VC)})")
    if motor == "seedvc" and not PY_SEEDVC.exists():
        raise RuntimeError("o Seed-VC nao esta instalado (motor\\.venv-seedvc)")
    MOTORES_VC[motor](t, voz, entrada, saida, p)


def modo_trocar(t, p):
    motor = str(p.get("motor_vc", MOTOR_VC_PADRAO))
    voz = next((v for v in listar_vozes() if v["nome"] == p.get("voz")), None)
    if motor == "elevenlabs":
        # A voz-alvo mora na conta ElevenLabs dele (el_voice_id), nao em vozes\ — a local e opcional.
        if not str(p.get("el_voice_id") or "").strip():
            raise RuntimeError("escolha uma voz do ElevenLabs (el_voice_id vazio)")
        voz = voz or {"nome": p.get("el_nome") or p.get("voz") or p.get("el_voice_id"),
                      "modelo": "", "indice": "", "referencia": ""}
    elif not voz:
        raise RuntimeError(f"voz '{p.get('voz')}' nao encontrada")
    # O RVC exige modelo treinado; o Seed-VC roda zero-shot pela referencia.
    if motor == "rvc" and not voz["modelo"]:
        raise RuntimeError(f"a voz '{p.get('voz')}' nao tem modelo treinado")

    fonte = _audio_de(t, Path(p["arquivo"]))   # video: extrai o audio antes

    # "Manter o ambiente": caminho novo, com stems. Ver _trocar_preservando.
    if p.get("preservar"):
        return _trocar_preservando(t, p, voz, fonte)

    cru = TRAB / f"{t}_rvc.wav"
    _troca_voz(t, voz, _sidon_antes(t, fonte, p), cru, p)

    # O RVC devolve 24 kHz. A barra do Sidon (0-100) dita o acabamento:
    # 0 = cru so nivelado/reamostrado, 100 = reconstrucao completa.
    # Com sidon_quando "antes"/"sem" o acabamento e so o nivelar (dose 0).
    destino = TRAB / f"{t}_trocado.wav"
    nivelar(cru, destino, str(p.get("lufs", -16)), dose=str(_dose_sidon_depois(p)))
    # P32: pico igual ao do trecho de entrada (antes do corte, que grava em inteiro)
    return cortar_no_tempo(_casar_pico(t, fonte, destino, p), fonte)


# ------------------------------------------ trocar voz MANTENDO o ambiente
# O problema que isto resolve (17/08/2026): jogar a mixagem inteira no RVC
# devolve voz de estudio SECA — sem sala, sem reverb, sem a cama de fundo que
# o Seedance ja tinha gerado casada com a imagem. Dai sobra "reambientar na
# mao", que e a queixa dele com o voice changer do Eleven Labs ("fica voz de
# microfone, nao fica ambientada").
#
# O desenho: a voz e SEPARADA da trilha, o RVC so ve a voz, e o resto do mundo
# volta por cima INTACTO. O "resto" nao vem do modelo — vem de SUBTRACAO
# (fonte menos a voz isolada), entao ele e o audio original amostra a amostra,
# sem buraco onde a fala estava e sem passar por resample nenhum.
#
# Nivel: a voz nova volta pro LUFS que a voz ANTIGA tinha DENTRO da mistura,
# nao pro alvo do arquivo inteiro. E isso que preserva o equilibrio voz x
# ambiente — normalizar a voz pra -16 aqui a poria gritando na frente da cama.


def _canais_de(arquivo):
    """Contagem de canais. 0 quando nao da pra medir — quem chama assume 2."""
    fp = _ffprobe()
    try:
        r = subprocess.run([str(fp), "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=channels",
                            "-of", "csv=p=0", str(arquivo)],
                           capture_output=True, text=True,
                           creationflags=SEM_JANELA)
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return 0


def separar_stems(t, fonte, p):
    """Devolve (voz, resto), os dois em 48 kHz e na contagem de canais da
    fonte, somando de volta na fonte.

    ⚠️ O demucs so aceita ESTEREO (bug de broadcast do torch com mono, ja pago
    no Beat Match) e trabalha em 44,1 kHz. Entao: converte pra 44,1/estereo pra
    ele, traz a voz de volta pra 48 k, e calcula o resto por SUBTRACAO na taxa
    original — assim o ambiente nunca faz a volta 48→44,1→48."""
    ff = achar_ffmpeg()
    nc = _canais_de(fonte) or 2
    _passo(t, 1, 5, "separando a voz do ambiente…")
    entrada = TRAB / f"{t}_stem_in.wav"
    roda([ff, "-y", "-i", fonte, "-ac", "2", "-ar", "44100",
          "-c:a", "pcm_f32le", entrada])
    pasta = TRAB / f"{t}_stems"
    motor = str(p.get("separador", "demucs"))
    if motor not in PY_SEPARADOR:
        raise RuntimeError(f"separador '{motor}' nao existe "
                           f"(use: {', '.join(PY_SEPARADOR)})")
    roda([PY_SEPARADOR[motor], MOTOR / "stems.py", "-i", entrada,
          "-o", pasta, "--motor", motor])

    voz = TRAB / f"{t}_stem_voz.wav"
    roda([ff, "-y", "-i", pasta / "voz.wav", "-ar", "48000", "-ac", str(nc),
          "-c:a", "pcm_f32le", voz])

    _passo(t, 2, 5, "isolando o ambiente…")
    # resto = fonte - voz, com a voz em fase invertida. Nao usamos o
    # "no_vocals" do modelo de proposito: ele sai em 44,1 k, e a cama de fundo
    # e a parte que tem que ficar intocada.
    resto = TRAB / f"{t}_stem_resto.wav"
    roda([ff, "-y", "-i", fonte, "-i", voz, "-filter_complex",
          "[1:a]volume=-1[inv];"
          "[0:a][inv]amix=inputs=2:duration=first:normalize=0[m]",
          "-map", "[m]", "-ar", "48000", "-ac", str(nc),
          "-c:a", "pcm_f32le", resto])
    return voz, resto, nc


def _trocar_preservando(t, p, voz_alvo, fonte):
    ff = achar_ffmpeg()
    lufs_geral = medir_lufs(fonte)
    stem_voz, stem_resto, nc = separar_stems(t, fonte, p)
    lufs_voz = medir_lufs(stem_voz)

    _passo(t, 3, 5, f"trocando a voz para {p.get('voz', '?')}…")
    # O RVC come MONO. Dialogo e centrado, entao o downmix nao custa imagem
    # estereo de verdade — a cama de fundo, que e onde o estereo mora, nem
    # passa por aqui.
    mono = TRAB / f"{t}_stem_voz_mono.wav"
    roda([ff, "-y", "-i", stem_voz, "-ac", "1", "-ar", "48000",
          "-c:a", "pcm_f32le", mono])
    cru = TRAB / f"{t}_rvc.wav"
    _troca_voz(t, voz_alvo, _sidon_antes(t, mono, p), cru, p)

    _passo(t, 4, 5, "acabamento da voz…")
    acabada = TRAB / f"{t}_voz_acabada.wav"
    nivelar(cru, acabada, "-16", dose=str(_dose_sidon_depois(p)))
    nova = TRAB / f"{t}_voz_nc.wav"
    roda([ff, "-y", "-i", acabada, "-ar", "48000", "-ac", str(nc),
          "-c:a", "pcm_f32le", nova])
    # Medir DEPOIS de virar nc canais: mono duplicado em estereo mede ~3 dB a
    # mais no LUFS (a norma soma os canais). Medir antes poria a voz baixa.
    if lufs_voz is not None:
        atual = medir_lufs(nova)
        if atual is not None:
            delta = max(-20.0, min(20.0, lufs_voz - atual))
            ajustada = TRAB / f"{t}_voz_ajustada.wav"
            roda([ff, "-y", "-i", nova, "-af", f"volume={delta:.2f}dB",
                  "-ar", "48000", "-c:a", "pcm_f32le", ajustada])
            nova = ajustada
    nova = cortar_no_tempo(nova, stem_voz)

    _passo(t, 5, 5, "remontando com o ambiente original…")
    mix = TRAB / f"{t}_remix.wav"
    roda([ff, "-y", "-i", stem_resto, "-i", nova, "-filter_complex",
          "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[m]",
          "-map", "[m]", "-ar", "48000", "-ac", str(nc),
          "-c:a", "pcm_f32le", mix])

    # Correcao final de nivel: a soma deveria cair no LUFS do original
    # sozinha (a voz voltou pro nivel dela, o resto nem foi tocado). Teto
    # APERTADO de proposito — delta grande aqui nao e ajuste, e sintoma de
    # separacao que deu errado, e e melhor ver o numero do que mascarar.
    if lufs_geral is not None:
        atual = medir_lufs(mix)
        if atual is not None:
            delta = max(-6.0, min(6.0, lufs_geral - atual))
            corr = TRAB / f"{t}_remix_niv.wav"
            roda([ff, "-y", "-i", mix, "-af", f"volume={delta:.2f}dB",
                  "-ar", "48000", "-c:a", "pcm_f32le", corr])
            mix = corr

    # P32: por ultimo, o pico da remontagem = pico do trecho de entrada (antes do corte, que grava em inteiro)
    final = cortar_no_tempo(_casar_pico(t, fonte, mix, p), fonte)
    # Mesmo porteiro da ambientacao: este wav pode ser REGRAVADO no arquivo
    # dele. Saida imedivel (inf/NaN) e erro na cara, nunca midia corrompida
    # em silencio.
    if medir_lufs(final) is None:
        raise RuntimeError(
            "a remontagem saiu corrompida (amostras não-finitas) — rode de "
            "novo; o arquivo original não foi tocado")
    return final


def modo_falar(t, p):
    voz = next((v for v in listar_vozes() if v["nome"] == p["voz"]), None)
    if not voz:
        raise RuntimeError(f"voz '{p.get('voz')}' nao encontrada")

    cru = TRAB / f"{t}_tts.wav"
    roda([PY_TTS, MOTOR / "tts.py", "-t", p["texto"], "-a", voz["referencia"],
          "-o", cru, "--idioma", p.get("idioma", "pt"),
          "--exagero", str(p.get("atuacao", 0.3)),
          "--cfg", str(p.get("adesao", 0.5))])

    destino = TRAB / f"{t}_falado.wav"
    nivelar(cru, destino, str(p.get("lufs", -16)), dose="completo")

    # Se tem modelo treinado, passa por cima pra cravar a identidade da voz.
    if voz["modelo"] and p.get("reforcar", True):
        try:
            reforco = TRAB / f"{t}_falado_rvc.wav"
            roda([PY_RVC, TREINO / "core.py", "infer",
                  "--input-path", destino, "--output-path", reforco,
                  "--pth-path", voz["modelo"], "--index-path", voz["indice"],
                  "--f0-method", "rmvpe", "--protect", "0.15",
                  "--index-rate", "0.5", "--volume-envelope", "1.0"], cwd=TREINO)
            final = TRAB / f"{t}_falado_final.wav"
            nivelar(reforco, final, str(p.get("lufs", -16)), dose="completo")
            return final
        except Exception:
            return destino   # se o reforco falhar, entrega o TTS puro
    return destino


def duracao_de(arquivo):
    """Segundos de AUDIO, via ffprobe. Devolve 0.0 quando nao da pra medir —
    quem chama trata 0 como "nao sei", nunca como "vazio".

    Mede a trilha de audio (a:0) primeiro: num video, format=duration e a
    duracao do CONTAINER (ditada pelo video) — um .mp4 de 60 s com 5 s de voz
    passaria na trava. Formatos que nao declaram duracao no stream (webm/ogg)
    caem no format=duration."""
    fp = _ffprobe()
    if not fp.exists():
        return 0.0
    try:
        r = subprocess.run([str(fp), "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=duration",
                            "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(arquivo)],
                           capture_output=True, text=True,
                           creationflags=SEM_JANELA)
        for linha in r.stdout.splitlines():   # stream vem antes do format
            try:
                return float(linha.strip())
            except ValueError:
                continue                       # "N/A" ou vazio: tenta a proxima
        return 0.0
    except Exception:
        return 0.0


def _amostra_da_voz(p, arquivos):
    """Qual dos arquivos vira a referencia.wav — a "foto" que o Seed-VC olha.

    Ate 24/08 era sempre o mais longo, o que confundiu o Luan com razao: ele
    separou 31 min pro Plexus achando que a amostra usava tudo, e o motor le
    25 s. Longo nao e bom; agora da pra ESCOLHER."""
    escolhido = str(p.get("amostra") or "").strip()
    if escolhido:
        alvo = Path(escolhido)
        for a in arquivos:
            if a == alvo or a.name == alvo.name:
                return a
    return max(arquivos, key=lambda a: a.stat().st_size)


def _fatias_do_dataset(nome):
    """Onde o preprocess do RVC deixou os pedacinhos de 3-4 s.

    O ajuste fino do Seed-VC PRECISA deles: o dataset dele descarta qualquer
    arquivo fora da janela de 1 a 30 s (ft_dataset.py:10-12 e :59), entao
    apontar pro material bruto faria ele pular tudo."""
    base = TREINO / "logs" / nome
    for sub in ("sliced_audios", "sliced_audios_16k"):
        pasta = base / sub
        if pasta.exists() and any(pasta.glob("*.wav")):
            return pasta
    return None


# Janela que o dataset do ajuste fino aceita (ft_dataset.py:10-12).
# Fora dela o arquivo e PULADO — e quando NENHUM sobra, o __getitem__ chama
# a si mesmo num indice aleatorio pra sempre: RecursionError.
FT_MIN_S, FT_MAX_S = 1.0, 30.0
FT_ALVO = 16          # quantos pedacos garantem lote cheio com folga


def _dur_wav(caminho):
    """Duracao de um .wav lendo o cabecalho na mao — sem subprocess. Sao
    centenas de fatias por treino; um ffprobe em cada uma custaria minutos.

    ⚠️ NAO da pra usar o modulo `wave` da stdlib: as fatias do RVC saem em
    float32 (WAVE_FORMAT_IEEE_FLOAT) e ele levanta "unknown format: 3". Se
    isso passar batido, TODA fatia mede 0 s, nenhuma entra na janela de 1-30 s
    e o material bom e descartado — o treino do Plexus inteiro (692 fatias)
    cairia no plano de emergencia. Medido em 24/08."""
    try:
        with open(caminho, "rb") as f:
            if f.read(4) != b"RIFF":
                return 0.0
            f.read(4)
            if f.read(4) != b"WAVE":
                return 0.0
            taxa = canais = bits = 0
            while True:
                cab = f.read(8)
                if len(cab) < 8:
                    return 0.0
                nome = cab[:4]
                tam = int.from_bytes(cab[4:8], "little")
                if nome == b"fmt ":
                    d = f.read(tam)
                    if len(d) >= 16:
                        canais = int.from_bytes(d[2:4], "little")
                        taxa = int.from_bytes(d[4:8], "little")
                        bits = int.from_bytes(d[14:16], "little")
                    if tam % 2:
                        f.seek(1, 1)
                elif nome == b"data":
                    por_amostra = max(1, (bits // 8)) * max(1, canais)
                    return tam / float(taxa * por_amostra) if taxa else 0.0
                else:
                    f.seek(tam + (tam % 2), 1)
    except Exception:
        return 0.0


def _dataset_do_ajuste_fino(t, nome, arquivos, fatias_dir):
    """Monta a pasta que o ajuste fino do Seed-VC vai ler, SO com o que serve.

    Em 24/08 ele treinou 2 s de uma criatura no nivel Boa e levou
    `RecursionError: maximum recursion depth exceeded`. Causa: o corte do RVC
    tira o silencio, entao 2 s de material com pausa viram fatias de menos de
    1 s — e o dataset do Seed-VC descarta TODAS, ficando sem nada pra sortear.
    Aqui a gente filtra antes e, se as fatias nao servirem, usa o proprio
    material (que costuma estar na janela). Se nem isso, estica com ffmpeg."""
    fatias = sorted(fatias_dir.glob("*.wav")) if fatias_dir else []
    bons = [f for f in fatias if FT_MIN_S <= _dur_wav(f) <= FT_MAX_S]

    # CAMINHO FELIZ (material normal): a MAIORIA das fatias serve e sao
    # muitas — usa a pasta original, sem copiar nada. Nao exijo 100% porque
    # sempre sobram fatias curtinhas: o Plexus tem 692 e 56 delas ficam abaixo
    # de 1 s (medido em 24/08). Com a maioria valida, o sorteio do dataset
    # acha uma boa na primeira ou segunda tentativa e nada quebra — a recursao
    # infinita so acontece quando NENHUMA serve.
    if len(bons) >= FT_ALVO and len(bons) * 2 >= len(fatias):
        return fatias_dir

    if not bons:
        for a in arquivos:
            if FT_MIN_S <= duracao_de(a) <= FT_MAX_S:
                bons.append(Path(a))

    destino = TREINO / "datasets" / f"{nome}__ft"
    if destino.exists():
        shutil.rmtree(destino, ignore_errors=True)
    destino.mkdir(parents=True, exist_ok=True)

    if not bons:
        candidatos = [Path(a) for a in arquivos if duracao_de(a) > 0]
        if not candidatos:
            raise RuntimeError(
                "não sobrou nada pra estudar a voz. Use o nível Rápida, "
                "que aceita qualquer tamanho.")
        maior = max(candidatos, key=duracao_de)
        dur = max(duracao_de(maior), 0.05)
        ff = achar_ffmpeg()
        if dur > FT_MAX_S:
            # MATERIAL LONGO sem fatia utilizavel (ex.: o corte do RVC nao
            # rodou ou foi limpo). Esticar seria absurdo aqui — o certo e
            # PICAR em pedacos de 10 s. Sem este ramo, 10 min de voz viravam
            # um pedacinho de 2 s e o ajuste fino estudava quase nada, calado.
            roda([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", maior,
                  "-f", "segment", "-segment_time", "10", "-vn",
                  "-acodec", "pcm_s16le", "-ar", "22050",
                  str(destino / "p%04d.wav")])
            bons = [f for f in sorted(destino.glob("p*.wav"))
                    if FT_MIN_S <= _dur_wav(f) <= FT_MAX_S]
            if not bons:
                raise RuntimeError(
                    "não consegui picar o material em pedaços de 1 a 30 s "
                    "pra estudar a voz.")
            return destino
        # material CURTO: estica ate passar de 1 s
        voltas = int(2.0 / dur) + 1
        esticado = destino / "0000.wav"
        roda([ff, "-y", "-hide_banner", "-loglevel", "error",
              "-stream_loop", str(voltas), "-i", maior, "-t", "2",
              "-vn", "-acodec", "pcm_s16le", "-ar", "22050", esticado])
        if not esticado.exists():
            raise RuntimeError(
                "não consegui montar nem um pedaço de 1 segundo pra estudar "
                "a voz. Use o nível Rápida.")
        bons = [esticado]

    quantos = max(FT_ALVO, len(bons))
    for i in range(quantos):
        origem = bons[i % len(bons)]
        alvo = destino / f"{i:04d}.wav"
        if origem != alvo:
            shutil.copy2(origem, alvo)
    return destino


def _afina_seedvc(nome, fatias, t=None, etapa=None, de=None):
    """AJUSTE FINO DO SEED-VC — a peca que faltava ter botao.

    O motor padrao funciona sem isto (zero-shot, so com a amostra). Isto aqui
    e o "estudar a voz": ~5 min de GPU contra as horas do RVC. Sai um
    ft_model.pth de 412 MB que vira o seedvc.pth da voz."""
    if not PY_SEEDVC.exists():
        raise RuntimeError("o Seed-VC nao esta instalado (motor\\.venv-seedvc)")
    est = _Estimador(SEEDVC_PASSOS)

    def olho(linha):
        # "epoch 0, step 340, loss: ..." -> "passo 341 de 1000 . faltam ~4 min"
        m = _RE_PASSO_SEEDVC.search(linha)
        if m and t:
            feito = min(int(m.group(1)) + 1, SEEDVC_PASSOS)
            falta = est.falta(feito)
            # depois desta etapa so sobra copiar arquivo, entao o numero vale
            # como "quanto falta pra voz ficar pronta" — sem ressalva
            _passo(t, etapa, de,
                   "ajuste fino do Seed-VC \u2014 passo "
                   f"{feito} de {SEEDVC_PASSOS}" + _quanto_falta(falta),
                   fracao=((etapa - 1) + feito / SEEDVC_PASSOS) / de,
                   restante_s=falta)

    roda_vendo([PY_SEEDVC, MOTOR / "seedvc" / "train.py",
                "--config", SEEDVC_CONFIG,
                "--dataset-dir", fatias,
                "--run-name", nome,
                "--max-steps", str(SEEDVC_PASSOS),
                "--save-every", str(SEEDVC_PASSOS),
                "--batch-size", "2",
                "--num-workers", "0"],
               cwd=MOTOR / "seedvc",
               olho=olho if (t and etapa and de) else None)
    pronto = MOTOR / "seedvc" / "runs" / nome / "ft_model.pth"
    if not pronto.exists():
        raise RuntimeError("o ajuste fino terminou mas nao gerou modelo")
    shutil.copy2(pronto, VOZES / nome / "seedvc.pth")


def modo_treinar(t, p):
    """Cria uma voz nova. TRES NIVEIS (24/08), do mais barato ao mais caro:

      rapida  1 etapa   segundos  so a amostra — o motor padrao nao treina
      boa     3 etapas  ~5 min    fatiar + ajuste fino do Seed-VC
      maxima  6 etapas  horas     tudo isso + o ficharo do RVC

    O resultado vira uma pasta em vozes/, pronta para os outros tres modos.
    """
    nome = "".join(c for c in str(p.get("nome", "")).strip()
                   if c.isalnum() or c in " _-").strip()
    if not nome:
        raise RuntimeError("de um nome para a voz")
    arquivos = [Path(a) for a in p.get("arquivos", []) if Path(a).exists()]
    if not arquivos:
        raise RuntimeError("envie pelo menos um audio")

    nivel = str(p.get("nivel", NIVEL_TREINO_PADRAO)).lower().strip()
    if nivel not in NIVEIS_TREINO:
        nivel = NIVEL_TREINO_PADRAO

    # HISTORIA DESTA MEDICAO: nasceu em 15/08 como TRAVA (recusar antes de
    # gastar GPU). Em 24/08 ele pediu o contrario — "e com o que tem, e nao
    # com o minimo" — e a medida virou insumo pra decidir quantas VOLTAS o
    # dataset leva. So mede quando TODOS os arquivos sao mensuraveis: um
    # arquivo que devolve 0 s derrubaria a conta de um material de sobra.
    medidas = [duracao_de(a) for a in arquivos]
    total = sum(medidas)
    medivel = all(m > 0 for m in medidas)

    # COM O QUE TEM (24/08). Nada de recusar por material curto: o "rapida"
    # nao treina e aceita qualquer coisa; os que treinam REPETEM o material
    # ate encher o minimo. So o piso absoluto continua de pe, e por motivo
    # tecnico (fatia menor que 1 s some no fatiador do ajuste fino).
    voltas = 1
    if nivel != "rapida" and medivel:
        if total < MINIMO_ABSOLUTO_S:
            raise RuntimeError(
                f"{total:.1f} s é curto demais até pra repetir: o fatiador "
                f"descarta pedaço com menos de 1 s e não sobraria nada pra "
                f"treinar. Use o nível Rápida, que aceita qualquer tamanho.")
        if total < MINIMO_TREINO_S:
            voltas = min(MAX_VOLTAS, int(MINIMO_TREINO_S / total) + 1)

    def passo(n, total, msg):
        TAREFAS[t] = {"estado": "rodando", "etapa": n, "de": total, "mensagem": msg}

    destino = VOZES / nome
    destino.mkdir(parents=True, exist_ok=True)
    amostra = _amostra_da_voz(p, arquivos)

    # ---------------------------------------------------------- NIVEL RAPIDA
    # Zero GPU. So a amostra — e com ela o motor padrao ja troca a voz, porque
    # ele nao treina nada. Era o caminho que nao existia: pra ter uma voz na
    # lista era obrigatorio passar pelas horas do RVC.
    if nivel == "rapida":
        passo(1, 1, "guardando a amostra…")
        nivelar(amostra, destino / "referencia.wav", "-16", dose="nivelar")
        return destino / "referencia.wav"

    epocas = max(10, min(1000, int(p.get("epocas", 100))))
    # Salvar de pouco em pouco NAO e detalhe: com intervalo grande, parar o treino
    # no meio joga tudo fora. Ja aconteceu.
    salvar_cada = max(5, epocas // 10)
    lote = int(p.get("lote", 8))

    # so os niveis que TREINAM precisam do dataset em disco — copiar 31 min de
    # material pra depois nao usar era so gastar tempo e espaco
    dados = TREINO / "datasets" / nome
    if dados.exists():
        # sobra de um treino anterior com o MESMO nome entraria no dataset
        # junto — e com a repeticao os nomes mudam, entao nem sobrescreve
        shutil.rmtree(dados, ignore_errors=True)
    dados.mkdir(parents=True, exist_ok=True)
    # REPETICAO (ideia dele, 24/08): com pouco material o dataset leva o mesmo
    # audio N vezes, com nomes diferentes. O fatiador trata cada copia como
    # arquivo novo, entao nascem fatias suficientes pra encher lote. A
    # informacao continua sendo a mesma — por isso o aviso vai pro REGISTRO.
    for volta in range(voltas):
        for i, a in enumerate(arquivos, 1):
            shutil.copy2(a, dados / f"{volta:02d}{i:03d}{a.suffix.lower()}")

    de = 6 if nivel == "maxima" else 3
    passo(1, de, "cortando o material em pedaços…"
                 + (f" (repetindo {voltas}× — são só {total:.0f} s de voz)"
                    if voltas > 1 else ""))
    roda([PY_RVC, TREINO / "core.py", "preprocess", "--model-name", nome,
          "--dataset-path", dados, "--sample-rate", "48000", "--cpu-cores", "8"],
         cwd=TREINO)

    fatias_dir = _fatias_do_dataset(nome)
    if not fatias_dir:
        raise RuntimeError("o preparo não gerou pedaço nenhum — confira se os "
                           "arquivos são de áudio (.wav, .mp3, .flac, .ogg)")

    # ------------------------------------------------------------ NIVEL BOA
    # Fatiar + ajuste fino do Seed-VC. Cinco minutos contra as horas do RVC.
    if nivel == "boa":
        passo(2, de, "ajuste fino do Seed-VC — uns 5 minutos…")
        _afina_seedvc(nome, _dataset_do_ajuste_fino(t, nome, arquivos, fatias_dir),
                      t=t, etapa=2, de=de)
        passo(3, de, "guardando a voz na biblioteca…")
        nivelar(amostra, destino / "referencia.wav", "-16", dose="nivelar")
        return destino / "seedvc.pth"

    # --------------------------------------------------------- NIVEL MAXIMA
    passo(2, de, "extraindo entonação e timbre…")
    roda([PY_RVC, TREINO / "core.py", "extract", "--model-name", nome,
          "--f0-method", "rmvpe", "--gpu", "0", "--sample-rate", "48000",
          "--cpu-cores", "8"], cwd=TREINO)

    # Lote maior que o dataset faz o treino SAIR SEM TREINAR (medido em 15/08:
    # 8 s de material viram 3 fatias; com lote 8 o loop nao da um passo e nao
    # nasce checkpoint nenhum). Encolhe o lote pra caber no material.
    fatias = len(list((TREINO / "logs" / nome / "sliced_audios_16k").glob("*.wav")))
    if fatias:
        lote = max(1, min(lote, fatias))

    passo(3, de, f"treinando {epocas} épocas — esta é a parte demorada")

    est_ep = _Estimador(epocas)

    def olho_epoca(linha):
        # "Nome | epoch=12 | step=980 | ..." -> "epoca 12 de 600".
        # Sem isto a barra ficava parada por HORAS no nivel Maxima.
        m = _RE_EPOCA_RVC.search(linha)
        if m:
            feito = min(int(m.group(1)), epocas)
            falta = est_ep.falta(feito)
            # "NESTA ETAPA" nao e enfeite: depois das epocas ainda vem o
            # ajuste fino do Seed-VC. Dizer "faltam 20 min" seco seria mentira
            _passo(t, 3, de,
                   f"treinando \u2014 \u00e9poca {feito} de {epocas}"
                   + _quanto_falta(falta, etapa_so=True),
                   fracao=(2 + feito / epocas) / de,
                   restante_s=falta)

    roda_vendo([PY_RVC, TREINO / "core.py", "train", "--model-name", nome,
                "--total-epoch", str(epocas), "--batch-size", str(lote),
                "--sample-rate", "48000", "--save-every-epoch", str(salvar_cada),
                "--save-every-weights", "--gpu", "0"],
               cwd=TREINO, olho=olho_epoca)

    passo(4, de, "guardando o fichário da voz…")
    registro = TREINO / "logs" / nome
    modelo = achar_modelo(registro)
    if not modelo:
        raise RuntimeError("o treino terminou mas nao gerou modelo utilizavel")

    indice = next(iter(registro.glob("*.index")), None)
    destino = VOZES / nome
    destino.mkdir(parents=True, exist_ok=True)
    shutil.copy2(modelo, destino / "modelo.pth")
    if indice:
        shutil.copy2(indice, destino / "indice.index")
    passo(5, de, "ajuste fino do Seed-VC — uns 5 minutos…")
    _afina_seedvc(nome, _dataset_do_ajuste_fino(t, nome, arquivos, fatias_dir),
                  t=t, etapa=5, de=de)

    # A amostra serve pro Seed-VC e pro modo Falar. Nao e mais "o mais longo"
    # por decreto: quem manda e o p["amostra"] quando ele escolhe.
    passo(6, de, "guardando a voz na biblioteca…")
    nivelar(amostra, destino / "referencia.wav", "-16", dose="nivelar")

    return destino / "modelo.pth"


def achar_modelo(pasta):
    """O .pth de inferencia (uns 55 MB). Se o treino nao salvou  acontece quando
    e retomado , extrai do checkpoint G_*.pth, que tem centenas de MB."""
    pequenos = [f for f in pasta.glob("*.pth")
                if not f.name.startswith(("G_", "D_"))]
    if pequenos:
        return max(pequenos, key=lambda f: f.stat().st_mtime)

    grandes = sorted(pasta.glob("G_*.pth"), key=lambda f: f.stat().st_mtime)
    if not grandes:
        return None
    extraido = pasta / f"{pasta.name}.pth"
    try:
        roda([PY_RVC, TREINO / "extrair_modelo.py", grandes[-1], extraido],
             cwd=TREINO)
        return extraido if extraido.exists() else None
    except Exception:
        return None


def cortar_no_tempo(arquivo, referencia):
    """A troca de voz devolve alguns milissegundos a mais. Num video isso e meio
    quadro, e acumula. Corta de volta no tempo exato da entrada."""
    ff = achar_ffmpeg()
    fp = _ffprobe()
    if not ff.exists() or not fp.exists():
        return arquivo
    try:
        dur = subprocess.run([str(fp), "-v", "error", "-show_entries",
                              "format=duration", "-of", "csv=p=0", str(referencia)],
                             capture_output=True, text=True,
                             creationflags=SEM_JANELA).stdout.strip()
        cortado = arquivo.with_name(arquivo.stem + "_cortado.wav")
        subprocess.run([str(ff), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(arquivo), "-t", dur, "-c:a", "pcm_s24le",
                        str(cortado)], creationflags=SEM_JANELA, check=True)
        return cortado if cortado.exists() else arquivo
    except Exception:
        return arquivo


def achar_ffmpeg():
    fixo = Path(r"C:\Users\Luan\AppData\Roaming\Adobe\CEP\extensions"
                r"\com.luan.beatmatch\bin\win\ffmpeg.exe")
    if fixo.exists():
        return fixo
    achado = shutil.which("ffmpeg")
    return Path(achado) if achado else fixo


# ------------------------------------------------------------- ambientes
# Presets DSP prontos (ambientes/presets/*.json) — reverb por convolucao com
# IR sintetica (ambientes/irs, gerados por gerar_irs.py) + EQ + compressao,
# tudo em escala CONHECIDA (Hz, dB, segundos). Decisao de 15/08 (noite): o
# Luan nao configura efeito nenhum; ambientes vem prontos e se ajustam por
# conversa (editar o JSON). Substitui o desenho "capturar efeitos do Premiere"
# — aquele exigia alguem configurando a cadeia na mao dentro do Premiere.
AMBIENTES = BASE / "ambientes"


def listar_ambientes():
    """Dois tipos convivem no dropdown do painel:
    - "dsp": presets prontos em ambientes/presets/ (o servidor processa o som);
    - "efeito": cadeias capturadas de um clipe do Premiere pelo botao Salvar do
      clipe (JSONs na raiz de ambientes/, campo "efeitos") — o PAINEL aplica
      via API UXP, nao-destrutivo. E o caminho pros presets de fabrica da
      Adobe: aplicar o preset num clipe (2 cliques) e capturar uma vez."""
    saida = []
    pasta = AMBIENTES / "presets"
    if pasta.exists():
        for arq in sorted(pasta.glob("*.json")):
            try:
                d = json.loads(arq.read_text(encoding="utf-8"))
                if d.get("nome"):
                    d["tipo"] = "dsp"
                    saida.append(d)
            except Exception:
                continue   # JSON quebrado nao derruba a lista inteira
    if AMBIENTES.exists():
        for arq in sorted(AMBIENTES.glob("*.json")):
            try:
                d = json.loads(arq.read_text(encoding="utf-8"))
                if d.get("efeitos"):
                    d["tipo"] = "efeito"
                    d.setdefault("nome", arq.stem)
                    saida.append(d)
            except Exception:
                continue
    return saida


def medir_lufs(arquivo):
    """LUFS integrado, medido pelo proprio loudnorm. None quando nao da.

    NAO-FINITO E "NAO DA": ja aconteceu (15/08) de um filtro devolver amostras
    inf por lixo de memoria — o loudnorm reporta input_i "-inf", e -inf tratado
    como numero virou volume=infdB no renivelamento, destruindo o arquivo.
    None obriga quem chama a tratar como desconhecido."""
    ff = achar_ffmpeg()
    try:
        r = subprocess.run([str(ff), "-hide_banner", "-i", str(arquivo),
                            "-af", "loudnorm=print_format=json",
                            "-f", "null", "-"],
                           capture_output=True, text=True, errors="replace",
                           creationflags=SEM_JANELA)
        bruto = r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1]
        valor = float(json.loads(bruto)["input_i"])
        return valor if math.isfinite(valor) else None
    except Exception:
        return None


def medir_pico(arquivo, segundos=None):
    """Pico de amostra (sample peak) em dBFS. None quando nao da — arquivo mudo (-inf) tambem e None.

    astats e nao volumedetect: os dois batem (conferido 29/10 nos wavs do e3b56de5d5), mas o astats le o float
    direto — pico ACIMA de 0 dBFS aparece como +x em vez de travar em 0 — e da as casas que o volumedetect
    arredonda pra 0,1. `segundos` mede so o comeco: a troca devolve uns ms a mais que o cortar_no_tempo apara."""
    ff = achar_ffmpeg()
    try:
        cmd = [str(ff), "-hide_banner", "-i", str(arquivo)]
        if segundos:
            cmd += ["-t", f"{float(segundos):.3f}"]
        r = subprocess.run(cmd + ["-af", "astats", "-f", "null", "-"],
                           capture_output=True, text=True, errors="replace",
                           creationflags=SEM_JANELA)
        achados = re.findall(r"Peak level dB:\s*(\S+)", r.stderr)
        valor = float(achados[-1])       # o ultimo e o "Overall" (os de cima sao por canal)
        return valor if math.isfinite(valor) else None
    except Exception:
        return None


# PICO IGUAL AO ORIGINAL (P32, Kakashi 29/10 17:53 «a original: amplitude de pico −1,5; a gerada: amplitude de pico 0,0» e
# 18:08 «o pico desse aqui igual ao amarelo que tá normal... automático»). O trocar nivela a voz pra um LUFS (−16) e o
# arquivo saia com pico perto de 0 dBFS — a onda ficava mais alta que a do clipe original e o volume que ele ja tinha
# acertado na timeline deixava de valer. Passo final comum (RVC, Seed-VC, ElevenLabs, com ou sem «manter ambiente»):
# ganho ESTATICO (volume=XdB, sem limiter) pro pico da saida = pico do trecho de entrada; teto −0,1 dBFS, nunca clipa.
PICO_TETO = -0.1


def _num_br(v):
    return f"{v:+.1f}".replace("-", "−").replace(".", ",").replace("+0,0", "0,0").replace("−0,0", "0,0")


# GANHO DE CLIPE (Kakashi 29/10 21:24 [59:20] «você me prometeu que ia colocar do dubx aqui com o volume acertado e você não fez
# isso»): o clipe ORIGINAL pode ter ganho de clipe (tecla G: −1,5 dB no J5b, +6,4 no SEED_F1_4m17), que a API do Premiere NAO
# entrega a plugin — o pico do arquivo casava, mas o clipe novo nascia sem o ganho e soava diferente. O ganho mora no .prproj SALVO
# (<Gain> no AudioClip; leitor em apps\REGUA\scripts\prproj_ganho.py): o painel manda onde o clipe esta (origem_clip) e o ganho
# entra no arquivo novo. Projeto com mudança não salva = vale o ganho do último save.
_GANHOS_CACHE = {}


def _ganho_do_clipe(oc):
    """dB do ganho de clipe do original (0.0 sem ganho), ou None se nao deu pra ler."""
    if not isinstance(oc, dict) or not oc.get("projeto") or not oc.get("faixa"):
        return None
    proj = Path(str(oc["projeto"]).replace("\\\\?\\", ""))
    try:
        chave = (str(proj), proj.stat().st_mtime)
    except OSError:
        return None
    if chave not in _GANHOS_CACHE:
        try:
            sys.path.insert(0, str(BASE.parent / "REGUA" / "scripts"))
            from prproj_ganho import ler_ganhos
            _GANHOS_CACHE.clear()
            _GANHOS_CACHE[chave] = ler_ganhos(str(proj))
        except Exception:
            return None
    faixa = "A%s" % int(oc["faixa"])
    ini = float(oc.get("inicio_s") or 0)
    seq = str(oc.get("sequencia") or "")
    for g in _GANHOS_CACHE[chave]:
        if g["faixa"] == faixa and abs(g["inicio_s"] - ini) < 0.05 and (not seq or g["sequencia"] == seq):
            return float(g["ganho_db"])
    return 0.0


def _casar_pico(t, entrada, saida, p):
    """Devolve o wav com o pico casado com o da `entrada` (ou a propria `saida`, se desligado/imedivel).

    Roda ANTES do ultimo cortar_no_tempo de proposito: ele grava pcm_s24le, e inteiro trava acima de 0 dBFS — a
    remontagem em float pode passar de 0 e, depois do corte, o pico ja estaria achatado em 0,0 (o que ele viu)."""
    if not _el_bool(p.get("casar_pico"), True):
        return saida
    saida = Path(saida)
    alvo = medir_pico(entrada)
    antes = medir_pico(saida, duracao_de(entrada) or None)
    if alvo is None or antes is None:
        AVISOS.setdefault(t, []).append("não deu pra medir o pico — o arquivo saiu sem casar o pico com o original")
        return saida
    g_clipe = _ganho_do_clipe(p.get("origem_clip"))
    if g_clipe is None:
        g_clipe = 0.0
        if p.get("origem_clip"):
            AVISOS.setdefault(t, []).append("não consegui ler o ganho de clipe do original no projeto salvo — confira o volume")
    desejado = alvo + g_clipe          # o que o original SOA na timeline (pico do arquivo + ganho do clipe)
    final = min(desejado, PICO_TETO)
    ganho = final - antes
    PICO_DA_TAREFA[t] = {"pico_entrada": round(alvo, 2), "pico_saida_antes": round(antes, 2),
                         "ganho_aplicado": round(ganho, 2), "ganho_clipe_original": round(g_clipe, 2),
                         "falta_no_clipe": round(max(0.0, desejado - PICO_TETO), 2)}
    if abs(ganho) < 0.005:
        return saida
    casado = TRAB / f"{t}_pico.wav"
    roda([achar_ffmpeg(), "-y", "-i", saida, "-af", f"volume={ganho:.3f}dB",
          "-c:a", "pcm_f32le", casado])
    return casado


def _recado_pico(info):
    """Linha pra TAREFAS[t]["mensagem"]: «pico igual ao original (−1,5 dB)»."""
    if not info:
        return None
    falta = info.get("falta_no_clipe") or 0
    if falta > 0.05:
        return (f"ganho do clipe original ({_num_br(info.get('ganho_clipe_original', 0))} dB) não cabe no arquivo sem clipar: "
                f"ponha {_num_br(falta)} dB de ganho (G) no clipe novo")
    if abs(info.get("ganho_clipe_original") or 0) > 0.05:
        return f"volume igual ao original (pico {_num_br(info['pico_entrada'])} dB + ganho do clipe {_num_br(info['ganho_clipe_original'])} dB)"
    if info["pico_entrada"] > PICO_TETO:
        return (f"pico em {_num_br(PICO_TETO)} dB (o original tem {_num_br(info['pico_entrada'])} dB; "
                "teto pra não clipar)")
    return f"pico igual ao original ({_num_br(info['pico_entrada'])} dB)"


# Modelo local de visao pro ambiente automatico (o combinado original da
# Fase 3: analisar o frame do video decide o ambiente). Validado em 15/08:
# qwen3.5:9b do Ollama LE imagem. Carrega ~7 GB de VRAM sob demanda — vale a
# mesma regra do treino: Ollama ocupado atrapalha treinar.
OLLAMA_MODELO = "qwen3.5:9b"


def classificar_ambiente(video, tempo):
    """Extrai um frame do video e pergunta ao modelo local em que LUGAR a
    cena acontece. Devolve (nome do preset com "cena": true, resposta crua)
    — nome None significa exterior/nada se aplica (fica seco)."""
    ff = achar_ffmpeg()
    frame = TRAB / f"frame_{uuid.uuid4().hex[:8]}.jpg"
    roda([ff, "-y", "-ss", f"{float(tempo):.3f}", "-i", video,
          "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "5", frame])
    b64 = base64.b64encode(frame.read_bytes()).decode("ascii")

    opcoes = [a["nome"] for a in listar_ambientes()
              if a.get("tipo") == "dsp" and a.get("cena")]
    prompt = ("Este é um frame de um vídeo. Em que tipo de LUGAR a cena "
              "acontece? Escolha exatamente UMA opção e responda SÓ com ela, "
              "sem explicar: " + " | ".join(opcoes) +
              " | Nenhum (exterior, céu aberto, ou nenhuma das opções serve)")
    corpo = json.dumps({"model": OLLAMA_MODELO, "prompt": prompt,
                        "images": [b64], "stream": False}).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                                 data=corpo,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            texto = json.loads(resp.read().decode("utf-8")).get("response", "")
    except Exception as exc:
        raise RuntimeError(f"Ollama não respondeu ({exc}) — ele está aberto?")
    finally:
        # o frame era uma foto por chamada acumulando em trabalho\ pra sempre
        frame.unlink(missing_ok=True)

    texto = texto.strip()
    if not texto:
        # ⚠️ NAO devolver None aqui: None quer dizer "exterior, deixa seco", e
        # a tela mostraria isso como se fosse um veredito. Resposta vazia e
        # FALHA do modelo, coisa diferente. Visto ao vivo em 23/08.
        raise RuntimeError("o modelo olhou o frame e não respondeu nada — "
                           "tente de novo, ou escolha o ambiente na mão")

    escolhido = next((n for n in opcoes if n.lower() in texto.lower()), None)
    return escolhido, texto


def _ambientar_wav(t, fonte, preset, intensidade):
    """Aplica um ambiente num wav e devolve outro com a MESMA duracao e o
    MESMO LUFS da entrada.

    A licao mais cara de 14/08 vale aqui dobrado: comparacao de audio exige
    volume igualado. Ambientar mexe no loudness, entao o resultado volta pro
    LUFS de entrada com ganho ESTATICO (nao loudnorm dinamico, que mudaria a
    dinamica do efeito). A cauda do reverb passa do fim: cortar_no_tempo
    apara de volta na duracao exata — sincronia e sagrada."""
    ff = achar_ffmpeg()
    lufs_base = medir_lufs(fonte)

    pre = (preset.get("pre") or "").strip()
    pos = (preset.get("pos") or "").strip()
    wet = float(preset.get("wet") or 0)
    ir = (preset.get("ir") or "").strip()
    ir_arq = AMBIENTES / "irs" / ir if ir else None
    # Arquivo ESTEREO: cada canal convolui com uma IR independente
    # (channelsplit -> afir x2 -> join) — decorrelacao que soa como sala de
    # verdade. IR estereo num arquivo so NAO funciona: o afir desta build
    # ignora o segundo canal (medido em 15/08, L-R -54 dB). Mono/outros usam
    # uma IR so — e o arquivo NUNCA muda de contagem de canais.
    ir_r = None
    if ir_arq is not None:
        try:
            r = subprocess.run([str(_ffprobe()),
                                "-v", "error", "-select_streams", "a:0",
                                "-show_entries", "stream=channels",
                                "-of", "csv=p=0", str(fonte)],
                               capture_output=True, text=True,
                               creationflags=SEM_JANELA)
            candidata = ir_arq.with_name(ir_arq.stem + "_R.wav")
            if int(r.stdout.strip()) == 2 and candidata.exists():
                ir_r = candidata
        except Exception:
            pass   # nao deu pra saber: uma IR so serve pra qualquer contagem

    proc = TRAB / f"{t}_amb_proc.wav"
    if ir and wet > 0:
        if not ir_arq.exists():
            raise RuntimeError(f"IR '{ir}' nao existe — rode ambientes\\gerar_irs.py")
        # irnorm=-1 e OBRIGATORIO: a normalizacao default (gtype=peak) esmaga
        # IR de ruido em ~32 dB e o reverb fica inaudivel (medido em 15/08 —
        # era por isso que "nao tinha diferenca nenhuma"). Com -1 vale a
        # calibracao de energia das IRs do gerar_irs.py.
        etapas = [f"[0:a]{pre + ',' if pre else ''}asplit=2[d][tw]"]
        entradas = [ff, "-y", "-i", fonte, "-i", ir_arq]
        if ir_r is not None:
            entradas += ["-i", ir_r]
            etapas += ["[tw]channelsplit=channel_layout=stereo[cl][cr]",
                       "[cl][1:a]afir=irnorm=-1[wl]",
                       "[cr][2:a]afir=irnorm=-1[wr]",
                       "[wl][wr]join=inputs=2:channel_layout=stereo[w0]"]
        else:
            etapas.append("[tw][1:a]afir=irnorm=-1[w0]")
        etapas += [f"[w0]volume={wet:.4f}[w]",
                   "[d][w]amix=inputs=2:duration=first:normalize=0[m]"]
        rot = "[m]"
        if pos:
            etapas.append(f"[m]{pos}[o]")
            rot = "[o]"
        roda(entradas + ["-filter_complex", ";".join(etapas), "-map", rot,
                         "-ar", "48000", "-c:a", "pcm_f32le", proc])
    else:
        cadeia = ",".join(x for x in (pre, pos) if x) or "anull"
        roda([ff, "-y", "-i", fonte, "-af", cadeia,
              "-ar", "48000", "-c:a", "pcm_f32le", proc])

    # intensidade < 100: mistura o processado de volta com o som seco
    a = max(0, min(100, int(intensidade or 100))) / 100.0
    if a < 1.0:
        mix = TRAB / f"{t}_amb_mix.wav"
        roda([ff, "-y", "-i", fonte, "-i", proc, "-filter_complex",
              f"[0:a][1:a]amix=inputs=2:duration=first:normalize=0"
              f":weights={1 - a:.3f} {a:.3f}[m]",
              "-map", "[m]", "-ar", "48000", "-c:a", "pcm_f32le", mix])
        proc = mix

    if lufs_base is not None:
        lufs_novo = medir_lufs(proc)
        if lufs_novo is not None:
            # teto no ganho: correcao honesta fica em poucos dB; +30 dB e
            # sintoma de medicao envenenada, nao de ambiente
            delta = max(-20.0, min(20.0, lufs_base - lufs_novo))
            niv = TRAB / f"{t}_amb_niv.wav"
            roda([ff, "-y", "-i", proc, "-af", f"volume={delta:.2f}dB",
                  "-ar", "48000", "-c:a", "pcm_f32le", niv])
            proc = niv

    final = cortar_no_tempo(proc, fonte)
    # Porteiro de sanidade: este wav vai ser REGRAVADO no arquivo do Luan.
    # Saida imedivel (inf/NaN — ja aconteceu por lixo de memoria em filtro)
    # e erro na cara, nunca midia corrompida em silencio.
    if medir_lufs(final) is None:
        raise RuntimeError(
            f"a saída do ambiente '{preset.get('nome')}' veio corrompida "
            f"(amostras não-finitas) — rode de novo; o arquivo original não foi tocado")
    return final


# ------------------------------------------------- painel Premiere (no lugar)
SO_AUDIO = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
COM_VIDEO = {".mp4", ".mov", ".mxf", ".mkv", ".avi"}


class _Estimador:
    """Quanto falta, medido no ritmo do proprio treino (25/08).

    Pedido dele: "um tempo restante seria maneiro - faltam aproximadamente x
    minutos, x horas". Nada de chute: o roda_vendo() entrega os passos reais,
    entao aqui e so regra de tres.

    O PRIMEIRO PASSO MENTE. Ele paga carregamento de modelo, alocacao de VRAM
    e aquecimento da GPU — usar ele como referencia dava "faltam 3 horas" num
    treino de 5 minutos. Por isso a ancora e o primeiro passo VISTO (nao o
    zero), e a resposta so sai depois de 2 passos e 5 segundos de medicao.
    """

    def __init__(self, total):
        self.total = max(1, int(total))
        self.t0 = None
        self.n0 = 0

    def falta(self, feito):
        agora = time.monotonic()
        if self.t0 is None or feito <= self.n0:
            # primeira noticia, ou o contador voltou (retomou de checkpoint):
            # re-ancora e nao arrisca palpite
            self.t0, self.n0 = agora, feito
            return None
        gasto = agora - self.t0
        andou = feito - self.n0
        if andou < 2 or gasto < 5:
            return None
        return max(0.0, (self.total - feito) * (gasto / andou))


def _quanto_falta(seg, etapa_so=False):
    """Vira o pedacinho de texto que vai colado na mensagem. Sem numero
    falso: se o estimador ainda nao sabe, nao escreve nada."""
    if seg is None:
        return ""
    seg = int(seg)
    fim = " nesta etapa" if etapa_so else ""
    if seg < 45:
        return " \u00b7 falta menos de 1 min" + fim
    minutos = int(round(seg / 60.0))
    if minutos < 60:
        return f" \u00b7 faltam ~{minutos} min" + fim
    h, m = divmod(minutos, 60)
    return f" \u00b7 faltam ~{h}h{m:02d}" + fim


def _passo(t, n, de, msg, fracao=None, restante_s=None):
    e = {"estado": "rodando", "etapa": n, "de": de, "mensagem": msg}
    # `fracao` e o progresso REAL de 0 a 1 (o passo dentro da etapa). Sem ela
    # a barra so sabe "etapa 2 de 3" e fica cravada em 66% por 11 minutos.
    if fracao is not None:
        e["fracao"] = round(max(0.0, min(1.0, fracao)), 4)
    if restante_s is not None:
        e["restante_s"] = int(max(0, restante_s))
    TAREFAS[t] = e


def _guarda_original(t, arquivo):
    """Copia integral em trabalho\\originais ANTES de tocar no arquivo.
    E o desfazer do painel; nunca pular esta etapa."""
    backups = TRAB / "originais"
    backups.mkdir(parents=True, exist_ok=True)
    shutil.copy2(arquivo, backups / f"{t}_{arquivo.name}")


def _audio_de(t, arquivo, rotulo=""):
    """Devolve um wav 48k com o audio do arquivo (extrai se for video).

    Guarda o caminho em AUDIO_FONTE: e ele que vira o "antes" do comparador
    A/B (de video nao da pra tocar so a trilha sem baixar o video inteiro)."""
    if arquivo.suffix.lower() in COM_VIDEO:
        ff = achar_ffmpeg()
        cru = TRAB / f"{t}{rotulo}_extraido.wav"
        roda([ff, "-y", "-i", arquivo, "-vn", "-acodec", "pcm_f32le",
              "-ar", "48000", cru])
        if duracao_de(cru) <= 0:
            raise RuntimeError("esse vídeo não tem trilha de áudio")
        AUDIO_FONTE.setdefault(t, str(cru))
        return cru
    AUDIO_FONTE.setdefault(t, str(arquivo))
    return arquivo


def _cortar_trecho(t, arquivo, ini, fim):
    """So o pedaco que o Luan marcou (pedido de 16/08: "arquivo muito grande,
    mas eu quero so um trecho especifico").

    Video: `-ss` antes do `-i` + `-c copy` — corte instantaneo e SEM
    recodificar, mas ele encosta no keyframe anterior (ate ~1 s a mais no
    comeco, conforme o GOP). Como o audio e extraido DEPOIS, do proprio
    trecho, imagem e som continuam alinhados. Audio: corte exato."""
    arquivo = Path(arquivo)
    ff = achar_ffmpeg()
    ini, fim = max(0.0, float(ini)), float(fim)
    if fim <= ini:
        raise RuntimeError("o trecho tem o fim antes do começo")
    dur = fim - ini
    if arquivo.suffix.lower() in COM_VIDEO:
        destino = TRAB / f"{t}_trecho{arquivo.suffix.lower()}"
        roda([ff, "-y", "-ss", f"{ini:.3f}", "-t", f"{dur:.3f}", "-i", arquivo,
              "-c", "copy", destino])
    else:
        destino = TRAB / f"{t}_trecho.wav"
        roda([ff, "-y", "-ss", f"{ini:.3f}", "-t", f"{dur:.3f}", "-i", arquivo,
              "-c:a", "pcm_f32le", "-ar", "48000", destino])
    if duracao_de(destino) <= 0:
        raise RuntimeError("o trecho saiu vazio — confira o começo e o fim")
    return destino


def _com_video(t, entrada, novo_wav, rotulo=""):
    """Entrada era VIDEO? Devolve um video NOVO com o audio trocado (pedido do
    Luan, 16/08: "tacar um video... mesma qualidade de entrada, so com audio
    bem melhor"). None quando a entrada nao e video.

    O video passa com `-c:v copy` — copia bit a bit, ZERO recodificacao, sem
    perda nenhuma. Sem `-shortest` de proposito: ele aparava o video pra caber
    no audio (38 ms medidos em 15/08) e sincronia e sagrada."""
    entrada = Path(str(entrada))
    if entrada.suffix.lower() not in COM_VIDEO or not entrada.exists():
        return None
    ff = achar_ffmpeg()
    destino = TRAB / f"{t}{rotulo}_final{entrada.suffix.lower()}"
    roda([ff, "-y", "-i", entrada, "-i", novo_wav,
          "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
          "-c:a", "aac", "-b:a", "320k", destino])
    return destino


def _regrava_no_lugar(t, arquivo, novo_wav):
    """Poe o novo audio DENTRO do arquivo original (mesmo caminho e container).
    Video nao e reprocessado: remux com -c:v copy. SEM -shortest — ele apara o
    video pra caber no audio (que perde uns ms no encode AAC) e 38 ms a menos
    ja e risco de sincronia (medido); o video intacto e quem dita a duracao."""
    ext = arquivo.suffix.lower()
    if ext in {".wav", ".aif", ".aiff"}:
        shutil.copy2(novo_wav, arquivo)
    elif ext in SO_AUDIO:
        ff = achar_ffmpeg()
        recod = TRAB / f"{t}_recod{ext}"
        roda([ff, "-y", "-i", novo_wav, recod])
        shutil.copy2(recod, arquivo)
    elif ext in COM_VIDEO:
        ff = achar_ffmpeg()
        remux = TRAB / f"{t}_remux{ext}"
        roda([ff, "-y", "-i", arquivo, "-i", novo_wav,
              "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
              "-c:a", "aac", "-b:a", "320k", remux])
        shutil.copy2(remux, arquivo)
    else:
        raise RuntimeError(f"extensao nao suportada: {ext}")


def modo_melhorar_no_lugar(t, p):
    """Fase 1 do painel Premiere: melhora o audio do arquivo NO LUGAR.
    O Premiere continua apontando pro mesmo caminho — o painel so chama
    refreshMedia() no fim."""
    arquivo = Path(p["arquivo"])
    if not arquivo.exists():
        raise RuntimeError(f"arquivo nao existe: {arquivo}")
    _guarda_original(t, arquivo)

    _passo(t, 1, 3, "extraindo o áudio…")
    fonte = _audio_de(t, arquivo)
    _passo(t, 2, 3, "melhorando…", fracao=0.03)
    melhorado = TRAB / f"{t}_melhorado.wav"
    _melhora_vendo(t, 2, 3, fonte, melhorado, int(p.get("dose", 100)),
                   p.get("lufs", -16), base=0.03, peso=0.92)
    _passo(t, 3, 3, "regravando o arquivo…", fracao=0.96)
    _regrava_no_lugar(t, arquivo, melhorado)
    return arquivo


def modo_trocar_no_lugar(t, p):
    """Painel Premiere: troca a voz do arquivo NO LUGAR. O miolo e o
    modo_trocar de sempre (RVC treinado + Sidon + nivelar + corte no tempo)."""
    arquivo = Path(p["arquivo"])
    if not arquivo.exists():
        raise RuntimeError(f"arquivo nao existe: {arquivo}")
    _guarda_original(t, arquivo)

    fonte = _audio_de(t, arquivo)
    p2 = dict(p)
    p2["arquivo"] = str(fonte)
    if not p.get("preservar"):
        # com "preservar" o proprio _trocar_preservando narra as 5 etapas
        _passo(t, 1, 2, f"trocando a voz para {p.get('voz', '?')}…")
    trocado = modo_trocar(t, p2)
    _passo(t, 2, 2, "regravando o arquivo…")
    _regrava_no_lugar(t, arquivo, trocado)
    return arquivo


# ------------------------------------------------------------------ dublagem
# Estilo Eleven Labs, 100% local (fila de estudo de 15/08, construida 16/08):
# a voz do resultado e CLONADA do proprio arquivo (zero-shot, sem treino).
_WHISPER_FIXO = Path(r"C:\Users\Luan\AppData\Roaming\open-whispr\bin"
                     r"\whisper-server-win32-x64-cuda.exe")
# ordem: variavel de ambiente > o do OpenWhispr do Luan (se existir) > PATH > o fixo (pra mensagem de erro)
WHISPER_BIN = Path(os.environ.get("DUBX_WHISPER_BIN")
                   or (str(_WHISPER_FIXO) if _WHISPER_FIXO.exists() else None)
                   or shutil.which("whisper-server") or str(_WHISPER_FIXO))
# LARGE, nao turbo — decisao dele em 23/08, MEDIDA no proprio arquivo dele:
# o turbo agrupou errado ("...mundo material" / "IRMAO, agora tu esta...") e
# marcou a 2a frase em 2,14 s; o large agrupou certo ("...mundo material,
# irmao." / "Agora tu ta...") e marcou 2,80 s. A fala real comeca em 1,92 e
# 3,70 — ou seja, o turbo jogava a 2a frase da dublagem ~900 ms adiantada,
# que e a "segunda frase precipitada" que ele ouviu. Custo: 0,4 s contra
# 0,2 s num clipe de 4,5 s — irrelevante perto dos minutos da dublagem.
# ⚠️ ACOPLAMENTO: se o OpenWhispr estiver ABERTO (porta 8178) o DUBX usa o
# servidor DELE, e o modelo passa a ser o que estiver escolhido LA. Este
# caminho aqui so vale quando o OpenWhispr esta fechado.
WHISPER_MODELO = Path(os.environ.get("DUBX_WHISPER_MODELO")
                      or (r"C:\Users\Luan\.cache\openwhispr\whisper-models"
                          r"\ggml-large-v3.bin"))
_CURL_WIN = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
# Windows: o curl do sistema, como sempre. Fora: o que estiver no PATH.
CURL = _CURL_WIN if (WIN and _CURL_WIN.exists()) else Path(shutil.which("curl") or _CURL_WIN)
PORTA_OPENWHISPR, PORTA_WHISPER_PROPRIA = 8178, 8899


def _porta_viva(porta):
    import socket
    try:
        with socket.create_connection(("127.0.0.1", porta), timeout=0.6):
            return True
    except OSError:
        return False


TANDERA = r"C:\Users\Luan\CLAUDE\apps\TANDERA"


def transcrever_srt(t, fonte, destino, idioma, max_len=70):
    """01/11/2026 (ele: «nao deveriam ser coisas separadas ja que o Tandera e o
    motor centralizado» + «ganha o modelo large»): a fala vem do TANDERA. Com o
    `whisper_proprio` ligado (padrao) = o MESMO large-v3 de 23/08 na 8899 (o
    Tandera sobe e desce o servidor); desligado = a carona no 8178 do ditado.
    Por cima, as regras do Tandera: so os trechos com som, frase inventada fora,
    laco. Medido no 4a20f606fd_trecho (TANDERA\\trabalho\\prova_large_dubx_
    2026-11-01.txt): legenda de 70 = 9/9 blocos iguais ao jeito antigo; palavra
    por palavra = 34/36 iguais (2 com ~0,09 s de diferenca). Se o Tandera falhar
    ou voltar vazio, o jeito antigo (abaixo) roda."""
    try:
        if TANDERA not in sys.path:
            sys.path.insert(0, TANDERA)
        import tandera
        proprio = bool(_config_le().get("whisper_proprio", True))
        tandera.transcrever(str(fonte), idioma=idioma, dispositivo="cuda", rapido=True,
                            modelo="large" if proprio else "turbo", srt=str(destino), max_len=max_len)
        if Path(destino).exists() and Path(destino).read_text(encoding="utf-8-sig", errors="ignore").strip():
            return
        print("fala: o Tandera voltou vazio; vai pelo jeito antigo")
    except Exception as e:      # noqa: BLE001
        print(f"fala: o Tandera nao transcreveu ({e}); vai pelo jeito antigo")
    _transcrever_srt_direto(t, fonte, destino, idioma, max_len)


def _transcrever_srt_direto(t, fonte, destino, idioma, max_len=70):
    """(RESERVA desde 01/11) Mesma receita do TANDERA (as armadilhas ja foram pagas la), com uma
    diferenca de 23/08: por padrao o DUBX sobe o PROPRIO Whisper na 8899, com
    o modelo grande, em vez de pegar carona no OpenWhispr da 8178.

    Motivo (pedido dele): ele quer o DITADO rapido (turbo no OpenWhispr) e a
    DUBLAGEM boa (large aqui). Pegando carona, o DUBX herdava o modelo do
    OpenWhispr sem avisar — e o turbo agrupa frase errado, o que jogava a
    dublagem fora do tempo (medido em 23/08).

    Quem preferir a carona (mais rapido, sem recarregar modelo) desliga o
    `whisper_proprio` na tela.

    O modelo turbo NAO tem task translate — transcreve no idioma ORIGINAL;
    traduzir e trabalho do qwen."""
    import time
    ff = achar_ffmpeg()
    wav16 = TRAB / f"{t}_whisper16k.wav"
    roda([ff, "-y", "-i", fonte, "-ar", "16000", "-ac", "1",
          "-c:a", "pcm_s16le", wav16])
    proprio = bool(_config_le().get("whisper_proprio", True))
    porta, meu = (PORTA_WHISPER_PROPRIA if proprio else PORTA_OPENWHISPR), None
    if not _porta_viva(porta):
        porta = PORTA_WHISPER_PROPRIA
        if not _porta_viva(porta):
            meu = subprocess.Popen(
                [str(WHISPER_BIN), "--model", str(WHISPER_MODELO),
                 "--host", "127.0.0.1", "--port", str(porta), "--threads", "8"],
                creationflags=SEM_JANELA)
            for _ in range(90):
                time.sleep(0.7)
                if _porta_viva(porta):
                    break
            else:
                # matar ANTES de levantar: o finally que faz isso esta depois
                # do try, e o raise nunca chegava la. Sobrava um whisper-server
                # segurando ~1,5 GB de VRAM ate reiniciar o PC. Auditoria 20/08.
                meu.terminate()
                raise RuntimeError("o Whisper nao subiu")
    try:
        roda([CURL, "-s", "--max-time", "3600",
              f"http://127.0.0.1:{porta}/inference",
              "-F", f"file=@{wav16}", "-F", "response_format=srt",
              "-F", f"language={idioma}", "-F", f"max_len={int(max_len)}",
              "-F", "split_on_word=true", "-F", "vad_max_speech_duration_s=600",
              "-o", destino])
    finally:
        if meu:
            meu.terminate()
    if not Path(destino).exists() or not Path(destino).read_text(
            encoding="utf-8-sig", errors="ignore").strip():
        raise RuntimeError("a transcrição veio vazia — tem fala nesse áudio?")


def _le_srt_blocos(caminho):
    import re
    blocos = re.split(r"\n\s*\n", Path(caminho).read_text(
        encoding="utf-8-sig", errors="ignore").strip())
    falas = []
    for b in blocos:
        linhas = [l for l in b.strip().split("\n") if l.strip()]
        tempo = next((l for l in linhas if "-->" in l), None)
        if not tempo:
            continue
        texto = " ".join(l.strip() for l in linhas[linhas.index(tempo) + 1:]).strip()
        if texto:
            falas.append({"tempo": tempo.strip(), "texto": texto})
    return falas


# Palavras-funcao de cada idioma: baratas de checar e quase impossiveis de
# faltar numa frase real. E o suficiente pra flagrar "a traducao veio no
# idioma errado" — nao precisa de detector de verdade.
_MARCAS_IDIOMA = {
    "pt": {"de", "que", "e", "o", "a", "os", "as", "um", "uma", "do", "da",
           "em", "para", "com", "não", "por", "mais", "se", "como", "mas",
           "ele", "ela", "você", "eu", "isso", "esse", "essa", "está",
           "muito", "bem", "já", "vai", "tem", "foi", "ser", "seu", "sua",
           "aqui", "ali", "estava", "era", "há", "isto", "esta", "estão",
           "quando", "também", "porque", "tudo", "agora", "então",
           "nada", "algo", "faz", "pode", "tenho", "quero", "pra"},
    "en": {"the", "of", "and", "to", "in", "is", "it", "you", "that", "he",
           "was", "for", "on", "are", "with", "as", "his", "they", "at",
           "this", "have", "from", "or", "one", "had", "by", "but", "not",
           "what", "all", "were", "we", "your", "can", "there", "do", "if",
           "here", "when", "because", "everything", "now", "then", "nothing",
           "something", "makes", "want", "why", "how", "just", "so", "my"},
    "es": {"de", "que", "y", "el", "la", "los", "las", "un", "una", "del",
           "en", "para", "con", "no", "por", "más", "si", "como", "pero",
           "él", "ella", "usted", "yo", "eso", "ese", "esa", "está",
           "muy", "bien", "ya", "va", "tiene", "fue", "ser", "su", "lo",
           "aquí", "allí", "estaba", "era", "hay", "esto", "esta",
           "están", "cuando", "también", "porque", "todo", "ahora",
           "entonces", "nada", "algo", "hace", "puede", "tengo", "quiero"},
    "fr": {"de", "que", "et", "le", "la", "les", "un", "une", "du", "des",
           "en", "pour", "avec", "ne", "pas", "par", "plus", "si", "comme",
           "mais", "il", "elle", "vous", "je", "ce", "cette", "est", "très",
           "bien", "déjà", "va", "a", "été", "être", "son", "sa",
           "ici", "là", "était", "quand", "aussi", "parce", "tout",
           "maintenant", "alors", "rien", "quelque", "fait", "peut", "veux"},
}


def _parece_idioma(texto, alvo):
    """True se a frase parece estar no idioma alvo (ou se nao da pra saber).

    Frase curtissima ou sem palavra-funcao nenhuma: na duvida, aprova —
    o objetivo e pegar frase INTEIRA no idioma errado, nao criar recusa
    falsa. Nome proprio e palavra emprestada passam."""
    marcas_alvo = _MARCAS_IDIOMA.get(alvo)
    if not marcas_alvo:
        return True                      # idioma que nao conhecemos: aprova
    palavras = [w.strip(".,!?;:\"'…()[]") for w in texto.lower().split()]
    palavras = [w for w in palavras if w]
    if len(palavras) < 3:
        return True
    no_alvo = sum(1 for w in palavras if w in marcas_alvo)
    fora = 0
    for outro, marcas in _MARCAS_IDIOMA.items():
        if outro == alvo:
            continue
        # so conta como "fora" a marca que NAO e tambem do alvo ("de" e
        # pt E es — nao acusa ninguem)
        fora = max(fora, sum(1 for w in palavras
                             if w in marcas and w not in marcas_alvo))
    # errado de verdade: o outro idioma domina e o alvo quase nao aparece
    return not (fora >= 2 and fora > no_alvo)


# Interjeicao / onomatopeia: traduzir isso e sempre pior do que nao traduzir.
# Medido com o qwen em 23/08: "Ai ai ai!" -> "Dude dude dude!", "Eita porra" ->
# "Dude", "Opa" -> "Hey". E mesmo sem traduzir, o Chatterbox tenta ATUAR a
# vocalizacao e sai robotico. Estas frases ficam com o audio ORIGINAL.
_INTERJEICOES = {
    "uh", "ah", "oh", "ih", "eh", "hm", "hmm", "hum", "huh", "ha", "haha",
    "hehe", "hihi", "ai", "ui", "opa", "eita", "uau", "wow", "hey", "ei",
    "psiu", "shh", "tsc", "ugh", "urgh", "argh", "aah", "ooh", "ahh", "uhh",
    "mm", "mmm", "nossa", "caraca", "caramba", "putz", "ixi", "oxe", "arre",
    "ufa", "epa", "uepa", "hein", "ne", "ta", "oi", "opah", "iha", "aiai",
}


def _eh_interjeicao(texto):
    """True quando a fala e SO vocalizacao, sem palavra de conteudo.

    Conservador de proposito: no maximo 3 tokens e TODOS tem que ser
    interjeicao conhecida ou puro alongamento de vogal ("aaah", "ooooh",
    "hmmm"). Uma palavra de verdade no meio ja derruba — errar pra menos aqui
    custa pouco (a frase e dublada normal), errar pra mais deixa fala de
    verdade sem dublagem."""
    import re as _re
    limpo = "".join(c if (c.isalnum() or c.isspace()) else " " for c in str(texto))
    tokens = [w.lower() for w in limpo.split() if w]
    if not tokens or len(tokens) > 3:
        return False
    for w in tokens:
        if w in _INTERJEICOES:
            continue
        # alongamento: so vogais, h e m, com alguma letra repetida (aaah, hmmm)
        if _re.fullmatch(r"[aeiouhm]{2,8}", w) and _re.search(r"(.)\1", w):
            continue
        return False
    return True


def _tamanho_fala(txt):
    """Proxy barato do tempo de fala: caracteres sem espaço. Não é sílaba, mas
    anda junto o bastante pra pegar o que importa aqui — encolhimento GRANDE."""
    return len([c for c in str(txt) if not c.isspace()])


def _traduz(texto, origem, alvo, insistir=False, medida=None):
    """`medida`: None normal · "curta" a anterior veio curta demais ·
    "longa" veio longa demais · `insistir` veio no idioma errado."""
    nomes = {"pt": "português", "en": "inglês", "es": "espanhol", "fr": "francês"}
    # ⚠️ CONSERTO DE 23/08. O prompt antigo dizia "prefira a forma mais curta,
    # corte palavra que não muda o sentido" — e o modelo obedeceu: comeu o
    # vocativo ("irmão") e trocou "transcender" por "left", entregando 58% do
    # tamanho original. Dublagem curta demais NÃO ajuda o lipsync: deixa buraco,
    # e o encaixe só estica 15%. O alvo certo é a MESMA duração, não a menor.
    prompt = (f"Traduza a fala abaixo de {nomes.get(origem, origem)} para "
              f"{nomes.get(alvo, alvo)}.\n"
              f"É DUBLAGEM: a tradução vai ser falada por cima do vídeo, então "
              f"tem que levar O MESMO TEMPO que o original — nem mais curta, "
              f"nem mais longa.\n"
              f"Regras:\n"
              f"- traduza o sentido INTEIRO, sem resumir e sem cortar nada\n"
              f"- vocativo, nome, gíria e interjeição FICAM "
              f"(\"irmão\" vira \"brother\", não some)\n"
              f"- use a forma falada e natural, não a formal\n"
              f"- se faltar tempo, escolha palavra mais curta; NUNCA apague ideia\n"
              f"Responda APENAS com a tradução, sem aspas, sem rótulo e sem "
              f"comentários.\n\nFALA: {texto}")
    if medida == "curta":
        prompt = (f"Traduza para {nomes.get(alvo, alvo)}. A versão anterior "
                  f"ficou CURTA DEMAIS e vai sobrar buraco na dublagem. "
                  f"Refaça com o MESMO tamanho falado do original: traduza tudo, "
                  f"inclusive vocativo e interjeição, e use as palavras "
                  f"completas em vez das abreviadas. SÓ a tradução.\n\n"
                  f"FALA: {texto}")
    elif medida == "longa":
        prompt = (f"Traduza para {nomes.get(alvo, alvo)}. A versão anterior "
                  f"ficou LONGA DEMAIS e a fala vai sair acelerada. Refaça "
                  f"mais enxuta, com o MESMO tamanho falado do original — mas "
                  f"sem apagar nenhuma ideia nem o vocativo. SÓ a tradução."
                  f"\n\nFALA: {texto}")
    if insistir:
        # segunda chance: a primeira veio no idioma errado/misturada
        prompt = (f"Traduza para {nomes.get(alvo, alvo)}. A resposta tem "
                  f"que estar 100% em {nomes.get(alvo, alvo)} — NENHUMA "
                  f"palavra em outro idioma (nome próprio pode). Falada, "
                  f"mesmo tamanho do original, sem cortar nada. SÓ a "
                  f"tradução.\n\nFALA: {texto}")
    corpo = json.dumps({"model": "qwen2.5:7b", "prompt": prompt,
                        "stream": False}).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                                 data=corpo,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode("utf-8")).get("response", "").strip()
    return _limpa_traducao(resp) or texto


def _limpa_traducao(resp):
    """O modelo as vezes devolve o rotulo do prompt junto ("FALA: ...") ou
    aspas — e o TTS falava isso em voz alta (visto em 16/08/2026)."""
    if not resp:
        return resp
    resp = resp.strip().splitlines()[0].strip()   # explicacao extra vem depois
    resp = re.sub(r"^\s*(FALA|TRADU[ÇC][ÃA]O|TRANSLATION|OUTPUT|RESPOSTA)\s*:\s*",
                  "", resp, flags=re.IGNORECASE)
    resp = resp.strip().strip('"“”\'').strip()
    # CAIXA ALTA: visto em 23/08 na re-pergunta ("NOW YOU'RE WHERE
    # EVERYTHING IS POSSIBLE."). O Chatterbox le maiuscula com enfase
    # errada, e as vezes soletra achando que e sigla. Se a frase INTEIRA
    # veio gritada, devolve pra caixa normal — so quando e longa, senao
    # estraga sigla de verdade.
    letras = [c for c in resp if c.isalpha()]
    if len(letras) >= 12 and all(c.isupper() for c in letras):
        resp = resp.capitalize()
    return resp


def modo_dublar(t, p):
    """Dublagem: transcreve com tempos -> traduz frase a frase (qwen local) ->
    Chatterbox gera cada frase no idioma alvo CLONANDO a voz do proprio
    arquivo (~30 s de referencia) -> encaixe.py estica cada uma pro tempo
    exato do original -> reforco RVC opcional -> Sidon + nivelar + corte."""
    arquivo = Path(p["arquivo"])
    if not arquivo.exists():
        raise RuntimeError(f"arquivo nao existe: {arquivo}")
    origem = str(p.get("origem", "pt"))
    alvo = str(p.get("alvo", "en"))

    _passo(t, 1, 6, "extraindo o áudio…")
    fonte = _audio_de(t, arquivo)

    _passo(t, 2, 6, "transcrevendo (Whisper)…")
    srt_orig = TRAB / f"{t}_original.srt"
    transcrever_srt(t, fonte, srt_orig, origem)
    falas = _le_srt_blocos(srt_orig)
    if not falas:
        raise RuntimeError("não achei fala nenhuma no áudio")

    if origem != alvo:
        try:
            derrapadas = 0
            for i, f in enumerate(falas, 1):
                # Interjeicao pura nao passa pelo tradutor NEM pelo TTS: fica
                # com o audio original (o `manter` la embaixo). Traduzir "Ai ai
                # ai!" virava "Dude dude dude!". Achado de 23/08.
                if _eh_interjeicao(f["texto"]):
                    f["manter"] = True
                    _passo(t, 3, 6, f"frase {i} é interjeição — mantendo a original")
                    continue
                _passo(t, 3, 6, f"traduzindo frase {i}/{len(falas)}…")
                trad = _traduz(f["texto"], origem, alvo)
                # TRAVA DE IDIOMA (auditoria 20/08): a traducao do qwen as
                # vezes vem misturada ("outro dimension") ou nem vem — e o
                # fallback devolvia o texto ORIGINAL, que o Chatterbox
                # falava no idioma errado. Confere e re-tenta uma vez.
                if not _parece_idioma(trad, alvo):
                    _passo(t, 3, 6,
                           f"frase {i} veio no idioma errado — re-traduzindo…")
                    trad2 = _traduz(f["texto"], origem, alvo, insistir=True)
                    if _parece_idioma(trad2, alvo):
                        trad = trad2
                    else:
                        derrapadas += 1   # ficou a melhor tentativa; avisa no fim
                        trad = trad2 or trad

                # TRAVA DE TAMANHO (23/08): tradução curta demais deixa buraco
                # e a longa demais sai acelerada — o encaixe só estica 15% e
                # comprime 10%, então fora dessa faixa ele não salva. Frase
                # curtinha fica de fora: proporção ali vira ruído.
                # A faixa é ASSIMÉTRICA de propósito: encolher é o que mata
                # (buraco não tem conserto), e em pt→en o inglês gasta mais
                # caractere pro mesmo tempo falado — 1,20 disparava à toa.
                base = _tamanho_fala(f["texto"])
                if base >= 12:
                    razao = _tamanho_fala(trad) / base
                    if razao < 0.80 or razao > 1.35:
                        como = "curta" if razao < 0.80 else "longa"
                        _passo(t, 3, 6, f"frase {i} saiu {como} demais "
                                        f"({razao:.0%}) — refazendo…")
                        trad2 = _traduz(f["texto"], origem, alvo, medida=como)
                        if trad2 and _parece_idioma(trad2, alvo):
                            r2 = _tamanho_fala(trad2) / base
                            # fica com a que chegou mais perto de 100%
                            if abs(r2 - 1.0) < abs(razao - 1.0):
                                trad = trad2
                f["texto"] = trad
            if derrapadas:
                _passo(t, 3, 6, f"⚠ {derrapadas} frase(s) podem ter saído "
                                f"no idioma errado — confira de ouvido")
        except Exception as exc:
            raise RuntimeError(f"tradução falhou ({exc}) — o Ollama está aberto?")
    srt_trad = TRAB / f"{t}_dublado.srt"
    srt_trad.write_text("\n\n".join(
        f"{i}\n{f['tempo']}\n{f['texto']}"
        for i, f in enumerate(falas, 1)) + "\n", encoding="utf-8")

    # VRAM: solta o qwen antes do Chatterbox (12 GB pra tudo; o ditado do
    # OpenWhispr recarrega o qwen sozinho quando precisar)
    try:
        subprocess.run(["ollama", "stop", "qwen2.5:7b"], capture_output=True,
                       creationflags=SEM_JANELA, timeout=30)
    except Exception:
        pass

    _passo(t, 4, 6, "preparando a referência da voz…")
    ff = achar_ffmpeg()
    ref_crua = TRAB / f"{t}_ref30.wav"
    roda([ff, "-y", "-i", fonte, "-t", "30", "-ar", "48000", ref_crua])
    ref = TRAB / f"{t}_ref.wav"
    nivelar(ref_crua, ref, "-16", dose="nivelar")

    _passo(t, 5, 6,
           f"gerando e encaixando {len(falas)} frase(s) — a parte demorada…")
    # Defaults da pesquisa de 16/08: cfg 0.3 desacelera a fala (queixa dele) e
    # exagero ~0.65 devolve expressividade; --fonte liga a referencia POR
    # SEGMENTO (clona cada frase do proprio trecho, estilo Eleven).
    bruto = TRAB / f"{t}_dub_bruto.wav"
    manter = [str(i) for i, f in enumerate(falas, 1) if f.get("manter")]
    cmd_enc = [PY_TTS, MOTOR / "encaixe.py", "-s", srt_trad, "-a", ref,
               "--fonte", fonte, "-o", bruto, "--idioma", alvo,
               "--exagero", str(p.get("atuacao", 0.65)),
               "--cfg", str(p.get("adesao", 0.3))]
    if manter:
        # essas frases saem do arquivo ORIGINAL, sem passar pelo TTS
        cmd_enc += ["--manter", ",".join(manter)]
    roda(cmd_enc)

    saida = bruto
    voz = next((v for v in listar_vozes() if v["nome"] == p.get("voz")), None)
    if voz and voz["modelo"]:
        _passo(t, 6, 6, f"reforçando com a voz {voz['nome']}…")
        try:
            reforco = TRAB / f"{t}_dub_rvc.wav"
            roda([PY_RVC, TREINO / "core.py", "infer",
                  "--input-path", saida, "--output-path", reforco,
                  "--pth-path", voz["modelo"], "--index-path", voz["indice"],
                  "--f0-method", "rmvpe", "--protect", "0.15",
                  "--index-rate", "0.5", "--volume-envelope", "1.0"],
                 cwd=TREINO)
            saida = reforco
        except Exception:
            pass   # reforco e opcional: falhou, segue a dublagem pura

    _passo(t, 6, 6, "acabamento…")
    final = TRAB / f"{t}_dublagem.wav"
    nivelar(saida, final, str(p.get("lufs", -16)), dose=str(_dose_sidon(p)))
    return cortar_no_tempo(final, fonte)


def separar_cinema(t, fonte, modelo="v2"):
    """fala / musica / efeitos com o Bandit v2 (ou BandIt Plus). Devolve (fala, [extras]).

    ⭐ 14/10/2026: modelo="plus" = BandIt Plus (DnR). Teste cego de OUVIDO dele contra o Fly Studio:
    Roformer -> resto -> BandIt Plus (cartao 2) foi o melhor LOCAL; a formula com Bandit v2 (cartao 5)
    deixou riser na musica e falhou onde a voz e forte. O Plus so entra no RESTO (sem voz): sozinho na
    mistura ele foi o pior (cartao 1). Fly Studio segue sendo o melhor de todos (pesos nao publicos).

    ⚠️ Duracao e taxa saem IDENTICAS (medido: 46,500s e 48 kHz nos quatro
    arquivos) — sem isso a corrente inteira perderia sincronia."""
    if not PY_CINEMA.exists():
        raise RuntimeError("o separador de cinema nao esta instalado "
                           "(~/CLAUDE/apps/separador-cinema)")
    entrada = TRAB / f"{t}_cine_in"
    saida = TRAB / f"{t}_cine_out"
    entrada.mkdir(parents=True, exist_ok=True)
    base = f"{t}_fonte"
    shutil.copy2(fonte, entrada / f"{base}.wav")
    if modelo == "plus":
        # o codigo do BandIt importa libs de TREINO que nao estao na venv: o stub finge que existem
        roda([PY_CINEMA, CINEMA / "modelos-teste" / "bancada" / "rodar_msst_stub.py",
              "--model_type", "bandit",
              "--config_path", CINEMA / "msst" / "configs" / "config_dnr_bandit_bsrnn_multi_mus64.yaml",
              "--start_check_point", CINEMA / "modelos-teste" / "bandit_plus" / "model_bandit_plus_dnr_sdr_11.47.chpt",
              "--input_folder", entrada, "--store_dir", saida])
    else:
        roda([PY_CINEMA, CINEMA / "msst" / "inference.py",
              "--model_type", "bandit_v2",
              "--config_path", CINEMA / "modelos" / "config_dnr_bandit_v2_mus64.yaml",
              "--start_check_point", CINEMA / "modelos" / "checkpoint-multi_fixed.ckpt",
              "--input_folder", entrada, "--store_dir", saida])
    pasta = saida / base
    # Bandit v2 grava Speech/Music/Sfx; o BandIt Plus grava speech/music/effects -> renomeia pra Sfx,
    # que e o nome que o resto do DUBX conhece (rotulo "Efeitos", saida "efeitos" do Fluxo)
    if (pasta / "effects.wav").exists() and not (pasta / "Sfx.wav").exists():
        (pasta / "effects.wav").rename(pasta / "Sfx.wav")
    fala = pasta / "Speech.wav"
    if not fala.exists():
        raise RuntimeError("o separador de cinema nao devolveu a fala")
    extras = [pasta / n for n in ("Music.wav", "Sfx.wav") if (pasta / n).exists()]
    shutil.rmtree(entrada, ignore_errors=True)
    return fala, extras


# Sufixos dos ids das vozes extras no historico: a voz 1 fica no id da
# tarefa, a 2 vira <id>b, a 3 <id>c... O mixer le a mesma sequencia.
SUFIXOS_VOZ = "bcdefghijklmnopqrstuvwxyz"


def modo_separar(t, p):
    """UMA FAIXA POR PESSOA — quem fala quando (diarizacao).

    ⚠️ MUDOU DE MOTOR EM 19/08. Antes era o SepFormer (separar.py), que serve
    pra gente falando UMA POR CIMA DA OUTRA. O material real dele e dialogo
    ALTERNADO (Plexus pergunta, Pedaleira responde) — ali o SepFormer nao tem
    o que separar e devolvia a mistura num canal e lixo no outro (provado por
    transcricao: a "voz 1" saia IGUAL a mistura). O separar.py continua no
    disco pro caso de material de fato sobreposto, mas nao e mais o padrao.

    O caminho novo (motor/diarizar.py) descobre os trechos de fala, agrupa por
    TIMBRE e RECORTA o original. Como nao reconstroi nada, cada voz sai na
    qualidade cheia do arquivo — nao passa por Sidon nem por 16 kHz."""
    arquivo = Path(p["arquivo"])
    if not arquivo.exists():
        raise RuntimeError(f"arquivo nao existe: {arquivo}")

    _passo(t, 1, 3, "extraindo o áudio…")
    fonte = _audio_de(t, arquivo)
    # wav de verdade pro diarizar (mp3/m4a nem sempre abrem no soundfile)
    if Path(fonte).suffix.lower() not in (".wav", ".aif", ".aiff"):
        conv = TRAB / f"{t}_para_separar.wav"
        roda([achar_ffmpeg(), "-y", "-i", fonte, "-c:a", "pcm_f32le", conv])
        fonte = conv

    # ⭐ LIMPAR ANTES (ideia dele, 19/08): sem isto cada personagem sai com a
    # MUSICA que tocava no trecho dele, porque o recorte e feito no arquivo
    # inteiro. Tirando fala x musica x efeitos primeiro, o recorte opera so na
    # FALA e os stems saem limpos — e musica/efeitos viram faixas proprias.
    # De quebra a transcricao melhora: medido no SHORT36, 93% -> 98% das
    # palavras casando com o roteiro (o Whisper erra menos sem musica em cima).
    extras_cinema = []
    # PADRAO = combinado: ele comparou A (so Bandit) x B (Roformer+Bandit) de
    # ouvido em 19/08 e disse "muito melhor" pro B. O custo e tempo (~25 s x
    # ~140 s no SHORT36), e ele escolheu qualidade.
    # 07/10: ele cortou as outras opcoes ("e obvio que tem que limpar, e essa
    # que e a separacao"). So existe Melhor voz; o parametro antigo e ignorado.
    limpeza = "combinado"
    motor = str(p.get("motor_diar", "roteiro"))
    if motor in MOTORES_CRUS:
        # ele (07/10): "pega o video e passa com os diferentes motores pra ver o
        # que vai devolver" — sem limpar, sem Sidon, pra saber se FUNCIONOU.
        limpeza = "nao"

    if limpeza == "combinado" and PY_CINEMA.exists():
        # ⭐ O MELHOR DOS DOIS (ideia dele, 19/08: "se der pra combinar seria
        # legal"). Medido pelo outro chat com gabarito: o Roformer isola VOZ
        # melhor que o Bandit (18,01 x 16,15 dB). Mas so o Bandit parte musica
        # de efeitos. Entao: Roformer tira a voz, o resto sai por SUBTRACAO
        # (exato por construcao) e o Bandit trabalha so no resto — sem voz pra
        # atrapalhar, que e o trabalho mais facil que ele pode receber.
        _passo(t, 1, 3, "isolando a voz (Roformer)…")
        p_stem = dict(p, separador="separator")
        voz, resto, _nc = separar_stems(t, fonte, p_stem)
        _passo(t, 1, 3, "partindo música e efeitos…")
        try:
            _fala_ignorada, extras_cinema = separar_cinema(t, resto, modelo="plus")
        except Exception:
            extras_cinema = [Path(resto)]      # sem Bandit, entrega o resto inteiro
        fonte = Path(voz)
    elif limpeza in ("cinema", "1", "sim") and PY_CINEMA.exists():
        _passo(t, 1, 3, "separando fala, música e efeitos…")
        fonte, extras_cinema = separar_cinema(t, fonte)

    pasta = TRAB / f"{t}_separadas"

    # ⭐ POR ROTEIRO e o padrao desde 19/08, e o motivo esta medido: no material
    # dele os motores ACUSTICOS empatam no chao do acaso (12/16 = chutar sempre
    # o Plexus), porque o Seedance gera os dois personagens com a MESMA voz. O
    # roteiro acerta 16/16 no mesmo arquivo. Timbre so vale quando as vozes sao
    # de fato diferentes.
    if motor in ("nenhum", "", None):
        # SO OS STEMS, sem dividir por pessoa (pedido dele em 20/08: "pra que
        # precisa de roteiro?" no fluxo de stems). A limpeza la em cima ja
        # separou fala / musica / efeitos; aqui e so nao diarizar. Sem isto o
        # painel exigia roteiro pra um trabalho que nao divide por personagem.
        if not extras_cinema:
            raise RuntimeError(
                "a limpeza (Roformer + Bandit) não rodou — sem ela não há "
                "voz/música/efeitos pra entregar")
        pasta.mkdir(parents=True, exist_ok=True)
        fala = pasta / "Fala.wav"
        shutil.copy2(fonte, fala)
        achadas = [fala] + extras_cinema
        # batismo ANTES de registrar: o historico ja nasce com o caminho bom
        bonitos = [_nome_da_faixa(x, i) for i, x in enumerate(achadas, start=1)]
        arq_base = _rotulo_de(p).split(" — ")[0]
        achadas = [Path(_batiza_entrega(t, x, f"{arq_base} — {b}"))
                   for x, b in zip(achadas, bonitos)]
        FAIXAS_DA_TAREFA[t] = [str(x) for x in achadas]
        _passo(t, 3, 3, f"guardando {len(achadas)} faixa(s)…")
        for i, wav in enumerate(achadas[1:], start=2):
            registra_historico({"id": f"{t}{SUFIXOS_VOZ[i - 2]}", "quando": _agora(),
                                "modo": "separar",
                                "rotulo": _rotulo_de(p).replace("voz 1", bonitos[i - 1]),
                                "saida": str(wav), "previa": None})
        return achadas[0]

    if motor == "roteiro":
        texto = str(p.get("roteiro", "")).strip()
        if not texto:
            raise RuntimeError(
                "cole o roteiro (uma linha por fala, no formato 'Personagem: fala') "
                "— é ele que diz quem fala o quê")
        arq_rot = TRAB / f"{t}_roteiro.txt"
        arq_rot.write_text(texto, encoding="utf-8")

        _passo(t, 2, 3, "ouvindo palavra por palavra…")
        srt = TRAB / f"{t}_palavras.srt"
        transcrever_srt(t, fonte, srt, str(p.get("idioma", "pt")), max_len=1)

        _passo(t, 3, 3, "casando a fala com o roteiro…")
        bruto = roda([PY_VOZ, MOTOR / "diarizar_roteiro.py", "-i", fonte, "-o", pasta,
                      "--srt", srt, "--roteiro", arq_rot])
    else:
        if motor not in MOTORES_DIAR:
            raise RuntimeError(f"motor '{motor}' nao existe "
                               f"(use: roteiro, {', '.join(MOTORES_DIAR)})")
        py_fn, script = MOTORES_DIAR[motor]
        py = py_fn()
        if not py.exists():
            raise RuntimeError(f"o motor '{motor}' nao esta instalado neste PC")
        _passo(t, 2, 3, "separando as vozes sobrepostas…" if motor in MOTORES_CRUS
               else "reconhecendo quem fala…")
        bruto = roda([py, MOTOR / script, "-i", fonte, "-o", pasta,
                      "--vozes", str(int(p.get("vozes", 0)))])
    # A ORDEM importa: no roteiro a voz 1 tem que ser o PRIMEIRO personagem a
    # falar, nao o primeiro em ordem alfabetica. Os motores imprimem um JSON
    # com a lista na ordem certa — usar ele, e so cair no glob se falhar.
    achadas = []
    for linha in reversed((bruto or "").strip().splitlines()):
        if linha.strip().startswith("{"):
            try:
                achadas = [Path(x) for x in json.loads(linha).get("vozes", [])]
            except Exception:
                achadas = []
            break
    achadas = [f for f in achadas if f.exists()]
    if not achadas:
        achadas = sorted((f for f in pasta.glob("*.wav") if not f.stem.startswith("_")),
                         key=lambda f: (int("".join(c for c in f.stem if c.isdigit()) or 0),
                                        f.stem))
    if not achadas:
        raise RuntimeError("a separação não gerou faixa nenhuma")
    achadas = achadas + extras_cinema      # Music/Sfx viram faixas tambem
    # batismo ANTES de registrar: o historico ja nasce com o caminho bom
    bonitos = [_nome_da_faixa(x, i) for i, x in enumerate(achadas, start=1)]
    arq_base = _rotulo_de(p).split(" — ")[0]
    achadas = [Path(_batiza_entrega(t, x, f"{arq_base} — {b}"))
               for x, b in zip(achadas, bonitos)]

    _passo(t, 3, 3, f"guardando {len(achadas)} voz(es)…")
    # o trabalhar() registra so a voz 1; as demais entram por fora e ficam
    # tocaveis/baixaveis pelo Historico e pela mesa de mistura.
    for i, wav in enumerate(achadas[1:], start=2):
        extra, previa = wav, None
        try:
            v = _com_video(t, arquivo, wav, rotulo=f"_voz{i}")
            if v:
                extra, previa = v, wav
        except Exception:
            pass   # sem video a voz extra ainda sai em wav
        nome_faixa = bonitos[i - 1]
        registra_historico({"id": f"{t}{SUFIXOS_VOZ[i - 2]}", "quando": _agora(),
                            "modo": "separar",
                            "rotulo": _rotulo_de(p).replace("voz 1", nome_faixa),
                            "saida": str(extra),
                            "previa": str(previa) if previa else None})
    FAIXAS_DA_TAREFA[t] = [str(x) for x in achadas]
    return achadas[0]


def modo_ambientar_no_lugar(t, p):
    """Painel Premiere: aplica um ambiente pronto no arquivo NO LUGAR.

    Pra "ir ajustando" sem degradar: a PRIMEIRA ambientacao de um arquivo
    guarda uma BASE seca em trabalho/base_ambiente, e reambientar parte
    SEMPRE dela — nunca do arquivo ja ambientado (reverb sobre reverb).
    Por isso a ordem certa do fluxo e melhorar/trocar ANTES de ambientar:
    o que vier depois da primeira ambientacao nao entra na base."""
    arquivo = Path(p["arquivo"])
    if not arquivo.exists():
        raise RuntimeError(f"arquivo nao existe: {arquivo}")
    nome = str(p.get("ambiente", "")).strip()
    preset = next((a for a in listar_ambientes() if a["nome"] == nome), None)
    if not preset:
        raise RuntimeError(f"ambiente '{nome}' nao existe")
    if preset.get("tipo") == "efeito":
        raise RuntimeError(f"'{nome}' é ambiente de efeito no clipe — o painel "
                           f"aplica direto no Premiere, não pelo servidor")

    _passo(t, 1, 3, "preparando a base seca…")
    _guarda_original(t, arquivo)
    bases = TRAB / "base_ambiente"
    bases.mkdir(parents=True, exist_ok=True)
    chave = hashlib.md5(str(arquivo).lower().encode("utf-8")).hexdigest()[:12]
    base = bases / f"{chave}_{arquivo.name}"
    if not base.exists():
        shutil.copy2(arquivo, base)

    _passo(t, 2, 3, f"aplicando {nome}…")
    fonte = _audio_de(t, base)
    final = _ambientar_wav(t, fonte, preset, p.get("intensidade", 100))
    _passo(t, 3, 3, "regravando o arquivo…")
    _regrava_no_lugar(t, arquivo, final)
    return arquivo


def modo_treinar_premiere(t, p):
    """Painel Premiere: treina uma voz nova com o que o Luan selecionou.

    SELECAO E CURADORIA (erro pago em 15/08: treinamos com o arquivo-fonte
    inteiro quando ele tinha recortado 2 segundos na timeline). Se vierem
    "trechos" [{arquivo, inicio, fim} em segundos], corta EXATAMENTE aquilo.
    "arquivos" inteiros ficam so como fallback.

    Cada trecho e NIVELADO (dose 0 de proposito: o Sidon RECONSTROI o timbre,
    e material de treino tem que ser a voz real — so ganho de volume) antes de
    ir pro dataset. Dai segue o modo_treinar normal (etapas 1-4)."""
    trechos = p.get("trechos") or [{"arquivo": a} for a in p.get("arquivos", [])]

    limpos = []
    ff = achar_ffmpeg()
    for i, tr in enumerate(trechos, 1):
        arq = Path(tr.get("arquivo", ""))
        if not arq.exists():
            continue
        _passo(t, 1, 5, f"preparando trecho {i}/{len(trechos)}…")
        ini, fim = tr.get("inicio"), tr.get("fim")
        if ini is not None and fim is not None and float(fim) > float(ini):
            fonte = TRAB / f"{t}_{i}_trecho.wav"
            roda([ff, "-y", "-ss", f"{float(ini):.3f}", "-to", f"{float(fim):.3f}",
                  "-i", arq, "-vn", "-acodec", "pcm_f32le", "-ar", "48000", fonte])
        else:
            fonte = _audio_de(t, arq, rotulo=f"_{i}")

        # NIVEL RAPIDA nao treina nada: o corte cru ja e a amostra, e o
        # modo_treinar nivela na hora de gravar a referencia.wav. Passar pelo
        # voz.py aqui era trabalho a toa — e foi exatamente onde o treino de
        # 2 s dele morreu em 24/08.
        if str(p.get("nivel", "")).lower() == "rapida":
            limpos.append(fonte)
            continue

        # ⚠️ O voz.py SAI COM CODIGO 0 mesmo quando falha num item: ele
        # imprime "ERRO —" e devolve {"files": []}. Ou seja, o roda() nao
        # levanta nada e o arquivo simplesmente nao existe — o modo_treinar
        # filtrava por .exists() e o erro chegava nele como o enigmatico
        # "envie pelo menos um audio" (print dele, 24/08). Agora: se o
        # nivelamento nao entregar, SEGUE COM O CORTE CRU, que serve
        # perfeitamente como material de treino, e avisa no lugar de morrer.
        limpo = TRAB / f"{t}_{i}_limpo.wav"
        usar = limpo
        try:
            roda([PY_VOZ, MOTOR / "voz.py", "-i", fonte, "-o", limpo,
                  "--dose", str(int(p.get("dose_limpeza", 0))), "--lufs", "-16"])
        except Exception as exc:
            AVISOS.setdefault(t, []).append(f"nivelamento do trecho {i}: {exc}")
            usar = fonte
        if not usar.exists():
            AVISOS.setdefault(t, []).append(
                f"o nivelamento do trecho {i} nao gerou arquivo — sigo com o corte cru")
            usar = fonte
        if not usar.exists():
            continue
        limpos.append(usar)

    if not limpos:
        raise RuntimeError("nenhum trecho valido na selecao")
    p2 = dict(p)
    p2["arquivos"] = [str(x) for x in limpos]
    return modo_treinar(t, p2)


def inicio_da_fala(arquivo):
    """Onde a fala comeca, em segundos. Usa o silencedetect do ffmpeg — nada de
    numpy aqui, o servidor e so biblioteca padrao.

    ⚠️ Existe por causa de um defeito do Whisper: com silencio na frente ele
    ancora a primeira palavra em 0,00 e espalha o texto pelo vazio (medido no
    SHORT36: fala em 2,14 s, marcada em 0,00). Cortando o silencio ANTES de
    transcrever, ele nao tem onde ancorar."""
    ff = achar_ffmpeg()
    r = subprocess.run([str(ff), "-hide_banner", "-i", str(arquivo),
                        "-af", "silencedetect=noise=-45dB:d=0.3", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=SEM_JANELA)
    saida = (r.stderr or "") + (r.stdout or "")
    # so interessa o silencio que COMECA no zero: e o da frente
    comeca_no_zero = False
    for linha in saida.splitlines():
        if "silence_start:" in linha:
            try:
                ini = float(linha.split("silence_start:")[1].split()[0])
            except (ValueError, IndexError):
                continue
            comeca_no_zero = ini <= 0.05
        elif "silence_end:" in linha and comeca_no_zero:
            try:
                return max(0.0, float(linha.split("silence_end:")[1].split()[0]) - 0.10)
            except (ValueError, IndexError):
                return 0.0
    return 0.0


def modo_legenda(t, p):
    """SRT ao lado do arquivo, com o TEXTO do roteiro e o TEMPO do audio.

    Pedido dele (19/08): "a transcricao do Premiere e uma merda, vou ter que
    ficar corrigindo na mao". Sem roteiro cai na transcricao pura (Whisper
    large-v3-turbo, ainda melhor que a do Premiere); com roteiro nao sobra
    correcao — nome proprio e pontuacao saem exatos.

    Limpar a fala ANTES melhora a marcacao das palavras: medido em 19/08,
    93% -> 98% de casamento com o roteiro."""
    arquivo = Path(p["arquivo"])
    if not arquivo.exists():
        raise RuntimeError(f"arquivo nao existe: {arquivo}")

    _passo(t, 1, 3, "extraindo o áudio…")
    fonte = _audio_de(t, arquivo)
    if Path(fonte).suffix.lower() not in (".wav", ".aif", ".aiff"):
        conv = TRAB / f"{t}_para_legenda.wav"
        roda([achar_ffmpeg(), "-y", "-i", fonte, "-c:a", "pcm_f32le", conv])
        fonte = conv
    if p.get("limpar", True) and PY_CINEMA.exists():
        try:
            fonte, _extras = separar_cinema(t, fonte)
        except Exception:
            pass          # sem limpeza a legenda ainda sai, so um pouco pior

    _passo(t, 2, 3, "ouvindo palavra por palavra…")
    # corta o silencio da frente pro Whisper nao ancorar a 1a palavra em 0,00
    desloca = inicio_da_fala(fonte)
    if desloca > 0.3:
        aparado = TRAB / f"{t}_sem_silencio.wav"
        roda([achar_ffmpeg(), "-y", "-ss", f"{desloca:.3f}", "-i", fonte,
              "-c:a", "pcm_f32le", aparado])
        fonte_txt = aparado
    else:
        desloca, fonte_txt = 0.0, fonte
    srt_palavras = TRAB / f"{t}_palavras.srt"
    transcrever_srt(t, fonte_txt, srt_palavras, str(p.get("idioma", "pt")), max_len=1)

    _passo(t, 3, 3, "montando a legenda…")
    texto = str(p.get("roteiro", "")).strip()
    arq_rot = None
    if texto:
        arq_rot = TRAB / f"{t}_roteiro_legenda.txt"
        arq_rot.write_text(texto, encoding="utf-8")

    # sai NA PASTA DO ARQUIVO dele, com o nome do arquivo — e onde ele vai
    # procurar no "Importar legendas do arquivo" do Premiere
    destino = arquivo.with_suffix(".srt")
    if destino.exists():
        destino = arquivo.with_name(f"{arquivo.stem} (DUBX).srt")
    PRESETS_LEG = {
        "normal": {"largura": 42, "linhas": 2, "min_dur": 1.0, "gap": 0.08,
                   "pontuacao": "tudo", "palavrao": "censurar"},
        "mini": {"largura": 15, "linhas": 1, "min_dur": 3.0, "gap": 0,
                 "pontuacao": "so-interrogacao", "palavrao": "censurar"},
    }
    for chave, valor in PRESETS_LEG.get(str(p.get("preset", "")), {}).items():
        p.setdefault(chave, valor)   # o que veio explicito sempre ganha

    cmd = [PY_VOZ, MOTOR / "legenda.py", "--srt", srt_palavras, "-o", destino,
           "--largura", str(int(p.get("largura", 42))),
           "--linhas", str(int(p.get("linhas", 2))),
           "--min-dur", str(float(p.get("min_dur", 1.0))),
           "--gap", str(float(p.get("gap", 0.08))),
           "--pontuacao", str(p.get("pontuacao", "tudo")),
           "--palavrao", str(p.get("palavrao", "manter")),
           "--personagem", str(p.get("personagem", "nao")),
           "--offset", f"{desloca:.3f}"]
    if arq_rot:
        cmd += ["--roteiro", arq_rot]
    roda(cmd)
    return destino


# ==================================================================== FLUXO
# A esteira de nodes (pedido dele, 20/08). Plano completo em
# "Nova pasta\PLANO_FLUXO.md" — LER antes de mexer aqui.
#
# As tres decisoes que sustentam o desenho:
#   1. O motor mora AQUI, no servidor, e nao na tela. Assim o app E o painel do
#      Premiere ganham fluxo de graca — e o painel, que e o mais limitado dos
#      dois, nao precisa saber executar nada.
#   2. Node nao tem UMA saida, tem saidas COM NOME. O `separar` devolve um
#      cacho (voz1, voz2, musica, efeitos) e a ligacao diz de QUAL saida ela
#      parte. Sem isso e impossivel expressar o pedido dele: "da voz que voce
#      separou, bota o voice changer NESSE aqui" — nesse, nao em todos.
#   3. Tem que existir o `juntar`. Se da pra separar, tem que dar pra voltar,
#      senao o fluxo abre e nunca fecha.
#
# O que trafega entre nodes e CAMINHO DE ARQUIVO, nunca audio em memoria: e
# como o resto do servidor ja trabalha, e e o que aguenta video de 2 GB.
#
# ⚠️ SERIAL DE PROPOSITO. Nada aqui roda dois nodes ao mesmo tempo. No Windows
# a VRAM esgotada nao da erro: derrama pra RAM e CONGELA o PC (ja aconteceu com
# ele em 19/08). "Paralelizar pra ficar mais rapido" nao e melhoria pendente,
# e defeito esperando pra acontecer.


def juntar_faixas(t, arquivos, volumes=None, destino=None):
    """Mistura N faixas numa so — o node `juntar`.

    ⚠️ `normalize=0` e OBRIGATORIO. Por padrao o amix DIVIDE pelo numero de
    entradas, entao juntar 4 stems devolveria tudo ~12 dB mais baixo — o
    oposto de reconstruir a mistura original."""
    arquivos = [str(a) for a in arquivos if a]
    if not arquivos:
        raise RuntimeError("juntar sem faixa nenhuma na entrada")
    destino = Path(destino or (TRAB / f"{t}_juntado.wav"))
    destino.parent.mkdir(parents=True, exist_ok=True)
    if len(arquivos) == 1:
        shutil.copy2(arquivos[0], destino)
        return destino

    vols = list(volumes or [])
    cmd = [achar_ffmpeg(), "-y"]
    for a in arquivos:
        cmd += ["-i", a]
    partes, rotulos = [], []
    for i in range(len(arquivos)):
        v = float(vols[i]) if i < len(vols) else 1.0
        partes.append(f"[{i}:a]volume={v}[a{i}]")
        rotulos.append(f"[a{i}]")
    filtro = (";".join(partes) + ";" + "".join(rotulos)
              + f"amix=inputs={len(arquivos)}:normalize=0[mix]")
    cmd += ["-filter_complex", filtro, "-map", "[mix]",
            "-c:a", "pcm_f32le", str(destino)]
    roda(cmd)
    return destino


def _saidas_nomeadas(arquivos):
    """Da nome a cada saida de uma separacao.

    Da pra ligar por POSICAO (`voz1`, `voz2`) ou pelo NOME DO PERSONAGEM
    (`Pedaleira`), porque o motor por roteiro batiza o arquivo com o nome de
    quem fala. Os dois apontam pro mesmo lugar — o que for mais natural pra
    ele na hora de montar o fluxo."""
    saidas, n = {}, 0
    for f in arquivos:
        # pos-batismo (24/08) o papel mora no sufixo apos o ultimo " - "
        cru = Path(f).stem.rsplit(" - ", 1)[-1]
        s = cru.lower()
        if s in ("music", "musica", "música"):
            saidas["musica"] = str(f)
        elif s in ("sfx", "efeitos"):
            saidas["efeitos"] = str(f)
        elif s in ("speech", "fala"):
            saidas["fala"] = str(f)
        else:
            n += 1
            saidas[f"voz{n}"] = str(f)
            saidas.setdefault(cru, str(f))   # tambem pelo nome do personagem
    return saidas


# tipo do node -> (modo do servidor, precisa de copia antes?)
# Os modos "_no_lugar" REGRAVAM o proprio arquivo. Num fluxo isso destruiria a
# entrada de outro node, entao eles rodam sempre sobre uma COPIA.
NODES_MODO = {
    "separar":   ("separar", False),
    "trocar":    ("trocar", False),
    "melhorar":  ("melhorar", False),
    "dublar":    ("dublar", False),
    "legenda":   ("legenda", False),
    "ambientar": ("ambientar_no_lugar", True),
}


def _assinatura(caminho):
    """Identidade barata de um arquivo, pro cache. Tamanho + data resolve:
    hash de conteudo em video de 2 GB custaria mais que refazer o node."""
    try:
        st = Path(caminho).stat()
        # Com so o NOME, "V028.mp4" da pasta A e "V028.mp4" da pasta B eram o
        # mesmo arquivo pro cache — e o fluxo servia o resultado do outro.
        # A pasta entra na identidade. Auditoria 20/08.
        return f"{str(Path(caminho).resolve()).lower()}:{st.st_size}:{int(st.st_mtime)}"
    except Exception:
        return str(caminho)


CACHE_FLUXO = TRAB / "cache_fluxo.json"

# ----------------------------------------------------------- configuracao
# Ajustes que ELE muda pela tela (regra de 20/08: "tudo pelo site, nada de
# terminal"). Arquivo simples ao lado do servidor; faltando, valem os
# defaults daqui.
# O som de "terminou", escolhido por ele em 23/08. O .wav e pro winsound
# (que so toca WAV); o .mp3 e o mesmo som, servido pro app do navegador.
SOM_PRONTO = BASE / "sons" / "pronto.wav"
SOM_PRONTO_MP3 = BASE / "sons" / "pronto.mp3"

CONFIG_ARQ = BASE / "config.json"
CONFIG_PADRAO = {
    "cache_teto_gb": 3.0,   # teto do cache do Fluxo
    "faxina_auto": False,   # apagar as passagens da tarefa ao terminar
    # True  = o DUBX sobe o PROPRIO Whisper com o modelo grande (melhor
    #         transcricao, independente do que o OpenWhispr esta usando)
    # False = reaproveita o servidor do OpenWhispr quando ele esta aberto
    #         (mais rapido, mas herda o modelo escolhido LA)
    "whisper_proprio": True,
}


def _config_le():
    try:
        d = json.loads(CONFIG_ARQ.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    fora = dict(CONFIG_PADRAO)
    fora.update({k: d[k] for k in CONFIG_PADRAO if k in d})
    return fora


def _config_grava(novos):
    cfg = _config_le()
    if "cache_teto_gb" in novos:
        try:
            # entre 0,5 e 50 GB; fora disso e dedo errado, nao intencao
            cfg["cache_teto_gb"] = min(50.0, max(0.5, float(novos["cache_teto_gb"])))
        except (TypeError, ValueError):
            pass
    if "faxina_auto" in novos:
        cfg["faxina_auto"] = bool(novos["faxina_auto"])
    if "whisper_proprio" in novos:
        cfg["whisper_proprio"] = bool(novos["whisper_proprio"])
    CONFIG_ARQ.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return cfg


def _cache_le():
    try:
        return json.loads(CACHE_FLUXO.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Teto do cache do Fluxo, em GB. Pedido dele em 20/08: "vamos ver uma forma de
# configurar um cache tbm". Sem teto o cache cresce pra sempre E protege da
# faxina tudo que aponta — ou seja, virava um vazamento com duas cabecas.
# Ao estourar, os nodes MAIS VELHOS saem primeiro (o trabalho recente e o que
# ele esta iterando; e esse que precisa voltar instantaneo).
# O VALOR vem da tela (config.json); 3 GB e so o default de fabrica.
CACHE_TETO_GB = CONFIG_PADRAO["cache_teto_gb"]


def _cache_poda(d):
    """Descarta as entradas mais velhas ate o cache caber no teto.

    A idade sai do mtime do arquivo de saida — quem nao existe mais no disco
    ja e lixo e sai primeiro, de graca."""
    itens = []
    for chave, saidas in d.items():
        arqs = [pathlib.Path(v) for v in (saidas or {}).values() if v]
        vivos = [a for a in arqs if a.exists()]
        if not vivos:
            continue                       # aponta pro vazio: descarta
        tam = sum(a.stat().st_size for a in set(vivos))
        quando = max(a.stat().st_mtime for a in vivos)
        itens.append((quando, tam, chave))
    itens.sort(reverse=True)               # mais novo primeiro
    teto = _config_le()["cache_teto_gb"] * 1073741824
    acumulado, mantidos = 0, {}
    for quando, tam, chave in itens:
        if acumulado + tam > teto and mantidos:
            break                          # daqui pra tras e velho demais
        acumulado += tam
        mantidos[chave] = d[chave]
    return mantidos


def _cache_grava(d):
    try:
        d = _cache_poda(d)
        CACHE_FLUXO.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FLUXO.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except Exception:
        pass


def _chave_no(no, entradas):
    """(tipo + params + assinatura das entradas). Mudar o LUFS do ULTIMO node
    nao pode refazer a separacao nem a troca de voz — sem cache, cada ajuste
    custa o fluxo inteiro (20+ min) e a ferramenta e abandonada na segunda
    tentativa. Isto nao e otimizacao, e requisito."""
    # Quando o FORMATO de saida de um node muda no codigo, o cache antigo
    # vira mentira (a legenda guardava {saida: srt} e passou a atravessar o
    # audio). Subir a versao daquele tipo aqui descarta so os caches dele.
    versoes = {"legenda": 3}
    # `titulo` e so exibicao — renomear a etapa NAO pode refazer o trabalho
    params = {k: v for k, v in (no.get("params") or {}).items() if k != "titulo"}
    cru = json.dumps({"tipo": no.get("tipo"),
                      "v": versoes.get(no.get("tipo"), 1),
                      "params": params,
                      "entradas": [_assinatura(e) for e in entradas]},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(cru.encode("utf-8")).hexdigest()


def _ordena_grafo(nodes, ligacoes):
    """Kahn. Devolve a ordem de execucao, ou explode apontando o ciclo."""
    entra = {i: 0 for i in nodes}
    saem = {i: [] for i in nodes}
    for l in ligacoes:
        saem[l["de"]].append(l["para"])
        entra[l["para"]] += 1
    fila = [i for i, n in entra.items() if n == 0]
    ordem = []
    while fila:
        i = fila.pop(0)
        ordem.append(i)
        for j in saem[i]:
            entra[j] -= 1
            if entra[j] == 0:
                fila.append(j)
    if len(ordem) != len(nodes):
        presos = [i for i in nodes if i not in ordem]
        raise RuntimeError("o fluxo tem um ciclo (volta pra tras) nos nodes: "
                           + ", ".join(presos))
    return ordem


def _valida_grafo(g):
    """Falha ANTES de gastar GPU. Erro de edicao aponta o node e nao roda nada
    — descobrir no minuto 18 que uma ligacao aponta pra lugar nenhum e o tipo
    de coisa que faz abandonar a ferramenta."""
    nodes = {}
    for n in g.get("nodes") or []:
        if not n.get("id"):
            raise RuntimeError("tem node sem id no fluxo")
        if n["id"] in nodes:
            raise RuntimeError(f"dois nodes com o mesmo id: {n['id']}")
        tipo = n.get("tipo")
        if tipo not in NODES_MODO and tipo not in ("entrada", "juntar", "saida"):
            raise RuntimeError(f"node de tipo desconhecido: {tipo}")
        nodes[n["id"]] = n
    if not nodes:
        raise RuntimeError("o fluxo esta vazio")

    ligacoes = []
    for l in g.get("ligacoes") or []:
        for lado in ("de", "para"):
            if l.get(lado) not in nodes:
                raise RuntimeError(
                    f"ligacao aponta pro node '{l.get(lado)}', que nao existe")
        ligacoes.append({"de": l["de"], "para": l["para"],
                         "saida": l.get("saida") or "saida"})

    if not any(n["tipo"] == "entrada" for n in nodes.values()):
        raise RuntimeError("o fluxo precisa de um node de entrada")
    return nodes, ligacoes


# Catalogo de etapas: o que cada passo E e quais ajustes ele tem por dentro.
# Mora AQUI, no servidor, e nao na tela, pelo mesmo motivo do motor: o app e o
# painel do Premiere desenham a mesma pilha sem duas listas pra manter.
# `pede` marca o ajuste que o fluxo pode deixar como curinga ("?voz").
CATALOGO = {
    "separar": {
        "nome": "Separar vozes",
        "resumo": "quebra em uma faixa por pessoa, mais música e efeitos",
        "produz": ["voz1", "voz2", "musica", "efeitos"],
        "controles": [
            {"id": "motor_diar", "nome": "Motor", "tipo": "escolha", "val": "roteiro",
             "op": [["roteiro", "Por personagem, pelo roteiro"],
                    ["nenhum", "Não dividir por pessoa — só voz/música/efeitos"],
                    ["timbre", "Por personagem, pelo timbre"],
                    ["pyannote", "pyannote"], ["sortformer", "Sortformer"],
                    ["mossformer2", "MossFormer2 — vozes sobrepostas (cru)"],
                    ["tiger", "TIGER — vozes sobrepostas (cru)"],
                    ["sam", "SAM Audio — vozes sobrepostas, 48 kHz (cru)"],
                    ["sam_risada", "SAM Audio + risadas à parte (pra dublagem)"]],
             "dica": "Pelo roteiro acerta 100% mesmo quando os personagens têm a MESMA voz — é o caso do Seedance. Se você só quer separar a fala da música e dos efeitos, escolha \"Não dividir por pessoa\" e não precisa de roteiro."},
            {"id": "vozes", "nome": "Vozes", "tipo": "escolha", "val": "2",
             "dica": "Quantas pessoas falam no arquivo. Diga o número quando souber — "
                     "\"descobrir sozinho\" às vezes divide a mesma pessoa em duas.",
             "so_se_nao": {"motor_diar": "roteiro"},   # o roteiro ja diz quantas
             "op": [["1", "1 pessoa (só tirar a fala)"],
                    ["2", "2 pessoas"], ["3", "3 pessoas"], ["4", "4 pessoas"],
                    ["0", "descobrir sozinho"]]},
            # `so_se`: o roteiro so existe (e so e obrigatorio) quando o motor
            # e o "roteiro". Pedido dele em 20/08 — o painel exigia roteiro no
            # fluxo "so stems", onde ele nem quer dividir por pessoa. Exigencia
            # que nao se aplica e defeito, nao rigor.
            {"id": "roteiro", "nome": "Roteiro", "tipo": "texto", "val": "",
             "so_se": {"motor_diar": "roteiro"},
             "dica": "Uma linha por fala: Personagem: fala"},
        ],
    },
    "trocar": {
        "nome": "Trocar voz",
        "resumo": "põe outra voz na fala, mantendo o jeito de falar",
        "produz": ["saida"],
        "controles": [
            {"id": "voz", "nome": "Voz", "tipo": "voz", "val": "",
             "dica": "A voz que vai SAIR. Se a que você quer não está aqui, "
                     "crie ela na aba Treinar."},
            {"id": "motor_vc", "nome": "Motor", "tipo": "escolha", "val": "seedvc",
             "op": [["seedvc", "Seed-VC (recomendado)"],
                    ["rvc", "RVC (treinado, 8 h)"]],
             "dica": "Seed-VC ganhou o seu teste cego de 23/08: chegou em 217 Hz "
                     "contra os 219 Hz do Plexus e corrompeu 1 palavra em 43. O RVC "
                     "parou em 161 Hz (ele preserva a altura da voz de origem) e "
                     "corrompeu 3. O Seed-VC também treina em 4 min contra 8 h do "
                     "RVC. Use o RVC quando quiser manter a altura da voz original."},
        ],
    },
    "melhorar": {
        "nome": "Melhorar",
        "resumo": "nivela e reconstrói com o Sidon",
        "produz": ["saida"],
        "controles": [
            {"id": "dose", "nome": "Dose", "tipo": "escolha", "val": "100",
             "op": [["0", "Só nivelar"], ["50", "Meio"], ["100", "Completo"]],
             "dica": "Quanto o Sidon reconstrói a voz. \"Só nivelar\" mexe apenas "
                     "no volume; \"Completo\" refaz a voz inteira — salva gravação "
                     "estragada, mas em voz que já está boa tira naturalidade."},
            {"id": "lufs", "nome": "LUFS alvo", "tipo": "numero", "val": "-16",
             "min": -36, "max": -6, "passo": 1,
             "dica": "O volume final. −16 para faixa de voz; vídeo montado mira −14."},
        ],
    },
    "dublar": {
        "nome": "Dublar",
        "resumo": "traduz e fala no outro idioma com a mesma voz",
        "produz": ["saida"],
        "controles": [
            {"id": "origem", "nome": "Do idioma", "tipo": "escolha", "val": "pt",
             "op": [["pt", "Português"], ["en", "Inglês"], ["es", "Espanhol"]],
             "dica": "O idioma que o áudio FALA hoje. Dizer certo dá resultado "
                     "melhor do que deixar o modelo adivinhar."},
            {"id": "alvo", "nome": "Para", "tipo": "escolha", "val": "en",
             "op": [["en", "Inglês"], ["es", "Espanhol"], ["pt", "Português"]],
             "dica": "O idioma do resultado. A voz é clonada do próprio arquivo, "
                     "frase a frase, e encaixada no tempo exato do original. Uma "
                     "trava confere se cada frase saiu no idioma certo e "
                     "re-traduz quando não saiu."},
        ],
        "aviso": "experimental — pode escapar palavra no idioma errado "
                 "(a trava re-traduz, mas confira de ouvido); "
                 "depende do Ollama aberto",
    },
    "ambientar": {
        "nome": "Ambientar",
        "resumo": "põe a voz no lugar da cena",
        "produz": ["saida"],
        "controles": [
            {"id": "ambiente", "nome": "Ambiente", "tipo": "ambiente", "val": "",
             "dica": "O lugar onde a cena acontece. Grava no arquivo com backup, "
                     "e reaplicar outro ambiente parte sempre do som SECO "
                     "original — nunca empilha reverb sobre reverb."},
            {"id": "intensidade", "nome": "Intensidade", "tipo": "escolha", "val": "100",
             "op": [["50", "Leve"], ["75", "Médio"], ["100", "Total"]],
             "dica": "Quanto do ambiente entra. \"Total\" é o desenho completo do "
                     "preset; abaixo disso ele fica mais discreto."},
        ],
    },
    "legenda": {
        "nome": "Legenda",
        "resumo": "gera o .srt na pasta do arquivo",
        "produz": ["srt"],
        "controles": [
            {"id": "preset", "nome": "Formato", "tipo": "escolha", "val": "normal",
             "op": [["normal", "Normal"], ["mini", "Plexus Mini"]],
             "dica": "Normal = 2 linhas de 42 caracteres, com pontuação. Plexus "
                     "Mini = linha única de 15 caracteres, sem pontuação (só os "
                     "\"?\") — igual ao seu preset do Premiere."},
            {"id": "personagem", "nome": "Marcar quem fala", "tipo": "escolha", "val": "nao",
             "op": [["nao", "Não"], ["sim", "Sim — [Plexus] / [Pedaleira]"]],
             "dica": "Escreve o nome de quem fala no começo de cada legenda. "
                     "Precisa do Roteiro preenchido — é dele que saem os nomes."},
            # Sem este campo o fluxo "Limpar e legendar" era IRRODAVEL: o passo
            # trazia o curinga "?roteiro", o painel virava isso em exigencia, e
            # nao existia campo nenhum pra preencher. Achado da auditoria 20/08.
            {"id": "roteiro", "nome": "Roteiro", "tipo": "texto", "val": "",
             "dica": "Opcional. Com ele a legenda sai com as palavras certas em vez do que o Whisper ouviu."},
        ],
    },
    "juntar": {
        "nome": "Juntar faixas",
        "resumo": "mistura as faixas de volta numa só",
        "produz": ["saida"],
        "controles": [],
    },
}


def _linear_para_grafo(passos):
    """Converte a PILHA VERTICAL de passos no grafo que o motor executa.

    Pedido dele (20/08, com print): o fluxo nao e um canvas de arrastar node,
    e uma lista de etapas empilhadas com seta pra baixo, cada uma colapsavel
    com seus ajustes dentro. Isso e melhor que o canvas pro caso dele — e a
    ramificacao vira PARAMETRO, nao desenho.

    Cada passo:
        {"tipo": "trocar", "de": "voz2", "params": {...}}

    `de` diz de onde vem a entrada:
      · omitido      -> a saida do passo anterior
      · "voz2"       -> aquela saida NOMEADA do ultimo passo que produziu ela
                        (e assim que se diz "troca a voz DESSE aqui" sem
                         desenhar ramo nenhum)
      · lista        -> varias entradas (so faz sentido no `juntar`)
      · "@anterior"  -> a saida do passo anterior, explicito, pra usar dentro
                        de uma lista junto com saidas nomeadas
    """
    nodes = [{"id": "e0", "tipo": "entrada", "params": {}}]
    ligacoes = []
    # nome da saida -> id do node que a produziu (o ultimo que produziu vence)
    onde = {"arquivo": "e0", "saida": "e0"}
    anterior = "e0"

    for i, passo in enumerate(passos or [], 1):
        tipo = passo.get("tipo")
        nid = f"p{i}"
        params = dict(passo.get("params") or {})
        # o titulo mora no PASSO, nao nos params — sem este resgate a barra de
        # progresso mostra "separar…" em vez de "Separar por personagem…"
        if passo.get("titulo"):
            params.setdefault("titulo", passo["titulo"])
        nodes.append({"id": nid, "tipo": tipo, "params": params})

        de = passo.get("de")
        if de is None:
            fontes = ["@anterior"]
        elif isinstance(de, list):
            fontes = list(de)
        else:
            fontes = [de]

        for f in fontes:
            if f in ("@anterior", None, ""):
                ligacoes.append({"de": anterior, "saida": "saida", "para": nid})
            else:
                origem = onde.get(f)
                if not origem:
                    # Passo pede uma saida que ninguem produziu ainda. Nao
                    # explode aqui: o _valida_grafo aponta melhor, e o motor
                    # ja sabe pular passo sem entrada com aviso no log.
                    continue
                ligacoes.append({"de": origem, "saida": f, "para": nid})

        # o que este passo passa a oferecer pra quem vem depois
        if tipo == "separar":
            # os nomes reais so aparecem na execucao; registramos os provaveis
            # pra quem escreveu o fluxo poder ligar em "voz2"/"musica"
            for nome in ("voz1", "voz2", "voz3", "voz4", "musica", "efeitos", "fala"):
                onde[nome] = nid
        elif tipo == "legenda":
            onde["srt"] = nid
        onde["saida"] = nid
        anterior = nid

    nodes.append({"id": "fim", "tipo": "saida", "params": {}})
    ligacoes.append({"de": anterior, "saida": "saida", "para": "fim"})
    return {"nodes": nodes, "ligacoes": ligacoes}


def _preenche_grafo(grafo, p):
    """Troca os curingas do fluxo salvo pelo que ele escolheu na hora.

    Um fluxo salvo com a voz "Pedaleira" chumbada dentro so serve pro
    Pedaleira. Entao o arquivo guarda `"voz": "?voz"` e quem roda manda
    `{"voz": "Pedaleira"}` — o mesmo fluxo vale pra qualquer voz, qualquer
    roteiro, qualquer idioma. Curinga sem resposta some do params (o modo
    cai no proprio padrao)."""
    def resolve(v):
        if isinstance(v, str) and v.startswith("?"):
            return p.get(v[1:], None)
        if isinstance(v, dict):
            return {k: resolve(x) for k, x in v.items()}
        if isinstance(v, list):
            return [resolve(x) for x in v]
        return v

    novo = dict(grafo)
    nodes = []
    for no in grafo.get("nodes") or []:
        n2 = dict(no)
        params = {}
        for k, v in (no.get("params") or {}).items():
            r = resolve(v)
            if r is not None and r != "":
                params[k] = r
        n2["params"] = params
        nodes.append(n2)
    novo["nodes"] = nodes
    return novo


def modo_fluxo(t, p):
    """Executa um grafo de nodes. Ver PLANO_FLUXO.md.

    Devolve o arquivo do ULTIMO node de saida (ou do ultimo node executado,
    se nao houver `saida` explicito). As demais saidas ficam em
    FAIXAS_DA_TAREFA[t], pro painel poder jogar todas na timeline."""
    # A PILHA VERTICAL e o formato de autoria (o que a tela mostra); o grafo
    # e o formato de execucao. Quem manda `passos` nao precisa saber de grafo.
    grafo = (_linear_para_grafo(p["passos"]) if p.get("passos")
             else (p.get("grafo") or {}))
    grafo = _preenche_grafo(grafo, p)
    nodes, ligacoes = _valida_grafo(grafo)
    ordem = _ordena_grafo(nodes, ligacoes)

    # MODO VALIDANDO (pedido de 20/08): roda so ate o passo N, entrega o
    # resultado dele e para. Continuar e mandar de novo com `ate` = N+1 — os
    # passos anteriores voltam do cache em milissegundos, entao "seguir" e
    # instantaneo e "refazer com outro ajuste" so refaz dali pra frente.
    if p.get("ate"):
        alvo = f"p{int(p['ate'])}"
        if alvo in ordem:
            ordem = ordem[:ordem.index(alvo) + 1]

    # node -> [(id_origem, nome_da_saida)], na ordem em que foram ligados
    entradas_de = {i: [] for i in nodes}
    for l in ligacoes:
        entradas_de[l["para"]].append((l["de"], l["saida"]))

    cache = _cache_le()
    usar_cache = bool(p.get("cache", True))
    resultados = {}          # id do node -> {nome da saida: caminho}
    total = len(ordem)
    ultimo_arquivo = None
    ultimo_no = None
    todas_as_saidas = []

    for passo, nid in enumerate(ordem, 1):
        no = nodes[nid]
        tipo = no["tipo"]
        params = dict(no.get("params") or {})
        # `titulo` e so pra barra de progresso — POP pra nunca vazar como
        # parametro pro modo la embaixo.
        rotulo = params.pop("titulo", None) or tipo

        # ---- junta as entradas vindas de cima
        entradas = []
        for origem, nome in entradas_de[nid]:
            saidas = resultados.get(origem) or {}
            if nome in saidas:
                entradas.append(saidas[nome])
            else:
                # Saida que nao existe (pediu voz3 e so tinha 2 pessoas): AVISA
                # e segue. Nao e erro fatal — o grafo e escrito antes de saber
                # quantas pessoas falam no arquivo.
                _passo(t, passo, total,
                       f"{rotulo}: '{nome}' nao existe em {origem} — pulando")
        if tipo != "entrada" and not entradas:
            _passo(t, passo, total, f"{rotulo}: sem entrada — node pulado")
            resultados[nid] = {}
            continue

        # ---- entrada e saida sao estruturais, nao chamam motor nenhum
        if tipo == "entrada":
            arq = params.get("arquivo") or p.get("arquivo")
            if not arq or not Path(arq).exists():
                raise RuntimeError(f"entrada sem arquivo: {arq}")
            resultados[nid] = {"arquivo": str(arq), "saida": str(arq)}
            ultimo_arquivo = str(arq)
            continue

        if tipo == "saida":
            resultados[nid] = {"saida": entradas[0]}
            ultimo_arquivo = entradas[0]
            # ⭐ Se quem alimenta este node produziu VARIAS faixas (o `separar`
            # devolve um cacho: Fala + Musica + Efeitos), a entrega e o CACHO
            # INTEIRO, nao so a primeira. Defeito real achado por ele em 20/08:
            # o "so stems" punha UMA faixa na timeline em vez de tres, porque a
            # ligacao pro node de saida carrega um arquivo so.
            origem = entradas_de[nid][0][0] if entradas_de[nid] else None
            for arq in (resultados.get(origem) or {}).values():
                if arq and arq not in todas_as_saidas:
                    todas_as_saidas.append(arq)
            if entradas[0] not in todas_as_saidas:
                todas_as_saidas.append(entradas[0])
            _passo(t, passo, total, f"{rotulo}: pronto")
            continue

        # ---- cache: mesmo node, mesmos params, mesmas entradas = ja esta feito
        chave = _chave_no(no, entradas)
        guardado = cache.get(chave) if usar_cache else None
        if guardado and all(Path(v).exists() for v in guardado.values()):
            resultados[nid] = guardado
            ultimo_no = nid        # <- faltava: etapa vinda do cache tambem e
                                   # "a ultima". Sem isso o modo Validando
                                   # entregava o cacho errado. Auditoria 20/08.
            ultimo_arquivo = guardado.get("saida") or next(iter(guardado.values()))
            _passo(t, passo, total, f"{rotulo}: reaproveitado do cache")
            continue

        sub = f"{t}n{passo}"          # id proprio: cada node tem seu espaco
        _passo(t, passo, total, f"{rotulo}…")

        if tipo == "juntar":
            destino = TRAB / f"{sub}_juntado.wav"
            saida = juntar_faixas(sub, entradas, params.get("volumes"), destino)
            saidas = {"saida": str(saida)}
        else:
            modo, precisa_copia = NODES_MODO[tipo]
            fonte = entradas[0]
            if precisa_copia:
                # o modo regrava o proprio arquivo: nunca deixar ele encostar
                # no original nem na saida de outro node
                copia = TRAB / f"{sub}_copia{Path(fonte).suffix}"
                shutil.copy2(fonte, copia)
                fonte = str(copia)
            pn = dict(params)
            pn["arquivo"] = str(fonte)
            pn["modo"] = modo
            destino = MODOS[modo](sub, pn)
            if tipo == "separar":
                achadas = FAIXAS_DA_TAREFA.pop(sub, None) or [destino]
                saidas = _saidas_nomeadas(achadas)
                saidas.setdefault("saida", str(destino))
            elif tipo == "legenda":
                # A legenda e EFEITO COLATERAL: o .srt vai pro disco e o AUDIO
                # atravessa o node intacto. Sem isto o fluxo "limpar e
                # legendar" entregava o .srt como faixa final e o audio
                # melhorado se perdia (visto no teste de 20/08) — e o painel
                # ainda tentaria por um .srt numa faixa de audio.
                #
                # Regra dele (19/08): "o .srt sai na pasta do PROPRIO arquivo,
                # pra achar facil". No fluxo a entrada do node e um
                # intermediario com nome de hash na pasta trabalho — entao o
                # .srt e COPIADO pro lado do arquivo ORIGINAL do fluxo, com o
                # nome dele.
                srt_final = destino
                try:
                    original = Path(p.get("arquivo", ""))
                    if original.exists():
                        srt_final = original.with_suffix(".srt")
                        # MESMA guarda do modo_legenda (1526-1527): legenda que
                        # ja esta la pode ser a que ELE corrigiu na mao. Nunca
                        # gravar por cima. Achado da auditoria de 20/08.
                        if srt_final.exists():
                            srt_final = original.with_name(
                                f"{original.stem} (DUBX).srt")
                        shutil.copy2(destino, srt_final)
                except Exception:
                    srt_final = destino     # sem original, fica onde nasceu
                saidas = {"srt": str(srt_final), "saida": str(fonte)}
            else:
                saidas = {"saida": str(destino)}

        resultados[nid] = saidas
        ultimo_no = nid
        ultimo_arquivo = saidas.get("saida") or next(iter(saidas.values()))
        if usar_cache:
            cache[chave] = saidas
            _cache_grava(cache)

    if not ultimo_arquivo:
        raise RuntimeError("o fluxo terminou sem produzir arquivo nenhum")

    # ⭐ TODAS as saidas da ultima etapa sao entrega, nao so a "saida".
    # Defeito achado por ele no primeiro uso real (20/08): o "so stems" punha
    # UMA faixa na timeline em vez de tres, porque so a saida principal chegava
    # no node final. Quando a ultima etapa e um `separar`, o resultado E o
    # cacho inteiro (Fala + Musica + Efeitos), nao o primeiro arquivo dele.
    if not todas_as_saidas:
        ultimas = resultados.get(ultimo_no) or {}
        todas_as_saidas = []
        for v in ultimas.values():          # dedup: voz1 e "Plexus" sao o mesmo
            if v and v not in todas_as_saidas:
                todas_as_saidas.append(v)
        if not todas_as_saidas:
            todas_as_saidas = [ultimo_arquivo]
    FAIXAS_DA_TAREFA[t] = [str(x) for x in todas_as_saidas]
    _limpa_subtarefas(t)
    return Path(ultimo_arquivo)


# Pastas que NUNCA entram na faxina, sob nenhuma circunstancia:
#   originais/     = backup do arquivo dele ANTES de qualquer regravacao
#   base_ambiente/ = a base SECA de cada arquivo; sem ela reambientar vira
#                    reverb sobre reverb
#   lote_premiere/ = area do painel
INTOCAVEIS = ("originais", "base_ambiente", "lote_premiere")


def _protegidos():
    """Caminhos que NAO podem ser apagados.

    Tres fontes, e faltar qualquer uma causa estrago diferente:
      · cache do Fluxo  -> apagar invalida o cache calado, e o proximo
                           "refazer so a ultima etapa" vira "refazer tudo"
      · HISTORICO       -> e o que ele toca/baixa na tela; apagar deixa o
                           item la, morto, apontando pro vazio
      · originais/      -> backup do arquivo dele. Sagrado."""
    fixos = set()
    for saidas in _cache_le().values():
        for v in (saidas or {}).values():
            if v:
                fixos.add(str(v).lower())
    try:
        for item in le_historico():
            for campo in ("saida", "previa", "antes"):
                v = item.get(campo)
                if v:
                    fixos.add(str(v).lower())
    except Exception:
        # historico ilegivel: melhor proteger DEMAIS do que apagar de menos
        pass
    return fixos


def _mede_lixo():
    """Tudo dentro de trabalho que nao esta protegido nem mora em pasta intocavel.

    Antes so olhava 6 sufixos de arquivo de passagem e enxergava 365 MB de
    6,6 GB reais (medido 20/08). O que enche o disco sao os derivados grandes
    — _final.mp4 do remux, wav limpo, stems de teste — que ja cumpriram o
    papel e nao estao no historico nem no cache."""
    fixos = _protegidos()
    alvos, total = [], 0
    if not TRAB.exists():
        return alvos, total
    for f in TRAB.rglob("*"):
        if not f.is_file():
            continue
        try:
            rel = f.relative_to(TRAB)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in INTOCAVEIS:
            continue
        if f.name == "cache_fluxo.json":
            continue
        if str(f).lower() in fixos:
            continue
        try:
            total += f.stat().st_size
            alvos.append(f)
        except OSError:
            pass
    return alvos, total


def _cache_rebatiza(antigo, novo):
    """O batismo MOVE a saida final; o cache do fluxo guardava o caminho
    velho e o node pesado re-executava toda rodada (cetico de 24/08).
    Reaponta a entrada do cache pro arquivo vivo."""
    if not antigo or str(antigo) == str(novo):
        return
    try:
        d = _cache_le()
        na = os.path.normcase(str(antigo))
        mudou = False
        for saidas in d.values():
            for ch, v in list(saidas.items()):
                if os.path.normcase(str(v)) == na:
                    saidas[ch] = str(novo)
                    mudou = True
        if mudou:
            _cache_grava(d)
    except Exception:
        pass          # cache e conforto, nunca derruba a entrega


def _limpa_intermediarios(t=None, poupar=None):
    """Apaga o que _mede_lixo apontou. Com `t`, so daquela tarefa;
    `poupar` e um conjunto de caminhos (minusculos) que ficam."""
    alvos, _ = _mede_lixo()
    if t:
        # a tarefa assina tanto arquivos soltos ({t}_x.wav) quanto PASTAS
        # ({t}n2_separadas\Fala.wav) — olhar o primeiro pedaco do caminho
        alvos = [f for f in alvos
                 if f.relative_to(TRAB).parts[0].startswith(t)]
    if poupar:
        alvos = [f for f in alvos if str(f).lower() not in poupar]
    liberado = 0
    for f in alvos:
        try:
            tam = f.stat().st_size
            f.unlink()
            liberado += tam
        except OSError:
            pass
    # pastas que ficaram vazias depois da limpeza
    for d in sorted((d for d in TRAB.rglob("*") if d.is_dir()),
                    key=lambda x: len(x.parts), reverse=True):
        rel = d.relative_to(TRAB)
        if rel.parts and rel.parts[0] in INTOCAVEIS:
            continue
        try:
            next(d.iterdir())
        except StopIteration:
            try:
                d.rmdir()
            except OSError:
                pass
        except OSError:
            pass
    return liberado


def _limpa_subtarefas(t):
    """Cada node roda com um id proprio ("<t>n3") pra ter espaco de trabalho
    separado, e esses ids ficam pendurados em TAREFAS como "rodando" pra sempre
    — o `trabalhar()` so finaliza o id do fluxo.

    Isso NAO e cosmetico: o CONTEXTO manda conferir /api/tarefas antes de
    reiniciar o servidor, e um fantasma ali faz parecer que tem trabalho em voo.
    Eu mesmo cai nessa em 20/08."""
    for chave in [k for k in TAREFAS if k.startswith(f"{t}n")]:
        TAREFAS.pop(chave, None)
        FAIXAS_DA_TAREFA.pop(chave, None)
        PICO_DA_TAREFA.pop(chave, None)


MODOS = {"melhorar": modo_melhorar, "trocar": modo_trocar,
         "falar": modo_falar, "treinar": modo_treinar,
         "dublar": modo_dublar, "separar": modo_separar,
         "melhorar_no_lugar": modo_melhorar_no_lugar,
         "trocar_no_lugar": modo_trocar_no_lugar,
         "ambientar_no_lugar": modo_ambientar_no_lugar,
         "treinar_premiere": modo_treinar_premiere,
         "legenda": modo_legenda,
         "fluxo": modo_fluxo}

# Modos do APP que aceitam video na entrada e devolvem video na saida.
# Os "_no_lugar" ficam de fora: eles ja regravam o proprio arquivo.
MODOS_VIDEO = {"melhorar", "trocar", "dublar", "separar"}

# Modos que ganham o comparador A/B (antes x depois) no player.
MODOS_COMPARA = {"melhorar", "trocar", "dublar", "separar"}


def trabalhar(t, p):
    # cada tarefa roda na sua propria thread: e por aqui que o /api/cancelar
    # descobre QUAL processo filho e de QUAL tarefa
    TAREFA_DA_THREAD[threading.get_ident()] = t
    try:
        TAREFAS[t] = {"estado": "rodando", "mensagem": "processando…"}
        # Trecho marcado na onda: corta ANTES de tudo — o resto do caminho
        # (inclusive o "antes" do A/B) passa a ser so o pedaco escolhido.
        if p.get("fim") and p["modo"] in MODOS_VIDEO:
            TAREFAS[t] = {"estado": "rodando", "mensagem": "cortando o trecho…"}
            p = dict(p)
            # o corte vira "{t}_trecho.wav" — sem guardar o nome do CLIPE o
            # batismo entregaria "<hash>_trecho - dublado.wav" (cetico 24/08)
            p["arquivo_original"] = p["arquivo"]
            p["arquivo"] = str(_cortar_trecho(t, p["arquivo"],
                                              p.get("inicio", 0), p["fim"]))
        destino = MODOS[p["modo"]](t, p)
        if p["modo"].startswith("treinar"):
            # Treino nao devolve audio pra tocar — devolve uma voz nova na lista.
            recado = "voz pronta e já disponível nos outros modos"
            if AVISOS.get(t):
                recado += " (avisos: " + " · ".join(AVISOS.pop(t)) + ")"
            TAREFAS[t] = {"estado": "pronto", "treino": True,
                          "nome": p.get("nome", ""), "mensagem": recado}
            registra_historico({"id": t, "quando": _agora(),
                                "modo": p["modo"], "rotulo": _rotulo_de(p),
                                "saida": None})
        else:
            # Entrada em VIDEO: entrega VIDEO (video copiado sem recodificar +
            # audio novo). O wav vira "previa" — e ele que toca no player e
            # desenha a onda; o baixar leva o video.
            previa, aviso = None, None
            if p["modo"] in MODOS_VIDEO:
                try:
                    _passo(t, 1, 1, "montando o vídeo (sem recodificar)…")
                    v = _com_video(t, p.get("arquivo", ""), destino)
                    if v:
                        previa, destino = destino, v
                except Exception as exc:
                    aviso = f"o áudio ficou pronto, mas a remontagem do vídeo falhou: {exc}"
            # Comparador A/B (pedido de 16/08): guarda o ORIGINAL tocável e o
            # LUFS dos dois lados. A lição mais cara do projeto (14/08): sem
            # volume igualado a comparação mede o volume, não a qualidade —
            # o painel usa esses números pra atenuar o mais alto.
            # BATISMO + consertos dos ceticos (24/08): a FONTE nunca e
            # movida (node legenda repassa a propria entrada), a previa de
            # video tambem ganha nome humano, e faixas/cache do fluxo sao
            # REAPONTADOS — sem isso o painel importava caminho morto e o
            # node pesado re-rodava sem cache.
            antigo = str(destino)
            if os.path.normcase(antigo) != os.path.normcase(str(p.get("arquivo") or "")):
                destino = _batiza_entrega(t, destino, _rotulo_de(p))
            if previa:
                previa = _batiza_entrega(t, previa, _rotulo_de(p) + " (áudio)")
            faixas_t = FAIXAS_DA_TAREFA.pop(t, None)
            if faixas_t:
                faixas_t = [str(destino) if os.path.normcase(str(x)) == os.path.normcase(antigo)
                            else str(x) for x in faixas_t]
            _cache_rebatiza(antigo, str(destino))
            antes = ganhos = None
            if p["modo"] in MODOS_COMPARA:
                # o arquivo que ELE soltou (video inteiro quando for video —
                # assim o A/B troca video por video, com imagem e tudo)
                antes = p.get("arquivo") or AUDIO_FONTE.get(t)
                if antes and Path(str(antes)).exists():
                    ganhos = {"antes": medir_lufs(Path(str(antes))),
                              "depois": medir_lufs(Path(str(previa or destino)))}
                else:
                    antes = None
            TAREFAS[t] = {"estado": "pronto", "saida": str(destino),
                          # caminhos em disco de TODAS as faixas produzidas —
                          # o painel do Premiere importa daqui direto.
                          "faixas": faixas_t,
                          "nome": Path(destino).name,
                          "amigavel": _nome_amigavel(_rotulo_de(p), destino),
                          "rotulo": _rotulo_de(p),
                          "previa": str(previa) if previa else None,
                          "antes": str(antes) if antes else None,
                          "lufs": ganhos, "aviso": aviso}
            # P32: pico casado com o original — numeros no historico, frase na mensagem final
            pico = PICO_DA_TAREFA.pop(t, None) or {}
            if pico:
                TAREFAS[t].update(pico)
                TAREFAS[t]["mensagem"] = _recado_pico(pico)
            if AVISOS.get(t):
                TAREFAS[t]["aviso"] = " · ".join(filter(None, [aviso] + AVISOS.pop(t)))
            registra_historico({"id": t, "quando": _agora(),
                                **pico,
                                "modo": p["modo"], "rotulo": _rotulo_de(p),
                                "saida": str(destino),
                                "previa": str(previa) if previa else None,
                                "antes": str(antes) if antes else None,
                                "lufs": ganhos, "modelo": _modelo_de(p),
                                # 29/10 (Kakashi 16:35 [00:00] «não sei quais foram as configurações desse»): os ajustes
                                # da troca ficam no historico, pra prévia mostrar e pro «regerar» repetir
                                "ajustes": {k: p.get(k) for k in AJUSTES_TROCA if k in p}})
        AUDIO_FONTE.pop(t, None)
        # FAXINA AUTOMATICA (opcional, pela tela): apaga as passagens DESTA
        # tarefa assim que ela termina. So depois do registra_historico —
        # o historico protege a entrega — e poupando as faixas que o painel
        # ainda vai importar pra timeline.
        if _config_le().get("faxina_auto"):
            try:
                vivas = set()
                info = TAREFAS.get(t) or {}
                for v in (info.get("faixas") or []):
                    vivas.add(str(v).lower())
                for campo in ("saida", "previa", "antes"):
                    if info.get(campo):
                        vivas.add(str(info[campo]).lower())
                _limpa_intermediarios(t, poupar=vivas)
            except Exception:
                pass   # faxina nunca pode derrubar a entrega
    except Cancelado:
        # nao e erro: ele mandou parar. Tela vermelha aqui so assustaria.
        TAREFAS[t] = {"estado": "cancelado", "mensagem": "cancelado por você"}
        for chave in [k for k in TAREFAS if k.startswith(f"{t}n")]:
            TAREFAS.pop(chave, None)
    except Exception as exc:
        # o filho morto pelo taskkill volta como erro comum ("codigo 1"),
        # nao como Cancelado — por isso a checagem tambem entra aqui.
        if t in CANCELADAS:
            TAREFAS[t] = {"estado": "cancelado", "mensagem": "cancelado por você"}
        else:
            TAREFAS[t] = {"estado": "erro", "mensagem": str(exc)}
        # fluxo que quebrou no meio tambem deixa sub-tarefa fantasma
        for chave in [k for k in TAREFAS if k.startswith(f"{t}n")]:
            TAREFAS.pop(chave, None)
    finally:
        TAREFA_DA_THREAD.pop(threading.get_ident(), None)
        PROCESSOS.pop(t, None)
        CANCELADAS.discard(t)
        PICO_DA_TAREFA.pop(t, None)


# ------------------------------------------------------------------ servidor
class Mao(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _envia(self, codigo, corpo, tipo="application/json; charset=utf-8"):
        dados = corpo if isinstance(corpo, bytes) else json.dumps(
            corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def _envia_arquivo(self, caminho, baixar=False, nome=None):
        """Manda um arquivo do disco em pedacos — video de 2 GB nao cabe na
        memoria (o _envia le tudo de uma vez). `nome` e o nome AMIGAVEL com
        que o arquivo chega no PC dele (o do disco tem hash).

        ⚠️ ACEITA RANGE (bug pago em 19/08): sem isto o player NAO PULA. Ele
        clicava no meio da onda e o audio recomecava do zero — porque uma
        resposta 200 sem `Accept-Ranges` nao e "seekable" pro Chrome: ele so
        sabe tocar do inicio. Agora respondemos 206 com a fatia pedida, que e
        o que qualquer player espera de servidor de midia. Vale pro <audio>,
        pro <video> e pra mesa de mistura."""
        caminho = Path(caminho)
        total = caminho.stat().st_size
        tipo = mimetypes.guess_type(caminho.name)[0] or "application/octet-stream"

        ini, fim, parcial = 0, max(0, total - 1), False
        faixa = (self.headers.get("Range") or "").strip()
        if faixa.startswith("bytes=") and total:
            trecho = faixa[6:].split(",")[0].strip()   # so a 1a faixa: basta
            a, _, b = trecho.partition("-")
            try:
                if a:
                    ini = int(a)
                    fim = min(int(b), total - 1) if b else total - 1
                elif b:                                # bytes=-N: o final
                    ini, fim = max(0, total - int(b)), total - 1
                parcial = 0 <= ini <= fim < total
            except ValueError:
                parcial = False
            if not parcial:
                self.send_response(416)                # pedido impossivel
                self.send_header("Content-Range", f"bytes */{total}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        self.send_response(206 if parcial else 200)
        self.send_header("Content-Type", tipo)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fim - ini + 1 if total else 0))
        if parcial:
            self.send_header("Content-Range", f"bytes {ini}-{fim}/{total}")
        self.send_header("Cache-Control", "no-store")
        if baixar:
            visivel = (nome or caminho.name).replace('"', "'")
            if Path(visivel).suffix.lower() != caminho.suffix.lower():
                visivel += caminho.suffix
            # filename* leva o UTF-8 (acento e travessao) sem quebrar navegador antigo
            self.send_header("Content-Disposition",
                             "attachment; filename=\"%s\"; filename*=UTF-8''%s"
                             % (visivel.encode("ascii", "ignore").decode() or "dubx",
                                quote(visivel)))
        self.end_headers()
        restante = fim - ini + 1 if total else 0
        with open(caminho, "rb") as f:
            f.seek(ini)
            while restante > 0:
                bloco = f.read(min(1024 * 1024, restante))
                if not bloco:
                    break
                self.wfile.write(bloco)
                restante -= len(bloco)

    def _arquivo_da_tarefa(self, t, campo):
        """Caminho de uma geracao. campo: "previa" (o que toca), "saida" (o
        final, pro download) ou "antes" (o original, pro comparador A/B).
        Procura no TAREFAS e cai no historico — assim os botoes continuam
        funcionando depois de reiniciar o servidor."""
        item = TAREFAS.get(t) or {}
        if not item.get("saida"):
            item = next((i for i in le_historico() if i.get("id") == t), {})
        if campo == "antes":
            alvo = item.get("antes")
        else:
            alvo = (item.get("previa") if campo == "previa" else None) or item.get("saida")
        return Path(alvo) if alvo else None

    def _faixas_de(self, t):
        """Descreve as faixas de uma separacao: original + voz 1 + voz 2.

        Ouvir sempre puxa o WAV (papel=previa quando a saida virou video,
        senao a propria saida); baixar leva o que existir de melhor."""
        hist = le_historico()
        def item(i):
            return next((x for x in hist if x.get("id") == i), None)

        # Os ids sao hexadecimais e SUFIXOS_VOZ comeca em "b" — decidir so por
        # "termina com letra de sufixo" mutilava todo id acabado em b/c/d/e/f e
        # a separacao aparecia como inexistente. A pergunta certa nao e "termina
        # com sufixo?" e sim "existe um PAI se eu tirar essa letra?".
        # (achado meu em 20/08, junto do conserto do SUFIXOS_VOZ)
        base = t
        if len(t) > 1 and t[-1] in SUFIXOS_VOZ and item(t[:-1]):
            base = t[:-1]

        principal = item(base)
        if not principal:
            return {"erro": "não achei essa separação", "faixas": []}
        # voz 1 no id base, as demais em <base>b, <base>c... enquanto existirem
        extras = []
        for suf in SUFIXOS_VOZ:
            it = item(f"{base}{suf}")
            if not it:
                break
            extras.append((f"{base}{suf}", it))

        rotulo = str(principal.get("rotulo") or "separacao")
        # "separar · arquivo.mp4 · voz 1" -> "arquivo" (nome bonito pro download)
        pedacos = [p.strip() for p in rotulo.split("·")]
        nome_base = next((p for p in pedacos if "." in p), pedacos[-1] if pedacos else "audio")
        nome_base = Path(nome_base).stem or "audio"

        faixas = []
        antes = principal.get("antes")
        if antes and Path(str(antes)).exists():
            faixas.append({"papel": "antes", "id": base, "nome": "Original",
                           # caminho EM DISCO: o painel do Premiere importa o
                           # arquivo de verdade; so o app usa as URLs de stream.
                           "caminho": str(antes),
                           "arquivo": f"{nome_base} — original{Path(str(antes)).suffix}",
                           "ouvir": f"/audio-h?id={base}&papel=antes",
                           "baixar": f"/baixar-h?id={base}&papel=antes",
                           "video": Path(str(antes)).suffix.lower() in
                                    (".mp4", ".mov", ".mkv", ".webm", ".avi")})
        for i, (idf, it) in enumerate([(base, principal)] + extras, 1):
            if not it:
                continue
            som = it.get("previa") or it.get("saida")
            if not som or not Path(str(som)).exists():
                continue
            temvid = bool(it.get("previa"))    # previa existe => saida e video
            # nome REAL da faixa quando o arquivo tem nome de gente/stem
            # (Plexus.wav, Pedaleira.wav, Music.wav...) — "Voz 3" nao diz nada
            bonito = {"music": "Música", "sfx": "Efeitos", "speech": "Fala"}
            # pos-batismo o arquivo chama "V007 - Música.wav": o papel esta
            # no sufixo depois do ultimo " - " (arquivo antigo cai no todo)
            cru = Path(str(som)).stem.rsplit(" - ", 1)[-1]
            nome_faixa = (f"Voz {i}" if cru.lower().startswith("voz")
                          else bonito.get(cru.lower(), cru))
            faixas.append({
                "papel": f"voz{i}", "id": idf, "nome": nome_faixa,
                "caminho": str(som),          # em disco, pro painel do Premiere
                "arquivo": f"{nome_base} — {nome_faixa}{Path(str(som)).suffix}",
                "arquivo_video": (f"{nome_base} — voz {i}"
                                  f"{Path(str(it['saida'])).suffix}") if temvid else None,
                "ouvir": f"/audio-h?id={idf}&papel={'previa' if temvid else 'saida'}",
                "baixar": f"/baixar-h?id={idf}&papel={'previa' if temvid else 'saida'}",
                "baixar_video": f"/baixar-h?id={idf}" if temvid else None,
                "video": temvid})
        return {"faixas": faixas, "base": nome_base}

    def do_GET(self):
        rota = urlparse(self.path)
        cam = rota.path

        if cam in ("/", "/index.html"):
            arq = UI / "index.html"
            if not arq.exists():
                return self._envia(500, {"erro": "ui/index.html nao encontrado"})
            return self._envia(200, arq.read_bytes(), "text/html; charset=utf-8")

        if cam == "/favicon.ico":
            # Sem isto a janela chrome --app fica com o icone de mundo na
            # barra de tarefas; o Chrome tira o icone da janela do favicon.
            ico = BASE / "dubx.ico"
            if ico.exists():
                return self._envia(200, ico.read_bytes(), "image/x-icon")
            return self._envia(404, {"erro": "sem icone"})

        if cam == "/api/vozes":
            return self._envia(200, {"vozes": listar_vozes()})

        if cam == "/api/presets":
            return self._envia(200, {"presets": le_presets()})

        if cam == "/api/el-vozes":
            # 29/10 (P30): vozes da conta ElevenLabs dele pro dropdown do Trocar. Sem chave/sem rede -> lista
            # vazia + erro legivel (200 de proposito: o painel so mostra o motivo, nao trata como falha do DUBX).
            try:
                return self._envia(200, {"vozes": el_vozes()})
            except Exception as exc:
                return self._envia(200, {"vozes": [], "erro": str(exc)[:300]})

        if cam == "/api/hf-token":
            # nunca devolve o valor: so se esta configurado
            return self._envia(200, {"tem": ARQ_HF_TOKEN.exists()})

        if cam == "/api/ambientes":
            # Presets DSP prontos (15/08 noite, substitui a lista de cadeias
            # capturadas — nenhuma chegou a existir). "detalhes" carrega a
            # descricao pro painel mostrar como dica.
            ambs = listar_ambientes()
            return self._envia(200, {"ambientes": [a["nome"] for a in ambs],
                                     "detalhes": [{"nome": a["nome"],
                                                   "descricao": a.get("descricao", ""),
                                                   "tipo": a.get("tipo", "dsp")}
                                                  for a in ambs]})

        if cam == "/api/ambiente":
            nome = parse_qs(rota.query).get("nome", [""])[0]
            arq = BASE / "ambientes" / f"{nome}.json"
            if not arq.exists():
                return self._envia(404, {"erro": f"ambiente '{nome}' nao existe"})
            return self._envia(200, arq.read_bytes())

        if cam == "/api/estado":
            t = parse_qs(rota.query).get("t", [""])[0]
            return self._envia(200, TAREFAS.get(t, {"estado": "desconhecido"}))

        if cam == "/api/tarefas":
            # Visao geral das tarefas DESTE processo. Existe por causa de
            # 16/08: um restart do servidor com tarefa em voo orfanou o
            # rastreamento (o motor terminou, o aviso morreu). QUALQUER
            # automacao que pense em reiniciar o servidor confere aqui antes.
            return self._envia(200, {"tarefas": {
                t: e.get("estado", "?") for t, e in TAREFAS.items()}})

        if cam.startswith(("/audio/", "/video/", "/baixar/", "/antes/")):
            # /audio/ = o que TOCA no player de onda (wav da previa);
            # /video/ = a saida TOCANDO dentro do app (sem forcar download);
            # /baixar/ = o que o Luan LEVA (attachment);
            # /antes/ = o ORIGINAL, pro comparador A/B.
            raiz = next(r for r in ("/audio/", "/video/", "/baixar/", "/antes/")
                        if cam.startswith(r))
            campo = {"/audio/": "previa", "/video/": "saida",
                     "/baixar/": "saida", "/antes/": "antes"}[raiz]
            t = cam.split(raiz, 1)[1]
            arq = self._arquivo_da_tarefa(t, campo)
            if not arq or not arq.exists():
                return self._envia(404, {"erro": "arquivo nao encontrado"})
            dono = TAREFAS.get(t) or next(
                (i for i in le_historico() if i.get("id") == t), {})
            return self._envia_arquivo(arq, baixar=(raiz == "/baixar/"),
                                       nome=dono.get("amigavel"))

        if cam == "/api/historico":
            hist = le_historico()
            # 05/11: melhorar com seco + Sidon guardados = cartao com UMA faixa e slider de reconstrucao ao vivo
            for i in hist:
                if str(i.get("modo", "")).startswith("melhorar") and (TRAB / f"{i.get('id')}_partes" / "molhado.wav").exists():
                    i["partes"] = True
            return self._envia(200, {"historico": hist})

        if cam == "/audio-partes":
            q = parse_qs(rota.query)
            base = re.sub(r"[^0-9a-zA-Z_-]", "", q.get("id", [""])[0])
            lado = q.get("lado", [""])[0]
            arq = TRAB / f"{base}_partes" / f"{lado}.wav"
            if not base or lado not in ("seco", "molhado") or not arq.exists():
                return self._envia(404, {"erro": "partes nao existem"})
            return self._envia_arquivo(arq)

        if cam in ("/audio-h", "/baixar-h"):
            # Item do HISTORICO (sobrevive a restart). Mesma divisao: ouvir
            # pega a previa; baixar leva o arquivo final (video, se houver).
            # &papel= escolhe explicitamente qual arquivo do item se quer
            # (a mesa de mistura do Separar precisa do WAV mesmo tendo video).
            q = parse_qs(rota.query)
            t = q.get("id", [""])[0]
            papel = q.get("papel", [""])[0]
            item = next((i for i in le_historico() if i.get("id") == t), None)
            if not item:
                return self._envia(404, {"erro": "não achei no histórico"})
            baixar = cam == "/baixar-h"
            if papel in ("antes", "previa", "saida"):
                alvo = item.get(papel)
            else:
                alvo = (None if baixar else item.get("previa")) or item.get("saida")
            arq = Path(alvo) if alvo else None
            if not arq or not arq.exists():
                return self._envia(404, {"erro": "arquivo nao existe mais"})
            amigo = item.get("amigavel") or _nome_amigavel(item.get("rotulo"), arq)
            if papel == "antes":
                amigo = _nome_amigavel(f"{Path(str(item.get('rotulo'))).stem} original", arq)
            return self._envia_arquivo(arq, baixar=baixar, nome=amigo)

        if cam == "/api/lixo":
            alvos, total = _mede_lixo()
            return self._envia(200, {"arquivos": len(alvos),
                                     "mb": round(total / 1048576)})

        if cam == "/api/config":
            return self._envia(200, _config_le())

        if cam == "/som/pronto.mp3":
            # o app do navegador toca sozinho; o painel UXP nao consegue
            # e por isso pede pelo /api/plim
            if SOM_PRONTO_MP3.exists():
                return self._envia_arquivo(SOM_PRONTO_MP3)
            return self._envia(404, {"erro": "som nao encontrado"})

        if cam == "/api/fluxos":
            # Os fluxos prontos da pasta DUBX/fluxos. O painel e o app so
            # mostram a lista e mandam de volta o `grafo` inteiro — o servidor
            # nao guarda estado de qual fluxo esta escolhido.
            saida = []
            for f in sorted(FLUXOS.glob("*.json")) if FLUXOS.exists() else []:
                try:
                    g = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                saida.append({"chave": f.stem,
                              "passos_lista": g.get("passos") or [],
                              "nome": g.get("nome") or f.stem,
                              "descricao": g.get("descricao") or "",
                              "pede": g.get("pede") or [],
                              "passos": len(g.get("passos") or g.get("nodes") or []),
                              "grafo": g})
            return self._envia(200, {"fluxos": saida, "catalogo": CATALOGO})

        if cam == "/api/faixas":
            # Mesa de mistura do Separar (pedido de 16/08): as duas vozes e o
            # original como FAIXAS — pra mutar/solar e ouvir junto do video.
            t = parse_qs(rota.query).get("id", [""])[0]
            return self._envia(200, self._faixas_de(t))

        return self._envia(404, {"erro": "rota desconhecida"})

    def do_POST(self):
        cam = urlparse(self.path).path
        tam = int(self.headers.get("Content-Length", 0))
        corpo = self.rfile.read(tam) if tam else b""

        if cam == "/api/enviar":
            nome = self.headers.get("X-Nome", "audio.wav")
            seguro = "".join(c for c in nome if c.isalnum() or c in "._- ")[-60:]
            TRAB.mkdir(parents=True, exist_ok=True)
            destino = TRAB / f"{uuid.uuid4().hex[:8]}_{seguro or 'audio.wav'}"
            destino.write_bytes(corpo)
            return self._envia(200, {"arquivo": str(destino), "nome": seguro})

        if cam == "/api/tocar":
            # Preview do painel: o UXP nao reproduz audio, entao o servidor
            # toca na saida do sistema. So WAV (winsound) e so de dentro do
            # DUBX — o painel nunca precisa de mais que isso.
            try:
                dados = json.loads(corpo.decode("utf-8"))
                arq = Path(str(dados.get("arquivo", "")))
            except Exception:
                return self._envia(400, {"erro": "json invalido"})
            try:
                arq.resolve().relative_to(BASE.resolve())
            except ValueError:
                return self._envia(400, {"erro": "so toco arquivos do DUBX"})
            if not arq.exists():
                return self._envia(404, {"erro": "arquivo nao existe mais"})
            if arq.suffix.lower() != ".wav":
                return self._envia(400, {"erro": "so consigo tocar WAV"})
            # tocar DO PONTO clicado na onda: o winsound nao busca posicao,
            # entao o ffmpeg corta do ponto pedido e o resto toca inteiro
            alvo = arq
            try:
                inicio = float(dados.get("inicio", 0) or 0)
            except (TypeError, ValueError):
                inicio = 0.0
            if inicio > 0.05:
                corte = TRAB / "_previa_seek.wav"
                try:
                    roda([achar_ffmpeg(), "-y", "-ss", f"{inicio:.3f}",
                          "-i", arq, "-c:a", "pcm_s16le", corte])
                    alvo = corte
                except Exception:
                    alvo = arq   # nao cortou: toca do comeco, melhor que nada
            if winsound:
                try:
                    winsound.PlaySound(str(alvo),
                                       winsound.SND_FILENAME | winsound.SND_ASYNC)
                except Exception as exc:
                    return self._envia(500, {"erro": str(exc)})
            return self._envia(200, {"tocando": arq.name})

        if cam == "/api/onda":
            # Picos pra desenhar a onda estilo Artlist no painel (a mesma
            # receita do FINDX: UM ffmpeg entrega PCM mono 8 kHz no stdout).
            try:
                dados = json.loads(corpo.decode("utf-8"))
                arq = Path(str(dados.get("arquivo", "")))
            except Exception:
                return self._envia(400, {"erro": "json invalido"})
            try:
                arq.resolve().relative_to(BASE.resolve())
            except ValueError:
                return self._envia(400, {"erro": "so leio arquivos do DUBX"})
            if not arq.exists():
                return self._envia(404, {"erro": "arquivo nao existe mais"})
            import array
            r = subprocess.run([str(achar_ffmpeg()), "-v", "error", "-i", arq,
                                "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                               capture_output=True, creationflags=SEM_JANELA)
            bruto = r.stdout
            amostras = len(bruto) // 2
            if not amostras:
                return self._envia(500, {"erro": "nao consegui ler o audio"})
            a = array.array("h")
            a.frombytes(bruto[:amostras * 2])
            N = 96
            passo = max(1, amostras // N)
            picos = []
            for i in range(N):
                seg = a[i * passo:(i + 1) * passo]
                pico = max((abs(x) for x in seg), default=0)
                picos.append(round(pico / 32768, 3))
            teto = max(picos) or 1.0
            picos = [round(x / teto, 3) for x in picos]   # normaliza pro desenho
            return self._envia(200, {"picos": picos, "dur": round(amostras / 8000, 2)})

        if cam == "/api/parar":
            if winsound:
                try:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except Exception:
                    pass
            return self._envia(200, {"parado": True})

        if cam == "/api/cancelar":
            # {"tarefa": "<id>"} para UMA · {} ou {"tudo": true} para TODAS.
            # Some o som da previa junto: cancelar tem que calar a maquina.
            try:
                p = json.loads(corpo.decode("utf-8")) if corpo else {}
            except Exception:
                p = {}
            if winsound:
                try:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except Exception:
                    pass
            parados = cancelar(None if p.get("tudo") else p.get("tarefa"))
            return self._envia(200, {"cancelados": parados})

        if cam == "/api/remisturar":
            # 05/11: slider de reconstrucao (app e painel). Direto, sem fila: ~1 s, e nao cria geracao nova
            # a cada mexida. {id, dose[, arquivo]} — sem arquivo, regrava a saida da propria geracao.
            try:
                q = json.loads(corpo.decode("utf-8"))
                return self._envia(200, remistura_geracao(str(q.get("id", "")), int(q.get("dose", 100)),
                                                          q.get("arquivo"), q.get("lufs", -16)))
            except Exception as exc:
                return self._envia(500, {"erro": str(exc)})

        if cam == "/api/geracao-ocultar":
            # GERACOES (07/10): o ✕ do cartao so ESCONDE da grade — nao apaga
            # arquivo nenhum. Ctrl+Z no app manda ocultar=false e ela volta.
            try:
                q = json.loads(corpo.decode("utf-8"))
            except Exception:
                return self._envia(400, {"erro": "json invalido"})
            alvo, ocultar = str(q.get("id", "")), bool(q.get("ocultar", True))
            with _TRAVA_HIST:
                try:
                    itens = json.loads(HISTORICO.read_text(encoding="utf-8"))
                except Exception:
                    itens = []
                achou = False
                for it in itens:
                    if it.get("id") == alvo:
                        it["oculta"] = ocultar
                        achou = True
                if achou:
                    HISTORICO.write_text(json.dumps(itens, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
            return self._envia(200 if achou else 404, {"ok": achou})

        if cam == "/api/limpar-lixo":
            liberado = _limpa_intermediarios()
            return self._envia(200, {"mb": round(liberado / 1048576)})

        if cam == "/api/config":
            try:
                novos = json.loads(corpo.decode("utf-8"))
            except Exception:
                return self._envia(400, {"erro": "json invalido"})
            return self._envia(200, _config_grava(novos))

        if cam == "/api/apagar-voz":
            # Apaga a voz do PC INTEIRO: biblioteca, registro de treino e
            # dataset. O painel ja pediu confirmacao; aqui so executa.
            try:
                dados = json.loads(corpo.decode("utf-8"))
                nome = str(dados.get("nome", "")).strip()
            except Exception:
                return self._envia(400, {"erro": "json invalido"})
            # nome vira caminho: so caracteres inofensivos, nada de .. ou barra
            if not nome or any(c not in " _-" and not c.isalnum() for c in nome):
                return self._envia(400, {"erro": "nome invalido"})
            if not (VOZES / nome).exists():
                return self._envia(404, {"erro": f"voz '{nome}' nao existe"})
            apagadas = []
            for pasta in (VOZES / nome, TREINO / "logs" / nome,
                          TREINO / "datasets" / nome):
                if pasta.exists():
                    shutil.rmtree(pasta, ignore_errors=True)
                    apagadas.append(str(pasta))
            # o ajuste fino do Seed-VC mora fora da pasta da voz
            fino = MOTOR / "seedvc" / "runs" / nome
            if fino.exists():
                shutil.rmtree(fino, ignore_errors=True)
                apagadas.append(str(fino))
            return self._envia(200, {"apagado": nome, "pastas": apagadas})

        if cam == "/api/renomear-voz":
            # RENOMEAR (pedido dele, 24/08: "coloca nas configuracoes pra eu
            # poder editar minhas vozes, renomear ou excluir"). Move as tres
            # pastas que carregam o nome; a de vozes/ e a unica obrigatoria.
            try:
                dados = json.loads(corpo.decode("utf-8"))
                de = str(dados.get("de", "")).strip()
                para = str(dados.get("para", "")).strip()
            except Exception:
                return self._envia(400, {"erro": "json invalido"})
            ruim = lambda s: (not s) or any(c not in " _-" and not c.isalnum() for c in s)
            if ruim(de) or ruim(para):
                return self._envia(400, {"erro": "nome invalido"})
            if not (VOZES / de).exists():
                return self._envia(404, {"erro": f"voz '{de}' nao existe"})
            if (VOZES / para).exists():
                return self._envia(409, {"erro": f"ja existe uma voz '{para}'"})
            movidas = []
            for raiz in (VOZES, TREINO / "logs", TREINO / "datasets",
                         MOTOR / "seedvc" / "runs"):
                antigo, novo_c = raiz / de, raiz / para
                if antigo.exists() and not novo_c.exists():
                    try:
                        antigo.rename(novo_c)
                        movidas.append(str(novo_c))
                    except OSError as exc:
                        if raiz is VOZES:
                            return self._envia(500, {"erro": f"nao consegui renomear: {exc}"})
            return self._envia(200, {"renomeado": para, "pastas": movidas})

        if cam == "/api/hf-token":
            # O token do HuggingFace (a pyannote exige) entra PELO APP, nao
            # pelo terminal — ele nao opera terminal, e token nao deve passear
            # por conversa. Gravado no mesmo lugar que o `huggingface-cli
            # login` usaria, entao todo motor daqui enxerga sozinho.
            try:
                dados = json.loads(corpo.decode("utf-8"))
                tok = str(dados.get("token", "")).strip()
            except Exception:
                return self._envia(400, {"erro": "json invalido"})
            if not tok:                       # vazio = esquecer o token
                try:
                    ARQ_HF_TOKEN.unlink()
                except FileNotFoundError:
                    pass
                return self._envia(200, {"tem": False})
            if not tok.startswith("hf_") or len(tok) < 20:
                return self._envia(400, {"erro": "isso não parece um token do "
                                                 "HuggingFace (começa com hf_)"})
            ARQ_HF_TOKEN.parent.mkdir(parents=True, exist_ok=True)
            ARQ_HF_TOKEN.write_text(tok, encoding="utf-8")
            return self._envia(200, {"tem": True})

        if cam == "/api/plim":
            # O painel (UXP nao toca audio confiavel) pede o som de conclusao
            # aqui — estilo o sininho de render do After Effects.
            tipo = b"erro" in corpo and "erro" or "ok"
            if winsound:
                try:
                    # ERRO fica com som DIFERENTE de proposito: "terminou" e
                    # "quebrou" nao podem soar igual, senao ele volta pra tela
                    # achando que deu certo.
                    if tipo == "ok" and SOM_PRONTO.exists():
                        winsound.PlaySound(str(SOM_PRONTO),
                                           winsound.SND_FILENAME | winsound.SND_ASYNC)
                    else:
                        alias = "SystemAsterisk" if tipo == "ok" else "SystemHand"
                        winsound.PlaySound(alias,
                                           winsound.SND_ALIAS | winsound.SND_ASYNC)
                except Exception:
                    pass
            return self._envia(200, {"plim": tipo})

        if cam == "/api/ambiente":
            # Salva um ambiente capturado pelo painel (Fase 3).
            try:
                dados = json.loads(corpo.decode("utf-8"))
                nome = str(dados.get("nome", "")).strip()
            except Exception:
                return self._envia(400, {"erro": "json invalido"})
            seguro = "".join(c for c in nome if c.isalnum() or c in " _-").strip()
            if not seguro:
                return self._envia(400, {"erro": "de um nome para o ambiente"})
            pasta = BASE / "ambientes"
            pasta.mkdir(parents=True, exist_ok=True)
            # Gravar o nome JA LIMPO por dentro: o arquivo se chama {seguro}.json
            # e a lista/GET procuram pelo campo "nome". Com o nome original com
            # acento la dentro, "Sala grande" salvava e sumia. Auditoria 20/08.
            (pasta / f"{seguro}.json").write_text(
                json.dumps(dict(dados, nome=seguro), ensure_ascii=False, indent=2),
                encoding="utf-8")
            return self._envia(200, {"salvo": seguro})

        if cam == "/api/ambiente_do_video":
            # Ambiente automatico: manda {video, tempo} e recebe o nome do
            # preset que combina com a CENA (ou null = deixa seco).
            try:
                dados = json.loads(corpo.decode("utf-8"))
                video = Path(dados.get("video", ""))
                tempo = float(dados.get("tempo", 0))
            except Exception:
                return self._envia(400, {"erro": "json invalido"})
            if not video.exists():
                return self._envia(404, {"erro": f"video nao existe: {video}"})
            try:
                nome, bruto = classificar_ambiente(video, tempo)
                return self._envia(200, {"ambiente": nome, "resposta": bruto})
            except Exception as exc:
                return self._envia(500, {"erro": str(exc)})

        if cam == "/api/sonda":
            # O painel-sonda (UXP, dentro do Premiere) manda o log completo pra ca.
            # Gravar em disco fecha o ciclo sem depender de copiar/colar da tela.
            destino = BASE / "painel-sonda" / "ultimo-log.txt"
            destino.write_bytes(corpo)
            return self._envia(200, {"gravado": str(destino)})

        if cam == "/api/preset":
            try:
                p = json.loads(corpo.decode("utf-8"))
                voz = str(p.get("voz") or "").strip()
                if not voz:
                    return self._envia(400, {"erro": "sem voz"})
                return self._envia(200, {"preset": grava_preset(voz, p.get("ajustes") or {})})
            except Exception as ex:
                return self._envia(400, {"erro": str(ex)[:200]})

        if cam == "/api/processar":
            try:
                p = json.loads(corpo.decode("utf-8"))
            except Exception:
                return self._envia(400, {"erro": "json invalido"})
            if p.get("modo") not in MODOS:
                return self._envia(400, {"erro": "modo invalido"})
            TRAB.mkdir(parents=True, exist_ok=True)
            t = uuid.uuid4().hex[:10]
            TAREFAS[t] = {"estado": "na fila"}
            threading.Thread(target=trabalhar, args=(t, p), daemon=True).start()
            return self._envia(200, {"tarefa": t})

        return self._envia(404, {"erro": "rota desconhecida"})


def main():
    mimetypes.init()
    VOZES.mkdir(parents=True, exist_ok=True)
    TRAB.mkdir(parents=True, exist_ok=True)
    # DOIS servidores ja conviveram na 7788 (SO_REUSEADDR do Windows permite
    # bind duplo) e o app consultava ora um, ora outro — barra parada com o
    # trabalho vivo. Sem reuse, o segundo morre no bind em vez de conviver.
    ThreadingHTTPServer.allow_reuse_address = False
    srv = ThreadingHTTPServer(("127.0.0.1", PORTA), Mao)
    print(f"DUBX no ar em http://127.0.0.1:{PORTA}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
