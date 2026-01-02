"""
Módulo para cargar y validar datos JSON de iRacing
"""

import json
from typing import List, Dict, Any, Optional
from datetime import datetime


class DataLoader:
    """Clase para cargar y procesar datos de iRacing"""
    
    def __init__(self):
        self.raw_data: List[Dict[str, Any]] = []
        self.races: List[Dict[str, Any]] = []
        self.practices: List[Dict[str, Any]] = []
        self.file_path: Optional[str] = None
    
    def load_file(self, file_path: str) -> bool:
        """
        Carga un archivo JSON de iRacing
        
        Args:
            file_path: Ruta al archivo JSON
            
        Returns:
            True si la carga fue exitosa, False en caso contrario
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # El JSON puede venir envuelto en arrays anidados
            if isinstance(data, list):
                if len(data) > 0 and isinstance(data[0], list):
                    self.raw_data = data[0]
                else:
                    self.raw_data = data
            else:
                self.raw_data = [data]
            
            self.file_path = file_path
            self._process_data()
            return True
            
        except json.JSONDecodeError as e:
            raise ValueError(f"Error al parsear JSON: {e}")
        except FileNotFoundError:
            raise FileNotFoundError(f"Archivo no encontrado: {file_path}")
        except Exception as e:
            raise Exception(f"Error al cargar archivo: {e}")
    
    def _process_data(self):
        """Procesa y clasifica los datos cargados"""
        self.races = []
        self.practices = []
        
        for session in self.raw_data:
            # Convertir fechas
            if 'start_time' in session:
                session['start_time_dt'] = self._parse_datetime(session['start_time'])
            if 'end_time' in session:
                session['end_time_dt'] = self._parse_datetime(session['end_time'])
            
            # Clasificar por tipo de evento
            event_type = session.get('event_type_name', '').lower()
            if event_type == 'race':
                self.races.append(session)
            elif event_type == 'practice':
                self.practices.append(session)
        
        # Ordenar por fecha
        self.races.sort(key=lambda x: x.get('start_time_dt', datetime.min))
        self.practices.sort(key=lambda x: x.get('start_time_dt', datetime.min))
    
    def _parse_datetime(self, dt_string: str) -> datetime:
        """Convierte string ISO a datetime"""
        try:
            # Formato: "2025-12-09T00:15:00Z"
            return datetime.fromisoformat(dt_string.replace('Z', '+00:00'))
        except:
            return datetime.min
    
    def get_all_sessions(self) -> List[Dict[str, Any]]:
        """Retorna todas las sesiones"""
        return self.raw_data
    
    def get_races(self) -> List[Dict[str, Any]]:
        """Retorna solo las carreras"""
        return self.races
    
    def get_practices(self) -> List[Dict[str, Any]]:
        """Retorna solo las prácticas"""
        return self.practices
    
    def get_unique_series(self) -> List[str]:
        """Retorna lista de series únicas"""
        series = set()
        for session in self.raw_data:
            if 'series_name' in session:
                series.add(session['series_name'])
        return sorted(list(series))
    
    def get_unique_tracks(self) -> List[str]:
        """Retorna lista de pistas únicas"""
        tracks = set()
        for session in self.raw_data:
            if 'track' in session and 'track_name' in session['track']:
                track_info = session['track']
                track_str = track_info['track_name']
                if track_info.get('config_name'):
                    track_str += f" - {track_info['config_name']}"
                tracks.add(track_str)
        return sorted(list(tracks))
    
    def get_unique_cars(self) -> List[str]:
        """Retorna lista de carros únicos"""
        cars = set()
        for session in self.raw_data:
            if 'car_name' in session:
                cars.add(session['car_name'])
        return sorted(list(cars))
    
    def get_sessions_by_series(self, series_name: str) -> List[Dict[str, Any]]:
        """Retorna sesiones filtradas por serie"""
        return [s for s in self.raw_data if s.get('series_name') == series_name]
    
    def get_races_by_series(self, series_name: str) -> List[Dict[str, Any]]:
        """Retorna carreras filtradas por serie"""
        return [r for r in self.races if r.get('series_name') == series_name]
    
    def is_loaded(self) -> bool:
        """Verifica si hay datos cargados"""
        return len(self.raw_data) > 0
    
    def get_summary(self) -> Dict[str, Any]:
        """Retorna un resumen de los datos cargados"""
        return {
            'total_sessions': len(self.raw_data),
            'total_races': len(self.races),
            'total_practices': len(self.practices),
            'unique_series': len(self.get_unique_series()),
            'unique_tracks': len(self.get_unique_tracks()),
            'unique_cars': len(self.get_unique_cars()),
            'file_path': self.file_path
        }
