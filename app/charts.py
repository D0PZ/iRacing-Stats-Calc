"""
Módulo para generación de gráficos
"""

import matplotlib
matplotlib.use('TkAgg')  # Usar backend TkAgg
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from typing import List, Dict, Any, Optional
from datetime import datetime
import statistics as stats_module


class ChartGenerator:
    """Generador de gráficos para estadísticas de iRacing"""
    
    # Colores del tema oscuro
    COLORS = {
        'primary': '#58A6FF',
        'success': '#3FB950',
        'warning': '#D29922',
        'danger': '#F85149',
        'info': '#39C5CF',
        'secondary': '#8B949E',
        'background': '#21262D',
        'text': '#F0F6FC',
        'grid': '#30363D',
        'purple': '#A371F7',
        'orange': '#F0883E'
    }
    
    def __init__(self, stats_calculator, projection_engine):
        self.stats_calc = stats_calculator
        self.projection = projection_engine
    
    def create_position_trend_chart(self, parent_frame) -> FigureCanvasTkAgg:
        """Crea gráfico de tendencia de posiciones"""
        trend = self.stats_calc.get_performance_trend()
        
        fig = Figure(figsize=(8, 4), dpi=100)
        fig.patch.set_facecolor(self.COLORS['background'])
        ax = fig.add_subplot(111)
        ax.set_facecolor(self.COLORS['background'])
        
        if not trend:
            ax.text(0.5, 0.5, 'No hay datos de carreras', 
                   ha='center', va='center', fontsize=12, color=self.COLORS['text'])
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
        else:
            races = list(range(1, len(trend) + 1))
            starts = [t['start'] if t['start'] > 0 else None for t in trend]
            finishes = [t['finish'] if t['finish'] > 0 else None for t in trend]
            
            # Filtrar None values para el plot
            valid_starts = [(r, s) for r, s in zip(races, starts) if s is not None]
            valid_finishes = [(r, f) for r, f in zip(races, finishes) if f is not None]
            
            if valid_starts:
                ax.plot([x[0] for x in valid_starts], [x[1] for x in valid_starts], 
                       'o--', color=self.COLORS['secondary'], label='Inicio', 
                       markersize=6, alpha=0.7)
            
            if valid_finishes:
                ax.plot([x[0] for x in valid_finishes], [x[1] for x in valid_finishes], 
                       'o-', color=self.COLORS['primary'], label='Final', 
                       markersize=8, linewidth=2)
                
                # Añadir línea de tendencia
                if len(valid_finishes) >= 3:
                    finish_values = [x[1] for x in valid_finishes]
                    slope, intercept = self.projection.calculate_linear_regression(finish_values)
                    trend_line = [slope * i + intercept for i in range(len(finish_values))]
                    ax.plot([x[0] for x in valid_finishes], trend_line, 
                           '--', color=self.COLORS['warning'], alpha=0.5, 
                           label='Tendencia', linewidth=1.5)
            
            ax.set_xlabel('Carrera #', color=self.COLORS['text'])
            ax.set_ylabel('Posición', color=self.COLORS['text'])
            ax.set_title('Tendencia de Posiciones', color=self.COLORS['text'])
            ax.legend(loc='upper right', fontsize=8, facecolor=self.COLORS['background'], 
                     labelcolor=self.COLORS['text'], edgecolor=self.COLORS['grid'])
            ax.invert_yaxis()  # Menor posición = mejor
            ax.grid(True, alpha=0.3, color=self.COLORS['grid'])
            ax.tick_params(colors=self.COLORS['text'])
            for spine in ax.spines.values():
                spine.set_color(self.COLORS['grid'])
        
        fig.tight_layout()
        
        canvas = FigureCanvasTkAgg(fig, parent_frame)
        return canvas
    
    def create_incidents_chart(self, parent_frame) -> FigureCanvasTkAgg:
        """Crea gráfico de incidentes por carrera"""
        trend = self.stats_calc.get_performance_trend()
        
        fig = Figure(figsize=(8, 4), dpi=100)
        fig.patch.set_facecolor(self.COLORS['background'])
        ax = fig.add_subplot(111)
        ax.set_facecolor(self.COLORS['background'])
        
        if not trend:
            ax.text(0.5, 0.5, 'No hay datos de carreras', 
                   ha='center', va='center', fontsize=12, color=self.COLORS['text'])
        else:
            races = list(range(1, len(trend) + 1))
            incidents = [t['incidents'] for t in trend]
            
            # Colorear barras según nivel de incidentes
            colors = []
            for inc in incidents:
                if inc <= 4:
                    colors.append(self.COLORS['success'])
                elif inc <= 8:
                    colors.append(self.COLORS['warning'])
                else:
                    colors.append(self.COLORS['danger'])
            
            ax.bar(races, incidents, color=colors, alpha=0.8, edgecolor=self.COLORS['grid'])
            
            # Línea de promedio
            if incidents:
                avg = sum(incidents) / len(incidents)
                ax.axhline(y=avg, color=self.COLORS['info'], linestyle='--', 
                          label=f'Promedio: {avg:.1f}x', linewidth=2)
                
                # Línea objetivo (4x)
                ax.axhline(y=4, color=self.COLORS['success'], linestyle=':', 
                          label='Objetivo: 4x', alpha=0.7)
            
            ax.set_xlabel('Carrera #', color=self.COLORS['text'])
            ax.set_ylabel('Incidentes', color=self.COLORS['text'])
            ax.set_title('Incidentes por Carrera', color=self.COLORS['text'])
            ax.legend(loc='upper right', fontsize=8, facecolor=self.COLORS['background'],
                     labelcolor=self.COLORS['text'], edgecolor=self.COLORS['grid'])
            ax.grid(True, alpha=0.3, axis='y', color=self.COLORS['grid'])
            ax.tick_params(colors=self.COLORS['text'])
            for spine in ax.spines.values():
                spine.set_color(self.COLORS['grid'])
        
        fig.tight_layout()
        
        canvas = FigureCanvasTkAgg(fig, parent_frame)
        return canvas
    
    def create_sof_vs_finish_chart(self, parent_frame) -> FigureCanvasTkAgg:
        """Crea gráfico de SOF vs Resultado"""
        trend = self.stats_calc.get_performance_trend()
        
        fig = Figure(figsize=(8, 4), dpi=100)
        fig.patch.set_facecolor(self.COLORS['background'])
        ax = fig.add_subplot(111)
        ax.set_facecolor(self.COLORS['background'])
        
        valid_data = [(t['sof'], t['finish']) for t in trend 
                      if t['sof'] > 0 and t['finish'] > 0]
        
        if not valid_data:
            ax.text(0.5, 0.5, 'No hay datos suficientes', 
                   ha='center', va='center', fontsize=12, color=self.COLORS['text'])
        else:
            sofs = [d[0] for d in valid_data]
            finishes = [d[1] for d in valid_data]
            
            # Scatter plot con tamaño basado en posición
            sizes = [100 / f * 10 for f in finishes]  # Mejor posición = punto más grande
            
            scatter = ax.scatter(sofs, finishes, c=finishes, cmap='RdYlGn_r', 
                               s=sizes, alpha=0.7, edgecolors=self.COLORS['grid'])
            
            ax.set_xlabel('Strength of Field (SOF)', color=self.COLORS['text'])
            ax.set_ylabel('Posición Final', color=self.COLORS['text'])
            ax.set_title('SOF vs Resultado', color=self.COLORS['text'])
            ax.invert_yaxis()
            ax.grid(True, alpha=0.3, color=self.COLORS['grid'])
            ax.tick_params(colors=self.COLORS['text'])
            for spine in ax.spines.values():
                spine.set_color(self.COLORS['grid'])
            
            # Colorbar
            cbar = fig.colorbar(scatter, ax=ax)
            cbar.set_label('Posición', color=self.COLORS['text'])
            cbar.ax.yaxis.set_tick_params(color=self.COLORS['text'])
            cbar.outline.set_edgecolor(self.COLORS['grid'])
            plt.setp(plt.getp(cbar.ax.axes, 'yticklabels'), color=self.COLORS['text'])
        
        fig.tight_layout()
        
        canvas = FigureCanvasTkAgg(fig, parent_frame)
        return canvas
    
    def create_series_distribution_chart(self, parent_frame) -> FigureCanvasTkAgg:
        """Crea gráfico de distribución por serie"""
        series_stats = self.stats_calc.calculate_stats_by_series()
        
        fig = Figure(figsize=(8, 4), dpi=100)
        fig.patch.set_facecolor(self.COLORS['background'])
        ax = fig.add_subplot(111)
        ax.set_facecolor(self.COLORS['background'])
        
        if not series_stats:
            ax.text(0.5, 0.5, 'No hay datos de series', 
                   ha='center', va='center', fontsize=12, color=self.COLORS['text'])
        else:
            # Preparar datos
            series_names = []
            race_counts = []
            avg_finishes = []
            
            for series, stats in series_stats.items():
                # Acortar nombres largos
                short_name = series[:25] + '...' if len(series) > 25 else series
                series_names.append(short_name)
                race_counts.append(stats.total_races)
                avg_finishes.append(stats.avg_finish)
            
            # Crear gráfico de barras horizontal
            y_pos = range(len(series_names))
            
            ax.barh(y_pos, race_counts, color=self.COLORS['primary'], alpha=0.8)
            
            # Añadir promedio de posición como texto
            for i, (count, avg) in enumerate(zip(race_counts, avg_finishes)):
                ax.text(count + 0.1, i, f'Avg: P{avg:.1f}', va='center', fontsize=8, 
                       color=self.COLORS['text'])
            
            ax.set_yticks(list(y_pos))
            ax.set_yticklabels(series_names, fontsize=8, color=self.COLORS['text'])
            ax.set_xlabel('Número de Carreras', color=self.COLORS['text'])
            ax.set_title('Carreras por Serie', color=self.COLORS['text'])
            ax.grid(True, alpha=0.3, axis='x', color=self.COLORS['grid'])
            ax.tick_params(colors=self.COLORS['text'])
            for spine in ax.spines.values():
                spine.set_color(self.COLORS['grid'])
        
        fig.tight_layout()
        
        canvas = FigureCanvasTkAgg(fig, parent_frame)
        return canvas
    
    def create_points_progression_chart(self, parent_frame) -> FigureCanvasTkAgg:
        """Crea gráfico de progresión de puntos"""
        trend = self.stats_calc.get_performance_trend()
        
        fig = Figure(figsize=(8, 4), dpi=100)
        fig.patch.set_facecolor(self.COLORS['background'])
        ax = fig.add_subplot(111)
        ax.set_facecolor(self.COLORS['background'])
        
        points_data = [(i+1, t['points']) for i, t in enumerate(trend) if t['points'] > 0]
        
        if not points_data:
            ax.text(0.5, 0.5, 'No hay datos de puntos', 
                   ha='center', va='center', fontsize=12, color=self.COLORS['text'])
        else:
            races = [d[0] for d in points_data]
            points = [d[1] for d in points_data]
            cumulative = []
            total = 0
            for p in points:
                total += p
                cumulative.append(total)
            
            # Puntos por carrera (barras)
            ax.bar(races, points, color=self.COLORS['success'], alpha=0.6, 
                  label='Puntos por carrera')
            
            # Puntos acumulados (línea)
            ax2 = ax.twinx()
            ax2.plot(races, cumulative, 'o-', color=self.COLORS['orange'], 
                    linewidth=2, markersize=6, label='Acumulado')
            ax2.set_ylabel('Puntos Acumulados', color=self.COLORS['orange'])
            ax2.tick_params(axis='y', colors=self.COLORS['orange'])
            for spine in ax2.spines.values():
                spine.set_color(self.COLORS['grid'])
            
            ax.set_xlabel('Carrera #', color=self.COLORS['text'])
            ax.set_ylabel('Puntos por Carrera', color=self.COLORS['success'])
            ax.set_title('Progresión de Puntos de Campeonato', color=self.COLORS['text'])
            ax.grid(True, alpha=0.3, color=self.COLORS['grid'])
            ax.tick_params(axis='x', colors=self.COLORS['text'])
            ax.tick_params(axis='y', colors=self.COLORS['success'])
            for spine in ax.spines.values():
                spine.set_color(self.COLORS['grid'])
            
            # Leyenda combinada
            lines1, labels1 = ax.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax.legend(lines1 + lines2, labels1 + labels2, loc='upper left', fontsize=8,
                     facecolor=self.COLORS['background'], labelcolor=self.COLORS['text'],
                     edgecolor=self.COLORS['grid'])
        
        fig.tight_layout()
        
        canvas = FigureCanvasTkAgg(fig, parent_frame)
        return canvas
    
    def create_projection_chart(self, parent_frame) -> FigureCanvasTkAgg:
        """Crea gráfico de proyección de posiciones"""
        trend = self.stats_calc.get_performance_trend()
        projection = self.projection.project_finish_position(5)
        
        fig = Figure(figsize=(8, 4), dpi=100)
        fig.patch.set_facecolor(self.COLORS['background'])
        ax = fig.add_subplot(111)
        ax.set_facecolor(self.COLORS['background'])
        
        if not trend or projection['trend'] == 'insufficient_data':
            ax.text(0.5, 0.5, 'Datos insuficientes para proyección', 
                   ha='center', va='center', fontsize=12, color=self.COLORS['text'])
        else:
            # Datos históricos
            finishes = [t['finish'] for t in trend if t['finish'] > 0]
            races_hist = list(range(1, len(finishes) + 1))
            
            # Proyecciones
            projections = projection['projections']
            races_proj = list(range(len(finishes) + 1, len(finishes) + len(projections) + 1))
            
            # Plot histórico
            ax.plot(races_hist, finishes, 'o-', color=self.COLORS['primary'], 
                   linewidth=2, markersize=6, label='Resultados')
            
            # Plot proyección
            if projections:
                # Conectar con último punto histórico
                all_proj_x = [races_hist[-1]] + races_proj
                all_proj_y = [finishes[-1]] + projections
                
                ax.plot(all_proj_x, all_proj_y, 'o--', color=self.COLORS['warning'], 
                       linewidth=2, markersize=6, alpha=0.7, label='Proyección')
                
                # Área de incertidumbre
                if len(finishes) > 1:
                    std = stats_module.stdev(finishes)
                    upper = [p + std for p in projections]
                    lower = [max(1, p - std) for p in projections]
                    ax.fill_between(races_proj, lower, upper, alpha=0.2, 
                                   color=self.COLORS['warning'])
            
            ax.set_xlabel('Carrera #', color=self.COLORS['text'])
            ax.set_ylabel('Posición', color=self.COLORS['text'])
            ax.set_title(f'Proyección de Resultados (Tendencia: {projection["trend"]})', 
                        color=self.COLORS['text'])
            ax.legend(loc='upper right', fontsize=8, facecolor=self.COLORS['background'],
                     labelcolor=self.COLORS['text'], edgecolor=self.COLORS['grid'])
            ax.invert_yaxis()
            ax.grid(True, alpha=0.3, color=self.COLORS['grid'])
            ax.tick_params(colors=self.COLORS['text'])
            for spine in ax.spines.values():
                spine.set_color(self.COLORS['grid'])
            
            # Añadir línea vertical separando histórico de proyección
            ax.axvline(x=len(finishes) + 0.5, color=self.COLORS['secondary'], 
                      linestyle=':', alpha=0.5)
        
        fig.tight_layout()
        
        canvas = FigureCanvasTkAgg(fig, parent_frame)
        return canvas
    
    def create_pie_chart_results(self, parent_frame) -> FigureCanvasTkAgg:
        """Crea gráfico circular de distribución de resultados"""
        stats = self.stats_calc.calculate_race_stats()
        
        fig = Figure(figsize=(6, 4), dpi=100)
        fig.patch.set_facecolor(self.COLORS['background'])
        ax = fig.add_subplot(111)
        ax.set_facecolor(self.COLORS['background'])
        
        if stats.total_races == 0:
            ax.text(0.5, 0.5, 'No hay datos', ha='center', va='center', fontsize=12,
                   color=self.COLORS['text'])
        else:
            labels = []
            sizes = []
            colors = []
            
            if stats.podiums > 0:
                labels.append(f'Podio ({stats.podiums})')
                sizes.append(stats.podiums)
                colors.append(self.COLORS['success'])
            
            top5_only = stats.top5 - stats.podiums
            if top5_only > 0:
                labels.append(f'Top 5 ({top5_only})')
                sizes.append(top5_only)
                colors.append(self.COLORS['info'])
            
            top10_only = stats.top10 - stats.top5
            if top10_only > 0:
                labels.append(f'Top 10 ({top10_only})')
                sizes.append(top10_only)
                colors.append(self.COLORS['primary'])
            
            outside_top10 = stats.total_races - stats.top10 - stats.dnf
            if outside_top10 > 0:
                labels.append(f'Fuera Top 10 ({outside_top10})')
                sizes.append(outside_top10)
                colors.append(self.COLORS['warning'])
            
            if stats.dnf > 0:
                labels.append(f'DNF ({stats.dnf})')
                sizes.append(stats.dnf)
                colors.append(self.COLORS['danger'])
            
            if sizes:
                wedges, texts, autotexts = ax.pie(sizes, labels=labels, colors=colors, 
                      autopct='%1.0f%%', startangle=90, explode=[0.02] * len(sizes),
                      textprops={'color': self.COLORS['text']})
                for autotext in autotexts:
                    autotext.set_color(self.COLORS['background'])
                    autotext.set_fontweight('bold')
                ax.set_title('Distribución de Resultados', color=self.COLORS['text'])
        
        fig.tight_layout()
        
        canvas = FigureCanvasTkAgg(fig, parent_frame)
        return canvas
