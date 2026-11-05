# -*- coding: utf-8 -*-
"""
DUBX — CRIAR VOZ POR DESCRICAO (Qwen3-TTS VoiceDesign).

Pedido dele (19/08): "tem como a gente criar vozes como o eleven labs? via
prompt?". Tem: descreve a voz em texto ("homem adulto, voz grave e debochada")
e o modelo constroi o locutor. Serve pra nascer personagem novo sem gravar
ninguem — e a saida vira REFERENCIA pro RVC treinar em cima.

POR QUE ESTE MODELO, e nao os outros (levantado em 19/08):
  · **VibeVoice (Microsoft)** — MIT e livre, mas o card diz: treinado SO em
    ingles e chines, outros idiomas "podem sair ininteligiveis". Fora.
  · **Parler-TTS** — Apache 2.0, mas focado em ingles.
  · **Qwen3-TTS VoiceDesign** — Apache 2.0, NAO gated, e cobre PORTUGUES
    explicitamente (10 idiomas). Ganhou.

⚠️ FLASH-ATTN NAO E OBRIGATORIO. O exemplo oficial usa
`attn_implementation="flash_attention_2"`, que e Linux-only e ja nos barrou em
16/08 — mas e OPCAO, nao exigencia: com "sdpa" o modelo roda no Windows
(ele avisa "will only run the manual PyTorch version" e segue). NAO copiar o
exemplo do card sem trocar isso.

Tres modos, todos do mesmo modelo:
    --modo design   texto + descricao da voz  (criar voz do zero)
    --modo clone    texto + audio de referencia (clonar voz existente)
    --modo preset   texto + nome de locutor pronto + instrucao de emocao

Uso:
    .venv-qtts/Scripts/python.exe voz_prompt.py -t "fala" \\
        --instrucao "homem adulto, voz grave, debochada" -o saida.wav
"""
import argparse
import json
import sys
from pathlib import Path

MODELOS = {
    "design": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    "clone": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    "preset": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
}


def main():
    p = argparse.ArgumentParser(description="Cria voz por descricao (Qwen3-TTS)")
    p.add_argument("-t", "--texto", required=True, help="o que a voz vai falar")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--modo", default="design", choices=list(MODELOS))
    p.add_argument("--instrucao", default=None,
                   help="descricao da voz (design) ou da emocao (preset)")
    p.add_argument("--referencia", default=None, help="wav da voz a clonar")
    p.add_argument("--ref-texto", default=None, dest="ref_texto",
                   help="o que a referencia fala (ajuda a clonagem)")
    p.add_argument("--locutor", default=None, help="nome do locutor (modo preset)")
    p.add_argument("--idioma", default="Portuguese")
    p.add_argument("--modelo", default=None)
    a = p.parse_args()

    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    if a.modo == "design" and not a.instrucao:
        raise RuntimeError("no modo design a --instrucao descreve a voz "
                           "(ex.: 'homem adulto, voz grave, tom debochado')")
    if a.modo == "clone" and not a.referencia:
        raise RuntimeError("no modo clone falta a --referencia (wav da voz)")

    nome = a.modelo or MODELOS[a.modo]
    print(f"  carregando {nome}…", flush=True)
    modelo = Qwen3TTSModel.from_pretrained(
        nome,
        device_map="cuda:0" if torch.cuda.is_available() else "cpu",
        dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        # ⚠️ sdpa, NAO flash_attention_2 — ver o cabecalho deste arquivo
        attn_implementation="sdpa",
    )

    print(f"  gerando ({a.modo}, {a.idioma})…", flush=True)
    if a.modo == "design":
        wavs, sr = modelo.generate_voice_design(
            text=a.texto, instruct=a.instrucao, language=a.idioma)
    elif a.modo == "clone":
        wavs, sr = modelo.generate_voice_clone(
            text=a.texto, ref_audio=str(a.referencia),
            ref_text=a.ref_texto, language=a.idioma)
    else:
        wavs, sr = modelo.generate_custom_voice(
            text=a.texto, speaker=a.locutor or "Vivian",
            instruct=a.instrucao, language=a.idioma)

    destino = Path(a.output)
    destino.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(destino), wavs[0], sr)
    dur = len(wavs[0]) / sr
    print(f"  {dur:.1f}s a {sr} Hz", flush=True)
    print(json.dumps({"ok": True, "arquivo": str(destino), "taxa": sr,
                      "segundos": round(dur, 2), "modo": a.modo},
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
              file=sys.stderr)
        sys.exit(1)
