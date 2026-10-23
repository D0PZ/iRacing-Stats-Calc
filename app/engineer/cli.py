"""Línea de comandos del ingeniero en vivo (python -m app.live_engineer / ingeniero.bat)."""

import os
import time

from app.engineer.audio import DEFAULT_NEURAL_VOICE, Voice, list_output_devices
from app.engineer.common import P_BATTLE, P_INFO
from app.paths import logs_dir


def main():
    import argparse
    import os
    import sys
    parser = argparse.ArgumentParser(description="Ingeniero de carrera en vivo para iRacing")
    parser.add_argument("--nombre", default=None, help="Tu nombre para la radio (queda guardado)")
    parser.add_argument("--sin-voz", action="store_true", help="Solo texto en consola")
    parser.add_argument("--voz", default=None,
                        help="Voz neuronal (ej: es-AR-TomasNeural, es-MX-JorgeNeural, es-CL-CatalinaNeural)")
    parser.add_argument("--voz-windows", action="store_true", help="Usar la voz clásica de Windows (sin internet)")
    parser.add_argument("--elegir-salida", action="store_true",
                        help="Elegir el dispositivo de audio por donde habla la ingeniera (audífonos)")
    parser.add_argument("--asignar-boton", action="store_true",
                        help="Aprieta el botón del volante que quieres usar para hablarle al ingeniero")
    parser.add_argument("--tecla", default=None, help="Tecla push-to-talk alternativa (por defecto Insert)")
    parser.add_argument("--sin-ptt", action="store_true", help="Desactivar las preguntas por voz")
    parser.add_argument("--sin-grabar", action="store_true",
                        help="No iniciar la grabación de telemetría automáticamente (usar Alt+L)")
    args = parser.parse_args()
    from app.live_engineer import LiveEngineer
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    log_dir = logs_dir()
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, time.strftime("radio_%Y%m%d_%H%M%S.txt"))
    from app.voice_commands import PushToTalk, load_config, save_config, wait_for_button
    cfg = load_config()
    if args.elegir_salida:
        devices = list_output_devices()
        if not devices:
            print("No encontré salidas de audio.")
            return
        print("Salidas de audio:")
        for i, name in enumerate(devices, 1):
            mark = "  <- actual" if name in (cfg.get("audio_output") or []) else ""
            print(f"  {i}. {name}{mark}")
        choice = input("Escribe el número de la salida para la radio y presiona Enter: ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(devices):
            print("Número inválido, no cambié nada.")
            return
        name = devices[int(choice) - 1]
        cfg["audio_output"] = [name]
        save_config(cfg)
        print(f"Listo: la radio va a salir por '{name}'. Escucha la prueba...")
        test = Voice(neural_voice=cfg.get("voice") or DEFAULT_NEURAL_VOICE, outputs=[name])
        test.say("Prueba de radio. Si me escuchas aquí, la salida quedó bien.", P_INFO, ttl=20)
        time.sleep(6)
        test.stop()
        return
    if args.asignar_boton:
        print("Aprieta el botón del volante que quieres usar para hablar (15 s)...", flush=True)
        found = wait_for_button()
        if not found:
            print("No detecté ningún botón.")
            return
        cfg["ptt_joystick"], cfg["ptt_button"] = found
        save_config(cfg)
        print(f"Listo: botón {found[1]} de {found[0]} guardado.")
        return
    if args.tecla:
        cfg["ptt_key"] = args.tecla
        save_config(cfg)
    if args.voz:
        cfg["voice"] = args.voz
        save_config(cfg)
    if args.nombre:
        cfg["driver_name"] = args.nombre
        save_config(cfg)
    voice = cfg.get("voice") or DEFAULT_NEURAL_VOICE

    eng = LiveEngineer(driver_name=cfg.get("driver_name") or "Diego", speak=not args.sin_voz,
                       neural_voice=None if args.voz_windows else voice, log_path=log_path,
                       auto_record=not args.sin_grabar)
    if not args.sin_ptt:
        ptt = PushToTalk(on_text=eng.ask, on_error=lambda m: eng.emit(m, P_BATTLE + 5, ttl=6, always=True),
                         config=cfg)
        ptt.start()
    try:
        eng.run()
    except KeyboardInterrupt:
        eng.stop()
