"""
Motor de Machine Learning para proyecciones inteligentes
Usa modelos de IA local para aprender de los datos históricos
Incluye persistencia del modelo para mantener el aprendizaje
"""

import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import statistics
import warnings
import os
import json
from pathlib import Path
from datetime import datetime
warnings.filterwarnings('ignore')

# Intentar importar joblib para persistencia
try:
    import joblib
    JOBLIB_AVAILABLE = True
except ImportError:
    JOBLIB_AVAILABLE = False

# Intentar importar sklearn, si no está disponible usar fallback
try:
    from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
    from sklearn.linear_model import LinearRegression, Ridge
    from sklearn.preprocessing import StandardScaler, LabelEncoder
    from sklearn.model_selection import cross_val_score
    from sklearn.cluster import KMeans
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    print("⚠️ scikit-learn no instalado. Ejecuta: pip install scikit-learn")


@dataclass
class RacePrediction:
    """Predicción para una carrera"""
    predicted_position: float
    confidence: float
    position_range: Tuple[float, float]
    predicted_incidents: float
    predicted_points: int
    factors: Dict[str, float]


@dataclass
class PerformanceInsight:
    """Insight sobre el rendimiento"""
    category: str
    title: str
    description: str
    impact: str  # 'positive', 'negative', 'neutral'
    score: float  # 0-100


class MLEngine:
    """Motor de Machine Learning para análisis predictivo de iRacing"""
    
    # Directorio para guardar modelos persistentes
    MODEL_DIR = Path(os.environ.get('APPDATA', os.path.expanduser('~'))) / 'iRacingStatsCalc' / 'ml_models'
    MODEL_FILE = 'iracing_ml_model.joblib'
    METADATA_FILE = 'model_metadata.json'
    HISTORY_FILE = 'training_history.json'
    
    def __init__(self, stats_calculator):
        self.stats_calc = stats_calculator
        self.position_model = None
        self.incident_model = None
        self.scaler = StandardScaler() if SKLEARN_AVAILABLE else None
        self.track_encoder = LabelEncoder() if SKLEARN_AVAILABLE else None
        self.series_encoder = LabelEncoder() if SKLEARN_AVAILABLE else None
        self.is_trained = False
        self.feature_importance = {}
        self.model_accuracy = 0.0
        self.samples_used = 0
        self.total_races_learned = 0  # Total de carreras aprendidas (acumulativo)
        self.training_sessions = 0    # Número de veces que se ha entrenado
        self.last_training_date = None
        self.training_history = []    # Historial de entrenamientos
        self.training_result = None
        
    def prepare_features(self) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Prepara las características (features) para el modelo ML
        
        Returns:
            X (features), y_position (target posición), y_incidents (target incidentes)
        """
        if not SKLEARN_AVAILABLE:
            return None, None, None
            
        trend = self.stats_calc.get_performance_trend()
        
        if len(trend) < 5:
            return None, None, None
        
        # Preparar datos
        tracks = []
        series = []
        features = []
        positions = []
        incidents = []
        
        for i, race in enumerate(trend):
            # Saltar carreras sin posición válida (finish debe ser >= 1)
            if race['finish'] < 1:
                continue
                
            # Features de la carrera
            track = race.get('track', 'Unknown')
            serie = race.get('series', 'Unknown')
            
            tracks.append(track)
            series.append(serie)
            
            # Calcular features históricas (promedio de últimas N carreras)
            lookback = min(5, i)
            if lookback > 0:
                recent_positions = [trend[j]['finish'] for j in range(max(0, i-lookback), i) if trend[j]['finish'] >= 1]
                recent_incidents = [trend[j]['incidents'] for j in range(max(0, i-lookback), i)]
                recent_sof = [trend[j]['sof'] for j in range(max(0, i-lookback), i) if trend[j]['sof'] > 0]
                
                avg_recent_position = statistics.mean(recent_positions) if recent_positions else 10
                avg_recent_incidents = statistics.mean(recent_incidents) if recent_incidents else 5
                avg_recent_sof = statistics.mean(recent_sof) if recent_sof else 1500
                position_trend = recent_positions[-1] - recent_positions[0] if len(recent_positions) > 1 else 0
            else:
                avg_recent_position = 10
                avg_recent_incidents = 5
                avg_recent_sof = 1500
                position_trend = 0
            
            feature_row = [
                race['sof'] if race['sof'] > 0 else 1500,           # SOF de la carrera
                race['start'] if race['start'] > 0 else 10,         # Posición de salida
                avg_recent_position,                                  # Promedio reciente de posiciones
                avg_recent_incidents,                                 # Promedio reciente de incidentes
                avg_recent_sof,                                       # SOF promedio reciente
                position_trend,                                       # Tendencia de posición
                i + 1,                                                # Número de carrera (experiencia)
                len(tracks),                                          # Carreras totales hasta ese momento
            ]
            
            features.append(feature_row)
            positions.append(race['finish'])
            incidents.append(race['incidents'])
        
        if len(features) < 5:
            return None, None, None
        
        # Encodear tracks y series
        if tracks:
            try:
                self.track_encoder.fit(tracks)
                track_encoded = self.track_encoder.transform(tracks)
                for i, t in enumerate(track_encoded):
                    features[i].append(t)
            except:
                for i in range(len(features)):
                    features[i].append(0)
        
        if series:
            try:
                self.series_encoder.fit(series)
                series_encoded = self.series_encoder.transform(series)
                for i, s in enumerate(series_encoded):
                    features[i].append(s)
            except:
                for i in range(len(features)):
                    features[i].append(0)
        
        X = np.array(features)
        y_position = np.array(positions)
        y_incidents = np.array(incidents)
        
        return X, y_position, y_incidents
    
    def train_models(self) -> Dict[str, Any]:
        """
        Entrena los modelos de ML con los datos históricos
        
        Returns:
            Diccionario con métricas de entrenamiento
        """
        if not SKLEARN_AVAILABLE:
            return {
                'success': False,
                'error': 'scikit-learn no está instalado',
                'message': 'Ejecuta: pip install scikit-learn'
            }
        
        X, y_position, y_incidents = self.prepare_features()
        
        if X is None or len(X) < 5:
            return {
                'success': False,
                'error': 'Datos insuficientes',
                'message': 'Se necesitan al menos 5 carreras para entrenar el modelo'
            }
        
        try:
            # Normalizar features
            X_scaled = self.scaler.fit_transform(X)
            
            # Modelo para predecir posición - Gradient Boosting es mejor para datos pequeños
            self.position_model = GradientBoostingRegressor(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.1,
                random_state=42
            )
            
            # Modelo para predecir incidentes
            self.incident_model = GradientBoostingRegressor(
                n_estimators=100,
                max_depth=3,
                learning_rate=0.1,
                random_state=42
            )
            
            # Entrenar modelos
            self.position_model.fit(X_scaled, y_position)
            self.incident_model.fit(X_scaled, y_incidents)
            
            # Calcular precisión con cross-validation
            if len(X) >= 10:
                cv_scores = cross_val_score(self.position_model, X_scaled, y_position, cv=min(5, len(X)//2))
                self.model_accuracy = max(0, cv_scores.mean())
            else:
                # Con pocos datos, usar score directo
                self.model_accuracy = self.position_model.score(X_scaled, y_position)
            
            # Obtener importancia de features
            feature_names = [
                'SOF', 'Pos. Salida', 'Avg Posición', 'Avg Incidentes', 
                'Avg SOF', 'Tendencia', 'Experiencia', 'Carreras', 
                'Pista', 'Serie'
            ]
            
            importances = self.position_model.feature_importances_
            self.feature_importance = dict(zip(feature_names[:len(importances)], importances))
            
            self.is_trained = True
            self.samples_used = len(X)
            self.total_races_learned += len(X)
            self.training_sessions += 1
            self.last_training_date = datetime.now().isoformat()
            
            self.training_result = {
                'success': True,
                'samples_used': len(X),
                'total_races_learned': self.total_races_learned,
                'training_sessions': self.training_sessions,
                'model_score': round(self.model_accuracy, 3),
                'feature_importance': {k: round(v, 3) for k, v in sorted(
                    self.feature_importance.items(), key=lambda x: x[1], reverse=True
                )},
                'message': f'Modelo entrenado con {len(X)} carreras'
            }
            
            # Guardar modelo automáticamente
            self.save_model()
            
            return self.training_result
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'message': 'Error al entrenar el modelo'
            }
    
    def save_model(self) -> Dict[str, Any]:
        """
        Guarda el modelo entrenado en disco para persistencia
        
        Returns:
            Diccionario con el resultado de la operación
        """
        if not SKLEARN_AVAILABLE or not JOBLIB_AVAILABLE:
            return {'success': False, 'error': 'Librerías no disponibles'}
        
        if not self.is_trained:
            return {'success': False, 'error': 'No hay modelo entrenado para guardar'}
        
        try:
            # Crear directorio si no existe
            self.MODEL_DIR.mkdir(parents=True, exist_ok=True)
            
            # Guardar modelos con joblib
            model_data = {
                'position_model': self.position_model,
                'incident_model': self.incident_model,
                'scaler': self.scaler,
                'track_encoder': self.track_encoder,
                'series_encoder': self.series_encoder,
                'feature_importance': self.feature_importance,
                'model_accuracy': self.model_accuracy,
            }
            
            model_path = self.MODEL_DIR / self.MODEL_FILE
            joblib.dump(model_data, model_path)
            
            # Guardar metadata en JSON legible
            metadata = {
                'last_training_date': self.last_training_date,
                'total_races_learned': self.total_races_learned,
                'training_sessions': self.training_sessions,
                'samples_used': self.samples_used,
                'model_accuracy': self.model_accuracy,
                'feature_importance': self.feature_importance,
                'version': '1.0'
            }
            
            metadata_path = self.MODEL_DIR / self.METADATA_FILE
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            # Agregar al historial
            history_entry = {
                'date': self.last_training_date,
                'samples': self.samples_used,
                'accuracy': self.model_accuracy,
                'total_learned': self.total_races_learned
            }
            self.training_history.append(history_entry)
            
            # Guardar historial
            history_path = self.MODEL_DIR / self.HISTORY_FILE
            with open(history_path, 'w', encoding='utf-8') as f:
                json.dump(self.training_history, f, indent=2, ensure_ascii=False)
            
            return {
                'success': True,
                'path': str(model_path),
                'message': f'Modelo guardado en {model_path}'
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def load_model(self) -> Dict[str, Any]:
        """
        Carga un modelo previamente guardado desde disco
        
        Returns:
            Diccionario con el resultado de la operación
        """
        if not SKLEARN_AVAILABLE or not JOBLIB_AVAILABLE:
            return {'success': False, 'error': 'Librerías no disponibles'}
        
        model_path = self.MODEL_DIR / self.MODEL_FILE
        metadata_path = self.MODEL_DIR / self.METADATA_FILE
        history_path = self.MODEL_DIR / self.HISTORY_FILE
        
        if not model_path.exists():
            return {'success': False, 'error': 'No hay modelo guardado', 'first_time': True}
        
        try:
            # Cargar modelos
            model_data = joblib.load(model_path)
            
            self.position_model = model_data['position_model']
            self.incident_model = model_data['incident_model']
            self.scaler = model_data['scaler']
            self.track_encoder = model_data['track_encoder']
            self.series_encoder = model_data['series_encoder']
            self.feature_importance = model_data['feature_importance']
            self.model_accuracy = model_data['model_accuracy']
            
            # Cargar metadata
            if metadata_path.exists():
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                    self.last_training_date = metadata.get('last_training_date')
                    self.total_races_learned = metadata.get('total_races_learned', 0)
                    self.training_sessions = metadata.get('training_sessions', 0)
                    self.samples_used = metadata.get('samples_used', 0)
            
            # Cargar historial
            if history_path.exists():
                with open(history_path, 'r', encoding='utf-8') as f:
                    self.training_history = json.load(f)
            
            self.is_trained = True
            
            return {
                'success': True,
                'message': f'Modelo cargado ({self.total_races_learned} carreras aprendidas)',
                'total_races_learned': self.total_races_learned,
                'training_sessions': self.training_sessions,
                'last_training': self.last_training_date,
                'accuracy': self.model_accuracy
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_model_info(self) -> Dict[str, Any]:
        """
        Obtiene información sobre el estado del modelo
        
        Returns:
            Diccionario con información del modelo
        """
        model_path = self.MODEL_DIR / self.MODEL_FILE
        
        info = {
            'is_trained': self.is_trained,
            'model_exists_on_disk': model_path.exists(),
            'model_path': str(self.MODEL_DIR),
            'total_races_learned': self.total_races_learned,
            'training_sessions': self.training_sessions,
            'last_training_date': self.last_training_date,
            'model_accuracy': self.model_accuracy,
            'samples_current_session': self.samples_used,
            'sklearn_available': SKLEARN_AVAILABLE,
            'joblib_available': JOBLIB_AVAILABLE
        }
        
        return info
    
    def delete_saved_model(self) -> Dict[str, Any]:
        """
        Elimina el modelo guardado en disco
        
        Returns:
            Diccionario con el resultado de la operación
        """
        try:
            model_path = self.MODEL_DIR / self.MODEL_FILE
            metadata_path = self.MODEL_DIR / self.METADATA_FILE
            history_path = self.MODEL_DIR / self.HISTORY_FILE
            
            deleted = []
            for path in [model_path, metadata_path, history_path]:
                if path.exists():
                    path.unlink()
                    deleted.append(path.name)
            
            # Resetear estado
            self.is_trained = False
            self.position_model = None
            self.incident_model = None
            self.total_races_learned = 0
            self.training_sessions = 0
            self.training_history = []
            
            return {
                'success': True,
                'deleted': deleted,
                'message': 'Modelo eliminado correctamente'
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def predict_next_race(self, sof: int = None, start_position: int = None, 
                          track: str = None, series: str = None) -> RacePrediction:
        """
        Predice el resultado de la próxima carrera
        
        Args:
            sof: Strength of Field esperado
            start_position: Posición de salida esperada
            track: Nombre de la pista
            series: Nombre de la serie
            
        Returns:
            RacePrediction con la predicción
        """
        # Si no hay modelo entrenado, entrenar primero
        if not self.is_trained:
            train_result = self.train_models()
            if not train_result['success']:
                # Usar fallback estadístico
                return self._fallback_prediction()
        
        trend = self.stats_calc.get_performance_trend()
        
        if not trend or not SKLEARN_AVAILABLE or self.position_model is None:
            return self._fallback_prediction()
        
        # Preparar features para predicción
        recent_races = trend[-5:] if len(trend) >= 5 else trend
        
        avg_recent_position = statistics.mean([r['finish'] for r in recent_races if r['finish'] > 0]) if recent_races else 10
        avg_recent_incidents = statistics.mean([r['incidents'] for r in recent_races])
        avg_recent_sof = statistics.mean([r['sof'] for r in recent_races if r['sof'] > 0]) if any(r['sof'] > 0 for r in recent_races) else 1500
        
        # Tendencia
        if len(recent_races) >= 2:
            positions = [r['finish'] for r in recent_races if r['finish'] > 0]
            position_trend = positions[-1] - positions[0] if len(positions) > 1 else 0
        else:
            position_trend = 0
        
        # Usar valores proporcionados o estimados
        estimated_sof = sof if sof else int(avg_recent_sof)
        estimated_start = start_position if start_position else int(avg_recent_position)
        
        # Encodear track y series
        track_encoded = 0
        series_encoded = 0
        
        if track and self.track_encoder:
            try:
                track_encoded = self.track_encoder.transform([track])[0]
            except:
                track_encoded = 0
                
        if series and self.series_encoder:
            try:
                series_encoded = self.series_encoder.transform([series])[0]
            except:
                series_encoded = 0
        
        # Crear vector de features
        features = np.array([[
            estimated_sof,
            estimated_start,
            avg_recent_position,
            avg_recent_incidents,
            avg_recent_sof,
            position_trend,
            len(trend) + 1,
            len(trend) + 1,
            track_encoded,
            series_encoded
        ]])
        
        # Escalar features
        features_scaled = self.scaler.transform(features)
        
        # Hacer predicciones
        predicted_position = self.position_model.predict(features_scaled)[0]
        predicted_incidents = self.incident_model.predict(features_scaled)[0]
        
        # Calcular rango de confianza basado en desviación histórica
        positions = [r['finish'] for r in trend if r['finish'] > 0]
        if len(positions) > 1:
            std = statistics.stdev(positions)
            position_range = (
                max(1, predicted_position - std),
                predicted_position + std
            )
        else:
            position_range = (max(1, predicted_position - 3), predicted_position + 3)
        
        # Calcular confianza
        confidence = min(0.95, 0.5 + (len(trend) * 0.03) + (self.model_accuracy * 0.3))
        
        # Estimar puntos basado en posición
        predicted_points = self._estimate_points(predicted_position)
        
        # Factores que influyen
        factors = {
            'historial_reciente': round(avg_recent_position, 1),
            'sof_impacto': round((estimated_sof - avg_recent_sof) / 100, 2),
            'tendencia': round(position_trend, 2),
            'experiencia': len(trend)
        }
        
        return RacePrediction(
            predicted_position=round(predicted_position, 1),
            confidence=round(confidence, 2),
            position_range=(round(position_range[0], 1), round(position_range[1], 1)),
            predicted_incidents=round(max(0, predicted_incidents), 1),
            predicted_points=predicted_points,
            factors=factors
        )
    
    def _fallback_prediction(self) -> RacePrediction:
        """Predicción de fallback cuando no hay modelo ML"""
        trend = self.stats_calc.get_performance_trend()
        
        if not trend:
            return RacePrediction(
                predicted_position=10.0,
                confidence=0.1,
                position_range=(5.0, 15.0),
                predicted_incidents=5.0,
                predicted_points=50,
                factors={}
            )
        
        positions = [r['finish'] for r in trend if r['finish'] > 0]
        incidents = [r['incidents'] for r in trend]
        
        avg_pos = statistics.mean(positions) if positions else 10
        avg_inc = statistics.mean(incidents) if incidents else 5
        
        return RacePrediction(
            predicted_position=round(avg_pos, 1),
            confidence=0.3,
            position_range=(max(1, avg_pos - 3), avg_pos + 3),
            predicted_incidents=round(avg_inc, 1),
            predicted_points=self._estimate_points(avg_pos),
            factors={'nota': 'Predicción basada en promedios (ML no disponible)'}
        )
    
    def _estimate_points(self, position: float) -> int:
        """Estima puntos basado en posición"""
        # Sistema de puntos aproximado de iRacing
        points_table = {
            1: 150, 2: 140, 3: 130, 4: 125, 5: 120,
            6: 115, 7: 110, 8: 105, 9: 100, 10: 95,
            11: 90, 12: 85, 13: 80, 14: 75, 15: 70,
            16: 65, 17: 60, 18: 55, 19: 50, 20: 45
        }
        pos_int = max(1, min(20, int(round(position))))
        return points_table.get(pos_int, max(20, 45 - (pos_int - 20) * 2))
    
    def predict_season(self, races_remaining: int = 8) -> Dict[str, Any]:
        """
        Predice resultados para el resto de la temporada
        
        Args:
            races_remaining: Carreras restantes en la temporada
            
        Returns:
            Diccionario con proyecciones de temporada
        """
        predictions = []
        total_points = 0
        
        # Obtener puntos actuales
        trend = self.stats_calc.get_performance_trend()
        current_points = sum(r['points'] for r in trend if r['points'] > 0)
        
        for i in range(races_remaining):
            pred = self.predict_next_race()
            predictions.append({
                'race': i + 1,
                'predicted_position': pred.predicted_position,
                'predicted_incidents': pred.predicted_incidents,
                'predicted_points': pred.predicted_points,
                'confidence': pred.confidence
            })
            total_points += pred.predicted_points
        
        # Calcular escenarios
        avg_confidence = statistics.mean([p['confidence'] for p in predictions])
        positions = [p['predicted_position'] for p in predictions]
        
        return {
            'races_remaining': races_remaining,
            'current_points': current_points,
            'projected_points': total_points,
            'total_projected': current_points + total_points,
            'avg_predicted_position': round(statistics.mean(positions), 1),
            'best_case_points': int(total_points * 1.2),
            'worst_case_points': int(total_points * 0.8),
            'confidence': round(avg_confidence, 2),
            'race_predictions': predictions
        }
    
    def analyze_patterns(self) -> Dict[str, Any]:
        """
        Analiza patrones en los datos usando clustering y correlaciones
        
        Returns:
            Diccionario con patrones encontrados
        """
        trend = self.stats_calc.get_performance_trend()
        
        if len(trend) < 5:
            return {'patterns': [], 'message': 'Datos insuficientes para análisis de patrones'}
        
        patterns = []
        
        # 1. Correlación SOF vs Resultado
        sof_data = [(r['sof'], r['finish']) for r in trend if r['sof'] > 0 and r['finish'] > 0]
        if len(sof_data) >= 3:
            sofs = [d[0] for d in sof_data]
            finishes = [d[1] for d in sof_data]
            correlation = self._calculate_correlation(sofs, finishes)
            
            if abs(correlation) > 0.3:
                if correlation > 0:
                    patterns.append({
                        'type': 'correlation',
                        'name': 'SOF vs Resultado',
                        'finding': 'Rindes PEOR en splits más fuertes',
                        'correlation': round(correlation, 2),
                        'recommendation': 'Practica más para competir en SOFs altos'
                    })
                else:
                    patterns.append({
                        'type': 'correlation',
                        'name': 'SOF vs Resultado',
                        'finding': 'Rindes MEJOR en splits más fuertes',
                        'correlation': round(correlation, 2),
                        'recommendation': '¡Excelente! Tu nivel real puede ser mayor que tu iRating'
                    })
        
        # 2. Patrón de incidentes vs posición de salida
        start_inc = [(r['start'], r['incidents']) for r in trend if r['start'] > 0]
        if len(start_inc) >= 3:
            starts = [d[0] for d in start_inc]
            incidents = [d[1] for d in start_inc]
            corr = self._calculate_correlation(starts, incidents)
            
            if corr < -0.3:
                patterns.append({
                    'type': 'correlation',
                    'name': 'Salida vs Incidentes',
                    'finding': 'Más incidentes cuando sales adelante',
                    'correlation': round(corr, 2),
                    'recommendation': 'Las primeras vueltas son críticas. Sé más conservador al inicio.'
                })
            elif corr > 0.3:
                patterns.append({
                    'type': 'correlation',
                    'name': 'Salida vs Incidentes',
                    'finding': 'Más incidentes cuando sales atrás',
                    'correlation': round(corr, 2),
                    'recommendation': 'Evita riesgos innecesarios al remontar. La paciencia paga.'
                })
        
        # 3. Detectar rachas (buenas y malas)
        positions = [r['finish'] for r in trend if r['finish'] > 0]
        streaks = self._find_streaks(positions)
        if streaks['best_streak'] >= 3:
            patterns.append({
                'type': 'streak',
                'name': 'Mejor racha',
                'finding': f'{streaks["best_streak"]} carreras consecutivas en Top 5',
                'recommendation': 'Has demostrado que puedes mantener buen nivel consistentemente'
            })
        if streaks['worst_streak'] >= 3:
            patterns.append({
                'type': 'streak',
                'name': 'Racha negativa',
                'finding': f'{streaks["worst_streak"]} carreras seguidas fuera del Top 10',
                'recommendation': 'Considera tomar descansos para resetear mentalmente'
            })
        
        # 4. Análisis por serie (si hay datos)
        series_stats = self.stats_calc.calculate_stats_by_series()
        if len(series_stats) >= 2:
            best_series = min(series_stats.items(), key=lambda x: x[1].avg_finish)
            worst_series = max(series_stats.items(), key=lambda x: x[1].avg_finish)
            
            if best_series[1].avg_finish < worst_series[1].avg_finish - 2:
                patterns.append({
                    'type': 'series',
                    'name': 'Rendimiento por Serie',
                    'finding': f'Mejor en {best_series[0][:30]} (P{best_series[1].avg_finish:.1f})',
                    'recommendation': f'Considera enfocarte más en esta serie donde rindes mejor'
                })
        
        # 5. Análisis por pista
        track_stats = self.stats_calc.calculate_stats_by_track()
        if len(track_stats) >= 2:
            # Ordenar por promedio de posición
            sorted_tracks = sorted(track_stats.items(), key=lambda x: x[1].avg_finish)
            best_track = sorted_tracks[0]
            worst_track = sorted_tracks[-1]
            
            if best_track[1].total_races >= 2:
                patterns.append({
                    'type': 'track',
                    'name': 'Mejor pista',
                    'finding': f'{best_track[0][:25]} - Avg: P{best_track[1].avg_finish:.1f}',
                    'recommendation': 'Esta es tu pista fuerte'
                })
            
            if worst_track[1].total_races >= 2 and worst_track[1].avg_finish > best_track[1].avg_finish + 3:
                patterns.append({
                    'type': 'track',
                    'name': 'Pista a mejorar',
                    'finding': f'{worst_track[0][:25]} - Avg: P{worst_track[1].avg_finish:.1f}',
                    'recommendation': 'Practica más en esta pista'
                })
        
        # 6. Tendencia temporal
        if len(positions) >= 6:
            first_half = positions[:len(positions)//2]
            second_half = positions[len(positions)//2:]
            
            avg_first = statistics.mean(first_half)
            avg_second = statistics.mean(second_half)
            
            improvement = avg_first - avg_second
            if improvement > 2:
                patterns.append({
                    'type': 'trend',
                    'name': 'Mejora temporal',
                    'finding': f'Has mejorado {improvement:.1f} posiciones en promedio',
                    'recommendation': '¡Excelente progreso! Sigue con el mismo enfoque'
                })
            elif improvement < -2:
                patterns.append({
                    'type': 'trend',
                    'name': 'Declive temporal',
                    'finding': f'Has empeorado {-improvement:.1f} posiciones en promedio',
                    'recommendation': 'Revisa qué ha cambiado. ¿Setup? ¿Concentración? ¿Pistas nuevas?'
                })
        
        return {
            'patterns': patterns,
            'total_races_analyzed': len(trend),
            'message': f'Se encontraron {len(patterns)} patrones significativos'
        }
    
    def _calculate_correlation(self, x: List[float], y: List[float]) -> float:
        """Calcula correlación de Pearson"""
        if len(x) != len(y) or len(x) < 2:
            return 0.0
        
        n = len(x)
        mean_x = statistics.mean(x)
        mean_y = statistics.mean(y)
        
        numerator = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        
        sum_sq_x = sum((xi - mean_x) ** 2 for xi in x)
        sum_sq_y = sum((yi - mean_y) ** 2 for yi in y)
        
        denominator = (sum_sq_x * sum_sq_y) ** 0.5
        
        if denominator == 0:
            return 0.0
        
        return numerator / denominator
    
    def _find_streaks(self, positions: List[int]) -> Dict[str, int]:
        """Encuentra rachas buenas y malas"""
        best_streak = 0
        worst_streak = 0
        current_good = 0
        current_bad = 0
        
        for pos in positions:
            if pos <= 5:  # Top 5
                current_good += 1
                current_bad = 0
                best_streak = max(best_streak, current_good)
            elif pos > 10:  # Fuera de Top 10
                current_bad += 1
                current_good = 0
                worst_streak = max(worst_streak, current_bad)
            else:
                current_good = 0
                current_bad = 0
        
        return {
            'best_streak': best_streak,
            'worst_streak': worst_streak
        }
    
    def generate_insights(self) -> List[PerformanceInsight]:
        """
        Genera insights inteligentes sobre el rendimiento
        
        Returns:
            Lista de insights ordenados por importancia
        """
        insights = []
        trend = self.stats_calc.get_performance_trend()
        
        if len(trend) < 3:
            return [PerformanceInsight(
                category='general',
                title='Datos insuficientes',
                description='Necesitas más carreras para generar insights significativos',
                impact='neutral',
                score=0
            )]
        
        # Entrenar modelo si no está entrenado
        if not self.is_trained:
            self.train_models()
        
        # 1. Insight de consistencia
        positions = [r['finish'] for r in trend if r['finish'] > 0]
        if len(positions) >= 3:
            std = statistics.stdev(positions)
            avg = statistics.mean(positions)
            
            if std < 3:
                insights.append(PerformanceInsight(
                    category='consistencia',
                    title='Alta consistencia',
                    description=f'Tu desviación es de solo {std:.1f} posiciones. Muy consistente.',
                    impact='positive',
                    score=85
                ))
            elif std > 6:
                insights.append(PerformanceInsight(
                    category='consistencia',
                    title='Alta variabilidad',
                    description=f'Tu desviación es de {std:.1f} posiciones. Resultados muy variables.',
                    impact='negative',
                    score=40
                ))
        
        # 2. Insight de incidentes
        incidents = [r['incidents'] for r in trend]
        avg_inc = statistics.mean(incidents)
        
        if avg_inc <= 4:
            insights.append(PerformanceInsight(
                category='seguridad',
                title='Piloto limpio',
                description=f'Promedio de {avg_inc:.1f}x incidentes. ¡Excelente control!',
                impact='positive',
                score=90
            ))
        elif avg_inc > 8:
            insights.append(PerformanceInsight(
                category='seguridad',
                title='Muchos incidentes',
                description=f'Promedio de {avg_inc:.1f}x incidentes. Prioriza carreras limpias.',
                impact='negative',
                score=30
            ))
        
        # 3. Insight de clasificación vs carrera
        quali = self.stats_calc.get_qualifying_analysis()
        if quali.get('improvement_rate', 0) > 60:
            insights.append(PerformanceInsight(
                category='ritmo',
                title='Fuerte en carrera',
                description=f'Mejoras posición en el {quali["improvement_rate"]:.0f}% de carreras.',
                impact='positive',
                score=80
            ))
        elif quali.get('improvement_rate', 100) < 30:
            insights.append(PerformanceInsight(
                category='ritmo',
                title='Mejor clasificador que corredor',
                description=f'Solo mejoras en el {quali["improvement_rate"]:.0f}% de carreras.',
                impact='negative',
                score=45
            ))
        
        # 4. Insight de tendencia reciente
        if len(positions) >= 6:
            recent = positions[-3:]
            older = positions[-6:-3]
            
            recent_avg = statistics.mean(recent)
            older_avg = statistics.mean(older)
            
            diff = older_avg - recent_avg
            
            if diff > 2:
                insights.append(PerformanceInsight(
                    category='tendencia',
                    title='En racha positiva',
                    description=f'Has mejorado {diff:.1f} posiciones en las últimas carreras.',
                    impact='positive',
                    score=85
                ))
            elif diff < -2:
                insights.append(PerformanceInsight(
                    category='tendencia',
                    title='Tendencia negativa',
                    description=f'Has empeorado {-diff:.1f} posiciones recientemente.',
                    impact='negative',
                    score=35
                ))
        
        # 5. Insight de feature importance (si modelo entrenado)
        if self.feature_importance:
            top_feature = max(self.feature_importance.items(), key=lambda x: x[1])
            insights.append(PerformanceInsight(
                category='análisis_ml',
                title='Factor más importante',
                description=f'"{top_feature[0]}" es el factor que más influye en tus resultados ({top_feature[1]*100:.0f}% importancia)',
                impact='neutral',
                score=70
            ))
        
        # Ordenar por score
        insights.sort(key=lambda x: x.score, reverse=True)
        
        return insights
    
    def get_recommendations(self) -> List[Dict[str, str]]:
        """
        Genera recomendaciones personalizadas basadas en ML
        
        Returns:
            Lista de recomendaciones priorizadas
        """
        recommendations = []
        
        insights = self.generate_insights()
        patterns = self.analyze_patterns()
        
        # Agregar recomendaciones de insights negativos
        for insight in insights:
            if insight.impact == 'negative':
                if insight.category == 'consistencia':
                    recommendations.append({
                        'priority': 'alta',
                        'area': 'Consistencia',
                        'action': 'Enfócate en terminar todas las carreras. Evita riesgos innecesarios.',
                        'expected_impact': 'Mejora de 2-3 posiciones en promedio'
                    })
                elif insight.category == 'seguridad':
                    recommendations.append({
                        'priority': 'alta',
                        'area': 'Incidentes',
                        'action': 'Practica la paciencia. Mejor perder una posición que tener un incidente.',
                        'expected_impact': 'Reducción de 2-4x incidentes por carrera'
                    })
                elif insight.category == 'ritmo':
                    recommendations.append({
                        'priority': 'media',
                        'area': 'Ritmo de carrera',
                        'action': 'Trabaja en la gestión de neumáticos y combustible en carrera.',
                        'expected_impact': 'Mantener posiciones ganadas en clasificación'
                    })
        
        # Agregar recomendaciones de patrones
        for pattern in patterns.get('patterns', []):
            if 'recommendation' in pattern:
                recommendations.append({
                    'priority': 'media',
                    'area': pattern['name'],
                    'action': pattern['recommendation'],
                    'expected_impact': pattern.get('finding', '')
                })
        
        # Si no hay recomendaciones, dar una general
        if not recommendations:
            recommendations.append({
                'priority': 'baja',
                'area': 'General',
                'action': '¡Sigue así! Mantén tu enfoque actual y busca pequeñas mejoras.',
                'expected_impact': 'Mejora gradual continua'
            })
        
        return recommendations
