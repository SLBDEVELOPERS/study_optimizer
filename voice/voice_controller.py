import json
import logging
import os
import queue
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal

import sounddevice as sd
from vosk import Model, KaldiRecognizer

logger = logging.getLogger(__name__)

VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
MODEL_DIR = Path("models")
VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOSK_MODEL_PATH = MODEL_DIR / VOSK_MODEL_NAME

def ensure_vosk_model():
    if VOSK_MODEL_PATH.exists():
        return
    logger.info("Downloading Vosk model (this may take a minute)...")
    MODEL_DIR.mkdir(exist_ok=True)
    zip_path = MODEL_DIR / "vosk_model.zip"
    
    try:
        urllib.request.urlretrieve(VOSK_MODEL_URL, zip_path)
        logger.info("Extracting Vosk model...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(MODEL_DIR)
        logger.info("Vosk model ready.")
    except Exception as e:
        logger.error(f"Failed to download Vosk model: {e}")
    finally:
        if zip_path.exists():
            zip_path.unlink()


class VoiceListenerThread(QThread):
    command_recognized = Signal(str)
    listening_started = Signal()
    listening_stopped = Signal()
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._q = queue.Queue()

    def _audio_callback(self, indata, frames, time, status):
        if status:
            logger.warning(f"Audio status: {status}")
        self._q.put(bytes(indata))

    def stop(self):
        self._running = False
        self.quit()
        self.wait()

    def run(self):
        ensure_vosk_model()
        
        if not VOSK_MODEL_PATH.exists():
            self.error_occurred.emit("Vosk model not found")
            return

        try:
            model = Model(str(VOSK_MODEL_PATH))
            recognizer = KaldiRecognizer(model, 16000)
        except Exception as e:
            self.error_occurred.emit(f"Failed to load Vosk model: {e}")
            return

        self._running = True
        self.listening_started.emit()

        try:
            with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype='int16',
                                   channels=1, callback=self._audio_callback):
                while self._running:
                    try:
                        data = self._q.get(timeout=0.5)
                        if recognizer.AcceptWaveform(data):
                            result = json.loads(recognizer.Result())
                            text = result.get("text", "")
                            if text:
                                logger.info(f"Recognized: {text}")
                                self.command_recognized.emit(text.lower())
                    except queue.Empty:
                        pass
        except Exception as e:
            logger.error(f"Audio stream error: {e}")
            self.error_occurred.emit(str(e))
        finally:
            self._running = False
            self.listening_stopped.emit()
