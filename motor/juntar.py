# -*- coding: utf-8 -*-
"""Junta a legenda feita COM ROTEIRO (texto certo) com a feita SO DA
TRANSCRICAO (cobre o que nao esta no roteiro).

Por que precisa: o roteiro do EP02 nao tem o cartao de titulo do comeco nem o
VO de chamada do fim — eles nasceram na edicao. A versao com roteiro simples-
mente nao cobre esses trechos. A da transcricao cobre, so que com o texto que
o Whisper ouviu. Aqui o roteiro manda; a transcricao so preenche buraco.

Uso: python juntar.py <roteiro.srt> <transcricao.srt> <saida.srt>
"""
import io
import sys

base, tapa, saida = sys.argv[1], sys.argv[2], sys.argv[3]


def le(caminho):
    fora = []
    for b in io.open(caminho, encoding="utf-8-sig").read().strip().split("\n\n"):
        p = b.split("\n", 2)
        if len(p) < 3:
            continue
        a, _, c = p[1].partition("-->")
        fora.append([seg(a.strip()), seg(c.strip()), p[2].strip()])
    return fora


def seg(t):
    h, m, r = t.split(":")
    s, ms = r.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def txt(t):
    h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
    return "%02d:%02d:%02d,%03d" % (h, m, s, int(round((t - int(t)) * 1000)))


bons = le(base)
extras = le(tapa)

# uma legenda da transcricao so entra se o MIOLO dela nao esta coberto por
# nenhuma do roteiro — assim nada aparece duas vezes na tela
juntas = list(bons)
entraram = 0
for ini, fim, t in extras:
    meio = (ini + fim) / 2
    if any(a - 0.15 <= meio <= b + 0.15 for a, b, _ in bons):
        continue
    juntas.append([ini, fim, t])
    entraram += 1

juntas = [x + ["rot"] for x in bons] + [x + ["tr"] for x in juntas[len(bons):]]
juntas.sort(key=lambda x: x[0])

# ⚠️ FANTASMA DO ALINHADOR: fala CORTADA na edicao nao tem tempo proprio,
# entao o alinhador pendura ela na palavra parecida mais proxima. No EP02
# "Tem uma relíquia guardada lá dentro" (cortada) grudou no "relíquia" do VO
# de chamada do fim e virou uma legenda repetindo o que ja estava na tela.
#
# NAO da pra pegar isso por regra automatica — tentei duas (vizinhanca e
# sobreposicao) e as duas levaram junto fala BOA do roteiro ("Cobra vive!",
# "eu to no paraíso dos obesos"), porque varias falas legitimas tambem saem
# com tempo torto do alinhador. Entao e lista na mao mesmo, conferida uma a
# uma: some so o que eu vi duplicando.
FANTASMAS = [(588.0, "relíquia guardada lá dentro.")]
limpas = []
for item in juntas:
    if any(abs(item[0] - t) < 1.0 and item[2] == txt_ for t, txt_ in FANTASMAS):
        print("fantasma removido: %.2f  %s" % (item[0], item[2]))
        continue
    limpas.append(item)
juntas = [x[:3] for x in limpas]
# Nunca duas legendas na tela ao mesmo tempo: quem comeca antes termina antes
# de a proxima entrar. O piso de 0,3 s NAO pode ganhar da proxima — foi assim
# que sobraram 6 sobreposicoes na primeira tentativa.
for i in range(len(juntas) - 1):
    limite = juntas[i + 1][0] - 0.02
    if juntas[i][1] > limite:
        juntas[i][1] = min(max(juntas[i][0] + 0.3, limite), limite)
# legenda que virou pisca-pisca (menos de 0,15 s) nem da tempo de aparecer
antes = len(juntas)
juntas = [x for x in juntas if x[1] - x[0] >= 0.15]
if antes != len(juntas):
    print("piscadas removidas: %d" % (antes - len(juntas)))

with io.open(saida, "w", encoding="utf-8") as f:
    for n, (ini, fim, t) in enumerate(juntas, 1):
        f.write("%d\n%s --> %s\n%s\n\n" % (n, txt(ini), txt(fim), t))
print("roteiro: %d · tapou buraco com %d da transcricao · total %d"
      % (len(bons), entraram, len(juntas)))
