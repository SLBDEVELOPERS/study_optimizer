from datetime import datetime
import base64
from collections import deque
import json
from pathlib import Path
import time

import cv2
from PySide6.QtCore import QObject, QTimer, Signal

from camera.face_analyzer import FaceEyeAnalyzer
from camera.posture_analyzer import PostureAnalyzer
from config import CONFIG, SystemState, ensure_models, logger, save_config
from data.reports import format_session_report
from data.session_logger import SessionLogger
from device.device_pairing import build_device_settings_payload, pair_device, update_device_connection


def session_clock(started_at: float) -> str:
    elapsed = int(time.time() - started_at)
    return f"{elapsed // 3600:02d}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}"


class AlertManager:
    def __init__(self, config, esp32):
        self.config = config
        self.esp32 = esp32

    def check(self, state):
        now = time.time()
        posture = state.posture
        fatigue = state.fatigue
        bad_posture = posture.is_slouching or posture.is_forward_head or posture.is_too_close

        if bad_posture:
            if posture.bad_posture_start == 0:
                posture.bad_posture_start = now
            elif (
                now - posture.bad_posture_start >= self.config.SLOUCH_CONFIRM_SECONDS
                and now - posture.last_alert_time >= self.config.POSTURE_ALERT_COOLDOWN
            ):
                posture.last_alert_time = now
                posture.alert_count += 1
                state.total_alerts += 1
                if self.config.SILENT_MODE:
                    state.device.last_command_status = "Posture alert recorded in silent mode"
                else:
                    sent = self.esp32.posture_buzz()
                    state.device.last_command_status = "Posture alert sent" if sent else "Posture alert simulated"
        else:
            posture.bad_posture_start = 0

        if not self.config.FATIGUE_ALERT_ENABLED:
            return

        if (
            fatigue.is_drowsy
            and fatigue.drowsy_start > 0
            and now - fatigue.drowsy_start >= self.config.DROWSY_CONFIRM_SECONDS
            and now - fatigue.last_alert_time >= self.config.DROWSY_ALERT_COOLDOWN
        ):
            fatigue.last_alert_time = now
            fatigue.alert_count += 1
            state.total_alerts += 1
            if self.config.SILENT_MODE:
                state.device.last_command_status = "Fatigue alert recorded in silent mode"
            else:
                sent = self.esp32.drowsy_buzz()
                state.device.last_command_status = "Drowsy alert sent" if sent else "Drowsy alert simulated"


class BackendService(QObject):
    status_updated = Signal(str)
    preview_updated = Signal(str)
    reports_updated = Signal(str)

    def __init__(self):
        super().__init__()
        ensure_models()

        self.config = CONFIG
        self.state = SystemState()
        self.state.environment.temperature_c = self.config.DEFAULT_TEMPERATURE_C
        self.state.device.fan_on = self.config.DEFAULT_FAN_ON
        self.state.device.lamp_brightness = self.config.DEFAULT_LAMP_BRIGHTNESS
        self.state.device.auto_mode = self.config.AUTO_MODE
        self.state.device.silent_mode = self.config.SILENT_MODE
        self.state.device.wifi_ssid = self.config.DEVICE_WIFI_SSID

        self.esp32 = pair_device(self.config)
        self.state.esp32_connected = self.esp32.connected
        self.pose_analyzer = PostureAnalyzer(self.config.POSE_MODEL, self.config)
        self.face_analyzer = FaceEyeAnalyzer(self.config.FACE_MODEL, self.config)
        self.alerts = AlertManager(self.config, self.esp32)
        self.session_logger = SessionLogger(self.config.REPORTS_FILE)

        self.cap = cv2.VideoCapture(self.config.CAMERA_INDEX)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.FRAME_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.FRAME_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, self.config.FPS)
        self.previous_tick = time.time()
        self.current_fps = 0.0
        self._reports_saved = False
        self._frame_skip = 0
        self.camera_paused = False
        self.camera_available = self.cap.isOpened()
        self.last_snapshot_path = ""
        self.blink_history = deque([0.0] * 20, maxlen=20)
        self.light_history = deque([0] * 20, maxlen=20)
        self.alert_history = deque([0] * 20, maxlen=20)

        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self.process_frame)
        self.frame_timer.start(max(16, int(1000 / max(self.config.FPS, 1))))

        self.break_timer = QTimer(self)
        self.break_timer.timeout.connect(self.check_break_reminder)
        self.break_timer.start(60_000)

    def initial_payload(self) -> dict:
        return {
            "status": self.metrics(),
            "settings": self.settings_payload(),
            "reports": self.reports_payload(),
        }

    def emit_status(self):
        payload = json.dumps(self.metrics())
        self.status_updated.emit(f"window.app && window.app.updateStatus({payload});")

    def emit_preview(self, image_b64: str):
        self.preview_updated.emit(f"window.app && window.app.updatePreview('{image_b64}');")

    def emit_reports(self):
        payload = json.dumps(self.reports_payload())
        self.reports_updated.emit(f"window.app && window.app.updateReports({payload});")

    def process_frame(self):
        self.camera_available = self.cap.isOpened()
        if not self.camera_available:
            self.state.device.last_command_status = "Camera unavailable"
            self.emit_status()
            return

        if self.camera_paused:
            self.state.device.last_command_status = "Camera paused"
            self.emit_status()
            return

        if not self.cap.isOpened():
            return

        ret, frame = self.cap.read()
        if not ret:
            self.state.device.last_command_status = "Camera feed lost"
            self.emit_status()
            return

        frame = cv2.flip(frame, 1)
        height, width = frame.shape[:2]
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        try:
            self.state.posture, pose_landmarks = self.pose_analyzer.analyze(frame_rgb, self.state.posture, height, width)
            self.pose_analyzer.draw(frame, pose_landmarks, width, height)
        except Exception as exc:
            logger.debug("Pose error: %s", exc)

        try:
            self.state.fatigue, self.state.posture, face_landmarks = self.face_analyzer.analyze(
                frame_rgb,
                self.state.fatigue,
                self.state.posture,
                height,
                width,
            )
            self.face_analyzer.draw(frame, face_landmarks, self.state.fatigue, width, height)
        except Exception as exc:
            logger.debug("Face error: %s", exc)

        self.update_environment(frame_rgb)
        self.run_automation()
        self.alerts.check(self.state)

        now = time.time()
        self.current_fps = 1.0 / (now - self.previous_tick + 1e-6)
        self.previous_tick = now
        cv2.putText(frame, f"{self.current_fps:.0f} fps", (width - 100, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 40, 60), 2)

        self.blink_history.append(round(self.state.fatigue.blink_rate, 1))
        self.light_history.append(self.state.environment.room_light_level)
        posture_bad = int(self.state.posture.is_slouching or self.state.posture.is_forward_head or self.state.posture.is_too_close)
        fatigue_bad = int(self.state.fatigue.is_drowsy)
        self.alert_history.append(posture_bad + fatigue_bad)

        self._frame_skip = (self._frame_skip + 1) % 2
        if self._frame_skip == 0:
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
            if ok:
                b64 = base64.b64encode(encoded.tobytes()).decode("ascii")
                self.emit_preview(b64)

        self.emit_status()

    def update_environment(self, frame_rgb):
        brightness = int(frame_rgb.mean() / 255 * 100)
        self.state.environment.room_light_level = brightness
        self.state.environment.room_light_status = "Low" if brightness < 42 else "Good"

    def run_automation(self):
        if not self.config.AUTO_MODE:
            return

        if self.config.AUTO_LAMP:
            target_lamp = 85 if self.state.environment.room_light_status == "Low" else 35
            if target_lamp != self.state.device.lamp_brightness:
                self.state.device.lamp_brightness = target_lamp
                self.esp32.set_lamp_brightness(target_lamp)

        if self.config.AUTO_FAN:
            target_fan = self.state.environment.temperature_c >= 30 or self.state.fatigue.is_drowsy
            if target_fan != self.state.device.fan_on:
                self.state.device.fan_on = target_fan
                self.esp32.set_fan(target_fan)

    def metrics(self) -> dict:
        posture_bad = self.state.posture.is_slouching or self.state.posture.is_forward_head or self.state.posture.is_too_close
        posture_detail = "Shoulders aligned"
        if self.state.posture.is_slouching:
            posture_detail = f"Shoulder angle {self.state.posture.shoulder_angle:.1f} deg"
        elif self.state.posture.is_forward_head:
            posture_detail = f"Head ratio {self.state.posture.head_forward_ratio:.2f}"
        elif self.state.posture.is_too_close:
            posture_detail = "Face too close to screen"

        fatigue_status = "Drowsy" if self.state.fatigue.is_drowsy else "Alert"
        fatigue_detail = "Blink pattern stable"
        if self.state.fatigue.is_drowsy:
            fatigue_detail = f"EAR {self.state.fatigue.ear_avg:.3f} / {self.state.fatigue.blink_rate:.1f} min"

        return {
            "posture_status": "Bad" if posture_bad else "Good",
            "posture_detail": posture_detail,
            "fatigue_status": fatigue_status,
            "fatigue_detail": fatigue_detail,
            "room_light": self.state.environment.room_light_status,
            "room_light_level": self.state.environment.room_light_level,
            "temperature_c": self.state.environment.temperature_c,
            "fan_on": self.state.device.fan_on,
            "fan_detail": "Auto cooling active" if self.state.device.fan_on else "Fan idle",
            "lamp_brightness": self.state.device.lamp_brightness,
            "lamp_detail": f"{'Auto' if self.config.AUTO_MODE else 'Manual'} brightness control",
            "today_alerts": self.state.total_alerts,
            "session_time": session_clock(self.state.session_start),
            "blink_rate": self.state.fatigue.blink_rate,
            "esp32_connected": self.state.esp32_connected,
            "fps": round(self.current_fps, 1),
            "last_command_status": self.state.device.last_command_status,
            "auto_mode": self.config.AUTO_MODE,
            "silent_mode": self.config.SILENT_MODE,
            "break_due": self.state.break_due,
            "blink_history": list(self.blink_history),
            "light_history": list(self.light_history),
            "alert_history": list(self.alert_history),
            "transport_mode": self.config.ESP32_MODE.upper(),
            "camera_paused": self.camera_paused,
            "camera_available": self.camera_available,
            "camera_status": self.camera_status(),
            "last_snapshot_path": self.last_snapshot_path,
        }

    def camera_status(self) -> str:
        if not self.camera_available:
            return "Unavailable"
        if self.camera_paused:
            return "Paused"
        return "Live"

    def settings_payload(self) -> dict:
        return {
            "esp32_url": self.config.ESP32_HTTP_URL,
            "camera_index": self.config.CAMERA_INDEX,
            "posture_sensitivity": self.config.POSTURE_SENSITIVITY,
            "fatigue_sensitivity": self.config.FATIGUE_SENSITIVITY,
            "fatigue_alert": self.config.FATIGUE_ALERT_ENABLED,
            "silent_mode": self.config.SILENT_MODE,
            "auto_fan": self.config.AUTO_FAN,
            "auto_lamp": self.config.AUTO_LAMP,
            "auto_mode": self.config.AUTO_MODE,
            "break_reminder_minutes": self.config.BREAK_REMINDER_MINUTES,
            "startup_auto_launch": self.config.STARTUP_AUTO_LAUNCH,
            "minimize_to_tray": self.config.MINIMIZE_TO_TRAY,
            "default_temperature_c": self.config.DEFAULT_TEMPERATURE_C,
            "default_lamp_brightness": self.config.DEFAULT_LAMP_BRIGHTNESS,
            "device_wifi_ssid": self.config.DEVICE_WIFI_SSID,
            "device_wifi_password": self.config.DEVICE_WIFI_PASSWORD,
        }

    def reports_payload(self) -> dict:
        return {
            "history": self.session_logger.load_history(),
            "daily": self.session_logger.load_daily_summary(),
        }

    def pair_device(self, payload: dict):
        self.config.DEVICE_WIFI_SSID = payload.get("device_wifi_ssid", self.config.DEVICE_WIFI_SSID)
        self.config.DEVICE_WIFI_PASSWORD = payload.get("device_wifi_password", self.config.DEVICE_WIFI_PASSWORD)
        endpoint = payload.get("endpoint", "").strip()
        mode = payload.get("mode", self.config.ESP32_MODE)
        if endpoint:
            update_device_connection(self.config, mode, endpoint)
        self.esp32.close()
        self.esp32 = pair_device(self.config)
        self.alerts = AlertManager(self.config, self.esp32)
        self.state.esp32_connected = self.esp32.connected
        self.state.device.wifi_ssid = self.config.DEVICE_WIFI_SSID
        self.state.device.paired_device_name = "ESP32 Desk Node" if self.state.esp32_connected else "Simulation Device"
        self.state.device.last_command_status = "Pairing updated"
        save_config(self.config)
        self.emit_status()

    def sync_device_settings(self):
        payload = build_device_settings_payload(self.config)
        sent = self.esp32.push_settings(payload)
        self.state.device.last_command_status = "Device settings synced" if sent else "Device settings simulated"
        self.emit_status()

    def refresh_device_status(self):
        status = self.esp32.get_status()
        self.state.esp32_connected = bool(status.get("connected"))
        if self.state.esp32_connected:
            self.state.device.last_command_status = "Device reachable"
            self.state.device.paired_device_name = status.get("device_name", "ESP32 Desk Node")
            self.state.device.fan_on = status.get("fan_on", self.state.device.fan_on)
            self.state.device.lamp_brightness = status.get("lamp_brightness", self.state.device.lamp_brightness)
            self.state.environment.temperature_c = status.get("temperature_c", self.state.environment.temperature_c)
        else:
            self.state.device.last_command_status = "Simulation mode"
        self.emit_status()

    def apply_settings(self, payload: dict):
        old_camera_index = self.config.CAMERA_INDEX
        self.config.update_from_user_settings(payload)
        self.state.environment.temperature_c = self.config.DEFAULT_TEMPERATURE_C
        self.state.device.auto_mode = self.config.AUTO_MODE
        self.state.device.silent_mode = self.config.SILENT_MODE

        if old_camera_index != self.config.CAMERA_INDEX:
            if self.cap.isOpened():
                self.cap.release()
            self.cap = cv2.VideoCapture(self.config.CAMERA_INDEX)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.FRAME_WIDTH)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.FRAME_HEIGHT)
            self.cap.set(cv2.CAP_PROP_FPS, self.config.FPS)

        save_config(self.config)
        self.state.device.last_command_status = "Settings applied"
        self.emit_status()

    def toggle_mode(self):
        self.config.AUTO_MODE = not self.config.AUTO_MODE
        self.state.device.auto_mode = self.config.AUTO_MODE
        self.state.device.last_command_status = "Auto mode enabled" if self.config.AUTO_MODE else "Manual mode enabled"
        save_config(self.config)
        self.emit_status()

    def toggle_camera(self):
        if not self.cap.isOpened():
            self.state.device.last_command_status = "Camera unavailable"
            self.emit_status()
            return
        self.camera_paused = not self.camera_paused
        self.state.device.last_command_status = "Camera paused" if self.camera_paused else "Camera resumed"
        self.emit_status()

    def capture_snapshot(self):
        if not self.cap.isOpened():
            self.state.device.last_command_status = "Snapshot failed: camera unavailable"
            self.emit_status()
            return

        ret, frame = self.cap.read()
        if not ret:
            self.state.device.last_command_status = "Snapshot failed: feed lost"
            self.emit_status()
            return

        snapshots_dir = Path("snapshots")
        snapshots_dir.mkdir(exist_ok=True)
        filename = snapshots_dir / f"snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        cv2.imwrite(str(filename), frame)
        self.last_snapshot_path = str(filename)
        self.state.device.last_command_status = f"Snapshot saved: {filename.name}"
        self.emit_status()

    def toggle_silent_mode(self):
        self.config.SILENT_MODE = not self.config.SILENT_MODE
        self.state.device.silent_mode = self.config.SILENT_MODE
        self.state.device.last_command_status = "Silent mode enabled" if self.config.SILENT_MODE else "Silent mode disabled"
        save_config(self.config)
        self.emit_status()

    def toggle_fan(self):
        self.state.device.fan_on = not self.state.device.fan_on
        sent = self.esp32.set_fan(self.state.device.fan_on)
        self.state.device.last_command_status = "Fan command sent" if sent else "Fan simulated"
        self.emit_status()

    def set_lamp_brightness(self, brightness: int):
        self.state.device.lamp_brightness = brightness
        self.config.DEFAULT_LAMP_BRIGHTNESS = brightness
        sent = self.esp32.set_lamp_brightness(brightness)
        self.state.device.last_command_status = "Lamp brightness sent" if sent else "Lamp simulated"
        save_config(self.config)
        self.emit_status()

    def trigger_posture_alert(self):
        if self.config.SILENT_MODE:
            self.state.device.last_command_status = "Silent mode skipped posture buzzer"
        else:
            sent = self.esp32.posture_buzz()
            self.state.device.last_command_status = "Posture test sent" if sent else "Posture test simulated"
        self.emit_status()

    def trigger_drowsy_alert(self):
        if self.config.SILENT_MODE:
            self.state.device.last_command_status = "Silent mode skipped drowsy buzzer"
        else:
            sent = self.esp32.drowsy_buzz()
            self.state.device.last_command_status = "Drowsy test sent" if sent else "Drowsy test simulated"
        self.emit_status()

    def check_break_reminder(self):
        elapsed_minutes = (time.time() - self.state.last_break_at) / 60.0
        if elapsed_minutes < self.config.BREAK_REMINDER_MINUTES:
            return
        self.state.last_break_at = time.time()
        self.state.break_due = True
        self.state.device.last_command_status = "Break reminder is due"
        self.emit_status()

    def save_session_report(self):
        if self._reports_saved:
            return
        summary = self.session_logger.build_summary(self.state)
        self.session_logger.append_summary(summary)
        self._reports_saved = True
        logger.info(format_session_report(summary))
        self.emit_reports()

    def shutdown(self):
        self.frame_timer.stop()
        self.break_timer.stop()
        self.save_session_report()
        if self.cap.isOpened():
            self.cap.release()
        self.pose_analyzer.close()
        self.face_analyzer.close()
        self.esp32.close()
