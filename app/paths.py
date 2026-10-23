"""
Carpetas de la aplicación (data/ y logs/).

Desde el código fuente cuelgan de la raíz del repo. En el .exe de PyInstaller (--onefile) __file__ apunta a
una carpeta temporal que se borra al cerrar, así que se usan las carpetas junto al ejecutable.
"""

import os
import sys


def app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir(*parts: str) -> str:
    return os.path.join(app_root(), "data", *parts)


def logs_dir() -> str:
    return os.path.join(app_root(), "logs")
