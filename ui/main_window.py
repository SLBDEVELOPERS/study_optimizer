from pathlib import Path
import json
import sys

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow

from ui.backend_service import BackendService


class WebBridge(QObject):
    def __init__(self, service: BackendService):
        super().__init__()
        self.service = service

    @Slot(result=str)
    def initialState(self) -> str:
        return json.dumps(self.service.initial_payload())

    @Slot()
    def toggleMode(self):
        self.service.toggle_mode()

    @Slot()
    def toggleSilentMode(self):
        self.service.toggle_silent_mode()

    @Slot()
    def toggleFan(self):
        self.service.toggle_fan()

    @Slot(int)
    def setLampBrightness(self, brightness: int):
        self.service.set_lamp_brightness(brightness)

    @Slot()
    def triggerPostureAlert(self):
        self.service.trigger_posture_alert()

    @Slot()
    def triggerDrowsyAlert(self):
        self.service.trigger_drowsy_alert()

    @Slot(str)
    def saveSettings(self, payload: str):
        self.service.apply_settings(json.loads(payload))

    @Slot(str)
    def pairDevice(self, payload: str):
        self.service.pair_device(json.loads(payload))

    @Slot()
    def discoverDevice(self):
        self.service.discover_device()

    @Slot(str)
    def provisionDeviceWifi(self, payload: str):
        self.service.provision_device_wifi(json.loads(payload))

    @Slot()
    def syncDeviceSettings(self):
        self.service.sync_device_settings()

    @Slot()
    def refreshDeviceStatus(self):
        self.service.refresh_device_status()

    @Slot()
    def toggleCamera(self):
        self.service.toggle_camera()

    @Slot()
    def captureSnapshot(self):
        self.service.capture_snapshot()

    @Slot()
    def resetCalibration(self):
        self.service.reset_calibration()


class SmartStudyOptimizer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Study Optimizer")
        self.resize(1500, 960)

        self.service = BackendService()
        self.view = QWebEngineView(self)
        self.setCentralWidget(self.view)

        self.channel = QWebChannel(self.view.page())
        self.bridge = WebBridge(self.service)
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        self.service.status_updated.connect(self.view.page().runJavaScript)
        self.service.preview_updated.connect(self.view.page().runJavaScript)
        self.service.reports_updated.connect(self.view.page().runJavaScript)

        frontend = Path(__file__).resolve().parent / "web" / "index.html"
        self.view.setUrl(QUrl.fromLocalFile(str(frontend)))

    def closeEvent(self, event):
        self.service.shutdown()
        super().closeEvent(event)

    def run(self):
        self.show()
        return self


def launch_app():
    app = QApplication.instance() or QApplication(sys.argv)
    window = SmartStudyOptimizer()
    window.show()
    return app.exec()
