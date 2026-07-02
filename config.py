from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import logging
import os
import time
import urllib.request


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"
CONFIG_PATH = BASE_DIR / "config.json"

MODEL_URLS = {
    "pose_landmarker_full.task": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
}


def ensure_models():
    MODEL_DIR.mkdir(exist_ok=True)
    for filename, url in MODEL_URLS.items():
        path = MODEL_DIR / filename
        if path.exists():
            continue
        logger.info("Downloading model: %s ...", filename)
        try:
            urllib.request.urlretrieve(url, path)
            logger.info("Downloaded: %s", filename)
        except Exception as exc:
            logger.error("Failed to download %s: %s", filename, exc)
            logger.error("Download manually from: %s", url)
            raise SystemExit(1) from exc


@dataclass
class Config:
    POSE_MODEL: str = str(MODEL_DIR / "pose_landmarker_full.task")
    FACE_MODEL: str = str(MODEL_DIR / "face_landmarker.task")
    REPORTS_FILE: str = str(BASE_DIR / "data" / "session_history.json")
    SETTINGS_FILE: str = str(CONFIG_PATH)

    CAMERA_INDEX: int = 0
    FRAME_WIDTH: int = 1280
    FRAME_HEIGHT: int = 720
    FPS: int = 30

    SLOUCH_ANGLE_THRESHOLD: float = 15.0
    FORWARD_HEAD_RATIO: float = 0.35
    CLOSE_FACE_RATIO: float = 0.40
    SLOUCH_CONFIRM_SECONDS: float = 3.0
    POSTURE_ALERT_COOLDOWN: float = 30.0

    EAR_THRESHOLD: float = 0.22
    EAR_CONSEC_FRAMES: int = 2
    EAR_SMOOTH_FRAMES: int = 3
    DROWSY_BLINK_RATE_LOW: float = 8.0
    DROWSY_BLINK_RATE_HIGH: float = 25.0
    DROWSY_EAR_AVG: float = 0.26
    DROWSY_CONFIRM_SECONDS: float = 5.0
    BLINK_WINDOW_SECONDS: float = 60.0
    BLINK_RATE_MIN_SECONDS: float = 20.0
    DROWSY_ALERT_COOLDOWN: float = 60.0

    MAR_THRESHOLD: float = 0.60
    MAR_CONSEC_FRAMES: int = 15

    FORWARD_HEAD_Z_DELTA: float = 0.12  # deviation from calibrated baseline (normalised by shoulder width)
    NOSE_DROP_RATIO: float = 0.12       # deviation from calibrated baseline (normalised by shoulder width)

    ESP32_MODE: str = "http"
    ESP32_SERIAL_PORT: str = os.getenv("ESP32_SERIAL_PORT", "/dev/ttyUSB0")
    ESP32_BAUD_RATE: int = 115200
    ESP32_HTTP_URL: str = os.getenv("ESP32_HTTP_URL", "http://192.168.1.100")
    DEVICE_WIFI_SSID: str = ""
    DEVICE_WIFI_PASSWORD: str = ""

    DEFAULT_TEMPERATURE_C: float = 30.0
    DEFAULT_LAMP_BRIGHTNESS: int = 65
    DEFAULT_FAN_ON: bool = True

    AUTO_MODE: bool = True
    SILENT_MODE: bool = False
    BREAK_REMINDER_MINUTES: int = 45
    POSTURE_SENSITIVITY: int = 15
    FATIGUE_SENSITIVITY: int = 22
    FATIGUE_ALERT_ENABLED: bool = True
    AUTO_FAN: bool = True
    AUTO_LAMP: bool = True
    STARTUP_AUTO_LAUNCH: bool = False
    MINIMIZE_TO_TRAY: bool = True

    def apply_runtime_rules(self):
        self.CAMERA_INDEX = max(0, int(self.CAMERA_INDEX))
        self.POSTURE_SENSITIVITY = max(5, min(45, int(self.POSTURE_SENSITIVITY)))
        self.FATIGUE_SENSITIVITY = max(15, min(35, int(self.FATIGUE_SENSITIVITY)))
        self.BREAK_REMINDER_MINUTES = max(5, int(self.BREAK_REMINDER_MINUTES))
        self.DEFAULT_LAMP_BRIGHTNESS = max(0, min(100, int(self.DEFAULT_LAMP_BRIGHTNESS)))

        self.SLOUCH_ANGLE_THRESHOLD = float(self.POSTURE_SENSITIVITY)
        self.EAR_THRESHOLD = round(self.FATIGUE_SENSITIVITY / 100.0, 2)

    def to_user_settings(self) -> dict:
        return {
            "esp32_url": self.ESP32_HTTP_URL,
            "camera_index": self.CAMERA_INDEX,
            "posture_sensitivity": self.POSTURE_SENSITIVITY,
            "fatigue_sensitivity": self.FATIGUE_SENSITIVITY,
            "fatigue_alert": self.FATIGUE_ALERT_ENABLED,
            "silent_mode": self.SILENT_MODE,
            "auto_fan": self.AUTO_FAN,
            "auto_lamp": self.AUTO_LAMP,
            "auto_mode": self.AUTO_MODE,
            "break_reminder_minutes": self.BREAK_REMINDER_MINUTES,
            "startup_auto_launch": self.STARTUP_AUTO_LAUNCH,
            "minimize_to_tray": self.MINIMIZE_TO_TRAY,
            "default_temperature_c": self.DEFAULT_TEMPERATURE_C,
            "default_lamp_brightness": self.DEFAULT_LAMP_BRIGHTNESS,
            "device_wifi_ssid": self.DEVICE_WIFI_SSID,
            "device_wifi_password": self.DEVICE_WIFI_PASSWORD,
        }

    def update_from_user_settings(self, payload: dict):
        self.ESP32_HTTP_URL = payload.get("esp32_url", self.ESP32_HTTP_URL)
        self.CAMERA_INDEX = int(payload.get("camera_index", self.CAMERA_INDEX))
        self.POSTURE_SENSITIVITY = int(payload.get("posture_sensitivity", self.POSTURE_SENSITIVITY))
        self.FATIGUE_SENSITIVITY = int(payload.get("fatigue_sensitivity", self.FATIGUE_SENSITIVITY))
        self.FATIGUE_ALERT_ENABLED = bool(payload.get("fatigue_alert", self.FATIGUE_ALERT_ENABLED))
        self.SILENT_MODE = bool(payload.get("silent_mode", self.SILENT_MODE))
        self.AUTO_FAN = bool(payload.get("auto_fan", self.AUTO_FAN))
        self.AUTO_LAMP = bool(payload.get("auto_lamp", self.AUTO_LAMP))
        self.AUTO_MODE = bool(payload.get("auto_mode", self.AUTO_MODE))
        self.BREAK_REMINDER_MINUTES = int(payload.get("break_reminder_minutes", self.BREAK_REMINDER_MINUTES))
        self.STARTUP_AUTO_LAUNCH = bool(payload.get("startup_auto_launch", self.STARTUP_AUTO_LAUNCH))
        self.MINIMIZE_TO_TRAY = bool(payload.get("minimize_to_tray", self.MINIMIZE_TO_TRAY))
        self.DEFAULT_TEMPERATURE_C = float(payload.get("default_temperature_c", self.DEFAULT_TEMPERATURE_C))
        self.DEFAULT_LAMP_BRIGHTNESS = int(payload.get("default_lamp_brightness", self.DEFAULT_LAMP_BRIGHTNESS))
        self.DEVICE_WIFI_SSID = payload.get("device_wifi_ssid", self.DEVICE_WIFI_SSID)
        self.DEVICE_WIFI_PASSWORD = payload.get("device_wifi_password", self.DEVICE_WIFI_PASSWORD)
        self.apply_runtime_rules()


@dataclass
class PostureState:
    is_slouching: bool = False
    is_forward_head: bool = False
    is_too_close: bool = False
    shoulder_angle: float = 0.0
    head_forward_ratio: float = 0.0
    face_size_ratio: float = 0.0
    bad_posture_start: float = 0.0
    last_alert_time: float = 0.0
    alert_count: int = 0


@dataclass
class FatigueState:
    blink_count: int = 0
    blink_times: deque = field(default_factory=deque)
    blink_rate: float = 0.0
    ear_left: float = 0.0
    ear_right: float = 0.0
    ear_avg: float = 0.0
    ear_history: deque = field(default_factory=lambda: deque(maxlen=90))
    ear_smooth_buf: deque = field(default_factory=lambda: deque(maxlen=3))
    consec_below_threshold: int = 0
    yawn_count: int = 0
    consec_mouth_open: int = 0
    mar: float = 0.0
    is_yawning: bool = False
    is_drowsy: bool = False
    drowsy_start: float = 0.0
    session_start: float = field(default_factory=time.time)
    last_alert_time: float = 0.0
    alert_count: int = 0


@dataclass
class EnvironmentState:
    room_light_status: str = "Good"
    room_light_level: int = 60
    temperature_c: float = 30.0


@dataclass
class DeviceState:
    fan_on: bool = True
    lamp_brightness: int = 65
    last_command_status: str = "Idle"
    paired_device_name: str = "ESP32 Desk Node"
    auto_mode: bool = True
    silent_mode: bool = False
    wifi_ssid: str = ""


@dataclass
class SystemState:
    posture: PostureState = field(default_factory=PostureState)
    fatigue: FatigueState = field(default_factory=FatigueState)
    environment: EnvironmentState = field(default_factory=EnvironmentState)
    device: DeviceState = field(default_factory=DeviceState)
    session_start: float = field(default_factory=time.time)
    total_alerts: int = 0
    esp32_connected: bool = False
    break_due: bool = False
    last_break_at: float = field(default_factory=time.time)


def load_config() -> Config:
    config = Config()
    if CONFIG_PATH.exists():
        try:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            config.update_from_user_settings(payload)
        except json.JSONDecodeError:
            logger.warning("Invalid config.json detected. Using defaults.")
    else:
        save_config(config)
    config.apply_runtime_rules()
    return config


def save_config(config: Config):
    CONFIG_PATH.write_text(json.dumps(config.to_user_settings(), indent=2), encoding="utf-8")


CONFIG = load_config()
