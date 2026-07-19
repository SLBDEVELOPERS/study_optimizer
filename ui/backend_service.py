from datetime import datetime
import base64
from collections import deque
import json
from pathlib import Path
import re
import threading
import time

import cv2
from PySide6.QtCore import QObject, QTimer, Signal

from camera.face_analyzer import FaceEyeAnalyzer
from camera.posture_analyzer import PostureAnalyzer
from config import CONFIG, SystemState, ensure_models, logger, save_config
from data.reports import format_session_report
from data.session_logger import SessionLogger
from data.validation_logger import ValidationLogger
from device.device_pairing import (
    auto_discover_endpoint,
    build_device_settings_payload,
    pair_device,
    provision_wifi,
    update_device_connection,
)
from ui.mobile_api import MobileApiServer
from voice.voice_controller import VoiceListenerThread


def session_clock(started_at: float) -> str:
    elapsed = int(time.time() - started_at)
    return f"{elapsed // 3600:02d}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}"


class AlertManager:
    def __init__(self, config, esp32):
        self.config = config
        self.esp32 = esp32
        self._posture_was_bad = False
        self._fatigue_was_bad = False

    def check(self, state):
        now = time.time()
        posture = state.posture
        fatigue = state.fatigue
        bad_posture = posture.is_slouching or posture.is_forward_head or posture.is_too_close

        if self._posture_was_bad and not bad_posture:
            self.esp32.clear_posture_alert()
        self._posture_was_bad = bad_posture

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
                    self.esp32.posture_buzz()
                    state.device.last_command_status = "Posture alert recorded in silent mode"
                else:
                    sent = self.esp32.posture_buzz()
                    state.device.last_command_status = "Posture alert sent" if sent else "Posture alert simulated"
        else:
            posture.bad_posture_start = 0

        fatigue_bad = self.config.FATIGUE_ALERT_ENABLED and fatigue.is_drowsy
        if self._fatigue_was_bad and not fatigue_bad:
            self.esp32.clear_fatigue_alert()
        self._fatigue_was_bad = fatigue_bad

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
                self.esp32.drowsy_buzz()
                state.device.last_command_status = "Fatigue alert recorded in silent mode"
            else:
                sent = self.esp32.drowsy_buzz()
                state.device.last_command_status = "Drowsy alert sent" if sent else "Drowsy alert simulated"


class BackendService(QObject):
    status_updated = Signal(str)
    preview_updated = Signal(str)
    reports_updated = Signal(str)
    device_status_received = Signal(object)

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

        self.esp32 = pair_device(self.config)
        self.state.esp32_connected = self.esp32.connected
        if self.state.esp32_connected:
            self.esp32.push_settings(build_device_settings_payload(self.config))
        self.pose_analyzer = PostureAnalyzer(self.config.POSE_MODEL, self.config)
        self.face_analyzer = FaceEyeAnalyzer(self.config.FACE_MODEL, self.config)
        self.alerts = AlertManager(self.config, self.esp32)
        self.session_logger = SessionLogger(self.config.REPORTS_FILE)
        self.validation_logger = ValidationLogger(self.config.VALIDATION_LOG_FILE)

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
        self.mobile_api = MobileApiServer(self)
        self.mobile_api.start()
        logger.info("Mobile API listening on http://0.0.0.0:8765")

        self._device_poll_running = False
        self.device_status_received.connect(self.apply_device_status)
        self.device_poll_timer = QTimer(self)
        self.device_poll_timer.timeout.connect(self.poll_device_status)
        self.device_poll_timer.start(2_000)
        self.poll_device_status()

        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self.process_frame)
        self.frame_timer.start(max(16, int(1000 / max(self.config.FPS, 1))))

        self.break_timer = QTimer(self)
        self.break_timer.timeout.connect(self.check_break_reminder)
        self.break_timer.start(60_000)

        self.voice_listening = False
        self.voice_thread = VoiceListenerThread(self)
        self.voice_thread.command_recognized.connect(self.handle_voice_command)
        self.voice_thread.listening_started.connect(self.on_voice_listening_started)
        self.voice_thread.listening_stopped.connect(self.on_voice_listening_stopped)
        self.voice_thread.start()

    def on_voice_listening_started(self):
        self.voice_listening = True
        self.state.device.last_command_status = "Voice control active"
        self.emit_status()

    def on_voice_listening_stopped(self):
        self.voice_listening = False
        self.emit_status()

    def handle_voice_command(self, text: str):
        if "fan on" in text:
            if not self.state.device.fan_on:
                self.toggle_fan()
        elif "fan off" in text:
            if self.state.device.fan_on:
                self.toggle_fan()
        elif "lamp brightness" in text:
            match = re.search(r"lamp brightness (\d+)", text)
            if match:
                self.set_lamp_brightness(int(match.group(1)))
        elif "lamp on" in text:
            self.set_lamp_brightness(100)
        elif "lamp off" in text:
            self.set_lamp_brightness(0)
        elif "auto mode on" in text:
            if not self.config.AUTO_MODE:
                self.toggle_mode()
        elif "auto mode off" in text:
            if self.config.AUTO_MODE:
                self.toggle_mode()
        elif "silent mode on" in text:
            if not self.config.SILENT_MODE:
                self.toggle_silent_mode()
        elif "silent mode off" in text:
            if self.config.SILENT_MODE:
                self.toggle_silent_mode()
        elif "recalibrate" in text or "reset calibration" in text:
            self.reset_calibration()
        elif "pause camera" in text:
            if not self.camera_paused:
                self.toggle_camera()
        elif "resume camera" in text:
            if self.camera_paused:
                self.toggle_camera()
        elif "take snapshot" in text:
            self.capture_snapshot()
        elif "test posture alert" in text:
            self.trigger_posture_alert()
        elif "test drowsy alert" in text:
            self.trigger_drowsy_alert()
        else:
            logger.debug(f"Unhandled voice command: {text}")

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

        self.update_environment(frame_rgb, face_landmarks, width, height)
        self.run_automation()
        self.alerts.check(self.state)

        self.state.total_frames += 1
        posture_bad = (
            self.state.posture.is_slouching
            or self.state.posture.is_forward_head
            or self.state.posture.is_too_close
        )
        if not posture_bad:
            self.state.good_posture_frames += 1

        now = time.time()
        self.current_fps = 1.0 / (now - self.previous_tick + 1e-6)
        self.previous_tick = now
        self.log_validation_frame()
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

    def log_validation_frame(self):
        if not self.config.VALIDATION_LOG_ENABLED:
            return
        if self.state.total_frames % self.config.VALIDATION_LOG_EVERY_N_FRAMES != 0:
            return
        try:
            self.validation_logger.append_state(
                self.state,
                self.state.total_frames,
                self.current_fps,
                self.camera_status(),
            )
        except Exception as exc:
            logger.debug("Validation log error: %s", exc)

    def update_environment(self, frame_rgb, face_landmarks=None, width: int = 0, height: int = 0):
        brightness = int(frame_rgb.mean() / 255 * 100)
        if face_landmarks is not None and width > 0 and height > 0:
            xs = [lm.x for lm in face_landmarks]
            ys = [lm.y for lm in face_landmarks]
            x1 = max(0, int(min(xs) * width))
            x2 = min(width, int(max(xs) * width))
            y1 = max(0, int(min(ys) * height))
            y2 = min(height, int(max(ys) * height))
            if x2 > x1 and y2 > y1:
                region = frame_rgb[y1:y2, x1:x2]
                brightness = int(region.mean() / 255 * 100)
        self.state.environment.room_light_level = brightness
        self.state.environment.room_light_status = "Low" if brightness < 42 else "Good"

    def run_automation(self):
        # The ESP32 owns automatic fan/lamp control using its DHT22 and LDR.
        # The desktop only sends settings/manual overrides and displays status.
        return

    def format_posture_detail(self) -> str:
        posture = self.state.posture
        reasons = list(posture.posture_reasons)
        if posture.is_too_close and "Too close to screen" not in reasons:
            reasons.append("Too close to screen")
        details = {
            "Shoulder imbalance": f"Shoulder imbalance ({posture.shoulder_angle:.1f} deg)",
            "Head dropped": f"Head dropped (drop {posture.head_drop_ratio:.2f})",
            "Forward head": f"Forward head (depth {posture.head_forward_ratio:.2f})",
            "Back leaning": f"Back leaning ({posture.back_lean_angle:.1f} deg)",
            "Too close to screen": f"Too close to screen ({posture.face_size_ratio:.2f})",
        }
        reasons = reasons or [posture.posture_reason]
        return " | ".join(details.get(reason, reason) for reason in reasons if reason) or "Posture needs attention"

    def metrics(self) -> dict:
        posture_bad = self.state.posture.is_slouching or self.state.posture.is_forward_head or self.state.posture.is_too_close
        posture_reasons = list(self.state.posture.posture_reasons)
        if self.state.posture.is_too_close and "Too close to screen" not in posture_reasons:
            posture_reasons.append("Too close to screen")
        posture_detail = "Shoulders aligned"
        if self.state.posture.is_forward_head:
            posture_detail = f"Forward head (depth {self.state.posture.head_forward_ratio:.2f})"
        elif self.state.posture.is_slouching:
            drop = getattr(self.state.posture, 'head_drop_ratio', 0.0)
            posture_detail = f"Slouching (tilt {self.state.posture.shoulder_angle:.1f}° / drop {drop:.2f})"
        elif self.state.posture.is_too_close:
            posture_detail = "Face too close to screen"
        if posture_bad:
            posture_detail = self.format_posture_detail()

        fatigue_status = "Drowsy" if self.state.fatigue.is_drowsy else "Alert"
        fatigue_detail = "Blink pattern stable"
        if self.state.fatigue.is_yawning:
            fatigue_detail = f"Yawning detected (EAR {self.state.fatigue.ear_avg:.3f})"
        elif self.state.fatigue.is_drowsy:
            fatigue_detail = f"EAR {self.state.fatigue.ear_avg:.3f} / {self.state.fatigue.blink_rate:.1f} bpm"

        return {
            "posture_status": "Bad" if posture_bad else "Good",
            "posture_detail": posture_detail,
            "posture_reason": posture_reasons[0] if posture_reasons else self.state.posture.posture_reason,
            "posture_reasons": posture_reasons,
            "posture_score": self.state.posture.posture_score,
            "posture_confidence": round(self.state.posture.posture_confidence, 3),
            "shoulder_confidence": round(self.state.posture.shoulder_confidence, 3),
            "head_drop_confidence": round(self.state.posture.head_drop_confidence, 3),
            "forward_head_confidence": round(self.state.posture.forward_head_confidence, 3),
            "back_lean_confidence": round(self.state.posture.back_lean_confidence, 3),
            "face_distance_confidence": round(self.state.posture.face_distance_confidence, 3),
            "shoulder_imbalance_confirmed": self.state.posture.shoulder_imbalance_confirmed,
            "head_drop_confirmed": self.state.posture.head_drop_confirmed,
            "forward_head_confirmed": self.state.posture.forward_head_confirmed,
            "back_lean_confirmed": self.state.posture.back_lean_confirmed,
            "back_lean_angle": self.state.posture.back_lean_angle,
            "torso_z_delta": self.state.posture.torso_z_delta,
            "fatigue_status": fatigue_status,
            "fatigue_detail": fatigue_detail,
            "fatigue_score": self.state.fatigue.fatigue_score,
            "fatigue_confidence": round(self.state.fatigue.fatigue_confidence, 3),
            "eye_confidence": round(self.state.fatigue.eye_confidence, 3),
            "yawn_confidence": round(self.state.fatigue.yawn_confidence, 3),
            "nod_confidence": round(self.state.fatigue.nod_confidence, 3),
            "nod_drop_ratio": round(self.state.fatigue.nod_drop_ratio, 3),
            "blink_confidence": round(self.state.fatigue.blink_confidence, 3),
            "focus_confidence": round(self.state.fatigue.focus_confidence, 3),
            "gaze_away_ratio": round(self.state.fatigue.gaze_away_ratio, 3),
            "focus_lost": self.state.fatigue.is_focus_lost,
            "focus_loss_reason": self.state.fatigue.focus_loss_reason,
            "face_visible": self.state.fatigue.face_visible,
            "perclos": round(self.state.fatigue.perclos, 3),
            "yawn_count": self.state.fatigue.yawn_count,
            "nod_count": self.state.fatigue.nod_count,
            "is_yawning": self.state.fatigue.is_yawning,
            "mar": round(self.state.fatigue.mar, 3),
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
            "voice_listening": self.voice_listening,
            "posture_calibrated": self.pose_analyzer._calibrated,
            "posture_landmarks_visible": self.state.posture.landmarks_visible,
            "hips_visible": self.state.posture.hips_visible,
            "calibration_status": self.state.posture.calibration_status,
            "calibration_progress": round(self.state.posture.calibration_progress, 1),
            "calibration_rejected_frames": self.state.posture.calibration_rejected_frames,
            "ear_calibrated": self.face_analyzer._personal_ear_valid,
            "posture_good_pct": round(
                self.state.good_posture_frames / max(1, self.state.total_frames) * 100, 1
            ),
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
            "validation_log_enabled": self.config.VALIDATION_LOG_ENABLED,
            "validation_log_every_n_frames": self.config.VALIDATION_LOG_EVERY_N_FRAMES,
        }

    def reports_payload(self) -> dict:
        return {
            "history": self.session_logger.load_history(),
            "daily": self.session_logger.load_daily_summary(),
        }

    def pair_device(self, payload: dict):
        endpoint = payload.get("endpoint", "").strip()
        mode = payload.get("mode", self.config.ESP32_MODE)
        if endpoint:
            update_device_connection(self.config, mode, endpoint)
        self.esp32.close()
        self.esp32 = pair_device(self.config)
        self.alerts = AlertManager(self.config, self.esp32)
        self.state.esp32_connected = self.esp32.connected
        if self.state.esp32_connected:
            self.esp32.push_settings(build_device_settings_payload(self.config))
        self.state.device.paired_device_name = "ESP32 Desk Node" if self.state.esp32_connected else "Simulation Device"
        self.state.device.last_command_status = "Pairing updated"
        save_config(self.config)
        self.emit_status()

    def discover_device(self):
        """Find the device via mDNS (studyguard.local) instead of a hand-typed IP."""
        url = auto_discover_endpoint()
        if url:
            self.pair_device({"endpoint": url, "mode": "http"})
            self.state.device.last_command_status = f"Device discovered at {url}"
        else:
            self.state.device.last_command_status = "Device not found — is it on this network?"
        self.emit_status()

    def provision_device_wifi(self, payload: dict):
        """Push new WiFi credentials to a device broadcasting StudyGuard-Setup
        (first-time setup, or after the device's WiFi was reset)."""
        ssid = payload.get("ssid", "").strip()
        password = payload.get("password", "")
        if not ssid:
            self.state.device.last_command_status = "WiFi setup failed: SSID required"
            self.emit_status()
            return
        sent = provision_wifi(ssid, password)
        if sent:
            self.state.device.last_command_status = "WiFi sent to device — it will reboot and reconnect"
        else:
            self.state.device.last_command_status = (
                "Could not reach device setup AP — connect this PC to StudyGuard-Setup first"
            )
        self.emit_status()

    def sync_device_settings(self):
        payload = build_device_settings_payload(self.config)
        sent = self.esp32.push_settings(payload)
        self.state.device.last_command_status = "Device settings synced" if sent else "Device settings simulated"
        self.emit_status()

    def refresh_device_status(self):
        status = self.esp32.get_status()
        self.apply_device_status(status)

    def poll_device_status(self):
        """Fetch ESP32 sensors without blocking the Qt camera/UI thread."""
        if self._device_poll_running:
            return
        self._device_poll_running = True

        def fetch():
            try:
                self.device_status_received.emit(self.esp32.get_status())
            finally:
                self._device_poll_running = False

        threading.Thread(target=fetch, name="esp32-status", daemon=True).start()

    def apply_device_status(self, status: dict):
        self.state.esp32_connected = bool(status.get("connected"))
        if self.state.esp32_connected:
            self.state.device.last_command_status = "Device reachable"
            self.state.device.paired_device_name = status.get("device_name", "ESP32 Desk Node")
            self.state.device.fan_on = status.get("fan_on", status.get("fan", self.state.device.fan_on))
            self.state.device.lamp_brightness = status.get(
                "lamp_brightness", status.get("lamp_pct", self.state.device.lamp_brightness)
            )
            self.state.environment.temperature_c = status.get(
                "temperature_c", status.get("temperature", self.state.environment.temperature_c)
            )
        else:
            self.state.device.last_command_status = "Simulation mode"
        self.emit_status()

    def apply_settings(self, payload: dict):
        old_camera_index = self.config.CAMERA_INDEX
        self.config.update_from_user_settings(payload)
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
        if self.state.esp32_connected:
            self.esp32.push_settings(build_device_settings_payload(self.config))
        self.state.device.last_command_status = "Settings applied"
        self.emit_status()

    def toggle_mode(self):
        self.config.AUTO_MODE = not self.config.AUTO_MODE
        self.state.device.auto_mode = self.config.AUTO_MODE
        self.state.device.last_command_status = "Auto mode enabled" if self.config.AUTO_MODE else "Manual mode enabled"
        save_config(self.config)
        if self.state.esp32_connected:
            self.esp32.push_settings(build_device_settings_payload(self.config))
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
        if self.state.esp32_connected:
            self.esp32.push_settings(build_device_settings_payload(self.config))
        self.emit_status()

    def toggle_fan(self):
        self.state.device.fan_on = not self.state.device.fan_on
        sent = self.esp32.set_fan(self.state.device.fan_on)
        self.state.device.last_command_status = "Fan command sent" if sent else "Fan simulated"
        self.emit_status()

    def set_lamp_brightness(self, brightness: int):
        brightness = max(0, min(100, int(brightness)))
        self.state.device.lamp_brightness = brightness
        self.config.DEFAULT_LAMP_BRIGHTNESS = brightness
        sent = self.esp32.set_lamp_brightness(brightness)
        self.state.device.last_command_status = "Lamp brightness sent" if sent else "Lamp simulated"
        save_config(self.config)
        self.emit_status()

    def reset_calibration(self):
        self.config.POSTURE_BASELINE_VALID = False
        self.config.POSTURE_BASELINE_Z = 0.0
        self.config.POSTURE_BASELINE_V = 0.0
        self.config.POSTURE_BASELINE_EAR_Z = 0.0
        self.config.POSTURE_BASELINE_TORSO_Z = 0.0
        self.config.PERSONAL_EAR_VALID = False
        self.config.PERSONAL_EAR_BASELINE = 0.0
        save_config(self.config)
        self.pose_analyzer.reset_calibration()
        self.face_analyzer.reset_ear_calibration()
        self.state.posture.back_lean_angle = 0.0
        self.state.posture.torso_z_delta = 0.0
        self.state.posture.posture_score = 100
        self.state.posture.shoulder_confidence = 0.0
        self.state.posture.head_drop_confidence = 0.0
        self.state.posture.forward_head_confidence = 0.0
        self.state.posture.back_lean_confidence = 0.0
        self.state.posture.face_distance_confidence = 0.0
        self.state.posture.posture_confidence = 0.0
        self.state.posture.shoulder_imbalance_confirmed = False
        self.state.posture.head_drop_confirmed = False
        self.state.posture.forward_head_confirmed = False
        self.state.posture.back_lean_confirmed = False
        self.state.posture.posture_reason = "Not calibrated"
        self.state.posture.posture_reasons = []
        self.state.posture.calibration_status = "Not calibrated"
        self.state.posture.calibration_progress = 0.0
        self.state.posture.calibration_rejected_frames = 0
        self.state.fatigue.perclos = 0.0
        self.state.fatigue.fatigue_score = 0
        self.state.fatigue.eye_confidence = 0.0
        self.state.fatigue.yawn_confidence = 0.0
        self.state.fatigue.nod_confidence = 0.0
        self.state.fatigue.nod_drop_ratio = 0.0
        self.state.fatigue.blink_confidence = 0.0
        self.state.fatigue.fatigue_confidence = 0.0
        self.state.fatigue.focus_confidence = 0.0
        self.state.fatigue.gaze_away_ratio = 0.0
        self.state.fatigue.is_focus_lost = False
        self.state.fatigue.focus_loss_reason = ""
        self.state.fatigue.yawn_times.clear()
        self.state.fatigue.nod_times.clear()
        self.state.fatigue.eye_closed_history.clear()
        self.state.fatigue.eye_observations.clear()
        self.state.device.last_command_status = "Calibration reset — sit in good posture"
        self.emit_status()

    def trigger_posture_alert(self):
        if self.config.SILENT_MODE:
            sent = self.esp32.posture_buzz()
            self.state.device.last_command_status = "Silent posture alert sent" if sent else "Posture test simulated"
        else:
            sent = self.esp32.posture_buzz()
            self.state.device.last_command_status = "Posture test sent" if sent else "Posture test simulated"
        self.emit_status()

    def trigger_drowsy_alert(self):
        if self.config.SILENT_MODE:
            sent = self.esp32.drowsy_buzz()
            self.state.device.last_command_status = "Silent fatigue alert sent" if sent else "Drowsy test simulated"
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
        sent = self.esp32.break_buzz()
        if self.config.SILENT_MODE:
            self.state.device.last_command_status = "Break reminder is due (silent mode)"
        else:
            self.state.device.last_command_status = "Break reminder sent" if sent else "Break reminder simulated"
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
        self.device_poll_timer.stop()
        self.save_session_report()
        if self.cap.isOpened():
            self.cap.release()
        self.pose_analyzer.close()
        self.face_analyzer.close()
        self.esp32.clear_alerts()
        self.esp32.close()
        self.mobile_api.stop()
        
        if self.voice_thread.isRunning():
            self.voice_thread.stop()
