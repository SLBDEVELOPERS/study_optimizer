import requests

from device.esp32_client import ESP32Communicator, resolve_device_url

# The device's own captive-portal network, live when it has no saved WiFi
# credentials (first boot, or after a /wifi/reset).
PROVISIONING_AP_URL = "http://192.168.4.1"


def pair_device(config):
    return ESP32Communicator(config)


def update_device_connection(config, mode: str, endpoint: str):
    config.ESP32_MODE = mode
    if mode == "http":
        config.ESP32_HTTP_URL = endpoint
        config.ESP32_LAST_KNOWN_IP = endpoint
    else:
        config.ESP32_SERIAL_PORT = endpoint


def build_device_settings_payload(config) -> dict:
    return {
        "silent_mode": config.SILENT_MODE,
        "auto_mode": config.AUTO_MODE,
        "auto_fan": config.AUTO_FAN,
        "auto_lamp": config.AUTO_LAMP,
        "posture_sensitivity": config.POSTURE_SENSITIVITY,
        "fatigue_sensitivity": config.FATIGUE_SENSITIVITY,
    }


def provision_wifi(ssid: str, password: str, timeout: float = 5.0) -> bool:
    """Send WiFi credentials to a device that's currently broadcasting its
    StudyGuard-Setup captive-portal AP (first-time setup or post-reset)."""
    try:
        response = requests.post(
            f"{PROVISIONING_AP_URL}/provision",
            data={"ssid": ssid, "password": password},
            timeout=timeout,
        )
        return response.status_code == 200
    except Exception:
        return False


def auto_discover_endpoint() -> str | None:
    """Find the paired device on the current network via mDNS, without the
    user typing an IP address."""
    return resolve_device_url()
