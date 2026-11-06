# DUBX

Melhora a voz de um áudio ou vídeo direto no seu PC — no aplicativo ou no painel do Premiere. Três motores no Melhorar:
**Sidon** (reconstrói a voz), **ClearerVoice** (só tira ruído e eco, mantém a voz original) e **ClearerVoice + Sidon**
(limpa e depois reconstrói). Escolha no campo **Motor**.

## Instalar (Windows + placa NVIDIA)

1. Baixe: botão verde **Code → Download ZIP** (ou `git clone`), e extraia numa pasta fixa (ex.: `C:\DUBX`).
2. Dois cliques em **INSTALAR.bat** e espere (na primeira vez baixa uns 6 GB: Sidon + ClearerVoice).
3. Abra o **DUBX** pelo atalho da área de trabalho.
4. No Premiere: feche e abra, depois **Janela → Extensões → DUBX**. O aplicativo DUBX precisa estar aberto.

## Usar o Melhorar

- Solte o áudio/vídeo e clique em **Melhorar**. O Sidon roda **uma vez**.
- Na grade de gerações, o cartão tem **uma faixa** e o slider **Reconstrução** embaixo: dê play e mexa enquanto toca.
  0% = só nivelado, 100% = Sidon puro. Soltou o slider, o arquivo é regravado nessa mistura.
- No Premiere: depois do Melhorar, mexa no slider **Dose** e solte — o áudio que entrou na timeline muda sem refazer o Sidon.

## Atualizar

Feche o DUBX. Baixe de novo o ZIP e extraia **por cima da mesma pasta** (ou `git pull`), e rode o **INSTALAR.bat**
outra vez: ele só completa o que falta (quem já tinha o Sidon baixa só o ClearerVoice).

Neste pacote só o **Melhorar** vem instalado. Trocar voz, Dublar e Separar usam outros motores que não estão aqui.
