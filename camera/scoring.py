def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def threshold_confidence(value: float, threshold: float) -> float:
    if threshold <= 0:
        return 0.0
    return clamp(value / threshold)


def calculate_posture_score(
    shoulder_angle: float,
    head_drop: float,
    forward_head: float,
    back_lean_angle: float,
) -> int:
    score = 100.0
    score -= clamp(shoulder_angle / 20.0) * 20.0
    score -= clamp(head_drop / 0.15) * 30.0
    score -= clamp(forward_head / 0.18) * 30.0
    score -= clamp(back_lean_angle / 25.0) * 20.0
    return int(round(clamp(score, 0.0, 100.0)))


def calculate_fatigue_score(
    perclos: float,
    yawns_recent: int,
    nods_recent: int,
    blink_rate: float,
) -> int:
    score = 0.0
    score += clamp(perclos / 0.30) * 40.0
    score += clamp(yawns_recent / 3.0) * 25.0
    score += clamp(nods_recent / 3.0) * 25.0

    if blink_rate > 0:
        if blink_rate < 8.0:
            score += clamp((8.0 - blink_rate) / 8.0) * 10.0
        elif blink_rate > 25.0:
            score += clamp((blink_rate - 25.0) / 20.0) * 10.0

    return int(round(clamp(score, 0.0, 100.0)))
