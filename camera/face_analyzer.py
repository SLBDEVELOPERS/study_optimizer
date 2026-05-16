import math
import time

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions


_LEFT_EYE = [362, 385, 387, 263, 373, 380]
_RIGHT_EYE = [33, 160, 158, 133, 153, 144]


def eye_aspect_ratio(points: list[tuple[int, int]]) -> float:
    def distance(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    a = distance(points[1], points[5])
    b = distance(points[2], points[4])
    c = distance(points[0], points[3])
    return (a + b) / (2.0 * c + 1e-6)


class FaceEyeAnalyzer:
    def __init__(self, model_path: str, config):
        self.config = config
        self._ts_ms = 1
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

    def analyze(self, frame_rgb: np.ndarray, fatigue_state, posture_state, height: int, width: int):
        self._ts_ms += 33
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self.landmarker.detect_for_video(mp_image, self._ts_ms)

        if not result.face_landmarks:
            return fatigue_state, posture_state, None

        landmarks = result.face_landmarks[0]

        def eye_points(indices):
            return [(int(landmarks[i].x * width), int(landmarks[i].y * height)) for i in indices]

        ear_left = eye_aspect_ratio(eye_points(_LEFT_EYE))
        ear_right = eye_aspect_ratio(eye_points(_RIGHT_EYE))
        ear_avg = (ear_left + ear_right) / 2.0

        fatigue_state.ear_left = ear_left
        fatigue_state.ear_right = ear_right
        fatigue_state.ear_avg = ear_avg
        fatigue_state.ear_history.append(ear_avg)

        now = time.time()
        if ear_avg < self.config.EAR_THRESHOLD:
            fatigue_state.consec_below_threshold += 1
        else:
            if fatigue_state.consec_below_threshold >= self.config.EAR_CONSEC_FRAMES:
                fatigue_state.blink_count += 1
                fatigue_state.blink_times.append(now)
            fatigue_state.consec_below_threshold = 0

        cutoff = now - self.config.BLINK_WINDOW_SECONDS
        while fatigue_state.blink_times and fatigue_state.blink_times[0] < cutoff:
            fatigue_state.blink_times.popleft()

        if fatigue_state.blink_times:
            span = min(now - fatigue_state.blink_times[0], self.config.BLINK_WINDOW_SECONDS)
            fatigue_state.blink_rate = (len(fatigue_state.blink_times) / span * 60.0) if span > 5 else 0.0
        else:
            fatigue_state.blink_rate = 0.0

        ys = [landmarks[i].y for i in range(len(landmarks))]
        posture_state.face_size_ratio = max(ys) - min(ys)
        posture_state.is_too_close = posture_state.face_size_ratio > self.config.CLOSE_FACE_RATIO

        history_avg = sum(fatigue_state.ear_history) / len(fatigue_state.ear_history) if fatigue_state.ear_history else 0.3
        ear_low = len(fatigue_state.ear_history) > 30 and history_avg < self.config.DROWSY_EAR_AVG
        blink_bad = fatigue_state.blink_rate > 0 and (
            fatigue_state.blink_rate < self.config.DROWSY_BLINK_RATE_LOW
            or fatigue_state.blink_rate > self.config.DROWSY_BLINK_RATE_HIGH
        )

        was_drowsy = fatigue_state.is_drowsy
        fatigue_state.is_drowsy = ear_low or blink_bad
        if fatigue_state.is_drowsy and not was_drowsy:
            fatigue_state.drowsy_start = now
        elif not fatigue_state.is_drowsy:
            fatigue_state.drowsy_start = 0.0

        return fatigue_state, posture_state, landmarks

    def draw(self, frame: np.ndarray, landmarks, fatigue_state, width: int, height: int):
        if landmarks is None:
            return

        color = (0, 60, 255) if fatigue_state.is_drowsy else (0, 220, 100)
        for indices in (_LEFT_EYE, _RIGHT_EYE):
            points = np.array(
                [(int(landmarks[i].x * width), int(landmarks[i].y * height)) for i in indices],
                dtype=np.int32,
            )
            cv2.polylines(frame, [points], True, color, 1, cv2.LINE_AA)

    def close(self):
        self.landmarker.close()
