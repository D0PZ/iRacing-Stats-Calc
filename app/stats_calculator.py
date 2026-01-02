"""
Módulo para calcular estadísticas de iRacing
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import statistics


@dataclass
class RaceStats:
    """Estructura para estadísticas de carreras"""
    total_races: int = 0
    total_laps: int = 0
    total_incidents: int = 0
    total_points: int = 0
    wins: int = 0
    podiums: int = 0
    top5: int = 0
    top10: int = 0
    dnf: int = 0
    avg_finish: float = 0.0
    avg_start: float = 0.0
    avg_gain: float = 0.0
    avg_incidents: float = 0.0
    avg_sof: float = 0.0
    best_finish: int = 999
    worst_finish: int = 0
    best_start: int = 999
    consistency: float = 0.0  # Desviación estándar
    laps_led: int = 0


class StatsCalculator:
    """Clase para calcular estadísticas de iRacing"""
    
    def __init__(self, data_loader):
        self.data_loader = data_loader
    
    def calculate_race_stats(self, races: Optional[List[Dict]] = None) -> RaceStats:
        """
        Calcula estadísticas de carreras
        
        Args:
            races: Lista de carreras (usa todas si no se especifica)
            
        Returns:
            RaceStats con todas las estadísticas calculadas
        """
        if races is None:
            races = self.data_loader.get_races()
        
        if not races:
            return RaceStats()
        
        stats = RaceStats()
        stats.total_races = len(races)
        
        finish_positions = []
        start_positions = []
        gains = []
        incidents_list = []
        sof_list = []
        
        for race in races:
            # Posición final
            finish_pos = race.get('finish_position_in_class', race.get('finish_position', 0))
            if finish_pos > 0:
                finish_positions.append(finish_pos)
                
                if finish_pos == 1:
                    stats.wins += 1
                if finish_pos <= 3:
                    stats.podiums += 1
                if finish_pos <= 5:
                    stats.top5 += 1
                if finish_pos <= 10:
                    stats.top10 += 1
                
                if finish_pos < stats.best_finish:
                    stats.best_finish = finish_pos
                if finish_pos > stats.worst_finish:
                    stats.worst_finish = finish_pos
            
            # Posición inicial
            start_pos = race.get('starting_position_in_class', race.get('starting_position', -1))
            if start_pos > 0:
                start_positions.append(start_pos)
                if start_pos < stats.best_start:
                    stats.best_start = start_pos
                
                # Calcular ganancia/pérdida de posiciones
                if finish_pos > 0:
                    gain = start_pos - finish_pos  # Positivo = ganó posiciones
                    gains.append(gain)
            
            # Vueltas completadas
            laps = race.get('laps_complete', 0)
            stats.total_laps += laps
            
            # DNF (no completó vueltas o muy pocas)
            if laps == 0:
                stats.dnf += 1
            
            # Vueltas lideradas
            stats.laps_led += race.get('laps_led', 0)
            
            # Incidentes
            incidents = race.get('incidents', 0)
            stats.total_incidents += incidents
            incidents_list.append(incidents)
            
            # Puntos de campeonato
            points = race.get('champ_points', 0)
            if points > 0:
                stats.total_points += points
            
            # SOF (Strength of Field)
            sof = race.get('event_strength_of_field', -1)
            if sof > 0:
                sof_list.append(sof)
        
        # Calcular promedios
        if finish_positions:
            stats.avg_finish = statistics.mean(finish_positions)
            if len(finish_positions) > 1:
                stats.consistency = statistics.stdev(finish_positions)
        
        if start_positions:
            stats.avg_start = statistics.mean(start_positions)
        
        if gains:
            stats.avg_gain = statistics.mean(gains)
        
        if incidents_list:
            stats.avg_incidents = statistics.mean(incidents_list)
        
        if sof_list:
            stats.avg_sof = statistics.mean(sof_list)
        
        # Ajustar valores no encontrados
        if stats.best_finish == 999:
            stats.best_finish = 0
        if stats.best_start == 999:
            stats.best_start = 0
        
        return stats
    
    def calculate_stats_by_series(self) -> Dict[str, RaceStats]:
        """Calcula estadísticas por cada serie"""
        series_stats = {}
        for series in self.data_loader.get_unique_series():
            races = self.data_loader.get_races_by_series(series)
            if races:
                series_stats[series] = self.calculate_race_stats(races)
        return series_stats
    
    def calculate_stats_by_track(self) -> Dict[str, RaceStats]:
        """Calcula estadísticas por cada pista"""
        track_stats = {}
        races = self.data_loader.get_races()
        
        # Agrupar carreras por pista
        races_by_track = {}
        for race in races:
            if 'track' in race:
                track_info = race['track']
                track_name = track_info.get('track_name', 'Unknown')
                config = track_info.get('config_name', '')
                track_key = f"{track_name} - {config}" if config else track_name
                
                if track_key not in races_by_track:
                    races_by_track[track_key] = []
                races_by_track[track_key].append(race)
        
        for track, track_races in races_by_track.items():
            track_stats[track] = self.calculate_race_stats(track_races)
        
        return track_stats
    
    def calculate_stats_by_car(self) -> Dict[str, RaceStats]:
        """Calcula estadísticas por cada carro"""
        car_stats = {}
        races = self.data_loader.get_races()
        
        # Agrupar carreras por carro
        races_by_car = {}
        for race in races:
            car_name = race.get('car_name', 'Unknown')
            if car_name not in races_by_car:
                races_by_car[car_name] = []
            races_by_car[car_name].append(race)
        
        for car, car_races in races_by_car.items():
            car_stats[car] = self.calculate_race_stats(car_races)
        
        return car_stats
    
    def get_recent_form(self, num_races: int = 5) -> RaceStats:
        """Calcula estadísticas de las últimas N carreras"""
        races = self.data_loader.get_races()
        recent_races = races[-num_races:] if len(races) >= num_races else races
        return self.calculate_race_stats(recent_races)
    
    def get_performance_trend(self) -> List[Dict[str, Any]]:
        """Obtiene la tendencia de rendimiento por carrera"""
        races = self.data_loader.get_races()
        trend = []
        
        for i, race in enumerate(races):
            # En iRacing, posición 0 significa 1er lugar en clase
            finish_pos = race.get('finish_position_in_class', race.get('finish_position', -1))
            start_pos = race.get('starting_position_in_class', race.get('starting_position', -1))
            
            # Convertir 0 a 1 (1er lugar en clase)
            if finish_pos == 0:
                finish_pos = 1
            if start_pos == 0:
                start_pos = 1
            
            trend.append({
                'race_num': i + 1,
                'date': race.get('start_time', ''),
                'series': race.get('series_short_name', race.get('series_name', '')),
                'track': race.get('track', {}).get('track_name', 'Unknown'),
                'start': start_pos,
                'finish': finish_pos,
                'incidents': race.get('incidents', 0),
                'sof': race.get('event_strength_of_field', 0),
                'points': race.get('champ_points', 0),
                'laps': race.get('laps_complete', 0)
            })
        
        return trend
    
    def get_incident_analysis(self) -> Dict[str, Any]:
        """Analiza los incidentes"""
        races = self.data_loader.get_races()
        
        if not races:
            return {}
        
        incidents = [r.get('incidents', 0) for r in races]
        
        # Clasificar carreras por nivel de incidentes
        clean_races = sum(1 for i in incidents if i <= 4)  # 4x o menos
        moderate_races = sum(1 for i in incidents if 4 < i <= 8)  # 5x-8x
        rough_races = sum(1 for i in incidents if i > 8)  # más de 8x
        
        return {
            'total_incidents': sum(incidents),
            'avg_incidents': statistics.mean(incidents) if incidents else 0,
            'min_incidents': min(incidents) if incidents else 0,
            'max_incidents': max(incidents) if incidents else 0,
            'clean_races': clean_races,
            'clean_rate': (clean_races / len(races) * 100) if races else 0,
            'moderate_races': moderate_races,
            'rough_races': rough_races,
            'incident_trend': incidents  # Para gráficos
        }
    
    def get_qualifying_analysis(self) -> Dict[str, Any]:
        """Analiza el rendimiento en clasificación vs carrera"""
        races = self.data_loader.get_races()
        
        improved = 0
        maintained = 0
        lost = 0
        
        for race in races:
            start = race.get('starting_position_in_class', race.get('starting_position', -1))
            finish = race.get('finish_position_in_class', race.get('finish_position', -1))
            
            if start > 0 and finish > 0:
                if finish < start:
                    improved += 1
                elif finish == start:
                    maintained += 1
                else:
                    lost += 1
        
        total = improved + maintained + lost
        
        return {
            'races_improved': improved,
            'races_maintained': maintained,
            'races_lost_positions': lost,
            'improvement_rate': (improved / total * 100) if total > 0 else 0,
            'consistency_rate': (maintained / total * 100) if total > 0 else 0
        }
