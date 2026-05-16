from datetime import datetime


def format_session_report(summary: dict) -> str:
    return "\n".join(
        [
            "",
            "=" * 40,
            f"  Date     : {summary['date']}",
            f"  Duration : {summary['duration_minutes']} minutes",
            f"  Posture  : {summary['posture_alerts']} alerts",
            f"  Fatigue  : {summary['fatigue_alerts']} alerts",
            f"  Blinks   : {summary['blink_count']}",
            "=" * 40,
        ]
    )


def report_row(summary: dict) -> list[str]:
    return [
        datetime.fromtimestamp(summary.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M"),
        f"{summary.get('duration_minutes', 0)} min",
        str(summary.get("posture_alerts", 0)),
        str(summary.get("fatigue_alerts", 0)),
        str(summary.get("blink_count", 0)),
        summary.get("room_light_status", "Unknown"),
    ]


def daily_report_lines(daily: dict) -> list[str]:
    return [
        f"Date: {daily['date']}",
        f"Sessions: {daily['session_count']}",
        f"Focus Time: {daily['duration_minutes']} min",
        f"Posture Alerts: {daily['posture_alerts']}",
        f"Fatigue Alerts: {daily['fatigue_alerts']}",
        f"Blinks: {daily['blink_count']}",
    ]
