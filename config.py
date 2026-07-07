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
    VALIDATION_LOG_FILE: str = str(BASE_DIR / "data" / "validation_log.csv")
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
    YAWN_MIN_FRAMES: int = 18
    YAWN_MAX_FRAMES: int = 120
    YAWN_MAX_GAZE_AWAY_RATIO: float = 0.16
    NOD_DROP_RATIO: float = 0.035
    NOD_RECOVER_RATIO: float = 0.018
    NOD_MAX_FACE_SCALE_DELTA: float = 0.025

    FORWARD_HEAD_Z_DELTA: float = 0.12  # deviation from calibrated baseline (normalised by shoulder width)
    NOSE_DROP_RATIO: float = 0.12       # deviation from calibrated baseline (normalised by shoulder width)
    BACK_LEAN_ANGLE_THRESHOLD: float = 15.0
    TORSO_LEAN_Z_DELTA: float = 0.12
    CALIBRATION_MAX_SHOULDER_ANGLE: float = 10.0
    CALIBRATION_MAX_SAMPLE_DRIFT: float = 0.10
    POSTURE_CONFIRM_FRAMES: int = 9
    POSTURE_RECOVERY_FRAMES: int = 6
    FATIGUE_CONFIRM_FRAMES: int = 15
    FATIGUE_RECOVERY_FRAMES: int = 15
    FACE_DISTANCE_CONFIRM_FRAMES: int = 12
    FOCUS_LOSS_CONFIRM_SECONDS: float = 8.0
    GAZE_AWAY_CONFIRM_SECONDS: float = 5.0
    GAZE_AWAY_RATIO: float = 0.18

    POSTURE_BASELINE_Z: float = 0.0
    POSTURE_BASELINE_V: float = 0.0
    POSTURE_BASELINE_EAR_Z: float = 0.0
    POSTURE_BASELINE_TORSO_Z: float = 0.0
    POSTURE_BASELINE_VALID: bool = False
    PERSONAL_EAR_BASELINE: float = 0.0
    PERSONAL_EAR_VALID: bool = False
    VALIDATION_LOG_ENABLED: bool = True
    VALIDATION_LOG_EVERY_N_FRAMES: int = 5

    ESP32_MODE: str = "http"
    ESP32_SERIAL_PORT: str = os.getenv("ESP32_SERIAL_PORT", "/dev/ttyUSB0")
    ESP32_BAUD_RATE: int = 115200
    ESP32_HTTP_URL: str = os.getenv("ESP32_HTTP_URL", "http://192.168.43.186")
    # Auto-managed cache of the last IP resolved via mDNS (studyguard.local) —
    # tried first for speed, never something the user edits by hand.
    ESP32_LAST_KNOWN_IP: str = ""

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
        self.VALIDATION_LOG_EVERY_N_FRAMES = max(1, int(self.VALIDATION_LOG_EVERY_N_FRAMES))
        self.POSTURE_CONFIRM_FRAMES = max(1, int(self.POSTURE_CONFIRM_FRAMES))
        self.POSTURE_RECOVERY_FRAMES = max(1, int(self.POSTURE_RECOVERY_FRAMES))
        self.FATIGUE_CONFIRM_FRAMES = max(1, int(self.FATIGUE_CONFIRM_FRAMES))
        self.FATIGUE_RECOVERY_FRAMES = max(1, int(self.FATIGUE_RECOVERY_FRAMES))
        self.FACE_DISTANCE_CONFIRM_FRAMES = max(1, int(self.FACE_DISTANCE_CONFIRM_FRAMES))
        self.YAWN_MIN_FRAMES = max(1, int(self.YAWN_MIN_FRAMES))
        self.YAWN_MAX_FRAMES = max(self.YAWN_MIN_FRAMES, int(self.YAWN_MAX_FRAMES))

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
            "esp32_last_known_ip": self.ESP32_LAST_KNOWN_IP,
            "posture_baseline_z": self.POSTURE_BASELINE_Z,
            "posture_baseline_v": self.POSTURE_BASELINE_V,
            "posture_baseline_ear_z": self.POSTURE_BASELINE_EAR_Z,
            "posture_baseline_torso_z": self.POSTURE_BASELINE_TORSO_Z,
            "posture_baseline_valid": self.POSTURE_BASELINE_VALID,
            "personal_ear_baseline": self.PERSONAL_EAR_BASELINE,
            "personal_ear_valid": self.PERSONAL_EAR_VALID,
            "validation_log_enabled": self.VALIDATION_LOG_ENABLED,
            "validation_log_every_n_frames": self.VALIDATION_LOG_EVERY_N_FRAMES,
            "posture_confirm_frames": self.POSTURE_CONFIRM_FRAMES,
            "posture_recovery_frames": self.POSTURE_RECOVERY_FRAMES,
            "fatigue_confirm_frames": self.FATIGUE_CONFIRM_FRAMES,
            "fatigue_recovery_frames": self.FATIGUE_RECOVERY_FRAMES,
            "face_distance_confirm_frames": self.FACE_DISTANCE_CONFIRM_FRAMES,
            "focus_loss_confirm_seconds": self.FOCUS_LOSS_CONFIRM_SECONDS,
            "gaze_away_confirm_seconds": self.GAZE_AWAY_CONFIRM_SECONDS,
            "gaze_away_ratio": self.GAZE_AWAY_RATIO,
            "yawn_min_frames": self.YAWN_MIN_FRAMES,
            "yawn_max_frames": self.YAWN_MAX_FRAMES,
            "yawn_max_gaze_away_ratio": self.YAWN_MAX_GAZE_AWAY_RATIO,
            "nod_drop_ratio": self.NOD_DROP_RATIO,
            "nod_recover_ratio": self.NOD_RECOVER_RATIO,
            "nod_max_face_scale_delta": self.NOD_MAX_FACE_SCALE_DELTA,
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
        self.ESP32_LAST_KNOWN_IP = payload.get("esp32_last_known_ip", self.ESP32_LAST_KNOWN_IP)
        self.POSTURE_BASELINE_Z = float(payload.get("posture_baseline_z", self.POSTURE_BASELINE_Z))
        self.POSTURE_BASELINE_V = float(payload.get("posture_baseline_v", self.POSTURE_BASELINE_V))
        self.POSTURE_BASELINE_EAR_Z = float(payload.get("posture_baseline_ear_z", self.POSTURE_BASELINE_EAR_Z))
        self.POSTURE_BASELINE_TORSO_Z = float(payload.get("posture_baseline_torso_z", self.POSTURE_BASELINE_TORSO_Z))
        self.POSTURE_BASELINE_VALID = bool(payload.get("posture_baseline_valid", self.POSTURE_BASELINE_VALID))
        self.PERSONAL_EAR_BASELINE = float(payload.get("personal_ear_baseline", self.PERSONAL_EAR_BASELINE))
        self.PERSONAL_EAR_VALID = bool(payload.get("personal_ear_valid", self.PERSONAL_EAR_VALID))
        self.VALIDATION_LOG_ENABLED = bool(payload.get("validation_log_enabled", self.VALIDATION_LOG_ENABLED))
        self.VALIDATION_LOG_EVERY_N_FRAMES = int(payload.get("validation_log_every_n_frames", self.VALIDATION_LOG_EVERY_N_FRAMES))
        self.POSTURE_CONFIRM_FRAMES = int(payload.get("posture_confirm_frames", self.POSTURE_CONFIRM_FRAMES))
        self.POSTURE_RECOVERY_FRAMES = int(payload.get("posture_recovery_frames", self.POSTURE_RECOVERY_FRAMES))
        self.FATIGUE_CONFIRM_FRAMES = int(payload.get("fatigue_confirm_frames", self.FATIGUE_CONFIRM_FRAMES))
        self.FATIGUE_RECOVERY_FRAMES = int(payload.get("fatigue_recovery_frames", self.FATIGUE_RECOVERY_FRAMES))
        self.FACE_DISTANCE_CONFIRM_FRAMES = int(payload.get("face_distance_confirm_frames", self.FACE_DISTANCE_CONFIRM_FRAMES))
        self.FOCUS_LOSS_CONFIRM_SECONDS = float(payload.get("focus_loss_confirm_seconds", self.FOCUS_LOSS_CONFIRM_SECONDS))
        self.GAZE_AWAY_CONFIRM_SECONDS = float(payload.get("gaze_away_confirm_seconds", self.GAZE_AWAY_CONFIRM_SECONDS))
        self.GAZE_AWAY_RATIO = float(payload.get("gaze_away_ratio", self.GAZE_AWAY_RATIO))
        self.YAWN_MIN_FRAMES = int(payload.get("yawn_min_frames", self.YAWN_MIN_FRAMES))
        self.YAWN_MAX_FRAMES = int(payload.get("yawn_max_frames", self.YAWN_MAX_FRAMES))
        self.YAWN_MAX_GAZE_AWAY_RATIO = float(payload.get("yawn_max_gaze_away_ratio", self.YAWN_MAX_GAZE_AWAY_RATIO))
        self.NOD_DROP_RATIO = float(payload.get("nod_drop_ratio", self.NOD_DROP_RATIO))
        self.NOD_RECOVER_RATIO = float(payload.get("nod_recover_ratio", self.NOD_RECOVER_RATIO))
        self.NOD_MAX_FACE_SCALE_DELTA = float(payload.get("nod_max_face_scale_delta", self.NOD_MAX_FACE_SCALE_DELTA))
        self.apply_runtime_rules()


@dataclass
class PostureState:
    is_slouching: bool = False
    is_forward_head: bool = False
    is_too_close: bool = False
    shoulder_angle: float = 0.0
    head_forward_ratio: float = 0.0
    head_drop_ratio: float = 0.0
    back_lean_angle: float = 0.0
    torso_z_delta: float = 0.0
    face_size_ratio: float = 0.0
    posture_score: int = 100
    shoulder_confidence: float = 0.0
    head_drop_confidence: float = 0.0
    forward_head_confidence: float = 0.0
    back_lean_confidence: float = 0.0
    face_distance_confidence: float = 0.0
    posture_confidence: float = 0.0
    shoulder_imbalance_confirmed: bool = False
    head_drop_confirmed: bool = False
    forward_head_confirmed: bool = False
    back_lean_confirmed: bool = False
    posture_reason: str = "Posture stable"
    posture_reasons: list[str] = field(default_factory=list)
    landmarks_visible: bool = False
    hips_visible: bool = False
    calibration_status: str = "Not calibrated"
    calibration_progress: float = 0.0
    calibration_rejected_frames: int = 0
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
    yawn_times: deque = field(default_factory=deque)
    consec_mouth_open: int = 0
    mar: float = 0.0
    perclos: float = 0.0
    fatigue_score: int = 0
    face_visible: bool = False
    eye_confidence: float = 0.0
    yawn_confidence: float = 0.0
    nod_confidence: float = 0.0
    blink_confidence: float = 0.0
    fatigue_confidence: float = 0.0
    focus_confidence: float = 0.0
    gaze_away_ratio: float = 0.0
    is_focus_lost: bool = False
    focus_loss_reason: str = ""
    nod_drop_ratio: float = 0.0
    is_yawning: bool = False
    is_drowsy: bool = False
    drowsy_start: float = 0.0
    nod_count: int = 0
    nod_times: deque = field(default_factory=deque)
    eye_closed_history: deque = field(default_factory=lambda: deque(maxlen=1800))
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
    total_frames: int = 0
    good_posture_frames: int = 0


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
