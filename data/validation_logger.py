import csv
import time
from pathlib import Path


class ValidationLogger:
    FIELDNAMES = [
        "timestamp",
        "session_elapsed_s",
        "frame",
        "fps",
        "camera_status",
        "landmarks_visible",
        "hips_visible",
        "posture_calibrated",
        "calibration_status",
        "calibration_progress",
        "calibration_rejected_frames",
        "shoulder_angle",
        "head_forward_ratio",
        "head_drop_ratio",
        "back_lean_angle",
        "torso_z_delta",
        "face_size_ratio",
        "posture_score",
        "posture_reason",
        "posture_reasons",
        "posture_confidence",
        "shoulder_confidence",
        "head_drop_confidence",
        "forward_head_confidence",
        "back_lean_confidence",
        "face_distance_confidence",
        "shoulder_imbalance_confirmed",
        "head_drop_confirmed",
        "forward_head_confirmed",
        "back_lean_confirmed",
        "is_slouching",
        "is_forward_head",
        "is_too_close",
        "face_visible",
        "ear_left",
        "ear_right",
        "ear_avg",
        "blink_rate",
        "mar",
        "perclos",
        "fatigue_score",
        "fatigue_confidence",
        "eye_confidence",
        "yawn_confidence",
        "nod_confidence",
        "blink_confidence",
        "focus_confidence",
        "gaze_away_ratio",
        "nod_drop_ratio",
        "is_focus_lost",
        "focus_loss_reason",
        "is_yawning",
        "is_drowsy",
        "yawn_count",
        "nod_count",
        "room_light_level",
        "room_light_status",
        "posture_alerts",
        "fatigue_alerts",
    ]

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append_state(self, state, frame_number: int, fps: float, camera_status: str):
        file_exists = self.path.exists() and self.path.stat().st_size > 0
        posture = state.posture
        fatigue = state.fatigue
        posture_reasons = list(posture.posture_reasons)
        if posture.is_too_close and "Too close to screen" not in posture_reasons:
            posture_reasons.append("Too close to screen")
        now = time.time()
        row = {
            "timestamp": int(now),
            "session_elapsed_s": int(now - state.session_start),
            "frame": frame_number,
            "fps": round(fps, 2),
            "camera_status": camera_status,
            "landmarks_visible": posture.landmarks_visible,
            "hips_visible": posture.hips_visible,
            "posture_calibrated": posture.calibration_progress >= 100.0,
            "calibration_status": posture.calibration_status,
            "calibration_progress": round(posture.calibration_progress, 1),
            "calibration_rejected_frames": posture.calibration_rejected_frames,
            "shoulder_angle": round(posture.shoulder_angle, 3),
            "head_forward_ratio": round(posture.head_forward_ratio, 3),
            "head_drop_ratio": round(posture.head_drop_ratio, 3),
            "back_lean_angle": round(posture.back_lean_angle, 3),
            "torso_z_delta": round(posture.torso_z_delta, 3),
            "face_size_ratio": round(posture.face_size_ratio, 3),
            "posture_score": posture.posture_score,
            "posture_reason": posture_reasons[0] if posture_reasons else posture.posture_reason,
            "posture_reasons": "|".join(posture_reasons),
            "posture_confidence": round(posture.posture_confidence, 3),
            "shoulder_confidence": round(posture.shoulder_confidence, 3),
            "head_drop_confidence": round(posture.head_drop_confidence, 3),
            "forward_head_confidence": round(posture.forward_head_confidence, 3),
            "back_lean_confidence": round(posture.back_lean_confidence, 3),
            "face_distance_confidence": round(posture.face_distance_confidence, 3),
            "shoulder_imbalance_confirmed": posture.shoulder_imbalance_confirmed,
            "head_drop_confirmed": posture.head_drop_confirmed,
            "forward_head_confirmed": posture.forward_head_confirmed,
            "back_lean_confirmed": posture.back_lean_confirmed,
            "is_slouching": posture.is_slouching,
            "is_forward_head": posture.is_forward_head,
            "is_too_close": posture.is_too_close,
            "face_visible": fatigue.face_visible,
            "ear_left": round(fatigue.ear_left, 3),
            "ear_right": round(fatigue.ear_right, 3),
            "ear_avg": round(fatigue.ear_avg, 3),
            "blink_rate": round(fatigue.blink_rate, 2),
            "mar": round(fatigue.mar, 3),
            "perclos": round(fatigue.perclos, 3),
            "fatigue_score": fatigue.fatigue_score,
            "fatigue_confidence": round(fatigue.fatigue_confidence, 3),
            "eye_confidence": round(fatigue.eye_confidence, 3),
            "yawn_confidence": round(fatigue.yawn_confidence, 3),
            "nod_confidence": round(fatigue.nod_confidence, 3),
            "blink_confidence": round(fatigue.blink_confidence, 3),
            "focus_confidence": round(fatigue.focus_confidence, 3),
            "gaze_away_ratio": round(fatigue.gaze_away_ratio, 3),
            "nod_drop_ratio": round(fatigue.nod_drop_ratio, 3),
            "is_focus_lost": fatigue.is_focus_lost,
            "focus_loss_reason": fatigue.focus_loss_reason,
            "is_yawning": fatigue.is_yawning,
            "is_drowsy": fatigue.is_drowsy,
            "yawn_count": fatigue.yawn_count,
            "nod_count": fatigue.nod_count,
            "room_light_level": state.environment.room_light_level,
            "room_light_status": state.environment.room_light_status,
            "posture_alerts": posture.alert_count,
            "fatigue_alerts": fatigue.alert_count,
        }

        with self.path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.FIELDNAMES)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
