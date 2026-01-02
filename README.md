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
