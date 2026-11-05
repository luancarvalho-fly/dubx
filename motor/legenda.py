# -*- coding: utf-8 -*-
"""
DUBX — LEGENDA: SRT com o texto do ROTEIRO e o tempo do ÁUDIO.

Pedido dele (19/08): "a transcrição do Premiere é uma merda, vou ter que ficar
corrigindo na mão". A sacada e a mesma do separar por roteiro: **transcricao
adivinha o texto; o roteiro JA TEM o texto**. O Whisper entra so pra dizer
QUANDO cada palavra acontece.

Resultado: nome proprio sai certo ("Pedaleira", nao "pedalileira"; "Plexus",
nao "Plyxos"), pontuacao e a do roteiro, e nao sobra correcao pra fazer.

Sem roteiro (--roteiro vazio) ele cai na transcricao pura — ainda melhor que a
do Premiere, porque usa o large-v3-turbo, mas ai o texto e o que o Whisper
ouviu.

Uso:
    .venv/Scripts/python.exe legenda.py --srt palavras.srt -o legenda.srt \\
        [--roteiro roteiro.txt] [--personagem sim] [--largura 42]
"""
import argparse
import difflib
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diarizar_roteiro import le_roteiro, le_srt, normaliza   # noqa: E402

LARGURA = 42        # caracteres por linha (padrao de legenda)
LINHAS = 2
ASPAS = '"' + "'"
FIM_FRASE = ".!?…"   # corte DURO: nunca junta o fim de uma frase com a proxima
PAUSA = ",;:"        # corte preferencial
MIN_DUR = 1.0       # legenda piscando nao da tempo de ler
MAX_DUR = 6.0
GAP = 0.08          # respiro entre legendas coladas


def tempo_srt(seg):
    seg = max(0.0, seg)
    h = int(seg // 3600)
    m = int((seg % 3600) // 60)
    s = int(seg % 60)
    ms = int(round((seg - int(seg)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# Palavrao que a legenda censura quando --palavrao=censurar (pedido dele,
# 19/08). ⚠️ Lista CURTA de proposito: na correcao dele "porra" virou "p#rra"
# mas "cacete" ficou inteiro — ou seja, ele censura o pesado, nao todo
# palavrao. Mexer aqui e decisao editorial dele, nao minha.
PALAVROES = ("porra", "caralho", "buceta", "piroca", "foda", "fodase",
             "puta", "putas", "putaria", "merda", "bosta", "arrombado",
             "corno", "viado", "cuzao", "desgraca", "desgraça")


def censura(texto):
    """Troca as vogais do MIOLO por # — "porra" vira "p#rra".

    Mantem a primeira e a ultima letra, que e como ele escreveu na correcao
    dele: da pra ler, mas nao fica escrito na tela. Pontuacao colada na
    palavra ("porra.") sobrevive."""
    import re as _re
    vogais = "aeiouáéíóúâêôãõàü"

    def mascara(palavra):
        if len(palavra) < 3:
            return palavra
        miolo = "".join("#" if c.lower() in vogais else c for c in palavra[1:-1])
        return palavra[0] + miolo + palavra[-1]

    def eh_palavrao(bruto):
        base = unicodedata.normalize("NFD", bruto.lower())
        base = "".join(c for c in base if unicodedata.category(c) != "Mn")
        return base in PALAVROES

    saida = []
    for w in texto.split():
        nucleo = _re.match(r"[^\W\d_]+", w, _re.UNICODE)
        if nucleo and eh_palavrao(nucleo.group(0)):
            saida.append(mascara(nucleo.group(0)) + w[nucleo.end():])
        else:
            saida.append(w)
    return " ".join(saida)


def limpa_pontuacao(texto):
    """Tira ponto, virgula, reticencias, travessao — mantem so o "?" (pedido
    dele, 19/08: legenda de short fica mais limpa sem pontuacao, mas a
    pergunta perde a entonacao sem o ponto de interrogacao)."""
    fora = ".,;:!…·—–\"'()[]{}"
    saida = "".join(" " if c in fora else c for c in texto)
    return " ".join(saida.split())


def parte_por_pausa(tokens, alvo, maxi):
    """Divide um pedaco longo cortando na MAIOR PAUSA de cada trecho.

    ⚠️ Foi o que faltou na 1a versao (19/08). Ele corrigiu a legenda na mao e
    os cortes dele nao seguiam contador nenhum: "Tu já parou" (11) · "pra
    pensar por que" (18) · "a grama do" (10). Ele cortava ONDE A PESSOA
    RESPIRA — e essa informacao esta no TEMPO das palavras, nao no texto.

    ⚠️ E NAO basta pegar as N maiores pausas do trecho inteiro (2a tentativa,
    tambem errada): isso corta onde o respiro e grande mesmo que o pedaco
    fique com uma palavra so — saiu "Tu" / "já" / "parou" e depois um bloco
    gigante. A busca tem que ser LOCAL: anda acumulando e, quando o pedaco
    ja tem tamanho aceitavel, corta na maior pausa que apareceu ali dentro.

    tokens: [(palavra, ini, fim)]. Devolve lista de listas de tokens."""
    if len(" ".join(t[0] for t in tokens)) <= maxi or len(tokens) < 2:
        return [tokens]

    minimo = max(1, int(alvo * 0.55))     # abaixo disso o pedaco fica curto demais
    saida, comeco = [], 0
    while comeco < len(tokens):
        melhor, acumulado = None, ""
        for i in range(comeco, len(tokens)):
            acumulado = (acumulado + " " + tokens[i][0]).strip()
            if len(acumulado) > maxi and melhor is not None:
                break
            ultimo = i == len(tokens) - 1
            if len(acumulado) >= minimo or ultimo:
                # pausa DEPOIS desta palavra (no fim do trecho, vale como enorme)
                pausa = 9.9 if ultimo else tokens[i + 1][1] - tokens[i][2]
                if melhor is None or pausa >= melhor[1]:
                    melhor = (i, pausa)
            if len(acumulado) > maxi and melhor is not None:
                break
        if melhor is None:                 # nada coube: leva uma palavra
            melhor = (comeco, 0.0)
        saida.append(tokens[comeco:melhor[0] + 1])
        comeco = melhor[0] + 1
    return [x for x in saida if x]


def agrupa_fala(texto, alvo, maxi):
    """Quebra uma fala em legendas RESPEITANDO O SENTIDO, nao o contador.

    ⚠️ Regras tiradas da correcao que o Luan fez na mao (19/08), depois de
    dizer que minha versao "fica dando spoiler do que vem na frente":

    1. **Fim de frase (. ? ! …) e corte DURO.** Era o "spoiler": eu enchia 15
       caracteres e levava junto a primeira palavra da frase seguinte —
       "Ô Pedaleira Tu" mostrava o "Tu" antes da hora. Ele separou em
       "ó Pedaleira" + "Tu já parou".
    2. **Virgula e corte PREFERENCIAL.** Numa enumeracao ele cortou em todas:
       "troca o solo" / "molha" / "cuida com carinho".
    3. **Pedaco curto GRUDA no vizinho** se a soma couber: ele manteve
       "verdinha meu irmão" (18) junto, em vez de partir no "verdinha,".
    4. **O limite e alvo, nao lei.** Ele passou de 15 varias vezes quando o
       grupo tinha sentido — "Tem bolo melhor que" tem 19.
    5. **Divisao equilibrada, nao gananciosa.** "O bolo da tua mãe" virou
       "O bolo" + "da tua mãe", e nao "O bolo da tua" + "mãe".
    """
    import re as _re
    # 1) frases — o corte duro
    frases = [f.strip() for f in _re.split(r"(?<=[.!?…])\s+", texto) if f.strip()]
    saida = []
    for frase in frases:
        # 2) virgulas (e ; :) — corte preferencial, guardando a pontuacao
        pedacos = [x.strip() for x in _re.split(r"(?<=[,;:])\s+", frase) if x.strip()]

        # 3) junta os curtinhos com o vizinho enquanto couber
        juntos = []
        for pedaco in pedacos:
            if juntos and len(juntos[-1]) + 1 + len(pedaco) <= maxi:
                juntos[-1] += " " + pedaco
            else:
                juntos.append(pedaco)

        # 5) o que ainda passa do teto vira N partes EQUILIBRADAS
        for pedaco in juntos:
            if len(pedaco) <= maxi:
                saida.append(pedaco)
                continue
            palavras = pedaco.split()
            n = max(2, -(-len(pedaco) // alvo))       # ceil
            n = min(n, len(palavras))
            base, resto = divmod(len(palavras), n)
            i = 0
            for k in range(n):
                quantas = base + (1 if k < resto else 0)
                saida.append(" ".join(palavras[i:i + quantas]))
                i += quantas
    return [x for x in saida if x]


def quebra_em_legendas(tokens, cabe, teto):
    """Aplica as 5 regras da correcao dele a uma lista de tokens COM TEMPO.

    ⚠️ Ate 20/09/2026 esse caminho existia SO dentro do ramo do roteiro. O
    ramo da transcricao pura empilhava palavra ate encher a largura e cortava
    onde desse — exatamente o "spoiler" que ele reclamou em 19/08. No EP02
    saiu "Se tu tá vendo isso, é porque eu" / "tô preso...". Agora os dois
    ramos passam por aqui: o Whisper devolve a pontuacao, entao as mesmas
    regras valem sem roteiro.

    tokens: [(palavra_com_pontuacao, ini, fim)] · devolve lista de listas."""
    # 1) fim de frase = corte DURO · 2) virgula = corte preferencial
    grupos, atual = [], []
    for tk in tokens:
        atual.append(tk)
        ultimo = tk[0].rstrip(ASPAS)[-1:]
        if ultimo in FIM_FRASE or ultimo in PAUSA:
            grupos.append(atual)
            atual = []
    if atual:
        grupos.append(atual)

    # 3) pedaco curto gruda no vizinho enquanto couber
    juntos = []
    for g in grupos:
        cabe_junto = (juntos and
                      len(" ".join(t[0] for t in juntos[-1] + g)) <= teto and
                      juntos[-1][-1][0].rstrip(ASPAS)[-1:] not in FIM_FRASE)
        if cabe_junto:
            juntos[-1] = juntos[-1] + g
        else:
            juntos.append(g)

    # 4/5) o que passa do teto quebra NAS PAUSAS da fala
    partes = []
    for g in juntos:
        partes.extend(parte_por_pausa(g, cabe, teto))

    # ⚠️ FUSAO FINAL: sem ela sobra migalha piscando na tela. A frase
    # "É porque é o cacete, rapaz." saia em TRES legendas — "É porque é o" /
    # "cacete" / "rapaz" — porque o corte da virgula e a quebra por pausa
    # agiram em sequencia. Ele juntou em "cacete rapaz".
    fundidas = []
    for parte in partes:
        if fundidas:
            anterior = fundidas[-1]
            junto = " ".join(t[0] for t in anterior + parte)
            fim_de_frase = anterior[-1][0].rstrip(ASPAS)[-1:] in FIM_FRASE
            if len(junto) <= teto and not fim_de_frase:
                fundidas[-1] = anterior + parte
                continue
        fundidas.append(parte)

    # ⚠️ RESGATE DE ORFAO (20/09/2026, EP02): quando o par nao cabe junto, a
    # fusao acima desiste e sobra uma legenda de uma palavra so — saiu
    # "é porque eu tô preso dos 60 anos no" (35) / "passado," (8). E a regra 5
    # dele ao contrario ("divisao equilibrada, nao gananciosa"). Aqui o par e
    # REDISTRIBUIDO: procura o corte que deixa os dois lados mais parecidos.
    # So mexe quando o primeiro NAO termina em fim de frase — senao desfaria
    # o corte duro da regra 1, que e o que evita o spoiler.
    for i in range(1, len(fundidas)):
        a, b = fundidas[i - 1], fundidas[i]
        texto_b = " ".join(t[0] for t in b)
        if len(texto_b) >= cabe * 0.45 or len(a) < 2:
            continue
        # ⚠️ so redistribui quando o corte do meio NAO foi escolha de
        # pontuacao. Tentei deixar solto e o resultado foi pior: "Quando a
        # moto explodiu, alguma" — o rebalanceamento pulou a virgula e
        # trouxe de volta o spoiler que a regra 2 existe pra evitar.
        if a[-1][0].rstrip(ASPAS)[-1:] in FIM_FRASE + PAUSA:
            continue
        juntos_tk = a + b
        melhor, nota_melhor = None, None
        for corte in range(1, len(juntos_tk)):
            esq = " ".join(t[0] for t in juntos_tk[:corte])
            dir_ = " ".join(t[0] for t in juntos_tk[corte:])
            if len(esq) > teto or len(dir_) > teto:
                continue
            nota = abs(len(esq) - len(dir_))
            if nota_melhor is None or nota < nota_melhor:
                melhor, nota_melhor = corte, nota
        if melhor:
            fundidas[i - 1] = juntos_tk[:melhor]
            fundidas[i] = juntos_tk[melhor:]
    return fundidas


def quebra(texto, largura=LARGURA, linhas=LINHAS):
    """Quebra em ate N linhas sem cortar palavra.

    ⚠️ Com linhas=1 NAO quebra: o preset Plexus Mini e de linha unica, e o
    agrupador ja decidiu o tamanho do grupo pelo SENTIDO. Quebrar aqui
    devolveria duas linhas num preset que pede uma."""
    if linhas <= 1:
        return " ".join(texto.split())
    palavras, saida, atual = texto.split(), [], ""
    for w in palavras:
        if not atual:
            atual = w
        elif len(atual) + 1 + len(w) <= largura:
            atual += " " + w
        else:
            saida.append(atual)
            atual = w
    if atual:
        saida.append(atual)
    if len(saida) <= linhas:
        return "\n".join(saida)
    # não coube: reequilibra em `linhas` pedaços parecidos
    passo = max(1, len(palavras) // linhas + (1 if len(palavras) % linhas else 0))
    return "\n".join(" ".join(palavras[i:i + passo])
                     for i in range(0, len(palavras), passo))[:400]


def main():
    p = argparse.ArgumentParser(description="Gera SRT de legenda")
    p.add_argument("--srt", required=True, help="SRT palavra a palavra (max_len=1)")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--roteiro", default=None, help="txt 'Personagem: fala'")
    p.add_argument("--personagem", default="nao",
                   help="sim = prefixa a legenda com o nome de quem fala")
    p.add_argument("--largura", type=int, default=LARGURA)
    p.add_argument("--linhas", type=int, default=LINHAS,
                   help="1 = linha unica (preset Plexus Mini)")
    p.add_argument("--min-dur", type=float, default=MIN_DUR, dest="min_dur")
    p.add_argument("--max-dur", type=float, default=MAX_DUR, dest="max_dur")
    p.add_argument("--gap", type=float, default=GAP,
                   help="respiro entre legendas, em segundos (0 = coladas)")
    p.add_argument("--pontuacao", default="tudo",
                   help="tudo | so-interrogacao (tira ponto/virgula, mantem ?)")
    p.add_argument("--palavrao", default="manter",
                   help="censurar = porra vira p#rra (so os pesados) | manter")
    p.add_argument("--offset", type=float, default=0.0,
                   help="segundos cortados do começo antes de transcrever")
    a = p.parse_args()

    blocos = le_srt(a.srt)
    if not blocos:
        raise RuntimeError("a transcrição veio vazia")

    # palavras ouvidas, com tempo
    palavras = []
    for ini, fim, txt in blocos:
        ws = txt.split()
        if not ws:
            continue
        passo = (fim - ini) / len(ws)
        for i, w in enumerate(ws):
            palavras.append([ini + i * passo, ini + (i + 1) * passo, w])

    falas = le_roteiro(a.roteiro) if a.roteiro and Path(a.roteiro).exists() else []

    if falas:
        # ---- TEXTO DO ROTEIRO, TEMPO DO AUDIO --------------------------
        # Alinha o que foi ouvido com o que esta escrito; cada fala do roteiro
        # herda o tempo da primeira e da ultima palavra que casaram com ela.
        ouvidas = [normaliza(w[2])[0] if normaliza(w[2]) else "" for w in palavras]
        escritas, dono, origem = [], [], []
        for idx, (nome, ws) in enumerate(falas):
            for w in ws:
                escritas.append(w)
                dono.append(nome)
                origem.append(idx)

        sm = difflib.SequenceMatcher(None, ouvidas, escritas, autojunk=False)
        tempo_de = {}          # indice da palavra escrita -> (ini, fim)
        for i, j, n in sm.get_matching_blocks():
            for k in range(n):
                tempo_de[j + k] = (palavras[i + k][0], palavras[i + k][1])

        # texto ORIGINAL (com acento e pontuacao) de cada fala
        textos = []
        for linha in Path(a.roteiro).read_text(encoding="utf-8-sig",
                                               errors="ignore").splitlines():
            linha = linha.strip()
            if not linha:
                continue
            if ":" in linha:
                nome, _, fala = linha.partition(":")
                textos.append((nome.strip(), fala.strip()))
        legendas = []
        cabe = a.largura * max(1, a.linhas)   # alvo de UMA legenda
        # 1,3x: ele deixou passar de 15 quando o grupo tinha sentido ("Tem
        # bolo melhor que" tem 19) mas cortou em 20 — o teto fica entre os dois.
        teto = int(cabe * 1.3)
        base_global = 0
        for idx, (nome, ws) in enumerate(falas):
            # tokens ORIGINAIS (com pontuacao) casados com o tempo de cada um
            texto = textos[idx][1] if idx < len(textos) else " ".join(ws)
            tokens, k_local = [], 0
            for bruto in texto.split():
                norm = normaliza(bruto)
                if not norm:
                    if tokens:                       # pontuacao solta: gruda
                        tokens[-1] = (tokens[-1][0] + bruto, tokens[-1][1], tokens[-1][2])
                    continue
                g = base_global + k_local
                t = tempo_de.get(g)
                k_local += len(norm)
                if t:
                    tokens.append((bruto, t[0], t[1]))
                elif tokens:                          # sem tempo: herda o vizinho
                    tokens.append((bruto, tokens[-1][2], tokens[-1][2]))
            base_global += len(ws)
            if not tokens:
                continue

            fundidas = quebra_em_legendas(tokens, cabe, teto)

            for parte in fundidas:
                txt = " ".join(t[0] for t in parte)
                if str(a.personagem).lower() in ("sim", "1", "true") and not legendas:
                    txt = f"[{nome}] {txt}"
                legendas.append([parte[0][1], parte[-1][2], txt])
        fonte = "roteiro"
    else:
        # ---- so a transcricao ------------------------------------------
        # ⚠️ 20/09/2026: aqui era um empilhador burro — enchia `largura` e
        # cortava onde desse, sem olhar ponto nem virgula nem respiro. Era o
        # spoiler de 19/08 vivo no ramo sem roteiro. O Whisper JA devolve a
        # pontuacao junto da palavra, entao da pra usar as mesmas regras.
        cabe = a.largura * max(1, a.linhas)
        teto = int(cabe * 1.3)
        tokens = [(w, ini, fim) for ini, fim, w in palavras if w.strip()]
        legendas = []
        for parte in quebra_em_legendas(tokens, cabe, teto):
            legendas.append([parte[0][1], parte[-1][2],
                             " ".join(t[0] for t in parte)])
        fonte = "transcricao"

    if str(a.pontuacao).lower().startswith("so"):
        for leg in legendas:
            leg[2] = limpa_pontuacao(leg[2])

    # censura e INDEPENDENTE da pontuacao: da pra querer palavrao mascarado
    # mantendo virgula, e vice-versa
    if str(a.palavrao).lower().startswith("cens"):
        for leg in legendas:
            leg[2] = censura(leg[2])

    # ⚠️ O WHISPER ANCORA A PRIMEIRA PALAVRA EM 0,00 mesmo quando o audio
    # comeca em silencio (medido no SHORT36: a fala so entra em 2,14 s e ele
    # marcou 0,00 — a legenda aparecia antes de alguem falar). A correcao e na
    # ORIGEM: o servidor corta o silencio da frente ANTES de transcrever, e
    # devolve o quanto cortou aqui. Tentar consertar depois, encostando cada
    # legenda na fala, nao funciona: mover so o inicio inverte a legenda.
    if a.offset:
        for leg in legendas:
            leg[0] += a.offset
            leg[1] += a.offset

    # ---- higiene de tempo ----------------------------------------------
    legendas.sort(key=lambda x: x[0])
    for i, leg in enumerate(legendas):
        if leg[1] - leg[0] < a.min_dur:
            leg[1] = leg[0] + a.min_dur
        if leg[1] - leg[0] > a.max_dur:
            leg[1] = leg[0] + a.max_dur
        # ⚠️ a duracao minima NAO pode passar por cima da proxima legenda:
        # com 15 caracteres por bloco a fala rende muito pedaco, e 3 s cada
        # seria impossivel. Quem manda e o tempo real da fala.
        if i + 1 < len(legendas) and leg[1] > legendas[i + 1][0] - a.gap:
            leg[1] = max(leg[0] + 0.3, legendas[i + 1][0] - a.gap)

    saida = []
    for i, (ini, fim, texto) in enumerate(legendas, 1):
        saida.append(f"{i}\n{tempo_srt(ini)} --> {tempo_srt(fim)}\n"
                     f"{quebra(texto, a.largura, max(1, a.linhas))}\n")
    Path(a.output).write_text("\n".join(saida), encoding="utf-8")

    print(json.dumps({"ok": True, "arquivo": str(a.output),
                      "legendas": len(legendas), "fonte": fonte},
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        sys.exit(1)
