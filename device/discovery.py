"""Zero-config ESP32 discovery and provisioning.

Replaces manual SSID/password/IP entry with:
  1. Reading the WiFi network the PC is already connected to (Windows).
  2. Provisioning a fresh ESP32 (still in its "StudyGuard-Setup" setup
     hotspot) with those same credentials, so it joins the PC's network.
  3. Finding the ESP32's address afterwards via mDNS (http://studyguard.local)
     or, if that fails, a quick scan of the local subnet — no static IP
     needs to be typed into config.json.
"""

import ipaddress
import re
import socket
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from config import logger

MDNS_HOSTNAME = "studyguard.local"
PROVISION_AP_SSID = "StudyGuard-Setup"
PROVISION_AP_URL = "http://192.168.4.1"
HTTP_TIMEOUT = 2.0
SCAN_TIMEOUT = 0.3
SCAN_WORKERS = 64


def _run_netsh(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["netsh", *args],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return result.stdout
    except Exception as exc:
        logger.warning("netsh command failed (%s): %s", args, exc)
        return ""


def get_current_wifi_ssid() -> str | None:
    """SSID of the WiFi network this PC is currently connected to."""
    output = _run_netsh(["wlan", "show", "interfaces"])
    for line in output.splitlines():
        match = re.match(r"\s*SSID\s*:\s*(.+)", line)
        if match and not line.strip().startswith("BSSID"):
            ssid = match.group(1).strip()
            if ssid:
                return ssid
    return None


def get_saved_wifi_password(ssid: str) -> str | None:
    """Password Windows already has saved for this SSID, if any."""
    output = _run_netsh(["wlan", "show", "profile", f"name={ssid}", "key=clear"])
    for line in output.splitlines():
        match = re.match(r"\s*Key Content\s*:\s*(.+)", line)
        if match:
            return match.group(1).strip()
    return None


def ping_device(base_url: str, timeout: float = HTTP_TIMEOUT) -> bool:
    try:
        response = requests.get(f"{base_url.rstrip('/')}/ping", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def discover_via_mdns(timeout: float = HTTP_TIMEOUT) -> str | None:
    """Try the ESP32's mDNS hostname; works without knowing any IP."""
    url = f"http://{MDNS_HOSTNAME}"
    if ping_device(url, timeout=timeout):
        return url
    return None


def _local_ipv4() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return None
    finally:
        sock.close()


def scan_subnet_for_device() -> str | None:
    """Last-resort discovery: probe every host on the PC's /24 for /ping."""
    local_ip = _local_ipv4()
    if not local_ip:
        return None

    network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    candidates = [str(ip) for ip in network.hosts() if str(ip) != local_ip]

    def check(ip: str) -> str | None:
        url = f"http://{ip}"
        return url if ping_device(url, timeout=SCAN_TIMEOUT) else None

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        futures = {pool.submit(check, ip): ip for ip in candidates}
        for future in as_completed(futures):
            found = future.result()
            if found:
                for pending in futures:
                    pending.cancel()
                return found
    return None


def discover_esp32(cached_url: str | None = None) -> str | None:
    """Find the ESP32's base URL with no manual IP entry.

    Order: cached URL from a previous run (fast path) -> mDNS -> subnet scan.
    """
    if cached_url and ping_device(cached_url):
        return cached_url.rstrip("/")

    found = discover_via_mdns()
    if found:
        return found

    logger.info("mDNS discovery failed, scanning local subnet for ESP32...")
    found = scan_subnet_for_device()
    if found:
        return found

    return None


def is_provisioning_ap_active() -> bool:
    return ping_device(PROVISION_AP_URL, timeout=1.5)


_OPEN_PROFILE_XML = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig>
        <SSID>
            <name>{ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>manual</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>open</authentication>
                <encryption>none</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
        </security>
    </MSM>
</WLANProfile>
"""


def _add_open_profile(ssid: str) -> bool:
    """Register a temporary open-network WLAN profile so `netsh wlan connect`
    is able to join the ESP32's unsecured setup hotspot."""
    xml_path = Path(tempfile.gettempdir()) / f"{ssid}.xml"
    xml_path.write_text(_OPEN_PROFILE_XML.format(ssid=ssid), encoding="utf-8")
    try:
        output = _run_netsh(["wlan", "add", "profile", f"filename={xml_path}", "user=current"])
        return "added" in output.lower() or "updated" in output.lower()
    finally:
        xml_path.unlink(missing_ok=True)


def provision_over_ap(ssid: str, password: str, restore_ssid: str | None = None) -> bool:
    """One-time pairing: join the ESP32's setup hotspot, hand it the PC's
    WiFi credentials, then reconnect the PC to its original network.

    Requires the PC to briefly switch WiFi networks, so this is only
    called from an explicit "pair device" action, never silently.
    """
    original_ssid = restore_ssid or get_current_wifi_ssid()

    _add_open_profile(PROVISION_AP_SSID)
    _run_netsh(["wlan", "connect", f"name={PROVISION_AP_SSID}", f"ssid={PROVISION_AP_SSID}"])
    time.sleep(3)

    ok = False
    try:
        for _ in range(5):
            if ping_device(PROVISION_AP_URL, timeout=2):
                break
            time.sleep(1)
        else:
            logger.warning("Could not reach ESP32 setup hotspot (%s)", PROVISION_AP_SSID)
            return False

        try:
            response = requests.post(
                f"{PROVISION_AP_URL}/provision",
                json={"ssid": ssid, "password": password},
                timeout=5,
            )
            ok = response.status_code == 200
        except Exception as exc:
            logger.warning("Provisioning request failed: %s", exc)
            ok = False
    finally:
        _run_netsh(["wlan", "delete", "profile", f"name={PROVISION_AP_SSID}"])
        if original_ssid:
            time.sleep(2)
            _run_netsh(["wlan", "connect", f"name={original_ssid}", f"ssid={original_ssid}"])

    return ok
