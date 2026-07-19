import math
from collections import deque

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions

from camera.scoring import calculate_posture_score, threshold_confidence
from camera.temporal import TemporalDurationFlag
from config import save_config


_NOSE = 0
_L_EAR = 7
_R_EAR = 8
_L_SHOULDER = 11
_R_SHOULDER = 12
_L_HIP = 23
_R_HIP = 24

# EMA smoothing factor (0 = ignore new data, 1 = no smoothing)
_ALPHA = 0.25

# How many initial frames to use for calibrating baseline values
_CALIBRATION_FRAMES = 60


def lm_px(lm, width: int, height: int) -> tuple[int, int]:
    return int(lm.x * width), int(lm.y * height)


def _ema(current: float, new_value: float, alpha: float = _ALPHA) -> float:
    """Exponential moving average — smooths noisy per-frame values."""
    return alpha * new_value + (1.0 - alpha) * current


def _visible(lm, threshold: float = 0.35) -> bool:
    return getattr(lm, "visibility", 1.0) >= threshold and getattr(lm, "presence", 1.0) >= threshold


def _mean_recent(samples: list[dict], key: str, count: int = 10) -> float:
    recent = samples[-count:]
    return sum(s[key] for s in recent) / len(recent)


def _set_posture_reasons(state):
    reasons = []
    if state.shoulder_imbalance_confirmed:
        reasons.append("Shoulder imbalance")
    if state.head_drop_confirmed:
        reasons.append("Head dropped")
    if state.forward_head_confirmed:
        reasons.append("Forward head")
    if state.back_lean_confirmed:
        reasons.append("Back leaning")
    if state.is_too_close:
        reasons.append("Too close to screen")

    state.posture_reasons = reasons
    state.posture_reason = reasons[0] if reasons else "Posture stable"


class PostureAnalyzer:
    def __init__(self, model_path: str, config):
        self.config = config
        self._ts_ms = 0

        # EMA state — initialised to neutral values
        self._smooth_shoulder_angle: float = 0.0
        self._smooth_z_delta_ratio: float = 0.0
        self._smooth_vert_ratio: float = 0.5  # start neutral
        self._smooth_back_lean_angle: float = 0.0
        self._smooth_torso_z_delta: float = 0.0
        self._shoulder_flag = TemporalDurationFlag(
            config.POSTURE_DETECT_CONFIRM_SECONDS, config.POSTURE_DETECT_RECOVERY_SECONDS
        )
        self._head_drop_flag = TemporalDurationFlag(
            config.POSTURE_DETECT_CONFIRM_SECONDS, config.POSTURE_DETECT_RECOVERY_SECONDS
        )
        self._forward_head_flag = TemporalDurationFlag(
            config.POSTURE_DETECT_CONFIRM_SECONDS, config.POSTURE_DETECT_RECOVERY_SECONDS
        )
        self._back_lean_flag = TemporalDurationFlag(
            config.POSTURE_DETECT_CONFIRM_SECONDS, config.POSTURE_DETECT_RECOVERY_SECONDS
        )

        # ── Baseline calibration ───────────────────────────────────────────
        self._calibration_samples: list[dict] = []
        self._baseline_z_delta_ratio: float | None = None
        self._baseline_vert_ratio: float | None = None
        self._baseline_ear_z_delta: float = 0.0
        self._baseline_torso_z: float = 0.0
        self._smooth_ear_z_delta: float = 0.0
        self._calibrated = False
        self._calibration_status = "Not calibrated"

        if config.POSTURE_BASELINE_VALID:
            self._baseline_z_delta_ratio = config.POSTURE_BASELINE_Z
            self._baseline_vert_ratio = config.POSTURE_BASELINE_V
            self._baseline_ear_z_delta = config.POSTURE_BASELINE_EAR_Z
            self._baseline_torso_z = config.POSTURE_BASELINE_TORSO_Z
            self._calibrated = True
            self._calibration_status = "Calibration loaded"

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
        if not self._calibration_samples:
            return
        # Median baselines resist transient landmark jumps and partial movement.
        self._baseline_z_delta_ratio = float(np.median([s["z"] for s in self._calibration_samples]))
        self._baseline_vert_ratio = float(np.median([s["v"] for s in self._calibration_samples]))
        self._baseline_ear_z_delta = float(np.median([s["ez"] for s in self._calibration_samples]))
        torso_samples = [s["tz"] for s in self._calibration_samples if s["tz"] is not None]
        self._baseline_torso_z = float(np.median(torso_samples)) if torso_samples else 0.0
        self._calibrated = True
        self.config.POSTURE_BASELINE_Z = self._baseline_z_delta_ratio
        self.config.POSTURE_BASELINE_V = self._baseline_vert_ratio
        self.config.POSTURE_BASELINE_EAR_Z = self._baseline_ear_z_delta
        self.config.POSTURE_BASELINE_TORSO_Z = self._baseline_torso_z
        self.config.POSTURE_BASELINE_VALID = True
        save_config(self.config)
        self._calibration_status = "Calibration complete"

    def _calibration_reject_reason(
        self,
        raw_angle: float,
        raw_vert_ratio: float,
        raw_z_delta_ratio: float,
        shoulder_width_norm: float,
    ) -> str | None:
        if shoulder_width_norm < 0.08:
            return "Move closer or face the camera"
        if raw_angle > self.config.CALIBRATION_MAX_SHOULDER_ANGLE:
            return "Level your shoulders"
        if raw_vert_ratio < 0.35:
            return "Raise your head"
        if raw_vert_ratio > 2.20:
            return "Adjust camera angle"

        if len(self._calibration_samples) >= 8:
            recent_v = _mean_recent(self._calibration_samples, "v")
            recent_z = _mean_recent(self._calibration_samples, "z")
            drift = max(abs(raw_vert_ratio - recent_v), abs(raw_z_delta_ratio - recent_z))
            if drift > self.config.CALIBRATION_MAX_SAMPLE_DRIFT:
                return "Keep still"

        return None

    def analyze(self, frame_rgb: np.ndarray, state, height: int, width: int):
        self._ts_ms += 33
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self.landmarker.detect_for_video(mp_image, self._ts_ms)

        if not result.pose_landmarks:
            state.landmarks_visible = False
            if not self._calibrated:
                state.calibration_status = "Sit where the camera can see you"
                state.calibration_progress = len(self._calibration_samples) / _CALIBRATION_FRAMES * 100.0
            return state, None

        landmarks = result.pose_landmarks[0]
        l_sh = landmarks[_L_SHOULDER]
        r_sh = landmarks[_R_SHOULDER]
        nose = landmarks[_NOSE]
        l_ear = landmarks[_L_EAR]
        r_ear = landmarks[_R_EAR]
        l_hip = landmarks[_L_HIP]
        r_hip = landmarks[_R_HIP]

        if not all(_visible(lm) for lm in (l_sh, r_sh, nose, l_ear, r_ear)):
            state.landmarks_visible = False
            if not self._calibrated:
                state.calibration_status = "Keep face and shoulders visible"
                state.calibration_progress = len(self._calibration_samples) / _CALIBRATION_FRAMES * 100.0
            return state, None
        state.landmarks_visible = True

        # ── Pixel coordinates ──────────────────────────────────────────────
        l_sh_px = lm_px(l_sh, width, height)
        r_sh_px = lm_px(r_sh, width, height)
        l_hip_px = lm_px(l_hip, width, height)
        r_hip_px = lm_px(r_hip, width, height)

        # ── Normalised metrics ─────────────────────────────────────────────
        shoulder_width_norm = abs(r_sh.x - l_sh.x) + 1e-6
        mid_sy_norm = (l_sh.y + r_sh.y) / 2.0
        avg_shoulder_z = (l_sh.z + r_sh.z) / 2.0
        hips_visible = _visible(l_hip) and _visible(r_hip)
        state.hips_visible = hips_visible

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
        raw_z_delta_ratio = (avg_shoulder_z - nose.z) / shoulder_width_norm
        self._smooth_z_delta_ratio = _ema(self._smooth_z_delta_ratio, raw_z_delta_ratio)

        # ── 4. Ear Z-depth — ear forward relative to shoulders ─────────────
        avg_ear_z = (l_ear.z + r_ear.z) / 2.0
        raw_ear_z_delta = (avg_shoulder_z - avg_ear_z) / shoulder_width_norm
        self._smooth_ear_z_delta = _ema(self._smooth_ear_z_delta, raw_ear_z_delta)

        raw_torso_z = None
        if hips_visible:
            shoulder_mid_px = ((l_sh_px[0] + r_sh_px[0]) / 2.0, (l_sh_px[1] + r_sh_px[1]) / 2.0)
            hip_mid_px = ((l_hip_px[0] + r_hip_px[0]) / 2.0, (l_hip_px[1] + r_hip_px[1]) / 2.0)
            torso_dx = shoulder_mid_px[0] - hip_mid_px[0]
            torso_dy = hip_mid_px[1] - shoulder_mid_px[1]
            raw_back_lean_angle = abs(math.degrees(math.atan2(torso_dx, torso_dy + 1e-6)))
            if raw_back_lean_angle > 90:
                raw_back_lean_angle = 180 - raw_back_lean_angle
            self._smooth_back_lean_angle = _ema(self._smooth_back_lean_angle, raw_back_lean_angle)

            avg_hip_z = (l_hip.z + r_hip.z) / 2.0
            raw_torso_z = (avg_hip_z - avg_shoulder_z) / shoulder_width_norm

        # ── Calibration phase ──────────────────────────────────────────────
        if not self._calibrated:
            reject_reason = self._calibration_reject_reason(
                raw_angle,
                raw_vert_ratio,
                raw_z_delta_ratio,
                shoulder_width_norm,
            )
            if reject_reason is not None:
                state.calibration_rejected_frames += 1
                state.calibration_status = reject_reason
                state.calibration_progress = len(self._calibration_samples) / _CALIBRATION_FRAMES * 100.0
                state.is_slouching = False
                state.is_forward_head = False
                state.head_forward_ratio = 0.0
                state.head_drop_ratio = 0.0
                state.back_lean_angle = 0.0
                state.torso_z_delta = 0.0
                state.posture_score = 100
                state.posture_reasons = []
                state.posture_reason = "Calibration in progress"
                return state, landmarks

            self._calibration_samples.append({
                "z": raw_z_delta_ratio,
                "v": raw_vert_ratio,
                "ez": raw_ear_z_delta,
                "tz": raw_torso_z,
            })
            if len(self._calibration_samples) >= _CALIBRATION_FRAMES:
                self._calibrate()
            state.calibration_status = self._calibration_status if self._calibrated else "Calibrating: sit upright and keep still"
            state.calibration_progress = len(self._calibration_samples) / _CALIBRATION_FRAMES * 100.0
            # During calibration, assume good posture
            state.is_slouching = False
            state.is_forward_head = False
            state.head_forward_ratio = 0.0
            state.head_drop_ratio = 0.0
            state.back_lean_angle = 0.0
            state.torso_z_delta = 0.0
            state.posture_score = 100
            state.posture_reasons = []
            state.posture_reason = "Calibration in progress"
            return state, landmarks

        # ── Post-calibration: compare against personal baseline ────────────
        state.calibration_status = self._calibration_status
        state.calibration_progress = 100.0

        z_deviation = self._smooth_z_delta_ratio - self._baseline_z_delta_ratio
        vert_deviation = self._baseline_vert_ratio - self._smooth_vert_ratio
        ear_z_deviation = self._smooth_ear_z_delta - self._baseline_ear_z_delta
        torso_z_deviation = 0.0
        if hips_visible and raw_torso_z is not None:
            self._smooth_torso_z_delta = _ema(self._smooth_torso_z_delta, raw_torso_z)
            torso_z_deviation = self._smooth_torso_z_delta - self._baseline_torso_z

        # Use the larger of nose/ear z-deviation for forward head (more robust)
        forward_ratio = max(z_deviation, ear_z_deviation * 0.9)
        state.head_forward_ratio = round(forward_ratio, 3)
        state.head_drop_ratio = round(vert_deviation, 3)
        state.back_lean_angle = round(self._smooth_back_lean_angle if hips_visible else 0.0, 1)
        state.torso_z_delta = round(abs(torso_z_deviation), 3)

        lateral_slouch_raw = self._smooth_shoulder_angle > self.config.SLOUCH_ANGLE_THRESHOLD
        vertical_slouch_raw = vert_deviation > self.config.NOSE_DROP_RATIO
        torso_lean_raw = (
            hips_visible
            and (
                self._smooth_back_lean_angle > self.config.BACK_LEAN_ANGLE_THRESHOLD
                or abs(torso_z_deviation) > self.config.TORSO_LEAN_Z_DELTA
            )
        )
        forward_head_raw = forward_ratio > self.config.FORWARD_HEAD_Z_DELTA

        state.shoulder_imbalance_confirmed = self._shoulder_flag.update(lateral_slouch_raw)
        state.head_drop_confirmed = self._head_drop_flag.update(vertical_slouch_raw)
        state.forward_head_confirmed = self._forward_head_flag.update(forward_head_raw)
        state.back_lean_confirmed = self._back_lean_flag.update(torso_lean_raw)
        state.is_slouching = (
            state.shoulder_imbalance_confirmed
            or state.head_drop_confirmed
            or state.back_lean_confirmed
        )
        state.is_forward_head = state.forward_head_confirmed

        state.shoulder_confidence = threshold_confidence(self._smooth_shoulder_angle, self.config.SLOUCH_ANGLE_THRESHOLD)
        state.head_drop_confidence = threshold_confidence(max(0.0, vert_deviation), self.config.NOSE_DROP_RATIO)
        state.forward_head_confidence = threshold_confidence(max(0.0, forward_ratio), self.config.FORWARD_HEAD_Z_DELTA)
        back_lean_confidence = threshold_confidence(self._smooth_back_lean_angle, self.config.BACK_LEAN_ANGLE_THRESHOLD)
        torso_confidence = threshold_confidence(abs(torso_z_deviation), self.config.TORSO_LEAN_Z_DELTA)
        state.back_lean_confidence = max(back_lean_confidence, torso_confidence) if hips_visible else 0.0
        state.posture_confidence = max(
            state.shoulder_confidence,
            state.head_drop_confidence,
            state.forward_head_confidence,
            state.back_lean_confidence,
            state.face_distance_confidence,
        )
        state.posture_score = calculate_posture_score(
            self._smooth_shoulder_angle,
            max(0.0, vert_deviation),
            max(0.0, forward_ratio),
            state.back_lean_angle,
        )
        _set_posture_reasons(state)

        return state, landmarks

    def draw(self, frame: np.ndarray, landmarks, width: int, height: int):
        if landmarks is None:
            return

        l_sh_px = lm_px(landmarks[_L_SHOULDER], width, height)
        r_sh_px = lm_px(landmarks[_R_SHOULDER], width, height)
        nose_px = lm_px(landmarks[_NOSE], width, height)
        l_hip = landmarks[_L_HIP]
        r_hip = landmarks[_R_HIP]

        bad = (
            self._calibrated
            and self.config
            and (
                self._smooth_shoulder_angle > self.config.SLOUCH_ANGLE_THRESHOLD
                or (self._baseline_vert_ratio is not None
                    and (self._baseline_vert_ratio - self._smooth_vert_ratio) > self.config.NOSE_DROP_RATIO)
                or (self._baseline_z_delta_ratio is not None
                    and (self._smooth_z_delta_ratio - self._baseline_z_delta_ratio) > self.config.FORWARD_HEAD_Z_DELTA)
                or (_visible(l_hip) and _visible(r_hip)
                    and self._smooth_back_lean_angle > self.config.BACK_LEAN_ANGLE_THRESHOLD)
            )
        )
        color = (0, 60, 255) if bad else (80, 220, 100)

        cv2.line(frame, l_sh_px, r_sh_px, color, 2)
        cv2.circle(frame, l_sh_px, 5, color, -1)
        cv2.circle(frame, r_sh_px, 5, color, -1)
        cv2.circle(frame, nose_px, 4, (100, 200, 255), -1)
        mid_point = ((l_sh_px[0] + r_sh_px[0]) // 2, (l_sh_px[1] + r_sh_px[1]) // 2)
        cv2.line(frame, mid_point, nose_px, (100, 200, 255), 1, cv2.LINE_AA)
        if _visible(l_hip) and _visible(r_hip):
            hip_mid = (
                int(((l_hip.x + r_hip.x) / 2.0) * width),
                int(((l_hip.y + r_hip.y) / 2.0) * height),
            )
            cv2.line(frame, mid_point, hip_mid, color, 1, cv2.LINE_AA)

        # Status text
        status_txt = "Calibrating..." if not self._calibrated else "Posture OK"
        if bad:
            status_txt = "Fix Posture!"
        cv2.putText(
            frame, status_txt,
            (10, frame.shape[0] - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
        )

    def reset_calibration(self):
        self._calibration_samples = []
        self._baseline_z_delta_ratio = None
        self._baseline_vert_ratio = None
        self._baseline_ear_z_delta = 0.0
        self._baseline_torso_z = 0.0
        self._smooth_shoulder_angle = 0.0
        self._smooth_z_delta_ratio = 0.0
        self._smooth_vert_ratio = 0.5
        self._smooth_ear_z_delta = 0.0
        self._smooth_back_lean_angle = 0.0
        self._smooth_torso_z_delta = 0.0
        self._calibrated = False
        self._calibration_status = "Not calibrated"
        self._shoulder_flag.reset()
        self._head_drop_flag.reset()
        self._forward_head_flag.reset()
        self._back_lean_flag.reset()

    def close(self):
        self.landmarker.close()
