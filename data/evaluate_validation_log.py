import argparse
import csv
from pathlib import Path


def _as_bool(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "bad", "drowsy"}


def _as_float(row, key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except ValueError:
        return default


def _classification_report(rows: list[dict], pred_key: str, label_key: str) -> dict | None:
    if not rows or label_key not in rows[0]:
        return None

    tp = fp = tn = fn = 0
    for row in rows:
        pred = _as_bool(row.get(pred_key))
        label = _as_bool(row.get(label_key))
        if pred and label:
            tp += 1
        elif pred and not label:
            fp += 1
        elif not pred and label:
            fn += 1
        else:
            tn += 1

    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    accuracy = (tp + tn) / max(1, tp + tn + fp + fn)
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "accuracy": round(accuracy, 3),
    }


def evaluate(path: Path) -> dict:
    with path.open("r", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    if not rows:
        return {"frames": 0}

    posture_bad = [
        row for row in rows
        if _as_bool(row.get("is_slouching")) or _as_bool(row.get("is_forward_head")) or _as_bool(row.get("is_too_close"))
    ]
    fatigue_bad = [row for row in rows if _as_bool(row.get("is_drowsy"))]
    focus_lost = [row for row in rows if _as_bool(row.get("is_focus_lost"))]

    summary = {
        "frames": len(rows),
        "duration_s": int(_as_float(rows[-1], "session_elapsed_s")),
        "posture_bad_pct": round(len(posture_bad) / len(rows) * 100.0, 1),
        "fatigue_bad_pct": round(len(fatigue_bad) / len(rows) * 100.0, 1),
        "focus_lost_pct": round(len(focus_lost) / len(rows) * 100.0, 1),
        "avg_posture_score": round(sum(_as_float(r, "posture_score") for r in rows) / len(rows), 1),
        "avg_fatigue_score": round(sum(_as_float(r, "fatigue_score") for r in rows) / len(rows), 1),
        "max_perclos": round(max(_as_float(r, "perclos") for r in rows), 3),
        "max_mar": round(max(_as_float(r, "mar") for r in rows), 3),
        "max_gaze_away_ratio": round(max(_as_float(r, "gaze_away_ratio") for r in rows), 3),
        "calibration_rejected_frames": int(max(_as_float(r, "calibration_rejected_frames") for r in rows)),
    }

    posture_report = _classification_report(rows, "is_slouching", "manual_posture_bad")
    fatigue_report = _classification_report(rows, "is_drowsy", "manual_fatigue_bad")
    if posture_report:
        summary["manual_posture_report"] = posture_report
    if fatigue_report:
        summary["manual_fatigue_report"] = fatigue_report
    return summary


def main():
    parser = argparse.ArgumentParser(description="Summarize Smart Study Optimizer validation logs.")
    parser.add_argument("path", nargs="?", default="data/validation_log.csv")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"Validation log not found: {path}")

    summary = evaluate(path)
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
