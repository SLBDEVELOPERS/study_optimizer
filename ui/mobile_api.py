from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import urlparse


class MobileApiServer:
    def __init__(self, service, host: str = "0.0.0.0", port: int = 8765):
        self.service = service
        self.host = host
        self.port = port
        self._server = None
        self._thread = None

    def start(self):
        if self._server:
            return

        service = self.service

        class Handler(BaseHTTPRequestHandler):
            def do_OPTIONS(self):
                self._send_empty()

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/health":
                    self._send_json({"ok": True, "service": "Smart Study Optimizer"})
                elif path == "/status":
                    self._send_json(service.metrics())
                elif path == "/settings":
                    self._send_json(service.settings_payload())
                elif path == "/reports":
                    self._send_json(service.reports_payload())
                elif path == "/initial":
                    self._send_json(service.initial_payload())
                else:
                    self._send_json({"error": "Not found"}, status=404)

            def do_POST(self):
                path = urlparse(self.path).path
                payload = self._read_json()

                actions = {
                    "/actions/toggle-mode": service.toggle_mode,
                    "/actions/toggle-silent": service.toggle_silent_mode,
                    "/actions/toggle-fan": service.toggle_fan,
                    "/actions/posture-alert": service.trigger_posture_alert,
                    "/actions/drowsy-alert": service.trigger_drowsy_alert,
                    "/actions/toggle-camera": service.toggle_camera,
                    "/actions/snapshot": service.capture_snapshot,
                    "/actions/sync-device": service.sync_device_settings,
                    "/actions/refresh-device": service.refresh_device_status,
                    "/actions/recalibrate": service.reset_calibration,
                }

                if path in actions:
                    actions[path]()
                    self._send_json({"ok": True, "status": service.metrics()})
                    return

                if path == "/actions/lamp":
                    service.set_lamp_brightness(int(payload.get("brightness", 0)))
                    self._send_json({"ok": True, "status": service.metrics()})
                    return

                if path == "/settings":
                    service.apply_settings(payload)
                    self._send_json({"ok": True, "settings": service.settings_payload()})
                    return

                if path == "/device/pair":
                    service.pair_device(payload)
                    self._send_json({"ok": True, "status": service.metrics()})
                    return

                self._send_json({"error": "Not found"}, status=404)

            def _read_json(self):
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    return {}
                try:
                    return json.loads(self.rfile.read(length).decode("utf-8"))
                except json.JSONDecodeError:
                    return {}

            def _send_empty(self):
                self.send_response(204)
                self._headers()
                self.end_headers()

            def _send_json(self, payload, status=200):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self._headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def log_message(self, format, *args):
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._thread = None
