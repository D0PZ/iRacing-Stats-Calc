# 🏎️ iRacing Stats Calculator

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/Platform-Windows-lightgrey.svg" alt="Platform">
  <img src="https://img.shields.io/badge/iRacing-Stats-orange.svg" alt="iRacing">
</p>

Una aplicación de escritorio completa para analizar y visualizar tus estadísticas de iRacing con **Machine Learning** integrado para predicciones inteligentes.

![Dark Theme Interface](https://img.shields.io/badge/Theme-Dark%20Mode-0D1117?style=for-the-badge)

## ✨ Características Principales

### 📊 Análisis de Estadísticas Completo
- **Estadísticas generales**: Total de carreras, vueltas, incidentes, puntos de campeonato
- **Posiciones**: Victorias, podios, Top 5, Top 10, DNFs
- **Promedios**: Posición de salida, llegada, ganancia/pérdida de posiciones
- **Consistency Score**: Mide tu regularidad usando desviación estándar
- **Análisis por serie**: Estadísticas desglosadas por cada serie de iRacing
- **Análisis por pista**: Rendimiento en cada circuito

### 📈 Visualización de Datos
- **Gráfico de tendencia de posiciones**: Evolución de tus posiciones de salida vs llegada
- **Gráfico de incidentes**: Historial de incidentes por carrera con zonas de peligro
- **Gráfico de puntos de campeonato**: Acumulación de puntos a lo largo del tiempo
- **Análisis SOF (Strength of Field)**: Distribución de la dificultad de las carreras
- **Gráficos interactivos** con tema oscuro integrado

### 🤖 Machine Learning Integrado
- **Predicción de posición**: El modelo aprende de tu historial para predecir tu próxima posición
- **Predicción de incidentes**: Estima cuántos incidentes podrías tener
- **Análisis de factores**: Identifica qué factores afectan más tu rendimiento
- **Modelo persistente**: El modelo se guarda y mejora con cada sesión
- **Insights inteligentes**: Recomendaciones personalizadas basadas en tus datos

### 🎯 Sistema de Proyecciones
- **Regresión lineal**: Proyecta tu rendimiento futuro
- **Análisis de tendencias**: Detecta si estás mejorando, empeorando o estable
- **Proyección de puntos**: Estima tus puntos futuros de campeonato
- **Intervalos de confianza**: Rangos de predicción con niveles de certeza

### 🎨 Interfaz Moderna
- **Dark Mode nativo**: Tema oscuro profesional estilo GitHub
- **Diseño responsive**: Se adapta a diferentes tamaños de ventana
- **Navegación por pestañas**: Organización clara de información
- **Tarjetas de estadísticas**: Visualización rápida de métricas clave

## 🚀 Instalación

### Requisitos
- Python 3.8 o superior
- Windows 10/11

### Instalación desde código fuente

```bash
# Clonar el repositorio
git clone https://github.com/D0PZ/iRacing-Stats-Calc.git
cd iRacing-Stats-Calc

# Instalar dependencias
pip install -r requirements.txt

# Ejecutar la aplicación
python main.py
```

### Dependencias
```
matplotlib>=3.5.0
scikit-learn>=1.0.0
numpy>=1.20.0
joblib>=1.0.0
```

## 📖 Uso

### 1. Exportar datos desde iRacing
1. Ve a [iRacing Members Site](https://members.iracing.com)
2. Navega a **Stats** → **Series Stats**
3. Selecciona la serie que quieres analizar
4. Haz clic en **Export** para descargar el archivo JSON

### 2. Cargar datos en la aplicación
1. Abre iRacing Stats Calculator
2. Haz clic en **📂 Cargar JSON**
3. Selecciona el archivo exportado de iRacing
4. ¡Explora tus estadísticas!

### 3. Entrenar el modelo ML
1. Ve a la pestaña **🤖 Machine Learning**
2. Haz clic en **🎓 Entrenar Modelo**
3. El modelo aprenderá de tus datos históricos
4. Obtén predicciones y recomendaciones personalizadas

## 🛠️ Estructura del Proyecto

```
iRacing-Stats-Calc/
├── main.py                 # Punto de entrada
├── requirements.txt        # Dependencias
├── README.md              # Documentación
├── app/
│   ├── __init__.py
│   ├── gui.py             # Interfaz gráfica (Tkinter)
│   ├── data_loader.py     # Carga y parseo de datos JSON
│   ├── stats_calculator.py # Cálculos estadísticos
│   ├── projections.py     # Motor de proyecciones
│   ├── charts.py          # Generador de gráficos (Matplotlib)
│   └── ml_engine.py       # Motor de Machine Learning
└── build/                 # Archivos de compilación
```

## 📊 Estadísticas Calculadas

| Métrica | Descripción |
|---------|-------------|
| Total Races | Número total de carreras |
| Wins | Victorias (P1) |
| Podiums | Posiciones 1-3 |
| Top 5 / Top 10 | Posiciones dentro del top |
| DNFs | Carreras no terminadas |
| Avg Finish | Promedio de posición de llegada |
| Avg Start | Promedio de posición de salida |
| Avg Gain | Promedio de posiciones ganadas/perdidas |
| Avg Incidents | Promedio de incidentes por carrera |
| Consistency | Desviación estándar de posiciones |
| Laps Led | Vueltas lideradas |
| Championship Points | Puntos totales de campeonato |

## 🤖 Modelos de Machine Learning

La aplicación utiliza varios algoritmos de scikit-learn:

- **Random Forest Regressor**: Predicción principal de posición
- **Gradient Boosting Regressor**: Modelo alternativo de alto rendimiento
- **Linear Regression**: Base para comparación y proyecciones
- **K-Means Clustering**: Agrupación de tipos de carreras

### Características del modelo
- Persistencia automática del modelo entrenado
- Historial de entrenamientos
- Métricas de precisión y confianza
- Importancia de características

## 🔧 Compilar Ejecutable

Para crear un ejecutable standalone:

```bash
# Instalar PyInstaller
pip install pyinstaller

# Compilar
python build_exe.py
```

El ejecutable se generará en la carpeta `dist/`.

## 🤝 Contribuir

Las contribuciones son bienvenidas. Por favor:

1. Fork el repositorio
2. Crea una rama para tu feature (`git checkout -b feature/NuevaCaracteristica`)
3. Commit tus cambios (`git commit -m 'Añadir nueva característica'`)
4. Push a la rama (`git push origin feature/NuevaCaracteristica`)
5. Abre un Pull Request

## 📝 Licencia

Este proyecto está bajo la Licencia MIT. Ver el archivo `LICENSE` para más detalles.

## 🙏 Agradecimientos

- [iRacing](https://www.iracing.com/) por la plataforma y la API de datos
- [Matplotlib](https://matplotlib.org/) para visualizaciones
- [scikit-learn](https://scikit-learn.org/) para Machine Learning

---

<p align="center">
  Hecho con ❤️ para la comunidad de iRacing
</p>
