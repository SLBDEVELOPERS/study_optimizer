import math

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions


_NOSE = 0
_L_SHOULDER = 11
_R_SHOULDER = 12


def lm_px(lm, width: int, height: int) -> tuple[int, int]:
    return int(lm.x * width), int(lm.y * height)


class PostureAnalyzer:
    def __init__(self, model_path: str, config):
        self.config = config
        self._ts_ms = 0
        options = PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.6,
            min_pose_presence_confidence=0.6,
            min_tracking_confidence=0.6,
        )
        self.landmarker = PoseLandmarker.create_from_options(options)

    def analyze(self, frame_rgb: np.ndarray, state, height: int, width: int):
        self._ts_ms += 33
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self.landmarker.detect_for_video(mp_image, self._ts_ms)

        if not result.pose_landmarks:
            return state, None

        landmarks = result.pose_landmarks[0]
        left_shoulder = lm_px(landmarks[_L_SHOULDER], width, height)
        right_shoulder = lm_px(landmarks[_R_SHOULDER], width, height)
        nose = lm_px(landmarks[_NOSE], width, height)

        dy = right_shoulder[1] - left_shoulder[1]
        dx = right_shoulder[0] - left_shoulder[0]
        angle = abs(math.degrees(math.atan2(dy, dx + 1e-6)))
        if angle > 90:
            angle = 180 - angle
        state.shoulder_angle = angle

        mid_sx = (left_shoulder[0] + right_shoulder[0]) / 2
        shoulder_width = abs(right_shoulder[0] - left_shoulder[0]) + 1e-6
        state.head_forward_ratio = abs((nose[0] - mid_sx) / shoulder_width)
        state.is_slouching = state.shoulder_angle > self.config.SLOUCH_ANGLE_THRESHOLD
        state.is_forward_head = state.head_forward_ratio > self.config.FORWARD_HEAD_RATIO

        return state, landmarks

    def draw(self, frame: np.ndarray, landmarks, width: int, height: int):
        if landmarks is None:
            return

        left_shoulder = lm_px(landmarks[_L_SHOULDER], width, height)
        right_shoulder = lm_px(landmarks[_R_SHOULDER], width, height)
        nose = lm_px(landmarks[_NOSE], width, height)
        color = (
            (0, 60, 255)
            if abs(math.degrees(math.atan2(right_shoulder[1] - left_shoulder[1], right_shoulder[0] - left_shoulder[0] + 1e-6)))
            > self.config.SLOUCH_ANGLE_THRESHOLD
            else (80, 220, 100)
        )

        cv2.line(frame, left_shoulder, right_shoulder, color, 2)
        cv2.circle(frame, left_shoulder, 5, color, -1)
        cv2.circle(frame, right_shoulder, 5, color, -1)
        cv2.circle(frame, nose, 4, (100, 200, 255), -1)
        mid_point = ((left_shoulder[0] + right_shoulder[0]) // 2, (left_shoulder[1] + right_shoulder[1]) // 2)
        cv2.line(frame, mid_point, nose, (100, 200, 255), 1, cv2.LINE_AA)

    def close(self):
        self.landmarker.close()
