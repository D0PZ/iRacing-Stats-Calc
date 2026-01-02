"""
Módulo para proyecciones y análisis de tendencias
"""

from typing import List, Dict, Any, Tuple
import statistics
from datetime import datetime, timedelta


class ProjectionEngine:
    """Motor de proyecciones para estadísticas de iRacing"""
    
    def __init__(self, stats_calculator):
        self.stats_calc = stats_calculator
    
    def calculate_linear_regression(self, values: List[float]) -> Tuple[float, float]:
        """
        Calcula regresión lineal simple
        
        Args:
            values: Lista de valores
            
        Returns:
            Tuple (pendiente, intercepto)
        """
        if len(values) < 2:
            return (0.0, values[0] if values else 0.0)
        
        n = len(values)
        x = list(range(n))
        
        x_mean = statistics.mean(x)
        y_mean = statistics.mean(values)
        
        numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        
        if denominator == 0:
            return (0.0, y_mean)
        
        slope = numerator / denominator
        intercept = y_mean - slope * x_mean
        
        return (slope, intercept)
    
    def project_finish_position(self, num_races_ahead: int = 5) -> Dict[str, Any]:
        """
        Proyecta posiciones de llegada futuras
        
        Args:
            num_races_ahead: Número de carreras a proyectar
            
        Returns:
            Diccionario con proyecciones
        """
        trend = self.stats_calc.get_performance_trend()
        
        if len(trend) < 2:
            return {
                'current_avg': 0,
                'projected_avg': 0,
                'trend': 'insufficient_data',
                'confidence': 'low',
                'projections': []
            }
        
        # Obtener posiciones de llegada válidas
        positions = [t['finish'] for t in trend if t['finish'] > 0]
        
        if len(positions) < 2:
            return {
                'current_avg': positions[0] if positions else 0,
                'projected_avg': positions[0] if positions else 0,
                'trend': 'insufficient_data',
                'confidence': 'low',
                'projections': []
            }
        
        slope, intercept = self.calculate_linear_regression(positions)
        
        current_avg = statistics.mean(positions[-5:]) if len(positions) >= 5 else statistics.mean(positions)
        
        # Proyectar posiciones futuras
        projections = []
        n = len(positions)
        for i in range(1, num_races_ahead + 1):
            projected = slope * (n + i) + intercept
            projected = max(1, projected)  # No puede ser menor a 1
            projections.append(round(projected, 1))
        
        projected_avg = statistics.mean(projections) if projections else current_avg
        
        # Determinar tendencia
        if slope < -0.1:
            trend_direction = 'improving'
        elif slope > 0.1:
            trend_direction = 'declining'
        else:
            trend_direction = 'stable'
        
        # Calcular confianza basada en consistencia
        if len(positions) >= 10:
            confidence = 'high'
        elif len(positions) >= 5:
            confidence = 'medium'
        else:
            confidence = 'low'
        
        return {
            'current_avg': round(current_avg, 2),
            'projected_avg': round(projected_avg, 2),
            'trend': trend_direction,
            'slope': round(slope, 4),
            'confidence': confidence,
            'projections': projections,
            'races_analyzed': len(positions)
        }
    
    def project_incidents(self, num_races_ahead: int = 5) -> Dict[str, Any]:
        """Proyecta incidentes futuros"""
        trend = self.stats_calc.get_performance_trend()
        
        if len(trend) < 2:
            return {
                'current_avg': 0,
                'projected_avg': 0,
                'trend': 'insufficient_data',
                'projections': []
            }
        
        incidents = [t['incidents'] for t in trend]
        
        slope, intercept = self.calculate_linear_regression(incidents)
        
        current_avg = statistics.mean(incidents[-5:]) if len(incidents) >= 5 else statistics.mean(incidents)
        
        # Proyectar incidentes futuros
        projections = []
        n = len(incidents)
        for i in range(1, num_races_ahead + 1):
            projected = slope * (n + i) + intercept
            projected = max(0, projected)  # No puede ser negativo
            projections.append(round(projected, 1))
        
        projected_avg = statistics.mean(projections) if projections else current_avg
        
        # Determinar tendencia
        if slope < -0.2:
            trend_direction = 'improving'
        elif slope > 0.2:
            trend_direction = 'worsening'
        else:
            trend_direction = 'stable'
        
        return {
            'current_avg': round(current_avg, 2),
            'projected_avg': round(projected_avg, 2),
            'trend': trend_direction,
            'slope': round(slope, 4),
            'projections': projections
        }
    
    def project_championship_points(self, races_remaining: int = 8) -> Dict[str, Any]:
        """Proyecta puntos de campeonato"""
        trend = self.stats_calc.get_performance_trend()
        
        if not trend:
            return {
                'current_total': 0,
                'projected_total': 0,
                'avg_points_per_race': 0
            }
        
        points = [t['points'] for t in trend if t['points'] > 0]
        
        if not points:
            return {
                'current_total': 0,
                'projected_total': 0,
                'avg_points_per_race': 0
            }
        
        current_total = sum(points)
        avg_points = statistics.mean(points)
        
        # Proyección simple basada en promedio
        projected_additional = avg_points * races_remaining
        projected_total = current_total + projected_additional
        
        # Proyección optimista (basada en mejores carreras)
        if len(points) >= 3:
            best_races = sorted(points, reverse=True)[:3]
            optimistic_avg = statistics.mean(best_races)
            optimistic_total = current_total + (optimistic_avg * races_remaining)
        else:
            optimistic_total = projected_total
        
        # Proyección pesimista (basada en peores carreras)
        if len(points) >= 3:
            worst_races = sorted(points)[:3]
            pessimistic_avg = statistics.mean(worst_races)
            pessimistic_total = current_total + (pessimistic_avg * races_remaining)
        else:
            pessimistic_total = projected_total
        
        return {
            'current_total': current_total,
            'avg_points_per_race': round(avg_points, 1),
            'projected_total': round(projected_total, 0),
            'optimistic_total': round(optimistic_total, 0),
            'pessimistic_total': round(pessimistic_total, 0),
            'races_counted': len(points),
            'races_remaining': races_remaining
        }
    
    def calculate_irating_estimate(self) -> Dict[str, Any]:
        """
        Estima el iRating basado en SOF y resultados
        (Nota: Esta es una estimación, no el valor real)
        """
        trend = self.stats_calc.get_performance_trend()
        
        if not trend:
            return {'estimated_irating': 0, 'trend': 'unknown'}
        
        # Filtrar carreras con SOF válido
        valid_races = [t for t in trend if t['sof'] > 0 and t['finish'] > 0]
        
        if not valid_races:
            return {'estimated_irating': 0, 'trend': 'unknown'}
        
        # Estimación simple: SOF ajustado por posición relativa
        estimates = []
        for race in valid_races:
            sof = race['sof']
            # Asumiendo ~20 participantes promedio
            avg_position = 10
            finish = race['finish']
            
            # Si terminó mejor que promedio, iRating > SOF, y viceversa
            adjustment = (avg_position - finish) * 20  # ~20 puntos por posición
            estimated = sof + adjustment
            estimates.append(estimated)
        
        current_estimate = estimates[-1] if estimates else 0
        avg_estimate = statistics.mean(estimates) if estimates else 0
        
        # Tendencia
        if len(estimates) >= 3:
            recent = statistics.mean(estimates[-3:])
            older = statistics.mean(estimates[:3]) if len(estimates) >= 6 else estimates[0]
            
            if recent > older + 50:
                ir_trend = 'rising'
            elif recent < older - 50:
                ir_trend = 'falling'
            else:
                ir_trend = 'stable'
        else:
            ir_trend = 'insufficient_data'
        
        return {
            'estimated_irating': round(avg_estimate, 0),
            'recent_estimate': round(current_estimate, 0),
            'trend': ir_trend,
            'estimates_history': [round(e, 0) for e in estimates]
        }
    
    def get_improvement_areas(self) -> List[Dict[str, Any]]:
        """Identifica áreas de mejora"""
        areas = []
        
        # Análisis de incidentes
        incident_analysis = self.stats_calc.get_incident_analysis()
        if incident_analysis.get('avg_incidents', 0) > 6:
            areas.append({
                'area': 'Incidentes',
                'severity': 'high' if incident_analysis['avg_incidents'] > 10 else 'medium',
                'current': f"{incident_analysis['avg_incidents']:.1f}x promedio",
                'recommendation': 'Enfocarse en carreras limpias. Objetivo: menos de 4x por carrera.'
            })
        
        # Análisis de clasificación
        quali_analysis = self.stats_calc.get_qualifying_analysis()
        if quali_analysis.get('improvement_rate', 0) < 40:
            areas.append({
                'area': 'Clasificación',
                'severity': 'medium',
                'current': f"{quali_analysis['improvement_rate']:.0f}% mejora en carrera",
                'recommendation': 'Mejorar posición de salida para reducir riesgo en primeras vueltas.'
            })
        
        # Análisis de consistencia
        stats = self.stats_calc.calculate_race_stats()
        if stats.consistency > 5:
            areas.append({
                'area': 'Consistencia',
                'severity': 'medium',
                'current': f"Desviación: {stats.consistency:.1f} posiciones",
                'recommendation': 'Enfocarse en terminar carreras de forma consistente.'
            })
        
        # Si no hay áreas de mejora significativas
        if not areas:
            areas.append({
                'area': 'General',
                'severity': 'low',
                'current': 'Buen rendimiento general',
                'recommendation': '¡Sigue así! Mantén la consistencia y busca oportunidades.'
            })
        
        return areas
    
    def generate_full_report(self) -> Dict[str, Any]:
        """Genera un reporte completo de proyecciones"""
        return {
            'finish_projection': self.project_finish_position(),
            'incident_projection': self.project_incidents(),
            'points_projection': self.project_championship_points(),
            'irating_estimate': self.calculate_irating_estimate(),
            'improvement_areas': self.get_improvement_areas()
        }
