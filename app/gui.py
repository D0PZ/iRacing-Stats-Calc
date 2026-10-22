"""
Interfaz gráfica principal para iRacing Stats Calculator
Tema: Dark Mode
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional
import os

# Importar módulos de la aplicación
from app.data_loader import DataLoader
from app.stats_calculator import StatsCalculator
from app.projections import ProjectionEngine
from app.charts import ChartGenerator
from app.ml_engine import MLEngine, SKLEARN_AVAILABLE
from app.telemetry_view import TelemetryView, LiveEngineerView
from app.progress_view import ProgressView


class IracingStatsApp:
    """Aplicación principal de estadísticas de iRacing - Dark Mode"""
    
    # Paleta de colores oscura
    DARK_THEME = {
        'bg_dark': '#0D1117',           # Fondo principal muy oscuro
        'bg_secondary': '#161B22',       # Fondo secundario
        'bg_card': '#21262D',            # Fondo de tarjetas
        'bg_hover': '#30363D',           # Hover
        'border': '#30363D',             # Bordes
        'text_primary': '#F0F6FC',       # Texto principal
        'text_secondary': '#8B949E',     # Texto secundario
        'text_muted': '#6E7681',         # Texto apagado
        'accent_blue': '#58A6FF',        # Azul acento
        'accent_green': '#3FB950',       # Verde éxito
        'accent_yellow': '#D29922',      # Amarillo warning
        'accent_red': '#F85149',         # Rojo peligro
        'accent_purple': '#A371F7',      # Púrpura
        'accent_cyan': '#39C5CF',        # Cyan info
        'accent_orange': '#F0883E',      # Naranja
    }
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("🏎️ iRacing Stats Calculator")
        self.root.geometry("1300x850")
        self.root.minsize(1100, 750)
        
        # Colores
        self.colors = self.DARK_THEME
        
        # Configurar estilo oscuro
        self.setup_dark_theme()
        
        # Inicializar componentes
        self.data_loader = DataLoader()
        self.stats_calc: Optional[StatsCalculator] = None
        self.projection: Optional[ProjectionEngine] = None
        self.chart_gen: Optional[ChartGenerator] = None
        self.ml_engine: Optional[MLEngine] = None
        
        # Variable para el archivo cargado
        self.loaded_file = tk.StringVar(value="Ningún archivo cargado")
        
        # Canvas activos para limpiar
        self.active_canvases = []

        # Telemetría (.ibt) e ingeniero en vivo: no dependen del JSON
        self.telemetry_view = TelemetryView(self)
        self.live_view = LiveEngineerView(self)
        self.progress_view = ProgressView(self)
        
        # Construir interfaz
        self.build_ui()
    
    def setup_dark_theme(self):
        """Configura el tema oscuro completo"""
        style = ttk.Style()
        
        # Usar tema base
        style.theme_use('clam')
        
        c = self.colors
        
        # Frame principal
        style.configure('TFrame', background=c['bg_dark'])
        style.configure('Dark.TFrame', background=c['bg_dark'])
        style.configure('Card.TFrame', background=c['bg_card'])
        style.configure('Secondary.TFrame', background=c['bg_secondary'])
        
        # Labels
        style.configure('TLabel', 
                       background=c['bg_dark'], 
                       foreground=c['text_primary'],
                       font=('Segoe UI', 10))
        
        style.configure('Title.TLabel', 
                       background=c['bg_dark'],
                       foreground=c['accent_blue'],
                       font=('Segoe UI', 28, 'bold'))
        
        style.configure('Subtitle.TLabel', 
                       background=c['bg_dark'],
                       foreground=c['text_secondary'],
                       font=('Segoe UI', 12))
        
        style.configure('Card.TLabel',
                       background=c['bg_card'],
                       foreground=c['text_primary'],
                       font=('Segoe UI', 10))
        
        style.configure('StatValue.TLabel',
                       background=c['bg_card'],
                       foreground=c['accent_cyan'],
                       font=('Segoe UI', 20, 'bold'))
        
        style.configure('StatTitle.TLabel',
                       background=c['bg_card'],
                       foreground=c['text_muted'],
                       font=('Segoe UI', 9))
        
        style.configure('Section.TLabel',
                       background=c['bg_dark'],
                       foreground=c['text_primary'],
                       font=('Segoe UI', 13, 'bold'))
        
        style.configure('Stat.TLabel',
                       background=c['bg_dark'],
                       foreground=c['text_secondary'],
                       font=('Segoe UI', 10))
        
        style.configure('Header.TLabel',
                       background=c['bg_secondary'],
                       foreground=c['text_primary'],
                       font=('Segoe UI', 10))
        
        # Botones
        style.configure('TButton',
                       background=c['bg_card'],
                       foreground=c['text_primary'],
                       font=('Segoe UI', 10),
                       padding=(15, 8))
        style.map('TButton',
                 background=[('active', c['bg_hover']), ('pressed', c['accent_blue'])],
                 foreground=[('active', c['text_primary'])])
        
        style.configure('Primary.TButton',
                       background=c['accent_blue'],
                       foreground=c['bg_dark'],
                       font=('Segoe UI', 10, 'bold'),
                       padding=(20, 10))
        style.map('Primary.TButton',
                 background=[('active', '#79C0FF'), ('pressed', '#388BFD')])
        
        style.configure('Nav.TButton',
                       background=c['bg_secondary'],
                       foreground=c['text_secondary'],
                       font=('Segoe UI', 10),
                       padding=(15, 12),
                       anchor='w')
        style.map('Nav.TButton',
                 background=[('active', c['bg_hover']), ('pressed', c['bg_card'])],
                 foreground=[('active', c['text_primary'])])
        
        style.configure('NavActive.TButton',
                       background=c['bg_card'],
                       foreground=c['accent_blue'],
                       font=('Segoe UI', 10, 'bold'),
                       padding=(15, 12))
        
        # LabelFrame
        style.configure('TLabelframe',
                       background=c['bg_card'],
                       foreground=c['text_primary'])
        style.configure('TLabelframe.Label',
                       background=c['bg_card'],
                       foreground=c['accent_blue'],
                       font=('Segoe UI', 11, 'bold'))
        
        # Scrollbar
        style.configure('TScrollbar',
                       background=c['bg_secondary'],
                       troughcolor=c['bg_dark'],
                       arrowcolor=c['text_secondary'])
        
        # Separator
        style.configure('TSeparator', background=c['border'])
        
        # Canvas y ventana principal
        self.root.configure(bg=c['bg_dark'])
    
    def build_ui(self):
        """Construye la interfaz de usuario"""
        c = self.colors
        
        # Frame principal
        self.main_frame = tk.Frame(self.root, bg=c['bg_dark'], padx=15, pady=10)
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        self.build_header()
        
        # Separador
        sep = tk.Frame(self.main_frame, height=1, bg=c['border'])
        sep.pack(fill=tk.X, pady=10)
        
        # Contenido principal (dividido en sidebar y contenido)
        self.content_frame = tk.Frame(self.main_frame, bg=c['bg_dark'])
        self.content_frame.pack(fill=tk.BOTH, expand=True)
        
        # Sidebar de navegación
        self.build_sidebar()
        
        # Área de contenido
        self.build_content_area()
        
        # Footer
        self.build_footer()
    
    def build_header(self):
        """Construye el header de la aplicación"""
        c = self.colors
        
        header_frame = tk.Frame(self.main_frame, bg=c['bg_dark'])
        header_frame.pack(fill=tk.X, pady=(0, 5))
        
        # Título
        title_frame = tk.Frame(header_frame, bg=c['bg_dark'])
        title_frame.pack(side=tk.LEFT)
        
        title_label = tk.Label(title_frame, 
                              text="🏎️ iRacing Stats Calculator",
                              bg=c['bg_dark'],
                              fg=c['accent_blue'],
                              font=('Segoe UI', 26, 'bold'))
        title_label.pack(anchor=tk.W)
        
        subtitle_label = tk.Label(title_frame,
                                 text="Analiza tu rendimiento y proyecta tu progreso",
                                 bg=c['bg_dark'],
                                 fg=c['text_secondary'],
                                 font=('Segoe UI', 11))
        subtitle_label.pack(anchor=tk.W)
        
        # Botón de carga y archivo
        load_frame = tk.Frame(header_frame, bg=c['bg_dark'])
        load_frame.pack(side=tk.RIGHT)
        
        # Indicador de archivo
        file_label = tk.Label(load_frame,
                             textvariable=self.loaded_file,
                             bg=c['bg_dark'],
                             fg=c['text_muted'],
                             font=('Segoe UI', 10))
        file_label.pack(side=tk.RIGHT, padx=15)
        
        # Botón cargar
        load_btn = tk.Button(load_frame,
                            text="📂 Cargar JSON",
                            command=self.load_file,
                            bg=c['accent_blue'],
                            fg=c['bg_dark'],
                            font=('Segoe UI', 11, 'bold'),
                            relief=tk.FLAT,
                            cursor='hand2',
                            padx=20,
                            pady=8)
        load_btn.pack(side=tk.RIGHT)
        load_btn.bind('<Enter>', lambda e: load_btn.config(bg='#79C0FF'))
        load_btn.bind('<Leave>', lambda e: load_btn.config(bg=c['accent_blue']))
    
    def build_sidebar(self):
        """Construye el sidebar de navegación"""
        c = self.colors
        
        self.sidebar = tk.Frame(self.content_frame, bg=c['bg_secondary'], width=220)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        self.sidebar.pack_propagate(False)
        
        # Padding interno
        inner_sidebar = tk.Frame(self.sidebar, bg=c['bg_secondary'], padx=10, pady=15)
        inner_sidebar.pack(fill=tk.BOTH, expand=True)
        
        # Título navegación
        nav_title = tk.Label(inner_sidebar,
                            text="📊 NAVEGACIÓN",
                            bg=c['bg_secondary'],
                            fg=c['text_muted'],
                            font=('Segoe UI', 10, 'bold'))
        nav_title.pack(anchor=tk.W, pady=(0, 15))
        
        # Botones de navegación
        self.nav_buttons = []
        nav_items = [
            ("📈  Resumen General", self.show_overview),
            ("🏁  Estadísticas Carreras", self.show_race_stats),
            ("🏆  Por Serie", self.show_series_stats),
            ("🛣️  Por Pista", self.show_track_stats),
            ("📉  Incidentes", self.show_incidents),
            ("🔮  Proyecciones", self.show_projections),
            ("🤖  IA Predictiva", self.show_ml_predictions),
            ("🔍  Análisis Patrones", self.show_pattern_analysis),
            ("📊  Gráficos", self.show_charts),
            ("🔧  Telemetría y Setup", self.show_telemetry),
            ("🎙️  Ingeniero en Vivo", self.show_live_engineer),
            ("📈  Progreso y A/B", self.show_progress),
        ]
        
        for text, command in nav_items:
            btn = tk.Button(inner_sidebar,
                           text=text,
                           command=command,
                           bg=c['bg_secondary'],
                           fg=c['text_secondary'],
                           font=('Segoe UI', 10),
                           relief=tk.FLAT,
                           anchor='w',
                           padx=10,
                           pady=10,
                           cursor='hand2',
                           width=18)
            btn.pack(fill=tk.X, pady=2)
            btn.bind('<Enter>', lambda e, b=btn: b.config(bg=c['bg_hover'], fg=c['text_primary']))
            btn.bind('<Leave>', lambda e, b=btn: b.config(bg=c['bg_secondary'], fg=c['text_secondary']))
            self.nav_buttons.append(btn)
    
    def build_content_area(self):
        """Construye el área de contenido principal"""
        c = self.colors
        
        # Frame contenedor
        self.content_container = tk.Frame(self.content_frame, bg=c['bg_dark'])
        self.content_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Canvas para scroll
        self.canvas = tk.Canvas(self.content_container, 
                               bg=c['bg_dark'], 
                               highlightthickness=0,
                               bd=0)
        self.scrollbar = ttk.Scrollbar(self.content_container, orient=tk.VERTICAL, 
                                       command=self.canvas.yview)
        
        self.scrollable_frame = tk.Frame(self.canvas, bg=c['bg_dark'])
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, 
                                                        anchor=tk.NW)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        # Ajustar ancho del frame interno
        self.canvas.bind('<Configure>', self._on_canvas_configure)
        
        # Scroll con rueda del mouse
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Mostrar mensaje inicial
        self.show_welcome()
    
    def _on_canvas_configure(self, event):
        """Ajusta el ancho del frame interno al canvas"""
        self.canvas.itemconfig(self.canvas_window, width=event.width)
    
    def _on_mousewheel(self, event):
        """Maneja el scroll con la rueda del mouse"""
        self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")
    
    def build_footer(self):
        """Construye el footer"""
        c = self.colors
        
        footer = tk.Frame(self.main_frame, bg=c['bg_dark'])
        footer.pack(fill=tk.X, pady=(10, 0))
        
        footer_label = tk.Label(footer,
                               text="iRacing Stats Calculator v1.0 | Tema Oscuro | Datos de iRacing API",
                               bg=c['bg_dark'],
                               fg=c['text_muted'],
                               font=('Segoe UI', 9))
        footer_label.pack()
    
    def clear_content(self):
        """Limpia el contenido actual"""
        # Limpiar canvases de matplotlib
        for canvas in self.active_canvases:
            try:
                canvas.get_tk_widget().destroy()
            except:
                pass
        self.active_canvases = []
        
        # Limpiar widgets
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
    
    def create_stat_card(self, parent, title: str, value: str, subtitle: str = "", 
                        accent_color: str = None) -> tk.Frame:
        """Crea una tarjeta de estadística con estilo oscuro"""
        c = self.colors
        accent = accent_color or c['accent_cyan']
        
        card = tk.Frame(parent, bg=c['bg_card'], padx=20, pady=15)
        card.configure(highlightbackground=c['border'], highlightthickness=1)
        
        # Título
        title_label = tk.Label(card, text=title, 
                              bg=c['bg_card'], fg=c['text_muted'],
                              font=('Segoe UI', 9))
        title_label.pack(anchor=tk.W)
        
        # Valor
        value_label = tk.Label(card, text=value,
                              bg=c['bg_card'], fg=accent,
                              font=('Segoe UI', 22, 'bold'))
        value_label.pack(anchor=tk.W, pady=(5, 3))
        
        # Subtítulo
        if subtitle:
            sub_label = tk.Label(card, text=subtitle,
                                bg=c['bg_card'], fg=c['text_muted'],
                                font=('Segoe UI', 9))
            sub_label.pack(anchor=tk.W)
        
        return card
    
    def create_section_title(self, parent, text: str, icon: str = ""):
        """Crea un título de sección"""
        c = self.colors
        frame = tk.Frame(parent, bg=c['bg_dark'])
        frame.pack(fill=tk.X, pady=(0, 20))
        
        label = tk.Label(frame, text=f"{icon} {text}" if icon else text,
                        bg=c['bg_dark'], fg=c['accent_blue'],
                        font=('Segoe UI', 22, 'bold'))
        label.pack(anchor=tk.W)
        
        # Línea decorativa
        line = tk.Frame(frame, height=3, bg=c['accent_blue'], width=60)
        line.pack(anchor=tk.W, pady=(8, 0))
    
    def show_welcome(self):
        """Muestra la pantalla de bienvenida"""
        self.clear_content()
        c = self.colors
        
        frame = tk.Frame(self.scrollable_frame, bg=c['bg_dark'], padx=50, pady=80)
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Icono grande
        icon_label = tk.Label(frame, text="🏎️",
                             bg=c['bg_dark'],
                             font=('Segoe UI', 72))
        icon_label.pack(pady=(0, 20))
        
        # Título
        title = tk.Label(frame, text="¡Bienvenido!",
                        bg=c['bg_dark'], fg=c['text_primary'],
                        font=('Segoe UI', 32, 'bold'))
        title.pack(pady=(0, 15))
        
        # Descripción
        desc = tk.Label(frame,
                       text="Carga un archivo JSON de iRacing para comenzar el análisis.",
                       bg=c['bg_dark'], fg=c['text_secondary'],
                       font=('Segoe UI', 14))
        desc.pack(pady=(0, 10))
        
        desc2 = tk.Label(frame,
                        text="El archivo debe contener los resultados de tus sesiones\n(puedes obtenerlo desde la API de iRacing o herramientas externas)",
                        bg=c['bg_dark'], fg=c['text_muted'],
                        font=('Segoe UI', 11),
                        justify=tk.CENTER)
        desc2.pack(pady=(0, 40))
        
        # Botón grande
        load_btn = tk.Button(frame,
                            text="📂  Cargar Archivo JSON",
                            command=self.load_file,
                            bg=c['accent_blue'],
                            fg=c['bg_dark'],
                            font=('Segoe UI', 14, 'bold'),
                            relief=tk.FLAT,
                            cursor='hand2',
                            padx=40,
                            pady=15)
        load_btn.pack()
        load_btn.bind('<Enter>', lambda e: load_btn.config(bg='#79C0FF'))
        load_btn.bind('<Leave>', lambda e: load_btn.config(bg=c['accent_blue']))
    
    def load_file(self):
        """Abre diálogo para cargar archivo JSON"""
        file_path = filedialog.askopenfilename(
            title="Seleccionar archivo JSON de iRacing",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=os.path.dirname(os.path.abspath(__file__))
        )
        
        if file_path:
            try:
                self.data_loader.load_file(file_path)
                
                # Inicializar calculadores
                self.stats_calc = StatsCalculator(self.data_loader)
                self.projection = ProjectionEngine(self.stats_calc)
                self.chart_gen = ChartGenerator(self.stats_calc, self.projection)
                self.ml_engine = MLEngine(self.stats_calc)
                
                # Intentar cargar modelo existente primero
                load_result = self.ml_engine.load_model()
                model_loaded = load_result.get('success', False)
                
                # Entrenar/actualizar modelo ML con nuevos datos
                ml_result = self.ml_engine.train_models()
                
                # Actualizar UI
                filename = os.path.basename(file_path)
                self.loaded_file.set(f"✅ {filename}")
                
                # Mostrar resumen
                self.show_overview()
                
                # Mensaje de estado del modelo
                if ml_result.get('success'):
                    total_learned = self.ml_engine.total_races_learned
                    sessions = self.ml_engine.training_sessions
                    if model_loaded:
                        ml_status = f"🤖 IA actualizada\n({total_learned} carreras aprendidas en {sessions} sesiones)"
                    else:
                        ml_status = f"🤖 IA entrenada\n({total_learned} carreras aprendidas)"
                else:
                    ml_status = "⚠️ IA no disponible"
                
                messagebox.showinfo("Éxito", 
                    f"Archivo cargado correctamente!\n\n"
                    f"Sesiones: {len(self.data_loader.get_all_sessions())}\n"
                    f"Carreras: {len(self.data_loader.get_races())}\n"
                    f"Prácticas: {len(self.data_loader.get_practices())}\n\n"
                    f"{ml_status}")
                
            except Exception as e:
                messagebox.showerror("Error", f"Error al cargar archivo:\n{str(e)}")
    
    def check_data_loaded(self) -> bool:
        """Verifica si hay datos cargados"""
        if not self.data_loader.is_loaded():
            messagebox.showwarning("Aviso", "Primero debes cargar un archivo JSON")
            return False
        return True
    
    def show_overview(self):
        """Muestra el resumen general"""
        if not self.check_data_loaded():
            return
        
        self.clear_content()
        c = self.colors
        
        main = tk.Frame(self.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        
        # Título
        self.create_section_title(main, "Resumen General", "📈")
        
        # Obtener estadísticas
        stats = self.stats_calc.calculate_race_stats()
        summary = self.data_loader.get_summary()
        
        # Primera fila de tarjetas
        row1 = tk.Frame(main, bg=c['bg_dark'])
        row1.pack(fill=tk.X, pady=10)
        
        self.create_stat_card(row1, "CARRERAS", str(stats.total_races), 
                             "Total de carreras", c['accent_blue']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row1, "PRÁCTICAS", str(summary['total_practices']), 
                             "Total de prácticas", c['accent_purple']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row1, "SERIES", str(summary['unique_series']), 
                             "Series diferentes", c['accent_cyan']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row1, "PISTAS", str(summary['unique_tracks']), 
                             "Pistas diferentes", c['accent_orange']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Segunda fila
        row2 = tk.Frame(main, bg=c['bg_dark'])
        row2.pack(fill=tk.X, pady=10)
        
        self.create_stat_card(row2, "MEJOR RESULTADO", f"P{stats.best_finish}" if stats.best_finish > 0 else "N/A", 
                             "Mejor posición", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row2, "PROMEDIO", f"P{stats.avg_finish:.1f}" if stats.avg_finish > 0 else "N/A", 
                             "Posición promedio", c['accent_blue']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row2, "PODIOS", str(stats.podiums), 
                             f"{(stats.podiums/stats.total_races*100):.0f}% de carreras" if stats.total_races > 0 else "", c['accent_yellow']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row2, "PUNTOS", str(stats.total_points), 
                             "Puntos de campeonato", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Tercera fila
        row3 = tk.Frame(main, bg=c['bg_dark'])
        row3.pack(fill=tk.X, pady=10)
        
        inc_color = c['accent_green'] if stats.avg_incidents <= 4 else c['accent_yellow'] if stats.avg_incidents <= 8 else c['accent_red']
        self.create_stat_card(row3, "INCIDENTES", f"{stats.avg_incidents:.1f}x", 
                             "Promedio por carrera", inc_color).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        gain_color = c['accent_green'] if stats.avg_gain >= 0 else c['accent_red']
        self.create_stat_card(row3, "GANANCIA", f"{'+' if stats.avg_gain >= 0 else ''}{stats.avg_gain:.1f}", 
                             "Posiciones promedio", gain_color).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row3, "VUELTAS LÍDER", str(stats.laps_led), 
                             "Total de vueltas", c['accent_purple']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row3, "CONSISTENCIA", f"±{stats.consistency:.1f}", 
                             "Desviación estándar", c['accent_cyan']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Gráfico de tendencia
        chart_frame = tk.LabelFrame(main, text=" Tendencia de Posiciones ", 
                                   bg=c['bg_card'], fg=c['accent_blue'],
                                   font=('Segoe UI', 11, 'bold'),
                                   padx=15, pady=15)
        chart_frame.pack(fill=tk.BOTH, expand=True, pady=20)
        
        if self.chart_gen:
            canvas = self.chart_gen.create_position_trend_chart(chart_frame)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.active_canvases.append(canvas)
    
    def show_race_stats(self):
        """Muestra estadísticas detalladas de carreras"""
        if not self.check_data_loaded():
            return
        
        self.clear_content()
        c = self.colors
        
        main = tk.Frame(self.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        
        self.create_section_title(main, "Estadísticas de Carreras", "🏁")
        
        stats = self.stats_calc.calculate_race_stats()
        quali = self.stats_calc.get_qualifying_analysis()
        
        # Tabla de estadísticas
        table_frame = tk.LabelFrame(main, text=" Resumen de Carreras ",
                                   bg=c['bg_card'], fg=c['accent_blue'],
                                   font=('Segoe UI', 11, 'bold'),
                                   padx=20, pady=15)
        table_frame.pack(fill=tk.X, pady=10)
        
        stats_data = [
            ("Total de carreras", str(stats.total_races), c['text_primary']),
            ("Victorias", f"{stats.wins} ({stats.wins/stats.total_races*100:.0f}%)" if stats.total_races > 0 else "0", c['accent_green']),
            ("Podios (Top 3)", f"{stats.podiums} ({stats.podiums/stats.total_races*100:.0f}%)" if stats.total_races > 0 else "0", c['accent_yellow']),
            ("Top 5", f"{stats.top5} ({stats.top5/stats.total_races*100:.0f}%)" if stats.total_races > 0 else "0", c['accent_blue']),
            ("Top 10", f"{stats.top10} ({stats.top10/stats.total_races*100:.0f}%)" if stats.total_races > 0 else "0", c['accent_cyan']),
            ("DNF", f"{stats.dnf} ({stats.dnf/stats.total_races*100:.0f}%)" if stats.total_races > 0 else "0", c['accent_red']),
            (None, None, None),
            ("Mejor resultado", f"P{stats.best_finish}" if stats.best_finish > 0 else "N/A", c['accent_green']),
            ("Peor resultado", f"P{stats.worst_finish}" if stats.worst_finish > 0 else "N/A", c['accent_red']),
            ("Promedio llegada", f"P{stats.avg_finish:.1f}" if stats.avg_finish > 0 else "N/A", c['accent_blue']),
            ("Promedio salida", f"P{stats.avg_start:.1f}" if stats.avg_start > 0 else "N/A", c['accent_purple']),
            ("Ganancia promedio", f"{'+' if stats.avg_gain >= 0 else ''}{stats.avg_gain:.1f} posiciones", c['accent_green'] if stats.avg_gain >= 0 else c['accent_red']),
            (None, None, None),
            ("Total de vueltas", str(stats.total_laps), c['text_primary']),
            ("Vueltas lideradas", str(stats.laps_led), c['accent_yellow']),
            ("Total de incidentes", f"{stats.total_incidents}x", c['accent_orange']),
            ("Promedio incidentes", f"{stats.avg_incidents:.1f}x", c['accent_orange']),
            ("SOF promedio", f"{stats.avg_sof:.0f}" if stats.avg_sof > 0 else "N/A", c['accent_cyan']),
        ]
        
        for i, (label, value, color) in enumerate(stats_data):
            if label is None:
                sep = tk.Frame(table_frame, height=1, bg=c['border'])
                sep.grid(row=i, column=0, columnspan=2, sticky='ew', pady=8)
            else:
                tk.Label(table_frame, text=label, bg=c['bg_card'], fg=c['text_secondary'],
                        font=('Segoe UI', 10)).grid(row=i, column=0, sticky='w', padx=10, pady=4)
                tk.Label(table_frame, text=value, bg=c['bg_card'], fg=color,
                        font=('Segoe UI', 12, 'bold')).grid(row=i, column=1, sticky='e', padx=10, pady=4)
        
        # Análisis de clasificación
        quali_frame = tk.LabelFrame(main, text=" Análisis Clasificación vs Carrera ",
                                   bg=c['bg_card'], fg=c['accent_blue'],
                                   font=('Segoe UI', 11, 'bold'),
                                   padx=15, pady=15)
        quali_frame.pack(fill=tk.X, pady=15)
        
        quali_row = tk.Frame(quali_frame, bg=c['bg_card'])
        quali_row.pack(fill=tk.X)
        
        self.create_stat_card(quali_row, "MEJORÓ POSICIÓN", str(quali['races_improved']), 
                             f"{quali['improvement_rate']:.0f}% de carreras", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(quali_row, "MANTUVO POSICIÓN", str(quali['races_maintained']), 
                             f"{quali['consistency_rate']:.0f}% de carreras", c['accent_yellow']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(quali_row, "PERDIÓ POSICIONES", str(quali['races_lost_positions']), 
                             "carreras", c['accent_red']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Gráfico de distribución
        pie_frame = tk.LabelFrame(main, text=" Distribución de Resultados ",
                                 bg=c['bg_card'], fg=c['accent_blue'],
                                 font=('Segoe UI', 11, 'bold'),
                                 padx=15, pady=15)
        pie_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        if self.chart_gen:
            canvas = self.chart_gen.create_pie_chart_results(pie_frame)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.active_canvases.append(canvas)
    
    def show_series_stats(self):
        """Muestra estadísticas por serie"""
        if not self.check_data_loaded():
            return
        
        self.clear_content()
        c = self.colors
        
        main = tk.Frame(self.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        
        self.create_section_title(main, "Estadísticas por Serie", "🏆")
        
        series_stats = self.stats_calc.calculate_stats_by_series()
        
        if not series_stats:
            tk.Label(main, text="No hay datos de series disponibles",
                    bg=c['bg_dark'], fg=c['text_secondary'],
                    font=('Segoe UI', 14)).pack(pady=20)
            return
        
        # Crear tarjeta para cada serie
        for series_name, stats in series_stats.items():
            series_frame = tk.LabelFrame(main, text=f" {series_name} ",
                                        bg=c['bg_card'], fg=c['accent_blue'],
                                        font=('Segoe UI', 11, 'bold'),
                                        padx=15, pady=15)
            series_frame.pack(fill=tk.X, pady=8)
            
            row = tk.Frame(series_frame, bg=c['bg_card'])
            row.pack(fill=tk.X)
            
            self.create_stat_card(row, "CARRERAS", str(stats.total_races), 
                                 "", c['accent_blue']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
            self.create_stat_card(row, "PROMEDIO", f"P{stats.avg_finish:.1f}", 
                                 "", c['accent_cyan']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
            self.create_stat_card(row, "MEJOR", f"P{stats.best_finish}" if stats.best_finish > 0 else "N/A", 
                                 "", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
            self.create_stat_card(row, "PODIOS", str(stats.podiums), 
                                 "", c['accent_yellow']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
            self.create_stat_card(row, "INCIDENTES", f"{stats.avg_incidents:.1f}x", 
                                 "", c['accent_orange']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
            self.create_stat_card(row, "PUNTOS", str(stats.total_points), 
                                 "", c['accent_purple']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Gráfico de distribución
        chart_frame = tk.LabelFrame(main, text=" Carreras por Serie ",
                                   bg=c['bg_card'], fg=c['accent_blue'],
                                   font=('Segoe UI', 11, 'bold'),
                                   padx=15, pady=15)
        chart_frame.pack(fill=tk.BOTH, expand=True, pady=15)
        
        if self.chart_gen:
            canvas = self.chart_gen.create_series_distribution_chart(chart_frame)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.active_canvases.append(canvas)
    
    def show_track_stats(self):
        """Muestra estadísticas por pista"""
        if not self.check_data_loaded():
            return
        
        self.clear_content()
        c = self.colors
        
        main = tk.Frame(self.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        
        self.create_section_title(main, "Estadísticas por Pista", "🛣️")
        
        track_stats = self.stats_calc.calculate_stats_by_track()
        
        if not track_stats:
            tk.Label(main, text="No hay datos de pistas disponibles",
                    bg=c['bg_dark'], fg=c['text_secondary'],
                    font=('Segoe UI', 14)).pack(pady=20)
            return
        
        # Crear tabla
        table_frame = tk.LabelFrame(main, text=" Rendimiento por Pista ",
                                   bg=c['bg_card'], fg=c['accent_blue'],
                                   font=('Segoe UI', 11, 'bold'),
                                   padx=20, pady=15)
        table_frame.pack(fill=tk.X, pady=10)
        
        # Encabezados
        headers = ["Pista", "Carreras", "Promedio", "Mejor", "Podios", "Incidentes"]
        for col, header in enumerate(headers):
            tk.Label(table_frame, text=header, bg=c['bg_card'], fg=c['text_muted'],
                    font=('Segoe UI', 10, 'bold')).grid(row=0, column=col, padx=15, pady=8, sticky='w')
        
        sep = tk.Frame(table_frame, height=1, bg=c['border'])
        sep.grid(row=1, column=0, columnspan=len(headers), sticky='ew', pady=5)
        
        # Datos
        for row, (track_name, stats) in enumerate(track_stats.items(), start=2):
            tk.Label(table_frame, text=track_name[:40], bg=c['bg_card'], fg=c['text_primary'],
                    font=('Segoe UI', 10)).grid(row=row, column=0, padx=15, pady=6, sticky='w')
            tk.Label(table_frame, text=str(stats.total_races), bg=c['bg_card'], fg=c['accent_blue'],
                    font=('Segoe UI', 10, 'bold')).grid(row=row, column=1, padx=15, pady=6)
            tk.Label(table_frame, text=f"P{stats.avg_finish:.1f}", bg=c['bg_card'], fg=c['accent_cyan'],
                    font=('Segoe UI', 10, 'bold')).grid(row=row, column=2, padx=15, pady=6)
            tk.Label(table_frame, text=f"P{stats.best_finish}" if stats.best_finish > 0 else "N/A", 
                    bg=c['bg_card'], fg=c['accent_green'],
                    font=('Segoe UI', 10, 'bold')).grid(row=row, column=3, padx=15, pady=6)
            tk.Label(table_frame, text=str(stats.podiums), bg=c['bg_card'], fg=c['accent_yellow'],
                    font=('Segoe UI', 10, 'bold')).grid(row=row, column=4, padx=15, pady=6)
            tk.Label(table_frame, text=f"{stats.avg_incidents:.1f}x", bg=c['bg_card'], fg=c['accent_orange'],
                    font=('Segoe UI', 10, 'bold')).grid(row=row, column=5, padx=15, pady=6)
    
    def show_incidents(self):
        """Muestra análisis de incidentes"""
        if not self.check_data_loaded():
            return
        
        self.clear_content()
        c = self.colors
        
        main = tk.Frame(self.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        
        self.create_section_title(main, "Análisis de Incidentes", "📉")
        
        incident_data = self.stats_calc.get_incident_analysis()
        
        if not incident_data:
            tk.Label(main, text="No hay datos de incidentes disponibles",
                    bg=c['bg_dark'], fg=c['text_secondary'],
                    font=('Segoe UI', 14)).pack(pady=20)
            return
        
        # Tarjetas de resumen
        row1 = tk.Frame(main, bg=c['bg_dark'])
        row1.pack(fill=tk.X, pady=10)
        
        self.create_stat_card(row1, "TOTAL", f"{incident_data['total_incidents']}x", 
                             "Incidentes totales", c['accent_orange']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        avg_color = c['accent_green'] if incident_data['avg_incidents'] <= 4 else c['accent_yellow'] if incident_data['avg_incidents'] <= 8 else c['accent_red']
        self.create_stat_card(row1, "PROMEDIO", f"{incident_data['avg_incidents']:.1f}x", 
                             "Por carrera", avg_color).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row1, "MÍNIMO", f"{incident_data['min_incidents']}x", 
                             "Mejor carrera", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row1, "MÁXIMO", f"{incident_data['max_incidents']}x", 
                             "Peor carrera", c['accent_red']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Distribución de carreras
        dist_frame = tk.LabelFrame(main, text=" Distribución de Carreras por Incidentes ",
                                  bg=c['bg_card'], fg=c['accent_blue'],
                                  font=('Segoe UI', 11, 'bold'),
                                  padx=15, pady=15)
        dist_frame.pack(fill=tk.X, pady=15)
        
        row2 = tk.Frame(dist_frame, bg=c['bg_card'])
        row2.pack(fill=tk.X)
        
        self.create_stat_card(row2, "🟢 LIMPIAS (≤4x)", str(incident_data['clean_races']), 
                             f"{incident_data['clean_rate']:.0f}% del total", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row2, "🟡 MODERADAS (5x-8x)", str(incident_data['moderate_races']), 
                             "carreras", c['accent_yellow']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row2, "🔴 ACCIDENTADAS (>8x)", str(incident_data['rough_races']), 
                             "carreras", c['accent_red']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Gráfico de incidentes
        chart_frame = tk.LabelFrame(main, text=" Incidentes por Carrera ",
                                   bg=c['bg_card'], fg=c['accent_blue'],
                                   font=('Segoe UI', 11, 'bold'),
                                   padx=15, pady=15)
        chart_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        if self.chart_gen:
            canvas = self.chart_gen.create_incidents_chart(chart_frame)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.active_canvases.append(canvas)
    
    def show_projections(self):
        """Muestra proyecciones y tendencias"""
        if not self.check_data_loaded():
            return
        
        self.clear_content()
        c = self.colors
        
        main = tk.Frame(self.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        
        self.create_section_title(main, "Proyecciones y Tendencias", "🔮")
        
        # Obtener proyecciones
        finish_proj = self.projection.project_finish_position()
        incident_proj = self.projection.project_incidents()
        points_proj = self.projection.project_championship_points()
        areas = self.projection.get_improvement_areas()
        
        # Proyección de posiciones
        pos_frame = tk.LabelFrame(main, text=" Proyección de Posiciones ",
                                 bg=c['bg_card'], fg=c['accent_blue'],
                                 font=('Segoe UI', 11, 'bold'),
                                 padx=15, pady=15)
        pos_frame.pack(fill=tk.X, pady=10)
        
        row = tk.Frame(pos_frame, bg=c['bg_card'])
        row.pack(fill=tk.X)
        
        trend_emoji = "📈" if finish_proj['trend'] == 'improving' else "📉" if finish_proj['trend'] == 'declining' else "➡️"
        trend_color = c['accent_green'] if finish_proj['trend'] == 'improving' else c['accent_red'] if finish_proj['trend'] == 'declining' else c['accent_yellow']
        
        self.create_stat_card(row, "ACTUAL", f"P{finish_proj['current_avg']:.1f}", 
                             "Promedio reciente", c['accent_cyan']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row, "PROYECTADO", f"P{finish_proj['projected_avg']:.1f}", 
                             "Próximas 5 carreras", c['accent_blue']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row, "TENDENCIA", f"{trend_emoji} {finish_proj['trend'].upper()}", 
                             f"Confianza: {finish_proj['confidence']}", trend_color).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Proyección de incidentes
        inc_frame = tk.LabelFrame(main, text=" Proyección de Incidentes ",
                                 bg=c['bg_card'], fg=c['accent_blue'],
                                 font=('Segoe UI', 11, 'bold'),
                                 padx=15, pady=15)
        inc_frame.pack(fill=tk.X, pady=10)
        
        row2 = tk.Frame(inc_frame, bg=c['bg_card'])
        row2.pack(fill=tk.X)
        
        inc_emoji = "📉" if incident_proj['trend'] == 'improving' else "📈" if incident_proj['trend'] == 'worsening' else "➡️"
        inc_color = c['accent_green'] if incident_proj['trend'] == 'improving' else c['accent_red'] if incident_proj['trend'] == 'worsening' else c['accent_yellow']
        
        self.create_stat_card(row2, "ACTUAL", f"{incident_proj['current_avg']:.1f}x", 
                             "Promedio reciente", c['accent_orange']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row2, "PROYECTADO", f"{incident_proj['projected_avg']:.1f}x", 
                             "Próximas 5 carreras", c['accent_cyan']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row2, "TENDENCIA", f"{inc_emoji} {incident_proj['trend'].upper()}", 
                             "", inc_color).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Proyección de puntos
        pts_frame = tk.LabelFrame(main, text=" Proyección de Puntos (8 carreras restantes) ",
                                 bg=c['bg_card'], fg=c['accent_blue'],
                                 font=('Segoe UI', 11, 'bold'),
                                 padx=15, pady=15)
        pts_frame.pack(fill=tk.X, pady=10)
        
        row3 = tk.Frame(pts_frame, bg=c['bg_card'])
        row3.pack(fill=tk.X)
        
        self.create_stat_card(row3, "ACTUAL", str(points_proj['current_total']), 
                             f"Avg: {points_proj['avg_points_per_race']:.0f}/carrera", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row3, "PROYECTADO", str(int(points_proj['projected_total'])), 
                             "Escenario base", c['accent_blue']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row3, "OPTIMISTA", str(int(points_proj['optimistic_total'])), 
                             "Mejor escenario", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row3, "PESIMISTA", str(int(points_proj['pessimistic_total'])), 
                             "Peor escenario", c['accent_red']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Áreas de mejora
        improve_frame = tk.LabelFrame(main, text=" 💡 Áreas de Mejora Recomendadas ",
                                     bg=c['bg_card'], fg=c['accent_yellow'],
                                     font=('Segoe UI', 11, 'bold'),
                                     padx=15, pady=15)
        improve_frame.pack(fill=tk.X, pady=10)
        
        for area in areas:
            severity_color = c['accent_red'] if area['severity'] == 'high' else c['accent_yellow'] if area['severity'] == 'medium' else c['accent_green']
            severity_icon = '🔴' if area['severity'] == 'high' else '🟡' if area['severity'] == 'medium' else '🟢'
            
            area_frame = tk.Frame(improve_frame, bg=c['bg_card'], pady=8)
            area_frame.pack(fill=tk.X)
            
            tk.Label(area_frame, text=f"{severity_icon} {area['area']}", 
                    bg=c['bg_card'], fg=severity_color,
                    font=('Segoe UI', 11, 'bold')).pack(anchor=tk.W)
            tk.Label(area_frame, text=f"   Estado actual: {area['current']}", 
                    bg=c['bg_card'], fg=c['text_secondary'],
                    font=('Segoe UI', 10)).pack(anchor=tk.W)
            tk.Label(area_frame, text=f"   📌 {area['recommendation']}", 
                    bg=c['bg_card'], fg=c['text_muted'],
                    font=('Segoe UI', 10)).pack(anchor=tk.W)
        
        # Gráfico de proyección
        chart_frame = tk.LabelFrame(main, text=" Gráfico de Proyección ",
                                   bg=c['bg_card'], fg=c['accent_blue'],
                                   font=('Segoe UI', 11, 'bold'),
                                   padx=15, pady=15)
        chart_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        if self.chart_gen:
            canvas = self.chart_gen.create_projection_chart(chart_frame)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.active_canvases.append(canvas)
    
    def show_charts(self):
        """Muestra todos los gráficos disponibles"""
        if not self.check_data_loaded():
            return
        
        self.clear_content()
        c = self.colors
        
        main = tk.Frame(self.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        
        self.create_section_title(main, "Galería de Gráficos", "📊")
        
        if not self.chart_gen:
            return
        
        # Gráfico 1: Tendencia de posiciones
        frame1 = tk.LabelFrame(main, text=" Tendencia de Posiciones ",
                              bg=c['bg_card'], fg=c['accent_blue'],
                              font=('Segoe UI', 11, 'bold'),
                              padx=15, pady=15)
        frame1.pack(fill=tk.BOTH, expand=True, pady=10)
        canvas1 = self.chart_gen.create_position_trend_chart(frame1)
        canvas1.draw()
        canvas1.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.active_canvases.append(canvas1)
        
        # Gráfico 2: Incidentes
        frame2 = tk.LabelFrame(main, text=" Incidentes por Carrera ",
                              bg=c['bg_card'], fg=c['accent_blue'],
                              font=('Segoe UI', 11, 'bold'),
                              padx=15, pady=15)
        frame2.pack(fill=tk.BOTH, expand=True, pady=10)
        canvas2 = self.chart_gen.create_incidents_chart(frame2)
        canvas2.draw()
        canvas2.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.active_canvases.append(canvas2)
        
        # Gráfico 3: SOF vs Resultado
        frame3 = tk.LabelFrame(main, text=" SOF vs Resultado ",
                              bg=c['bg_card'], fg=c['accent_blue'],
                              font=('Segoe UI', 11, 'bold'),
                              padx=15, pady=15)
        frame3.pack(fill=tk.BOTH, expand=True, pady=10)
        canvas3 = self.chart_gen.create_sof_vs_finish_chart(frame3)
        canvas3.draw()
        canvas3.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.active_canvases.append(canvas3)
        
        # Gráfico 4: Progresión de puntos
        frame4 = tk.LabelFrame(main, text=" Progresión de Puntos ",
                              bg=c['bg_card'], fg=c['accent_blue'],
                              font=('Segoe UI', 11, 'bold'),
                              padx=15, pady=15)
        frame4.pack(fill=tk.BOTH, expand=True, pady=10)
        canvas4 = self.chart_gen.create_points_progression_chart(frame4)
        canvas4.draw()
        canvas4.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.active_canvases.append(canvas4)
    
    def show_telemetry(self):
        """Análisis de telemetría .ibt con recomendaciones de setup y pilotaje"""
        self.telemetry_view.show()

    def show_progress(self):
        """Evolución por pista/auto y pruebas A/B de setup"""
        self.progress_view.show()

    def show_live_engineer(self):
        """Ingeniero de carrera por voz en tiempo real"""
        self.live_view.show()

    def show_ml_predictions(self):
        """Muestra predicciones basadas en Machine Learning"""
        if not self.check_data_loaded():
            return
        
        self.clear_content()
        c = self.colors
        
        main = tk.Frame(self.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        
        self.create_section_title(main, "🤖 Predicciones con IA", "")
        
        if not self.ml_engine:
            tk.Label(main, text="Motor de IA no inicializado",
                    bg=c['bg_dark'], fg=c['accent_red'],
                    font=('Segoe UI', 14)).pack(pady=20)
            return
        
        # Status del modelo
        if not SKLEARN_AVAILABLE:
            status_frame = tk.LabelFrame(main, text=" ⚠️ scikit-learn no instalado ",
                                        bg=c['bg_card'], fg=c['accent_red'],
                                        font=('Segoe UI', 11, 'bold'),
                                        padx=15, pady=15)
            status_frame.pack(fill=tk.X, pady=10)
            
            tk.Label(status_frame, 
                    text="Para usar predicciones de IA, instala scikit-learn:\n\npip install scikit-learn",
                    bg=c['bg_card'], fg=c['text_secondary'],
                    font=('Segoe UI', 11), justify=tk.LEFT).pack(anchor=tk.W)
            return
        
        # Entrenar modelo si no está entrenado o obtener resultado previo
        if not self.ml_engine.is_trained:
            train_result = self.ml_engine.train_models()
        else:
            # Usar resultado guardado del entrenamiento
            train_result = self.ml_engine.training_result or {
                'success': True, 
                'samples_used': self.ml_engine.samples_used,
                'model_score': self.ml_engine.model_accuracy,
                'total_races_learned': self.ml_engine.total_races_learned
            }
        
        # Información de persistencia del modelo
        model_info = self.ml_engine.get_model_info()
        
        persist_frame = tk.LabelFrame(main, text=" 💾 Persistencia del Modelo ",
                                     bg=c['bg_card'], fg=c['accent_cyan'],
                                     font=('Segoe UI', 11, 'bold'),
                                     padx=15, pady=15)
        persist_frame.pack(fill=tk.X, pady=10)
        
        row_persist = tk.Frame(persist_frame, bg=c['bg_card'])
        row_persist.pack(fill=tk.X)
        
        total_learned = model_info.get('total_races_learned', 0)
        sessions = model_info.get('training_sessions', 0)
        last_date = model_info.get('last_training_date', 'Nunca')
        if last_date and last_date != 'Nunca':
            # Formatear fecha
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(last_date)
                last_date = dt.strftime('%d/%m/%Y %H:%M')
            except:
                pass
        
        disk_status = "✅ Guardado" if model_info.get('model_exists_on_disk') else "❌ No guardado"
        
        self.create_stat_card(row_persist, "TOTAL APRENDIDO", str(total_learned), 
                             "Carreras acumuladas", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_persist, "SESIONES", str(sessions), 
                             "Veces entrenado", c['accent_blue']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_persist, "EN DISCO", disk_status, 
                             "Estado de guardado", c['accent_purple']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Botón para resetear modelo
        btn_frame = tk.Frame(persist_frame, bg=c['bg_card'])
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        tk.Label(btn_frame, text=f"📁 Ubicación: {model_info.get('model_path', 'N/A')}",
                bg=c['bg_card'], fg=c['text_muted'],
                font=('Segoe UI', 9)).pack(side=tk.LEFT)
        
        def reset_model():
            if messagebox.askyesno("Confirmar", "¿Eliminar todo el aprendizaje guardado?\nEl modelo empezará de cero."):
                result = self.ml_engine.delete_saved_model()
                if result.get('success'):
                    messagebox.showinfo("Éxito", "Modelo eliminado. Se entrenará de nuevo al cargar datos.")
                    self.show_ml_predictions()
        
        tk.Button(btn_frame, text="🗑️ Resetear Modelo", command=reset_model,
                 bg=c['accent_red'], fg='white',
                 font=('Segoe UI', 9, 'bold'),
                 relief=tk.FLAT, cursor='hand2').pack(side=tk.RIGHT)
        
        # Estado del modelo
        model_frame = tk.LabelFrame(main, text=" 🧠 Estado del Modelo ML ",
                                   bg=c['bg_card'], fg=c['accent_purple'],
                                   font=('Segoe UI', 11, 'bold'),
                                   padx=15, pady=15)
        model_frame.pack(fill=tk.X, pady=10)
        
        if train_result.get('success'):
            status_color = c['accent_green']
            status_text = "✅ Modelo entrenado y listo"
        else:
            status_color = c['accent_red']
            status_text = f"❌ {train_result.get('message', 'Error')}"
        
        row_model = tk.Frame(model_frame, bg=c['bg_card'])
        row_model.pack(fill=tk.X)
        
        self.create_stat_card(row_model, "ESTADO", status_text, 
                             "", status_color).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        score = train_result.get('model_score', 0)
        score_color = c['accent_green'] if score > 0.6 else c['accent_yellow'] if score > 0.3 else c['accent_red']
        self.create_stat_card(row_model, "PRECISIÓN", f"{score*100:.1f}%", 
                             "Score del modelo", score_color).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_model, "DATOS", str(train_result.get('samples_used', 0)), 
                             "Carreras analizadas", c['accent_cyan']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Importancia de features
        if self.ml_engine.feature_importance:
            feat_frame = tk.LabelFrame(main, text=" 📊 Factores que más influyen en tus resultados ",
                                      bg=c['bg_card'], fg=c['accent_blue'],
                                      font=('Segoe UI', 11, 'bold'),
                                      padx=15, pady=15)
            feat_frame.pack(fill=tk.X, pady=10)
            
            sorted_features = sorted(self.ml_engine.feature_importance.items(), 
                                    key=lambda x: x[1], reverse=True)
            
            for i, (feature, importance) in enumerate(sorted_features[:6]):
                bar_frame = tk.Frame(feat_frame, bg=c['bg_card'])
                bar_frame.pack(fill=tk.X, pady=3)
                
                tk.Label(bar_frame, text=feature, width=15, anchor='w',
                        bg=c['bg_card'], fg=c['text_primary'],
                        font=('Segoe UI', 10)).pack(side=tk.LEFT)
                
                # Barra de progreso visual
                bar_width = int(importance * 300)
                bar_color = c['accent_blue'] if i < 2 else c['accent_cyan'] if i < 4 else c['accent_purple']
                
                bar = tk.Frame(bar_frame, height=18, width=bar_width, bg=bar_color)
                bar.pack(side=tk.LEFT, padx=10)
                
                tk.Label(bar_frame, text=f"{importance*100:.1f}%",
                        bg=c['bg_card'], fg=c['text_muted'],
                        font=('Segoe UI', 9)).pack(side=tk.LEFT)
        
        # Predicción próxima carrera
        pred_frame = tk.LabelFrame(main, text=" 🔮 Predicción Próxima Carrera ",
                                  bg=c['bg_card'], fg=c['accent_green'],
                                  font=('Segoe UI', 11, 'bold'),
                                  padx=15, pady=15)
        pred_frame.pack(fill=tk.X, pady=10)
        
        prediction = self.ml_engine.predict_next_race()
        
        row_pred = tk.Frame(pred_frame, bg=c['bg_card'])
        row_pred.pack(fill=tk.X)
        
        self.create_stat_card(row_pred, "POSICIÓN PREDICHA", f"P{prediction.predicted_position:.0f}", 
                             f"Rango: P{prediction.position_range[0]:.0f}-P{prediction.position_range[1]:.0f}", 
                             c['accent_blue']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_pred, "INCIDENTES", f"{prediction.predicted_incidents:.1f}x", 
                             "Estimados", c['accent_orange']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_pred, "PUNTOS", str(prediction.predicted_points), 
                             "Estimados", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_pred, "CONFIANZA", f"{prediction.confidence*100:.0f}%", 
                             "Del modelo", c['accent_purple']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Proyección de temporada
        season_frame = tk.LabelFrame(main, text=" 📅 Proyección Resto de Temporada (8 carreras) ",
                                    bg=c['bg_card'], fg=c['accent_yellow'],
                                    font=('Segoe UI', 11, 'bold'),
                                    padx=15, pady=15)
        season_frame.pack(fill=tk.X, pady=10)
        
        season = self.ml_engine.predict_season(8)
        
        row_season = tk.Frame(season_frame, bg=c['bg_card'])
        row_season.pack(fill=tk.X)
        
        self.create_stat_card(row_season, "PUNTOS ACTUALES", str(season['current_points']), 
                             "", c['accent_cyan']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_season, "PROYECTADO", str(season['total_projected']), 
                             f"+{season['projected_points']} pts", c['accent_blue']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_season, "MEJOR CASO", str(season['current_points'] + season['best_case_points']), 
                             "", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_season, "PEOR CASO", str(season['current_points'] + season['worst_case_points']), 
                             "", c['accent_red']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_season, "POS. MEDIA", f"P{season['avg_predicted_position']:.1f}", 
                             "Proyectada", c['accent_purple']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Insights de IA
        insights_frame = tk.LabelFrame(main, text=" 💡 Insights del Modelo ",
                                      bg=c['bg_card'], fg=c['accent_cyan'],
                                      font=('Segoe UI', 11, 'bold'),
                                      padx=15, pady=15)
        insights_frame.pack(fill=tk.X, pady=10)
        
        insights = self.ml_engine.generate_insights()
        
        for insight in insights[:5]:
            insight_frame = tk.Frame(insights_frame, bg=c['bg_card'], pady=6)
            insight_frame.pack(fill=tk.X)
            
            impact_icon = '🟢' if insight.impact == 'positive' else '🔴' if insight.impact == 'negative' else '🔵'
            impact_color = c['accent_green'] if insight.impact == 'positive' else c['accent_red'] if insight.impact == 'negative' else c['accent_blue']
            
            tk.Label(insight_frame, text=f"{impact_icon} {insight.title} ({insight.category})", 
                    bg=c['bg_card'], fg=impact_color,
                    font=('Segoe UI', 11, 'bold')).pack(anchor=tk.W)
            tk.Label(insight_frame, text=f"   {insight.description}", 
                    bg=c['bg_card'], fg=c['text_secondary'],
                    font=('Segoe UI', 10)).pack(anchor=tk.W)
        
        # Recomendaciones
        rec_frame = tk.LabelFrame(main, text=" 🎯 Recomendaciones Personalizadas ",
                                 bg=c['bg_card'], fg=c['accent_orange'],
                                 font=('Segoe UI', 11, 'bold'),
                                 padx=15, pady=15)
        rec_frame.pack(fill=tk.X, pady=10)
        
        recommendations = self.ml_engine.get_recommendations()
        
        for rec in recommendations[:4]:
            rec_inner = tk.Frame(rec_frame, bg=c['bg_card'], pady=6)
            rec_inner.pack(fill=tk.X)
            
            priority_color = c['accent_red'] if rec['priority'] == 'alta' else c['accent_yellow'] if rec['priority'] == 'media' else c['accent_green']
            priority_icon = '🔴' if rec['priority'] == 'alta' else '🟡' if rec['priority'] == 'media' else '🟢'
            
            tk.Label(rec_inner, text=f"{priority_icon} [{rec['priority'].upper()}] {rec['area']}", 
                    bg=c['bg_card'], fg=priority_color,
                    font=('Segoe UI', 11, 'bold')).pack(anchor=tk.W)
            tk.Label(rec_inner, text=f"   📌 {rec['action']}", 
                    bg=c['bg_card'], fg=c['text_primary'],
                    font=('Segoe UI', 10)).pack(anchor=tk.W)
            if rec.get('expected_impact'):
                tk.Label(rec_inner, text=f"   💫 Impacto esperado: {rec['expected_impact']}", 
                        bg=c['bg_card'], fg=c['text_muted'],
                        font=('Segoe UI', 9)).pack(anchor=tk.W)
    
    def show_pattern_analysis(self):
        """Muestra análisis de patrones detectados por IA"""
        if not self.check_data_loaded():
            return
        
        self.clear_content()
        c = self.colors
        
        main = tk.Frame(self.scrollable_frame, bg=c['bg_dark'], padx=20, pady=20)
        main.pack(fill=tk.BOTH, expand=True)
        
        self.create_section_title(main, "🔍 Análisis de Patrones", "")
        
        if not self.ml_engine:
            tk.Label(main, text="Motor de IA no inicializado",
                    bg=c['bg_dark'], fg=c['accent_red'],
                    font=('Segoe UI', 14)).pack(pady=20)
            return
        
        # Análisis de patrones
        patterns_result = self.ml_engine.analyze_patterns()
        patterns = patterns_result.get('patterns', [])
        
        # Header con resumen
        summary_frame = tk.LabelFrame(main, text=" 📈 Resumen del Análisis ",
                                     bg=c['bg_card'], fg=c['accent_blue'],
                                     font=('Segoe UI', 11, 'bold'),
                                     padx=15, pady=15)
        summary_frame.pack(fill=tk.X, pady=10)
        
        row_sum = tk.Frame(summary_frame, bg=c['bg_card'])
        row_sum.pack(fill=tk.X)
        
        self.create_stat_card(row_sum, "CARRERAS", str(patterns_result.get('total_races_analyzed', 0)), 
                             "Analizadas", c['accent_blue']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_sum, "PATRONES", str(len(patterns)), 
                             "Encontrados", c['accent_green']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        # Patrones encontrados
        if not patterns:
            tk.Label(main, text="No se encontraron patrones significativos.\nNecesitas más carreras para un análisis más profundo.",
                    bg=c['bg_dark'], fg=c['text_secondary'],
                    font=('Segoe UI', 12), justify=tk.CENTER).pack(pady=30)
        else:
            for pattern in patterns:
                pattern_type = pattern.get('type', 'general')
                
                # Color según tipo
                type_colors = {
                    'correlation': c['accent_purple'],
                    'streak': c['accent_yellow'],
                    'series': c['accent_cyan'],
                    'track': c['accent_green'],
                    'trend': c['accent_blue']
                }
                type_icons = {
                    'correlation': '📊',
                    'streak': '🔥',
                    'series': '🏆',
                    'track': '🛣️',
                    'trend': '📈'
                }
                
                frame_color = type_colors.get(pattern_type, c['accent_blue'])
                icon = type_icons.get(pattern_type, '📌')
                
                pat_frame = tk.LabelFrame(main, text=f" {icon} {pattern.get('name', 'Patrón')} ",
                                         bg=c['bg_card'], fg=frame_color,
                                         font=('Segoe UI', 11, 'bold'),
                                         padx=15, pady=15)
                pat_frame.pack(fill=tk.X, pady=8)
                
                # Finding
                tk.Label(pat_frame, text=f"🔍 Hallazgo: {pattern.get('finding', 'N/A')}", 
                        bg=c['bg_card'], fg=c['text_primary'],
                        font=('Segoe UI', 11)).pack(anchor=tk.W, pady=3)
                
                # Correlación si existe
                if 'correlation' in pattern:
                    corr = pattern['correlation']
                    corr_color = c['accent_green'] if corr < 0 else c['accent_red']
                    tk.Label(pat_frame, text=f"📉 Correlación: {corr:.2f}", 
                            bg=c['bg_card'], fg=corr_color,
                            font=('Segoe UI', 10)).pack(anchor=tk.W, pady=2)
                
                # Recomendación
                if 'recommendation' in pattern:
                    tk.Label(pat_frame, text=f"💡 Recomendación: {pattern['recommendation']}", 
                            bg=c['bg_card'], fg=c['accent_yellow'],
                            font=('Segoe UI', 10, 'italic')).pack(anchor=tk.W, pady=3)
        
        # iRating estimado
        ir_frame = tk.LabelFrame(main, text=" 📊 Estimación de iRating ",
                                bg=c['bg_card'], fg=c['accent_cyan'],
                                font=('Segoe UI', 11, 'bold'),
                                padx=15, pady=15)
        ir_frame.pack(fill=tk.X, pady=15)
        
        ir_estimate = self.projection.calculate_irating_estimate()
        
        row_ir = tk.Frame(ir_frame, bg=c['bg_card'])
        row_ir.pack(fill=tk.X)
        
        ir_trend = ir_estimate.get('trend', 'unknown')
        ir_color = c['accent_green'] if ir_trend == 'rising' else c['accent_red'] if ir_trend == 'falling' else c['accent_yellow']
        ir_icon = '📈' if ir_trend == 'rising' else '📉' if ir_trend == 'falling' else '➡️'
        
        self.create_stat_card(row_ir, "iRATING ESTIMADO", str(int(ir_estimate.get('estimated_irating', 0))), 
                             "Basado en SOF y resultados", c['accent_blue']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_ir, "RECIENTE", str(int(ir_estimate.get('recent_estimate', 0))), 
                             "Última carrera", c['accent_cyan']).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.create_stat_card(row_ir, "TENDENCIA", f"{ir_icon} {ir_trend.upper()}", 
                             "", ir_color).pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        
        tk.Label(ir_frame, 
                text="⚠️ Nota: Esta es una estimación aproximada, no el iRating real de iRacing",
                bg=c['bg_card'], fg=c['text_muted'],
                font=('Segoe UI', 9, 'italic')).pack(pady=(10, 0))


def main():
    """Función principal para iniciar la aplicación"""
    root = tk.Tk()
    app = IracingStatsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
