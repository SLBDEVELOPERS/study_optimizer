# Smart Study Optimizer

Smart Study Optimizer is a Python desktop application that monitors posture and eye fatigue in real time using a webcam. It uses OpenCV and the MediaPipe Tasks API to detect slouching, forward-head posture, face distance, blink patterns, and possible drowsiness. Alerts can be sent to an ESP32 over HTTP or serial, and the app also works in simulation mode when the ESP32 is unavailable.

This project provides study/work posture and fatigue warnings only. It is not a medical device and must not be used to diagnose sleep disorders, eye disease, spine problems, or any other health condition.

## Features

- Real-time posture monitoring
- Forward-head and slouch detection
- Back-lean detection using shoulder/hip landmarks when visible
- Per-user posture and eye calibration
- Posture and fatigue scores
- Per-feature confidence values
- Face-too-close detection
- Blink counting and blink-rate tracking
- PERCLOS-style eye-closure tracking
- Yawn, nod, focus-loss, and possible drowsiness warnings
- Validation CSV logging for real-user threshold tuning
- On-screen HUD with session stats and alert counters
- ESP32 buzzer alerts through HTTP or serial
- Snapshot capture during runtime

## Requirements

- Python 3.9, 3.10, 3.11, or 3.12
- Webcam
- Internet connection on first run if model files are not already present
- Optional: ESP32 for external buzzer alerts

## Installation

1. Clone or download this project.
2. Create and activate a virtual environment.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

## Model Files

The app expects these MediaPipe model files:

- `pose_landmarker_full.task`
- `face_landmarker.task`

If they are missing, the application attempts to download them automatically on startup.

Manual download links:

- `https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task`
- `https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task`

## Run

```bash
python main.py
```

When the application starts, it will:

- open the default webcam
- analyze posture and face landmarks frame by frame
- show the live dashboard window
- trigger alerts when posture or fatigue rules are violated long enough

## Controls

- `Q` or `Esc`: quit the application
- `S`: save a snapshot image

## ESP32 Configuration

Configuration is currently defined inside [main.py](/d:/python%20projects/study_optimizer/main.py).

Important settings in the `Config` dataclass:

- `ESP32_MODE`: `"http"` or `"serial"`
- `ESP32_SERIAL_PORT`: serial device path
- `ESP32_BAUD_RATE`: serial baud rate
- `ESP32_HTTP_URL`: base URL of the ESP32 HTTP server

Default behavior:

- If ESP32 is reachable, commands are sent to it.
- If not reachable, the app continues in simulation mode without crashing.

Expected HTTP endpoints:

- `GET /ping`
- `POST /command`

Example payload:

```json
{
  "action": "buzzer",
  "pattern": "posture",
  "duration_ms": 500,
  "repeats": 3
}
```

## Detection Logic Summary

Posture checks:

- shoulder tilt angle
- forward head ratio
- head drop ratio
- shoulder/hip torso lean when hips are visible
- face size ratio for distance
- temporal confirmation before warning flags are set

Fatigue checks:

- eye aspect ratio (EAR)
- PERCLOS
- blink count over time
- blink-rate thresholds
- mouth aspect ratio (MAR) for yawning
- head-drop/nod events
- gaze-away or missing-face focus loss
- temporal confirmation before warning flags are set

Alerts are throttled using cooldown values in the `Config` dataclass.

## Validation Logs

When enabled, the app writes raw metrics to `data/validation_log.csv`. Use this file to tune thresholds with real users instead of guessing from live output.

Run:

```bash
python data/evaluate_validation_log.py
```

If you add manual label columns named `manual_posture_bad` and `manual_fatigue_bad`, the evaluator also prints basic precision, recall, and accuracy.

## Project Structure

```text
study_optimizer/
|-- main.py
|-- config.py
|-- camera/
|   |-- posture_analyzer.py
|   `-- face_analyzer.py
|-- device/
|   |-- esp32_client.py
|   `-- device_pairing.py
|-- ui/
|   |-- main_window.py
|   `-- dashboard.py
|-- data/
|   |-- session_logger.py
|   `-- reports.py
|-- models/
|   |-- pose_landmarker_full.task
|   `-- face_landmarker.task
|-- assets/
|   `-- logo.png
|-- requirements.txt
|-- note.txt
`-- README.md
```

## Notes

- The app uses the newer `mediapipe.tasks` API instead of deprecated `mp.solutions.*`.
- The current configuration values are hardcoded in `main.py`.
- On Windows, you may need to confirm camera permissions if the webcam does not open.

## Future Improvements

- Move configuration to a separate `.env` or JSON file
- Add alert sounds on the PC itself
- Save session logs to disk
- Add calibration for different users and camera positions
- Add a dedicated ESP32 firmware example
