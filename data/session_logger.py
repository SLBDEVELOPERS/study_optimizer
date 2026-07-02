import json
import time
from datetime import datetime
from pathlib import Path


class SessionLogger:
    def __init__(self, report_file: str):
        self.report_path = Path(report_file)
        self.report_path.parent.mkdir(parents=True, exist_ok=True)

    def build_summary(self, state) -> dict:
        elapsed_seconds = int(time.time() - state.session_start)
        elapsed_minutes = max(1, elapsed_seconds // 60)

        posture_good_pct = round(
            state.good_posture_frames / max(1, state.total_frames) * 100, 1
        )

        blink_rate = state.fatigue.blink_rate
        blink_score = max(0.0, min(100.0, (1.0 - abs(blink_rate - 15.0) / 20.0) * 100.0)) if blink_rate > 0 else 50.0

        alerts_per_min = (state.posture.alert_count + state.fatigue.alert_count) / elapsed_minutes
        alert_score = max(0.0, min(100.0, 100.0 - alerts_per_min * 15.0))
        fatigue_health_score = max(0.0, min(100.0, 100.0 - state.fatigue.fatigue_score))

        quality_score = int(
            posture_good_pct * 0.4
            + blink_score * 0.2
            + alert_score * 0.2
            + fatigue_health_score * 0.2
        )

        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "timestamp": int(time.time()),
            "duration_minutes": elapsed_minutes,
            "duration_seconds": elapsed_seconds,
            "posture_alerts": state.posture.alert_count,
            "fatigue_alerts": state.fatigue.alert_count,
            "blink_count": state.fatigue.blink_count,
            "nod_count": state.fatigue.nod_count,
            "room_light_status": state.environment.room_light_status,
            "temperature_c": state.environment.temperature_c,
            "fan_on": state.device.fan_on,
            "lamp_brightness": state.device.lamp_brightness,
            "auto_mode": state.device.auto_mode,
            "silent_mode": state.device.silent_mode,
            "posture_good_pct": posture_good_pct,
            "posture_score": state.posture.posture_score,
            "fatigue_score": state.fatigue.fatigue_score,
            "perclos": round(state.fatigue.perclos, 3),
            "quality_score": quality_score,
        }

    def append_summary(self, summary: dict):
        history = self.load_history()
        history.append(summary)
        self.report_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

    def load_history(self) -> list[dict]:
        if not self.report_path.exists():
            return []
        try:
            return json.loads(self.report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def load_daily_summary(self, target_date: str | None = None) -> dict:
        target = target_date or datetime.now().strftime("%Y-%m-%d")
        sessions = [item for item in self.load_history() if item.get("date") == target]
        return {
            "date": target,
            "session_count": len(sessions),
            "duration_minutes": sum(item.get("duration_minutes", 0) for item in sessions),
            "posture_alerts": sum(item.get("posture_alerts", 0) for item in sessions),
            "fatigue_alerts": sum(item.get("fatigue_alerts", 0) for item in sessions),
            "blink_count": sum(item.get("blink_count", 0) for item in sessions),
        }
