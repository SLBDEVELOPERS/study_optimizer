import json
import threading

import requests
import serial
from zeroconf import Zeroconf

from config import logger, save_config

MDNS_SERVICE_TYPE = "_http._tcp.local."
MDNS_INSTANCE_NAME = "studyguard._http._tcp.local."


def resolve_device_url(timeout: float = 3.0) -> str | None:
    """Look up the ESP32 device via mDNS (studyguard.local) instead of a hand-typed IP."""
    zc = Zeroconf()
    try:
        info = zc.get_service_info(MDNS_SERVICE_TYPE, MDNS_INSTANCE_NAME, timeout=int(timeout * 1000))
        if info:
            addresses = info.parsed_addresses()
            if addresses:
                return f"http://{addresses[0]}"
    except Exception as exc:
        logger.warning("mDNS resolution failed: %s", exc)
    finally:
        zc.close()
    return None


class ESP32Communicator:
    def __init__(self, config):
        self.config = config
        self.serial_conn = None
        self.connected = False
        self._lock = threading.Lock()
        self._connect()

    def _base_url(self) -> str:
        return self.config.ESP32_HTTP_URL.rstrip("/")

    def _connect(self):
        if self.config.ESP32_MODE == "serial":
            try:
                self.serial_conn = serial.Serial(
                    self.config.ESP32_SERIAL_PORT,
                    self.config.ESP32_BAUD_RATE,
                    timeout=1,
                )
                self.connected = True
                logger.info("ESP32 Serial connected")
            except Exception as exc:
                logger.warning("ESP32 Serial unavailable: %s -> simulation mode", exc)
                self.connected = False
        else:
            self.connected = self.ping()
            if not self.connected:
                self._rediscover()
            logger.info("ESP32 HTTP %s", "connected" if self.connected else "simulation mode")

    def _rediscover(self):
        """Cached IP failed — try mDNS to find the device on its (possibly new) network."""
        url = resolve_device_url()
        if not url:
            return
        self.config.ESP32_HTTP_URL = url
        self.config.ESP32_LAST_KNOWN_IP = url
        save_config(self.config)
        self.connected = self.ping()

    def _serial_send(self, payload: dict) -> bool:
        if not self.serial_conn:
            return False
        self.serial_conn.write((json.dumps(payload) + "\n").encode())
        return True

    def send(self, command: dict) -> bool:
        with self._lock:
            try:
                if self.config.ESP32_MODE == "serial":
                    return self._serial_send(command)
                response = requests.post(
                    f"{self._base_url()}/command",
                    json=command,
                    timeout=2,
                )
                return response.status_code == 200
            except Exception:
                return False

    def get_status(self) -> dict:
        if self.config.ESP32_MODE == "serial":
            return {"connected": self.serial_conn is not None, "transport": "serial"}
        try:
            response = requests.get(f"{self._base_url()}/status", timeout=2)
            if response.status_code == 200:
                data = response.json()
                data["connected"] = True
                return data
        except Exception:
            pass
        return {"connected": False}

    def push_settings(self, payload: dict) -> bool:
        with self._lock:
            try:
                if self.config.ESP32_MODE == "serial":
                    return self._serial_send({"action": "settings", "payload": payload})
                response = requests.post(f"{self._base_url()}/settings", json=payload, timeout=3)
                return response.status_code == 200
            except Exception:
                return False

    def posture_buzz(self):
        sent = self.send({"action": "buzzer", "pattern": "posture", "duration_ms": 500, "repeats": 3})
        logger.info("[ESP32] Posture buzz -> %s", "sent" if sent else "simulated")
        return sent

    def drowsy_buzz(self):
        sent = self.send({"action": "buzzer", "pattern": "drowsy", "duration_ms": 1000, "repeats": 2})
        logger.info("[ESP32] Drowsy buzz -> %s", "sent" if sent else "simulated")
        return sent

    def set_fan(self, enabled: bool) -> bool:
        sent = self.send({"action": "fan", "state": "on" if enabled else "off"})
        logger.info("[ESP32] Fan -> %s", "sent" if sent else "simulated")
        return sent

    def set_lamp_brightness(self, brightness: int) -> bool:
        sent = self.send({"action": "lamp", "brightness": brightness})
        logger.info("[ESP32] Lamp brightness -> %s", "sent" if sent else "simulated")
        return sent

    def ping(self) -> bool:
        if self.config.ESP32_MODE == "serial":
            return self.serial_conn is not None
        try:
            response = requests.get(f"{self._base_url()}/ping", timeout=2)
            return response.status_code == 200
        except Exception:
            return False

    def close(self):
        if self.serial_conn:
            self.serial_conn.close()
