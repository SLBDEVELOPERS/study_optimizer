import math
from collections import deque

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions


_NOSE = 0
_L_SHOULDER = 11
_R_SHOULDER = 12

# EMA smoothing factor (0 = ignore new data, 1 = no smoothing)
_ALPHA = 0.25

# How many initial frames to use for calibrating baseline values
_CALIBRATION_FRAMES = 60


def lm_px(lm, width: int, height: int) -> tuple[int, int]:
    return int(lm.x * width), int(lm.y * height)


def _ema(current: float, new_value: float, alpha: float = _ALPHA) -> float:
    """Exponential moving average — smooths noisy per-frame values."""
    return alpha * new_value + (1.0 - alpha) * current


class PostureAnalyzer:
    def __init__(self, model_path: str, config):
        self.config = config
        self._ts_ms = 0

        # EMA state — initialised to neutral values
        self._smooth_shoulder_angle: float = 0.0
        self._smooth_z_delta_ratio: float = 0.0
        self._smooth_vert_ratio: float = 0.5  # start neutral

        # ── Baseline calibration ───────────────────────────────────────────
        # Collect data during the first N frames to establish personal baseline
        self._calibration_samples: list[dict] = []
        self._baseline_z_delta_ratio: float | None = None
        self._baseline_vert_ratio: float | None = None
        self._calibrated = False

        options = PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.6,
            min_pose_presence_confidence=0.6,
            min_tracking_confidence=0.6,
        )
        self.landmarker = PoseLandmarker.create_from_options(options)

    def _calibrate(self):
        """Compute baselines from collected calibration samples."""
        if not self._calibration_samples:
            return
        self._baseline_z_delta_ratio = sum(s["z"] for s in self._calibration_samples) / len(self._calibration_samples)
        self._baseline_vert_ratio = sum(s["v"] for s in self._calibration_samples) / len(self._calibration_samples)
        self._calibrated = True

    def analyze(self, frame_rgb: np.ndarray, state, height: int, width: int):
        self._ts_ms += 33
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self.landmarker.detect_for_video(mp_image, self._ts_ms)

        if not result.pose_landmarks:
            return state, None

        landmarks = result.pose_landmarks[0]
        l_sh = landmarks[_L_SHOULDER]
        r_sh = landmarks[_R_SHOULDER]
        nose = landmarks[_NOSE]

        # ── Pixel coordinates ──────────────────────────────────────────────
        l_sh_px = lm_px(l_sh, width, height)
        r_sh_px = lm_px(r_sh, width, height)

        # ── Normalised metrics ─────────────────────────────────────────────
        shoulder_width_norm = abs(r_sh.x - l_sh.x) + 1e-6
        mid_sy_norm = (l_sh.y + r_sh.y) / 2.0
        avg_shoulder_z = (l_sh.z + r_sh.z) / 2.0

        # ── 1. Shoulder lateral tilt (secondary slouch signal) ─────────────
        dy = r_sh_px[1] - l_sh_px[1]
        dx = r_sh_px[0] - l_sh_px[0]
        raw_angle = abs(math.degrees(math.atan2(dy, dx + 1e-6)))
        if raw_angle > 90:
            raw_angle = 180 - raw_angle
        self._smooth_shoulder_angle = _ema(self._smooth_shoulder_angle, raw_angle)
        state.shoulder_angle = self._smooth_shoulder_angle

        # ── 2. Vertical ratio — nose height above shoulder midpoint ────────
        # Higher ratio = nose well above shoulders (good posture)
        # Lower ratio = head drooping towards shoulders (slouch)
        raw_vert_ratio = (mid_sy_norm - nose.y) / shoulder_width_norm
        self._smooth_vert_ratio = _ema(self._smooth_vert_ratio, raw_vert_ratio)

        # ── 3. Z-depth ratio — nose forward relative to shoulders ──────────
        # Normalised by shoulder width for person/distance independence
        raw_z_delta_ratio = (avg_shoulder_z - nose.z) / shoulder_width_norm
        self._smooth_z_delta_ratio = _ema(self._smooth_z_delta_ratio, raw_z_delta_ratio)

        # ── Calibration phase ──────────────────────────────────────────────
        if not self._calibrated:
            self._calibration_samples.append({
                "z": raw_z_delta_ratio,
                "v": raw_vert_ratio,
            })
            if len(self._calibration_samples) >= _CALIBRATION_FRAMES:
                self._calibrate()
            # During calibration, assume good posture
            state.is_slouching = False
            state.is_forward_head = False
            state.head_forward_ratio = 0.0
            state.head_drop_ratio = 0.0
            return state, landmarks

        # ── Post-calibration: compare against personal baseline ────────────
        # Deviation from baseline (positive = worse than baseline)
        z_deviation = self._smooth_z_delta_ratio - self._baseline_z_delta_ratio
        vert_deviation = self._baseline_vert_ratio - self._smooth_vert_ratio

        state.head_forward_ratio = round(z_deviation, 3)
        state.head_drop_ratio = round(vert_deviation, 3)

        # Slouch: lateral tilt too high OR head dropped significantly from baseline
        lateral_slouch = self._smooth_shoulder_angle > self.config.SLOUCH_ANGLE_THRESHOLD
        vertical_slouch = vert_deviation > self.config.NOSE_DROP_RATIO
        state.is_slouching = lateral_slouch or vertical_slouch

        # Forward head: nose pushed significantly forward from baseline
        state.is_forward_head = z_deviation > self.config.FORWARD_HEAD_Z_DELTA

        return state, landmarks

    def draw(self, frame: np.ndarray, landmarks, width: int, height: int):
        if landmarks is None:
            return

        l_sh_px = lm_px(landmarks[_L_SHOULDER], width, height)
        r_sh_px = lm_px(landmarks[_R_SHOULDER], width, height)
        nose_px = lm_px(landmarks[_NOSE], width, height)

        bad = (
            self._calibrated
            and self.config
            and (
                self._smooth_shoulder_angle > self.config.SLOUCH_ANGLE_THRESHOLD
                or (self._baseline_vert_ratio is not None
                    and (self._baseline_vert_ratio - self._smooth_vert_ratio) > self.config.NOSE_DROP_RATIO)
                or (self._baseline_z_delta_ratio is not None
                    and (self._smooth_z_delta_ratio - self._baseline_z_delta_ratio) > self.config.FORWARD_HEAD_Z_DELTA)
            )
        )
        color = (0, 60, 255) if bad else (80, 220, 100)

        cv2.line(frame, l_sh_px, r_sh_px, color, 2)
        cv2.circle(frame, l_sh_px, 5, color, -1)
        cv2.circle(frame, r_sh_px, 5, color, -1)
        cv2.circle(frame, nose_px, 4, (100, 200, 255), -1)
        mid_point = ((l_sh_px[0] + r_sh_px[0]) // 2, (l_sh_px[1] + r_sh_px[1]) // 2)
        cv2.line(frame, mid_point, nose_px, (100, 200, 255), 1, cv2.LINE_AA)

        # Status text
        status_txt = "Calibrating..." if not self._calibrated else "Posture OK"
        if bad:
            status_txt = "Fix Posture!"
        cv2.putText(
            frame, status_txt,
            (10, frame.shape[0] - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
        )

    def close(self):
        self.landmarker.close()
