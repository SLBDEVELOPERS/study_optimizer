import math
import time
from collections import deque

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions

from camera.scoring import calculate_fatigue_score, threshold_confidence
from camera.temporal import TemporalFlag
from config import save_config


# ── Eye landmark indices (MediaPipe 468-point mesh) ────────────────────────
_LEFT_EYE  = [362, 385, 387, 263, 373, 380]
_RIGHT_EYE = [33,  160, 158, 133, 153, 144]

# ── Mouth landmark indices for MAR (yawn detection) ───────────────────────
# Vertical pair: top-lip / bottom-lip; horizontal pair: corners
# Using the standard 6-point mouth representation from the mesh
_MOUTH_OUTER = [61, 291, 13, 14, 78, 308]   # left-corner, right-corner, inner top/bottom, top-left/right
_MOUTH_TOP    = 13   # upper inner lip center
_MOUTH_BOTTOM = 14   # lower inner lip center
_MOUTH_LEFT   = 61   # left corner
_MOUTH_RIGHT  = 291  # right corner
_MOUTH_TOP_L  = 78   # upper-left
_MOUTH_TOP_R  = 308  # upper-right
_NOSE_TIP = 1

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


_NOD_DROP_THRESHOLD = 0.014   # normalised Y shift per frame = nod down
_NOD_RECOVER_THRESHOLD = 0.008  # recovery upward shift = nod confirmed
_MIN_EAR_BASELINE_SAMPLES = 150  # open-eye frames needed before personalising threshold


class FaceEyeAnalyzer:
    def __init__(self, model_path: str, config):
        self.config = config
        self._ts_ms = 1
        self._smooth_face_size: float = 0.0

        # Personal EAR baseline
        self._personal_ear_samples: list[float] = []
        self._personal_ear_baseline: float = 0.0
        self._personal_ear_valid: bool = False
        if config.PERSONAL_EAR_VALID and config.PERSONAL_EAR_BASELINE > 0:
            self._personal_ear_baseline = config.PERSONAL_EAR_BASELINE
            self._personal_ear_valid = True

        # Head nod detection state
        self._prev_face_y: float = 0.0
        self._prev_face_size: float = 0.0
        self._nose_drop_baseline: float | None = None
        self._nod_drop_peak: float = 0.0
        self._nod_down_pending: bool = False
        self._nod_pending_start: float = 0.0
        self._last_face_seen: float = time.time()
        self._yawn_frontal_frames: int = 0
        self._too_close_flag = TemporalFlag(config.FACE_DISTANCE_CONFIRM_FRAMES, config.FACE_DISTANCE_CONFIRM_FRAMES)
        self._yawn_flag = TemporalFlag(1, max(3, config.MAR_CONSEC_FRAMES // 2))
        self._drowsy_flag = TemporalFlag(config.FATIGUE_CONFIRM_FRAMES, config.FATIGUE_RECOVERY_FRAMES)
        self._no_face_focus_flag = TemporalFlag(
            int(config.FOCUS_LOSS_CONFIRM_SECONDS * max(config.FPS, 1)),
            int(2 * max(config.FPS, 1)),
        )
        self._gaze_away_flag = TemporalFlag(
            int(config.GAZE_AWAY_CONFIRM_SECONDS * max(config.FPS, 1)),
            int(2 * max(config.FPS, 1)),
        )

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
            now = time.time()
            fatigue_state.face_visible = False
            no_face_elapsed = now - self._last_face_seen
            focus_raw = no_face_elapsed >= self.config.FOCUS_LOSS_CONFIRM_SECONDS
            self._gaze_away_flag.reset()
            fatigue_state.is_focus_lost = self._no_face_focus_flag.update(focus_raw)
            fatigue_state.focus_loss_reason = "Face not visible" if fatigue_state.is_focus_lost else ""
            fatigue_state.focus_confidence = min(1.0, no_face_elapsed / max(self.config.FOCUS_LOSS_CONFIRM_SECONDS, 1e-6))
            return fatigue_state, posture_state, None

        landmarks = result.face_landmarks[0]
        now = time.time()
        fatigue_state.face_visible = True
        self._last_face_seen = now
        self._no_face_focus_flag.reset()
        xs = [lm.x for lm in landmarks]
        ys = [lm.y for lm in landmarks]
        face_width = max(xs) - min(xs) + 1e-6
        raw_face_size = max(ys) - min(ys)
        face_center_x = (max(xs) + min(xs)) / 2.0
        nose_x = landmarks[_NOSE_TIP].x
        fatigue_state.gaze_away_ratio = abs(nose_x - face_center_x) / face_width

        def eye_points(indices):
            return [(int(landmarks[i].x * width), int(landmarks[i].y * height)) for i in indices]

        # ── 1. EAR with rolling-average smoothing ──────────────────────────
        raw_ear_left  = eye_aspect_ratio(eye_points(_LEFT_EYE))
        raw_ear_right = eye_aspect_ratio(eye_points(_RIGHT_EYE))
        raw_ear_avg   = (raw_ear_left + raw_ear_right) / 2.0

        ear_avg = self._smooth_ear(fatigue_state, raw_ear_avg)

        fatigue_state.ear_left  = raw_ear_left
        fatigue_state.ear_right = raw_ear_right
        fatigue_state.ear_avg   = ear_avg
        fatigue_state.ear_history.append(ear_avg)

        # ── Personal EAR baseline: collect open-eye samples ───────────────
        # Floor is deliberately low (below typical open-eye EAR) so users
        # with naturally smaller/narrower eyes still accumulate enough
        # samples to personalise — 0.15 still excludes genuine blinks/closures.
        if not self._personal_ear_valid and ear_avg > 0.15:
            self._personal_ear_samples.append(ear_avg)
            if len(self._personal_ear_samples) >= _MIN_EAR_BASELINE_SAMPLES:
                sorted_s = sorted(self._personal_ear_samples)
                # 75th percentile = typical open-eye EAR, robust to partial blinks
                p75 = sorted_s[int(len(sorted_s) * 0.75)]
                if p75 > 0.15:
                    self._personal_ear_baseline = p75
                    self._personal_ear_valid = True
                    self.config.PERSONAL_EAR_BASELINE = p75
                    self.config.PERSONAL_EAR_VALID = True
                    save_config(self.config)

        # Adaptive thresholds: personal baseline when available, else config fallback
        if self._personal_ear_valid:
            ear_threshold = max(0.15, self._personal_ear_baseline * 0.65)
            drowsy_ear_avg = self._personal_ear_baseline * 0.87
        else:
            ear_threshold = self.config.EAR_THRESHOLD
            drowsy_ear_avg = self.config.DROWSY_EAR_AVG

        fatigue_state.eye_closed_history.append(ear_avg < ear_threshold)
        fatigue_state.perclos = (
            sum(fatigue_state.eye_closed_history) / len(fatigue_state.eye_closed_history)
            if fatigue_state.eye_closed_history else 0.0
        )

        # Nose-relative drop is more stable than whole-face Y movement.
        face_center_y = (max(ys) + min(ys)) / 2.0
        nose_drop_ratio = (landmarks[_NOSE_TIP].y - face_center_y) / (raw_face_size + 1e-6)
        if self._nose_drop_baseline is None:
            self._nose_drop_baseline = nose_drop_ratio
        face_size_delta = abs(raw_face_size - self._prev_face_size)
        stable_face_scale = self._prev_face_size <= 0 or face_size_delta < self.config.NOD_MAX_FACE_SCALE_DELTA
        head_drop = nose_drop_ratio - self._nose_drop_baseline
        fatigue_state.nod_drop_ratio = round(max(0.0, head_drop), 3)
        if stable_face_scale:
            if head_drop > self.config.NOD_DROP_RATIO:
                if not self._nod_down_pending:
                    self._nod_pending_start = now
                self._nod_down_pending = True
                self._nod_drop_peak = max(self._nod_drop_peak, head_drop)
            elif self._nod_down_pending and head_drop < self.config.NOD_RECOVER_RATIO:
                # A quick drop-and-recover is a drowsy micro-nod. A dwell
                # that lingers past NOD_MAX_DURATION_SECONDS is more likely
                # a deliberate posture change (e.g. reading notes) — don't
                # count it, just settle into the new baseline.
                if now - self._nod_pending_start <= self.config.NOD_MAX_DURATION_SECONDS:
                    fatigue_state.nod_count += 1
                    fatigue_state.nod_times.append(now)
                self._nod_down_pending = False
                self._nod_drop_peak = 0.0
            elif not self._nod_down_pending:
                self._nose_drop_baseline = 0.98 * self._nose_drop_baseline + 0.02 * nose_drop_ratio
        else:
            self._nod_down_pending = False
            self._nod_drop_peak = 0.0
        self._prev_face_y = face_center_y
        self._prev_face_size = raw_face_size

        # ── 2. Blink detection (on smoothed EAR) ──────────────────────────
        now = time.time()
        if ear_avg < ear_threshold:
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

        face_frontal = fatigue_state.gaze_away_ratio <= self.config.YAWN_MAX_GAZE_AWAY_RATIO
        mouth_open = mar > self.config.MAR_THRESHOLD
        if mouth_open:
            # Count every open-mouth frame regardless of momentary gaze —
            # a single glance-away frame mid-yawn shouldn't split/kill it.
            # Frontal-ness is judged by majority over the whole window instead.
            fatigue_state.consec_mouth_open += 1
            if face_frontal:
                self._yawn_frontal_frames += 1
        else:
            yawn_frames = fatigue_state.consec_mouth_open
            frontal_ratio = (self._yawn_frontal_frames / yawn_frames) if yawn_frames else 0.0
            if (
                frontal_ratio >= 0.7
                and self.config.YAWN_MIN_FRAMES <= yawn_frames <= self.config.YAWN_MAX_FRAMES
            ):
                fatigue_state.yawn_count += 1
                fatigue_state.yawn_times.append(now)
            fatigue_state.consec_mouth_open = 0
            self._yawn_frontal_frames = 0

        yawn_active = (
            face_frontal
            and self.config.YAWN_MIN_FRAMES <= fatigue_state.consec_mouth_open <= self.config.YAWN_MAX_FRAMES
        )
        fatigue_state.is_yawning = self._yawn_flag.update(yawn_active)
        fatigue_state.yawn_confidence = threshold_confidence(mar, self.config.MAR_THRESHOLD)

        # ── 4. Face distance — smoothed face size ratio ────────────────────
        self._smooth_face_size = _ALPHA * raw_face_size + (1.0 - _ALPHA) * self._smooth_face_size
        posture_state.face_size_ratio = self._smooth_face_size
        too_close_raw = self._smooth_face_size > self.config.CLOSE_FACE_RATIO
        posture_state.is_too_close = self._too_close_flag.update(too_close_raw)
        posture_state.face_distance_confidence = threshold_confidence(self._smooth_face_size, self.config.CLOSE_FACE_RATIO)

        # ── 5. Drowsiness decision ─────────────────────────────────────────
        # Gate ALL drowsiness checks behind the minimum observation window.
        # During the first N seconds, face detection is warming up and EAR
        # values are unreliable — skip drowsiness entirely.
        if session_elapsed < self.config.BLINK_RATE_MIN_SECONDS:
            fatigue_state.is_drowsy = False
            fatigue_state.drowsy_start = 0.0
            fatigue_state.fatigue_score = 0
            return fatigue_state, posture_state, landmarks

        history_avg = (
            sum(fatigue_state.ear_history) / len(fatigue_state.ear_history)
            if fatigue_state.ear_history else 0.3
        )
        ear_low = len(fatigue_state.ear_history) > 60 and history_avg < drowsy_ear_avg

        blink_bad = (
            fatigue_state.blink_rate > 0
            and (
                fatigue_state.blink_rate < self.config.DROWSY_BLINK_RATE_LOW
                or fatigue_state.blink_rate > self.config.DROWSY_BLINK_RATE_HIGH
            )
        )
        fatigue_state.eye_confidence = threshold_confidence(fatigue_state.perclos, 0.30)
        if fatigue_state.blink_rate > 0:
            if fatigue_state.blink_rate < self.config.DROWSY_BLINK_RATE_LOW:
                fatigue_state.blink_confidence = threshold_confidence(
                    self.config.DROWSY_BLINK_RATE_LOW - fatigue_state.blink_rate,
                    self.config.DROWSY_BLINK_RATE_LOW,
                )
            elif fatigue_state.blink_rate > self.config.DROWSY_BLINK_RATE_HIGH:
                fatigue_state.blink_confidence = threshold_confidence(
                    fatigue_state.blink_rate - self.config.DROWSY_BLINK_RATE_HIGH,
                    20.0,
                )
            else:
                fatigue_state.blink_confidence = 0.0
        else:
            fatigue_state.blink_confidence = 0.0

        recent_cutoff = now - 600.0
        while fatigue_state.yawn_times and fatigue_state.yawn_times[0] < recent_cutoff:
            fatigue_state.yawn_times.popleft()
        while fatigue_state.nod_times and fatigue_state.nod_times[0] < recent_cutoff:
            fatigue_state.nod_times.popleft()

        recent_yawns = len(fatigue_state.yawn_times) + (1 if fatigue_state.is_yawning else 0)
        recent_nods = len(fatigue_state.nod_times)
        fatigue_state.fatigue_score = calculate_fatigue_score(
            fatigue_state.perclos,
            recent_yawns,
            recent_nods,
            fatigue_state.blink_rate,
        )

        score_signal = fatigue_state.fatigue_score >= 60
        perclos_signal = fatigue_state.perclos >= 0.30 or (ear_low and fatigue_state.perclos >= 0.18)
        blink_signal = blink_bad and fatigue_state.perclos >= 0.12
        yawn_signal = recent_yawns >= 3
        nod_signal = recent_nods >= 3
        fatigue_state.nod_confidence = min(1.0, recent_nods / 3.0)
        fatigue_state.fatigue_confidence = max(
            fatigue_state.eye_confidence,
            fatigue_state.yawn_confidence,
            fatigue_state.nod_confidence,
            fatigue_state.blink_confidence,
            threshold_confidence(fatigue_state.fatigue_score, 60.0),
        )

        gaze_away_raw = fatigue_state.gaze_away_ratio > self.config.GAZE_AWAY_RATIO
        fatigue_state.is_focus_lost = self._gaze_away_flag.update(gaze_away_raw)
        fatigue_state.focus_confidence = threshold_confidence(
            fatigue_state.gaze_away_ratio,
            self.config.GAZE_AWAY_RATIO,
        )
        fatigue_state.focus_loss_reason = "Looking away" if fatigue_state.is_focus_lost else ""

        was_drowsy = fatigue_state.is_drowsy
        # Eye-closure evidence (perclos/blink) or the weighted composite score
        # is trusted on its own. Yawning and nodding are common for reasons
        # unrelated to fatigue (talking, stretching, reading posture), so
        # either alone is not enough — only count them once they corroborate
        # each other.
        eye_evidence = perclos_signal or blink_signal
        secondary_signal_count = int(yawn_signal) + int(nod_signal)
        drowsy_raw = eye_evidence or score_signal or secondary_signal_count >= 2
        fatigue_state.is_drowsy = self._drowsy_flag.update(drowsy_raw)
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
            f"EAR:{fatigue_state.ear_avg:.3f}  MAR:{fatigue_state.mar:.3f}  PERCLOS:{fatigue_state.perclos:.2f}  F:{fatigue_state.fatigue_score}",
            (10, frame.shape[0] - 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )

    def reset_ear_calibration(self):
        self._personal_ear_samples = []
        self._personal_ear_baseline = 0.0
        self._personal_ear_valid = False
        self._prev_face_y = 0.0
        self._prev_face_size = 0.0
        self._nose_drop_baseline = None
        self._nod_drop_peak = 0.0
        self._nod_down_pending = False
        self._nod_pending_start = 0.0
        self._last_face_seen = time.time()
        self._yawn_frontal_frames = 0
        self._too_close_flag.reset()
        self._yawn_flag.reset()
        self._drowsy_flag.reset()
        self._no_face_focus_flag.reset()
        self._gaze_away_flag.reset()

    def close(self):
        self.landmarker.close()
