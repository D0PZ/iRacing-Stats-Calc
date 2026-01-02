"""
Script para construir el ejecutable de iRacing Stats Calculator
Ejecutar: python build_exe.py
"""

import subprocess
import sys
import os

def build():
    # Obtener el directorio actual
    base_dir = os.path.dirname(os.path.abspath(__file__))
    main_file = os.path.join(base_dir, "main.py")
    icon_file = os.path.join(base_dir, "icon.ico")
    
    # Verificar que existe el icono
    if not os.path.exists(icon_file):
        print("⚠️ Icono no encontrado, creándolo...")
        subprocess.run([sys.executable, os.path.join(base_dir, "create_icon.py")])
    
    # Comando de PyInstaller
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=iRacing Stats Calculator",  # Nombre del ejecutable
        "--onefile",                         # Un solo archivo .exe
        "--windowed",                        # Sin consola (app GUI)
        "--noconfirm",                       # Sobrescribir sin preguntar
        "--clean",                           # Limpiar cache
        "--add-data=app;app",               # Incluir el módulo app
        f"--icon={icon_file}",              # Icono personalizado
        f"--distpath={os.path.join(base_dir, 'dist')}",
        f"--workpath={os.path.join(base_dir, 'build')}",
        f"--specpath={base_dir}",
        main_file
    ]
    
    print("=" * 50)
    print("Construyendo iRacing Stats Calculator...")
    print("=" * 50)
    print(f"Comando: {' '.join(cmd)}")
    print()
    
    # Ejecutar PyInstaller
    result = subprocess.run(cmd, cwd=base_dir)
    
    if result.returncode == 0:
        exe_path = os.path.join(base_dir, "dist", "iRacing Stats Calculator.exe")
        print()
        print("=" * 50)
        print("✅ ¡BUILD EXITOSO!")
        print("=" * 50)
        print(f"Ejecutable creado en:")
        print(f"  {exe_path}")
        print()
        print("Puedes copiar este .exe a cualquier lugar y ejecutarlo.")
    else:
        print()
        print("❌ Error durante el build")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(build())
