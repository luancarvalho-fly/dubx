"""ClearerVoice (MossFormer2_SE_48K): LIMPA a voz (tira ruído/reverb) a 48 kHz — candidato a substituto/parceiro do
Sidon no Melhorar do DUBX (05/11, pesquisa: nenhum aberto bate o Sidon em reconstrução; este só limpa, mas ele quer
ouvir). Uso: rodar_clearervoice_se.py -i entrada.wav -o saida.wav
⚠️ o clearvoice le pelo pydub e quebra com ESPACO no caminho — a entrada vai pra uma pasta de trabalho sem espaco."""
import sys, os, json, argparse, warnings, shutil, glob, uuid
warnings.filterwarnings("ignore")
from pathlib import Path
AQUI = Path(__file__).parent
os.chdir(AQUI)                             # o clearvoice baixa checkpoints relativo ao cwd
from clearvoice import ClearVoice

ap = argparse.ArgumentParser()
ap.add_argument("-i", "--input", required=True)
ap.add_argument("-o", "--output", required=True)
a = ap.parse_args()

trab = AQUI / "trabalho" / ("se_" + uuid.uuid4().hex[:8]); trab.mkdir(parents=True, exist_ok=True)
try:
    ent = trab / "entrada.wav"
    shutil.copy2(a.input, ent)
    cv = ClearVoice(task="speech_enhancement", model_names=["MossFormer2_SE_48K"])
    sai = trab / "saida"
    cv(input_path=str(ent), online_write=True, output_path=str(sai))
    achados = glob.glob(str(sai / "**" / "*.wav"), recursive=True)
    if not achados:
        raise SystemExit("o ClearerVoice nao devolveu audio")
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(achados[0], a.output)
finally:
    shutil.rmtree(trab, ignore_errors=True)
print(json.dumps({"ok": True, "saida": a.output}), flush=True)
