/* DUBX — painel UXP do Premiere. Fase 1: Melhorar em lote.
 *
 * O fluxo inteiro foi decidido pela sonda (15/08, ver Nova pasta\CONTEXTO.md):
 *   selecao -> so os trackitems de AUDIO -> ClipProjectItem.cast ->
 *   getMediaFilePath -> DUBX melhora NO LUGAR (backup em trabalho\originais) ->
 *   refreshMedia() e o Premiere reexibe o arquivo ja tratado.
 *
 * O painel NAO processa audio nem toca em disco: quem faz e o servidor do
 * DUBX (127.0.0.1:7788). Aqui e so seleção, ordem e acompanhamento.
 */

const ppro = require("premierepro");

const DUBX = "http://127.0.0.1:7788";
/* GUID de mediaType medido pela sonda neste host. Item de video da
 * "Invalid parameter." em qualquer API de audio — filtrar e obrigatorio. */
const AUDIO_GUID = "80b8e3d5-6dca-4195-aefb-cb5f407ab009";

const elLista = document.getElementById("lista");
const elLog = document.getElementById("log");
const btEscanear = document.getElementById("escanear");
const btMelhorar = document.getElementById("melhorar");
const elVcPuxarTom = document.getElementById("vcPuxarTom");
const elLufs = document.getElementById("lufs");
const elVozes = document.getElementById("vozes");
const btTrocar = document.getElementById("trocar");
const elNomeVoz = document.getElementById("nomeVoz");
const elEpocas = document.getElementById("epocas");
const elFid = document.getElementById("fidelidade");
const btTreinar = document.getElementById("treinar");
const btApagarVoz = document.getElementById("apagarVoz");
const btLegendar = document.getElementById("legendar");
const elRotLegenda = document.getElementById("rotLegenda");
const elLegPersonagem = document.getElementById("legPersonagem");
/* Separar / Dublar (20/08) — os dois modos que faltavam no painel. O que
 * mudou de ideia: no app o Separar termina numa mesa de mistura; aqui ele
 * termina em FAIXA na timeline, e o proprio Premiere vira a mesa. */
const btSeparar = document.getElementById("separar");
const elMotorDiar = document.getElementById("motorDiar");
const elQtdVozes = document.getElementById("qtdVozes");
const elRotSeparar = document.getElementById("rotSeparar");
const elCriarFaixas = document.getElementById("criarFaixas");
const btDublar = document.getElementById("dublar");
const elDubOrigem = document.getElementById("dubOrigem");
const elDubAlvo = document.getElementById("dubAlvo");
const elDubAtuacao = document.getElementById("dubAtuacao");
const elDubAdesao = document.getElementById("dubAdesao");
const elDubSidon = document.getElementById("dubSidon");
const elDubFaixa = document.getElementById("dubFaixa");
const elVcMotor = document.getElementById("vcMotor");
const elVcSidon = document.getElementById("vcSidon");
const elVcPresenca = document.getElementById("vcPresenca");
const elVcAtuacao = document.getElementById("vcAtuacao");
const elVcSemelhanca = document.getElementById("vcSemelhanca");
const elVcTom = document.getElementById("vcTom");
const elVcF0 = document.getElementById("vcF0");
const elVcPreservar = document.getElementById("vcPreservar");
/* motor ElevenLabs no Trocar (29/10, P31) — contrato com o servidor (P30): el_voice_id, el_modelo, el_estabilidade,
 * el_semelhanca, el_estilo (0-1), el_speaker_boost, el_remover_ruido */
const elElVoz = document.getElementById("elVoz");
let APLICANDO_FAV = false;        // favorito do ElevenLabs sendo aplicado (nao reentrar)
let REFAZENDO_LISTA_EL = false;   // lista do ElevenLabs sendo refeita (nao reaplicar favorito)
const elElEstab = document.getElementById("elEstab");
const elElSemel = document.getElementById("elSemel");
const elElEstilo = document.getElementById("elEstilo");
const elElBoost = document.getElementById("elBoost");
const elElRuido = document.getElementById("elRuido");
const EL_MODELO = "eleven_multilingual_sts_v2";
let SIDON_QUANDO = "depois";   // "antes" | "depois" | "sem" — padrao = o jeito de antes de 29/10
const elFalarVoz = document.getElementById("falarVoz");
const elFalarTexto = document.getElementById("falarTexto");
const elFalarAtuacao = document.getElementById("falarAtuacao");
const elFalarAdesao = document.getElementById("falarAdesao");
const elFalarFaixa = document.getElementById("falarFaixa");
const elDose = document.getElementById("dose");
let VCMODO = "faixa";   // "faixa" = novo arquivo na timeline (Ctrl+Z) · "arquivo" = substitui
let MLMODO = "faixa";   // o mesmo par pro Melhorar (pedido de 24/08)

/* MODO DE EDICAO DA PREVIA (24/08). Guarda em qual faixa e com qual acao ele
 * entrou pela bolinha. Enquanto nao e null, o botao da tela aplica NA FAIXA.
 * ⚠️ Declarado AQUI EM CIMA de proposito: o atualizaBotao le esta variavel e
 * roda no boot: `let` em zona morta faz ate o `typeof` LANCAR, e o painel
 * inteiro morria antes de desenhar. */
let EDITANDO = null;
let ROTULO_ANTES = "";

const elBarra = document.getElementById("barra");
const elBarraFill = document.getElementById("barraFill");
const elBarraTexto = document.getElementById("barraTexto");
const elBtCancelar = document.getElementById("btCancelar");
const elBarraPct = document.getElementById("barraPct");
let BARRA_T0 = 0;               // quando este trabalho comecou (pro "faltam")

let CLIPES = [];   // {caminho, nome, clip (ClipProjectItem), marcado, elEstado}
let DOSE = 100;
let RODANDO = false;
let LOTE = null;   // {idx, total} — de onde a barra tira a fracao global

/* ------------------------------------------------------- barra e plim */
/* O esconde e agendado (2,5 s depois do fim); um lote novo comecando nessa
 * janela cancelaria nada e a barra sumia no meio do trabalho. Timer guardado
 * pra barraMostra() poder cancelar. */
let BARRA_TIMER = null;
function barraMostra() {
  if (BARRA_TIMER) { clearTimeout(BARRA_TIMER); BARRA_TIMER = null; }
  elBarra.style.display = "block";
  elBarraTexto.style.display = "block";
  if (elBtCancelar) elBtCancelar.style.display = "block";
  BARRA_T0 = Date.now();
  barraSet(0, "");
}
function barraEsconde() {
  elBarra.style.display = "none";
  elBarraTexto.style.display = "none";
  if (elBtCancelar) elBtCancelar.style.display = "none";
}
function barraSet(fracao, texto) {
  const pct = Math.round(Math.max(0, Math.min(1, fracao)) * 100);
  elBarraFill.style.width = pct + "%";
  if (elBarraPct) elBarraPct.textContent = pct + "%";
  elBarraTexto.textContent = texto || "";
}

/* "faltam aproximadamente x minutos, x horas" (pedido dele, 25/08). */
function faltaTexto(seg) {
  if (seg === null || seg === undefined || !isFinite(seg) || seg < 0) return "";
  const s = Math.round(seg);
  if (s < 45) return "falta menos de 1 min";
  const m = Math.round(s / 60);
  if (m < 60) return "faltam ~" + m + " min";
  const h = Math.floor(m / 60), r = m % 60;
  return "faltam ~" + h + "h" + (r < 10 ? "0" : "") + r;
}

/* Estimativa pelo RELOGIO: o que ja andou diz quanto falta andar. E o que
 * serve pro trabalho que o servidor nao mede por dentro — um lote de 8
 * clipes, por exemplo, onde ele so enxerga o item que esta na mao e quem
 * sabe o tamanho do servico e o painel.
 * Fica calada nos primeiros 8 segundos e abaixo de 3%: antes disso a conta
 * balanca demais e cuspiria numero ridiculo. Melhor nao dizer nada do que
 * dizer "faltam 3 horas" num trabalho de 2 minutos. */
function estimaPelaBarra(fracao) {
  if (!BARRA_T0 || fracao <= 0.03) return null;
  const gasto = (Date.now() - BARRA_T0) / 1000;
  if (gasto < 8) return null;
  return gasto * (1 - fracao) / fracao;
}
/* Reduz a fracao do lote usando a etapa da tarefa atual, quando o servidor manda.
 *
 * O `if (!LOTE) return` que morava aqui era um bug (25/08): editar uma previa
 * pela bolinha nao e lote, entao a barra aparecia e NUNCA era pintada — 0% de
 * largura, texto vazio, por todos os 5 minutos de um treino. Foi o "nao ta
 * mostrando nada, mas minha placa de video ta fazendo barulho". O servidor
 * mandava etapa/de/mensagem o tempo inteiro; ninguem lia. Sem lote, a barra
 * passa a andar pela etapa da propria tarefa. */
function barraTarefa(e, prefixo) {
  /* `fracao` e o progresso fino que o servidor manda quando sabe contar
     passo (treino). Sem ela so restava "etapa 2 de 3", que cravava a barra
     em 66% por 11 minutos enquanto o texto contava passo a passo. */
  /* ⚠️ 29/09/2026: era `etapa / de`. O servidor manda a etapa que COMECOU,
     entao "etapa 2 de 2" virava 100% no instante em que o Sidon comecava —
     16 min cravado em "100% · falta menos de 1 min" num clipe de 28 min.
     Etapa que comecou = as anteriores feitas: (etapa - 1) / de. */
  const medida = !!(e && typeof e.fracao === "number");
  const dentro = medida ? e.fracao
               : (e && e.etapa && e.de) ? (e.etapa - 1) / e.de : 0.3;
  const fracao = LOTE ? (LOTE.idx + dentro) / LOTE.total : dentro;
  let txt = (prefixo || "") + (e && e.mensagem ? " — " + e.mensagem : "");
  /* quando o servidor mede o tempo por dentro (treino, melhorar), ele ja
     escreveu o "faltam ~X min" na propria mensagem — repetir aqui seria dizer
     duas vezes. Sem medida nenhuma (nem fracao, nem lote), o relogio so
     chutaria: melhor calar do que mentir "falta menos de 1 min". */
  if (!(e && typeof e.restante_s === "number") && (medida || LOTE)) {
    const f = faltaTexto(estimaPelaBarra(fracao));
    if (f) txt += " · " + f;
  }
  barraSet(fracao, txt);
}

/* O plim de conclusao (estilo sininho do After Effects). O UXP nao toca audio
 * confiavel, entao quem toca e o servidor do DUBX, no sistema. */
function plim(tipo) {
  fetch(DUBX + "/api/plim", { method: "POST", body: tipo || "ok" }).catch(() => {});
}

/* ------------------------------------------------------- vozes treinadas */
/* contato com o servidor: quem falhou e quantas vezes ja tentei */
const SEM_CONTATO = { vozes: 0, ambientes: 0 };
function avisaSemContato(quem, refaz) {
  if (SEM_CONTATO[quem] === 0)
    escreve("o DUBX nao respondeu — as " + quem + " entram sozinhas assim que ele subir", "erro");
  if (SEM_CONTATO[quem] < 30) { SEM_CONTATO[quem]++; setTimeout(refaz, 4000); }
}
function voltouContato(quem) {
  if (SEM_CONTATO[quem] > 0) escreve("DUBX no ar — " + quem + " carregadas", "ok");
  SEM_CONTATO[quem] = 0;
}

/* A BIBLIOTECA E A MESMA (24/08). App e painel leem o mesmo /api/vozes, que
 * le a mesma pasta vozes\ — treinar num aparece no outro. O que faltava era
 * RELER: o painel montava a lista no boot e nunca mais olhava, entao a voz
 * treinada no app so aparecia reabrindo o Premiere. Agora ele confere de
 * tempos em tempos e SO redesenha quando a lista realmente mudou (redesenhar
 * a toa fecharia o dropdown na mao dele). */
let ASSINATURA_VOZES = null;   // null = nunca li; "" = li e esta vazia

async function listaVozes() {
  try {
    const { vozes } = await (await fetch(DUBX + "/api/vozes")).json();
    const assinatura = vozes.map((v) => v.nome + (v.pode_trocar ? "R" : "")
                                       + (v.pode_trocar_seedvc ? "S" : "")).join("|");
    if (assinatura === ASSINATURA_VOZES) { voltouContato("vozes"); return; }
    const primeira = ASSINATURA_VOZES === null;
    ASSINATURA_VOZES = assinatura;
    if (!primeira) escreve("a lista de vozes mudou — recarregada", "ok");
    const atual = elVozes.value;
    elVozes.textContent = "";
    const zero = document.createElement("option");
    zero.value = ""; zero.textContent = "— vozes treinadas —";
    elVozes.appendChild(zero);
    /* TRAVA VELHA (consertada em 24/08). Isto escondia do Trocar QUALQUER voz
     * sem modelo.pth — regra da epoca em que o RVC era o unico motor. O
     * Seed-VC, que e o PADRAO desde 23/08, troca voz so com a amostra, e o
     * servidor so recusa quando o motor escolhido e o RVC (servidor.py:357).
     * Resultado da trava: ele treinou a "Membro 1" no nivel Boa, deu certo, e
     * a voz nao aparecia pra usar. */
    for (const v of vozes) {
      const o = document.createElement("option");
      o.value = v.nome;
      o.textContent = v.nome;   // so o nome (pedido dele, 24/08)
      o.dataset.rvc = v.pode_trocar ? "1" : "";
      elVozes.appendChild(o);
    }
    try { pintaFavoritosEl(); } catch (_) {}
    if ([...elVozes.options].some((o) => o.value === atual)) elVozes.value = atual;
    // o dropdown do FALAR anda junto — mesma lista, sem o filtro pode_trocar
    // (falar usa a referencia da voz; nao exige modelo treinado)
    if (elFalarVoz) {
      const atualF = elFalarVoz.value;
      elFalarVoz.textContent = "";
      const z = document.createElement("option");
      z.value = ""; z.textContent = "— vozes —";
      elFalarVoz.appendChild(z);
      for (const v of vozes) {
        const o = document.createElement("option");
        o.value = v.nome; o.textContent = v.nome;
        elFalarVoz.appendChild(o);
      }
      if ([...elFalarVoz.options].some((o) => o.value === atualF)) elFalarVoz.value = atualF;
    }
    voltouContato("vozes");
  } catch (e) {
    /* antes isto era MUDO — e foi o que fez ele ver "— vozes —" com
     * Plexus e TOM treinadas no disco (print de 24/08). O servidor
     * estava certo; o painel e que desistia calado na primeira falha. */
    avisaSemContato("vozes", listaVozes);
  }
}

/* ------------------------------------------------------------------ log */
function escreve(msg, classe) {
  const linha = document.createElement("div");
  // `.lok`/`.lerr` e nao `.ok`/`.erro`: a regra antiga era `#log .ok`, que e
  // descendente e o UXP ignora — a unica sinalizacao de sucesso/erro que ele
  // ve no painel saia sem cor nenhuma. Achado da varredura de 23/08.
  if (classe) linha.className = classe === "ok" ? "lok"
                              : classe === "erro" ? "lerr" : classe;
  linha.textContent = msg;
  elLog.appendChild(linha);
  elLog.scrollTop = elLog.scrollHeight;
  // o Registro nasce recolhido (23/08); ERRO abre sozinho — problema
  // escondido atras de cartao fechado e problema dobrado
  if (classe === "erro" && elLog.classList.contains("oculto")) {
    elLog.classList.remove("oculto");
    const ch = document.getElementById("logChev");
    if (ch) ch.textContent = "▾";
  }
}

/* O BANNER, igual o do app. O log fica no rodape, fora do campo de visao de
 * quem acabou de clicar num botao no meio da tela — o app resolve isso com um
 * aviso colado no botao, e o painel nao tinha. */
const elAviso = document.getElementById("aviso");
function mostrar(tipo, msg) {
  if (!elAviso) return;
  elAviso.className = "aviso on " + tipo;
  elAviso.textContent = msg;
}
function esconder() { if (elAviso) elAviso.className = "aviso"; }

/* diz a mesma coisa nos DOIS lugares: no banner (perto do botao) e no log
 * (que fica no rodape). Antes so ia pro log — e nem la chegava, porque o
 * botao desabilitado engolia o clique. */
function avisaFalta(msg) { mostrar("falta", msg); escreve(msg, "lfalta"); }
/* <div> nao tem `disabled`. O estado desligado e uma CLASSE, e o guard
 * de verdade fica no proprio handler (que ja checava RODANDO). */
/* ESPIAO DE CLIQUE (23/08). Ele disse "cliquei e nao acontece nada" e
 * eu vinha DEDUZINDO em vez de olhar. Isto poe no log todo clique que
 * chega num controle: se o clique nao aparecer, o problema e o evento;
 * se aparecer e nada rodar, e a funcao. Pode sair quando estabilizar. */
document.addEventListener("click", function (ev) {
  try {
    var el = ev.target;
    for (var i = 0; i < 4 && el; i++) {
      if (el.getAttribute && el.getAttribute("role") === "button") {
        var quem = el.id || (el.className || "").toString();
        escreve("· clique: " + quem
                + (el.classList && el.classList.contains("desligado")
                   ? " (DESLIGADO)" : ""));
        return;
      }
      el = el.parentNode;
    }
  } catch (e) { /* espiao nunca pode derrubar o painel */ }
});

/* TODO clique de acao passa por aqui. Duas garantias:
 *  1. erro NUNCA some — vira mensagem no banner e no log
 *  2. a trava RODANDO NUNCA fica presa — solta no finally, deu certo ou nao
 * Provado na bancada em 23/08: a trava presa de uma acao anterior fazia o
 * clique seguinte voltar calado, e era o "nao acontece nada" dele. */
/* «FICOU DUPLICADO» (Kakashi 29/10 16:35 [06:32]). O passo 2 soltava a trava de QUALQUER um: um segundo clique no Trocar
 * enquanto o primeiro rodava voltava cedo ("ja esta trabalhando"), e o fim DESTE clique zerava o RODANDO e escondia a barra
 * do trabalho que seguia rodando. O botao voltava a ficar clicavel, o clique seguinte rodava o MESMO clipe de novo e a fila
 * ganhava duas previas iguais. Agora so solta a trava quem a pegou (estava livre quando o clique chegou). */
function aoClicar(el, fn, nome) {
  if (!el) { console.warn("aoClicar: elemento nao existe:", nome); return; }
  el.addEventListener("click", function () {
    const jaRodava = RODANDO;
    Promise.resolve()
      .then(fn)
      .catch(function (e) {
        var m = (nome ? nome + ": " : "") + msgErro(e);
        escreve(m, "erro");
        mostrar("erro", m);
      })
      .then(function () {
        if (RODANDO && !jaRodava) { RODANDO = false; LOTE = null; atualizaBotao(); barraEsconde(); }
      });
  });
}

/* CANCELAR (20/09/2026). Manda o servidor matar o processo filho E OS NETOS
 * do que estiver rodando — o separador sobe um python que sobe outro, e
 * matando so o pai o neto continuava com a GPU na mao (foi assim que o PC
 * dele travou). Manda {tudo:true}: quando ele clica em cancelar, ele quer
 * que PARE, nao escolher qual dos trabalhos para.
 * Nao passa pelo aoClicar de proposito: aquele solta a trava RODANDO no fim,
 * e aqui quem solta e o loop da tarefa quando enxerga "cancelado". */
if (elBtCancelar) {
  elBtCancelar.addEventListener("click", function () {
    elBtCancelar.textContent = "parando...";
    fetch(DUBX + "/api/cancelar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tudo: true })
    }).catch(function () { /* servidor mudo: o loop da tarefa ja avisa */ })
      .then(function () { elBtCancelar.textContent = "Cancelar"; });
  });
}

/* No UXP, `.value` de textarea/input VAZIO devolve NULL (o Chrome devolve
 * ""). Provado em 23/08: "Cannot read properties of null (reading 'trim')"
 * no clique do Separar — e o mesmo null cru indo pro servidor viraria a
 * string "None" no Python. TODO campo de texto passa por aqui. */
function texto(el) {
  return String((el && el.value != null) ? el.value : "").trim();
}

function desliga(el, sim) {
  if (!el) return;
  if (sim) el.classList.add("desligado");
  else el.classList.remove("desligado");
}

function msgErro(e) {
  if (e && e.message !== undefined) return e.message;
  try { return JSON.stringify(e); } catch (_) { return String(e); }
}

/* ------------------------------------------------------------------ UI */
/* ⚠️ CLASSE UNICA, nao seletor descendente. O UXP devolve VAZIO em
 * querySelectorAll(".grupo .sbt") — sem erro, sem aviso, so nao liga
 * listener nenhum e o botao vira decoracao. Os 16 segmentados do
 * painel nasceram mortos por isso (achado em 23/08, com ele reclamando
 * "nao consigo clicar em nada"). As abas sempre funcionaram porque
 * usam querySelectorAll(".aba"), classe simples. */
// dose agora e slider (paridade com o app; os 3 botoes fixos sairam)
if (elDose) elDose.addEventListener("input", () => { DOSE = Number(elDose.value); });
// 05/11: soltou o slider depois de um melhorar → remistura o que acabou de entrar, sem rodar o Sidon de novo
if (elDose) elDose.addEventListener("change", () => { remisturaUltimo(); });

/* Fidelidade do treino: Rapido/Medio/Muito fiel sao apelidos pra epocas
 * (50/150/300). Slider e numero andam juntos; mexer na mao solta o preset. */
const DICAS_FID = {
  50: "Rápido ≈ 15 min — bom pra TESTAR a voz antes de investir.",
  150: "Médio ≈ 45 min — meio-termo.",
  300: "Muito fiel ≈ 1h30 com ~5 min de material — a voz de verdade.",
};
function fidSincroniza(valor, dePreset) {
  const v = Math.max(10, Math.min(1000, Number(valor) || 300));
  elEpocas.value = v;
  elFid.value = Math.min(600, v);
  document.querySelectorAll(".sbt-fid").forEach((x) =>
    x.classList.toggle("ativa", dePreset && Number(x.dataset.ep) === v));
  const dica = document.getElementById("dicaTreino");
  dica.textContent = DICAS_FID[v] ||
    (v + " épocas (~" + Math.round(v * 0.3) + " min com ~5 min de material).");
}
document.querySelectorAll(".sbt-fid").forEach((b) => {
  b.addEventListener("click", () => fidSincroniza(b.dataset.ep, true));
});
elFid.addEventListener("input", () => fidSincroniza(elFid.value, false));
elEpocas.addEventListener("change", () => fidSincroniza(elEpocas.value, false));

/* ⭐ O BOTAO NAO FICA MAIS CINZA POR FALTA DE SELECAO — ele fica CLICAVEL
 * e DIZ o que falta.
 *
 * Por que: as 8 mensagens de "escaneie e marque os clipes primeiro" que
 * existiam dentro de cada funcao eram CODIGO MORTO. O botao estava
 * `disabled`, entao o clique nunca disparava e a explicacao nunca
 * aparecia. Ele clicou, nao aconteceu nada, e ficou com razao ao dizer
 * "nao funciona". Desabilitar SO enquanto roda — ai o motivo e obvio. */
function atualizaBotao() {
  const n = CLIPES.filter((c) => c.marcado).length;
  // chip do heroi: quantos clipes a analise achou ("um clique e vejo os nomes")
  const chip = document.getElementById("chipClipes");
  if (chip) chip.textContent = CLIPES.length + (CLIPES.length === 1 ? " clipe" : " clipes") + " ▾";
  // rotulo = acao exata (checklist): sem selecao o botao diz o gesto, nao
  // "0 arquivo(s)"; com selecao, plural de gente ("1 arquivo", "3 arquivos")
  btMelhorar.textContent = RODANDO ? "trabalhando…"
    : n === 0 ? "Melhorar seleção"
    : "Melhorar " + n + (n === 1 ? " arquivo" : " arquivos");
  desliga(btMelhorar, RODANDO);
  desliga(btTrocar, RODANDO);
  desliga(document.getElementById("ambientar"), RODANDO); // btAmbientar ainda nao existe no boot (TDZ)
  desliga(btTreinar, RODANDO);
  desliga(btLegendar, RODANDO);
  desliga(btSeparar, RODANDO);
  desliga(btDublar, RODANDO);
  const ba = document.getElementById("ambienteAuto");
  if (ba) desliga(ba, RODANDO);
  const bps = document.getElementById("pitchSync");
  if (bps) desliga(bps, RODANDO);
  const bpl = document.getElementById("psLer");
  if (bpl) desliga(bpl, RODANDO);
  const bf = document.getElementById("rodarFluxo");
  if (bf) desliga(bf, RODANDO);
  // trocar de fluxo NO MEIO da execucao trocava as etapas ao vivo e as
  // seguintes rodavam do fluxo novo. Auditoria 20/08.
  const sf = document.getElementById("fluxo");
  if (sf) sf.disabled = RODANDO;   // <select> de verdade: tem `disabled` nativo
  // re-escanear no meio do lote orfana os elEstado da fila em andamento
  desliga(btEscanear, RODANDO);
  /* MODO DE EDICAO DA PREVIA vence o rotulo (24/08): sem isto o btMelhorar
     voltava a dizer "Melhorar seleção" e ele aplicaria na timeline achando
     que estava mexendo na faixa da fila. */
  if (EDITANDO && !RODANDO) {
    const bt = botaoDaAcao(EDITANDO.acao);
    if (bt) bt.textContent = "Aplicar nesta prévia";
  }
}

function desenhaLista() {
  elLista.textContent = "";
  if (!CLIPES.length) {
    const v = document.createElement("div");
    v.className = "vazio";
    v.textContent = "nenhum clipe de áudio na seleção.";
    elLista.appendChild(v);
    return;
  }
  for (const c of CLIPES) {
    const li = document.createElement("div");
    li.className = "clipe";
    const cx = document.createElement("input");
    cx.className = "cx";
    cx.type = "checkbox";
    cx.checked = c.marcado;
    cx.addEventListener("change", () => { c.marcado = cx.checked; atualizaBotao(); });
    const nome = document.createElement("span");
    nome.className = "nome";
    nome.textContent = c.nome;
    nome.title = c.caminho;
    const estado = document.createElement("span");
    estado.className = "estado";
    estado.textContent = "na fila";
    c.elEstado = estado;
    li.appendChild(cx); peleCheckbox(cx);   // nasce depois do boot
    li.appendChild(nome); li.appendChild(estado);
    elLista.appendChild(li);
  }
}

function estadoDo(c, texto, classe) {
  if (!c.elEstado) return;
  c.elEstado.textContent = texto;
  c.elEstado.className = "estado" + (classe ? " " + classe : "");
}

/* ------------------------------------------------------------- escanear */
async function escanear() {
  esconder();   // limpa o aviso da tentativa anterior
  elLog.textContent = "";
  CLIPES = [];
  // O DUBX pode ter subido DEPOIS do painel (ordem comum no dia a dia) e os
  // dropdowns terem carregado vazios — escanear e o gesto natural de retry.
  listaVozes();
  listaAmbientes();
  try {
    CLIPES = await lerSelecao();
    desenhaLista();
    peleTodosCheckbox();   // os checkbox da lista nascem agora
    atualizaBotao();
    escreve(CLIPES.length + " clipe(s) de áudio na seleção.", CLIPES.length ? "ok" : "erro");
  } catch (e) {
    desenhaLista();
    atualizaBotao();
    escreve(msgErro(e), "erro");
  }
}

/* SELECAO VIVA (Kakashi 29/10 20:27 [00:24]: «se eu clicar pra gerar, é só esse clipe de áudio aqui, e não todos que estão
 * carregados... Ele já, automático, já sabe qual é a seleção específica»): Trocar/Melhorar leem a selecao da timeline NA HORA do
 * clique; a lista do «Analisar seleção» so vale quando nada esta selecionado. */
async function pegaSelecaoViva() {
  try {
    const vivos = await lerSelecao();
    if (!vivos.length) return;
    CLIPES = vivos;
    desenhaLista();
    peleTodosCheckbox();
    atualizaBotao();
    escreve("seleção atual da timeline: " + vivos.length + " clipe(s).");
  } catch (_) { /* nada selecionado: fica a lista analisada */ }
}

async function lerSelecao() {
  const lista = [];
  {
    const project = await ppro.Project.getActiveProject();
    if (!project) throw new Error("nenhum projeto aberto");
    const seq = await project.getActiveSequence();
    if (!seq) throw new Error("nenhuma sequência ativa");
    const selecao = await seq.getSelection();
    const itens = await selecao.getTrackItems();
    if (!itens || !itens.length) throw new Error("nada selecionado na timeline");

    const vistos = new Set();
    for (const item of itens) {
      try {
        let tipo = item.getMediaType ? item.getMediaType() : undefined;
        if (tipo && typeof tipo.then === "function") tipo = await tipo;
        if (String(tipo) !== AUDIO_GUID) continue;   // video/dado: fora

        const pi = await item.getProjectItem();
        const clip = await ppro.ClipProjectItem.cast(pi);
        const caminho = await clip.getMediaFilePath();
        if (!caminho) continue;
        /* 29/10 (ele: «selecionei 5 clipes e ele só reconheceu 2»): o dedup era pelo ARQUIVO, e varios clipes da timeline
         * cortam o MESMO arquivo em trechos diferentes. Cada clipe e um trecho: a chave e arquivo + in/out. So o MESMO
         * trecho 2x (clipe linkado/estereo) vira um. */
        let ini = 0, fim = 0, pos = 0;
        try { ini = (await item.getInPoint()).seconds; fim = (await item.getOutPoint()).seconds; } catch (_) {}
        try { pos = (await item.getStartTime()).seconds; } catch (_) {}
        const chave = caminho + "|" + ini.toFixed(3) + "|" + fim.toFixed(3);
        if (vistos.has(chave)) continue;
        vistos.add(chave);
        const mm = Math.floor(pos / 60), ss = Math.floor(pos % 60);
        lista.push({
          caminho,
          nome: caminho.split(/[\\/]/).pop() + " @" + mm + ":" + (ss < 10 ? "0" : "") + ss,
          clip,
          // O trackitem tambem: o Separar precisa do getStartTime() dele pra
          // alinhar os stems no MESMO ponto da timeline (o `clip` e o item do
          // projeto, nao sabe onde esta na sequencia).
          item,
          marcado: true,
          elEstado: null,
        });
      } catch (e) {
        escreve("item pulado: " + msgErro(e), "erro");
      }
    }
  }
  return lista;
}

/* BOOT ESSENCIAL — cedo e blindado (25/08).
 * O arquivo é um bloco só: em 25/08 uma linha do meio falhou e levou
 * junto o "Analisar seleção" e o carregamento das vozes, que eram
 * ligados perto do fim. Agora o que o painel precisa pra funcionar é
 * ligado AQUI, antes de qualquer coisa que possa quebrar, e cada passo
 * do resto do boot roda dentro de seguro(). */
function seguro(fn, nome) {
  try { fn(); }
  catch (e) {
    try { escreve("boot (" + nome + "): " + msgErro(e), "erro"); } catch (_) {}
  }
}

seguro(function () { aoClicar(btEscanear, escanear, "escanear"); }, "analisar");
seguro(function () { aoClicar(btMelhorar, melhorar, "melhorar"); }, "melhorar");
seguro(function () { atualizaBotao(); }, "botao");
seguro(function () { listaVozes(); }, "vozes");
seguro(function () { listaAmbientes(); }, "ambientes");
/* releitura periodica: e o que faz a voz treinada no APP aparecer aqui sem
   reabrir o Premiere. Nao roda durante um trabalho pra nao competir. */
seguro(function () {
  setInterval(function () { if (!RODANDO) listaVozes(); }, 15000);
}, "releitura das vozes");

/* ------------------------------------------------------------- melhorar */
function espera(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function enviaTarefa(c, corpo) {
  const r = await fetch(DUBX + "/api/processar", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(Object.assign({ arquivo: c.caminho }, corpo)),
  });
  const { tarefa, erro } = await r.json();
  if (!tarefa) throw new Error(erro || "o DUBX nao aceitou a tarefa");

  for (;;) {
    await espera(1000);
    const e = await (await fetch(DUBX + "/api/estado?t=" + tarefa)).json();
    if (e.estado === "pronto") return;
    if (e.estado === "cancelado") throw new Error("cancelado");
    if (e.estado === "erro") throw new Error(e.mensagem || "falhou no DUBX");
    estadoDo(c, e.mensagem || "processando…");
    barraTarefa(e, c.nome);
  }
}

/* O Premiere segura aberto o arquivo que esta em uso ativo (monitor, playhead)
 * e a regravacao cai com WinError 32. O contorno, medido ao vivo em 15/08:
 * poe a midia OFFLINE (solta o handle), regrava, e religa no MESMO caminho. */
function arquivoEmUso(e) {
  return /WinError 32|sendo usado|being used/i.test(msgErro(e));
}

async function processaUm(c, corpo) {
  estadoDo(c, "enviando…");
  try {
    await enviaTarefa(c, corpo);
  } catch (e) {
    if (!arquivoEmUso(e)) throw e;
    estadoDo(c, "liberando arquivo…");
    const project = await ppro.Project.getActiveProject();
    project.lockedAccess(() => {
      project.executeTransaction((ca) => {
        ca.addAction(c.clip.createSetOfflineAction());
      }, "DUBX: liberar " + c.nome);
    });
    await espera(1500);   // o handle nao solta na hora
    try {
      await enviaTarefa(c, corpo);
    } catch (e2) {
      // A retentativa falhou DEPOIS do setOffline: religa antes de propagar,
      // senao o clipe fica offline na timeline com o arquivo intacto no disco.
      try { await c.clip.changeMediaFilePath(c.caminho, true); } catch (_) {}
      throw e2;
    }
    estadoDo(c, "religando…");
    const religou = await c.clip.changeMediaFilePath(c.caminho, true);
    if (!religou) throw new Error("processou, mas nao religou — religue na mao (botao direito > Vincular midia)");
  }

  estadoDo(c, "atualizando no Premiere…");
  const deuCerto = await c.clip.refreshMedia();
  if (!deuCerto) escreve(c.nome + ": refreshMedia devolveu false — confira o clipe", "erro");
  estadoDo(c, "pronto", "ok");
}

/* Laco comum do lote arquivo-a-arquivo (Melhorar e Trocar voz usam igual):
 * barra global, contorno de arquivo travado, plim no fim. */
async function rodaLote(corpo, verbo) {
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  RODANDO = true;
  atualizaBotao();
  const fila = CLIPES.filter((c) => c.marcado);
  escreve(verbo + " " + fila.length + " arquivo(s). backup de cada original em DUBX\\trabalho\\originais.");
  barraMostra();
  let feitos = 0;
  for (let i = 0; i < fila.length; i++) {
    LOTE = { idx: i, total: fila.length };
    try {
      await processaUm(fila[i], corpo);
      feitos++;
      escreve("OK " + fila[i].nome, "ok");
    } catch (e) {
      estadoDo(fila[i], "erro", "erro");
      escreve(fila[i].nome + ": " + msgErro(e), "erro");
    }
  }
  LOTE = null;
  barraSet(1, "concluído");
  const tudoCerto = feitos === fila.length;
  escreve("fim: " + feitos + "/" + fila.length + ".", tudoCerto ? "ok" : "erro");
  plim(tudoCerto ? "ok" : "erro");
  RODANDO = false;
  atualizaBotao();
  BARRA_TIMER = setTimeout(barraEsconde, 2500);
}

async function melhorar() {
  if (desviaPraPrevia("melhorar")) return;
  if (!RODANDO) await pegaSelecaoViva();
  /* REGRAVAR O ORIGINAL: era o único jeito até 24/08 — e sem Ctrl+Z,
   * porque Ctrl+Z não desfaz escrita em disco. Virou opt-in, como o
   * Trocar já tinha feito em 23/08. Aqui vai o arquivo INTEIRO de
   * propósito: regravar só o trecho apagaria o resto do arquivo. */
  if (MLMODO === "arquivo") {
    return rodaLote(
      { modo: "melhorar_no_lugar", dose: DOSE, lufs: Number(elLufs.value) || -16 },
      "melhorando");
  }
  return melhorarNovo();
}

/* MELHORAR SEM TOCAR NO ORIGINAL — padrão desde 24/08. Ele pediu:
 * "eu quero poder dar um Ctrl+Z e um Ctrl+Alt+Z pra ficar desfazendo e
 * refazendo e ver a diferença". Sai um arquivo NOVO, entra como clipe
 * numa transação só e o clipe velho fica mudo junto.
 * Conserta de quebra a outra queixa do mesmo dia: manda o TRECHO do
 * clipe, então melhora só o pedaço marcado e não o arquivo inteiro
 * (o `melhorar_no_lugar` nem entra em MODOS_VIDEO no servidor, ou seja
 * ignorava início/fim e processava tudo). */
async function melhorarNovo() {
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  const fila = CLIPES.filter((c) => c.marcado);
  if (!fila.length) { avisaFalta("selecione clipes na timeline e clique em Analisar seleção."); return; }
  RODANDO = true; atualizaBotao(); barraMostra();
  ULTIMO_MELHORAR = [];
  abreLote();   // entrega tudo junto no fim: uma faixa por faixa de origem
  escreve("melhorando " + fila.length + " arquivo(s) — só o trecho do clipe; o original NÃO é tocado.");
  let feitos = 0;
  for (let i = 0; i < fila.length; i++) {
    LOTE = { idx: i, total: fila.length };
    const c = fila[i];
    try {
      estadoDo(c, "enviando…");
      const trecho = await trechoDoClipe(c);   // so o pedaco que ELE marcou
      const r = await fetch(DUBX + "/api/processar", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign(
          { modo: "melhorar", arquivo: c.caminho,
            dose: DOSE, lufs: Number(elLufs.value) || -16 }, trecho || {})),
      });
      const { tarefa, erro } = await r.json();
      if (!tarefa) throw new Error(erro || "o DUBX não aceitou a tarefa");
      let pronto = null;
      for (;;) {
        await espera(1000);
        const e = await (await fetch(DUBX + "/api/estado?t=" + tarefa)).json();
        if (e.estado === "pronto") { pronto = e; break; }
        if (e.estado === "cancelado") throw new Error("cancelado");
        if (e.estado === "erro") throw new Error(e.mensagem || "falhou no DUBX");
        estadoDo(c, e.mensagem || "melhorando…");
        barraTarefa(e, c.nome);
      }
      const arquivo = pronto.previa || pronto.saida;   // audio pra timeline
      if (!arquivo) throw new Error("o melhorar não devolveu arquivo");
      estadoDo(c, "entregando…");
      const msg = await entregaResultado(arquivo, c, "melhorado — " + c.nome, true);
      ULTIMO_MELHORAR.push({ tarefa, arquivo: pronto.previa || pronto.saida });
      escreve("melhorado: " + msg, "ok");
      estadoDo(c, "pronto", "ok");
      feitos++;
    } catch (e) {
      estadoDo(c, "erro", "erro");
      escreve(c.nome + ": " + msgErro(e), "erro");
    }
  }
  LOTE = null; barraSet(1, "concluído");
  if (ULTIMO_MELHORAR.length)
    escreve("reconstrução ajustável: mexa no slider Dose e solte — o áudio que entrou muda em segundos, sem refazer o Sidon.");
  try { await fechaLote("melhorado", true); }
  catch (e) { escreve("pôr na timeline: " + msgErro(e), "erro"); }
  escreve("fim: " + feitos + "/" + fila.length + ".", feitos === fila.length ? "ok" : "erro");
  plim(feitos === fila.length ? "ok" : "erro");
  RODANDO = false; atualizaBotao();
  BARRA_TIMER = setTimeout(barraEsconde, 2500);
}

/* RECONSTRUÇÃO AJUSTÁVEL (05/11, ele: «melhorou o áudio, beleza, e aí deixa o slider de reconstrução pra eu ficar
 * brincando e ver qual eu prefiro»). O melhorar guarda seco + Sidon no servidor; soltar o slider Dose manda
 * "remisturar" pros arquivos do ÚLTIMO melhorar: o servidor regrava o mesmo wav e aqui só se recarrega a mídia. */
let ULTIMO_MELHORAR = [];   // {tarefa, arquivo}
let REMIX_PENDENTE = false;

async function clipesDoArquivo(caminho) {
  const achados = [];
  const project = await ppro.Project.getActiveProject();
  const seq = project && await project.getActiveSequence();
  if (!seq) return achados;
  const total = await seq.getAudioTrackCount();
  for (let i = 0; i < total; i++) {
    for (const it of await itensDaFaixa(await seq.getAudioTrack(i))) {
      try {
        const clip = await ppro.ClipProjectItem.cast(await it.getProjectItem());
        if (String(await clip.getMediaFilePath()).toLowerCase() === String(caminho).toLowerCase())
          achados.push(clip);
      } catch (_) { /* item sem mídia legível */ }
    }
  }
  return achados;
}

async function remisturaUm(u, dose) {
  // direto (~1 s), sem fila e sem criar geração nova a cada mexida
  const r = await fetch(DUBX + "/api/remisturar", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: u.tarefa, arquivo: u.arquivo, dose, lufs: Number(elLufs.value) || -16 }),
  });
  const j = await r.json();
  if (!r.ok || !j.ok) throw new Error(j.erro || "a remistura falhou");
}

async function remisturaUltimo() {
  if (!ULTIMO_MELHORAR.length) return;
  if (RODANDO) { REMIX_PENDENTE = true; return; }
  RODANDO = true; atualizaBotao();
  const dose = DOSE;
  try {
    for (const u of ULTIMO_MELHORAR) {
      const nome = String(u.arquivo).split(/[\\/]/).pop();
      const clips = await clipesDoArquivo(u.arquivo);
      try {
        await remisturaUm(u, dose);
      } catch (e) {
        // o Premiere segura o wav aberto (WinError 32): mesmo contorno do processaUm — offline, regrava, religa
        if (!arquivoEmUso(e) || !clips.length) throw e;
        const project = await ppro.Project.getActiveProject();
        project.lockedAccess(() => {
          project.executeTransaction((ca) => {
            for (const c of clips) ca.addAction(c.createSetOfflineAction());
          }, "DUBX: liberar " + nome);
        });
        await espera(1500);
        try { await remisturaUm(u, dose); }
        finally { for (const c of clips) { try { await c.changeMediaFilePath(u.arquivo, true); } catch (_) {} } }
      }
      for (const c of clips) { try { await c.refreshMedia(); } catch (_) {} }
      escreve("reconstrução " + dose + "%: " + nome, "ok");
    }
  } catch (e) {
    escreve("reconstrução: " + msgErro(e), "erro");
  } finally {
    RODANDO = false; atualizaBotao();
  }
  // soltou o slider de novo enquanto remisturava: aplica a última posição
  if (REMIX_PENDENTE) { REMIX_PENDENTE = false; if (DOSE !== dose) remisturaUltimo(); }
}

/* Os ajustes que o app sempre teve e o painel nunca mandou. O servidor
 * aceita todos desde o comeco (presenca/atuacao/semelhanca/tom/f0 no _rvc;
 * preservar na corrente de stems) — o painel e que mandava so {voz, lufs}. */
function ajustesTroca(voz) {
  const a = {
    voz,
    motor_vc: elVcMotor ? elVcMotor.value : "seedvc",
    sidon: elVcSidon ? Number(elVcSidon.value) : 100,
    presenca: elVcPresenca ? Number(elVcPresenca.value) / 100 : 0.15,
    atuacao: elVcAtuacao ? Number(elVcAtuacao.value) / 100 : 1.0,
    semelhanca: elVcSemelhanca ? Number(elVcSemelhanca.value) / 100 : 0.5,
    tom: elVcTom ? Number(elVcTom.value) : 0,
    f0: elVcF0 ? elVcF0.value : "rmvpe",
    preservar: !!(elVcPreservar && elVcPreservar.checked),
    // PUXAR A ALTURA: so o Seed-VC usa; no RVC quem move o tom e o slider Tom
    puxar_tom: !!(elVcPuxarTom && elVcPuxarTom.checked),
    lufs: Number(elLufs.value) || -16,
  };
  a.sidon_quando = SIDON_QUANDO;
  if (a.motor_vc === "elevenlabs") {
    Object.assign(a, {
      el_voice_id: elElVoz ? elElVoz.value : "",
      el_nome: voz,
      el_modelo: EL_MODELO,
      el_estabilidade: elElEstab ? Number(elElEstab.value) / 100 : 0.5,
      el_semelhanca: elElSemel ? Number(elElSemel.value) / 100 : 0.75,
      el_estilo: elElEstilo ? Number(elElEstilo.value) / 100 : 0,
      el_speaker_boost: elElBoost ? !!elElBoost.checked : true,
      el_remover_ruido: !!(elElRuido && elElRuido.checked),
    });
  }
  return a;
}

/* a voz que o Trocar vai usar: no ElevenLabs e a do dropdown da conta dele (manda o NOME em `voz`, o id em el_voice_id);
 * nos outros motores, a voz treinada do DUBX */
function vozDaTroca() {
  if (elVcMotor && elVcMotor.value === "elevenlabs") {
    if (!elElVoz || !elElVoz.value) return "";
    for (let i = 0; i < elElVoz.options.length; i++)
      if (elElVoz.options[i].value === elElVoz.value) return (elElVoz.options[i].textContent || elElVoz.value).replace(/^★\s*/, "");
    return elElVoz.value;
  }
  return elVozes.value;
}
function faltaVozTroca() {
  return elVcMotor && elVcMotor.value === "elevenlabs"
    ? "escolha a voz do ElevenLabs primeiro." : "escolha uma voz treinada primeiro.";
}

async function trocarVoz() {
  if (desviaPraPrevia("trocar")) return;
  if (!RODANDO) await pegaSelecaoViva();
  const voz = vozDaTroca();
  if (!voz) { avisaFalta(faltaVozTroca()); return; }

  /* so o RVC exige o ficharo (modelo.pth); o Seed-VC roda com a amostra */
  const motor = elVcMotor ? elVcMotor.value : "seedvc";
  if (motor === "rvc") {
    let temRvc = false;
    for (let i = 0; i < elVozes.options.length; i++)
      if (elVozes.options[i].value === voz) temRvc = !!elVozes.options[i].dataset.rvc;
    if (!temRvc) {
      avisaFalta("a voz \"" + voz + "\" nao tem o ficharo do RVC. Troque o Motor "
                 + "para Seed-VC, ou treine ela de novo no nivel Maxima.");
      return;
    }
  }

  /* SUBSTITUIR O ARQUIVO: o comportamento antigo, agora OPT-IN. Em 23/08 ele
   * rodou o trocar, o arquivo ORIGINAL foi regravado e o Ctrl+Z nao desfazia
   * — Ctrl+Z nao desfaz escrita em disco. O padrao virou a faixa nova. */
  if (VCMODO === "arquivo") {
    return rodaLote(
      Object.assign({ modo: "trocar_no_lugar" }, ajustesTroca(voz)),
      "trocando a voz (" + voz + ") de");
  }

  /* NOVA FAIXA (padrao): o modo `trocar` cria um arquivo NOVO — o original
   * nao e tocado — e o resultado cai na timeline pelo MESMO caminho ja
   * provado do dublar. Como e clipe na sequencia, o Ctrl+Z desfaz. */
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  const fila = CLIPES.filter((c) => c.marcado);
  if (!fila.length) { avisaFalta("selecione clipes na timeline e clique em Analisar seleção."); return; }
  RODANDO = true; atualizaBotao(); barraMostra();
  abreLote();   // uma faixa por faixa de origem, nao uma por clipe
  escreve("trocando a voz (" + voz + ") de " + fila.length + " arquivo(s) — o original NÃO é tocado.");
  let feitos = 0;
  for (let i = 0; i < fila.length; i++) {
    LOTE = { idx: i, total: fila.length };
    const c = fila[i];
    try {
      estadoDo(c, "enviando…");
      const trecho = await trechoDoClipe(c);   // so o pedaco que ELE marcou
      const r = await fetch(DUBX + "/api/processar", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign(
          { modo: "trocar", arquivo: c.caminho }, ajustesTroca(voz), trecho || {})),
      });
      const { tarefa, erro } = await r.json();
      if (!tarefa) throw new Error(erro || "o DUBX não aceitou a tarefa");
      let pronto = null;
      for (;;) {
        await espera(1000);
        const e = await (await fetch(DUBX + "/api/estado?t=" + tarefa)).json();
        if (e.estado === "pronto") { pronto = e; break; }
        if (e.estado === "cancelado") throw new Error("cancelado");
        if (e.estado === "erro") throw new Error(e.mensagem || "falhou no DUBX");
        estadoDo(c, e.mensagem || "trocando…");
        barraTarefa(e, c.nome);
      }
      const arquivo = pronto.previa || pronto.saida;   // audio pra timeline
      if (!arquivo) throw new Error("a troca não devolveu arquivo");
      estadoDo(c, "entregando…");
      const msg = await entregaResultado(arquivo, c, "voz " + voz + " — " + c.nome, false,
                                         { ajustes: ajustesTroca(voz), trecho, origem: c.caminho });
      escreve("voz trocada: " + msg, "ok");
      estadoDo(c, "pronto", "ok");
      feitos++;
    } catch (e) {
      estadoDo(c, "erro", "erro");
      escreve(c.nome + ": " + msgErro(e), "erro");
    }
  }
  LOTE = null; barraSet(1, "concluído");
  try { await fechaLote("voz trocada", true); }
  catch (e) { escreve("pôr na timeline: " + msgErro(e), "erro"); }
  escreve("fim: " + feitos + "/" + fila.length + ".", feitos === fila.length ? "ok" : "erro");
  plim(feitos === fila.length ? "ok" : "erro");
  RODANDO = false; atualizaBotao();
  BARRA_TIMER = setTimeout(barraEsconde, 2500);
}

/* ------------------------------------------------------------- falar
 * A aba que existia so no app: TEXTO vira fala com a voz treinada, e a fala
 * nasce no CURSOR da timeline — nao precisa de clipe selecionado. */
async function falar() {
  const voz = elFalarVoz ? elFalarVoz.value : "";
  const frase = texto(elFalarTexto);
  if (!voz) { avisaFalta("escolha a voz que vai falar."); return; }
  if (!frase) { avisaFalta("escreva a fala primeiro."); return; }
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  RODANDO = true; atualizaBotao(); barraMostra();
  escreve("gerando a fala com a voz " + voz + "…");
  try {
    const r = await fetch(DUBX + "/api/processar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        modo: "falar", texto: frase, voz,
        atuacao: Number(elFalarAtuacao.value) / 100,
        adesao: Number(elFalarAdesao.value) / 100,
        lufs: Number(elLufs.value) || -16,
      }),
    });
    const { tarefa, erro } = await r.json();
    if (!tarefa) throw new Error(erro || "o DUBX não aceitou a tarefa");
    let pronto = null;
    for (;;) {
      await espera(1000);
      const e = await (await fetch(DUBX + "/api/estado?t=" + tarefa)).json();
      if (e.estado === "pronto") { pronto = e; break; }
      if (e.estado === "cancelado") throw new Error("cancelado");
      if (e.estado === "erro") throw new Error(e.mensagem || "falhou no DUBX");
      barraSet(0.4, e.mensagem || "gerando…");
    }
    const arquivo = pronto.previa || pronto.saida;
    if (elFalarFaixa && elFalarFaixa.checked && arquivo) {
      const msg = await entregaResultado(arquivo, null, "fala (" + voz + ")");
      escreve("fala: " + msg, "ok");
    } else {
      escreve("fala pronta: " + arquivo, "ok");
    }
    plim("ok");
  } catch (e) {
    escreve("falar: " + msgErro(e), "erro");
    mostrar("erro", "falar: " + msgErro(e));
    plim("erro");
  } finally {
    RODANDO = false; atualizaBotao();
    barraSet(1, "concluído");
    BARRA_TIMER = setTimeout(barraEsconde, 2500);
  }
}

/* ------------------------------------------------------------- treinar */
/* SELECAO E CURADORIA (licao de 15/08): treina com os TRECHOS que estao
 * selecionados na timeline AGORA — in/out de cada clipe, nunca o arquivo
 * inteiro. Por isso le a selecao viva no clique, sem dedup por caminho. */
/* ATE ONDE IR (24/08). Uma voz no DUBX tem ate tres pecas: a amostra (o
 * unico arquivo obrigatorio), o ajuste fino do Seed-VC e o ficharo do RVC.
 * O nivel diz quantas delas fazer — e so a terceira custa horas. */
let NIVEL_TREINO = "boa";
const TEXTO_NIVEL = {
  rapida: "Só guarda a amostra, sem treinar nada — o motor padrão copia a voz na hora, olhando 25 s dela. Sai na hora e não gasta placa. É o modo dos personagens random.",
  boa: "Faz o motor padrão ESTUDAR a voz com todo o material selecionado — uns 5 minutos. Melhor custo-benefício; use este por padrão.",
  maxima: "Tudo do Boa mais o fichário do RVC: horas de treino guardando pedaços reais da voz. É o que pega mania de falar (língua presa, sotaque). Depois você escolhe o motor no Trocar voz."
};
function pintaNivel() {
  const bts = document.getElementsByClassName("sbt-nivel");
  for (let i = 0; i < bts.length; i++)
    bts[i].className = "sbt sbt-nivel" + (bts[i].dataset.nivel === NIVEL_TREINO ? " ativa" : "");
  const d = document.getElementById("dicaNivel");
  if (d) d.textContent = TEXTO_NIVEL[NIVEL_TREINO] || "";
  // Epocas so existe no RVC: nos outros niveis o controle nao muda nada
  const so = NIVEL_TREINO === "maxima";
  for (const id of ["segFid", "ctrlEpocas", "dicaTreino"]) {
    const el = document.getElementById(id);
    if (el) el.className = el.className.replace(/ ?oculto/g, "") + (so ? "" : " oculto");
  }
}
seguro(function () {
  const bts = document.getElementsByClassName("sbt-nivel");
  for (let i = 0; i < bts.length; i++) {
    // captura o elemento: `this` dentro de listener nao e confiavel no UXP
    const bt = bts[i];
    bt.addEventListener("click", function () {
      NIVEL_TREINO = bt.dataset.nivel;
      pintaNivel();
    });
  }
  pintaNivel();
}, "niveis do treino");

async function treinarVoz() {
  if (desviaPraPrevia("treinar")) return;
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  const nome = texto(elNomeVoz);
  if (!nome) { avisaFalta("dê um nome pra voz nova."); return; }
  const epocas = Number(elEpocas.value) || 300;

  /* RODANDO fecha ANTES do primeiro await: a coleta de trechos faz varios
   * round-trips no host, e um clique duplo nessa janela disparava DOIS treinos
   * com o mesmo nome de voz. Os returns de desistencia reabrem. */
  RODANDO = true;
  atualizaBotao();
  const desiste = (msg) => {
    if (msg) escreve(msg, "erro");
    RODANDO = false;
    atualizaBotao();
  };

  let trechos = [];
  try {
    const { audio } = await itensDeAudioSelecionados();
    for (const item of audio) {
      const pi = await item.getProjectItem();
      const clip = await ppro.ClipProjectItem.cast(pi);
      const caminho = await clip.getMediaFilePath();
      const ini = (await item.getInPoint()).seconds;
      const fim = (await item.getOutPoint()).seconds;
      trechos.push({ arquivo: caminho, inicio: ini, fim: fim });
    }
  } catch (e) { desiste("treinar: " + msgErro(e)); return; }
  if (!trechos.length) { desiste("selecione na timeline os trechos da voz."); return; }

  /* Trava dura (pedido de 15/08, depois do treino de 2 s): pouquissimo material
   * nao gera modelo utilizavel — erro aqui mesmo, sem gastar GPU. O servidor
   * confere de novo com a duracao REAL dos arquivos (MINIMO_TREINO_S no
   * servidor.py): mudou la, mude aqui. FLOOR, nunca round: 29,6 s arredondado
   * pra 30 passaria aqui e o servidor recusaria depois de ja ter cortado. */
  const durReal = trechos.reduce((s, x) => s + (x.fim - x.inicio), 0);
  const dur = Math.floor(durReal);
  /* COM O QUE TEM (24/08). O painel parou de barrar por material curto: o
     "rapida" nao treina e aceita qualquer tamanho, e os que treinam REPETEM
     o material no servidor ate encher o minimo. So o piso absoluto de 1,5 s
     continua, porque abaixo disso o fatiador do ajuste fino descarta tudo. */
  if (durReal <= 0) { desiste("a seleção não tem áudio nenhum."); return; }
  if (NIVEL_TREINO !== "rapida" && durReal < 1.5) {
    desiste("são só " + durReal.toFixed(1) + " s — curto demais até pra repetir: "
            + "o fatiador descarta pedaço com menos de 1 s. Use o nível Rápida, "
            + "que aceita qualquer tamanho.");
    plim("erro");
    return;
  }
  escreve("treinando \"" + nome + "\" com " + trechos.length + " trecho(s) da timeline (" +
          dur + " s de material) — nível " + NIVEL_TREINO.toUpperCase() +
          (NIVEL_TREINO === "maxima" ? ", " + epocas + " épocas (horas)."
           : NIVEL_TREINO === "boa" ? ", uns 5 minutos." : ", sai na hora.") +
          (NIVEL_TREINO !== "rapida" && durReal < 30
            ? " AVISO: menos de 30 s — vou REPETIR o material pra dar liga; a voz sai grosseira, mas roda."
            : NIVEL_TREINO !== "rapida" && dur < 60
            ? " AVISO: menos de 1 min — serve pra teste, a voz sai fraca." : "") +
          (NIVEL_TREINO === "rapida" ? ""
           : " Se travar por falta de VRAM, feche o Ollama e rode de novo."));
  barraMostra();
  try {
    const r = await fetch(DUBX + "/api/processar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        modo: "treinar_premiere",
        nome, epocas, trechos, nivel: NIVEL_TREINO,
      }),
    });
    const { tarefa, erro } = await r.json();
    if (!tarefa) throw new Error(erro || "o DUBX nao aceitou o treino");

    for (;;) {
      await espera(2000);
      const e = await (await fetch(DUBX + "/api/estado?t=" + tarefa)).json();
      if (e.estado === "pronto") break;
      if (e.estado === "cancelado") throw new Error("cancelado");
      if (e.estado === "erro") throw new Error(e.mensagem || "o treino falhou");
      // fracao real quando o servidor mede; senao (etapa - 1) / de (29/09)
      const frac = typeof e.fracao === "number" ? e.fracao
                 : (e.etapa && e.de) ? (e.etapa - 1) / e.de : 0.1;
      barraSet(frac, e.mensagem || "treinando…");
    }
    barraSet(1, "voz pronta");
    escreve("voz \"" + nome + "\" treinada e já disponível pra trocar e falar.", "ok");
    plim("ok");
    elNomeVoz.value = "";
    await listaVozes();
    elVozes.value = nome;
  } catch (e) {
    escreve("treinar: " + msgErro(e), "erro");
    plim("erro");
  }
  RODANDO = false;
  atualizaBotao();
  BARRA_TIMER = setTimeout(barraEsconde, 2500);
}

/* --------------------------------------------------------- ambientacao
 * Redesenho de 15/08 (noite), pedido do Luan: ele NAO configura efeito nenhum
 * ("sempre que preciso fazer e uma guerra"). Os ambientes sao presets DSP
 * PRONTOS do servidor (reverb por IR + EQ + compressao, escala conhecida) e
 * o painel so escolhe e manda aplicar NO ARQUIVO, pelo mesmo laco de lote do
 * Melhorar. O capturar-efeitos-do-Premiere saiu de cena (mecanismo provado
 * pela sonda, registrado no CONTEXTO.md — volta se um dia fizer falta).
 * Futuro combinado: analisar o frame do video decide o ambiente sozinho. */

const elAmbientes = document.getElementById("ambientes");
const btAmbientar = document.getElementById("ambientar");
const elDicaAmb = document.getElementById("dicaAmbiente");
const elNomeAmb = document.getElementById("nomeAmbiente");
let DETALHES_AMB = {};   // nome -> {descricao, tipo: "dsp" | "efeito"}
let INTENS = 100;

async function listaAmbientes() {
  try {
    const { detalhes } = await (await fetch(DUBX + "/api/ambientes")).json();
    DETALHES_AMB = {};
    const atual = elAmbientes.value;
    elAmbientes.textContent = "";
    const zero = document.createElement("option");
    zero.value = ""; zero.textContent = "— ambientes —";
    elAmbientes.appendChild(zero);
    const grupos = { dsp: "Prontos (DUBX)", efeito: "Capturados do Premiere" };
    for (const tipo of ["dsp", "efeito"]) {
      const itens = (detalhes || []).filter((d) => (d.tipo || "dsp") === tipo);
      if (!itens.length) continue;
      const g = document.createElement("optgroup");
      g.label = grupos[tipo];
      for (const d of itens) {
        DETALHES_AMB[d.nome] = { descricao: d.descricao || "", tipo };
        const o = document.createElement("option");
        o.value = d.nome; o.textContent = d.nome;
        g.appendChild(o);
      }
      elAmbientes.appendChild(g);
    }
    if (DETALHES_AMB[atual]) elAmbientes.value = atual;
    voltouContato("ambientes");
  } catch (e) {
    avisaSemContato("ambientes", listaAmbientes);
  }
}

document.querySelectorAll(".sbt-intens").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".sbt-intens").forEach((x) => x.classList.remove("ativa"));
    b.classList.add("ativa");
    INTENS = Number(b.dataset.int);
  });
});

function mostraDicaAmbiente() {
  const d = DETALHES_AMB[elAmbientes.value];
  elDicaAmb.textContent = (d && d.descricao) ||
    "gravado no arquivo com backup; reaplicar outro ambiente parte sempre do som seco original.";
}

/* AMBIENTE AUTOMATICO — o servidor olha o FRAME e diz de que lugar e a cena.
 *
 * O lado servidor esta pronto e validado desde 16/08 (3/3 com footage real):
 * POST /api/ambiente_do_video {video, tempo} -> nome do preset, ou null pra
 * exterior (que fica seco de proposito). O que faltava era ACHAR o clipe de
 * video que esta por cima do audio selecionado.
 *
 * Como acha: varre as faixas de VIDEO da sequencia e pega o clipe cujo
 * intervalo [inicio, fim) cobre o inicio do clipe de AUDIO. Faixa de cima
 * ganha (e a que aparece na imagem). O tempo mandado e relativo ao ARQUIVO
 * de video, nao a sequencia — inPoint + (quanto do clipe ja passou).
 *
 * ⚠ ESCRITO CONTRA O .d.ts, NAO EXERCITADO AO VIVO (23/08). Os equivalentes
 * de AUDIO (getStartTime, getProjectItem, getMediaFilePath) ja rodam provados
 * neste painel; a parte de VIDEO nunca passou pela sonda. Se falhar, o erro
 * aparece no log e nada e alterado no projeto. */
async function videoSobreposto(seq, itemAudio) {
  const inicioAudio = await itemAudio.getStartTime();
  const seg = (tt) => (tt && tt.seconds !== undefined) ? tt.seconds : 0;
  const alvo = seg(inicioAudio);
  const nTrilhas = await seq.getVideoTrackCount();
  // de cima pra baixo: a faixa mais alta e a que aparece na imagem
  for (let i = nTrilhas - 1; i >= 0; i--) {
    let trilha;
    try { trilha = await seq.getVideoTrack(i); } catch (_) { continue; }
    if (!trilha) continue;
    let itens = [];
    try {
      itens = trilha.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || [];
    } catch (_) { continue; }
    for (const it of itens) {
      try {
        const ini = seg(await it.getStartTime());
        const fim = seg(await it.getEndTime());
        if (alvo < ini || alvo >= fim) continue;
        const caminho = await caminhoDoItem(await it.getProjectItem());
        if (!caminho) continue;
        // tempo DENTRO do arquivo: onde o clipe comeca + o que ja correu
        const entrada = seg(await it.getInPoint());
        return { caminho, tempo: Math.max(0, entrada + (alvo - ini)) };
      } catch (_) { /* clipe estranho: proximo */ }
    }
  }
  return null;
}

async function ambienteAutomatico() {
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  const fila = CLIPES.filter((c) => c.marcado);
  if (!fila.length) { avisaFalta("selecione clipes na timeline e clique em Analisar seleção."); return; }
  RODANDO = true; atualizaBotao();
  try {
    const project = await ppro.Project.getActiveProject();
    const seq = project && await project.getActiveSequence();
    if (!seq) throw new Error("nenhuma sequência ativa");

    const c = fila[0];
    if (!c.item) throw new Error("re-analise a seleção (falta o clipe da timeline)");
    escreve("procurando o vídeo por cima de " + c.nome + "…");
    const achado = await videoSobreposto(seq, c.item);
    if (!achado) throw new Error("não achei clipe de vídeo nesse ponto da timeline");

    escreve("olhando o frame de " + achado.caminho.split(/[\\/]/).pop()
            + " em " + achado.tempo.toFixed(1) + "s…");
    const r = await fetch(DUBX + "/api/ambiente_do_video", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video: achado.caminho, tempo: achado.tempo }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.erro || "o servidor recusou");

    if (!j.ambiente) {
      escreve("a cena é externa (céu aberto) — o certo aqui é deixar SECO, "
              + "sem ambiente.", "ok");
      return;
    }
    // so ESCOLHE no dropdown; quem manda ambientar continua sendo ele
    const achou = Array.from(elAmbientes.options).some((o) => o.value === j.ambiente);
    if (!achou) throw new Error("o servidor sugeriu \"" + j.ambiente
                                + "\", que não está na sua lista");
    elAmbientes.value = j.ambiente;
    mostraDicaAmbiente();
    escreve("a cena parece: " + j.ambiente + " — já escolhi no campo. "
            + "Confira e clique em Ambientar seleção.", "ok");
  } catch (e) {
    escreve("ambiente automático: " + msgErro(e), "erro");
  } finally {
    RODANDO = false; atualizaBotao();
  }
}

function ambientar() {
  const nome = elAmbientes.value;
  if (!nome) { avisaFalta("escolha um ambiente primeiro."); return; }
  const det = DETALHES_AMB[nome] || {};
  if (det.tipo === "efeito") {
    // sem trava, o segundo clique anexava os efeitos DE NOVO nos mesmos
    // clipes (createAppendComponentAction empilha). Auditoria 20/08.
    if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
    RODANDO = true; atualizaBotao();
    return aplicarEfeitos(nome).finally(() => { RODANDO = false; atualizaBotao(); });
  }
  return rodaLote(
    { modo: "ambientar_no_lugar", ambiente: nome, intensidade: INTENS },
    "ambientando (" + nome + " " + INTENS + "%)");
}

/* --------------------- ambientes capturados: efeito no clipe, via API UXP.
 * Mecanismo provado pela sonda de 15/08: append em transacao -> reler a
 * cadeia -> o recem-anexado e o ULTIMO indice -> setar params um a um.
 * E o caminho pros presets de FABRICA da Adobe: o Luan aplica o preset num
 * clipe (2 cliques), captura UMA vez, e vira item do dropdown pra sempre. */

const INTRINSECOS = ["Volume", "Volume do canal", "Channel Volume",
                     "Panorâmica", "Panner", "Equilíbrio", "Balance"];

async function capturarAmbiente() {
  const nome = texto(elNomeAmb);
  if (!nome) { avisaFalta("dê um nome pro ambiente antes de salvar."); return; }
  try {
    const { audio } = await itensDeAudioSelecionados();
    const item = audio[0];
    const cadeia = await item.getComponentChain();
    let n = cadeia.getComponentCount();
    if (n && typeof n.then === "function") n = await n;

    const efeitos = [];
    for (let i = 0; i < n; i++) {
      let c = cadeia.getComponentAtIndex(i);
      if (c && typeof c.then === "function") c = await c;
      const nomeEfeito = await c.getDisplayName();
      if (INTRINSECOS.includes(nomeEfeito)) continue;

      let total = c.getParamCount();
      if (total && typeof total.then === "function") total = await total;
      const params = [];
      for (let j = 0; j < total; j++) {
        let p = c.getParam(j);
        if (p && typeof p.then === "function") p = await p;
        let valor = null;
        try {
          const v = await p.getValueAtTime(ppro.TickTime.TIME_ZERO);
          // so tipos que sobrevivem a JSON e voltam inteiros pro createKeyframe
          valor = (typeof v === "number" || typeof v === "boolean" || typeof v === "string") ? v : null;
        } catch (e) { /* parametro ilegivel: fica null e nao se aplica */ }
        params.push({ nome: p.displayName, valor });
      }
      efeitos.push({ efeito: nomeEfeito, params });
    }
    if (!efeitos.length) { avisaFalta("o clipe não tem efeito além dos intrínsecos — aplica um preset de fábrica nele antes de salvar."); return; }

    const r = await fetch(DUBX + "/api/ambiente", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nome, efeitos }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.erro || "o DUBX não salvou");
    escreve("ambiente \"" + j.salvo + "\" salvo: " + efeitos.map((e) => e.efeito).join(" + "), "ok");
    elNomeAmb.value = "";
    await listaAmbientes();
    elAmbientes.value = j.salvo;
    atualizaBotao();
  } catch (e) {
    escreve("capturar: " + msgErro(e), "erro");
  }
}

async function aplicarEfeitos(nome) {
  try {
    const r = await fetch(DUBX + "/api/ambiente?nome=" + encodeURIComponent(nome));
    if (!r.ok) throw new Error("ambiente \"" + nome + "\" não encontrado no DUBX");
    const amb = await r.json();

    const { project, audio } = await itensDeAudioSelecionados();
    const disponiveis = await ppro.AudioFilterFactory.getDisplayNames();
    escreve("aplicando \"" + nome + "\" (efeito no clipe) em " + audio.length + " clipe(s)…");

    let aplicados = 0;
    for (const item of audio) {
      try {
        for (const ef of amb.efeitos) {
          if (!disponiveis.includes(ef.efeito)) {
            throw new Error("efeito \"" + ef.efeito + "\" não existe neste Premiere (idioma diferente?)");
          }
          const comp = await ppro.AudioFilterFactory.createComponentByDisplayName(ef.efeito, item);
          const cadeia = await item.getComponentChain();
          project.lockedAccess(() => {
            project.executeTransaction((ca) => {
              ca.addAction(cadeia.createAppendComponentAction(comp));
            }, "DUBX: ambiente " + nome);
          });

          /* o objeto da factory e opaco: rele a cadeia e pega o RECEM-anexado
           * (ultimo indice) pra setar os parametros nele */
          const cadeia2 = await item.getComponentChain();
          let n = cadeia2.getComponentCount();
          if (n && typeof n.then === "function") n = await n;
          let alvo = cadeia2.getComponentAtIndex(n - 1);
          if (alvo && typeof alvo.then === "function") alvo = await alvo;

          let total = alvo.getParamCount();
          if (total && typeof total.then === "function") total = await total;
          for (let j = 0; j < Math.min(total, ef.params.length); j++) {
            const alvoValor = ef.params[j] ? ef.params[j].valor : null;
            if (alvoValor === null || alvoValor === undefined) continue;
            try {
              let p = alvo.getParam(j);
              if (p && typeof p.then === "function") p = await p;
              project.lockedAccess(() => {
                const kf = p.createKeyframe(alvoValor);
                project.executeTransaction((ca) => {
                  ca.addAction(p.createSetValueAction(kf, true));
                }, "DUBX: " + ef.efeito + " [" + j + "]");
              });
            } catch (e) {
              escreve("  param [" + j + "] " + (ef.params[j].nome || "") + ": " + msgErro(e), "erro");
            }
          }
        }
        aplicados++;
      } catch (e) {
        escreve("  clipe pulado: " + msgErro(e), "erro");
      }
    }
    escreve("ambiente \"" + nome + "\" aplicado em " + aplicados + "/" + audio.length + " clipe(s).",
            aplicados === audio.length ? "ok" : "erro");
  } catch (e) {
    escreve("aplicar: " + msgErro(e), "erro");
  }
}

async function itensDeAudioSelecionados() {
  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error("nenhum projeto aberto");
  const seq = await project.getActiveSequence();
  if (!seq) throw new Error("nenhuma sequência ativa");
  const selecao = await seq.getSelection();
  const itens = await selecao.getTrackItems();
  if (!itens || !itens.length) throw new Error("nada selecionado na timeline");
  const audio = [];
  for (const item of itens) {
    let tipo = item.getMediaType ? item.getMediaType() : undefined;
    if (tipo && typeof tipo.then === "function") tipo = await tipo;
    if (String(tipo) === AUDIO_GUID) audio.push(item);
  }
  if (!audio.length) throw new Error("nenhum clipe de áudio na seleção");
  return { project, audio };
}

/* ------------------------------------------------------- apagar voz
 * Destrutivo de verdade (biblioteca + registro de treino + dataset), entao
 * exige DOIS cliques: o primeiro vira "Confirmar?" por 4 segundos. */
let CONFIRMA_APAGAR = null;
let APAGAR_TIMER = null;
async function apagarVoz() {
  const nome = elVozes.value;
  if (!nome) { avisaFalta("escolha a voz que quer apagar."); return; }
  if (CONFIRMA_APAGAR !== nome) {
    CONFIRMA_APAGAR = nome;
    btApagarVoz.textContent = "Confirmar?";
    btApagarVoz.classList.add("confirmar");
    clearTimeout(APAGAR_TIMER);        // o timer da voz ANTERIOR encurtava
    APAGAR_TIMER = setTimeout(() => {  // a janela desta. Auditoria 20/08.
      CONFIRMA_APAGAR = null;
      btApagarVoz.textContent = "Apagar";
      btApagarVoz.classList.remove("confirmar");
    }, 4000);
    return;
  }
  CONFIRMA_APAGAR = null;
  btApagarVoz.textContent = "Apagar";
  btApagarVoz.classList.remove("confirmar");
  try {
    const r = await fetch(DUBX + "/api/apagar-voz", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nome }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.erro || "o DUBX nao apagou");
    escreve("voz \"" + nome + "\" apagada do PC (" + j.pastas.length + " pasta(s)).", "ok");
    await listaVozes();
    atualizaBotao();
  } catch (e) {
    escreve("apagar voz: " + msgErro(e), "erro");
  }
}
aoClicar(btApagarVoz, apagarVoz, "apagar voz");

/* ---------------------------------------------------------------- legenda
 * SRT do proprio DUBX, pra ele parar de depender da transcricao do Premiere
 * ("é uma merda, vou ter que ficar corrigindo na mão", 19/08). Com roteiro
 * colado o texto sai EXATO — nome proprio e pontuacao inclusos. */
const PRESETS_LEG = {
  normal: {largura: 42, linhas: 2, min_dur: 1.0, gap: 0.08, pontuacao: "tudo",
           palavrao: "censurar",
           dica: "Normal: 2 linhas de 42 caracteres, com pontuação. O SRT sai na pasta do próprio arquivo."},
  mini:   {largura: 15, linhas: 1, min_dur: 3.0, gap: 0, pontuacao: "so-interrogacao",
           palavrao: "censurar",
           dica: "Plexus Mini: linha única de 15 caracteres, sem pontuação (só os \"?\"). Igual ao seu preset do Premiere."},
};
let PRESET_LEG = "normal";
document.querySelectorAll(".sbt-preset").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".sbt-preset").forEach((x) => x.classList.remove("ativa"));
    b.classList.add("ativa");
    PRESET_LEG = b.dataset.preset;
    document.getElementById("dicaLegenda").textContent = PRESETS_LEG[PRESET_LEG].dica;
  });
});

async function legendar() {
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  const fila = CLIPES.filter((c) => c.marcado);
  if (!fila.length) { avisaFalta("selecione clipes na timeline e clique em Analisar seleção."); return; }
  RODANDO = true;
  atualizaBotao();
  barraMostra();
  const cfg = Object.assign({}, PRESETS_LEG[PRESET_LEG]);
  delete cfg.dica;   // "dica" e texto de tela, nao ajuste; ia junto pro servidor
  escreve("gerando legenda de " + fila.length + " arquivo(s) — o .srt sai na pasta de cada um.");
  let feitos = 0;
  const srts = [];   // pra importar tudo de uma vez no fim
  for (let i = 0; i < fila.length; i++) {
    LOTE = { idx: i, total: fila.length };
    const c = fila[i];
    try {
      estadoDo(c, "transcrevendo…");
      const r = await fetch(DUBX + "/api/processar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({
          modo: "legenda", arquivo: c.caminho,
          roteiro: texto(elRotLegenda),   // null cru viraria "None" no Python
          personagem: elLegPersonagem.checked ? "sim" : "nao",
        }, cfg)),
      });
      const { tarefa, erro } = await r.json();
      if (!tarefa) throw new Error(erro || "o DUBX nao aceitou a tarefa");
      for (;;) {
        await espera(1000);
        const e = await (await fetch(DUBX + "/api/estado?t=" + tarefa)).json();
        if (e.estado === "pronto") {
          estadoDo(c, "pronto", "ok");
          escreve("OK " + (e.nome || c.nome), "ok");
          if (e.saida) srts.push(e.saida);
          feitos++;
          break;
        }
        if (e.estado === "cancelado") throw new Error("cancelado");
        if (e.estado === "erro") throw new Error(e.mensagem || "falhou");
        estadoDo(c, e.mensagem || "…");
        barraTarefa(e, c.nome);
      }
    } catch (e) {
      estadoDo(c, "erro", "erro");
      escreve(c.nome + ": " + msgErro(e), "erro");
    }
  }
  LOTE = null;
  barraSet(1, "concluído");

  /* Poe o .srt DENTRO do projeto. Colocar na TIMELINE nao da: o UXP nao
   * expoe isso (CaptionTrack so renomeia/muta/le, e insert/overwrite so
   * aceitam faixa de video ou audio). Importar e o mais perto que da —
   * vira um arraste no painel de Projeto em vez de Texto -> Legendas ->
   * Importar -> achar a pasta. Queixa dele no teste de 23/08. */
  let noProjeto = 0;
  if (srts.length) {
    try {
      const project = await ppro.Project.getActiveProject();
      const raiz = project && await project.getRootItem();
      if (raiz && await project.importFiles(srts, true, raiz)) {
        noProjeto = srts.length;
      }
    } catch (e) {
      escreve("o .srt ficou no disco, mas nao entrou no projeto: "
              + msgErro(e), "erro");
    }
  }

  escreve("fim: " + feitos + "/" + fila.length + ". "
          + (noProjeto
             ? "A legenda ja esta no seu projeto — arraste da janela "
               + "Projeto pra uma faixa de legenda."
             : "No Premiere: Texto → Legendas → Importar legendas do arquivo."),
          feitos === fila.length ? "ok" : "erro");
  plim(feitos === fila.length ? "ok" : "erro");
  RODANDO = false;
  atualizaBotao();
  // solto, este timer sumia com a barra do lote SEGUINTE. Auditoria 20/08.
  BARRA_TIMER = setTimeout(barraEsconde, 2500);
}
aoClicar(btLegendar, legendar, "legendar");

/* ===================================================================
 * FAIXAS NA TIMELINE — o coracao do Separar/Dublar aqui dentro.
 *
 * O padrao ja existia e estava PROVADO no Beat Match (CEP), so que em
 * ExtendScript. A traducao pro UXP:
 *     CEP: app.project.importFiles(paths, true, bin, false)
 *          qe.addTracks(...)            <- API publica do CEP nao criava faixa
 *          seq.audioTracks[i].overwriteClip(item, tempo)
 *     UXP: project.importFiles(paths, true, raiz)
 *          SequenceEditor.getEditor(seq)
 *          createOverwriteItemAction(pi, tempo, videoIdx, audioIdx)
 *
 * ⚠️ SOBRESCREVER, NAO INSERIR. `insert` faz ripple e empurraria a edicao
 * inteira dele. Como as faixas de destino sao NOVAS e vazias, sobrescrever
 * e semanticamente certo: nao ha nada embaixo. O insert so entra como
 * ultimo recurso (com limitShift=true) e AVISA no log quando entra.
 *
 * ⚠️ NADA AQUI FOI RODADO DENTRO DO PREMIERE AINDA (20/08). Por isso as
 * tentativas em cadeia: `videoTrackIndex = -1` e o unico ponto que o .d.ts
 * nao esclarece (so diz `number`), entao caimos pra 0 se ele reclamar.
 * O que funcionar sai escrito no log e vai na sonda.
 * =================================================================== */

async function caminhoDoItem(projectItem) {
  try {
    const clip = await ppro.ClipProjectItem.cast(projectItem);
    return await clip.getMediaFilePath();
  } catch (_) { return null; }
}

/* ONDE POR O RESULTADO — escolha dele (20/08: "eu que tenho q escolher se vai
 * pra faixas vazias ou se cria tres novas").
 *
 *   "vazias" (padrao) — usa as faixas de audio SEM CLIPE que ja existem.
 *                       Sobrescrever em faixa vazia e seguro por construcao.
 *   "criar"           — usa `insert` com indice MAIOR que a contagem. O doc do
 *                       `createInsertProjectItemAction` diz textualmente que
 *                       isso CRIA a faixa. E o unico caminho no UXP: varri o
 *                       .d.ts inteiro (20/08) e nao ha addTracks, createTrack
 *                       nem ponte pra ExtendScript/QE — o Beat Match so
 *                       consegue porque e CEP e chama `qe.addTracks`.
 *                       Vai com limitShift=true. Faixa nova esta VAZIA, entao
 *                       nao ha o que deslocar nela; o risco de ripple que eu
 *                       levantei antes era exagerado. Ainda assim nao e o
 *                       padrao ate ele confirmar num teste.
 */
let DESTINO = "auto";
document.querySelectorAll(".sbt-modotrab").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".sbt-modotrab").forEach((x) =>
      x.classList.toggle("ativa", x === b));
    MODOTRAB = b.dataset.mt;
  });
});

document.querySelectorAll(".sbt-destino").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".sbt-destino").forEach((x) => x.classList.remove("ativa"));
    b.classList.add("ativa");
    DESTINO = b.dataset.destino;
    document.getElementById("dicaDestino").textContent = DESTINO === "criar"
      ? "Sempre cria faixas novas, mesmo que existam vazias."
      : DESTINO === "vazias"
      ? "Só usa faixas vazias que já existem. Se faltar, os stems que sobrarem ficam de fora."
      : "Enche as faixas vazias que já existem e cria só o que faltar. É o que você quer na maioria das vezes.";
  });
});

// TrackItemType.CLIP e 1 no enum. Se o namespace nao estiver exposto, cair no
// numero — senao TODA faixa daria erro e o painel diria "nao ha faixa vazia",
// que e a mentira mais cara possivel aqui.
function tipoClipe() {
  return (ppro.Constants && ppro.Constants.TrackItemType
          && ppro.Constants.TrackItemType.CLIP !== undefined)
    ? ppro.Constants.TrackItemType.CLIP : 1;
}

async function itensDaFaixa(tr) {
  let itens = tr.getTrackItems(tipoClipe(), false);
  if (itens && typeof itens.then === "function") itens = await itens;
  return itens || [];
}

/* EM QUE FAIXA este clipe esta. O UXP nao expoe isso no trackItem, entao
 * varre as faixas comparando inicio+fim. Serve pra por o resultado ABAIXO do
 * original — pedido dele em 23/08: "eu queria que o clipe ficasse embaixo e
 * nao em cima". Indice 0 = A1 = a faixa mais ALTA; "abaixo" e indice MAIOR. */
async function faixaDoClipe(seq, item) {
  if (!item) return -1;
  let ini, fim;
  try {
    ini = (await item.getStartTime()).seconds;
    fim = (await item.getEndTime()).seconds;
  } catch (_) { return -1; }
  const total = await seq.getAudioTrackCount();
  for (let i = 0; i < total; i++) {
    try {
      const tr = await seq.getAudioTrack(i);
      for (const it of await itensDaFaixa(tr)) {
        const a = (await it.getStartTime()).seconds;
        const b = (await it.getEndTime()).seconds;
        if (Math.abs(a - ini) < 0.02 && Math.abs(b - fim) < 0.02) return i;
      }
    } catch (_) { /* faixa ilegivel: proxima */ }
  }
  return -1;
}

async function faixasVazias(seq, quantas, abaixoDe, soAbaixo) {
  /* Procura faixas de audio SEM NENHUM CLIPE. Sobrescrever numa faixa vazia e
   * seguro por construcao: nao ha o que sobrescrever.
   *
   * Junta TODAS antes de escolher (antes parava na primeira que servia) pra
   * poder preferir as de BAIXO do clipe de origem. Se nao houver o bastante
   * abaixo, completa com as de cima — melhor pousar acima do que nao pousar. */
  const total = await seq.getAudioTrackCount();
  const livres = [];
  for (let i = 0; i < total; i++) {
    try {
      const tr = await seq.getAudioTrack(i);
      const itens = await itensDaFaixa(tr);
      if (!itens.length) livres.push({ i, nome: (tr.name || ("A" + (i + 1))) });
    } catch (_) { /* faixa que nao da pra ler: pular */ }
  }
  const piso = (abaixoDe === undefined || abaixoDe === null) ? -1 : abaixoDe;
  const abaixo = livres.filter((f) => f.i > piso);
  const acima = livres.filter((f) => f.i <= piso);
  // No Automatico, vazia ACIMA da origem nao serve: e melhor criar uma embaixo.
  // Pedido dele em 23/08 — "se nao tem faixa vazia embaixo, cria uma, po".
  return (soAbaixo ? abaixo : abaixo.concat(acima)).slice(0, quantas);
}

/* PÕE O LOTE INTEIRO DE UMA VEZ — UMA FAIXA POR FAIXA DE ORIGEM (25/08).
 *
 * Ele melhorou 8 clipes e levou 8 faixas novas, em escadinha (print de 25/08).
 * Era consequencia de entregar clipe a clipe: a primeira faixa vazia deixa de
 * estar vazia depois do primeiro pouso, entao cada resultado seguinte ia pra
 * uma faixa diferente.
 *
 * A chave e agrupar por FAIXA DE ORIGEM: dois clipes que estavam na mesma
 * faixa NUNCA se sobrepoem no tempo (o Premiere nao permite), entao cabem
 * todos numa faixa nova so. "Selecionei clipes de duas faixas" -> duas faixas
 * novas. Literalmente um clone da disposicao dele.
 *
 * Clipe cuja faixa de origem eu nao consigo identificar ganha grupo proprio:
 * sem saber onde ele estava, nao da pra garantir que nao colide com outro. */
async function colocaLoteNaTimeline(itens, etiqueta, silenciaOrigem) {
  if (!itens.length) return 0;
  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error("nenhum projeto aberto");
  const seq = await project.getActiveSequence();
  if (!seq) throw new Error("nenhuma sequência ativa");
  const raiz = await project.getRootItem();

  const caminhos = itens.map((x) => x.arquivo);
  if (!(await project.importFiles(caminhos, true, raiz)))
    throw new Error("o Premiere não importou os arquivos");

  const porCaminho = new Map();
  for (const it of await raiz.getItems()) {
    const p = await caminhoDoItem(it);
    if (p) porCaminho.set(String(p).toLowerCase(), it);
  }

  for (let k = 0; k < itens.length; k++) {
    const x = itens[k];
    x.pi = porCaminho.get(String(x.arquivo).toLowerCase());
    x.origem = await faixaDoClipe(seq, x.c && x.c.item);
    try { x.inicio = await x.c.item.getStartTime(); }
    catch (_) { x.inicio = ppro.TickTime.TIME_ZERO; }
    x.chave = x.origem >= 0 ? ("f" + x.origem) : ("solto" + k);
  }

  const grupos = new Map();
  for (const x of itens) {
    if (!x.pi) { escreve("não achei no projeto: " + x.etiqueta, "erro"); continue; }
    if (!grupos.has(x.chave)) grupos.set(x.chave, []);
    grupos.get(x.chave).push(x);
  }
  const chaves = [];
  grupos.forEach((_v, k) => chaves.push(k));
  if (!chaves.length) return 0;

  // dentro do grupo, em ORDEM DE TEMPO: com a faixa criada na hora, cada
  // insercao seguinte cai depois da anterior e nada e empurrado
  grupos.forEach((lista) => {
    lista.sort((a, b) => {
      const sa = (a.inicio && a.inicio.seconds) || 0;
      const sb = (b.inicio && b.inicio.seconds) || 0;
      return sa - sb;
    });
  });

  const totalFaixas = await seq.getAudioTrackCount();
  const origens = itens.map((x) => x.origem).filter((i) => i >= 0);
  const piso = origens.length ? Math.max.apply(null, origens) : -1;

  let alvos = [];
  if (DESTINO !== "criar")
    alvos = await faixasVazias(seq, chaves.length, piso, DESTINO === "auto");
  const usadasVazias = alvos.length;
  if (DESTINO !== "vazias" && alvos.length < chaves.length) {
    let prox = totalFaixas;
    for (const a of alvos) if (a.i + 1 > prox) prox = a.i + 1;
    while (alvos.length < chaves.length) {
      alvos.push({ i: prox, nome: "nova A" + (prox + 1), criar: true });
      prox++;
    }
  }
  if (!alvos.length) {
    throw new Error("não há faixa de áudio vazia — crie " + chaves.length
      + " na mão, ou troque ONDE PÔR O RESULTADO para \"Automático\"");
  }
  escreve("faixas na sequência: " + totalFaixas + " · grupos: " + chaves.length
          + " (uma faixa por faixa de origem) · vazias usadas: " + usadasVazias
          + " · novas: " + (alvos.length - usadasVazias));
  if (alvos.length < chaves.length)
    escreve("só " + alvos.length + " faixa(s) para " + chaves.length
            + " grupo(s) — o que sobrar fica de fora.", "erro");

  const editor = await ppro.SequenceEditor.getEditor(seq);
  const postar = [];
  for (let g = 0; g < chaves.length && g < alvos.length; g++) {
    const alvo = alvos[g];
    for (const x of grupos.get(chaves[g])) postar.push({ x, alvo });
  }

  const podeCalar = !!silenciaOrigem;
  const planos = podeCalar ? [[0, true], [1, true], [0, false], [1, false]]
                           : [[0, false], [1, false]];
  let entrou = false, ultimo = "", calou = false;
  for (let k = 0; k < planos.length && !entrou; k++) {
    const v = planos[k][0], comMudo = planos[k][1];
    try {
      await project.lockedAccess(() => {
        project.executeTransaction((ca) => {
          for (const p of postar) {
            const vid = v === 0 ? -1 : 0;
            ca.addAction(p.alvo.criar
              ? editor.createInsertProjectItemAction(p.x.pi, p.x.inicio, vid, p.alvo.i, true)
              : editor.createOverwriteItemAction(p.x.pi, p.x.inicio, vid, p.alvo.i));
            if (comMudo && p.x.c && p.x.c.item && p.x.c.item.createSetDisabledAction)
              ca.addAction(p.x.c.item.createSetDisabledAction(true));
          }
        }, "DUBX: " + etiqueta);
      });
      entrou = true; calou = comMudo;
    } catch (e) { ultimo = msgErro(e); }
  }

  if (!entrou) { escreve("  nada entrou na timeline: " + ultimo, "erro"); return 0; }
  for (const p of postar)
    escreve("  " + p.x.etiqueta + " -> " + p.alvo.nome + " (faixa " + (p.alvo.i + 1) + ")", "ok");
  if (calou)
    escreve("  os clipes originais ficaram MUDOS pra comparação — Ctrl+Z volta tudo.", "ok");
  escreve("  (um Ctrl+Z desfaz o lote inteiro)", "ok");
  return postar.length;
}

/* ESCOLHER A FAIXA NO CONCLUIR (Kakashi 29/10 16:35 [16:52] «quando eu colocar em concluir aparece uma lista das faixas de áudio
 * disponíveis... eu clico na que eu quiser»; PC 17:36 [1:15] «cliquei em concluir pra adicionar a frase na A10... e não foi»).
 * Lista TODAS as faixas de audio; marca as que estao livres no trecho do clipe (nao so as vazias inteiras — a A10 dele ja tem
 * outros clipes) e sugere a primeira livre ABAIXO da faixa de origem. Devolve {i, nome} ou null (cancelou). */
/* MEMORIA DA FAIXA DO DUBX (Kakashi 17:53 [01:13–02:32]: «não é a faixa livre no foda-se, é a faixa que você criou... Sempre cria
 * uma faixa de áudio pra colocar... se eu quisesse trocar todas essas vozes, teria que ter criado essa A10... e a A17 também»).
 * Por faixa de origem, a faixa do DUBX escolhida da primeira vez (criada ou a livre logo abaixo); as proximas caem NELA, mesmo que
 * ja tenha clipes do DUBX. Vive enquanto o painel vive (e na sequencia certa pelo id). */
const DUBX_FAIXA = {};   // "<seqId>:<origem>" -> indice da faixa
async function idDaSequencia(seq) {
  try { return String(await seq.guid || seq.guid || seq.name || ""); } catch (_) { return ""; }
}

/* VOLUME HERDADO: le o Nivel do componente Volume do clipe ORIGINAL e poe o mesmo valor no clipe que acabou de pousar na faixa do
 * DUBX (mesmo inicio). O valor viaja como veio (o UXP guarda o Volume em amplitude, 0 dB = 0,1778 — copiar o numero cru mantem o
 * dB igual). Keyframes no original: copia o valor do INICIO do clipe (nao reproduz a curva). */
async function nivelDoVolume(item) {
  const cadeia = await item.getComponentChain();
  let n = cadeia.getComponentCount(); if (n && typeof n.then === "function") n = await n;
  for (let i = 0; i < n; i++) {
    let comp = cadeia.getComponentAtIndex(i); if (comp && typeof comp.then === "function") comp = await comp;
    const nome = await comp.getDisplayName();
    if (nome !== "Volume") continue;
    let total = comp.getParamCount(); if (total && typeof total.then === "function") total = await total;
    for (let j = 0; j < total; j++) {
      let prm = comp.getParam(j); if (prm && typeof prm.then === "function") prm = await prm;
      const dn = String(prm.displayName || "");
      if (/n[ií]vel|level/i.test(dn) || (total === 2 && j === 1)) {
        const t0 = await item.getStartTime();
        const v = await prm.getValueAtTime(t0);
        return { param: prm, valor: v, nome: dn };
      }
    }
  }
  return null;
}
async function herdaVolume(c, faixaIdx) {
  if (!c || !c.item) return;
  const ppro = require("premierepro");
  const project = await ppro.Project.getActiveProject();
  const seq = project && await project.getActiveSequence();
  if (!seq) return;
  const orig = await nivelDoVolume(c.item);
  if (!orig || typeof orig.valor !== "number") { escreve("volume: o original não tem Nível legível — ficou o padrão."); return; }
  const ini = (await c.item.getStartTime()).seconds;
  const tr = await seq.getAudioTrack(faixaIdx);
  let novo = null;
  for (const it of await itensDaFaixa(tr)) {
    const a = (await it.getStartTime()).seconds;
    if (Math.abs(a - ini) < 0.02) { novo = it; break; }
  }
  if (!novo) { escreve("volume: não achei o clipe novo na A" + (faixaIdx + 1) + "."); return; }
  const dest = await nivelDoVolume(novo);
  if (!dest) { escreve("volume: o clipe novo não tem Volume legível."); return; }
  if (Math.abs(Number(dest.valor) - orig.valor) < 1e-6) return;   // ja igual
  await project.lockedAccess(() => {
    project.executeTransaction((ca) => {
      ca.addAction(dest.param.createSetValueAction(dest.param.createKeyframe(orig.valor), true));
    }, "DUBX: volume igual ao original");
  });
  const db = (v) => (20 * Math.log10(Math.max(1e-9, Number(v)) / 0.1778)).toFixed(1).replace(".", ",");
  escreve("volume do clipe novo = o do original (" + db(orig.valor) + " dB).", "ok");
}

/* EFEITOS HERDADOS (Kakashi 29/10 20:27 [00:00]: «o clipe foi aqui pra baixo, mas não herdou o efeito»): copia pro clipe novo os
 * efeitos de CLIPE do original (os que ele pos: reverb, EQ, dinamica...), na mesma ordem e com os mesmos valores. Os fixos do
 * Premiere (Volume, Volume do canal, Panner) vem por indice: o clipe novo nasce so com eles, entao copia-se do indice N em diante.
 * Fica de fora o Deslocador de tom (Pitch Sync de velocidade do ORIGINAL: o arquivo novo ja sai no tempo certo). Keyframes: copia
 * o valor do INICIO do clipe, nao a curva. */
async function herdaEfeitos(c, faixaIdx) {
  if (!c || !c.item) return;
  const ppro = require("premierepro");
  const project = await ppro.Project.getActiveProject();
  const seq = project && await project.getActiveSequence();
  if (!seq) return;
  const ini = (await c.item.getStartTime()).seconds;
  const tr = await seq.getAudioTrack(faixaIdx);
  let novo = null;
  for (const it of await itensDaFaixa(tr)) {
    if (Math.abs((await it.getStartTime()).seconds - ini) < 0.02) { novo = it; break; }
  }
  if (!novo) { escreve("efeitos: não achei o clipe novo na A" + (faixaIdx + 1) + "."); return; }
  const aw = async (v) => (v && typeof v.then === "function" ? await v : v);
  const cadO = await c.item.getComponentChain();
  const nO = await aw(cadO.getComponentCount());
  let cadN = await novo.getComponentChain();
  const base = await aw(cadN.getComponentCount());
  const t0O = await c.item.getStartTime(), t0N = await novo.getStartTime();
  const copiados = [], falhas = [];
  for (let i = base; i < nO; i++) {
    const comp = await aw(cadO.getComponentAtIndex(i));
    let nome = "";
    try { nome = await comp.getDisplayName(); } catch (_) { continue; }
    if (!nome || psCasa(nome, PS_EFEITO_FORTE, PS_EFEITO_PALAVRAS)) continue;
    try {
      const novoComp = await ppro.AudioFilterFactory.createComponentByDisplayName(nome, novo);
      await project.lockedAccess(() => {
        project.executeTransaction((ca) => { ca.addAction(cadN.createAppendComponentAction(novoComp)); }, "DUBX: efeito do original (" + nome + ")");
      });
      /* o objeto da factory e opaco: rele a cadeia e pega o recem-anexado */
      cadN = await novo.getComponentChain();
      const alvo = await aw(cadN.getComponentAtIndex((await aw(cadN.getComponentCount())) - 1));
      const tot = await aw(comp.getParamCount());
      const acoes = [];
      for (let j = 0; j < tot; j++) {
        try {
          const pO = await aw(comp.getParam(j)), pN = await aw(alvo.getParam(j));
          if (!pO || !pN) continue;
          const v = await pO.getValueAtTime(t0O);
          const atual = await pN.getValueAtTime(t0N);
          if (v === undefined || v === null || v === atual) continue;
          acoes.push(pN.createSetValueAction(pN.createKeyframe(v), true));
        } catch (_) { /* parametro que o UXP nao deixa ler/escrever: fica o padrao */ }
      }
      if (acoes.length) await project.lockedAccess(() => {
        project.executeTransaction((ca) => { acoes.forEach((a) => ca.addAction(a)); }, "DUBX: valores do efeito (" + nome + ")");
      });
      copiados.push(nome);
    } catch (e) { falhas.push(nome + " (" + msgErro(e) + ")"); }
  }
  if (copiados.length) escreve("efeitos do original no clipe novo: " + copiados.join(", ") + ".", "ok");
  if (falhas.length) escreve("efeitos que não consegui copiar: " + falhas.join("; "), "erro");
}

async function faixasParaEscolher(c) {
  const ppro = require("premierepro");
  const project = await ppro.Project.getActiveProject();
  const seq = project && await project.getActiveSequence();
  if (!seq) throw new Error("nenhuma sequência ativa");
  let ini = null, fim = null;
  try { if (c && c.item) { ini = (await c.item.getStartTime()).seconds; fim = (await c.item.getEndTime()).seconds; } } catch (_) {}
  const origem = await faixaDoClipe(seq, c && c.item);
  const total = await seq.getAudioTrackCount();
  const lista = [];
  for (let i = 0; i < total; i++) {
    let nome = "A" + (i + 1), livre = true;
    try {
      const tr = await seq.getAudioTrack(i);
      if (tr.name) nome = "A" + (i + 1) + " " + tr.name;
      if (ini !== null) {
        for (const it of await itensDaFaixa(tr)) {
          const a = (await it.getStartTime()).seconds, b = (await it.getEndTime()).seconds;
          if (a < fim - 0.001 && b > ini + 0.001) { livre = false; break; }
        }
      }
    } catch (_) { livre = false; }
    lista.push({ i, nome, livre, origem: i === origem });
  }
  const chave = (await idDaSequencia(seq)) + ":" + origem;
  const lembrada = DUBX_FAIXA[chave];
  let sugerida = null;
  if (lembrada !== undefined && lista[lembrada] && lista[lembrada].livre) sugerida = lista[lembrada];
  else sugerida = lista.find((f) => f.i > origem && f.livre) || null;   // so ABAIXO da origem; acima nunca
  return { lista, sugerida, origem, chave };
}

/* LISTA DE FAIXAS INLINE (P41, Kakashi 29/10 16:52 «Concluir cai na faixa do grupo, botão extra "pôr na faixa" escolhe»).
 * Antes era uma caixa flutuante (position:fixed) — o UXP nao desenha position:fixed e a caixa nunca apareceu. Agora devolve
 * um bloco que vai DENTRO do card da previa (desenhaPrevias), sem fundo escuro por cima de nada. Faixa ocupada no trecho e a
 * propria origem aparecem apagadas e NAO sao clicaveis: o pouso e overwrite e apagaria o audio dele naquele pedaco.
 * aoEscolher(faixa | null) — null = fechar sem por. */
function escolheFaixa(info, aoEscolher) {
  const caixa = document.createElement("div");
  caixa.className = "escolha inline";
  const tit = document.createElement("div");
  tit.className = "escolha-tit"; tit.textContent = "pôr em qual faixa?";
  caixa.appendChild(tit);
  const rol = document.createElement("div");
  rol.className = "escolha-rol";
  info.lista.forEach((f) => {
    const pode = f.livre && !f.origem;
    const it = document.createElement("div");
    it.className = "escolha-it" + (f.livre ? "" : " ocupada") + (info.sugerida && f.i === info.sugerida.i ? " sugerida" : "")
                 + (f.origem ? " origem" : "");
    it.textContent = f.nome + (f.origem ? "  (origem)" : "") + (f.livre || f.origem ? "" : "  · ocupada aqui");
    if (pode) {
      it.setAttribute("role", "button");
      it.addEventListener("click", () => aoEscolher({ i: f.i, nome: f.nome, chave: info.chave }));
    }
    rol.appendChild(it);
  });
  caixa.appendChild(rol);
  const nova = document.createElement("div");
  nova.className = "escolha-it nova"; nova.textContent = "+ faixa nova (A" + (info.lista.length + 1) + ")";
  nova.setAttribute("role", "button");
  nova.addEventListener("click", () => aoEscolher({ i: info.lista.length, nome: "nova A" + (info.lista.length + 1), criar: true,
                                                     chave: info.chave }));
  caixa.appendChild(nova);
  const sair = document.createElement("div");
  sair.className = "escolha-sair"; sair.textContent = "fechar"; sair.setAttribute("role", "button");
  sair.addEventListener("click", () => aoEscolher(null));
  caixa.appendChild(sair);
  return caixa;
}

/* abre/fecha a lista do «pôr na faixa» de UMA previa. O estado mora na previa (p._escolhendo, p._faixas) porque o
 * desenhaPrevias refaz o card inteiro a cada mudanca da fila (onda que chega, outra previa que sai) — a lista sobrevive. */
async function abreListaFaixas(p) {
  if (p._escolhendo) { p._escolhendo = false; p._faixas = null; desenhaPrevias(); return; }
  if (p._concluindo) { escreve("\"" + p.etiqueta + "\" já está indo pra timeline…"); return; }
  // (juiz P41) abrir a lista NAO para o que esta tocando: ele pode escolher a faixa ouvindo; o concluiPrevia para na hora de por.
  p._escolhendo = true; p._faixas = null; p._faixasErro = "";
  desenhaPrevias();
  try {
    if (!p.c && p.cRef) p.c = await reacheClipe(p.cRef);
    // sem o clipe original nao da pra saber o que esta livre NO TRECHO (toda faixa pareceria livre) — mesma regra do Concluir
    if (!p.c || !p.c.item) throw new Error("não achei o clipe original desta prévia na sequência aberta — sem ele não sei que faixa está livre. O Concluir põe numa faixa vazia, no cursor");
    p._faixas = await faixasParaEscolher(p.c);
  } catch (e) { p._faixasErro = msgErro(e); }
  if (p._escolhendo && PREVIAS.indexOf(p) >= 0) desenhaPrevias();
}

async function colocaNaTimeline(caminhos, c, etiqueta, silenciaOrigem, faixaFixa) {
  if (!caminhos.length) return 0;
  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error("nenhum projeto aberto");
  const seq = await project.getActiveSequence();
  if (!seq) throw new Error("nenhuma sequência ativa");
  const raiz = await project.getRootItem();

  const importou = await project.importFiles(caminhos, true, raiz);
  if (!importou) throw new Error("o Premiere não importou os arquivos");

  // importFiles devolve booleano, nao os itens — reachar pelo caminho, igual
  // o _findItemByPath do Beat Match.
  const porCaminho = new Map();
  for (const it of await raiz.getItems()) {
    const p = await caminhoDoItem(it);
    if (p) porCaminho.set(String(p).toLowerCase(), it);
  }

  // Alinhamento: os stems comecam no MESMO ponto do clipe original. Sem isto o
  // lipsync do video vai pro espaco — foi a dor toda de 19/08.
  let inicio;
  try {
    if (c && c.item) inicio = await c.item.getStartTime();
    else inicio = await seq.getPlayerPosition();   // Falar: nasce no cursor
  } catch (_) { inicio = ppro.TickTime.TIME_ZERO; }

  /* ⚠️ LICAO CARA (20/08, primeiro uso real dele): eu mandava pra faixa
   * `contagem + i`, apostando que indice maior que a contagem CRIARIA a faixa.
   * O log dele provou que nao: "Fala.wav -> faixa A19 (havia 18)" e NADA
   * pousou. O `createOverwriteItemAction` alem da contagem nao cria faixa,
   * nao coloca o clipe, e NAO DA ERRO — some calado.
   *
   * E nao da pra criar faixa: o UXP nao tem addTracks (varri o .d.ts; so
   * existe getAudioTrackCount/getAudioTrack). O Beat Match so conseguia via
   * QE DOM, que o UXP nao tem.
   *
   * Entao: sobrescrever so em faixa VAZIA (seguro por construcao — nao ha o que
   * apagar), e quando faltar faixa, CRIAR com `createInsertProjectItemAction`
   * usando indice alem da contagem, que e o unico caminho que a Adobe documenta
   * pra isso (nao existe addTracks no UXP; o Beat Match so cria porque e CEP e
   * usa `qe.addTracks`). Vai com limitShift=true e a faixa nova esta vazia,
   * entao nao ha o que deslocar nela. */
  const totalFaixas = await seq.getAudioTrackCount();
  const editor = await ppro.SequenceEditor.getEditor(seq);

  /* AUTOMATICO (padrao): enche as faixas VAZIAS que ja existem e CRIA so o que
   * faltar. Nasceu do uso real dele em 20/08 — a sequencia tinha 3 vazias e o
   * "separar por personagem" produz 4 (voz1 + voz2 + musica + efeitos). Fazer
   * ele contar faixa na mao antes de rodar e o tipo de atrito que mata a
   * ferramenta. As duas opcoes manuais continuam, pra quando ele quiser mandar. */
  // onde esta o clipe de origem — o resultado deve cair ABAIXO dele
  const faixaOrigem = await faixaDoClipe(seq, c && c.item);   // -1 sem clipe: qualquer vazia serve
  let alvos = [];
  if (faixaFixa) {
    // FAIXA ESCOLHIDA POR ELE no Concluir (29/10): vai nela, ponto. Livre no trecho ja foi conferido na lista.
    alvos = [{ i: faixaFixa.i, nome: faixaFixa.nome, criar: !!faixaFixa.criar || faixaFixa.i >= totalFaixas }];
  } else if (DESTINO !== "criar") {
    // "So vazias" e escolha explicita dele: ali qualquer vazia vale, ate acima.
    alvos = await faixasVazias(seq, caminhos.length, faixaOrigem,
                               DESTINO === "auto");
  }
  const usadasVazias = alvos.length;
  if (!faixaFixa && DESTINO !== "vazias" && alvos.length < caminhos.length) {
    let prox = totalFaixas;
    for (const a of alvos) if (a.i + 1 > prox) prox = a.i + 1;
    while (alvos.length < caminhos.length) {
      alvos.push({ i: prox, nome: "nova A" + (prox + 1), criar: true });
      prox++;
    }
  }
  escreve("faixas na sequência: " + totalFaixas + " · vazias usadas: "
          + usadasVazias + " · novas: " + (alvos.length - usadasVazias)
          + (faixaOrigem >= 0
             ? " · origem em A" + (faixaOrigem + 1)
             : " · NÃO achei a faixa do seu clipe (por isso pode subir)"));
  const acimaDaOrigem = alvos.filter((a) => faixaOrigem >= 0 && a.i < faixaOrigem);
  if (acimaDaOrigem.length) {
    escreve("não havia vazia abaixo de A" + (faixaOrigem + 1)
            + " — " + acimaDaOrigem.length + " faixa(s) foram pra cima. "
            + "Troque ONDE PÔR O RESULTADO para \"Automático\" pra criar embaixo.",
            "erro");
  }
  if (!alvos.length) {
    throw new Error("não há faixa de áudio vazia — crie " + caminhos.length
      + " na mão, ou troque ONDE PÔR O RESULTADO para \"Automático\"");
  }
  if (alvos.length < caminhos.length) {
    escreve("só " + alvos.length + " faixa(s) para " + caminhos.length
      + " stems — as que sobrarem ficam de fora. Troque para \"Automático\".", "erro");
  }

  const acoesPara = (alvo) => alvo.criar
    ? [{ nome: "inserir", faz: (pi, tt, a) => editor.createInsertProjectItemAction(pi, tt, -1, a, true) },
       { nome: "inserir (vídeo 0)", faz: (pi, tt, a) => editor.createInsertProjectItemAction(pi, tt, 0, a, true) }]
    : [{ nome: "vídeo -1", faz: (pi, tt, a) => editor.createOverwriteItemAction(pi, tt, -1, a) },
       { nome: "vídeo 0",  faz: (pi, tt, a) => editor.createOverwriteItemAction(pi, tt, 0, a) }];

  const livres = alvos;

  /* ⭐ TUDO NUMA TRANSACAO SO. Antes era uma por arquivo, entao 4 stems viravam
   * 4 Ctrl+Z — e a faixa criada ficava pra tras. Pedido dele em 23/08: "se eu
   * der o Ctrl+Z eu quero que a track que foi criada se desfaca tambem, porque
   * isso faz parte do que o plugin fez". A criacao da faixa acontece DENTRO da
   * acao de insert, entao entrando na mesma transacao ela volta junto. */
  let postos = 0;
  const postar = [];
  for (let i = 0; i < Math.min(caminhos.length, livres.length); i++) {
    // faltava a barra invertida: no Windows o caminho vem com \ e o
    // log imprimia o caminho inteiro em vez do nome. Auditoria 20/08.
    const arq = caminhos[i].split(/[\\/]/).pop();
    // .srt (e afins) nao e faixa de audio — avisa onde ficou e segue
    if (/\.(srt|txt|json)$/i.test(arq)) {
      escreve("  " + arq + " salvo em disco (importa em Legendas no Premiere): "
              + caminhos[i], "ok");
      postos++;
      continue;
    }
    const pi = porCaminho.get(String(caminhos[i]).toLowerCase());
    if (!pi) { escreve("não achei no projeto: " + arq, "erro"); continue; }
    postar.push({ pi, arq, alvo: livres[i] });
  }

  if (postar.length) {
    // as duas variantes (videoTrackIndex -1 e 0) viram duas TENTATIVAS da
    // transacao inteira, nao dois caminhos por arquivo
    /* SILENCIAR A ORIGEM (24/08, pedido do Melhorar). Sem isto o clipe
     * velho continua tocando junto com o novo e o A/B do Ctrl+Z não serve
     * pra nada — ele ouviria os dois somados. A ação entra na MESMA
     * transação: um Ctrl+Z devolve o áudio original E tira a faixa nova,
     * o Ctrl+Shift+Z refaz. Se a versão do Premiere recusar a ação, as
     * duas últimas tentativas rodam sem ela — melhor sobrepor do que
     * não entregar nada. */
    const podeCalar = !!(silenciaOrigem && c && c.item && c.item.createSetDisabledAction);
    const planos = podeCalar ? [[0, true], [1, true], [0, false], [1, false]]
                             : [[0, false], [1, false]];
    let entrou = false, ultimo = "", calou = false;
    for (let k = 0; k < planos.length && !entrou; k++) {
      const v = planos[k][0], comMudo = planos[k][1];
      try {
        await project.lockedAccess(() => {
          project.executeTransaction((ca) => {
            for (const p of postar) {
              ca.addAction(acoesPara(p.alvo)[v].faz(p.pi, inicio, p.alvo.i));
            }
            // criada AQUI dentro: ação de transação que falhou não se reusa
            if (comMudo) ca.addAction(c.item.createSetDisabledAction(true));
          }, "DUBX: " + etiqueta);
        });
        entrou = true; calou = comMudo;
      } catch (e) { ultimo = msgErro(e); }
    }
    if (entrou) {
      for (const p of postar) {
        escreve("  " + p.arq + " -> " + p.alvo.nome + " (faixa " + (p.alvo.i + 1) + ")", "ok");
      }
      postos += postar.length;
      if (calou)
        escreve("  o clipe original ficou MUDO pra comparação — Ctrl+Z volta ao som de antes, Ctrl+Shift+Z refaz.", "ok");
      else if (podeCalar)
        escreve("  não consegui emudecer o clipe original: ele vai tocar junto. Desligue a faixa dele na mão pra comparar.", "erro");
      escreve("  (um Ctrl+Z desfaz tudo isso de uma vez)", "ok");
    } else {
      escreve("  nada entrou na timeline: " + ultimo, "erro");
    }
  }
  return postos;
}

/* ----------------------------------------------------------- separar */
async function separar() {
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  const fila = CLIPES.filter((c) => c.marcado);
  if (!fila.length) { avisaFalta("selecione clipes na timeline e clique em Analisar seleção."); return; }
  const motor = elMotorDiar.value;
  const roteiro = texto(elRotSeparar);
  if (motor === "roteiro" && !roteiro) {
    avisaFalta("cole o roteiro — é ele que diz quem fala o quê."); return;
  }
  RODANDO = true; atualizaBotao(); barraMostra();
  escreve("separando " + fila.length + " arquivo(s) — motor: " + motor + ".");
  let feitos = 0;
  for (let i = 0; i < fila.length; i++) {
    LOTE = { idx: i, total: fila.length };
    const c = fila[i];
    try {
      estadoDo(c, "enviando…");
      // mesmo motivo do dublar: separar o TRECHO, nao a fonte inteira
      const trecho = await trechoDoClipe(c);
      const r = await fetch(DUBX + "/api/processar", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({
          modo: "separar", arquivo: c.caminho,
          motor_diar: motor, limpeza: "combinado",
          vozes: Number(elQtdVozes.value) || 0, roteiro,
        }, trecho || {})),
      });
      const { tarefa, erro } = await r.json();
      if (!tarefa) throw new Error(erro || "o DUBX não aceitou a tarefa");

      for (;;) {
        await espera(1000);
        const e = await (await fetch(DUBX + "/api/estado?t=" + tarefa)).json();
        if (e.estado === "pronto") break;
        if (e.estado === "cancelado") throw new Error("cancelado");
        if (e.estado === "erro") throw new Error(e.mensagem || "falhou no DUBX");
        estadoDo(c, e.mensagem || "separando…");
        barraTarefa(e, c.nome);
      }

      // as faixas (voz 1..N + Musica + Efeitos) com o caminho EM DISCO
      const { faixas, erro: eF } = await (await fetch(DUBX + "/api/faixas?id=" + tarefa)).json();
      void 0;
      if (eF) throw new Error(eF);
      const stems = (faixas || [])
        .filter((f) => f.papel !== "antes" && f.caminho)
        .map((f) => f.caminho);
      if (!stems.length) throw new Error("a separação não devolveu faixa nenhuma");
      escreve(c.nome + ": " + stems.length + " faixa(s) — "
              + (faixas || []).filter((f) => f.papel !== "antes").map((f) => f.nome).join(", "), "ok");

      if (elCriarFaixas.checked) {
        if (previaLigada()) {
          // cada stem vira uma previa propria: ele ouve um a um e conclui os
          // que quiser. O Concluir individual usa o mesmo colocaNaTimeline,
          // entao cada um cai na proxima vazia abaixo da origem.
          for (const s of stems) {
            const nomeStem = String(s).split(/[\\/]/).pop();
            poeNaFila({ arquivo: s, c, etiqueta: nomeStem + " — " + c.nome });
          }
          escreve(stems.length + " stem(s) na fila de prévias — ouça e conclua.", "ok");
        } else {
          estadoDo(c, "pondo na timeline…");
          const postos = await colocaNaTimeline(stems, c, "separar " + c.nome);
          escreve(postos + "/" + stems.length + " faixa(s) na timeline.",
                  postos === stems.length ? "ok" : "erro");
        }
      } else {
        escreve("stems prontos em disco (não pedi pra criar faixa).", "ok");
      }
      estadoDo(c, "pronto", "ok");
      feitos++;
    } catch (e) {
      estadoDo(c, "erro", "erro");
      escreve(c.nome + ": " + msgErro(e), "erro");
    }
  }
  LOTE = null; barraSet(1, "concluído");
  escreve("fim: " + feitos + "/" + fila.length + ".", feitos === fila.length ? "ok" : "erro");
  plim(feitos === fila.length ? "ok" : "erro");
  RODANDO = false; atualizaBotao();
  BARRA_TIMER = setTimeout(barraEsconde, 2500);
}

/* ------------------------------------------------------------ dublar */
/* O TRECHO do clipe, no formato que o servidor espera.
 *
 * `getInPoint`/`getOutPoint` do trackItem dizem QUAL PEDACO do arquivo de
 * origem aquele clipe usa — nao onde ele esta na sequencia. E exatamente o que
 * o `_cortar_trecho` do servidor quer.
 *
 * Ele reclamou disso duas vezes: no treino (17/08) e no dublar (23/08) —
 * "eu fiz a curadoria", "ele dublou a fonte inteira e nao o clipe".
 *
 * ⚠️ So vale pros modos que produzem arquivo NOVO (dublar, separar). Nos
 * `*_no_lugar` o corte REGRAVARIA o original com so o trecho e comeria o
 * resto do arquivo. */
async function trechoDoClipe(c) {
  try {
    if (!c.item) return null;
    const ini = (await c.item.getInPoint()).seconds;
    const fim = (await c.item.getOutPoint()).seconds;
    if (!(fim > ini)) return null;
    /* VELOCIDADE (Kakashi 29/10 15:31 [12:00] «Aqui cortou a porra da fala»): o in/out vem no relogio da TIMELINE; num clipe a 119,6% o
     * arquivo de origem anda mais rapido — cortar 1,916 s da fonte perdia os ultimos 0,38 s. O trecho da FONTE = in/out x velocidade. */
    let vel = 1;
    try { const s = await c.item.getSpeed(); if (s && isFinite(s) && s > 0) vel = Math.abs(s); } catch (_) {}
    /* GANHO DE CLIPE (Kakashi 29/10 21:24 [59:20] «você me prometeu que ia colocar do dubx aqui com o volume acertado»): o
     * original pode ter ganho de clipe (tecla G), que a API NAO entrega a plugin. Manda onde o clipe esta; o servidor le o ganho
     * no .prproj SALVO e embute no arquivo novo. */
    let origem_clip = null;
    try {
      const project = await ppro.Project.getActiveProject();
      const seq = await project.getActiveSequence();
      const fx = await faixaDoClipe(seq, c.item);
      origem_clip = { projeto: project.path || "", sequencia: seq.name || "", faixa: fx >= 0 ? fx + 1 : 0,
                      inicio_s: (await c.item.getStartTime()).seconds, nome: (c.caminho || "").split(/[\\/]/).pop() };
    } catch (_) {}
    return { inicio: ini * vel, fim: fim * vel, velocidade: vel, origem_clip };
  } catch (_) { return null; }
}

async function dublar() {
  if (desviaPraPrevia("dublar")) return;
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  const fila = CLIPES.filter((c) => c.marcado);
  if (!fila.length) { avisaFalta("selecione clipes na timeline e clique em Analisar seleção."); return; }
  const origem = elDubOrigem.value, alvo = elDubAlvo.value;
  // os tres que governam a interpretacao. Sem eles o painel usava os
  // defaults do servidor (Sidon 100), que soava robotico. Queixa de 23/08.
  // ⚠️ os sliders sao 0-100 INTEIROS porque o UXP descarta passo quebrado
  // (min=0 max=1 step=0.05 travava o controle em 0 — visto em 23/08).
  // O servidor espera 0-1 nestes dois, entao a conversao mora aqui.
  const atuacao = Number(elDubAtuacao.value) / 100;
  const adesao = Number(elDubAdesao.value) / 100;
  const sidon = Number(elDubSidon.value);
  if (origem === alvo) { avisaFalta("os dois idiomas estão iguais."); return; }
  RODANDO = true; atualizaBotao(); barraMostra();
  abreLote();   // uma faixa por faixa de origem, nao uma por clipe
  escreve("dublando " + fila.length + " arquivo(s): " + origem + " → " + alvo
          + ". o original NÃO é tocado.");
  let feitos = 0;
  for (let i = 0; i < fila.length; i++) {
    LOTE = { idx: i, total: fila.length };
    const c = fila[i];
    try {
      estadoDo(c, "enviando…");
      // so o pedaco que ELE marcou na timeline, nao o arquivo inteiro
      const trecho = await trechoDoClipe(c);
      const r = await fetch(DUBX + "/api/processar", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign(
          { modo: "dublar", arquivo: c.caminho, origem, alvo,
            atuacao, adesao, sidon }, trecho || {})),
      });
      const { tarefa, erro } = await r.json();
      if (!tarefa) throw new Error(erro || "o DUBX não aceitou a tarefa");

      let pronto = null;
      for (;;) {
        await espera(1000);
        const e = await (await fetch(DUBX + "/api/estado?t=" + tarefa)).json();
        if (e.estado === "pronto") { pronto = e; break; }
        if (e.estado === "cancelado") throw new Error("cancelado");
        if (e.estado === "erro") throw new Error(e.mensagem || "falhou no DUBX");
        estadoDo(c, e.mensagem || "dublando…");
        barraTarefa(e, c.nome);
      }
      // previa e o WAV quando a saida virou video — na timeline queremos o audio
      const arquivo = pronto.previa || pronto.saida;
      if (!arquivo) throw new Error("a dublagem não devolveu arquivo");

      if (elDubFaixa.checked) {
        estadoDo(c, "entregando…");
        const msg = await entregaResultado(arquivo, c, "dublagem — " + c.nome);
        escreve("dublagem: " + msg, "ok");
      } else {
        escreve("dublagem pronta: " + arquivo, "ok");
      }
      estadoDo(c, "pronto", "ok");
      feitos++;
    } catch (e) {
      estadoDo(c, "erro", "erro");
      escreve(c.nome + ": " + msgErro(e), "erro");
    }
  }
  LOTE = null; barraSet(1, "concluído");
  try { await fechaLote("dublado", false); }
  catch (e) { escreve("pôr na timeline: " + msgErro(e), "erro"); }
  escreve("fim: " + feitos + "/" + fila.length + ".", feitos === fila.length ? "ok" : "erro");
  plim(feitos === fila.length ? "ok" : "erro");
  RODANDO = false; atualizaBotao();
  BARRA_TIMER = setTimeout(barraEsconde, 2500);
}

/* Motor da separacao manda no que aparece: roteiro so pro "roteiro", token
 * so pra pyannote. O token fica VISIVEL na aba (nao escondido atras de um
 * dropdown) — em 20/08 ele nao achou o campo no app por estar enterrado. */
function ajustaSeparar() {
  const m = elMotorDiar.value;
  document.getElementById("zonaRoteiro").className = m === "roteiro" ? "" : "oculto";
  // zonaToken agora mora no popover da engrenagem, sempre visivel (24/08)
  document.getElementById("ctrlQtdVozes").className =
    (m === "roteiro" || m === "nenhum" || m === "mossformer2" || m === "tiger" || m === "sam" || m === "sam_risada") ? "ctrl oculto" : "ctrl";
  if (m === "pyannote") estadoToken();
}

async function estadoToken() {
  try {
    const j = await (await fetch(DUBX + "/api/hf-token")).json();
    document.getElementById("tokEstado").textContent = j.tem
      ? "token configurado." : "sem token — a pyannote não roda sem ele.";
  } catch (_) { /* DUBX fora do ar */ }
}

async function salvaToken() {
  const el = document.getElementById("tokValor");
  const v = texto(el);
  try {
    const r = await fetch(DUBX + "/api/hf-token", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: v }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.erro || "não deu pra salvar");
    el.value = "";                       // nao deixa o segredo na tela
    await estadoToken();
    escreve(j.tem ? "token guardado." : "token apagado.", "ok");
  } catch (e) { escreve(msgErro(e), "erro"); }
}

/* ===================================================================
 * FLUXO — a esteira, como PILHA VERTICAL de etapas.
 *
 * Formato pedido por ele em 20/08 (com print): nao e canvas de arrastar
 * node, e uma lista empilhada com seta pra baixo. Cada etapa COLAPSA e tem
 * os ajustes dela por dentro. Isso e melhor que o canvas pro caso dele — e
 * de quebra cabe em 380px, o que canvas nenhum caberia.
 *
 * A ramificacao vira PARAMETRO, nao desenho: o passo guarda `de: "voz2"`,
 * que e como se diz "troca a voz DESSE aqui" sem ramo na tela.
 *
 * DOIS JEITOS DE RODAR:
 *   · Automatico — manda o fluxo inteiro e avisa no fim.
 *   · Validando  — roda ate a etapa N, poe o resultado na timeline, e PARA
 *                  esperando voce ouvir. Continuar e mandar de novo com
 *                  ate=N+1: as etapas anteriores voltam do cache do servidor
 *                  em milissegundos. Por isso "refazer com outro ajuste" so
 *                  refaz dali pra frente, e nao o fluxo todo.
 * =================================================================== */
let FLUXOS = [];
let CATALOGO = {};
let PASSOS = [];          // a pilha do fluxo escolhido, ja editavel
let JEITO = "auto";       // "auto" | "validar"
let ABERTO = -1;          // qual etapa esta expandida (uma por vez)
let APROVACAO = null;     // resolve() do botao "continuar", no modo validando
let VALIDANDO = -1;       // qual etapa espera aprovacao (-1 = nenhuma)

const elFluxo = document.getElementById("fluxo");
const btRodarFluxo = document.getElementById("rodarFluxo");
const elPilha = document.getElementById("pilha");

async function listaFluxos() {
  try {
    const d = await (await fetch(DUBX + "/api/fluxos")).json();
    FLUXOS = d.fluxos || [];
    CATALOGO = d.catalogo || {};
    elFluxo.textContent = "";
    const zero = document.createElement("option");
    zero.value = ""; zero.textContent = "— escolha um fluxo —";
    elFluxo.appendChild(zero);
    for (const f of FLUXOS) {
      const o = document.createElement("option");
      o.value = f.chave; o.textContent = f.nome;
      elFluxo.appendChild(o);
    }
  } catch (_) { /* DUBX fora do ar */ }
}

/* Ao escolher o fluxo, os curingas ("?voz") viram CAMPO VAZIO e obrigatorio.
 * No formato antigo o curinga era respondido num campo solto la embaixo;
 * agora cada etapa tem o proprio ajuste, entao ele responde no lugar certo. */
function escolheFluxo() {
  const f = FLUXOS.find((x) => x.chave === elFluxo.value) || null;
  PASSOS = [];
  if (f) {
    for (const p of (f.passos_lista || [])) {
      const copia = JSON.parse(JSON.stringify(p));
      copia.params = copia.params || {};
      copia._exige = [];
      for (const k of Object.keys(copia.params)) {
        if (typeof copia.params[k] === "string" && copia.params[k].startsWith("?")) {
          copia.params[k] = "";
          copia._exige.push(k);
        }
      }
      copia._estado = "";
      PASSOS.push(copia);
    }
  }
  document.getElementById("dicaFluxo").textContent = f
    ? f.descricao : "faz a esteira inteira numa tacada só, sem você ficar subindo e baixando arquivo.";
  ABERTO = -1;
  desenhaPilha();
  atualizaBotao();
}

function seta() {
  const d = document.createElement("div");
  d.className = "seta"; d.textContent = "↓";
  return d;
}

function desenhaPilha() {
  elPilha.textContent = "";
  if (!PASSOS.length) {
    const v = document.createElement("div");
    v.className = "vazio";
    v.textContent = "escolha um fluxo pra ver as etapas.";
    elPilha.appendChild(v);
    return;
  }
  PASSOS.forEach((p, i) => {
    if (i) elPilha.appendChild(seta());
    elPilha.appendChild(cartaoPasso(p, i));
  });
  poeCaixaValidar();   // sem isto o modo Validando trava pra sempre
  vestirTudo();        // a pilha nasce depois do boot: veste agora
}

function cartaoPasso(passo, i) {
  const cat = CATALOGO[passo.tipo] || {};
  const cx = document.createElement("div");
  cx.className = "passo" + (passo._estado ? " " + passo._estado : "");

  const cab = document.createElement("div");
  cab.className = "passo-cab";
  const num = document.createElement("div");
  num.className = "passo-num";
  num.textContent = passo._estado === "pronto" ? "✓" : String(i + 1);
  const nome = document.createElement("div");
  nome.className = "passo-nome";
  nome.textContent = passo.titulo || cat.nome || passo.tipo;
  const est = document.createElement("div");
  est.className = "passo-estado";
  est.textContent = passo._msg || "";
  const chev = document.createElement("div");
  chev.className = "passo-chev";
  chev.textContent = ABERTO === i ? "▾" : "▸";
  cab.appendChild(num); cab.appendChild(nome); cab.appendChild(est); cab.appendChild(chev);
  cab.addEventListener("click", () => { ABERTO = ABERTO === i ? -1 : i; desenhaPilha(); });
  cx.appendChild(cab);

  if (ABERTO === i) {
    const corpo = document.createElement("div");
    corpo.className = "passo-corpo";
    if (cat.resumo) {
      const r = document.createElement("div");
      r.className = "passo-resumo"; r.textContent = cat.resumo;
      corpo.appendChild(r);
    }
    if (cat.aviso) {
      const a = document.createElement("div");
      a.className = "passo-aviso"; a.textContent = "⚠ " + cat.aviso;
      corpo.appendChild(a);
    }
    for (const c of (cat.controles || [])) {
      if (!valeAqui(c, passo)) continue;
      corpo.appendChild(controle(c, passo));
    }
    if (!(cat.controles || []).length) {
      const r = document.createElement("div");
      r.className = "passo-resumo"; r.textContent = "esta etapa não tem ajuste.";
      corpo.appendChild(r);
    }
    cx.appendChild(corpo);
  }
  return cx;
}

/* Desenha UM ajuste da etapa. O catalogo vem do servidor (rota /api/fluxos),
 * entao o app e o painel desenham a mesma coisa sem duas listas pra manter. */
function controle(c, passo) {
  const val = passo.params[c.id] !== undefined ? passo.params[c.id] : c.val;

  if (c.tipo === "texto") {
    const cx = document.createElement("div");
    cx.style.marginTop = "8px";
    const rot = document.createElement("div");
    rot.className = "passo-resumo"; rot.textContent = c.nome
      + (passo._exige.indexOf(c.id) >= 0 ? " (obrigatório)" : "");
    const ta = document.createElement("textarea");
    ta.value = val || "";
    ta.placeholder = c.dica || "";
    ta.addEventListener("change", () => { passo.params[c.id] = ta.value; });
    // balao ao repousar o mouse. Pedido dele em 23/08: "as vezes eu nao vou
    // lembrar de tudo, entao e sempre bom ali ter uma explicacao".
    if (c.dica) { cx.title = c.dica; rot.title = c.dica; ta.title = c.dica; }
    cx.appendChild(rot); cx.appendChild(ta);
    return cx;
  }

  const linha = document.createElement("div");
  linha.className = "ctrl";
  const n = document.createElement("span");
  n.className = "n";
  n.textContent = c.nome
    + ((passo._exige || []).indexOf(c.id) >= 0 ? " (obrigatório)" : "");
  linha.appendChild(n);

  let campo;
  if (c.tipo === "numero") {
    campo = document.createElement("input");
    campo.type = "number"; campo.className = "num"; campo.value = val;
    if (c.min !== undefined) campo.min = c.min;
    if (c.max !== undefined) campo.max = c.max;
    if (c.passo !== undefined) campo.step = c.passo;
    const espaco = document.createElement("span");
    espaco.style.flex = "1"; linha.appendChild(espaco);
  } else {
    campo = document.createElement("select");
    campo.className = "campo";
    let ops = c.op || [];
    // campo obrigatorio ainda vazio: sem opcao vazia o select MENTE,
    // mostrando a primeira opcao como escolha dele. Auditoria 20/08.
    if ((passo._exige || []).indexOf(c.id) >= 0 && String(val) === "") {
      ops = [["", "— escolha —"]].concat(ops);
    }
    // "voz" e "ambiente" saem das listas vivas do proprio painel
    if (c.tipo === "voz") {
      ops = [["", "— escolha a voz —"]];
      for (const o of elVozes.options) if (o.value) ops.push([o.value, o.textContent]);
    } else if (c.tipo === "ambiente") {
      ops = [["", "— escolha o ambiente —"]];
      for (const o of elAmbientes.options) if (o.value) ops.push([o.value, o.textContent]);
    }
    for (const [v, rot] of ops) {
      const o = document.createElement("option");
      o.value = v; o.textContent = rot;
      campo.appendChild(o);
    }
    campo.value = String(val);
  }
  campo.addEventListener("change", () => {
    passo.params[c.id] = campo.value;
    desenhaPilha();   // mudar o motor faz o roteiro aparecer/sumir na hora
  });
  linha.appendChild(campo);

  const cx = document.createElement("div");
  cx.appendChild(linha);
  if (c.dica && c.tipo !== "texto") {
    // o texto embaixo CONTINUA (o UXP nem sempre honra `title`); o balao
    // e reforco, nao substituto. Pedido dele em 23/08.
    const d = document.createElement("div");
    d.className = "dica"; d.textContent = c.dica;
    cx.appendChild(d);
    cx.title = c.dica; linha.title = c.dica;
    n.title = c.dica; campo.title = c.dica;
  }
  return cx;
}

/* `so_se` / `so_se_nao` no catalogo: um ajuste que nao se aplica NAO aparece
 * e NAO e exigido. Nasceu de 20/08 — o painel pedia roteiro no fluxo de stems,
 * onde ele nem quer dividir por pessoa. Exigencia que nao se aplica e defeito. */
function valeAqui(c, passo) {
  const bate = (regra) => Object.keys(regra).every((k) => {
    const atual = passo.params[k] !== undefined ? passo.params[k] : valorPadrao(passo, k);
    return String(atual) === String(regra[k]);
  });
  if (c.so_se && !bate(c.so_se)) return false;
  if (c.so_se_nao && bate(c.so_se_nao)) return false;
  return true;
}
function valorPadrao(passo, id) {
  const cat = CATALOGO[passo.tipo] || {};
  const c = (cat.controles || []).find((x) => x.id === id);
  return c ? c.val : "";
}

function marcaPasso(i, estado, msg) {
  if (!PASSOS[i]) return;
  PASSOS[i]._estado = estado || "";
  PASSOS[i]._msg = msg || "";
  // NO LUGAR, sem redesenhar tudo: o poll de 1 s do modo Validando apagava
  // a digitacao dele e roubava o foco a cada segundo. Auditoria 20/08.
  const cart = elPilha.querySelectorAll(".passo")[i];
  if (!cart) { desenhaPilha(); return; }
  cart.className = "passo" + (estado ? " " + estado : "");
  const num = cart.querySelector(".passo-num");
  if (num) {
    num.textContent = estado === "pronto" ? "✓" : String(i + 1);
    // a classe vai NO FILHO: `.passo.pronto .passo-num` era descendente e
    // nunca pintou a bolinha. Achado da varredura de 23/08.
    num.className = "passo-num" + (estado ? " " + estado : "");
  }
  const est = cart.querySelector(".passo-estado");
  if (est) {
    est.textContent = msg || "";
    est.className = "passo-estado" + (estado ? " " + estado : "");
  }
}

/* No modo validando, para aqui ate ele decidir. Devolve "seguir" ou "refazer".
 * O botao vive DENTRO do cartao da etapa que acabou — e ali que ele esta
 * olhando quando o resultado chega. */
/* A caixa fica FORA de esperaAprovacao porque desenhaPilha() limpa a pilha
 * inteira. Sem alguem que REPONHA os botoes, abrir uma etapa pra mudar um
 * ajuste — o gesto que antecede "Refazer" — matava o unico caminho de
 * resolve() e o fluxo travava pra sempre. Auditoria 20/08. */
function poeCaixaValidar() {
  if (VALIDANDO < 0 || !APROVACAO) return;
  const alvo = elPilha.querySelectorAll(".passo")[VALIDANDO];
  if (!alvo || alvo.querySelector(".validar")) return;
  const decide = (r) => {
    const responde = APROVACAO;
    APROVACAO = null; VALIDANDO = -1;
    if (responde) responde(r);
  };
  const box = document.createElement("div");
  box.className = "validar";
  // div role=button: o pintor do UXP ignora background em <button>
  const seguir = document.createElement("div");
  seguir.setAttribute("role", "button"); seguir.tabIndex = 0;
  seguir.className = "acao vbt"; seguir.style.marginTop = "0";
  seguir.textContent = VALIDANDO + 1 >= PASSOS.length ? "Terminar" : "Aprovar e continuar";
  seguir.addEventListener("click", () => decide("seguir"));
  const refazer = document.createElement("div");
  refazer.setAttribute("role", "button"); refazer.tabIndex = 0;
  refazer.className = "ghost vbt";
  refazer.textContent = "Refazer";
  refazer.addEventListener("click", () => decide("refazer"));
  box.appendChild(seguir); box.appendChild(refazer);
  alvo.appendChild(box);
}

function esperaAprovacao(i) {
  return new Promise((resolve) => {
    APROVACAO = resolve;
    VALIDANDO = i;
    desenhaPilha();           // ela mesma repoe a caixa, sempre
    if (!elPilha.querySelectorAll(".passo")[i]) {
      APROVACAO = null; VALIDANDO = -1; resolve("seguir");
    }
  });
}

function passosLimpos() {
  // tira os campos internos (_estado, _msg, _exige) antes de mandar
  return PASSOS.map((p) => ({ tipo: p.tipo, de: p.de, titulo: p.titulo, params: p.params }));
}

function faltaPreencher() {
  for (let i = 0; i < PASSOS.length; i++) {
    for (const k of (PASSOS[i]._exige || [])) {
      const cat = CATALOGO[PASSOS[i].tipo] || {};
      const ctrl = (cat.controles || []).find((x) => x.id === k);
      if (ctrl && !valeAqui(ctrl, PASSOS[i])) continue;   // nao se aplica
      if (!String(PASSOS[i].params[k] || "").trim()) {
        return "etapa " + (i + 1) + " (" + (PASSOS[i].titulo || PASSOS[i].tipo)
             + ") precisa de: " + k;
      }
    }
  }
  return null;
}

/* Manda o fluxo ate a etapa `ate` (ou inteiro, se ate for null) e espera. */
async function rodaFluxoAte(c, ate) {
  const r = await fetch(DUBX + "/api/processar", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ modo: "fluxo", arquivo: c.caminho,
                           passos: passosLimpos(), ate: ate || undefined }),
  });
  const { tarefa, erro } = await r.json();
  if (!tarefa) throw new Error(erro || "o DUBX não aceitou o fluxo");
  for (;;) {
    await espera(1000);
    const e = await (await fetch(DUBX + "/api/estado?t=" + tarefa)).json();
    if (e.estado === "pronto") return { tarefa, estado: e };
    if (e.estado === "cancelado") throw new Error("cancelado");
    if (e.estado === "erro") throw new Error(e.mensagem || "falhou no DUBX");
    const onde = (e.etapa && e.de) ? e.etapa + "/" + e.de + " · " : "";
    estadoDo(c, onde + (e.mensagem || "rodando…"));
    if (ate) marcaPasso(ate - 1, "rodando", e.mensagem || "rodando…");
    barraTarefa(e, c.nome);
  }
}

async function saidasNaTimeline(tarefa, c, etiqueta, estado) {
  if (!document.getElementById("fluxoFaixas").checked) return 0;
  // O estado da tarefa ja traz os caminhos. NAO usar /api/faixas aqui: ela le
  // o HISTORICO, e no fluxo os stems extras ficam registrados sob o id da
  // sub-tarefa ("<t>n2"), nao sob o id do fluxo — foi o que fez o "so stems"
  // pôr UMA faixa em vez de tres no primeiro teste real (20/08).
  let saidas = (estado && estado.faixas) ? estado.faixas.slice() : [];
  if (!saidas.length) {
    const { faixas } = await (await fetch(DUBX + "/api/faixas?id=" + tarefa)).json();
    saidas = (faixas || []).filter((x) => x.papel !== "antes" && x.caminho)
                           .map((x) => x.caminho);
  }
  if (!saidas.length) return 0;
  const postos = await colocaNaTimeline(saidas, c, etiqueta);
  escreve(postos + "/" + saidas.length + " faixa(s) na timeline.",
          postos === saidas.length ? "ok" : "erro");
  return postos;
}

async function rodarFluxo() {
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  if (!PASSOS.length) { avisaFalta("escolha um fluxo primeiro."); return; }
  const falta = faltaPreencher();
  if (falta) { escreve(falta, "erro"); return; }
  const fila = CLIPES.filter((c) => c.marcado);
  if (!fila.length) { avisaFalta("selecione clipes na timeline e clique em Analisar seleção."); return; }
  if (JEITO === "validar" && fila.length > 1) {
    avisaFalta("validando roda um arquivo por vez — marque só um clipe."); return;
  }

  RODANDO = true; atualizaBotao(); barraMostra();
  PASSOS.forEach((p, i) => marcaPasso(i, "", ""));
  escreve((JEITO === "validar" ? "validando" : "rodando") + " o fluxo ("
          + PASSOS.length + " etapas) em " + fila.length
          + " arquivo(s). o original NÃO é tocado.");
  let feitos = 0;

  for (let k = 0; k < fila.length; k++) {
    LOTE = { idx: k, total: fila.length };
    const c = fila[k];
    try {
      if (JEITO === "auto") {
        const r = await rodaFluxoAte(c, null);
        PASSOS.forEach((p, i) => marcaPasso(i, "pronto", ""));
        await saidasNaTimeline(r.tarefa, c, "fluxo", r.estado);
      } else {
        // UMA ETAPA POR VEZ: roda, poe na timeline pra ele OUVIR no Premiere,
        // e para. O Premiere e o player — o UXP nao toca audio confiavel.
        let i = 0;
        while (i < PASSOS.length) {
          marcaPasso(i, "rodando", "rodando…");
          const r = await rodaFluxoAte(c, i + 1);
          marcaPasso(i, "pronto", "pronto");
          await saidasNaTimeline(r.tarefa, c, "etapa " + (i + 1), r.estado);
          escreve("etapa " + (i + 1) + " pronta — ouça no Premiere e decida.", "ok");
          const acao = await esperaAprovacao(i);
          if (acao === "refazer") {
            // params mudados = chave de cache diferente = so esta etapa refaz
            marcaPasso(i, "", "");
            escreve("refazendo a etapa " + (i + 1) + " (as anteriores vêm do cache).");
            continue;
          }
          i++;
        }
      }
      estadoDo(c, "pronto", "ok");
      feitos++;
    } catch (e) {
      estadoDo(c, "erro", "erro");
      escreve(c.nome + ": " + msgErro(e), "erro");
    }
  }

  LOTE = null; barraSet(1, "concluído");
  escreve("fim: " + feitos + "/" + fila.length + ".", feitos === fila.length ? "ok" : "erro");
  plim(feitos === fila.length ? "ok" : "erro");
  RODANDO = false; atualizaBotao();
  BARRA_TIMER = setTimeout(barraEsconde, 2500);
}

document.querySelectorAll(".sbt-jeito").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".sbt-jeito").forEach((x) => x.classList.remove("ativa"));
    b.classList.add("ativa");
    JEITO = b.dataset.jeito;
    document.getElementById("dicaJeito").textContent = JEITO === "validar"
      ? "Validando: roda uma etapa, põe na timeline pra você ouvir, e espera seu OK. Refazer só refaz dali pra frente."
      : "Automático: roda tudo de ponta a ponta e te avisa no fim.";
  });
});

elFluxo.addEventListener("change", escolheFluxo);
aoClicar(btRodarFluxo, rodarFluxo, "rodar fluxo");
listaFluxos().then(escolheFluxo);


aoClicar(btSeparar, separar, "separar");
/* Rotulo vivo dos sliders da dublagem. Numero solto no meio da tela nao
 * ajuda ninguem — ele precisa ver o valor enquanto arrasta. */
for (const [el, saida, fmt] of [
  [elDubAtuacao, "dDubAtuacao", (v) => v + "%"],
  [elDubAdesao, "dDubAdesao", (v) => v + "%"],
  [elDubSidon, "dDubSidon", (v) => v + "%"],
  [elDose, "dDose", (v) => v + "%"],
  [elLufs, "dLufs", (v) => String(v)],
  [elVcSidon, "dVcSidon", (v) => v + "%"],
  [elVcPresenca, "dVcPresenca", (v) => (Number(v) / 100).toFixed(2).replace(".", ",")],
  [elVcAtuacao, "dVcAtuacao", (v) => (Number(v) / 100).toFixed(2).replace(".", ",")],
  [elVcSemelhanca, "dVcSemelhanca", (v) => (Number(v) / 100).toFixed(2).replace(".", ",")],
  [elVcTom, "dVcTom", (v) => (Number(v) > 0 ? "+" : "") + v],
  [elElEstab, "dElEstab", (v) => (Number(v) / 100).toFixed(2).replace(".", ",")],
  [elElSemel, "dElSemel", (v) => (Number(v) / 100).toFixed(2).replace(".", ",")],
  [elElEstilo, "dElEstilo", (v) => (Number(v) / 100).toFixed(2).replace(".", ",")],
  [elFalarAtuacao, "dFalarAtuacao", (v) => (Number(v) / 100).toFixed(2).replace(".", ",")],
  [elFalarAdesao, "dFalarAdesao", (v) => (Number(v) / 100).toFixed(2).replace(".", ",")],
  [document.getElementById("espTeto"), "dEspTeto", (v) => v + " GB"],
]) {
  if (!el) continue;
  el.addEventListener("input", () => {
    const d = document.getElementById(saida);
    if (d) d.textContent = fmt(el.value);
  });
}
aoClicar(btDublar, dublar, "dublar");
seguro(function () { elMotorDiar.addEventListener("change", ajustaSeparar); }, "motor separar");
aoClicar(document.getElementById("tokSalvar"), salvaToken, "salvar token");
seguro(ajustaSeparar, "ajusta separar");

aoClicar(btAmbientar, ambientar, "ambientar");
aoClicar(document.getElementById("ambienteAuto"),
         ambienteAutomatico, "descobrir pela imagem");
aoClicar(document.getElementById("capturar"), capturarAmbiente, "salvar ambiente");
elAmbientes.addEventListener("change", () => { mostraDicaAmbiente(); atualizaBotao(); });
aoClicar(btTrocar, trocarVoz, "trocar voz");

/* Presenca/Atuacao/Semelhanca/Tom/Entonacao SO valem pro RVC — o Seed-VC (o
 * padrao, vencedor do teste cego dele) ignora todos. Escondidos quando nao se
 * aplicam: ajuste que nao se aplica e ruido, nao opcao. Pedido de 23/08
 * ("tom e entonacao, nao sei o que e"). */
function _soSeed(mostra) {
  const els = document.getElementsByClassName("soSeed");
  for (let i = 0; i < els.length; i++)
    els[i].className = els[i].className.replace(/ ?oculto/g, "") + (mostra ? "" : " oculto");
}

/* ELEVENLABS (29/10): com esse motor aparecem SO os ajustes que o site do ElevenLabs tem (.soEl) e somem os do RVC/Seed-VC
 * (.soRvc, .soSeed, .naoEl — a voz treinada, a Semelhanca do DUBX e o preset por voz). Sidon, manter musica e o modo do
 * arquivo valem pra todos os motores e ficam. */
function ajustaTrocar() {
  const m = elVcMotor ? elVcMotor.value : "seedvc";
  const rvc = m === "rvc", el = m === "elevenlabs";
  document.querySelectorAll(".soRvc").forEach((x) => { x.classList.toggle("oculto", !rvc); });
  document.querySelectorAll(".soEl").forEach((x) => { x.classList.toggle("oculto", !el); });
  document.querySelectorAll(".naoEl").forEach((x) => { x.classList.toggle("oculto", el); });
  const av = document.getElementById("dicaSoRvc");
  if (av) av.classList.toggle("oculto", rvc || el);
  _soSeed(m === "seedvc");   // "puxar a altura" so existe no Seed-VC
  const dica = document.getElementById("elDica");
  if (dica && !dica.textContent) dica.classList.add("oculto");
  if (el && !EL_VOZES_OK) listaVozesEl();
}

/* vozes da conta do ElevenLabs (GET /api/el-vozes — rota do P30). So pede quando o motor e escolhido: nao gasta chamada
 * a toa. Erro ou rota ainda inexistente viram uma linha de dica, nunca quebram o painel. */
let EL_VOZES_OK = false;
let EL_VOZES_PEDINDO = false;
let EL_VOZ_PENDENTE = null;   // {id, nome} vindo de preset/regerar antes da lista chegar
function garanteOpcaoEl(id, nome) {
  if (!elElVoz || !id) return;
  for (let i = 0; i < elElVoz.options.length; i++) if (elElVoz.options[i].value === id) return;
  const o = document.createElement("option");
  o.value = id; o.textContent = nome || id;
  elElVoz.appendChild(o);
}
function escolheVozEl(id, nome) {
  if (!elElVoz || !id) return;
  garanteOpcaoEl(id, nome);
  elElVoz.value = id; dispara(elElVoz, "change");
  if (!EL_VOZES_OK) EL_VOZ_PENDENTE = { id, nome };
}
async function listaVozesEl() {
  if (!elElVoz || EL_VOZES_PEDINDO) return;
  EL_VOZES_PEDINDO = true;
  const dica = document.getElementById("elDica");
  const diz = (t) => {
    if (!dica) return;
    dica.textContent = t || "";
    dica.classList.toggle("oculto", !t || !(elVcMotor && elVcMotor.value === "elevenlabs"));
  };
  try {
    const r = await fetch(DUBX + "/api/el-vozes");
    let j = null;
    try { j = await r.json(); } catch (_) { j = null; }
    if (!j || !Array.isArray(j.vozes)) throw new Error(r.status === 404 ? "o servidor do DUBX ainda não tem a rota do ElevenLabs (reinicie o DUBX)"
                                                                         : "resposta estranha do DUBX");
    const atual = (EL_VOZ_PENDENTE && EL_VOZ_PENDENTE.id) || elElVoz.value;
    elElVoz.textContent = "";
    const zero = document.createElement("option");
    zero.value = ""; zero.textContent = j.vozes.length ? "— vozes da sua conta —" : "— nenhuma voz na conta —";
    elElVoz.appendChild(zero);
    j.vozes.forEach((v) => {
      const o = document.createElement("option");
      o.value = v.id; o.textContent = v.nome || v.id;
      if (v.categoria) o.setAttribute("title", v.categoria);
      elElVoz.appendChild(o);
    });
    if (atual) garanteOpcaoEl(atual, EL_VOZ_PENDENTE && EL_VOZ_PENDENTE.nome);
    try { pintaFavoritosEl(); } catch (_) {}   // favoritos (★) no topo, com o nome que ele deu
    elElVoz.value = atual || "";
    REFAZENDO_LISTA_EL = true;   // refazer a lista nao reaplica favorito (senao: motor -> lista -> favorito -> motor, em laco)
    try { dispara(elElVoz, "change"); } finally { REFAZENDO_LISTA_EL = false; }
    EL_VOZ_PENDENTE = null;
    EL_VOZES_OK = !j.erro;
    diz(j.erro ? "ElevenLabs: " + j.erro : "");
  } catch (e) {
    diz("não consegui a lista de vozes do ElevenLabs: " + msgErro(e) + " — troque o motor e volte pra tentar de novo.");
  } finally {
    EL_VOZES_PEDINDO = false;
  }
}
/* SIDON ANTES / DEPOIS / SEM (Kakashi 29/10 16:35 [00:56]). A Forca so vale com Antes ou Depois: com Sem o slider apaga. */
function setaSidonQuando(q) {
  if (["antes", "depois", "sem"].indexOf(q) < 0) return;
  SIDON_QUANDO = q;
  document.querySelectorAll(".sbt-sidonq").forEach((x) => { x.classList.toggle("ativa", x.dataset.sq === q); });
  if (elVcSidon) elVcSidon.disabled = (q === "sem");
  const linha = document.getElementById("ctrlVcSidon");
  if (linha) linha.style.opacity = q === "sem" ? "0.4" : "";
}
document.querySelectorAll(".sbt-sidonq").forEach((b) => {
  b.addEventListener("click", () => setaSidonQuando(b.dataset.sq));
});
setaSidonQuando(SIDON_QUANDO);

if (elVcMotor) elVcMotor.addEventListener("change", ajustaTrocar);
ajustaTrocar();
aoClicar(document.getElementById("falar"), falar, "falar");

// o segmentado Nova faixa / Substituir do Trocar
document.querySelectorAll(".sbt-mlmodo").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".sbt-mlmodo").forEach((x) => x.classList.remove("ativa"));
    b.classList.add("ativa");
    MLMODO = b.dataset.mlmodo;
    const d = document.getElementById("dicaMlModo");
    if (d) d.textContent = MLMODO === "arquivo"
      ? "regrava o ARQUIVO original no disco, inteiro (backup em DUBX\\trabalho\\originais). Ctrl+Z NÃO desfaz — desfazer é restaurar o backup."
      : "o resultado entra numa faixa nova e o clipe original fica mudo — Ctrl+Z volta ao som de antes e Ctrl+Shift+Z refaz. Só o trecho do clipe marcado é processado.";
  });
});

document.querySelectorAll(".sbt-vcmodo").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".sbt-vcmodo").forEach((x) => x.classList.remove("ativa"));
    b.classList.add("ativa");
    VCMODO = b.dataset.vcmodo;
    const d = document.getElementById("dicaVcModo");
    if (d) d.textContent = VCMODO === "arquivo"
      ? "regrava o ARQUIVO original (backup em DUBX\\trabalho\\originais). Ctrl+Z NÃO desfaz — desfazer é restaurar o backup."
      : "o resultado cai numa faixa abaixo do clipe — o original não é tocado e o Ctrl+Z desfaz.";
  });
});

/* Ajustes do DUBX (teto do cache / faxina automatica / whisper proprio) —
 * carregados do servidor e salvos no change. Existiam so no app. */
async function cargaConfig() {
  try {
    const cfg = await (await fetch(DUBX + "/api/config")).json();
    const t = document.getElementById("espTeto");
    const a = document.getElementById("espAuto");
    const w = document.getElementById("espWhisper");
    if (t) t.value = Math.round(cfg.cache_teto_gb || 3);
    if (a) a.checked = !!cfg.faxina_auto;
    if (w) w.checked = cfg.whisper_proprio !== false;
  } catch (e) { /* servidor fora: o cartao fica nos defaults */ }
}
async function salvaConfig() {
  try {
    const corpo = {
      cache_teto_gb: Number(document.getElementById("espTeto").value) || 3,
      faxina_auto: document.getElementById("espAuto").checked,
      whisper_proprio: document.getElementById("espWhisper").checked,
    };
    const cfg = await (await fetch(DUBX + "/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo) })).json();
    escreve("ajustes guardados — teto " + cfg.cache_teto_gb + " GB, faxina automática "
            + (cfg.faxina_auto ? "ligada" : "desligada") + ", Whisper "
            + (cfg.whisper_proprio !== false ? "próprio" : "do OpenWhispr") + ".", "ok");
  } catch (e) { escreve("não deu pra salvar os ajustes: " + msgErro(e), "erro"); }
}
for (const id of ["espTeto", "espAuto", "espWhisper"]) {
  const el = document.getElementById(id);
  if (el) el.addEventListener("change", salvaConfig);
}
cargaConfig();

/* PELE DOS CHECKBOXES (23/08): o quadrado nativo do UXP e uma das "coisinhas
 * toscas" que ele apontou. O input real continua (escondido) — quem aparece e
 * um div, que pinta certo. dispara("change") mantem todo listener existente. */
function dispara(el, tipo) {
  try { el.dispatchEvent(new Event(tipo, { bubbles: true })); }
  catch (e) {
    const ev = document.createEvent("Event");
    ev.initEvent(tipo, true, false);
    el.dispatchEvent(ev);
  }
}
function peleCheckbox(cx) {
  if (!cx || cx.type !== "checkbox" || cx.dataset.compele) return;
  cx.dataset.compele = "1";
  const pele = document.createElement("div");
  pele.className = "cxpele" + (cx.checked ? " marcada" : "");
  const bola = document.createElement("div");
  bola.className = "cxbola";
  pele.appendChild(bola);
  cx.parentNode.insertBefore(pele, cx);
  cx.style.display = "none";
  const pinta = () => { pele.className = "cxpele" + (cx.checked ? " marcada" : ""); };
  pele.addEventListener("click", (ev) => {
    ev.preventDefault(); ev.stopPropagation();   // o label tambem alterna; evitar o dobro
    cx.checked = !cx.checked; pinta(); dispara(cx, "change");
  });
  cx.addEventListener("change", pinta);
}
/* ------------------------------------------------------------ PREVIAS
 * O resultado NAO vai direto pra timeline: entra aqui, ele OUVE (o servidor
 * toca — o UXP nao reproduz audio) e so o Concluir manda. Pedido de 23/08:
 * "fiz, nao gostei, to dando Ctrl+Z... deixa com um preview igual o app". */
let PREVIAS = [];   // {arquivo, c, etiqueta, picos, dur, _bars}
let TOCA = null;    // {p, desde, t0} — o que esta tocando (progresso por relogio)
let TOCA_TIMER = null;

function pintaProgresso() {
  if (!TOCA || !TOCA.p._bars) return;
  const frac = (TOCA.desde + (Date.now() - TOCA.t0) / 1000) / (TOCA.p.dur || 1);
  const corte = Math.floor(frac * TOCA.p._bars.length);
  TOCA.p._bars.forEach((b, i) => {
    b.className = "obar" + (i < corte ? " viva" : "");
  });
  if (frac >= 1) pararPrevia();
}

function pararPrevia() {
  fetch(DUBX + "/api/parar", { method: "POST" }).catch(() => {});
  if (TOCA && TOCA.p._bars) TOCA.p._bars.forEach((b) => { b.className = "obar"; });
  TOCA = null;
  if (TOCA_TIMER) { clearInterval(TOCA_TIMER); TOCA_TIMER = null; }
}

async function tocaPrevia(p, desde) {
  pararPrevia();
  try {
    const r = await (await fetch(DUBX + "/api/tocar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ arquivo: p.arquivo, inicio: desde || 0 }) })).json();
    if (r.erro) { escreve("ouvir: " + r.erro, "erro"); return; }
    TOCA = { p, desde: desde || 0, t0: Date.now() };
    TOCA_TIMER = setInterval(pintaProgresso, 120);
  } catch (e) { escreve("ouvir: " + msgErro(e), "erro"); }
}

/* ---------------------------------------------------- minhas vozes
 * Renomear e apagar, dentro do popover da engrenagem. O campo de renomear
 * nasce DENTRO do popover de proposito: o escondeCamposFora deixa visiveis
 * os campos que estao dentro do popover aberto. */
async function desenhaVozesConfig() {
  const caixa = document.getElementById("vozesConfig");
  if (!caixa) return;
  caixa.textContent = "";
  let vozes = [];
  try {
    vozes = (await (await fetch(DUBX + "/api/vozes")).json()).vozes || [];
  } catch (e) { caixa.textContent = "o DUBX não respondeu."; return; }
  if (!vozes.length) { caixa.textContent = "nenhuma voz cadastrada ainda."; return; }

  vozes.forEach((v) => {
    const linha = document.createElement("div");
    linha.className = "vzitem";
    const nome = document.createElement("div");
    nome.className = "vznome"; nome.textContent = v.nome;
    const marca = document.createElement("div");
    marca.className = "vzmarca";
    marca.textContent = v.pode_trocar ? "RVC + Seed-VC"
                      : v.pode_trocar_seedvc ? "Seed-VC afinado" : "só amostra";
    const bRen = document.createElement("div");
    bRen.className = "hbt"; bRen.textContent = "✎"; bRen.title = "renomear";
    bRen.setAttribute("role", "button");
    const bDel = document.createElement("div");
    bDel.className = "hbt"; bDel.textContent = "✕"; bDel.title = "apagar do PC";
    bDel.setAttribute("role", "button");

    bRen.addEventListener("click", () => {
      linha.textContent = "";
      const campo = document.createElement("input");
      campo.type = "text"; campo.value = v.nome; campo.className = "campo";
      const ok = document.createElement("div");
      ok.className = "hbt"; ok.textContent = "✓"; ok.title = "salvar";
      ok.setAttribute("role", "button");
      const nao = document.createElement("div");
      nao.className = "hbt"; nao.textContent = "✕"; nao.title = "cancelar";
      nao.setAttribute("role", "button");
      nao.addEventListener("click", desenhaVozesConfig);
      ok.addEventListener("click", async () => {
        const novo = texto(campo);
        if (!novo || novo === v.nome) { desenhaVozesConfig(); return; }
        try {
          const r = await (await fetch(DUBX + "/api/renomear-voz", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ de: v.nome, para: novo }) })).json();
          if (r.erro) { escreve("renomear: " + r.erro, "erro"); }
          else { escreve("voz \"" + v.nome + "\" agora é \"" + novo + "\".", "ok"); }
        } catch (e) { escreve("renomear: " + msgErro(e), "erro"); }
        desenhaVozesConfig(); listaVozes();
      });
      linha.appendChild(campo); linha.appendChild(ok); linha.appendChild(nao);
      vestirTudo();          // o campo nasce agora: precisa da moldura
    });

    let confirmando = false;
    bDel.addEventListener("click", async () => {
      if (!confirmando) {
        confirmando = true;
        bDel.textContent = "apagar?";
        bDel.style.color = "var(--danger)";
        setTimeout(() => {
          confirmando = false; bDel.textContent = "✕"; bDel.style.color = "";
        }, 4000);
        return;
      }
      try {
        const r = await (await fetch(DUBX + "/api/apagar-voz", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nome: v.nome }) })).json();
        if (r.erro) escreve("apagar: " + r.erro, "erro");
        else escreve("voz \"" + v.nome + "\" apagada do PC.", "ok");
      } catch (e) { escreve("apagar: " + msgErro(e), "erro"); }
      desenhaVozesConfig(); listaVozes();
    });

    linha.appendChild(nome); linha.appendChild(marca);
    linha.appendChild(bRen); linha.appendChild(bDel);
    caixa.appendChild(linha);
  });
}

/* ------------------------------------------------------------ historico
 * Nada nunca sumiu: o servidor guarda historico.json desde 16/08 e o app ja
 * mostrava. Faltava a tela AQUI. Duas acoes, que sao as que fazem sentido
 * dentro do Premiere: ouvir e por na timeline. */
let HIST_ABERTO = false;

async function desenhaHistorico() {
  const caixa = document.getElementById("historico");
  if (!caixa) return;
  caixa.textContent = "";
  let itens = [];
  try {
    const r = await (await fetch(DUBX + "/api/historico")).json();
    itens = r.historico || [];
  } catch (e) {
    caixa.textContent = "o DUBX não respondeu — abra o app uma vez e tente de novo.";
    return;
  }
  if (!itens.length) { caixa.textContent = "ainda não gerou nada por aqui."; return; }

  itens.slice(0, 40).forEach((h) => {
    const arquivo = h.saida || h.previa;
    const linha = document.createElement("div");
    linha.className = "hitem" + (h.existe && arquivo ? "" : " sumiu");
    const nome = document.createElement("div");
    nome.className = "hnome";
    nome.textContent = h.amigavel || h.rotulo || h.id;
    nome.title = (h.modo || "") + " · " + (arquivo || "arquivo apagado na faxina");
    const quando = document.createElement("div");
    quando.className = "hquando";
    quando.textContent = h.quando || "";
    linha.appendChild(nome); linha.appendChild(quando);

    if (h.existe && arquivo) {
      const bOuvir = document.createElement("div");
      bOuvir.className = "hbt"; bOuvir.textContent = "▶";
      bOuvir.title = "ouvir (sai pelo alto-falante do PC)";
      bOuvir.setAttribute("role", "button");
      bOuvir.addEventListener("click", async () => {
        try {
          const r = await (await fetch(DUBX + "/api/tocar", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ arquivo, inicio: 0 }) })).json();
          if (r.erro) escreve("ouvir: " + r.erro, "erro");
        } catch (e) { escreve("ouvir: " + msgErro(e), "erro"); }
      });
      const bPor = document.createElement("div");
      bPor.className = "hbt"; bPor.textContent = "⤓";
      bPor.title = "pôr na timeline (um Ctrl+Z desfaz)";
      bPor.setAttribute("role", "button");
      bPor.addEventListener("click", async () => {
        if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
        try {
          const postos = await colocaNaTimeline([arquivo], null, h.amigavel || h.rotulo || "histórico");
          escreve(postos ? (h.amigavel || h.rotulo) + " → timeline."
                         : "não consegui pôr na timeline.", postos ? "ok" : "erro");
        } catch (e) { escreve("timeline: " + msgErro(e), "erro"); }
      });
      linha.appendChild(bOuvir); linha.appendChild(bPor);
    }
    caixa.appendChild(linha);
  });
}

seguro(function () {
  const cab = document.getElementById("histCab");
  const caixa = document.getElementById("historico");
  const chev = document.getElementById("histChev");
  if (!cab || !caixa) return;
  cab.addEventListener("click", function () {
    HIST_ABERTO = !HIST_ABERTO;
    caixa.className = HIST_ABERTO ? "" : "oculto";
    if (chev) chev.textContent = HIST_ABERTO ? "▾" : "▸";
    if (HIST_ABERTO) desenhaHistorico();
  });
}, "historico");

/* picos da onda: pedidos UMA vez por previa, desenhados quando chegam */
/* uma vez POR ARQUIVO (29/10): o desenhaPrevias pede a onda de toda previa sem picos, e cada onda que chega redesenha — com a
 * fila restaurada no boot (varias previas sem onda) isso virava N² pedidos ao servidor */
async function carregaOnda(p) {
  if (p._ondaDe === p.arquivo) return;
  const arq = p.arquivo;
  p._ondaDe = arq;
  try {
    const r = await (await fetch(DUBX + "/api/onda", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ arquivo: arq }) })).json();
    if (p.arquivo !== arq) return;   // a previa mudou de arquivo no meio (editar/↶): a onda nova ja foi pedida
    if (r.picos) { p.picos = r.picos; p.dur = r.dur; desenhaPrevias(); }
    else p._ondaDe = null;   // sem onda: tenta de novo no proximo redesenho (falha nao redesenha, entao nao vira laco)
  } catch (e) { if (p.arquivo === arq) p._ondaDe = null; /* sem onda: a linha fica so com os botoes */ }
}

/* FLUXO | MANUAL (24/08): "isso ja e o proprio fluxo" — a fila de previas
 * com Editar+Concluir E o jeito de trabalhar. Manual = direto na timeline. */
let MODOTRAB = "fluxo";
function previaLigada() { return MODOTRAB === "fluxo"; }

/* EDITAR NA FILA (24/08): roda uma acao EM CIMA da faixa em stand-by,
 * com os ajustes atuais do card daquele modo, e o resultado SUBSTITUI a
 * faixa — encadeia ate ele Concluir. Cada faixa e independente. */
const ROTULO_ACAO = { melhorar: "melhorado", trocar: "voz trocada",
                      dublar: "dublado", treinar: "voz treinada" };
/* o ROTULO_ACAO e particípio ("voz treinada") e serve pro fim; enquanto roda,
   a barra precisa do gerúndio, senao anuncia pronto o que esta no meio */
const EM_CURSO = { melhorar: "melhorando", trocar: "trocando a voz",
                   dublar: "dublando", treinar: "treinando a voz" };

function botaoDaAcao(acao) {
  return { melhorar: btMelhorar, trocar: btTrocar,
           dublar: btDublar, treinar: btTreinar }[acao] || null;
}

function pintaFita() {
  const fita = document.getElementById("fitaPrevia");
  if (!fita) return;
  while (fita.firstChild) fita.removeChild(fita.firstChild);
  if (!EDITANDO) { fita.className = "fita"; return; }
  fita.className = "fita on";
  fita.title = "o botão desta tela aplica NESTA prévia, não na seleção da "
             + "timeline. Clique aqui pra cancelar.";

  const marca = document.createElement("div");
  marca.className = "fita-marca";
  marca.textContent = "EDITANDO";

  const nome = document.createElement("div");
  nome.className = "fita-nome";
  nome.textContent = EDITANDO.p.etiqueta;

  const sair = document.createElement("div");
  sair.className = "fita-sair";
  sair.textContent = "\u2715";
  sair.title = "cancelar — volta a trabalhar na seleção da timeline";
  sair.setAttribute("role", "button");
  sair.addEventListener("click", saiDaEdicao);

  fita.appendChild(marca); fita.appendChild(nome); fita.appendChild(sair);
}

function entraNaEdicao(p, acao) {
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  EDITANDO = { p, acao };
  /* pula pro modo da acao: os controles de verdade ja moram la. Duplicar
   * eles num popover seria manter dois lugares com a mesma coisa. */
  try { if (window.mostraModo) window.mostraModo(acao); } catch (_) {}
  const bt = botaoDaAcao(acao);
  if (bt) { ROTULO_ANTES = bt.textContent; bt.textContent = "Aplicar nesta prévia"; }
  pintaFita();
  atualizaBotao();
  escreve("mexendo na prévia \"" + p.etiqueta + "\" — ajuste os parâmetros e clique em "
          + "\"Aplicar nesta prévia\".");
}

function saiDaEdicao() {
  if (!EDITANDO) return;
  const bt = botaoDaAcao(EDITANDO.acao);
  if (bt && ROTULO_ANTES) bt.textContent = ROTULO_ANTES;
  EDITANDO = null; ROTULO_ANTES = "";
  pintaFita();
  atualizaBotao();
}

/* cada handler de acao comeca por aqui: se ele entrou pela bolinha da fila,
 * o clique vai pra faixa em vez de ir pra selecao da timeline */
function desviaPraPrevia(acao) {
  if (!EDITANDO || EDITANDO.acao !== acao) return false;
  editaPrevia(EDITANDO.p, acao);
  return true;
}

seguro(function () {
  const fita = document.getElementById("fitaPrevia");
  if (fita) fita.addEventListener("click", saiDaEdicao);
}, "fita da previa");

function payloadEdicao(acao, p) {
  if (acao === "melhorar")
    return { modo: "melhorar", dose: DOSE, lufs: Number(elLufs.value) || -16 };
  if (acao === "trocar") {
    const voz = vozDaTroca();
    if (!voz) throw new Error(faltaVozTroca());
    return Object.assign({ modo: "trocar" }, ajustesTroca(voz));
  }
  if (acao === "dublar") {
    const origem = elDubOrigem.value, alvo = elDubAlvo.value;
    if (origem === alvo) throw new Error("os dois idiomas estão iguais — ajuste no modo Dublar");
    return { modo: "dublar", origem, alvo,
             atuacao: Number(elDubAtuacao.value) / 100,
             adesao: Number(elDubAdesao.value) / 100,
             sidon: Number(elDubSidon.value) };
  }
  if (acao === "treinar") {
    const nome = texto(elNomeVoz);
    if (!nome) throw new Error("dê um nome pra voz no modo Treinar primeiro");
    if (!p.dur) throw new Error("espere a onda da faixa carregar (preciso da duração)");
    return { modo: "treinar_premiere", nome,
             epocas: Number(elEpocas.value) || 300,
             trechos: [{ arquivo: p.arquivo, inicio: 0, fim: p.dur }] };
  }
  throw new Error("ação desconhecida: " + acao);
}

async function editaPrevia(p, acao) {
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  let corpo;
  try { corpo = payloadEdicao(acao, p); }
  catch (e) { avisaFalta(msgErro(e)); return; }
  RODANDO = true; atualizaBotao(); barraMostra();
  pararPrevia();
  escreve("editando \"" + p.etiqueta + "\": " + acao + "…");
  try {
    // treinar_premiere leva a faixa dentro de `trechos`; os outros levam
    // o arquivo da faixa como entrada normal
    // REGERAR (29/10): a partir do ORIGINAL (e do trecho dele), nao em cima da previa — senao troca voz sobre voz trocada
    const doOriginal = p._regerarDoOriginal && p.origem;
    p._regerarDoOriginal = false;
    const corpoFinal = (corpo.modo === "treinar_premiere")
      ? corpo : Object.assign({ arquivo: doOriginal ? p.origem : p.arquivo }, corpo, doOriginal ? (p.trecho || {}) : {});
    const r = await fetch(DUBX + "/api/processar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpoFinal) });
    const { tarefa, erro } = await r.json();
    if (!tarefa) throw new Error(erro || "o DUBX não aceitou a tarefa");
    let pronto = null;
    for (;;) {
      await espera(1000);
      const e = await (await fetch(DUBX + "/api/estado?t=" + tarefa)).json();
      if (e.estado === "pronto") { pronto = e; break; }
      if (e.estado === "cancelado") throw new Error("cancelado");
      if (e.estado === "erro") throw new Error(e.mensagem || "falhou no DUBX");
      barraTarefa(e, EM_CURSO[acao] || acao);
    }
    if (acao === "treinar") {
      escreve("voz treinada com \"" + p.etiqueta + "\" — já aparece nas listas de voz. A faixa segue na fila.", "ok");
      try { listaVozes(); } catch (_) {}
    } else {
      const novo = pronto.previa || pronto.saida;
      if (!novo) throw new Error("a edição não devolveu arquivo");
      // RETROCEDER (24/08): guarda o estado anterior antes de trocar. Os
      // arquivos intermediarios ficam no disco ate a faxina, entao voltar
      // e so re-apontar — nao refaz nada.
      p.pilha = (p.pilha || []).concat([{ arquivo: p.arquivo, etapas: (p.etapas || []).slice(), ajustes: p.ajustes }]);
      p.arquivo = novo;
      if (acao === "trocar") p.ajustes = Object.assign({}, corpo);   // o que ESTA versao usou (pra setinha e regerar)
      p.etapas = (p.etapas || []).concat([acao]);  // vira pontinho na linha
      p.picos = null; p.dur = null; p._bars = null;
      desenhaPrevias();
      carregaOnda(p);
      escreve("faixa editada (" + ROTULO_ACAO[acao] + ") — ouça; se não gostou, o ↶ volta um passo.", "ok");
    }
    plim("ok");
  } catch (e) {
    escreve("editar (" + acao + "): " + msgErro(e), "erro");
    mostrar("erro", "editar: " + msgErro(e));
    plim("erro");
  } finally {
    saiDaEdicao();          // aplicou: volta o botao ao normal
    RODANDO = false; LOTE = null; atualizaBotao();
    barraSet(1, "concluído");
    BARRA_TIMER = setTimeout(barraEsconde, 2000);
  }
}

const MIME_ARRASTO = { wav: "audio/wav", mp3: "audio/mpeg", aac: "audio/aac", m4a: "audio/m4a", aif: "audio/aif",
                       mp4: "video/mp4", mov: "video/quicktime" };
function uriArquivo(caminho) {
  let c = String(caminho || "");
  if (/^[A-Za-z]:\\/.test(c)) c = "/" + c.replace(/\\/g, "/");   // C:\x\y -> /C:/x/y (jeito do exemplo da Adobe)
  return "file://" + encodeURI(c);
}
function preparaArrasto(el, p) {
  if (!p || !p.arquivo) return;
  const ext = String(p.arquivo).split(".").pop().toLowerCase();
  const mime = MIME_ARRASTO[ext];
  if (!mime) return;
  el.setAttribute("draggable", "true");
  el.title = (el.title ? el.title + "\n" : "") + "arraste pra timeline ou pro painel Projeto";
  el.addEventListener("dragstart", (ev) => {
    const payload = JSON.stringify({ version: "1.0.0", items: [{ name: p.etiqueta + "." + ext, display_name: p.etiqueta,
                                                                  content_type: mime, uri: uriArquivo(p.arquivo) }] });
    try { ev.dataTransfer.setData("text/plain", payload); } catch (e) { escreve("arrastar: " + msgErro(e), "erro"); return; }
    try { ev.dataTransfer.effectAllowed = "copyMove"; ev.dataTransfer.dropEffect = "copy"; } catch (_) {}
    pararPrevia();
    escreve("arrastando \"" + p.etiqueta + "\" — solte na timeline (coloca) ou no Projeto (importa).");
  });
}

/* CONCLUIR de uma previa. escolhida = null → automatico (a faixa do DUBX abaixo da origem); escolhida = {i, nome, criar, chave}
 * → a que ele clicou no «pôr na faixa» (P41). Os dois lembram a faixa em DUBX_FAIXA e herdam volume/efeitos do original. */
async function concluiPrevia(p, escolhida) {
  pararPrevia();   // "apertei concluir e o audio ficou infinito" (23/08)
  // clique duplo no Concluir punha o MESMO audio duas vezes na timeline (o pouso leva segundos e nao havia trava)
  if (p._concluindo) { escreve("\"" + p.etiqueta + "\" já está indo pra timeline…"); return; }
  p._concluindo = true;
  try {
    /* PREVIA RESTAURADA (fila que sobrevive ao reload, 29/10): o objeto do clipe do Premiere nao viaja no localStorage.
     * Re-acha o clipe original na timeline pelo caminho + posicao guardados; sem achar, NAO usa a lista de faixas (sem
     * o trecho do clipe toda faixa parece livre e a sugerida seria a A1 por cima do audio dele) — cai no automatico. */
    if (!p.c && p.cRef) {
      p.c = await reacheClipe(p.cRef);
      if (!p.c) escreve("não achei o clipe original de \"" + p.etiqueta + "\" na sequência aberta — vai no cursor, numa faixa vazia.", "erro");
    }
    /* 29/10 21h30 (ele: «coloca esse mecanismo de criar uma faixa de áudio nova e coloca ela lá, só isso. No caso já tem a
     * 10, então... apertar o concluir e ir»): SEM pergunta. A faixa = a primeira livre NO TRECHO logo abaixo da faixa do
     * clipe original (a A10 dele); se nenhuma estiver livre ali, cria uma nova. Escolher a dedo = botao «pôr na faixa». */
    let faixa = null;
    if (escolhida) {
      faixa = escolhida;
      escreve("pôr na faixa → " + faixa.nome + (faixa.criar ? " (nova)" : ""));
    } else if (p.c && p.c.item) try {
      const info = await faixasParaEscolher(p.c);
      faixa = info.sugerida || { i: info.lista.length, nome: "nova A" + (info.lista.length + 1), criar: true };
      faixa.chave = info.chave;
      escreve("concluir → " + faixa.nome + (faixa.criar ? " (nova)" : ""));
    } catch (e) { escreve("faixas: " + msgErro(e) + " — vou pelo automático.", "erro"); }
    const postos = await colocaNaTimeline([p.arquivo], p.c, p.etiqueta, false, faixa);
    if (postos && faixa) {
      if (faixa.chave) DUBX_FAIXA[faixa.chave] = faixa.i;   // as proximas da mesma origem caem aqui
      // VOLUME HERDADO (Kakashi 17:53 [02:32–04:33]: «ele não herdou o mesmo volume do áudio original... tem que herdar esse
      // volume da fala anterior... eu quero que isso seja automático»): copia o Nível do Volume do clipe original pro novo
      try { await herdaVolume(p.c, faixa.i); } catch (e) { escreve("volume: não consegui copiar (" + msgErro(e) + ")", "erro"); }
      try { await herdaEfeitos(p.c, faixa.i); } catch (e) { escreve("efeitos: não consegui copiar (" + msgErro(e) + ")", "erro"); }
    }
    if (postos) {
      escreve(p.etiqueta + " → timeline.", "ok");
      tiraDaFila(p);
    } else {
      escreve("não consegui pôr na timeline — a prévia continua aqui.", "erro");
      desenhaPrevias();
    }
  } catch (e) { escreve("concluir: " + msgErro(e), "erro"); desenhaPrevias(); }
  finally { p._concluindo = false; }
}

function desenhaPrevias() {
  salvaFila();   // toda mudanca na fila passa por um redesenho: aqui ela vai pro disco do painel
  const cartao = document.getElementById("cartaoPrevias");
  const lista = document.getElementById("listaPrevias");
  if (!cartao || !lista) return;
  cartao.classList.toggle("oculto", PREVIAS.length === 0);
  while (lista.firstChild) lista.removeChild(lista.firstChild);
  PREVIAS.forEach((p, i) => {
    const linha = document.createElement("div");
    linha.className = "previa";
    const cab = document.createElement("div");
    cab.className = "previa-cab";
    const nome = document.createElement("div");
    nome.className = "previa-nome";
    nome.textContent = p.etiqueta;
    nome.title = p.arquivo;
    /* ARRASTAR PRA TIMELINE (Kakashi 29/10 16:35 [07:55]: "no FindX eu consigo arrastar para timeline, é só você colocar a
     * mesma feature"). O FINDX e CEP (chave com.adobe.cep.dnd.file.N); aqui e UXP: o jeito oficial da Adobe e um JSON em
     * text/plain no dragstart de um elemento draggable (developer.adobe.com/premiere-pro/uxp/plugins/tutorials/drag-and-drop).
     * A doc diz Premiere 27; o PR da Adobe dizia 25.4 — testado no 26.5.2 dele em 29/10 (ver CONTEXTO). Quem decide e o alvo
     * do solto: timeline importa E coloca; painel Projeto so importa. */
    // 29/10 21h30: arrastar DESLIGADO — no Premiere 26.5 o UXP dispara o dragstart mas nunca entrega o arquivo, e cada clique na
    // onda virava um «arrastando…» no registro sem tocar («fica aparecendo essa porra aqui no registro»). Volta quando ele tiver o 27.
    const bOuvir = document.createElement("div");
    bOuvir.className = "pglifo"; bOuvir.textContent = "▶";
    bOuvir.title = "tocar do começo";
    bOuvir.setAttribute("role", "button");
    bOuvir.addEventListener("click", () => tocaPrevia(p, 0));
    const bParar = document.createElement("div");
    bParar.className = "pglifo"; bParar.textContent = "■";
    bParar.title = "parar";
    bParar.setAttribute("role", "button");
    bParar.addEventListener("click", pararPrevia);
    /* as ACOES sao bolinhas coloridas ("nao precisa nem do editar",
     * 24/08): repousar o mouse mostra a palavra na cor; clicar roda a
     * acao em cima da faixa. As etapas JA aplicadas viram pontinhos
     * ao lado do nome. */
    const edlinha = document.createElement("div");
    edlinha.className = "edlinha";
    const rotEd = document.createElement("div");
    rotEd.className = "edrot";
    const ACOES = [["melhorar", "Melhorar", "#30d158"],
                   ["trocar", "Trocar voz", "#0a84ff"],
                   ["dublar", "Dublar", "#ff9f0a"],
                   ["treinar", "Treinar voz", "#ff453a"]];
    const ROTULO_PADRAO = "editar a faixa:";
    rotEd.textContent = ROTULO_PADRAO;
    ACOES.forEach((a) => {
      const cel = document.createElement("div");
      cel.className = "bcel";
      cel.setAttribute("role", "button");
      const b = document.createElement("div");
      b.className = "bopt";
      b.style.background = a[2];
      cel.appendChild(b);
      cel.addEventListener("mouseover", () => {
        rotEd.textContent = a[1]; rotEd.style.color = a[2];
        b.style.width = "19px"; b.style.height = "19px";
        b.style.borderRadius = "9px";   // pintor: raio inteiro <= metade
      });
      cel.addEventListener("mouseout", () => {
        rotEd.textContent = ROTULO_PADRAO; rotEd.style.color = "";
        b.style.width = ""; b.style.height = ""; b.style.borderRadius = "";
      });
      // ABRE os parametros (pedido de 24/08) em vez de rodar automatico
      cel.addEventListener("click", () => entraNaEdicao(p, a[0]));
      edlinha.appendChild(cel);
    });
    edlinha.appendChild(rotEd);
    const pontosEtapa = [];
    (p.etapas || []).forEach((et) => {
      const a = ACOES.filter((x) => x[0] === et)[0];
      if (!a) return;
      const ponto = document.createElement("div");
      ponto.className = "betapa";
      ponto.style.background = a[2];
      ponto.addEventListener("mouseover", () => {
        rotEd.textContent = a[1]; rotEd.style.color = a[2];
        ponto.style.width = "10px"; ponto.style.height = "10px";
        ponto.style.borderRadius = "5px";
      });
      ponto.addEventListener("mouseout", () => {
        rotEd.textContent = ROTULO_PADRAO; rotEd.style.color = "";
        ponto.style.width = ""; ponto.style.height = ""; ponto.style.borderRadius = "";
      });
      pontosEtapa.push(ponto);
    });
    /* RETROCEDER: so aparece quando ha o que desfazer. Volta arquivo e
     * pontinho de etapa ao estado de antes da ultima edicao. */
    let bVolta = null;
    if ((p.pilha || []).length) {
      bVolta = document.createElement("div");
      bVolta.className = "pbt";
      bVolta.textContent = "\u21b6";
      bVolta.title = "desfazer a última edição desta faixa";
      bVolta.setAttribute("role", "button");
      bVolta.addEventListener("click", () => {
        if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
        pararPrevia();
        const antes = p.pilha.pop();
        p.arquivo = antes.arquivo; p.etapas = antes.etapas; p.ajustes = antes.ajustes;
        p.picos = null; p.dur = null; p._bars = null;
        desenhaPrevias(); carregaOnda(p);
        escreve("voltei um passo em \"" + p.etiqueta + "\".", "ok");
      });
    }

    const bVai = document.createElement("div");
    bVai.className = "pbt vai"; bVai.textContent = "Concluir";
    bVai.setAttribute("role", "button");
    bVai.addEventListener("click", () => concluiPrevia(p, null));
    /* «PÔR NA FAIXA» (P41): o Concluir continua automatico; este abre a lista de faixas DENTRO do card e ele escolhe. */
    const bFaixa = document.createElement("div");
    bFaixa.className = "pbt mini" + (p._escolhendo ? " aberto" : ""); bFaixa.textContent = "pôr na faixa";
    bFaixa.title = "escolher a faixa (o Concluir escolhe sozinho: a do DUBX abaixo da origem)";
    bFaixa.setAttribute("role", "button");
    bFaixa.addEventListener("click", () => abreListaFaixas(p));
    const bFora = document.createElement("div");
    bFora.className = "pfechar"; bFora.textContent = "✕";
    bFora.title = "descartar (a faxina limpa o arquivo)";
    bFora.setAttribute("role", "button");
    bFora.addEventListener("click", () => {
      pararPrevia();
      tiraDaFila(p);
      escreve("prévia descartada.", "ok");
    });
    /* linha 1: nome (colado na onda) + play/parar na direita.
     * O Concluir e o descartar descem pro rodape — "tá muita
     * informação" numa linha so (24/08). */
    cab.appendChild(nome);
    pontosEtapa.forEach((x) => cab.appendChild(x));
    /* REFAZER no cabecalho, do tamanho do play/parar (Kakashi 17:53 [00:00]: «não é pra o botão estar aqui, é pra ele estar aqui,
     * do mesmo tamanho desses outros dois... do tamanho dessa bola») */
    if (p.ajustes) {
      const bRe = document.createElement("div");
      bRe.className = "pglifo"; bRe.textContent = "↻"; bRe.setAttribute("role", "button");
      bRe.title = "refazer: abre o Trocar voz com estes parâmetros e gera de novo a partir do original";
      bRe.addEventListener("click", () => {
        if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
        const a = p.ajustes;
        // no ElevenLabs a voz mora no dropdown da conta (aplicaPresetObjeto escolhe pelo el_voice_id), nao nas treinadas
        if (a.voz && a.motor_vc !== "elevenlabs") { elVozes.value = a.voz; dispara(elVozes, "change"); }
        aplicaPresetObjeto(a);
        entraNaEdicao(p, "trocar");
        p._regerarDoOriginal = true;
      });
      cab.appendChild(bRe);
    }
    cab.appendChild(bOuvir); cab.appendChild(bParar);
    linha.appendChild(cab);
    // a onda entra aqui embaixo (bloco adiante) e as bolinhas por ULTIMO

    /* A ONDA: 96 barras. Clicar numa barra toca DALI — a barra sabe o
     * proprio indice, entao nao precisa de getBoundingClientRect (que
     * devolve 0 no UXP). */
    if (p.picos) {
      const onda = document.createElement("div");
      onda.className = "onda";
      p._bars = [];
      /* ARRASTAR PELA ONDA (29/10 chat: «eu clicando na waveform também, segurando, porque clicando dá play»): a onda e
       * draggable; o CLIQUE curto toca dali, o segurar-e-arrastar leva pra timeline (dragstart cancela o play). */
      p.picos.forEach((pico, k) => {
        // a CELULA de altura cheia recebe o clique — antes so a barrinha
        // desenhada era o alvo e ele tinha que mirar no pixel (24/08)
        const cel = document.createElement("div");
        cel.className = "ocel";
        const b = document.createElement("div");
        b.className = "obar";
        b.style.height = Math.max(2, Math.round(pico * 34)) + "px";
        cel.appendChild(b);
        cel.addEventListener("click", () => {
          tocaPrevia(p, (k / p.picos.length) * (p.dur || 0));
          // pinta ja ate o ponto clicado, sem esperar o timer
          p._bars.forEach((x, j) => { x.className = "obar" + (j < k ? " viva" : ""); });
        });
        p._bars.push(b);
        onda.appendChild(cel);
      });
      linha.appendChild(onda);
    } else {
      p._bars = null;
      carregaOnda(p);   // busca uma vez; redesenha quando chegar
    }
    /* PARAMETROS DA PREVIA (29/10 [00:00] «uma setinha para eu abrir aqui e aí colapsar se eu quiser, tipo configs») +
     * REGERAR ([12:44] «se eu quiser regerar essa, eu clico em algum botão... abre a voz específica pra eu regerar ela só,
     * com os parâmetros»; [13:43] «Isso, boa»). O regerar roda a troca DE NOVO a partir do original, com os sliders
     * preenchidos pelos parametros desta previa (ele mexe e aplica). */
    if (p.ajustes) {
      const a = p.ajustes;
      const cfg = document.createElement("div");
      cfg.className = "pcfg";
      const seta = document.createElement("span");
      seta.className = "pseta"; seta.textContent = p._cfgAberta ? "▾" : "▸";
      seta.setAttribute("role", "button"); seta.title = "ver os parâmetros desta prévia";
      const txt = document.createElement("span");
      const pc = (v) => (v === undefined || v === null) ? "?" : Math.round(Number(v) * 100) + "%";
      const F0 = { rmvpe: "Equilíbrio (rmvpe)", crepe: "Mais preciso, lento (crepe)", fcpe: "Rápido (fcpe)" };
      const sid = a.sidon_quando === "sem" ? " · Sidon: sem"
        : " · Sidon " + (a.sidon_quando || "depois") + " " + (a.sidon ?? "?") + "%";
      const det = a.motor_vc === "elevenlabs"
        ? ("voz " + (a.el_nome || a.voz || "?") + " · motor ElevenLabs" + sid + " · estabilidade " + pc(a.el_estabilidade)
           + " · semelhança " + pc(a.el_semelhanca) + " · exagero de estilo " + pc(a.el_estilo)
           + (a.el_speaker_boost ? " · speaker boost" : "") + (a.el_remover_ruido ? " · remove ruído" : ""))
        : ("voz " + (a.voz || "?") + " · motor " + (a.motor_vc || "?") + sid + " · presença " + pc(a.presenca)
           + " · atuação " + pc(a.atuacao) + " · semelhança " + pc(a.semelhanca) + " · tom " + (a.tom ?? "?")
           + " · entonação " + (F0[a.f0] || a.f0 || "?"));
      txt.textContent = p._cfgAberta ? det + (a.preservar ? " · mantém música" : "") : "configs";
      seta.addEventListener("click", () => { p._cfgAberta = !p._cfgAberta; desenhaPrevias(); });
      cfg.appendChild(seta); cfg.appendChild(txt);
      linha.appendChild(cfg);
    }
    /* rodape: bolinhas de acao a esquerda, Concluir e descartar a direita */
    const pe = document.createElement("div");
    pe.className = "previa-pe";
    edlinha.style.marginTop = "0";
    pe.appendChild(edlinha);
    const vazio = document.createElement("div");
    vazio.className = "previa-vazio";
    pe.appendChild(vazio);
    if (bVolta) pe.appendChild(bVolta);
    pe.appendChild(bFaixa); pe.appendChild(bVai); pe.appendChild(bFora);
    linha.appendChild(pe);
    if (p._escolhendo) {
      let bloco;
      if (p._faixas) {
        bloco = escolheFaixa(p._faixas, (f) => {
          p._escolhendo = false; p._faixas = null;
          desenhaPrevias();             // a lista some na hora; o pouso leva segundos
          if (f) concluiPrevia(p, f);
        });
      } else {
        bloco = document.createElement("div");
        bloco.className = "pcfg";
        bloco.textContent = p._faixasErro ? "pôr na faixa: " + p._faixasErro + "." : "lendo as faixas…";
        if (p._faixasErro) bloco.style.color = "var(--danger)";
      }
      linha.appendChild(bloco);
    }
    lista.appendChild(linha);
    /* (juiz P41) a lista tem teto de 180 px e a sugerida fica logo ABAIXO da origem (a origem dele e a A10: a A11 cairia fora
     * do teto, escondida). Rola SO a lista (nao o painel) ate a sugerida ficar no meio. So depois de pendurar no documento,
     * senao nao ha medida. getBoundingClientRect existe no UXP; se faltar, nada quebra. */
    if (p._escolhendo && p._faixas) {
      try {
        const rol = linha.querySelector(".escolha-rol"), sug = rol && rol.querySelector(".sugerida");
        if (rol && sug && rol.getBoundingClientRect) {
          const R = rol.getBoundingClientRect(), S = sug.getBoundingClientRect();
          rol.scrollTop += (S.top - R.top) - (R.height - S.height) / 2;
        }
      } catch (_) {}
    }
  });
}

/* Todo resultado de audio passa por aqui: previa ligada = espera o ouvido;
 * desligada = timeline direto (comportamento antigo). Devolve o texto do log. */
/* FILA DE ENTREGA DO LOTE (25/08). Enquanto ela existe, cada resultado
 * pronto so entra na fila; quem poe na timeline e o fechaLote(), de uma vez. */
let ENTREGA_LOTE = null;

function abreLote() {
  /* se um lote anterior morreu no meio (excecao entre o abre e o fecha), os
     itens dele ficariam pendurados e cairiam na timeline junto com os novos */
  if (ENTREGA_LOTE && ENTREGA_LOTE.length)
    escreve("descartei " + ENTREGA_LOTE.length + " resultado(s) de um lote que nao terminou.", "erro");
  ENTREGA_LOTE = [];
}

async function fechaLote(etiqueta, silenciaOrigem) {
  const itens = ENTREGA_LOTE || [];
  ENTREGA_LOTE = null;
  if (!itens.length) return 0;
  return colocaLoteNaTimeline(itens, etiqueta, silenciaOrigem);
}

/* FILA QUE SOBREVIVE AO RELOAD (Kakashi 29/10 16:35 [16:30]: ele deu Concluir com medo de perder a previa ao reinstalar o
 * plugin). A fila vai pro localStorage do painel a cada mudanca e volta ao abrir. Viaja SO dado puro: arquivo, etiqueta,
 * ajustes, trecho, origem, pilha, etapas e cRef (caminho + posicao do clipe original na timeline, pra re-achar ele no
 * Concluir). O objeto `c` do Premiere e as barras da onda nao viajam (nao sao serializaveis / sao refeitos). */
function salvaFila() {
  try {
    const puro = PREVIAS.map((p) => ({
      arquivo: p.arquivo, etiqueta: p.etiqueta, ajustes: p.ajustes, trecho: p.trecho, origem: p.origem,
      pilha: p.pilha, etapas: p.etapas, cRef: p.cRef,
    }));
    localStorage.setItem("dubx.previas.v1", JSON.stringify(puro));
  } catch (e) { /* sem localStorage: a fila so vive enquanto o painel vive, como antes */ }
}
function restauraFila() {
  let lidas = [];
  try { lidas = JSON.parse(localStorage.getItem("dubx.previas.v1") || "[]"); } catch (_) { lidas = []; }
  if (!Array.isArray(lidas)) return;
  lidas = lidas.filter((p) => p && p.arquivo);
  if (!lidas.length) return;
  lidas.forEach((p) => { p.c = null; PREVIAS.push(p); });
  desenhaPrevias();
  escreve(lidas.length + " prévia(s) da sessão anterior de volta na fila.", "ok");
}
function tiraDaFila(p) {
  const k = PREVIAS.indexOf(p);   // pela PROPRIA previa: o indice do desenho fica velho se a fila mudar durante o Concluir
  if (k >= 0) PREVIAS.splice(k, 1);
  desenhaPrevias();
}
/* o que basta pra re-achar o clipe original depois do reload: arquivo + inicio/fim na timeline (segundos) */
async function refDoClipe(c) {
  if (!c || !c.item || !c.caminho) return null;
  try {
    return { caminho: c.caminho, nome: c.nome, ini: (await c.item.getStartTime()).seconds,
             fim: (await c.item.getEndTime()).seconds };
  } catch (_) { return null; }
}
async function reacheClipe(ref) {
  try {
    const project = await ppro.Project.getActiveProject();
    const seq = project && await project.getActiveSequence();
    if (!seq || !ref) return null;
    const total = await seq.getAudioTrackCount();
    for (let i = 0; i < total; i++) {
      const tr = await seq.getAudioTrack(i);
      for (const it of await itensDaFaixa(tr)) {
        const a = (await it.getStartTime()).seconds, b = (await it.getEndTime()).seconds;
        if (Math.abs(a - ref.ini) > 0.02 || Math.abs(b - ref.fim) > 0.02) continue;
        try {
          const clip = await ppro.ClipProjectItem.cast(await it.getProjectItem());
          const cam = await clip.getMediaFilePath();
          if (String(cam).toLowerCase() === String(ref.caminho).toLowerCase())
            return { caminho: cam, nome: ref.nome, clip, item: it, marcado: false, elEstado: null };
        } catch (_) { /* item sem midia legivel: proximo */ }
      }
    }
  } catch (_) {}
  return null;
}
/* toda previa nova entra por aqui (troca, melhorar, dublar, stems do separar) */
function poeNaFila(p) {
  PREVIAS.push(p);
  desenhaPrevias();          // ja mostra a linha (e salva); a onda chega em seguida
  carregaOnda(p);
  if (p.c && !p.cRef) refDoClipe(p.c).then((r) => { if (r) { p.cRef = r; salvaFila(); } });
}

async function entregaResultado(arquivo, c, etiqueta, silenciaOrigem, extra) {
  if (previaLigada()) {
    const p = Object.assign({ arquivo, c, etiqueta }, extra || {});   // extra: ajustes/trecho/origem (pra ver e regerar)
    poeNaFila(p);
    return "na fila de prévias — ouça e clique Concluir.";
  }
  if (ENTREGA_LOTE) {
    ENTREGA_LOTE.push({ arquivo, c, etiqueta });
    return "pronto — vai pra timeline quando o lote terminar.";
  }
  const postos = await colocaNaTimeline([arquivo], c, etiqueta, silenciaOrigem);
  return postos ? "na timeline." : "não consegui pôr na timeline.";
}

/* SLIDER PROPRIO. O input real fica escondido e continua sendo a fonte da
 * verdade (.value + evento "input"), entao rotulo vivo, DOSE, ajustesTroca —
 * tudo segue lendo o mesmo lugar. Pedido dele em 23/08, depois do polegar
 * nativo se provar inestilizavel no UXP. */
let FX_ARRASTA = false;
document.addEventListener("mouseup", () => { FX_ARRASTA = false; });

function viraSlider(input) {
  if (!input || input.type !== "range" || input.dataset.comfx) return;
  input.dataset.comfx = "1";
  const min = Number(input.min || 0), max = Number(input.max || 100);
  const step = Number(input.step || 1);
  const trilho = document.createElement("div");
  trilho.className = "fxs";
  const fracAtual = () => (Number(input.value) - min) / (max - min);
  /* TRILHO CONTINUO (Kakashi 29/10 17:47 [00:00]: «já era pra ter ido pra zero e travou... não é pra ficar um monte de linhazinha
   * repartido, é pra ser infinito... tô segurando o mouse, boto pra cá, muda o de baixo»). As tres queixas vinham das 60 celulas +
   * um FX_ARRASTA global: o mouseover de OUTRO slider pegava o arrasto, as bordas das celulas viravam linhas, e a celula do botao
   * nao registrava o fim. Agora: um div so; o arrasto e DESTE slider (estado proprio), segue o mouse pelo deslocamento em pixels
   * desde o clique (getBoundingClientRect devolve 0 no UXP, por isso offsetX + deltas, nao posicao absoluta) e so solta no mouseup. */
  const barra = document.createElement("div"); barra.className = "fxt";
  const viva = document.createElement("div"); viva.className = "fxv";
  const knob = document.createElement("div"); knob.className = "fxk";
  barra.appendChild(viva); barra.appendChild(knob);
  trilho.appendChild(barra);
  const KNOB = 14;
  function pinta() {
    const f = Math.max(0, Math.min(1, fracAtual()));
    const w = barra.offsetWidth || 1;
    const x = Math.round(f * w);
    viva.style.width = x + "px";
    knob.style.left = x + "px";
  }
  function setaFrac(f) {
    f = Math.max(0, Math.min(1, f));
    let v = min + f * (max - min);
    v = Math.round(v / step) * step;
    v = Math.max(min, Math.min(max, v));
    if (Number(input.value) !== v) { input.value = v; dispara(input, "input"); dispara(input, "change"); }
    pinta();
  }
  let arr = null;   // {x0, f0}: arrasto DESTE slider
  const fracDoEvento = (ev) => {
    const w = barra.offsetWidth || 1;
    if (!arr) return Math.max(0, Math.min(1, (ev.offsetX !== undefined ? ev.offsetX : 0) / w));
    return arr.f0 + (ev.clientX - arr.x0) / w;
  };
  trilho.addEventListener("mousedown", (ev) => {
    if (ev.button !== 0 || input.disabled) return;   // disabled: ex. Forca do Sidon com «Sem» (29/10)
    ev.preventDefault();
    // onde clicou dentro da barra: offsetX e relativo ao alvo — converte pro trilho pelo offsetLeft da barra
    const alvoX = (ev.target === barra || barra.contains(ev.target)) ? ev.offsetX + (ev.target === barra ? 0 : (ev.target.offsetLeft || 0)) : ev.offsetX - (barra.offsetLeft || 0);
    const w = barra.offsetWidth || 1;
    const f = Math.max(0, Math.min(1, alvoX / w));
    arr = { x0: ev.clientX, f0: f };
    FX_ARRASTA = true;
    setaFrac(f);
  });
  const move = (ev) => { if (arr) setaFrac(fracDoEvento(ev)); };
  const solta = () => { arr = null; };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", solta);
  input.parentNode.insertBefore(trilho, input);
  input.style.display = "none";
  input.addEventListener("input", pinta);   // se algo setar por codigo
  /* NUMERO EDITAVEL NO ROTULO QUE JA EXISTE (Kakashi PC 29/10 17:36 [0:30]: «era só clicar naquele número ali que já tá...
   * sem criar card em volta, nem fundo preto horrível... ou rolar com o mouse»). O .d ao lado do slider vira clicavel: clique = digita
   * (Enter/sair aplica, Esc desiste); roda do mouse em cima = +-1 passo. E uma bolha com o valor em cima do botao ENQUANTO arrasta,
   * igual o slider do ElevenLabs que ele mostrou [2:30] («pode ficar assim o número, em cima, é bem melhor»). */
  const rot = input.parentNode.querySelector(".d");
  const aplicaValor = (v) => {
    if (!isFinite(v)) return;
    v = Math.round(v / step) * step; v = Math.max(min, Math.min(max, v));
    input.value = v; dispara(input, "input"); dispara(input, "change"); pinta();
  };
  if (rot && !rot.dataset.editavel) {
    rot.dataset.editavel = "1";
    rot.classList.add("dedit");
    rot.title = "clique pra digitar · roda do mouse ajusta";
    rot.addEventListener("wheel", (ev) => { if (input.disabled) return; ev.preventDefault(); aplicaValor(Number(input.value) + (ev.deltaY < 0 ? step : -step)); });
    rot.addEventListener("click", () => {
      if (input.disabled || rot.querySelector("input")) return;
      const antes = rot.textContent;
      const campo = document.createElement("input");
      campo.type = "text"; campo.className = "dcampo"; campo.value = String(input.value);
      rot.textContent = ""; rot.appendChild(campo); campo.focus(); campo.select();
      let fechado = false;
      const fecha = (aplica) => {
        if (fechado) return; fechado = true;
        if (aplica) aplicaValor(Number(String(campo.value).replace(",", "."))); else rot.textContent = antes;
        if (aplica) dispara(input, "input");   // o rotulo vivo reescreve o .d
      };
      campo.addEventListener("keydown", (ev) => { if (ev.key === "Enter") fecha(true); if (ev.key === "Escape") fecha(false); });
      campo.addEventListener("blur", () => fecha(true));
    });
  }
  const bolha = document.createElement("div");
  bolha.className = "fxbolha"; bolha.style.display = "none";
  trilho.appendChild(bolha);
  const mostraBolha = () => {
    if (!rot) return;
    bolha.textContent = rot.textContent; bolha.style.display = "";
    bolha.style.left = Math.round(fracAtual() * 100) + "%";
  };
  trilho.addEventListener("mousedown", () => { if (!input.disabled) mostraBolha(); });
  document.addEventListener("mousemove", () => { if (arr) mostraBolha(); });
  document.addEventListener("mouseup", () => { bolha.style.display = "none"; });
  pinta();
  // a largura so existe depois de desenhar: repinta no proximo quadro e quando a janela muda de tamanho
  setTimeout(pinta, 0);
  window.addEventListener("resize", pinta);
}

/* DROPDOWN PROPRIO (24/08): o pintor do UXP pinta o miolo de <select> de
 * PRETO ignorando background — moldura sozinha virava "card triplicado"
 * (prints dele DENTRO do Premiere). Caixa e lista sao DIVs; o <select>
 * real fica escondido DENTRO do wrapper como fonte de verdade: todo
 * codigo que le .value, escuta change ou troca as options continua
 * funcionando sem mexer em nada. A lista reconstroi ao ABRIR (options
 * mudam em tempo real, ex. listaVozes) e um relogio leve ressincroniza o
 * rotulo quando alguem seta .value por codigo sem disparar evento. */
/* clique fora (ou em outro dropdown) fecha as listinhas — 24/08 */
/* CAMPO FURANDO POPUP (print de 25/08: "nome da voz" por cima do menu).
 * No UXP o campo nativo e pintado numa camada acima de tudo — z-index nao
 * resolve. Enquanto um popover esta aberto, os campos de FORA dele ficam
 * invisiveis; a moldura continua, entao o layout nao mexe. */
let CAMPOS_SUMIDOS = [];
function _dentro(alvo, raiz) {
  for (let e = alvo; e; e = e.parentNode) if (e === raiz) return true;
  return false;
}
function escondeCamposFora(raiz) {
  mostraCamposDeNovo();
  const tags = ["input", "select", "textarea"];
  for (const tag of tags) {
    const lista = document.getElementsByTagName(tag);
    for (let i = 0; i < lista.length; i++) {
      const el = lista[i];
      if (raiz && _dentro(el, raiz)) continue;
      /* ja escondido por outro dono (o <select> do viraSelect, a caixinha
         de marcar): nao e meu, nao mexo e nao devolvo depois */
      if (el.style.display === "none") continue;
      CAMPOS_SUMIDOS.push(el);
      el.style.display = "none";
    }
  }
}
function mostraCamposDeNovo() {
  for (let i = 0; i < CAMPOS_SUMIDOS.length; i++) CAMPOS_SUMIDOS[i].style.display = "";
  CAMPOS_SUMIDOS = [];
}
window.desenhaVozesConfig = desenhaVozesConfig; // a engrenagem chama
window.escondeCamposFora = escondeCamposFora;   // o script do heroi usa
window.mostraCamposDeNovo = mostraCamposDeNovo;

const DROPDOWNS = [];
function fechaOutrosDropdowns(menos) {
  DROPDOWNS.forEach((d) => { if (d.lista !== menos) d.fecha(); });
}
document.addEventListener("click", () => fechaOutrosDropdowns(null));

const SELECTS_SINC = [];
setInterval(() => {
  for (let i = 0; i < SELECTS_SINC.length; i++) {
    try { SELECTS_SINC[i](); } catch (e) { /* select saiu do DOM */ }
  }
}, 700);

function viraSelect(s) {
  if (s.dataset.commoldura) return;
  s.dataset.commoldura = "1";
  const w = document.createElement("div");
  w.className = "selwrap";
  w.setAttribute("role", "button");
  w.tabIndex = 0;
  const rot = document.createElement("div"); rot.className = "selrot";
  const seta = document.createElement("div"); seta.className = "selseta";
  seta.textContent = "▾";
  const lista = document.createElement("div");
  lista.className = "sellista oculto";
  s.parentNode.insertBefore(w, s);
  w.appendChild(rot); w.appendChild(seta); w.appendChild(s); w.appendChild(lista);
  s.style.display = "none";

  const opcaoAtual = () => {
    // por VALUE: selectedIndex de select escondido devolvia -1 no UXP
    // (rotulo "—" nos prints de 24/08) — value e a fonte firme
    for (let k = 0; k < s.options.length; k++)
      if (s.options[k].value === s.value) return s.options[k];
    return s.options[s.selectedIndex] || s.options[0] || null;
  };
  const sincroniza = () => {
    const o = opcaoAtual();
    const rotNovo = o ? o.textContent : "—";
    if (rot.textContent !== rotNovo) rot.textContent = rotNovo;
    w.style.opacity = s.disabled ? "0.45" : "";
  };
  const fecha = () => {
    lista.className = "sellista oculto"; seta.textContent = "▾";
    mostraCamposDeNovo();
  };
  const abre = () => {
    while (lista.firstChild) lista.removeChild(lista.firstChild);
    /* EXPLICACAO NO REPOUSO (24/08): o title da <option> vira um rodape
     * de dica dentro da propria listinha — pra qualquer pessoa usar sem
     * conhecer os nomes (Bandit, Roformer, rmvpe…). */
    const dicaRodape = document.createElement("div");
    dicaRodape.className = "seldica";
    const oAtual = opcaoAtual();
    dicaRodape.textContent = (oAtual && oAtual.getAttribute("title")) || "";
    let temDica = false;
    for (let k = 0; k < s.options.length; k++) {
      const o = s.options[k];
      const it = document.createElement("div");
      it.className = "selitem" + (o === oAtual ? " atual" : "")
                               + (o.disabled ? " desligado" : "");
      it.setAttribute("role", "button");   // o espiao de clique passa a ver
      it.textContent = o.textContent;
      const exp = o.getAttribute("title") || "";
      if (exp) temDica = true;
      it.addEventListener("mouseover", () => {
        dicaRodape.textContent = exp;
        it.style.fontSize = "13px";
      });
      it.addEventListener("mouseout", () => { it.style.fontSize = ""; });
      if (!o.disabled) {
        const escolhe = ((val) => (ev) => {
          ev.stopPropagation();
          s.value = val;              // por value: firme mesmo escondido
          dispara(s, "change");
          sincroniza(); fecha();
        })(o.value);
        it.addEventListener("click", escolhe);
      }
      lista.appendChild(it);
    }
    if (temDica) lista.appendChild(dicaRodape);
    lista.className = "sellista"; seta.textContent = "▴";
    escondeCamposFora(lista);      // campo nativo fura popup no UXP
  };
  w.addEventListener("click", (ev) => {
    if (s.disabled) return;
    ev.stopPropagation();
    fechaOutrosDropdowns(lista);
    if (lista.className.indexOf("oculto") < 0) fecha(); else abre();
  });
  lista.addEventListener("click", (ev) => ev.stopPropagation());
  DROPDOWNS.push({ lista, fecha });
  s.addEventListener("change", sincroniza);
  // UM relogio global (antes era um setInterval POR select: cada
  // redesenho da pilha do Fluxo deixava timers orfaos)
  SELECTS_SINC.push(sincroniza);
  sincroniza();
}

function viraTodosSelects() {
  const sels = document.getElementsByTagName("select");
  for (let i = 0; i < sels.length; i++) viraSelect(sels[i]);
}

/* MOLDURA DOS CAMPOS DE TEXTO: mesma doenca — o DIV pinta a caixa e o
 * campo fica nu (transparent) por dentro. Digitacao intacta. */
/* chamada sempre que algo desenha controle DEPOIS do boot (pilha do
 * Fluxo, lista de clipes): sem isto aquele pedaco fica com controle
 * nativo cru — preto, quadrado, texto no canto. */
function vestirTudo() {
  try {
    peleTodosCheckbox(); viraTodosSliders();
    viraTodosSelects(); viraTodosCampos();
  } catch (e) {
    escreve("vestir controles: " + msgErro(e), "erro");
  }
}

function viraTodosCampos() {
  const alvos = [];
  const ins = document.getElementsByTagName("input");
  for (let i = 0; i < ins.length; i++)
    if (["checkbox", "radio", "range", "hidden", "button", "submit"].indexOf(ins[i].type) < 0) alvos.push(ins[i]);
  const tas = document.getElementsByTagName("textarea");
  for (let i = 0; i < tas.length; i++) alvos.push(tas[i]);
  alvos.forEach((el) => {
    if (el.dataset.commoldura) return;
    el.dataset.commoldura = "1";
    const w = document.createElement("div");
    w.className = "campow" + (el.tagName === "TEXTAREA" ? " campow-alto" : "")
                + (el.className.indexOf("num") >= 0 ? " campow-num" : "");
    if (el.style.width) { w.style.flex = "0 0 " + el.style.width; el.style.width = ""; }
    el.parentNode.insertBefore(w, el);
    w.appendChild(el);
    el.className = (el.className ? el.className + " " : "") + "campnu";
  });
}

function viraTodosSliders() {
  const ins = document.getElementsByTagName("input");
  for (let i = 0; i < ins.length; i++) {
    if (ins[i].type === "range") viraSlider(ins[i]);
  }
}

function peleTodosCheckbox() {
  const cxs = document.getElementsByTagName("input");
  for (let i = 0; i < cxs.length; i++) {
    if (cxs[i].type === "checkbox") peleCheckbox(cxs[i]);
  }
}
seguro(peleTodosCheckbox, "checkbox");
seguro(viraTodosSliders, "sliders");
seguro(viraTodosSelects, "dropdowns");
seguro(viraTodosCampos, "campos");
aoClicar(btTreinar, treinarVoz, "treinar voz");
elVozes.addEventListener("change", atualizaBotao);

/* PRESETS POR VOZ (29/10, Kakashi 16:35 [08:38] «Salva esse preset do pedaleiro aqui... Botar um preset aqui também» + chat
 * «quando clicar no pedaleira, coloca aquele preset como padrão»). Moram no servidor (presets.json) — sobrevivem ao reload
 * do painel. Escolher a voz aplica o preset dela nos sliders; o botao «salvar preset» grava os sliders atuais pra essa voz. */
let PRESETS = {};
async function carregaPresets() {
  try { PRESETS = (await (await fetch(DUBX + "/api/presets")).json()).presets || {}; } catch (_) {}
  try { pintaFavoritosEl(); } catch (_) {}
}
function setaCampo(el, v) {
  if (!el || v === undefined || v === null) return;
  if (el.type === "checkbox") { el.checked = !!v; dispara(el, "change"); return; }
  el.value = String(v); dispara(el, "input"); dispara(el, "change");
}
function aplicaPreset(voz) {
  const pr = PRESETS[voz];
  if (!pr) return false;
  aplicaPresetObjeto(pr);
  escreve("preset da voz \"" + voz + "\" aplicado" + (pr.nota ? " (" + pr.nota + ")" : "") + ".", "ok");
  return true;
}
function aplicaPresetObjeto(pr) {   // poe um conjunto de ajustes nos sliders (preset da voz ou parametros de uma previa)
  if (!pr) return;
  setaCampo(elVcMotor, pr.motor_vc);
  setaCampo(elVcSidon, pr.sidon);
  setaCampo(elVcPresenca, pr.presenca !== undefined ? Math.round(pr.presenca * 100) : undefined);
  setaCampo(elVcAtuacao, pr.atuacao !== undefined ? Math.round(pr.atuacao * 100) : undefined);
  setaCampo(elVcSemelhanca, pr.semelhanca !== undefined ? Math.round(pr.semelhanca * 100) : undefined);
  setaCampo(elVcTom, pr.tom);
  setaCampo(elVcF0, pr.f0);
  setaCampo(elVcPreservar, pr.preservar);
  setaCampo(elVcPuxarTom, pr.puxar_tom);
  if (pr.sidon_quando) setaSidonQuando(pr.sidon_quando);   // preset antigo sem o campo: fica o que esta na tela
  if (pr.motor_vc === "elevenlabs") {
    const pc = (v) => (v === undefined || v === null) ? undefined : Math.round(Number(v) * 100);
    if (pr.el_voice_id) escolheVozEl(pr.el_voice_id, pr.el_nome || pr.voz);
    setaCampo(elElEstab, pc(pr.el_estabilidade));
    setaCampo(elElSemel, pc(pr.el_semelhanca));
    setaCampo(elElEstilo, pc(pr.el_estilo));
    setaCampo(elElBoost, pr.el_speaker_boost);
    setaCampo(elElRuido, pr.el_remover_ruido);
  }
}
/* FAVORITOS DO ELEVENLABS (29/10, ele: «coloca como favorito... essa voz, em específico, coloca como Sargento Vinagre... bota entre
 * parênteses a voz original»). Favorito = preset com motor elevenlabs, guardado no presets.json com o nome que ele deu
 * («Sargento Vinagre (Declan - Horror, Narrator)»). Aparece com ★ no topo da lista do ElevenLabs E na lista «Voz» de cima
 * (escolher ali troca o motor pro ElevenLabs e aplica os ajustes). */
function favoritosEl() {
  return Object.keys(PRESETS).filter((k) => PRESETS[k] && PRESETS[k].motor_vc === "elevenlabs" && PRESETS[k].el_voice_id)
    .map((k) => ({ k, p: PRESETS[k] }));
}
function pintaFavoritosEl() {
  const favs = favoritosEl();
  if (elElVoz) {
    for (let n = favs.length - 1; n >= 0; n--) {
      const f = favs[n];
      let o = null;
      for (let i = 0; i < elElVoz.options.length; i++) if (elElVoz.options[i].value === f.p.el_voice_id) o = elElVoz.options[i];
      if (!o) { o = document.createElement("option"); o.value = f.p.el_voice_id; }
      o.textContent = "★ " + f.k;
      const ref = elElVoz.options.length > 1 ? elElVoz.options[1] : null;
      if (ref !== o) elElVoz.insertBefore(o, ref);
    }
  }
  if (elVozes) {
    for (let i = elVozes.options.length - 1; i >= 0; i--) if (elVozes.options[i].value.indexOf("el:") === 0) elVozes.removeChild(elVozes.options[i]);
    favs.forEach((f) => {
      const o = document.createElement("option");
      o.value = "el:" + f.k; o.textContent = "★ " + f.k + " — ElevenLabs";
      elVozes.appendChild(o);
    });
  }
}
function favoritoDaVozEl(id) {
  const f = favoritosEl().find((x) => x.p.el_voice_id === id);
  return f || null;
}
if (elElVoz) elElVoz.addEventListener("change", () => {
  const f = favoritoDaVozEl(elElVoz.value);
  if (f && !APLICANDO_FAV && !REFAZENDO_LISTA_EL) { APLICANDO_FAV = true; try { aplicaPresetObjeto(f.p); } finally { APLICANDO_FAV = false; }
    escreve("favorito \"" + f.k + "\" aplicado.", "ok"); }
});


async function favoritaVozEl() {
  if (!elElVoz || !elElVoz.value) { avisaFalta("escolha a voz do ElevenLabs primeiro."); return; }
  let original = "";
  for (let i = 0; i < elElVoz.options.length; i++) if (elElVoz.options[i].value === elElVoz.value) original = elElVoz.options[i].textContent;
  original = original.replace(/^★\s*/, "");
  const ja = favoritoDaVozEl(elElVoz.value);
  if (ja) original = (ja.p.el_nome || original);
  const ap = document.getElementById("elApelido");
  const apelido = ap && ap.value.trim();
  const chave = apelido ? apelido + " (" + original + ")" : (ja ? ja.k : original);
  const aj = ajustesTroca(chave);
  aj.el_nome = original;
  try {
    const r = await (await fetch(DUBX + "/api/preset", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voz: chave, ajustes: aj }) })).json();
    if (r.erro) { escreve("favorito: " + r.erro, "erro"); return; }
    if (ja && ja.k !== chave) delete PRESETS[ja.k];
    PRESETS[chave] = r.preset;
    pintaFavoritosEl();
    elElVoz.value = aj.el_voice_id; dispara(elElVoz, "change");
    if (ap) ap.value = "";
    escreve("★ favorito salvo: \"" + chave + "\" (com os ajustes de agora).", "ok");
  } catch (e) { escreve("favorito: " + msgErro(e), "erro"); }
}
elVozes.addEventListener("change", () => {
  const v = elVozes.value;
  if (!v) return;
  if (v.indexOf("el:") === 0) {
    const k = v.slice(3);
    elVozes.value = "";
    if (PRESETS[k]) { aplicaPresetObjeto(PRESETS[k]); escreve("favorito \"" + k + "\" aplicado (motor ElevenLabs).", "ok"); }
    return;
  }
  aplicaPreset(v);
});
async function salvaPreset() {
  const voz = elVozes.value;
  if (!voz) { avisaFalta("escolha a voz primeiro."); return; }
  try {
    const r = await (await fetch(DUBX + "/api/preset", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voz, ajustes: ajustesTroca(voz) }) })).json();
    if (r.erro) { escreve("preset: " + r.erro, "erro"); return; }
    PRESETS[voz] = r.preset;
    escreve("preset da voz \"" + voz + "\" salvo — escolher a voz já aplica ele.", "ok");
  } catch (e) { escreve("preset: " + msgErro(e), "erro"); }
}
seguro(function () {
  carregaPresets();
  const bt = document.getElementById("salvarPreset");
  if (bt) bt.addEventListener("click", salvaPreset);
  const bf = document.getElementById("favoritarEl");
  if (bf) bf.addEventListener("click", favoritaVozEl);
}, "presets");
seguro(listaAmbientes, "ambientes 2");
seguro(listaVozes, "vozes 2");

/* ------------------------------------------------------------- faxina */
/* Um fluxo de 4 etapas num video deixa GB de rastro (4,3 GB medidos em
 * 20/08). O servidor so apaga arquivo de PASSAGEM — original, resultados e
 * cache ficam. */
async function mostraLixo() {
  try {
    const j = await (await fetch(DUBX + "/api/lixo")).json();
    document.getElementById("lixoInfo").textContent =
      j.mb > 0 ? j.mb + " MB de arquivos de passagem" : "nada pra limpar";
  } catch (_) {
    document.getElementById("lixoInfo").textContent = "DUBX fora do ar";
  }
}
document.getElementById("limparLixo").addEventListener("click", async () => {
  try {
    const j = await (await fetch(DUBX + "/api/limpar-lixo", { method: "POST" })).json();
    escreve("faxina: " + j.mb + " MB liberados.", "ok");
    mostraLixo();
  } catch (e) { escreve(msgErro(e), "erro"); }
});
mostraLixo();

/* (o Analisar ja foi ligado no BOOT ESSENCIAL, la em cima) */
/* (o Melhorar ja foi ligado no BOOT ESSENCIAL) */

/* ------------------------------------------------------------- PITCH SYNC
 * (23/09/2026) Veio do Beat Match a pedido dele: "coloca o Pitch Sync no
 * Melhorar do DUBX — e pra audio e eu uso muito". Clipe com velocidade
 * diferente de 100% muda de TOM junto (mais rapido = mais agudo). Isto le a
 * velocidade de cada clipe de audio selecionado e poe o Deslocador de tom
 * NATIVO do Premiere com a compensacao exata: -12*log2(vel/100) semitons.
 * Sem render, sem arquivo novo, Ctrl+Z desfaz.
 * Licoes herdadas do Beat Match/QE (20/08): o nome do efeito e TRADUZIDO e
 * case-sensitive -> casar por palavra-chave na lista que o host devolve; em
 * pt-BR o unico parametro acessivel e "Transpor proporcao", uma RAZAO
 * normalizada 0..1 <-> 0,5..2,0 (+-12 st), nao semitons. Mecanismo de
 * aplicar = o mesmo do Ambiente por efeito (aplicarEfeitos): append em
 * transacao -> reler a cadeia -> o recem-anexado e o ULTIMO indice.
 * As funcoes entre PS-PURO-INICIO/FIM nao tocam DOM nem UXP: sao testadas
 * no node (scratchpad/teste_pitchsync.js). */
/*PS-PURO-INICIO*/
const PS_EFEITO_FORTE = ["pitch shifter", "pitch-shifter", "deslocador de tom",
                         "cambiador de tono", "changement de hauteur",
                         "tonhöhenverschiebung"];
const PS_EFEITO_PALAVRAS = ["pitch", "tom", "tono", "hauteur", "tonhöhe"];
const PS_PARAM_FORTE = ["semitone", "semi-ton", "semiton", "transpose",
                        "transposition", "transpor", "transposição",
                        "transposicao", "transposicion", "transposición", "pitch"];
const PS_PARAM_PALAVRAS = ["semi", "tom", "ton", "tono"];
const PS_RAZAO_MIN = 0.5;
const PS_RAZAO_MAX = 2.0;

/* casa por trecho forte OU por palavra inteira (evita "tom" dentro de "tomada") */
function psCasa(nome, fortes, palavras) {
  const n = String(nome || "").toLowerCase();
  if (!n) return false;
  for (let i = 0; i < fortes.length; i++) if (n.indexOf(fortes[i]) !== -1) return true;
  const toks = n.split(/[^a-z\u00c0-\u00ff0-9]+/).filter(Boolean);
  for (let j = 0; j < toks.length; j++) if (palavras.indexOf(toks[j]) !== -1) return true;
  return false;
}
/* o parametro guarda uma RAZAO normalizada (pt-BR "Transpor proporção") ou semitons? */
function psParamEhRazao(nome) {
  const n = String(nome || "").toLowerCase();
  if (n.indexOf("semi") !== -1 || n.indexOf("halbt") !== -1 || n.indexOf("demi-t") !== -1) return false;
  return n.indexOf("propor") !== -1 || n.indexOf("ratio") !== -1
      || n.indexOf("razão") !== -1 || n.indexOf("razao") !== -1
      || n.indexOf("transpo") !== -1;
}
/* getSpeed() pode vir como razao (1.0 = 100%) ou como porcentagem; normaliza pra % */
function psVelocidadePct(cru) {
  const v = Number(cru);
  if (!isFinite(v) || v <= 0) return null;
  return v < 10 ? v * 100 : v;   // velocidade real nunca fica abaixo de 10%
}
/* semitons que devolvem o tom original nesta velocidade (2 casas) */
function psSemitons(pct) {
  return Math.round(-12 * Math.log2(pct / 100) * 100) / 100;
}
/* o valor que se ESCREVE no parametro: razao normalizada ou semitons crus */
function psValorDoParam(nomeParam, st) {
  if (psParamEhRazao(nomeParam)) {
    const c = Math.max(-12, Math.min(12, st));
    const razao = Math.pow(2, c / 12);
    const norm = (razao - PS_RAZAO_MIN) / (PS_RAZAO_MAX - PS_RAZAO_MIN);
    return Math.max(0, Math.min(1, norm));
  }
  return st;
}
function psFmtSt(st) { return (st > 0 ? "+" : "") + st.toFixed(2).replace(".", ",") + " st"; }
function psFmtPct(pct) { return pct.toFixed(1).replace(".", ",") + "%"; }
/*PS-PURO-FIM*/

/* acha (ou anexa) o Deslocador de tom na cadeia do clipe e devolve o parametro de tom */
async function psEfeitoEParam(item, nomeEfeito, project) {
  let cadeia = await item.getComponentChain();
  let n = cadeia.getComponentCount();
  if (n && typeof n.then === "function") n = await n;
  let alvo = null;
  for (let i = 0; i < n; i++) {
    let c = cadeia.getComponentAtIndex(i);
    if (c && typeof c.then === "function") c = await c;
    let dn = "";
    try { dn = await c.getDisplayName(); } catch (_) { dn = ""; }
    if (psCasa(dn, PS_EFEITO_FORTE, PS_EFEITO_PALAVRAS)) { alvo = c; break; }
  }
  let novo = false;
  if (!alvo) {
    const comp = await ppro.AudioFilterFactory.createComponentByDisplayName(nomeEfeito, item);
    project.lockedAccess(() => {
      project.executeTransaction((ca) => {
        ca.addAction(cadeia.createAppendComponentAction(comp));
      }, "DUBX: Pitch Sync (" + nomeEfeito + ")");
    });
    /* o objeto da factory e opaco: rele a cadeia e pega o RECEM-anexado */
    cadeia = await item.getComponentChain();
    n = cadeia.getComponentCount();
    if (n && typeof n.then === "function") n = await n;
    alvo = cadeia.getComponentAtIndex(n - 1);
    if (alvo && typeof alvo.then === "function") alvo = await alvo;
    novo = true;
  }
  let total = alvo.getParamCount();
  if (total && typeof total.then === "function") total = await total;
  const nomes = [];
  let param = null;
  for (let j = 0; j < total; j++) {
    let p = alvo.getParam(j);
    if (p && typeof p.then === "function") p = await p;
    const pn = p ? p.displayName : "";
    nomes.push(pn);
    if (!param && psCasa(pn, PS_PARAM_FORTE, PS_PARAM_PALAVRAS)) param = p;
  }
  return { param, nomes, novo };
}

/* soLer=true: so mostra velocidade e compensacao no registro, nao mexe no clipe */
async function pitchSync(soLer) {
  if (RODANDO) { avisaFalta("o DUBX já está trabalhando — espere terminar."); return; }
  RODANDO = true; atualizaBotao();
  try {
    esconder();
    const { project, audio } = await itensDeAudioSelecionados();
    let nomeEfeito = null;
    if (!soLer) {
      const disponiveis = await ppro.AudioFilterFactory.getDisplayNames();
      for (let i = 0; i < disponiveis.length; i++) {
        if (psCasa(disponiveis[i], PS_EFEITO_FORTE, PS_EFEITO_PALAVRAS)) { nomeEfeito = disponiveis[i]; break; }
      }
      if (!nomeEfeito) {
        throw new Error("não achei o Deslocador de tom (Pitch Shifter) neste Premiere. "
                        + "Efeitos de áudio que ele tem: " + disponiveis.join(", "));
      }
    }
    escreve((soLer ? "lendo a velocidade de " : "Pitch Sync em ") + audio.length + " clipe(s)…");
    let aplicados = 0, pulados = 0, erros = 0;
    for (const item of audio) {
      let nome = "clipe";
      try {
        try { nome = (await item.getName()) || nome; } catch (_) { /* fica "clipe" */ }
        let cru = item.getSpeed ? item.getSpeed() : NaN;
        if (cru && typeof cru.then === "function") cru = await cru;
        const pct = psVelocidadePct(cru);
        if (pct === null) throw new Error("velocidade ilegível (" + cru + ")");
        const st = psSemitons(pct);
        if (Math.abs(st) < 0.005) {
          pulados++;
          escreve("  " + nome + ": 100% — nada a compensar.");
          continue;
        }
        if (soLer) {
          escreve("  " + nome + ": " + psFmtPct(pct) + " → " + psFmtSt(st));
          continue;
        }
        const r = await psEfeitoEParam(item, nomeEfeito, project);
        if (!r.param) {
          throw new Error("achei o efeito mas não o parâmetro de tom. Parâmetros dele: " + r.nomes.join(", "));
        }
        const valor = psValorDoParam(r.param.displayName, st);
        project.lockedAccess(() => {
          const kf = r.param.createKeyframe(valor);
          project.executeTransaction((ca) => {
            ca.addAction(r.param.createSetValueAction(kf, true));
          }, "DUBX: Pitch Sync " + psFmtSt(st));
        });
        aplicados++;
        escreve("  " + nome + ": " + psFmtPct(pct) + " → " + psFmtSt(st)
                + (r.novo ? " (efeito novo)" : " (efeito ajustado)"), "ok");
      } catch (e) {
        erros++;
        escreve("  " + nome + ": " + msgErro(e), "erro");
      }
    }
    const resumo = soLer
      ? "leitura pronta."
      : "Pitch Sync: " + aplicados + " aplicado(s), " + pulados + " já em 100%"
        + (erros ? ", " + erros + " com erro" : "") + ".";
    escreve(resumo, erros ? "erro" : "ok");
    if (erros) mostrar("erro", resumo);
    if (!soLer && aplicados) plim("ok");
  } finally {
    RODANDO = false; atualizaBotao();
  }
}
aoClicar(document.getElementById("pitchSync"), function () { return pitchSync(false); }, "pitch sync");
aoClicar(document.getElementById("psLer"), function () { return pitchSync(true); }, "ler velocidade");

/* ⭐ BOOT. Isto NUNCA existiu, e era o defeito mais caro do painel: os 8
 * botoes de acao nascem com `disabled` no HTML e so saem disso quando
 * `atualizaBotao()` roda. Como ninguem chamava no boot, eles ficavam
 * cinza e INCLICAVEIS desde a abertura — nao era estilo, era estado.
 * Junto com os 16 segmentados sem listener, e o "nada funciona" dele. */
atualizaBotao();
seguro(restauraFila, "fila de prévias");   // no fim: tudo que o desenho da fila usa ja existe
