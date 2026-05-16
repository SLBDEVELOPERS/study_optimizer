"""
Smart Study/Work Environment Optimizer
=====================================
OpenCV + MediaPipe Tasks API (NEW - Python 3.12 compatible)
Uses: mediapipe.tasks.vision — NOT deprecated mp.solutions.*

Requires model files (auto-downloaded on first run):
  - pose_landmarker_full.task
  - face_landmarker.task

Author: Smart Environment System
"""

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import PoseLandmarker, FaceLandmarker
from mediapipe.tasks.python.vision import PoseLandmarkerOptions, FaceLandmarkerOptions
import numpy as np
import time
import math
import serial
import threading
import json
import requests
import urllib.request
import os
from dataclasses import dataclass, field
from collections import deque
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Model auto-downloader
# ─────────────────────────────────────────────
MODEL_URLS = {
    "pose_landmarker_full.task":
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
    "face_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
}

def ensure_models():
    """Download .task model files if not present."""
    for filename, url in MODEL_URLS.items():
        if not os.path.exists(filename):
            logger.info(f"Downloading model: {filename} ...")
            try:
                urllib.request.urlretrieve(url, filename)
                logger.info(f"Downloaded: {filename}")
            except Exception as e:
                logger.error(f"Failed to download {filename}: {e}")
                logger.error(f"Download manually from: {url}")
                raise SystemExit(1)


# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
@dataclass
class Config:
    # Models
    POSE_MODEL: str = "pose_landmarker_full.task"
    FACE_MODEL: str = "face_landmarker.task"

    # Camera
    CAMERA_INDEX: int = 0
    FRAME_WIDTH: int = 1280
    FRAME_HEIGHT: int = 720
    FPS: int = 30

    # Posture thresholds
    SLOUCH_ANGLE_THRESHOLD: float = 15.0
    FORWARD_HEAD_RATIO: float = 0.35
    CLOSE_FACE_RATIO: float = 0.40
    SLOUCH_CONFIRM_SECONDS: float = 3.0
    POSTURE_ALERT_COOLDOWN: float = 30.0

    # Blink / Eye detection
    EAR_THRESHOLD: float = 0.22
    EAR_CONSEC_FRAMES: int = 2
    DROWSY_BLINK_RATE_LOW: float = 8.0
    DROWSY_BLINK_RATE_HIGH: float = 25.0
    DROWSY_EAR_AVG: float = 0.26
    DROWSY_CONFIRM_SECONDS: float = 5.0
    BLINK_WINDOW_SECONDS: float = 60.0

    # ESP32
    ESP32_MODE: str = "http"           # "serial" or "http"
    ESP32_SERIAL_PORT: str = "/dev/ttyUSB0"
    ESP32_BAUD_RATE: int = 115200
    ESP32_HTTP_URL: str = "http://192.168.1.100"
    DROWSY_ALERT_COOLDOWN: float = 60.0


CONFIG = Config()


# ─────────────────────────────────────────────
# Data Classes
# ─────────────────────────────────────────────
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
    consec_below_threshold: int = 0
    is_drowsy: bool = False
    drowsy_start: float = 0.0
    last_alert_time: float = 0.0
    alert_count: int = 0


@dataclass
class SystemState:
    posture: PostureState = field(default_factory=PostureState)
    fatigue: FatigueState = field(default_factory=FatigueState)
    session_start: float = field(default_factory=time.time)
    total_alerts: int = 0
    esp32_connected: bool = False


# ─────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────
def lm_px(lm, w: int, h: int) -> tuple:
    return int(lm.x * w), int(lm.y * h)


def eye_aspect_ratio(pts: list) -> float:
    """EAR from 6 (x,y) points: vertical / horizontal ratio."""
    def d(a, b): return math.hypot(a[0]-b[0], a[1]-b[1])
    A = d(pts[1], pts[5])
    B = d(pts[2], pts[4])
    C = d(pts[0], pts[3])
    return (A + B) / (2.0 * C + 1e-6)


# ─────────────────────────────────────────────
# ESP32 Communicator
# ─────────────────────────────────────────────
class ESP32Communicator:
    def __init__(self, config: Config):
        self.config = config
        self.serial_conn = None
        self.connected = False
        self._lock = threading.Lock()
        self._connect()

    def _connect(self):
        if self.config.ESP32_MODE == "serial":
            try:
                self.serial_conn = serial.Serial(
                    self.config.ESP32_SERIAL_PORT,
                    self.config.ESP32_BAUD_RATE, timeout=1)
                self.connected = True
                logger.info("ESP32 Serial connected")
            except Exception as e:
                logger.warning(f"ESP32 Serial unavailable: {e} → simulation mode")
        else:
            try:
                r = requests.get(f"{self.config.ESP32_HTTP_URL}/ping", timeout=2)
                self.connected = r.status_code == 200
                logger.info("ESP32 HTTP connected")
            except Exception as e:
                logger.warning(f"ESP32 HTTP unavailable: {e} → simulation mode")

    def send(self, cmd: dict) -> bool:
        with self._lock:
            try:
                payload = json.dumps(cmd)
                if self.config.ESP32_MODE == "serial" and self.serial_conn:
                    self.serial_conn.write((payload + "\n").encode())
                    return True
                elif self.config.ESP32_MODE == "http":
                    r = requests.post(f"{self.config.ESP32_HTTP_URL}/command",
                                      json=cmd, timeout=2)
                    return r.status_code == 200
            except Exception:
                pass
        return False

    def posture_buzz(self):
        ok = self.send({"action": "buzzer", "pattern": "posture", "duration_ms": 500, "repeats": 3})
        logger.info(f"[ESP32] Posture buzz → {'sent' if ok else 'simulated'}")

    def drowsy_buzz(self):
        ok = self.send({"action": "buzzer", "pattern": "drowsy", "duration_ms": 1000, "repeats": 2})
        logger.info(f"[ESP32] Drowsy buzz → {'sent' if ok else 'simulated'}")

    def close(self):
        if self.serial_conn:
            self.serial_conn.close()


# ─────────────────────────────────────────────
# Posture Analyzer  (NEW Tasks API)
# ─────────────────────────────────────────────
_NOSE       = 0
_L_SHOULDER = 11
_R_SHOULDER = 12

class PostureAnalyzer:
    """
    Uses mediapipe.tasks.vision.PoseLandmarker — NEW API.
    mp.solutions.pose is deprecated; this works on Python 3.12.
    """

    def __init__(self, model_path: str):
        options = PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.6,
            min_pose_presence_confidence=0.6,
            min_tracking_confidence=0.6,
        )
        self.landmarker = PoseLandmarker.create_from_options(options)
        self._ts_ms = 0

    def analyze(self, frame_rgb: np.ndarray, state: PostureState, h: int, w: int):
        self._ts_ms += 33
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self.landmarker.detect_for_video(mp_img, self._ts_ms)

        if not result.pose_landmarks:
            return state, None

        lms = result.pose_landmarks[0]   # list of NormalizedLandmark

        ls   = lm_px(lms[_L_SHOULDER], w, h)
        rs   = lm_px(lms[_R_SHOULDER], w, h)
        nose = lm_px(lms[_NOSE], w, h)

        # Shoulder tilt angle
        dy = rs[1] - ls[1]
        dx = rs[0] - ls[0]
        angle = abs(math.degrees(math.atan2(dy, dx + 1e-6)))
        if angle > 90:
            angle = 180 - angle
        state.shoulder_angle = angle

        # Forward head ratio
        mid_sx = (ls[0] + rs[0]) / 2
        sw = abs(rs[0] - ls[0]) + 1e-6
        state.head_forward_ratio = abs((nose[0] - mid_sx) / sw)

        state.is_slouching    = state.shoulder_angle > CONFIG.SLOUCH_ANGLE_THRESHOLD
        state.is_forward_head = state.head_forward_ratio > CONFIG.FORWARD_HEAD_RATIO

        return state, lms

    def draw(self, frame: np.ndarray, lms, w: int, h: int):
        if lms is None:
            return
        ls   = lm_px(lms[_L_SHOULDER], w, h)
        rs   = lm_px(lms[_R_SHOULDER], w, h)
        nose = lm_px(lms[_NOSE], w, h)
        col = (0, 60, 255) if (lms[_L_SHOULDER] and
               abs(math.degrees(math.atan2(rs[1]-ls[1], rs[0]-ls[0]+1e-6))) > CONFIG.SLOUCH_ANGLE_THRESHOLD
               ) else (80, 220, 100)
        cv2.line(frame, ls, rs, col, 2)
        cv2.circle(frame, ls, 5, col, -1)
        cv2.circle(frame, rs, 5, col, -1)
        cv2.circle(frame, nose, 4, (100, 200, 255), -1)
        mid = ((ls[0]+rs[0])//2, (ls[1]+rs[1])//2)
        cv2.line(frame, mid, nose, (100, 200, 255), 1, cv2.LINE_AA)

    def close(self):
        self.landmarker.close()


# ─────────────────────────────────────────────
# Face / Eye Analyzer  (NEW Tasks API)
# ─────────────────────────────────────────────
_LEFT_EYE  = [362, 385, 387, 263, 373, 380]
_RIGHT_EYE = [33, 160, 158, 133, 153, 144]

class FaceEyeAnalyzer:
    """
    Uses mediapipe.tasks.vision.FaceLandmarker — NEW API.
    478 landmarks (includes iris).
    """

    def __init__(self, model_path: str):
        options = FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.6,
            min_face_presence_confidence=0.6,
            min_tracking_confidence=0.6,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
        )
        self.landmarker = FaceLandmarker.create_from_options(options)
        self._ts_ms = 1

    def analyze(self, frame_rgb: np.ndarray, f_state: FatigueState,
                p_state: PostureState, h: int, w: int):
        self._ts_ms += 33
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self.landmarker.detect_for_video(mp_img, self._ts_ms)

        if not result.face_landmarks:
            return f_state, p_state, None

        lms = result.face_landmarks[0]   # list of NormalizedLandmark

        def eye_pts(indices):
            return [(int(lms[i].x * w), int(lms[i].y * h)) for i in indices]

        ear_l = eye_aspect_ratio(eye_pts(_LEFT_EYE))
        ear_r = eye_aspect_ratio(eye_pts(_RIGHT_EYE))
        ear   = (ear_l + ear_r) / 2.0

        f_state.ear_left  = ear_l
        f_state.ear_right = ear_r
        f_state.ear_avg   = ear
        f_state.ear_history.append(ear)

        # Blink
        now = time.time()
        if ear < CONFIG.EAR_THRESHOLD:
            f_state.consec_below_threshold += 1
        else:
            if f_state.consec_below_threshold >= CONFIG.EAR_CONSEC_FRAMES:
                f_state.blink_count += 1
                f_state.blink_times.append(now)
            f_state.consec_below_threshold = 0

        # Prune old
        cutoff = now - CONFIG.BLINK_WINDOW_SECONDS
        while f_state.blink_times and f_state.blink_times[0] < cutoff:
            f_state.blink_times.popleft()

        if f_state.blink_times:
            span = min(now - f_state.blink_times[0], CONFIG.BLINK_WINDOW_SECONDS)
            f_state.blink_rate = (len(f_state.blink_times) / span * 60.0) if span > 5 else 0.0
        else:
            f_state.blink_rate = 0.0

        # Face proximity
        ys = [lms[i].y for i in range(len(lms))]
        p_state.face_size_ratio = max(ys) - min(ys)
        p_state.is_too_close = p_state.face_size_ratio > CONFIG.CLOSE_FACE_RATIO

        # Drowsiness
        hist_avg = sum(f_state.ear_history) / len(f_state.ear_history) if f_state.ear_history else 0.3
        ear_low   = len(f_state.ear_history) > 30 and hist_avg < CONFIG.DROWSY_EAR_AVG
        blink_bad = (f_state.blink_rate > 0 and
                     (f_state.blink_rate < CONFIG.DROWSY_BLINK_RATE_LOW or
                      f_state.blink_rate > CONFIG.DROWSY_BLINK_RATE_HIGH))

        was_drowsy = f_state.is_drowsy
        f_state.is_drowsy = ear_low or blink_bad
        if f_state.is_drowsy and not was_drowsy:
            f_state.drowsy_start = now
        elif not f_state.is_drowsy:
            f_state.drowsy_start = 0.0

        return f_state, p_state, lms

    def draw(self, frame: np.ndarray, lms, fatigue: FatigueState, w: int, h: int):
        if lms is None:
            return
        col = (0, 60, 255) if fatigue.is_drowsy else (0, 220, 100)
        for indices in (_LEFT_EYE, _RIGHT_EYE):
            pts = np.array(
                [(int(lms[i].x * w), int(lms[i].y * h)) for i in indices],
                dtype=np.int32)
            cv2.polylines(frame, [pts], True, col, 1, cv2.LINE_AA)

    def close(self):
        self.landmarker.close()


# ─────────────────────────────────────────────
# Alert Manager
# ─────────────────────────────────────────────
class AlertManager:
    def __init__(self, esp32: ESP32Communicator):
        self.esp32 = esp32

    def check(self, state: SystemState):
        now = time.time()
        p = state.posture
        f = state.fatigue

        is_bad = p.is_slouching or p.is_forward_head or p.is_too_close
        if is_bad:
            if p.bad_posture_start == 0:
                p.bad_posture_start = now
            elif (now - p.bad_posture_start >= CONFIG.SLOUCH_CONFIRM_SECONDS and
                  now - p.last_alert_time >= CONFIG.POSTURE_ALERT_COOLDOWN):
                logger.warning("POSTURE ALERT!")
                self.esp32.posture_buzz()
                p.last_alert_time = now
                p.alert_count += 1
                state.total_alerts += 1
        else:
            p.bad_posture_start = 0

        if (f.is_drowsy and f.drowsy_start > 0 and
                now - f.drowsy_start >= CONFIG.DROWSY_CONFIRM_SECONDS and
                now - f.last_alert_time >= CONFIG.DROWSY_ALERT_COOLDOWN):
            logger.warning("DROWSY ALERT!")
            self.esp32.drowsy_buzz()
            f.last_alert_time = now
            f.alert_count += 1
            state.total_alerts += 1


# ─────────────────────────────────────────────
# Dashboard HUD
# ─────────────────────────────────────────────
class HUD:
    F     = cv2.FONT_HERSHEY_SIMPLEX
    OK    = (80, 220, 100)
    WARN  = (0, 200, 255)
    ALERT = (0, 60, 255)
    DIM   = (100, 100, 120)
    WHITE = (230, 230, 230)

    def render(self, frame: np.ndarray, state: SystemState) -> np.ndarray:
        h, w = frame.shape[:2]
        panel = frame.copy()
        cv2.rectangle(panel, (0, 0), (280, h), (10, 12, 20), -1)
        cv2.addWeighted(panel, 0.78, frame, 0.22, 0, frame)
        self._header(frame)
        self._posture(frame, state.posture)
        self._fatigue(frame, state.fatigue)
        self._session(frame, state, h)
        self._statusbar(frame, state, w, h)
        return frame

    def _header(self, f):
        cv2.putText(f, "STUDY OPTIMIZER", (10, 30), self.F, 0.62, (100,180,255), 2)
        cv2.putText(f, datetime.now().strftime("%H:%M:%S"), (10, 50), self.F, 0.42, self.DIM, 1)
        cv2.line(f, (10, 58), (270, 58), (40,40,60), 1)

    def _posture(self, f, p: PostureState):
        y = 76
        cv2.putText(f, "POSTURE", (10,y), self.F, 0.48, (150,150,200), 1); y += 18
        sc = self.ALERT if p.is_slouching else self.OK
        cv2.putText(f, f"Shoulder: {p.shoulder_angle:.1f}deg", (12,y), self.F, 0.40, sc, 1)
        self._bar(f, 12, y+4, 255, 10, min(p.shoulder_angle/30, 1), sc); y += 26
        fc = self.ALERT if p.is_forward_head else self.OK
        cv2.putText(f, f"Fwd Head: {p.head_forward_ratio:.2f}", (12,y), self.F, 0.40, fc, 1)
        self._bar(f, 12, y+4, 255, 10, min(p.head_forward_ratio/0.5, 1), fc); y += 26
        tc = self.ALERT if p.is_too_close else self.OK
        cv2.putText(f, "TOO CLOSE!" if p.is_too_close else "Distance OK", (12,y), self.F, 0.40, tc, 1); y += 22
        bad = p.is_slouching or p.is_forward_head or p.is_too_close
        cv2.rectangle(f, (10,y), (270,y+20), (60,10,10) if bad else (10,50,20), -1)
        cv2.putText(f, "BAD POSTURE" if bad else "POSTURE OK", (14,y+14), self.F, 0.48,
                    self.ALERT if bad else self.OK, 1)
        cv2.line(f, (10,y+28), (270,y+28), (40,40,60), 1)

    def _fatigue(self, f, ft: FatigueState):
        y = 290
        cv2.putText(f, "FATIGUE", (10,y), self.F, 0.48, (150,150,200), 1); y += 18
        ec = self.ALERT if ft.ear_avg < CONFIG.EAR_THRESHOLD else self.OK
        cv2.putText(f, f"EAR: {ft.ear_avg:.3f}", (12,y), self.F, 0.40, ec, 1)
        self._bar(f, 12, y+4, 255, 10, min(ft.ear_avg/0.35, 1), ec); y += 26
        bc = self.WARN if (ft.blink_rate > 0 and
             (ft.blink_rate < CONFIG.DROWSY_BLINK_RATE_LOW or
              ft.blink_rate > CONFIG.DROWSY_BLINK_RATE_HIGH)) else self.OK
        cv2.putText(f, f"Blinks/min: {ft.blink_rate:.1f}", (12,y), self.F, 0.40, bc, 1); y += 20
        cv2.putText(f, f"Total: {ft.blink_count}", (12,y), self.F, 0.40, self.DIM, 1); y += 24
        cv2.rectangle(f, (10,y), (270,y+20), (60,10,10) if ft.is_drowsy else (10,50,20), -1)
        cv2.putText(f, "DROWSY DETECTED" if ft.is_drowsy else "EYES ALERT", (14,y+14),
                    self.F, 0.45, self.ALERT if ft.is_drowsy else self.OK, 1)

    def _session(self, f, state: SystemState, h: int):
        y = h - 115
        cv2.line(f, (10,y), (270,y), (40,40,60), 1); y += 15
        e = int(time.time() - state.session_start)
        cv2.putText(f, f"Session {e//3600:02d}:{(e%3600)//60:02d}:{e%60:02d}",
                    (12,y), self.F, 0.43, self.WHITE, 1); y += 18
        cv2.putText(f, f"Posture alerts: {state.posture.alert_count}",
                    (12,y), self.F, 0.40, self.WARN, 1); y += 16
        cv2.putText(f, f"Fatigue alerts: {state.fatigue.alert_count}",
                    (12,y), self.F, 0.40, self.WARN, 1); y += 16
        cv2.putText(f, f"ESP32: {'CONNECTED' if state.esp32_connected else 'SIMULATION'}",
                    (12,y), self.F, 0.40,
                    self.OK if state.esp32_connected else self.DIM, 1)

    def _statusbar(self, f, state: SystemState, w: int, h: int):
        bar = f.copy()
        cv2.rectangle(bar, (280, h-34), (w, h), (10,10,18), -1)
        cv2.addWeighted(bar, 0.85, f, 0.15, 0, f)
        p = state.posture; ft = state.fatigue
        items = [
            ("POSTURE", "ALERT" if (p.is_slouching or p.is_forward_head or p.is_too_close) else "OK",
             self.ALERT if (p.is_slouching or p.is_forward_head or p.is_too_close) else self.OK),
            ("FATIGUE", "DROWSY" if ft.is_drowsy else "OK",
             self.ALERT if ft.is_drowsy else self.OK),
            ("ALERTS", str(state.total_alerts), self.WARN),
        ]
        x = 290
        for label, val, col in items:
            cv2.putText(f, f"{label}:", (x, h-12), self.F, 0.37, self.DIM, 1)
            cv2.putText(f, val, (x+58, h-12), self.F, 0.40, col, 1)
            x += 155
        cv2.putText(f, "Q=quit  S=snapshot", (w-155, h-12), self.F, 0.37, self.DIM, 1)

    def _bar(self, f, x, y, w, h, ratio, color):
        cv2.rectangle(f, (x,y), (x+w, y+h), (35,35,50), -1)
        fill = int(ratio * w)
        if fill > 0:
            cv2.rectangle(f, (x,y), (x+fill, y+h), color, -1)


# ─────────────────────────────────────────────
# Main Application
# ─────────────────────────────────────────────
class SmartStudyOptimizer:

    def __init__(self):
        logger.info("Initializing Smart Study Optimizer...")
        ensure_models()

        self.state = SystemState()
        self.esp32 = ESP32Communicator(CONFIG)
        self.state.esp32_connected = self.esp32.connected

        self.pose_analyzer = PostureAnalyzer(CONFIG.POSE_MODEL)
        self.face_analyzer = FaceEyeAnalyzer(CONFIG.FACE_MODEL)
        self.alerts = AlertManager(self.esp32)
        self.hud = HUD()

        self.cap = cv2.VideoCapture(CONFIG.CAMERA_INDEX)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CONFIG.FRAME_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CONFIG.FRAME_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, CONFIG.FPS)
        logger.info("Ready! Q=quit, S=snapshot")

    def run(self):
        prev = time.time()
        while True:
            ret, frame = self.cap.read()
            if not ret:
                logger.error("Camera feed lost!")
                break

            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            # NEW API needs RGB
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            try:
                self.state.posture, pose_lms = self.pose_analyzer.analyze(
                    frame_rgb, self.state.posture, h, w)
                self.pose_analyzer.draw(frame, pose_lms, w, h)
            except Exception as e:
                logger.debug(f"Pose error: {e}")

            try:
                self.state.fatigue, self.state.posture, face_lms = \
                    self.face_analyzer.analyze(
                        frame_rgb, self.state.fatigue, self.state.posture, h, w)
                self.face_analyzer.draw(frame, face_lms, self.state.fatigue, w, h)
            except Exception as e:
                logger.debug(f"Face error: {e}")

            self.alerts.check(self.state)

            now = time.time()
            fps = 1.0 / (now - prev + 1e-6)
            prev = now
            cv2.putText(frame, f"{fps:.0f}fps", (w-70, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (70,70,90), 1)

            frame = self.hud.render(frame, self.state)
            cv2.imshow("Smart Study Optimizer", frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord('q'), 27):
                break
            elif key == ord('s'):
                fname = f"snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                cv2.imwrite(fname, frame)
                logger.info(f"Snapshot: {fname}")

        self._cleanup()

    def _cleanup(self):
        self.cap.release()
        cv2.destroyAllWindows()
        self.pose_analyzer.close()
        self.face_analyzer.close()
        self.esp32.close()
        e = int(time.time() - self.state.session_start)
        print(f"\n{'='*40}")
        print(f"  Session  : {e//3600:02d}:{(e%3600)//60:02d}:{e%60:02d}")
        print(f"  Posture  : {self.state.posture.alert_count} alerts")
        print(f"  Fatigue  : {self.state.fatigue.alert_count} alerts")
        print(f"  Blinks   : {self.state.fatigue.blink_count}")
        print(f"{'='*40}")


if __name__ == "__main__":
    SmartStudyOptimizer().run()
