"""
Pantalla de progreso: evolución por pista y auto (mejor vuelta, ideal, ritmo, consistencia) y las pruebas A/B
de setup de la base de datos.
"""

import glob
import json
import os
import tkinter as tk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from app import progress as prog


def _load_all():
    out = []
    for path in sorted(glob.glob(os.path.join(prog.BASE, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("sessions"):
                out.append(d)
        except (OSError, ValueError):
            continue
    return out


class ProgressView:
    def __init__(self, app):
        self.app = app
        self.c = app.colors

    def show(self):
        app, c = self.app, self.c
        app.clear_content()
        main = tk.Frame(app.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        app.create_section_title(main, "Progreso y pruebas de setup", "📈")
        data = _load_all()
        if not data:
            tk.Label(main, text="Todavía no hay sesiones guardadas. Se registran solas al correr con el ingeniero "
                                "o al analizar un .ibt.", bg=c['bg_dark'], fg=c['text_secondary'],
                     font=('Segoe UI', 11)).pack(anchor=tk.W)
            return
        for d in data:
            self._track_block(main, d)

    def _track_block(self, parent, d):
        c = self.c
        db = prog.ProgressDB(d["track"], d["car"])
        f = tk.LabelFrame(parent, text=f" {d['track']} · {d['car']} ", bg=c['bg_card'], fg=c['accent_blue'],
                          font=('Segoe UI', 11, 'bold'), padx=15, pady=12)
        f.pack(fill=tk.X, pady=10)
        for line in db.phrases():
            tk.Label(f, text=line, bg=c['bg_card'], fg=c['text_primary'], font=('Segoe UI', 10),
                     wraplength=900, justify=tk.LEFT).pack(anchor=tk.W)
        ss = db.sessions
        if len(ss) >= 2:
            fig = Figure(figsize=(9, 2.8), dpi=100)
            fig.patch.set_facecolor(c['bg_card'])
            ax = fig.add_subplot(111)
            ax.set_facecolor(c['bg_card'])
            x = list(range(1, len(ss) + 1))
            for key, label, color in (("mejor", "Mejor vuelta", c['accent_green']), ("ideal", "Ideal", c['accent_purple']),
                                      ("ritmo", "Ritmo (3 mejores)", c['accent_orange'])):
                pts = [(i, s[key]) for i, s in zip(x, ss) if s.get(key)]
                if pts:
                    ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", color=color, label=label, linewidth=1.5)
            ax.set_xticks(x)
            ax.set_xticklabels([s["fecha"][5:16] for s in ss], rotation=20, fontsize=7, color=c['text_secondary'])
            ax.tick_params(colors=c['text_secondary'], labelsize=8)
            ax.set_ylabel("segundos", color=c['text_secondary'])
            ax.grid(True, color=c['border'], alpha=0.6, linewidth=0.5)
            for spine in ax.spines.values():
                spine.set_color(c['border'])
            leg = ax.legend(fontsize=8, facecolor=c['bg_card'], edgecolor=c['border'])
            for t in leg.get_texts():
                t.set_color(c['text_secondary'])
            fig.tight_layout()
            canvas = FigureCanvasTkAgg(fig, master=f)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.X, pady=(8, 4))
            self.app.active_canvases.append(canvas)
        # Tabla de sesiones
        table = tk.Frame(f, bg=c['bg_card'])
        table.pack(fill=tk.X, pady=(6, 0))
        heads = ["Fecha", "Tipo", "Vueltas", "Mejor", "Ideal", "Ritmo", "Consistencia", "Salidas", "Inc.", "Fuente"]
        for j, h in enumerate(heads):
            tk.Label(table, text=h, bg=c['bg_card'], fg=c['text_muted'], font=('Segoe UI', 8, 'bold')).grid(
                row=0, column=j, padx=4, sticky=tk.W)
        record = min(s["mejor"] for s in ss)
        for i, s in enumerate(ss, start=1):
            vals = [s["fecha"], s.get("tipo") or "", str(s["vueltas"]), prog.fmt(s["mejor"]), prog.fmt(s.get("ideal")),
                    prog.fmt(s.get("ritmo")),
                    f"±{s['consistencia']:.2f}s" if s.get("consistencia") is not None else "--",
                    str(s.get("salidas_pista", "")), str(s.get("incidentes", "")), s.get("fuente", "")]
            for j, v in enumerate(vals):
                col = c['accent_green'] if j == 3 and s["mejor"] == record else c['text_primary']
                tk.Label(table, text=v, bg=c['bg_card'], fg=col, font=('Segoe UI', 9)).grid(
                    row=i, column=j, padx=4, sticky=tk.W)
        self._ab_block(f, d["track"], d["car"])

    def _ab_block(self, parent, track, car):
        """Pruebas A/B entre salidas consecutivas con distinto setup (base de setups)"""
        from app.ab_test import compare, describe, setup_changes
        from app.setup_db import SetupDB
        from app.vehicle_dynamics import fuel_seconds_per_liter
        c = self.c
        stints = [s for s in SetupDB(track, car).stints if len(s.get("tiempos") or []) >= 2]
        results = []
        for a, b in zip(stints, stints[1:]):
            if setup_changes(a.get("setup", {}), b.get("setup", {})):
                r = compare(a, b, fuel_seconds_per_liter(min(b["tiempos"])))
                if r:
                    results.append((b["fecha"], r))
        tk.Label(parent, text="Pruebas A/B de setup", bg=c['bg_card'], fg=c['accent_cyan'],
                 font=('Segoe UI', 10, 'bold')).pack(anchor=tk.W, pady=(12, 2))
        if not results:
            tk.Label(parent, text="Todavía no hay dos salidas seguidas con distinto setup y tiempos guardados. "
                                  "Cambia una cosa entre salidas de 3 a 5 vueltas.", bg=c['bg_card'],
                     fg=c['text_muted'], font=('Segoe UI', 9), wraplength=900, justify=tk.LEFT).pack(anchor=tk.W)
            return
        colors = {"mejor": c['accent_green'], "peor": c['accent_red'], "sin diferencia demostrable": c['text_secondary']}
        for when, r in results[-8:]:
            tk.Label(parent, text=f"{when}: {describe(r)}", bg=c['bg_card'], fg=colors[r["veredicto"]],
                     font=('Segoe UI', 9), wraplength=900, justify=tk.LEFT).pack(anchor=tk.W, pady=1)
