"""
Vistas de Telemetría (.ibt) e Ingeniero en Vivo para la interfaz principal
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from app.ibt_reader import telemetry_dir
from app.telemetry_analyzer import TelemetryAnalyzer, group_session_files, fmt_lap, KMH

PRIORITY_LABEL = {1: "ALTA", 2: "MEDIA", 3: "INFO"}
DEFAULT_VOICE = "es-CL-CatalinaNeural"
VOICES = [DEFAULT_VOICE, "es-CL-LorenzoNeural", "es-AR-TomasNeural", "es-AR-ElenaNeural",
          "es-MX-JorgeNeural", "es-MX-DaliaNeural", "es-ES-AlvaroNeural", "es-CO-GonzaloNeural",
          "es-US-AlonsoNeural"]


def default_telemetry_dir() -> str:
    path = telemetry_dir()
    return path if os.path.isdir(path) else os.path.expanduser("~")


class TelemetryView:
    """Pantalla de análisis de telemetría"""

    def __init__(self, app):
        self.app = app
        self.c = app.colors
        self.result = None

    def show(self):
        app, c = self.app, self.c
        app.clear_content()
        main = tk.Frame(app.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        app.create_section_title(main, "Telemetría y Setup", "🔧")

        bar = tk.Frame(main, bg=c['bg_dark'])
        bar.pack(fill=tk.X, pady=(0, 15))
        btn = tk.Button(bar, text="📂 Cargar .ibt", command=lambda: self.load(main),
                        bg=c['accent_blue'], fg=c['bg_dark'], font=('Segoe UI', 11, 'bold'),
                        relief=tk.FLAT, cursor='hand2', padx=20, pady=8)
        btn.pack(side=tk.LEFT)
        tk.Label(bar, text="Los archivos de la misma sesión se combinan automáticamente. "
                           "Graba en iRacing con Alt+L.",
                 bg=c['bg_dark'], fg=c['text_muted'], font=('Segoe UI', 10)).pack(side=tk.LEFT, padx=15)

        self.body = tk.Frame(main, bg=c['bg_dark'])
        self.body.pack(fill=tk.BOTH, expand=True)
        if self.result:
            self.render(self.result)

    def load(self, main):
        path = filedialog.askopenfilename(
            title="Seleccionar telemetría de iRacing",
            filetypes=[("Telemetría iRacing", "*.ibt"), ("Todos", "*.*")],
            initialdir=default_telemetry_dir())
        if not path:
            return
        for w in self.body.winfo_children():
            w.destroy()
        status = tk.Label(self.body, text="⏳ Analizando telemetría...", bg=self.c['bg_dark'],
                          fg=self.c['accent_yellow'], font=('Segoe UI', 12))
        status.pack(pady=30)

        def work():
            try:
                files = group_session_files(path)
                res = TelemetryAnalyzer(files).analyze()
                self.app.root.after(0, lambda: self._done(res, files))
            except Exception as e:
                err = str(e)
                self.app.root.after(0, lambda: messagebox.showerror("Error", f"No se pudo analizar:\n{err}"))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, res, files):
        self.result = res
        self.app.loaded_file.set(f"🔧 {os.path.basename(files[-1])} ({len(files)} archivo/s)")
        for w in self.body.winfo_children():
            w.destroy()
        self.render(res)

    # ------------------------------------------------------------ render
    def render(self, res):
        app, c = self.app, self.c
        body = self.body
        tk.Label(body, text=f"{res.car}  •  {res.track}  •  {res.session_type}  •  "
                            f"Setup {'fijo' if res.fixed_setup else 'abierto'}",
                 bg=c['bg_dark'], fg=c['text_secondary'], font=('Segoe UI', 11)).pack(anchor=tk.W, pady=(0, 10))

        cards = tk.Frame(body, bg=c['bg_dark'])
        cards.pack(fill=tk.X, pady=5)
        valid = [l for l in res.laps if l.valid]
        offs = sum(cr.offtracks for cr in res.corners)
        for title, value, sub, color in (
            ("MEJOR VUELTA", fmt_lap(res.best_lap.time) if res.best_lap else "--",
             f"Vuelta {res.best_lap.number}" if res.best_lap else "", c['accent_green']),
            ("VUELTA TEÓRICA", fmt_lap(res.theoretical_best),
             f"{(res.best_lap.time - res.theoretical_best):.2f} s por ganar" if res.best_lap else "", c['accent_purple']),
            ("VUELTAS VÁLIDAS", str(len(valid)), f"de {len(res.laps)} completas", c['accent_blue']),
            ("COMBUSTIBLE", f"{res.fuel_per_lap:.2f} L", "por vuelta", c['accent_orange']),
            ("SALIDAS DE PISTA", str(offs), "en vueltas válidas", c['accent_red'] if offs else c['accent_green']),
        ):
            app.create_stat_card(cards, title, value, sub, color).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        self._recommendations(body, res)
        if res.dist is not None and res.traces:
            self._trace_chart(body, res)
        if res.corners:
            self._corner_table(body, res)
            self._loss_chart(body, res)
        if res.limit_zones:
            self._limits(body, res)
        if res.tires:
            self._tires(body, res)
        self._laps(body, res)

    def _limits(self, body, res):
        c = self.c
        f = self._frame(body, "Límites de pista (margen al límite en cada vuelta, medido con GPS)")
        tk.Label(f, text="Positivo = te sobró espacio · negativo = te pasaste · km/h = velocidad de salida",
                 bg=c['bg_card'], fg=c['text_muted'], font=('Segoe UI', 8, 'italic')).pack(anchor=tk.W)
        for z in res.limit_zones:
            row = tk.Frame(f, bg=c['bg_card'])
            row.pack(fill=tk.X, pady=4)
            side = "izq." if z["side"] > 0 else "der."
            tk.Label(row, text=f"{z['name'].capitalize()}  ({z['s']:.0f} m, lado {side})", bg=c['bg_card'],
                     fg=c['accent_cyan'], font=('Segoe UI', 10, 'bold'), width=34, anchor=tk.W).pack(side=tk.LEFT)
            for lap in z["laps"]:
                off = lap["off"]
                col = c['accent_red'] if off else c['accent_green'] if lap["margin"] < 0.3 else c['text_primary']
                tk.Label(row, text=f"V{lap['lap']}  {lap['margin'] * 100:+.0f} cm\n{lap['exit_kmh']:.0f} km/h",
                         bg=c['bg_secondary'], fg=col, font=('Consolas', 9), padx=6, pady=2).pack(side=tk.LEFT, padx=2)

    def _frame(self, parent, title):
        c = self.c
        f = tk.LabelFrame(parent, text=f" {title} ", bg=c['bg_card'], fg=c['accent_blue'],
                          font=('Segoe UI', 11, 'bold'), padx=15, pady=15)
        f.pack(fill=tk.X, pady=10)
        return f

    def _recommendations(self, body, res):
        c = self.c
        f = self._frame(body, "Recomendaciones del ingeniero")
        colors = {1: c['accent_red'], 2: c['accent_yellow'], 3: c['accent_cyan']}
        for r in res.recommendations:
            row = tk.Frame(f, bg=c['bg_card'])
            row.pack(fill=tk.X, pady=4)
            tk.Label(row, text=f"{PRIORITY_LABEL[r.priority]}", bg=colors[r.priority], fg=c['bg_dark'],
                     font=('Segoe UI', 8, 'bold'), width=6).pack(side=tk.LEFT, anchor=tk.N, padx=(0, 10))
            text = tk.Frame(row, bg=c['bg_card'])
            text.pack(side=tk.LEFT, fill=tk.X, expand=True)
            tk.Label(text, text=f"{r.category}: {r.title}", bg=c['bg_card'], fg=c['text_primary'],
                     font=('Segoe UI', 10, 'bold'), anchor=tk.W, justify=tk.LEFT).pack(anchor=tk.W)
            tk.Label(text, text=r.detail, bg=c['bg_card'], fg=c['text_secondary'], font=('Segoe UI', 9),
                     anchor=tk.W, justify=tk.LEFT, wraplength=850).pack(anchor=tk.W)

    def _figure(self, parent, height=3.6):
        fig = Figure(figsize=(9, height), dpi=100)
        fig.patch.set_facecolor(self.c['bg_card'])
        return fig

    def _style(self, ax):
        c = self.c
        ax.set_facecolor(c['bg_card'])
        ax.tick_params(colors=c['text_secondary'], labelsize=8)
        for s in ax.spines.values():
            s.set_color(c['border'])
        ax.grid(True, color=c['border'], alpha=0.6, linewidth=0.5)
        ax.yaxis.label.set_color(c['text_secondary'])
        ax.xaxis.label.set_color(c['text_secondary'])

    def _embed(self, frame, fig):
        canvas = FigureCanvasTkAgg(fig, master=frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.app.active_canvases.append(canvas)

    def _trace_chart(self, body, res):
        c = self.c
        f = self._frame(body, "Velocidad, acelerador y freno (mejor vuelta vs vuelta típica)")
        fig = self._figure(f, 5.0)
        ax1 = fig.add_subplot(211)
        ax2 = fig.add_subplot(212, sharex=ax1)
        palette = [c['accent_green'], c['accent_orange']]
        for (name, tr), col in zip(res.traces.items(), palette):
            ax1.plot(res.dist, tr["Speed"] * KMH, color=col, linewidth=1, label=name)
            ax2.plot(res.dist, tr["Throttle"] * 100, color=col, linewidth=0.8)
            ax2.plot(res.dist, -tr["Brake"] * 100, color=col, linewidth=0.8, linestyle="--")
        for cr in res.corners:
            ax1.axvline(cr.apex_m, color=c['border'], linewidth=0.8)
            ax1.text(cr.apex_m, ax1.get_ylim()[1] if False else 5, f"C{cr.number}",
                     color=c['text_muted'], fontsize=7, ha='center')
        ax1.set_ylabel("km/h")
        ax2.set_ylabel("acel. % / freno %")
        ax2.set_xlabel("distancia (m)")
        leg = ax1.legend(loc="lower right", fontsize=8, facecolor=c['bg_card'], edgecolor=c['border'])
        for t in leg.get_texts():
            t.set_color(c['text_secondary'])
        for ax in (ax1, ax2):
            self._style(ax)
        fig.tight_layout()
        self._embed(f, fig)

    def _loss_chart(self, body, res):
        c = self.c
        f = self._frame(body, "Tiempo perdido por curva (promedio vs tu mejor paso)")
        fig = self._figure(f, 2.8)
        ax = fig.add_subplot(111)
        names = [f"C{cr.number}" for cr in res.corners]
        losses = [cr.time_loss_avg for cr in res.corners]
        top = max(losses) if losses else 0
        ax.bar(names, losses, color=[c['accent_red'] if l == top else c['accent_blue'] for l in losses])
        ax.set_ylabel("segundos")
        self._style(ax)
        fig.tight_layout()
        self._embed(f, fig)

    def _corner_table(self, body, res):
        c = self.c
        f = self._frame(body, "Curva por curva")
        headers = ["Curva", "Metro", "Tipo", "Marcha", "V. mín mejor/prom", "Salida mejor/prom",
                   "Frenada (m antes)", "Frena en (mejor)", "A fondo (m después)", "Pérdida", "Subviraje",
                   "Patina", "Bloqueo", "ABS", "Salidas"]
        for j, h in enumerate(headers):
            tk.Label(f, text=h, bg=c['bg_card'], fg=c['text_muted'], font=('Segoe UI', 8, 'bold')).grid(
                row=0, column=j, padx=4, pady=2, sticky=tk.W)
        worst = max((cr.time_loss_avg for cr in res.corners), default=0)
        for i, cr in enumerate(res.corners, start=1):
            brake = (f"{cr.brake_m_best:.0f}/{cr.brake_m_avg:.0f}"
                     if cr.brake_m_best is not None and cr.brake_m_avg is not None else "-")
            under_col = (c['accent_orange'] if cr.understeer_deg > 1.2 else
                         c['accent_purple'] if cr.understeer_deg < -0.8 else c['text_primary'])
            cells = [
                (f"C{cr.number}", c['accent_blue']), (f"{cr.apex_m:.0f}", c['text_secondary']),
                (cr.kind, c['text_secondary']), (str(cr.gear), c['text_secondary']),
                (f"{cr.min_speed_best:.0f}/{cr.min_speed_avg:.0f}", c['text_primary']),
                (f"{cr.exit_speed_best:.0f}/{cr.exit_speed_avg:.0f}", c['text_primary']),
                (brake, c['text_primary']),
                (f"metro {cr.brake_at_m:.0f}" if cr.brake_at_m is not None else "-", c['accent_cyan']),
                (f"{cr.throttle_m_best:.0f}/{cr.throttle_m_avg:.0f}"
                 if cr.throttle_m_best is not None and cr.throttle_m_avg is not None else "-", c['text_primary']),
                (f"{cr.time_loss_avg:.2f}s", c['accent_red'] if cr.time_loss_avg == worst else c['text_primary']),
                (f"{cr.understeer_deg:+.1f}°", under_col),
                (f"{cr.wheelspin:.0f}%", c['accent_orange'] if cr.wheelspin > 12 else c['text_primary']),
                (f"{cr.lockups:.0f}%", c['accent_orange'] if cr.lockups > 10 else c['text_primary']),
                (f"{cr.abs_pct:.0f}%", c['accent_orange'] if cr.abs_pct > 40 else c['text_primary']),
                (str(cr.offtracks), c['accent_red'] if cr.offtracks else c['text_primary']),
            ]
            for j, (txt, col) in enumerate(cells):
                tk.Label(f, text=txt, bg=c['bg_card'], fg=col, font=('Segoe UI', 9)).grid(
                    row=i, column=j, padx=4, pady=1, sticky=tk.W)
        tk.Label(f, text="Subviraje: + = el auto gira menos de lo que pides (subviraje), − = rota de más (sobreviraje).",
                 bg=c['bg_card'], fg=c['text_muted'], font=('Segoe UI', 8, 'italic')).grid(
            row=len(res.corners) + 1, column=0, columnspan=len(headers), sticky=tk.W, pady=(8, 0))

    def _tires(self, body, res):
        c = self.c
        f = self._frame(body, "Neumáticos (temperatura en curva, °C)")
        layout = {"LF": (0, 0), "RF": (0, 1), "LR": (1, 0), "RR": (1, 1)}
        for key, (r, col) in layout.items():
            t = res.tires.get(key)
            if not t:
                continue
            box = tk.Frame(f, bg=c['bg_secondary'], padx=12, pady=8)
            box.grid(row=r, column=col, padx=8, pady=6, sticky="nsew")
            tk.Label(box, text=t['name'], bg=c['bg_secondary'], fg=c['accent_cyan'],
                     font=('Segoe UI', 10, 'bold')).pack(anchor=tk.W)
            # Mostrar de izquierda a derecha como se ve el auto desde atrás
            order = [("Ext", t['outer']), ("Centro", t['middle']), ("Int", t['inner'])]
            if key[0] == "R":
                order.reverse()
            tk.Label(box, text="   ".join(f"{n} {v:.0f}" for n, v in order), bg=c['bg_secondary'],
                     fg=c['text_primary'], font=('Consolas', 11)).pack(anchor=tk.W)
            tk.Label(box, text=f"{t['pressure']:.0f} kPa en caliente  •  desgaste {t['wear']:.0f}%",
                     bg=c['bg_secondary'], fg=c['text_muted'], font=('Segoe UI', 9)).pack(anchor=tk.W)
        f.grid_columnconfigure(0, weight=1)
        f.grid_columnconfigure(1, weight=1)

    def _laps(self, body, res):
        c = self.c
        f = self._frame(body, "Vueltas")
        for l in res.laps:
            notes = [n for n, cond in (("pits", l.pit), ("fuera de pista", l.offtrack),
                                       (f"{l.incidents}x", l.incidents)) if cond]
            best = res.best_lap is l
            color = c['accent_green'] if best else c['text_primary'] if l.valid else c['text_muted']
            txt = f"V{l.number:<3}  {fmt_lap(l.time)}   {l.fuel_used:5.2f} L"
            if notes:
                txt += "   (" + ", ".join(notes) + ")"
            if best:
                txt += "   ← mejor"
            tk.Label(f, text=txt, bg=c['bg_card'], fg=color, font=('Consolas', 10)).pack(anchor=tk.W)


class LiveEngineerView:
    """Encender/apagar el ingeniero en vivo desde la app y ver la radio"""

    def __init__(self, app):
        self.app = app
        self.c = app.colors
        self.engineer = None
        self.thread = None
        self.messages = []
        self.log_widget = None
        self.ptt = None

    def show(self):
        app, c = self.app, self.c
        app.clear_content()
        main = tk.Frame(app.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        app.create_section_title(main, "Ingeniero en Vivo", "🎙️")
        tk.Label(main, text="Te habla por voz durante la sesión: accidentes cerca, banderas, ataque/defensa, "
                            "neumáticos, frenos, combustible y setup al entrar a pits.",
                 bg=c['bg_dark'], fg=c['text_secondary'], font=('Segoe UI', 11),
                 wraplength=900, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 15))
        from app.voice_commands import load_config
        from app.live_engineer import DEFAULT_NEURAL_VOICE
        cfg = load_config()
        running = self.thread is not None and self.thread.is_alive()
        btn = tk.Button(main, text="⏹ Detener ingeniero" if running else "▶ Iniciar ingeniero",
                        command=self.toggle,
                        bg=c['accent_red'] if running else c['accent_green'], fg=c['bg_dark'],
                        font=('Segoe UI', 12, 'bold'), relief=tk.FLAT, cursor='hand2', padx=25, pady=10)
        btn.pack(anchor=tk.W)

        # Configuración: voz y push-to-talk
        conf = tk.LabelFrame(main, text=" Radio ", bg=c['bg_card'], fg=c['accent_blue'],
                             font=('Segoe UI', 11, 'bold'), padx=12, pady=10)
        conf.pack(fill=tk.X, pady=(15, 0))
        tk.Label(conf, text="Voz:", bg=c['bg_card'], fg=c['text_secondary'], font=('Segoe UI', 10)).grid(
            row=0, column=0, sticky=tk.W)
        self.voice_var = tk.StringVar(value=cfg.get("voice") or DEFAULT_NEURAL_VOICE)
        menu = tk.OptionMenu(conf, self.voice_var, *VOICES, command=self._save_voice)
        menu.config(bg=c['bg_secondary'], fg=c['text_primary'], relief=tk.FLAT, highlightthickness=0)
        menu.grid(row=0, column=1, sticky=tk.W, padx=8)
        from app.live_engineer import list_output_devices
        devices = list_output_devices() or ["(predeterminada de Windows)"]
        current = (cfg.get("audio_output") or [None])[0]
        tk.Label(conf, text="Salida de audio:", bg=c['bg_card'], fg=c['text_secondary'],
                 font=('Segoe UI', 10)).grid(row=3, column=0, sticky=tk.W, pady=(8, 0))
        self.output_var = tk.StringVar(value=current if current in devices else devices[0])
        out_menu = tk.OptionMenu(conf, self.output_var, *devices, command=self._save_output)
        out_menu.config(bg=c['bg_secondary'], fg=c['text_primary'], relief=tk.FLAT, highlightthickness=0)
        out_menu.grid(row=3, column=1, columnspan=2, sticky=tk.W, padx=8, pady=(8, 0))
        btn_ptt = cfg.get("ptt_button")
        ptt_text = (f"botón {btn_ptt} de {cfg.get('ptt_joystick')}" if btn_ptt is not None else "sin botón asignado") + \
            f"  •  tecla {str(cfg.get('ptt_key', 'insert')).upper()}"
        self.ptt_label = tk.Label(conf, text=f"Hablar con el ingeniero: {ptt_text}", bg=c['bg_card'],
                                  fg=c['text_secondary'], font=('Segoe UI', 10))
        self.ptt_label.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))
        tk.Button(conf, text="🎮 Asignar botón del volante", command=self._assign_button,
                  bg=c['accent_purple'], fg=c['bg_dark'], font=('Segoe UI', 10, 'bold'), relief=tk.FLAT,
                  cursor='hand2', padx=12, pady=4).grid(row=1, column=2, padx=10, pady=(8, 0))
        tk.Label(conf, text='Mantén el botón, pregunta ("¿cuánto combustible me queda?", "¿tengo daño?", '
                            '"¿a cuánto está el de adelante?", "silencio"...) y suelta.',
                 bg=c['bg_card'], fg=c['text_muted'], font=('Segoe UI', 9), wraplength=850,
                 justify=tk.LEFT).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))
        f = tk.LabelFrame(main, text=" Radio ", bg=c['bg_card'], fg=c['accent_blue'],
                          font=('Segoe UI', 11, 'bold'), padx=10, pady=10)
        f.pack(fill=tk.BOTH, expand=True, pady=15)
        self.log_widget = tk.Text(f, height=22, bg=c['bg_card'], fg=c['text_primary'], relief=tk.FLAT,
                                  font=('Consolas', 10), wrap=tk.WORD)
        self.log_widget.pack(fill=tk.BOTH, expand=True)
        for m in self.messages:
            self.log_widget.insert(tk.END, m + "\n")
        self.log_widget.see(tk.END)

    def toggle(self):
        from app.live_engineer import LiveEngineer, IRSDK_AVAILABLE
        if self.thread is not None and self.thread.is_alive():
            self.engineer.stop()
            self.thread = None
            self.show()
            return
        if not IRSDK_AVAILABLE:
            messagebox.showerror("Falta pyirsdk", "Instala la dependencia: pip install pyirsdk pywin32")
            return
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(log_dir, exist_ok=True)
        import time
        from app.voice_commands import PushToTalk, load_config
        from app.live_engineer import P_BATTLE
        cfg = load_config()
        self.engineer = LiveEngineer(on_message=self._on_message, neural_voice=cfg.get("voice") or DEFAULT_VOICE,
                                     log_path=os.path.join(log_dir, time.strftime("radio_%Y%m%d_%H%M%S.txt")))
        if self.ptt is None:
            self.ptt = PushToTalk(on_text=lambda t: self.engineer and self.engineer.ask(t),
                                  on_error=lambda m: self.engineer and self.engineer.emit(m, P_BATTLE + 5, ttl=6,
                                                                                          always=True),
                                  config=cfg)
            self.ptt.start()
        self.thread = threading.Thread(target=self.engineer.run, daemon=True)
        self.thread.start()
        self.show()

    def _save_output(self, value):
        from app.voice_commands import load_config, save_config
        cfg = load_config()
        cfg["audio_output"] = [value]
        save_config(cfg)

    def _save_voice(self, value):
        from app.voice_commands import load_config, save_config
        cfg = load_config()
        cfg["voice"] = value
        save_config(cfg)

    def _assign_button(self):
        from app.voice_commands import load_config, save_config, wait_for_button
        self.ptt_label.config(text="Aprieta ahora el botón del volante que quieres usar (15 s)...",
                              fg=self.c['accent_yellow'])

        def work():
            found = wait_for_button()

            def done():
                if not found:
                    self.ptt_label.config(text="No detecté ningún botón. Intenta de nuevo.", fg=self.c['accent_red'])
                    return
                cfg = load_config()
                cfg["ptt_joystick"], cfg["ptt_button"] = found
                save_config(cfg)
                self.ptt_label.config(text=f"Listo: botón {found[1]} de {found[0]}. Reinicia el ingeniero para usarlo.",
                                      fg=self.c['accent_green'])
            self.app.root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _on_message(self, text, priority):
        import time
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        self.messages.append(line)

        def add():
            if self.log_widget is not None and self.log_widget.winfo_exists():
                self.log_widget.insert(tk.END, line + "\n")
                self.log_widget.see(tk.END)
        self.app.root.after(0, add)
