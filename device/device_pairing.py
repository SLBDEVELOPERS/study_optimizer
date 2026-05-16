from device.esp32_client import ESP32Communicator


def pair_device(config):
    return ESP32Communicator(config)


def update_device_connection(config, mode: str, endpoint: str):
    config.ESP32_MODE = mode
    if mode == "http":
        config.ESP32_HTTP_URL = endpoint
    else:
        config.ESP32_SERIAL_PORT = endpoint


def build_device_settings_payload(config) -> dict:
    return {
        "wifi_ssid": config.DEVICE_WIFI_SSID,
        "wifi_password": config.DEVICE_WIFI_PASSWORD,
        "silent_mode": config.SILENT_MODE,
        "auto_mode": config.AUTO_MODE,
        "auto_fan": config.AUTO_FAN,
        "auto_lamp": config.AUTO_LAMP,
        "posture_sensitivity": config.POSTURE_SENSITIVITY,
        "fatigue_sensitivity": config.FATIGUE_SENSITIVITY,
    }
