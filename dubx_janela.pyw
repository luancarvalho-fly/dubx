# -*- coding: utf-8 -*-
"""Janela do DUBX na barra de tarefas (30/10/2026, P41).

Pedido dele (Kakashi 29/10 16:52, «com a caixa»): cada app na BARRA DE TAREFAS com ícone próprio + miniatura, igual o Kakashi.
Até aqui o servidor subia escondido (sem janela, sem ícone) e a janela chrome --app caía no grupo do Flyjutsu.

O que esta janela faz:
- AppUserModelID próprio («Luan.DUBX») ANTES de criar qualquer janela → não agrupa com o Python/Kakashi/Chrome; ícone = dubx.ico.
  Também grava na janela o comando de religar (DUBX.vbs): fixar na barra abre o DUBX certo, não o pythonw pelado.
- Sobe o servidor.py (porta 7788) se ele não estiver no ar, como processo SOLTO (sem janela, saída no servidor.log, fora do
  grupo desta janela) — fechar/matar a janela não derruba o servidor.
- Mostra o estado: porta no ar, tarefa rodando (com a mensagem), última prévia do histórico.
- Fechar (X) só MINIMIZA: fica na barra com miniatura; o servidor continua. Abrir de novo (atalho/abrir.ps1) traz ela pra frente.
Roda com o Python do sistema (tem tk), não com o .venv do motor — o servidor continua no Python do motor, igual ao abrir.ps1."""
import ctypes, ctypes.wintypes as wt, json, os, subprocess, sys, threading, time, urllib.request
import tkinter as tk
import tkinter.font as tkfont

AQUI = os.path.dirname(os.path.abspath(__file__))
PORTA = 7788
URL = "http://127.0.0.1:%d" % PORTA
ICO = os.path.join(AQUI, "dubx.ico")
PY_MOTOR = os.path.join(AQUI, "motor", ".venv", "Scripts", "python.exe")
TITULO = "DUBX"
AUMID = "Luan.DUBX"

# tokens do DUBX (ui\index.html :root)
BG, CARD, HAIR, ELEV = "#0e0e11", "#17171b", "#26262b", "#212127"
TEXTO, MUTED, HEADER, FAINT = "#ececf0", "#9a9aa3", "#8e8e97", "#6c6c76"
ACENTO, SOBRE, PERIGO = "#30d158", "#06230f", "#ff453a"

user32 = ctypes.windll.user32
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def log(*a):
    try:
        with open(os.path.join(AQUI, "janela.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%d/%m %H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
    except Exception:
        pass


# ---------- identidade na barra de tarefas ----------

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wt.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("p", ctypes.c_void_p), ("p2", ctypes.c_void_p)]


def _guid(s):
    g = GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
    return g


def identidade_da_janela(hwnd):
    """Propriedades da janela (IPropertyStore): ID + como religar quando ele fixar na barra. Sem isto, fixar a janela
    fixaria «pythonw.exe» sem argumento. Falhar aqui não é grave: o ID do processo já separa o grupo."""
    try:
        ctypes.windll.ole32.CoInitialize(None)
        ps = ctypes.c_void_p()
        iid = _guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
        hr = ctypes.windll.shell32.SHGetPropertyStoreForWindow(wt.HWND(hwnd), ctypes.byref(iid), ctypes.byref(ps))
        if hr != 0 or not ps:
            log("SHGetPropertyStoreForWindow hr=%#x" % (hr & 0xffffffff)); return
        vt = ctypes.cast(ps, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        SetValue = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                                      ctypes.POINTER(PROPVARIANT))(vt[6])
        Commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vt[7])
        Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])
        fmt = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
        vbs = os.path.join(AQUI, "DUBX.vbs")
        wscript = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe")
        guarda = []
        # a ordem importa: os de religar ANTES do ID (doc da Microsoft, PKEY_AppUserModel_RelaunchCommand)
        for pid, valor in ((2, '"%s" "%s"' % (wscript, vbs)), (4, TITULO), (3, ICO + ",0"), (5, AUMID)):
            buf = ctypes.create_unicode_buffer(valor); guarda.append(buf)
            pk = PROPERTYKEY(_guid(fmt), pid)
            pv = PROPVARIANT(); pv.vt = 31; pv.p = ctypes.cast(buf, ctypes.c_void_p)    # VT_LPWSTR
            hr = SetValue(ps, ctypes.byref(pk), ctypes.byref(pv))
            if hr != 0:
                log("SetValue pid=%d hr=%#x" % (pid, hr & 0xffffffff))
        Commit(ps); Release(ps)
    except Exception as e:
        log("identidade_da_janela:", repr(e))


# ---------- servidor ----------

def pega(rota, tempo=2):
    with urllib.request.urlopen(URL + rota, timeout=tempo) as r:
        return json.loads(r.read().decode("utf-8"))


def no_ar():
    try:
        pega("/api/vozes", 2); return True
    except Exception:
        return False


def sobe_servidor():
    """Mesmo jeito do abrir.ps1 (Python do motor, pasta do DUBX, saída no servidor.log), mas SOLTO desta janela."""
    if no_ar():
        return True
    py = PY_MOTOR if os.path.isfile(PY_MOTOR) else sys.executable.replace("pythonw.exe", "python.exe")
    arq = os.path.join(AQUI, "servidor.log")
    with open(arq, "w", encoding="utf-8") as f:
        f.write("=== inicio %s (pela janela) ===\n" % time.strftime("%d/%m %H:%M:%S"))
    saida = open(arq, "a", encoding="utf-8")
    amb = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    base = 0x08000000 | 0x00000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    for flags in (base | 0x01000000, base):  # + CREATE_BREAKAWAY_FROM_JOB (se o job deixar)
        try:
            subprocess.Popen([py, os.path.join(AQUI, "servidor.py")], cwd=AQUI, stdin=subprocess.DEVNULL,
                             stdout=saida, stderr=subprocess.STDOUT, creationflags=flags, env=amb, close_fds=True)
            log("servidor solto (flags %#x) com %s" % (flags, py))
            break
        except OSError as e:
            log("Popen flags %#x falhou: %r" % (flags, e))
    saida.close()
    for _ in range(40):
        if no_ar():
            return True
        time.sleep(0.5)
    return False


def abre_app():
    """A janela de aplicativo do DUBX (a mesma que o abrir.ps1 abre)."""
    navs = [os.path.expandvars(p) for p in (
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe")]
    nav = next((n for n in navs if os.path.isfile(n)), None)
    if not nav:
        os.startfile(URL); return
    perfil = os.path.join(AQUI, ".janela")
    os.makedirs(perfil, exist_ok=True)
    subprocess.Popen([nav, "--app=" + URL, "--user-data-dir=" + perfil, "--window-size=520,900",
                      "--window-position=140,60", "--no-first-run", "--no-default-browser-check",
                      "--disable-features=Translate,AutofillServerCommunication"], close_fds=True)


# ---------- desenho (cartão e botão arredondados, como o .cartao/.acao/.ghost do DUBX) ----------

def redondo(cv, x0, y0, x1, y1, r, **kw):
    p = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
         x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(p, smooth=True, **kw)


class Botao(tk.Canvas):
    def __init__(self, pai, texto, comando, tipo="acao", larg=180, alt=34, fonte=None, k=1.0):
        larg, alt = int(larg * k), int(alt * k)
        super().__init__(pai, width=larg, height=alt, bg=pai["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.tipo, self.comando, self.larg, self.alt, self.fonte, self.k = tipo, comando, larg, alt, fonte, k
        self.texto = texto
        self.bind("<Enter>", lambda e: self._pinta(True)); self.bind("<Leave>", lambda e: self._pinta(False))
        self.bind("<Button-1>", lambda e: self.comando())
        self._pinta(False)

    def _pinta(self, sobre):
        self.delete("all")
        w, h = self.larg, self.alt
        if self.tipo == "acao":
            redondo(self, 1, 1, w - 1, h - 1, 10 * self.k, fill="#3ad962" if sobre else ACENTO, outline="")
            cor = SOBRE
        else:
            redondo(self, 1, 1, w - 1, h - 1, 8 * self.k, fill="#1d1d22" if sobre else self["bg"],
                    outline="#3a3a40" if sobre else HAIR)
            cor = TEXTO if sobre else MUTED
        self.create_text(w // 2, h // 2, text=self.texto, fill=cor, font=self.fonte)


# ---------- a janela ----------

class Janela:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()        # só aparece depois de ganhar o ID e o ícone (senão a barra cria o botão no grupo errado)
        self.root.title(TITULO)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.k = max(1.0, self.root.winfo_fpixels("1i") / 96.0)     # escala da tela (a janela é DPI-aware)
        try:
            self.root.iconbitmap(default=ICO)
        except Exception as e:
            log("iconbitmap:", repr(e))
        fams = set(tkfont.families(self.root))
        fam = next((f for f in ("SF Pro Text", "Segoe UI Variable Text", "Segoe UI") if f in fams), "TkDefaultFont")
        self.f_marca = (fam, 20, "bold")
        self.f_sub = (fam, 9)
        self.f_rot = (fam, 8, "bold")
        self.f_txt = (fam, 10)
        self.f_bt = (fam, 9, "bold")
        self.estado = {"ar": None, "rodando": [], "ultima": None, "subindo": False}
        self.trava = threading.Lock()
        self._monta()
        self.root.protocol("WM_DELETE_WINDOW", self.fechar)
        self.root.update_idletasks()
        hw = user32.GetParent(self.root.winfo_id())
        try:    # barra de título escura (Windows 11), igual ao Kakashi
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hw, 20, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass
        identidade_da_janela(hw)
        self.root.geometry("+%d+%d" % (int(680 * self.k), int(60 * self.k)))
        self.root.deiconify()
        threading.Thread(target=self._ciclo, daemon=True).start()
        self.root.after(300, self._atualiza)

    def _monta(self):
        k = self.k
        q = lambda v: int(v * k)
        app = tk.Frame(self.root, bg=BG); app.pack(padx=q(18), pady=(q(16), q(16)))
        topo = tk.Frame(app, bg=BG); topo.pack(fill="x", pady=(0, q(12)))
        try:
            self.img = tk.PhotoImage(file=os.path.join(AQUI, "dubx_64.png"))
            if k < 1.75:
                self.img = self.img.subsample(2)
            tk.Label(topo, image=self.img, bg=BG, bd=0).pack(side="left", padx=(0, q(10)))
        except Exception:
            pass
        marca = tk.Frame(topo, bg=BG); marca.pack(side="left")
        tk.Label(marca, text="DUBX", font=self.f_marca, fg=TEXTO, bg=BG, bd=0).pack(anchor="w")
        tk.Label(marca, text="servidor de voz · porta %d" % PORTA, font=self.f_sub, fg=HEADER, bg=BG, bd=0).pack(anchor="w")

        # .cartao: fundo do cartão + fio de 1 px + canto 14; a altura acompanha o conteúdo (nome comprido quebra linha)
        L = q(330)
        cv = tk.Canvas(app, width=L, height=q(150), bg=BG, highlightthickness=0, bd=0); cv.pack()
        dentro = tk.Frame(cv, bg=CARD); cv.create_window(q(15), q(13), window=dentro, anchor="nw", width=L - q(30))

        def redesenha(_e=None):
            A = dentro.winfo_reqheight() + q(13) + q(4)
            cv.configure(height=A)
            cv.delete("fundo")
            redondo(cv, 1, 1, L - 1, A - 1, q(14), fill=CARD, outline=HAIR, tags="fundo")
            cv.tag_lower("fundo")
        dentro.bind("<Configure>", redesenha)

        def linha(rotulo):
            tk.Label(dentro, text=rotulo, font=self.f_rot, fg=MUTED, bg=CARD, bd=0).pack(anchor="w", pady=(0, q(2)))
            f = tk.Frame(dentro, bg=CARD); f.pack(fill="x", pady=(0, q(9)))
            return f
        f = linha("SERVIDOR")
        self.ponto = tk.Canvas(f, width=q(10), height=q(10), bg=CARD, highlightthickness=0, bd=0)
        self.ponto.pack(side="left", padx=(0, q(7)))
        self.bola = self.ponto.create_oval(1, 1, q(9), q(9), fill=FAINT, outline="")
        self.lb_ar = tk.Label(f, text="conferindo…", font=self.f_txt, fg=TEXTO, bg=CARD, bd=0); self.lb_ar.pack(side="left")
        f = linha("TAREFA")
        self.lb_tar = tk.Label(f, text="—", font=self.f_txt, fg=TEXTO, bg=CARD, bd=0, justify="left",
                               wraplength=L - q(34), anchor="w")
        self.lb_tar.pack(fill="x")
        f = linha("ÚLTIMA PRÉVIA")
        self.lb_ult = tk.Label(f, text="—", font=self.f_txt, fg=TEXTO, bg=CARD, bd=0, justify="left",
                               wraplength=L - q(34), anchor="w")
        self.lb_ult.pack(fill="x")

        bts = tk.Frame(app, bg=BG); bts.pack(fill="x", pady=(q(12), 0))
        Botao(bts, "Abrir o DUBX", abre_app, "acao", 200, 34, self.f_bt, k).pack(side="left")
        self.bt_ligar = Botao(bts, "Ligar servidor", self.ligar, "ghost", 120, 34, self.f_bt, k)
        tk.Label(app, text="fechar só minimiza — o servidor continua no ar", font=self.f_sub, fg=FAINT, bg=BG,
                 bd=0).pack(anchor="w", pady=(q(10), 0))

    def fechar(self):
        self.root.iconify()     # fica na barra (com miniatura); o servidor não depende desta janela

    def ligar(self):
        if self.estado["subindo"]:
            return
        self.estado["subindo"] = True
        threading.Thread(target=lambda: (sobe_servidor(), self.estado.update(subindo=False)), daemon=True).start()

    def _ciclo(self):
        if not no_ar():
            self.estado["subindo"] = True
            ok = sobe_servidor()
            self.estado["subindo"] = False
            if not ok:
                log("o servidor não subiu (ver servidor.log)")
        ult_hist = 0
        while True:
            ar = no_ar()
            rod = []
            if ar:
                try:
                    tar = pega("/api/tarefas").get("tarefas", {})
                    for t, est in tar.items():
                        if est == "rodando":
                            try:
                                msg = pega("/api/estado?t=" + t).get("mensagem", "")
                            except Exception:
                                msg = ""
                            rod.append((t, msg))
                except Exception:
                    pass
                if time.time() - ult_hist > 10:
                    try:
                        h = pega("/api/historico", 5).get("historico", [])
                        it = next((i for i in h if not i.get("oculta")), None)
                        self.estado["ultima"] = it
                        ult_hist = time.time()
                    except Exception:
                        pass
            self.estado["ar"], self.estado["rodando"] = ar, rod
            time.sleep(2)

    def _atualiza(self):
        e = self.estado
        if e["ar"] is None or (e["subindo"] and not e["ar"]):
            cor, txt = FAINT, "ligando…" if e["subindo"] else "conferindo…"
        elif e["ar"]:
            cor, txt = ACENTO, "no ar · %s" % URL.replace("http://", "")
        else:
            cor, txt = PERIGO, "fora do ar"
        self.ponto.itemconfigure(self.bola, fill=cor)
        self.lb_ar.configure(text=txt)
        if e["ar"] is False and not e["subindo"]:
            if not self.bt_ligar.winfo_ismapped():
                self.bt_ligar.pack(side="left", padx=(int(10 * self.k), 0))
        elif self.bt_ligar.winfo_ismapped():
            self.bt_ligar.pack_forget()
        rod = e["rodando"]
        if not e["ar"]:
            self.lb_tar.configure(text="—", fg=MUTED)
        elif not rod:
            self.lb_tar.configure(text="nenhuma rodando", fg=MUTED)
        else:
            self.lb_tar.configure(text="  ·  ".join(m or "rodando" for _, m in rod[:2]) +
                                  ("  (+%d)" % (len(rod) - 2) if len(rod) > 2 else ""), fg=ACENTO)
        u = e["ultima"]
        if u:
            self.lb_ult.configure(text="%s\n%s%s" % (u.get("rotulo") or u.get("amigavel") or u.get("id"), u.get("quando", ""),
                                                     (" · " + u["modelo"]) if u.get("modelo") else ""), fg=TEXTO)
        titulo = TITULO + (" · trabalhando" if rod else ("" if e["ar"] is not False else " · fora do ar"))
        if self.root.title() != titulo:
            self.root.title(titulo)
        self.root.after(1000, self._atualiza)


def traz_a_que_ja_existe():
    for tit in (TITULO, TITULO + " · trabalhando", TITULO + " · fora do ar"):
        hw = user32.FindWindowW("TkTopLevel", tit)
        if hw:
            user32.ShowWindow(hw, 9)            # SW_RESTORE
            user32.SetForegroundWindow(hw)
            return True
    return False


def main():
    # ANTES de qualquer janela: é o que separa o DUBX do grupo do Python na barra
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(AUMID))
    except Exception as e:
        log("AUMID:", repr(e))
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    kernel32.CreateMutexW(None, False, "Local\\DubxJanelaLuan")
    if ctypes.get_last_error() == 183:          # já tem uma aberta: só traz ela pra frente
        traz_a_que_ja_existe()
        return
    log("janela aberta")
    Janela().root.mainloop()


if __name__ == "__main__":
    main()
