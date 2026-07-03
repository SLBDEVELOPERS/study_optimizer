import time

from config import logger, save_config
from device.discovery import (
    discover_esp32,
    get_current_wifi_ssid,
    get_saved_wifi_password,
    is_provisioning_ap_active,
    provision_over_ap,
)
from device.esp32_client import ESP32Communicator


def pair_device(config):
    return ESP32Communicator(config)


def auto_pair_device(config) -> dict:
    """Zero-config pairing flow, called instead of asking the user to type
    SSID/password/IP by hand.

    1. If the ESP32 is already on the network (previously provisioned),
       just find it (mDNS / subnet scan) - nothing to type.
    2. Otherwise, if it's broadcasting its "StudyGuard-Setup" hotspot,
       hand it the WiFi network this PC is already connected to, then
       find it once it joins.
    """
    pc_ssid = get_current_wifi_ssid()

    found_url = discover_esp32(cached_url=config.ESP32_HTTP_URL)
    if found_url:
        config.ESP32_HTTP_URL = found_url
        save_config(config)
        return {"ok": True, "step": "found_existing", "esp32_url": found_url, "wifi_ssid": pc_ssid}

    if not pc_ssid:
        return {"ok": False, "step": "no_wifi", "error": "PC is not connected to WiFi"}

    if not is_provisioning_ap_active():
        return {
            "ok": False,
            "step": "device_not_found",
            "error": "ESP32 not found on this network and its setup hotspot (StudyGuard-Setup) is not visible. "
                     "Power-cycle the device and try again.",
        }

    pc_password = get_saved_wifi_password(pc_ssid) or config.DEVICE_WIFI_PASSWORD
    provisioned = provision_over_ap(pc_ssid, pc_password, restore_ssid=pc_ssid)
    if not provisioned:
        return {"ok": False, "step": "provision_failed", "error": "Could not send WiFi credentials to the device"}

    config.DEVICE_WIFI_SSID = pc_ssid
    config.DEVICE_WIFI_PASSWORD = pc_password
    save_config(config)

    found_url = None
    for _ in range(10):
        found_url = discover_esp32()
        if found_url:
            break
        time.sleep(2)
    if not found_url:
        return {"ok": False, "step": "provisioned_not_found", "error": "Provisioned but couldn't locate the device on the network yet"}

    config.ESP32_HTTP_URL = found_url
    save_config(config)
    logger.info("Auto-paired ESP32 at %s on network %s", found_url, pc_ssid)
    return {"ok": True, "step": "provisioned", "esp32_url": found_url, "wifi_ssid": pc_ssid}


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
