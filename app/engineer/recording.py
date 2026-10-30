"""Grabación automática de telemetría y análisis de cada sesión terminada."""

import time

from app.engineer.common import P_INFO, irsdk


class RecordingMixin:
    # ------------------------------------------------ grabación automática
    def _auto_recording(self):
        """Graba telemetría a disco mientras estás en el auto (sin Alt+L) y la cierra al bajarte"""
        if not self.auto_record:
            return
        ir = self.ir
        active = ir["IsDiskLoggingActive"]
        if active is None:
            if self.on_track and not self._rec_warned:
                self._rec_warned = True
                self._rec_log("iRacing no informa el estado de la grabación (variables no disponibles)")
            return
        # IsDiskLoggingEnabled llega en falso hasta que algo enciende la grabación (Alt+L o esta orden),
        # aunque app.ini tenga irsdkEnableDisk=1: no sirve para decidir si mandar la orden
        if self.on_track:
            self._off_track_since = None
            if not active and self._tick_n % 60 == 0:   # reintentar como mucho una vez por segundo
                ir.telem_command(irsdk.TelemCommandMode.start)
                if not self._we_record:
                    self._we_record = True
                    self._rec_check_t = time.time()
                    self._rec_log("orden de grabación enviada")
            if active and self._rec_check_t is not None:
                self._rec_log("grabación confirmada por iRacing")
                self._rec_check_t = None
            elif not active and self._rec_check_t is not None and time.time() - self._rec_check_t > 5 \
                    and not self._rec_warned:
                self._rec_warned = True
                self._rec_log("iRacing no aceptó la orden de grabar en 5 s (revisa irsdkEnableDisk=1 en app.ini)")
                self.emit("No pude iniciar la grabación de telemetría: aprieta Alt mas L.", P_INFO, ttl=15)
        else:
            now = time.time()
            if self._off_track_since is None:
                self._off_track_since = now
            elif now - self._off_track_since > 5 and self._we_record and active:
                ir.telem_command(irsdk.TelemCommandMode.stop)
                self._we_record = False
                print("[telemetría] grabación cerrada, se analizará automáticamente", flush=True)

    def _rec_log(self, msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] [telemetría] {msg}"
        print(line, flush=True)
        with self._emit_lock:
            if self.log_file:
                self.log_file.write(line + "\n")
                self.log_file.flush()

    # ------------------------------------------------ análisis automático
    def _analysis_done(self, res, report_path: str):
        from app.auto_analysis import spoken_summary
        print(f"[análisis] reporte guardado en {report_path}", flush=True)
        self.emit(spoken_summary(res), P_INFO, ttl=120)
        self._reload_track_data = True
