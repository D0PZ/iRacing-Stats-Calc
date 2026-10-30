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
- **Tire temperatures**: surface O/M/I on the loaded (outside) wheel of each corner, plus the carcass O/M/I iRacing
  measures at pit entry (same as the garage) → exact per-wheel camber and cold pressure, in garage steps
- **Setup and driving recommendations** mapped to the garage fields (ARBs, wing, camber, brake bias, TC, ABS, fuel)
- CLI: `python -m app.telemetry_analyzer "path/to/file.ibt"`

### 🎙️ Live Race Engineer (voice)
- Connects to iRacing in real time and talks to you during the session (Spanish, natural neural voice via `edge-tts`, falls back to Windows voice offline)
- Stopped/spun cars ahead, flags, rivals pitting or going off, traffic and faster classes
- Attack/defend calls with gap trend ("you catch him in 3 laps"), and where a rival is faster than you
- Tire/slide warnings, ABS overuse, in-car adjustments (brake bias, TC), fuel to the finish, laps remaining
- Setup report every time you return to the pits (saved to `logs/`)
- **Tire model** (O/M/I per wheel): inner−outer → camber, middle vs edges → cold pressure, both coordinated with the
  toe advice. Sensitivities per wheel are learned with a Kalman filter that separates camber, toe and pressure
  changes made together; ideal values use a Bayesian fit over all your runs on that track. Pressures move in the
  garage's 0.5 psi clicks, the hot pressure is predicted with the gas law, and runs where the tires never got
  warm are ignored. Works with metric or imperial garage units (kPa/psi, °C/°F, mm/fractions of an inch)
- **Car profile** from the iRacing manual (Porsche 911 Cup 992.2): minimum cold pressure is the fastest, as much
  camber as possible unless the inner edge overheats, rear toe-in only
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
- Python 3.9 or higher (tested on 3.12)
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
See `requirements.txt`: matplotlib, scikit-learn and numpy for the stats app; pyirsdk, pywin32, edge-tts,
sounddevice, SpeechRecognition, pygame and keyboard for the live engineer; anthropic for the optional
Claude API backend.

### Configuration
The live engineer reads `data/ingeniero.json` (created when you pick a voice, audio output or wheel button).
Copy `data/ingeniero.example.json` to start from every option:

| Key | Default | What it does |
|-----|---------|--------------|
| `driver_name` | `"Diego"` | How the radio calls you (`--nombre` saves it) |
| `language` | `"es-CL"` | Speech recognition language |
| `voice` | `"es-CL-CatalinaNeural"` | Neural voice (edge-tts) |
| `audio_output` | `["Auriculares", "Realtek(R) Audio"]` | Output devices to try, first match wins |
| `ptt_joystick`, `ptt_button` | `null` | Wheel button for push-to-talk (`asignar_boton.bat`) |
| `ptt_key` | `"insert"` | Keyboard push-to-talk |
| `claude` | `enabled`, `backend: "claude_code"`, `model: "haiku"`, `effort: "low"` | Voice-question interpreter |
| `tire_targets` | `front_spread 7`, `rear_spread 5`, `mid 0` | Fixed tire temperature targets (°C) instead of learned ones |
| `tire_limits` | 992 Cup profile | Per car (key = part of the car name): `cold_min_kpa`, `cold_max_kpa`, `camber_front`/`camber_rear` `[min, max]`, `hot_window_kpa` `{"front": [a, b], "rear": [a, b]}`, `min_pressure_fastest`, `camber_bias: "max"`, `rear_toe_min_mm` |
| `pad_options` | 992 Cup pads | Front brake-pad options per car (key = part of the car name) |

Running from source, `data/` and `logs/` live in the repo folder; the built `.exe` keeps them next to itself.
Trained ML models are stored in `%APPDATA%\iRacingStatsCalc\ml_models`.

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
├── main.py                    # GUI entry point
├── ingeniero.bat              # Live engineer standalone
├── asignar_boton.bat          # Assign the push-to-talk wheel button
├── elegir_salida.bat          # Pick the radio audio output
├── build_exe.py               # PyInstaller build (create_icon.py makes icon.ico)
├── app/
│   ├── gui.py                 # Tkinter interface
│   ├── data_loader.py         # iRacing stats JSON loading
│   ├── stats_calculator.py    # Race statistics
│   ├── projections.py         # Trend projections
│   ├── charts.py              # Matplotlib charts
│   ├── ml_engine.py           # Machine Learning models
│   ├── ibt_reader.py          # .ibt telemetry file reader
│   ├── telemetry_analyzer.py  # Corner-by-corner .ibt analysis (CLI too)
│   ├── telemetry_view.py      # Telemetry and live engineer tabs
│   ├── progress.py / progress_view.py  # Progress per track and car
│   ├── ab_test.py             # Statistical A/B setup tests
│   ├── setup_db.py            # Setup database per track and car
│   ├── tire_model.py          # Tire model: O/M/I -> camber, pressure and toe (Kalman, gas law, car profiles)
│   ├── chassis_advisor.py     # ARBs, wing, spring perch
│   ├── toe_advisor.py         # Toe per wheel
│   ├── units.py               # Garage units: kPa/psi, °C/°F, mm/inches, 0.5 psi pressure clicks
│   ├── balance_model.py       # Understeer gradient
│   ├── vehicle_dynamics.py    # Physics: grip envelopes, fuel mass
│   ├── driver_inputs.py       # Pedal and steering analysis
│   ├── session_plan.py        # Qualy/race fuel and brake pads
│   ├── references.py          # Brake/throttle references per corner
│   ├── track_limits.py        # Track limits from GPS
│   ├── auto_analysis.py       # Analyze each finished session
│   ├── voice_commands.py      # Push-to-talk, speech recognition, config
│   ├── claude_interpreter.py  # Claude as voice-question interpreter
│   ├── paths.py / util.py     # data/ and logs/ folders, shared helpers
│   ├── live_engineer.py       # Live engineer core loop
│   └── engineer/              # Live engineer parts: audio, fuel, rivals, race control,
│                              # coaching, track, setup coach, recording, voice Q&A, CLI
├── tests/                     # pytest suite (synthetic .ibt files, fake iRacing SDK)
├── data/                      # Your config, setups, progress, track limits (git-ignored)
└── logs/                      # Radio logs and reports (git-ignored)
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
