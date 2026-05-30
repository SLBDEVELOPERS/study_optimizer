import math
import time
from collections import deque

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions


# ── Eye landmark indices (MediaPipe 468-point mesh) ────────────────────────
_LEFT_EYE  = [362, 385, 387, 263, 373, 380]
_RIGHT_EYE = [33,  160, 158, 133, 153, 144]

# ── Mouth landmark indices for MAR (yawn detection) ───────────────────────
# Vertical pair: top-lip / bottom-lip; horizontal pair: corners
# Using the standard 6-point mouth representation from the mesh
_MOUTH_OUTER = [61, 291, 0, 17, 78, 308]   # left-corner, right-corner, top, bottom, top-left, top-right
_MOUTH_TOP    = 0    # upper lip center
_MOUTH_BOTTOM = 17   # lower lip center
_MOUTH_LEFT   = 61   # left corner
_MOUTH_RIGHT  = 291  # right corner
_MOUTH_TOP_L  = 78   # upper-left
_MOUTH_TOP_R  = 308  # upper-right

# EMA smoothing factor
_ALPHA = 0.3


def _dist(a: tuple, b: tuple) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def eye_aspect_ratio(points: list[tuple[int, int]]) -> float:
    """Standard 6-point EAR formula."""
    a = _dist(points[1], points[5])
    b = _dist(points[2], points[4])
    c = _dist(points[0], points[3])
    return (a + b) / (2.0 * c + 1e-6)


def mouth_aspect_ratio(landmarks, width: int, height: int) -> float:
    """
    Mouth Aspect Ratio (MAR) — analogous to EAR but for the mouth.
    High MAR (> threshold) indicates the mouth is open wide → yawn.
    """
    def pt(idx):
        lm = landmarks[idx]
        return int(lm.x * width), int(lm.y * height)

    top    = pt(_MOUTH_TOP)
    bottom = pt(_MOUTH_BOTTOM)
    left   = pt(_MOUTH_LEFT)
    right  = pt(_MOUTH_RIGHT)
    top_l  = pt(_MOUTH_TOP_L)
    top_r  = pt(_MOUTH_TOP_R)

    vertical_a = _dist(top_l, bottom)
    vertical_b = _dist(top_r, bottom)
    horizontal = _dist(left, right) + 1e-6

    return (vertical_a + vertical_b) / (2.0 * horizontal)


class FaceEyeAnalyzer:
    def __init__(self, model_path: str, config):
        self.config = config
        self._ts_ms = 1
        self._smooth_face_size: float = 0.0  # EMA for face_size_ratio

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

    # ── Internal helpers ───────────────────────────────────────────────────

    def _smooth_ear(self, fatigue_state, raw_ear: float) -> float:
        """Rolling-average EAR over last N frames to reduce landmark jitter."""
        fatigue_state.ear_smooth_buf.append(raw_ear)
        return sum(fatigue_state.ear_smooth_buf) / len(fatigue_state.ear_smooth_buf)

    # ── Main analysis ──────────────────────────────────────────────────────

    def analyze(self, frame_rgb: np.ndarray, fatigue_state, posture_state, height: int, width: int):
        self._ts_ms += 33
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self.landmarker.detect_for_video(mp_image, self._ts_ms)

        if not result.face_landmarks:
            return fatigue_state, posture_state, None

        landmarks = result.face_landmarks[0]

        def eye_points(indices):
            return [(int(landmarks[i].x * width), int(landmarks[i].y * height)) for i in indices]

        # ── 1. EAR with rolling-average smoothing ──────────────────────────
        raw_ear_left  = eye_aspect_ratio(eye_points(_LEFT_EYE))
        raw_ear_right = eye_aspect_ratio(eye_points(_RIGHT_EYE))
        raw_ear_avg   = (raw_ear_left + raw_ear_right) / 2.0

        # Apply per-eye smoothing by averaging the smoothed combined value
        ear_avg = self._smooth_ear(fatigue_state, raw_ear_avg)

        fatigue_state.ear_left  = raw_ear_left
        fatigue_state.ear_right = raw_ear_right
        fatigue_state.ear_avg   = ear_avg
        fatigue_state.ear_history.append(ear_avg)

        # ── 2. Blink detection (on smoothed EAR) ──────────────────────────
        now = time.time()
        if ear_avg < self.config.EAR_THRESHOLD:
            fatigue_state.consec_below_threshold += 1
        else:
            if fatigue_state.consec_below_threshold >= self.config.EAR_CONSEC_FRAMES:
                fatigue_state.blink_count += 1
                fatigue_state.blink_times.append(now)
            fatigue_state.consec_below_threshold = 0

        # Trim old blink timestamps
        cutoff = now - self.config.BLINK_WINDOW_SECONDS
        while fatigue_state.blink_times and fatigue_state.blink_times[0] < cutoff:
            fatigue_state.blink_times.popleft()

        # Blink rate (blinks/min) — only once minimum observation window elapsed
        session_elapsed = now - fatigue_state.session_start
        if fatigue_state.blink_times and session_elapsed >= self.config.BLINK_RATE_MIN_SECONDS:
            span = min(now - fatigue_state.blink_times[0], self.config.BLINK_WINDOW_SECONDS)
            fatigue_state.blink_rate = (len(fatigue_state.blink_times) / span * 60.0) if span > 5 else 0.0
        else:
            fatigue_state.blink_rate = 0.0

        # ── 3. Yawn detection via MAR ──────────────────────────────────────
        mar = mouth_aspect_ratio(landmarks, width, height)
        fatigue_state.mar = mar

        if mar > self.config.MAR_THRESHOLD:
            fatigue_state.consec_mouth_open += 1
        else:
            if fatigue_state.consec_mouth_open >= self.config.MAR_CONSEC_FRAMES:
                fatigue_state.yawn_count += 1
            fatigue_state.consec_mouth_open = 0

        fatigue_state.is_yawning = fatigue_state.consec_mouth_open >= self.config.MAR_CONSEC_FRAMES

        # ── 4. Face distance — smoothed face size ratio ────────────────────
        ys = [landmarks[i].y for i in range(len(landmarks))]
        raw_face_size = max(ys) - min(ys)
        self._smooth_face_size = _ALPHA * raw_face_size + (1.0 - _ALPHA) * self._smooth_face_size
        posture_state.face_size_ratio = self._smooth_face_size
        posture_state.is_too_close = self._smooth_face_size > self.config.CLOSE_FACE_RATIO

        # ── 5. Drowsiness decision ─────────────────────────────────────────
        # Gate ALL drowsiness checks behind the minimum observation window.
        # During the first N seconds, face detection is warming up and EAR
        # values are unreliable — skip drowsiness entirely.
        if session_elapsed < self.config.BLINK_RATE_MIN_SECONDS:
            fatigue_state.is_drowsy = False
            fatigue_state.drowsy_start = 0.0
            return fatigue_state, posture_state, landmarks

        history_avg = (
            sum(fatigue_state.ear_history) / len(fatigue_state.ear_history)
            if fatigue_state.ear_history else 0.3
        )
        # Only flag ear_low if we have enough history (>60 frames = ~2s of stable data)
        ear_low = len(fatigue_state.ear_history) > 60 and history_avg < self.config.DROWSY_EAR_AVG

        blink_bad = (
            fatigue_state.blink_rate > 0
            and (
                fatigue_state.blink_rate < self.config.DROWSY_BLINK_RATE_LOW
                or fatigue_state.blink_rate > self.config.DROWSY_BLINK_RATE_HIGH
            )
        )

        # Yawning alone can trigger drowsiness
        yawn_signal = fatigue_state.yawn_count >= 2 or fatigue_state.is_yawning

        was_drowsy = fatigue_state.is_drowsy
        fatigue_state.is_drowsy = ear_low or blink_bad or yawn_signal
        if fatigue_state.is_drowsy and not was_drowsy:
            fatigue_state.drowsy_start = now
        elif not fatigue_state.is_drowsy:
            fatigue_state.drowsy_start = 0.0

        return fatigue_state, posture_state, landmarks

    # ── Drawing ────────────────────────────────────────────────────────────

    def draw(self, frame: np.ndarray, landmarks, fatigue_state, width: int, height: int):
        if landmarks is None:
            return

        eye_color   = (0, 60, 255) if fatigue_state.is_drowsy else (0, 220, 100)
        mouth_color = (0, 180, 255) if fatigue_state.is_yawning else (120, 120, 120)

        # Draw eye contours
        for indices in (_LEFT_EYE, _RIGHT_EYE):
            points = np.array(
                [(int(landmarks[i].x * width), int(landmarks[i].y * height)) for i in indices],
                dtype=np.int32,
            )
            cv2.polylines(frame, [points], True, eye_color, 1, cv2.LINE_AA)

        # Draw mouth corners to indicate MAR tracking
        lc = (int(landmarks[_MOUTH_LEFT].x * width),  int(landmarks[_MOUTH_LEFT].y * height))
        rc = (int(landmarks[_MOUTH_RIGHT].x * width), int(landmarks[_MOUTH_RIGHT].y * height))
        cv2.line(frame, lc, rc, mouth_color, 1, cv2.LINE_AA)

        # Debug overlay — MAR value
        cv2.putText(
            frame,
            f"EAR:{fatigue_state.ear_avg:.3f}  MAR:{fatigue_state.mar:.3f}  Yawns:{fatigue_state.yawn_count}",
            (10, frame.shape[0] - 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )

    def close(self):
        self.landmarker.close()
