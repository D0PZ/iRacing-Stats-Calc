"""
iRacing Stats Calculator
Punto de entrada principal de la aplicación
"""

import sys
import os

# Añadir el directorio raíz al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.gui import main

if __name__ == "__main__":
    main()
