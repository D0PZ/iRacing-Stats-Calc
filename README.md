# 🏎️ iRacing Stats Calculator

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/Platform-Windows-lightgrey.svg" alt="Platform">
  <img src="https://img.shields.io/badge/iRacing-Stats-orange.svg" alt="iRacing">
</p>

A comprehensive desktop application to analyze and visualize your iRacing statistics with integrated **Machine Learning** for intelligent predictions.

![Dark Theme Interface](https://img.shields.io/badge/Theme-Dark%20Mode-0D1117?style=for-the-badge)

## ✨ Main Features

### 📊 Complete Statistics Analysis
- **General statistics**: Total races, laps, incidents, championship points
- **Positions**: Wins, podiums, Top 5, Top 10, DNFs
- **Averages**: Starting position, finishing position, positions gained/lost
- **Consistency Score**: Measures your regularity using standard deviation
- **Series analysis**: Statistics broken down by each iRacing series
- **Track analysis**: Performance at each circuit

### 📈 Data Visualization
- **Position trend chart**: Evolution of your starting vs finishing positions
- **Incidents chart**: Incident history per race with danger zones
- **Championship points chart**: Point accumulation over time
- **SOF (Strength of Field) analysis**: Distribution of race difficulty
- **Interactive charts** with integrated dark theme

### 🤖 Integrated Machine Learning
- **Position prediction**: The model learns from your history to predict your next position
- **Incident prediction**: Estimates how many incidents you might have
- **Factor analysis**: Identifies which factors affect your performance the most
- **Persistent model**: The model saves and improves with each session
- **Smart insights**: Personalized recommendations based on your data

### 🎯 Projection System
- **Linear regression**: Projects your future performance
- **Trend analysis**: Detects if you're improving, declining, or stable
- **Points projection**: Estimates your future championship points
- **Confidence intervals**: Prediction ranges with certainty levels

### 🔧 Telemetry & Setup Analysis (.ibt)
- **Reads iRacing telemetry files** directly (record in-sim with `Alt+L`); split recordings of the same session are merged
- **Corner-by-corner analysis**: braking point, minimum/exit speed, time lost vs your best pass, understeer angle, wheelspin, lockups, ABS usage, track-limit excursions
- **Theoretical best lap** from your best pass through each corner
- **Tire temperatures** (outer/middle/inner under load), hot pressures and wear → camber and pressure advice
- **Setup and driving recommendations** mapped to the garage fields (ARBs, wing, camber, brake bias, TC, ABS, fuel)
- CLI: `python -m app.telemetry_analyzer "path/to/file.ibt"`

### 🎙️ Live Race Engineer (voice)
- Connects to iRacing in real time and talks to you during the session (Spanish, natural neural voice via `edge-tts`, falls back to Windows voice offline)
- Stopped/spun cars ahead, flags, rivals pitting or going off, traffic and faster classes
- Attack/defend calls with gap trend ("you catch him in 3 laps"), and where a rival is faster than you
- Tire/slide warnings, ABS overuse, in-car adjustments (brake bias, TC), fuel to the finish, laps remaining
- Setup report every time you return to the pits (saved to `logs/`)
- Starts disk telemetry recording when you get in the car and closes it when you get out (no `Alt+L`;
  disable with `--sin-grabar`); each finished session is analyzed automatically and summarized by voice
- Reads telemetry at iRacing's native 60 Hz (waits on the SDK data-ready event, no polling delay)
- **Ask by voice** (push-to-talk on a wheel button or the Insert key): damage, fuel, pit strategy, tires,
  track limits, gaps to the car ahead/behind, laps left, lap times, where you lose time, engine temps,
  brake bias/TC/ABS, "silencio" to mute non-critical calls. Assign the wheel button with `asignar_boton.bat`
- **Claude as interpreter (optional)**: voice questions are answered by Claude from a compact snapshot of the
  current state (one call per question, no history); radio commands and automatic calls stay local.
  Default backend `claude_code` runs `claude -p` with your Claude subscription (no extra cost, no window);
  backend `api` uses `ANTHROPIC_API_KEY` (billed separately). Configure in `data/ingeniero.json`:
  `"claude": {"enabled": true, "backend": "claude_code", "model": "haiku", "effort": "low"}`
- **Track limits**: warns before zones where you exceed the limit, calls each excursion with its corner and
  praises clean full-curb exits. Zones are learned from your `.ibt` files (GPS) and from live excursions,
  stored per track in `data/track_limits/`
- Run it standalone with `ingeniero.bat` or `python -m app.live_engineer --voz es-AR-TomasNeural`

### 📏 Track limits from .ibt (GPS)
- Learns each limit from your real off-track moments and measures, lap by lap, how many centimetres you
  had left (or went over) at that spot, plus your exit speed — so you see whether using the limit pays off

### 🎨 Modern Interface
- **Native Dark Mode**: Professional dark theme GitHub-style
- **Responsive design**: Adapts to different window sizes
- **Tab navigation**: Clear organization of information
- **Stats cards**: Quick visualization of key metrics

## 🚀 Installation

### Requirements
- Python 3.8 or higher
- Windows 10/11

### Installation from source

```bash
# Clone the repository
git clone https://github.com/D0PZ/iRacing-Stats-Calc.git
cd iRacing-Stats-Calc

# Install dependencies
pip install -r requirements.txt

# Run the application
python main.py
```

### Dependencies
```
matplotlib>=3.5.0
scikit-learn>=1.0.0
numpy>=1.20.0
joblib>=1.0.0
```

## 📖 Usage

### 1. Export data from iRacing
1. Go to [iRacing Members Site](https://members.iracing.com)
2. Navigate to **Stats** → **Series Stats**
3. Select the series you want to analyze
4. Click **Export** to download the JSON file

### 2. Load data into the application
1. Open iRacing Stats Calculator
2. Click **📂 Load JSON**
3. Select the exported file from iRacing
4. Explore your statistics!

### 3. Train the ML model
1. Go to the **🤖 Machine Learning** tab
2. Click **🎓 Train Model**
3. The model will learn from your historical data
4. Get personalized predictions and recommendations

## 🛠️ Project Structure

```
iRacing-Stats-Calc/
├── main.py                 # Entry point
├── requirements.txt        # Dependencies
├── README.md              # Documentation
├── app/
│   ├── __init__.py
│   ├── gui.py             # Graphical interface (Tkinter)
│   ├── data_loader.py     # JSON data loading and parsing
│   ├── stats_calculator.py # Statistical calculations
│   ├── projections.py     # Projection engine
│   ├── charts.py          # Chart generator (Matplotlib)
│   └── ml_engine.py       # Machine Learning engine
└── build/                 # Build files
```

## 📊 Calculated Statistics

| Metric | Description |
|--------|-------------|
| Total Races | Total number of races |
| Wins | Victories (P1) |
| Podiums | Positions 1-3 |
| Top 5 / Top 10 | Positions within the top |
| DNFs | Did Not Finish races |
| Avg Finish | Average finishing position |
| Avg Start | Average starting position |
| Avg Gain | Average positions gained/lost |
| Avg Incidents | Average incidents per race |
| Consistency | Standard deviation of positions |
| Laps Led | Laps led |
| Championship Points | Total championship points |

## 🤖 Machine Learning Models

The application uses several scikit-learn algorithms:

- **Random Forest Regressor**: Main position prediction
- **Gradient Boosting Regressor**: High-performance alternative model
- **Linear Regression**: Base for comparison and projections
- **K-Means Clustering**: Race type grouping

### Model Features
- Automatic persistence of trained model
- Training history
- Accuracy and confidence metrics
- Feature importance

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests
```

The tests build synthetic `.ibt` files in iRacing's binary format and drive the live engineer with a fake
SDK, so they run without iRacing or recorded telemetry.

## 🔧 Build Executable

To create a standalone executable:

```bash
# Install PyInstaller
pip install pyinstaller

# Build
python build_exe.py
```

The executable will be generated in the `dist/` folder.

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a branch for your feature (`git checkout -b feature/NewFeature`)
3. Commit your changes (`git commit -m 'Add new feature'`)
4. Push to the branch (`git push origin feature/NewFeature`)
5. Open a Pull Request

## 📝 License

This project is under the MIT License. See the `LICENSE` file for more details.

## 🙏 Acknowledgments

- [iRacing](https://www.iracing.com/) for the platform and data API
- [Matplotlib](https://matplotlib.org/) for visualizations
- [scikit-learn](https://scikit-learn.org/) for Machine Learning

---

<p align="center">
  Made with ❤️ for the iRacing community
</p>
