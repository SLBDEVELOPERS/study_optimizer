from datetime import datetime
from pathlib import Path
import os
import sys
import time

import cv2
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from camera.face_analyzer import FaceEyeAnalyzer
from camera.posture_analyzer import PostureAnalyzer
from config import CONFIG, SystemState, ensure_models, logger, save_config
from data.reports import daily_report_lines, format_session_report, report_row
from data.session_logger import SessionLogger
from device.device_pairing import build_device_settings_payload, pair_device, update_device_connection
from ui.dashboard import DashboardPage


def session_clock(started_at: float) -> str:
    elapsed = int(time.time() - started_at)
    return f"{elapsed // 3600:02d}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}"


def set_windows_startup(enabled: bool):
    if os.name != "nt":
        return
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "SmartStudyOptimizer"
        python_bin = Path(sys.executable)
        launcher = python_bin.with_name("pythonw.exe") if python_bin.name.lower() == "python.exe" else python_bin
        command = f'"{launcher}" "{Path(__file__).resolve().parents[1] / "main.py"}"'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
    except Exception as exc:
        logger.warning("Startup auto-launch update failed: %s", exc)


class AlertManager:
    def __init__(self, config, esp32):
        self.config = config
        self.esp32 = esp32

    def check(self, state):
        now = time.time()
        posture = state.posture
        fatigue = state.fatigue

        bad_posture = posture.is_slouching or posture.is_forward_head or posture.is_too_close
        if bad_posture:
            if posture.bad_posture_start == 0:
                posture.bad_posture_start = now
            elif (
                now - posture.bad_posture_start >= self.config.SLOUCH_CONFIRM_SECONDS
                and now - posture.last_alert_time >= self.config.POSTURE_ALERT_COOLDOWN
            ):
                posture.last_alert_time = now
                posture.alert_count += 1
                state.total_alerts += 1
                if self.config.SILENT_MODE:
                    state.device.last_command_status = "Posture alert recorded in silent mode"
                else:
                    sent = self.esp32.posture_buzz()
                    state.device.last_command_status = "Posture alert sent" if sent else "Posture alert simulated"
        else:
            posture.bad_posture_start = 0

        if not self.config.FATIGUE_ALERT_ENABLED:
            return

        if (
            fatigue.is_drowsy
            and fatigue.drowsy_start > 0
            and now - fatigue.drowsy_start >= self.config.DROWSY_CONFIRM_SECONDS
            and now - fatigue.last_alert_time >= self.config.DROWSY_ALERT_COOLDOWN
        ):
            fatigue.last_alert_time = now
            fatigue.alert_count += 1
            state.total_alerts += 1
            if self.config.SILENT_MODE:
                state.device.last_command_status = "Fatigue alert recorded in silent mode"
            else:
                sent = self.esp32.drowsy_buzz()
                state.device.last_command_status = "Drowsy alert sent" if sent else "Drowsy alert simulated"


class SectionFrame(QFrame):
    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.setStyleSheet("QFrame {background: white; border: 1px solid #D9E0EA; border-radius: 18px;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 20px; font-weight: 800; color: #142033;")
        layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setStyleSheet("font-size: 13px; color: #6D7787;")
            layout.addWidget(subtitle_label)
        self.body = layout


class CameraPreviewPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        preview_frame = SectionFrame("Camera Preview", "Live processed feed with posture and face overlays")
        self.preview_label = QLabel("Camera starting...")
        self.preview_label.setMinimumSize(960, 540)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("background: #0C1220; color: #9AA7B8; border-radius: 14px;")
        preview_frame.body.addWidget(self.preview_label)

        stats = QGridLayout()
        stats.setHorizontalSpacing(14)
        stats.setVerticalSpacing(14)
        self.posture_label = QLabel("Posture: Good")
        self.fatigue_label = QLabel("Fatigue: Alert")
        self.light_label = QLabel("Room Light: Good")
        self.temp_label = QLabel("Temperature: 30.0 C")
        self.fps_label = QLabel("FPS: 0")
        self.command_label = QLabel("Device: Idle")
        for index, widget in enumerate(
            [
                self.posture_label,
                self.fatigue_label,
                self.light_label,
                self.temp_label,
                self.fps_label,
                self.command_label,
            ]
        ):
            widget.setStyleSheet("font-size: 14px; font-weight: 600; color: #334155;")
            stats.addWidget(widget, index // 3, index % 3)
        preview_frame.body.addLayout(stats)
        root.addWidget(preview_frame)

    def set_frame(self, image: QImage):
        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)

    def update_metrics(self, metrics: dict):
        self.posture_label.setText(f"Posture: {metrics['posture_status']}")
        self.fatigue_label.setText(f"Fatigue: {metrics['fatigue_status']}")
        self.light_label.setText(f"Room Light: {metrics['room_light']} ({metrics['room_light_level']}%)")
        self.temp_label.setText(f"Temperature: {metrics['temperature_c']:.1f} C")
        self.fps_label.setText(f"FPS: {metrics['fps']:.0f}")
        self.command_label.setText(f"Device: {metrics['last_command_status']}")


class DevicePairingPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        connection = SectionFrame("Device Wi-Fi Pairing", "Laptop app communicates with ESP32 over JSON HTTP endpoints")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(12)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["http", "serial"])
        self.endpoint_input = QLineEdit()
        self.endpoint_input.setPlaceholderText("http://192.168.1.100 or COM3")
        self.wifi_ssid_input = QLineEdit()
        self.wifi_ssid_input.setPlaceholderText("Device Wi-Fi SSID")
        self.wifi_password_input = QLineEdit()
        self.wifi_password_input.setPlaceholderText("Device Wi-Fi Password")
        self.device_name_label = QLabel("ESP32 Desk Node")
        self.status_label = QLabel("Status: Simulation")
        self.http_info = QLabel("HTTP API: GET /ping, POST /command, GET /status, POST /settings")
        self.http_info.setStyleSheet("font-size: 12px; color: #6D7787;")
        self.pair_button = QPushButton("Pair Device")
        self.sync_button = QPushButton("Sync Settings")
        self.refresh_button = QPushButton("Refresh Status")

        form.addWidget(QLabel("Mode"), 0, 0)
        form.addWidget(self.mode_combo, 0, 1)
        form.addWidget(QLabel("Endpoint"), 1, 0)
        form.addWidget(self.endpoint_input, 1, 1)
        form.addWidget(QLabel("Wi-Fi SSID"), 2, 0)
        form.addWidget(self.wifi_ssid_input, 2, 1)
        form.addWidget(QLabel("Wi-Fi Password"), 3, 0)
        form.addWidget(self.wifi_password_input, 3, 1)
        form.addWidget(QLabel("Device Name"), 4, 0)
        form.addWidget(self.device_name_label, 4, 1)
        form.addWidget(QLabel("Connection"), 5, 0)
        form.addWidget(self.status_label, 5, 1)
        connection.body.addLayout(form)
        connection.body.addWidget(self.http_info)

        actions = QHBoxLayout()
        actions.addWidget(self.pair_button)
        actions.addWidget(self.sync_button)
        actions.addWidget(self.refresh_button)
        actions.addStretch()
        connection.body.addLayout(actions)
        root.addWidget(connection)

    def update_status(self, connected: bool, mode: str, endpoint: str, device_name: str, wifi_ssid: str):
        self.mode_combo.setCurrentText(mode)
        if not self.endpoint_input.hasFocus():
            self.endpoint_input.setText(endpoint)
        if not self.wifi_ssid_input.hasFocus():
            self.wifi_ssid_input.setText(wifi_ssid)
        self.device_name_label.setText(device_name)
        self.status_label.setText("Status: Connected" if connected else "Status: Simulation")


class SettingsPage(QWidget):
    def __init__(self, config):
        super().__init__()
        self.config = config
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        frame = SectionFrame("Settings", "Persistent product settings saved to config.json")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        self.camera_index = QSpinBox()
        self.camera_index.setRange(0, 5)
        self.posture_sensitivity = QSpinBox()
        self.posture_sensitivity.setRange(5, 45)
        self.fatigue_sensitivity = QSpinBox()
        self.fatigue_sensitivity.setRange(15, 35)
        self.break_minutes = QSpinBox()
        self.break_minutes.setRange(5, 180)
        self.temperature_spin = QSpinBox()
        self.temperature_spin.setRange(18, 40)
        self.temperature_spin.setSuffix(" C")

        self.auto_mode = QCheckBox("Auto Mode")
        self.silent_mode = QCheckBox("Silent Mode")
        self.fatigue_alert = QCheckBox("Fatigue Alerts Enabled")
        self.auto_fan = QCheckBox("Auto Fan")
        self.auto_lamp = QCheckBox("Auto Lamp")
        self.startup_auto_launch = QCheckBox("Startup Auto Launch")
        self.minimize_to_tray = QCheckBox("Minimize To System Tray")

        grid.addWidget(QLabel("Camera Index"), 0, 0)
        grid.addWidget(self.camera_index, 0, 1)
        grid.addWidget(QLabel("Posture Sensitivity"), 1, 0)
        grid.addWidget(self.posture_sensitivity, 1, 1)
        grid.addWidget(QLabel("Fatigue Sensitivity"), 2, 0)
        grid.addWidget(self.fatigue_sensitivity, 2, 1)
        grid.addWidget(QLabel("Break Reminder"), 3, 0)
        grid.addWidget(self.break_minutes, 3, 1)
        grid.addWidget(QLabel("Temperature"), 4, 0)
        grid.addWidget(self.temperature_spin, 4, 1)
        grid.addWidget(self.auto_mode, 5, 0, 1, 2)
        grid.addWidget(self.silent_mode, 6, 0, 1, 2)
        grid.addWidget(self.fatigue_alert, 7, 0, 1, 2)
        grid.addWidget(self.auto_fan, 8, 0, 1, 2)
        grid.addWidget(self.auto_lamp, 9, 0, 1, 2)
        grid.addWidget(self.startup_auto_launch, 10, 0, 1, 2)
        grid.addWidget(self.minimize_to_tray, 11, 0, 1, 2)
        frame.body.addLayout(grid)

        self.apply_button = QPushButton("Save Settings")
        frame.body.addWidget(self.apply_button, alignment=Qt.AlignLeft)
        root.addWidget(frame)

    def load_from_config(self, config):
        self.camera_index.setValue(config.CAMERA_INDEX)
        self.posture_sensitivity.setValue(config.POSTURE_SENSITIVITY)
        self.fatigue_sensitivity.setValue(config.FATIGUE_SENSITIVITY)
        self.break_minutes.setValue(config.BREAK_REMINDER_MINUTES)
        self.temperature_spin.setValue(int(config.DEFAULT_TEMPERATURE_C))
        self.auto_mode.setChecked(config.AUTO_MODE)
        self.silent_mode.setChecked(config.SILENT_MODE)
        self.fatigue_alert.setChecked(config.FATIGUE_ALERT_ENABLED)
        self.auto_fan.setChecked(config.AUTO_FAN)
        self.auto_lamp.setChecked(config.AUTO_LAMP)
        self.startup_auto_launch.setChecked(config.STARTUP_AUTO_LAUNCH)
        self.minimize_to_tray.setChecked(config.MINIMIZE_TO_TRAY)


class DailyFocusReportPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        summary_frame = SectionFrame("Daily Focus Report", "Daily summary plus individual session records")
        self.summary_labels = []
        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(18)
        summary_grid.setVerticalSpacing(10)
        for index, label in enumerate(["Date", "Sessions", "Focus Time", "Posture Alerts", "Fatigue Alerts", "Blinks"]):
            title = QLabel(label)
            title.setStyleSheet("font-size: 12px; color: #6D7787; font-weight: 700;")
            value = QLabel("--")
            value.setStyleSheet("font-size: 24px; color: #142033; font-weight: 800;")
            summary_grid.addWidget(title, 0, index)
            summary_grid.addWidget(value, 1, index)
            self.summary_labels.append(value)
        summary_frame.body.addLayout(summary_grid)
        root.addWidget(summary_frame)

        table_frame = SectionFrame("Session History", "Every session is saved with key wellness metrics")
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Date", "Duration", "Posture", "Fatigue", "Blinks", "Light"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table_frame.body.addWidget(self.table)
        root.addWidget(table_frame)

    def load_reports(self, daily_summary: dict, history: list[dict]):
        for label, value in zip(self.summary_labels, daily_report_lines(daily_summary)):
            label.setText(value.split(": ", 1)[1])
        self.table.setRowCount(len(history))
        for row_index, report in enumerate(reversed(history)):
            for column, value in enumerate(report_row(report)):
                self.table.setItem(row_index, column, QTableWidgetItem(value))


class ManualControlPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        controls = SectionFrame("Manual Control", "Override fan, lamp, and buzzer actions")
        row = QHBoxLayout()
        self.mode_button = QPushButton("Switch To Manual Mode")
        self.silent_button = QPushButton("Enable Silent Mode")
        self.fan_button = QPushButton("Turn Fan OFF")
        self.posture_alert_button = QPushButton("Test Posture Alert")
        self.drowsy_alert_button = QPushButton("Test Drowsy Alert")
        row.addWidget(self.mode_button)
        row.addWidget(self.silent_button)
        row.addWidget(self.fan_button)
        row.addWidget(self.posture_alert_button)
        row.addWidget(self.drowsy_alert_button)
        row.addStretch()
        controls.body.addLayout(row)

        lamp_row = QVBoxLayout()
        lamp_title = QLabel("Lamp Brightness")
        lamp_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #334155;")
        self.lamp_slider = QSlider(Qt.Horizontal)
        self.lamp_slider.setRange(0, 100)
        self.lamp_value = QLabel("65%")
        self.mode_label = QLabel("Mode: Auto")
        self.mode_label.setStyleSheet("font-size: 14px; color: #334155;")
        lamp_row.addWidget(lamp_title)
        lamp_row.addWidget(self.lamp_slider)
        lamp_row.addWidget(self.lamp_value)
        lamp_row.addWidget(self.mode_label)
        controls.body.addLayout(lamp_row)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Manual control activity will appear here.")
        controls.body.addWidget(self.log_output)
        root.addWidget(controls)

    def append_log(self, message: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{stamp}] {message}")

    def sync_state(self, auto_mode: bool, silent_mode: bool, fan_on: bool, brightness: int):
        self.mode_button.setText("Switch To Manual Mode" if auto_mode else "Switch To Auto Mode")
        self.silent_button.setText("Disable Silent Mode" if silent_mode else "Enable Silent Mode")
        self.fan_button.setText("Turn Fan OFF" if fan_on else "Turn Fan ON")
        self.lamp_slider.blockSignals(True)
        self.lamp_slider.setValue(brightness)
        self.lamp_slider.blockSignals(False)
        self.lamp_value.setText(f"{brightness}%")
        self.mode_label.setText(f"Mode: {'Auto' if auto_mode else 'Manual'} | Silent: {'ON' if silent_mode else 'OFF'}")


class SmartStudyOptimizer(QMainWindow):
    def __init__(self, config=CONFIG):
        super().__init__()
        self.config = config
        self.setWindowTitle("Smart Study Optimizer")
        self.resize(1500, 960)
        self._closing = False
        self._reports_saved = False

        logger.info("Initializing Smart Study Optimizer desktop UI...")
        ensure_models()

        self.state = SystemState()
        self.state.environment.temperature_c = self.config.DEFAULT_TEMPERATURE_C
        self.state.device.fan_on = self.config.DEFAULT_FAN_ON
        self.state.device.lamp_brightness = self.config.DEFAULT_LAMP_BRIGHTNESS
        self.state.device.auto_mode = self.config.AUTO_MODE
        self.state.device.silent_mode = self.config.SILENT_MODE
        self.state.device.wifi_ssid = self.config.DEVICE_WIFI_SSID

        self.esp32 = pair_device(self.config)
        self.state.esp32_connected = self.esp32.connected
        self.pose_analyzer = PostureAnalyzer(self.config.POSE_MODEL, self.config)
        self.face_analyzer = FaceEyeAnalyzer(self.config.FACE_MODEL, self.config)
        self.alerts = AlertManager(self.config, self.esp32)
        self.session_logger = SessionLogger(self.config.REPORTS_FILE)

        self.cap = cv2.VideoCapture(self.config.CAMERA_INDEX)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.FRAME_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.FRAME_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, self.config.FPS)
        self.previous_tick = time.time()
        self.current_fps = 0.0

        self._build_ui()
        self._wire_events()
        self._setup_tray()
        self.apply_theme()
        self.settings_page.load_from_config(self.config)
        self.refresh_reports()
        self.refresh_all_views()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.process_frame)
        self.timer.start(max(16, int(1000 / max(self.config.FPS, 1))))

        self.break_timer = QTimer(self)
        self.break_timer.timeout.connect(self.check_break_reminder)
        self.break_timer.start(60_000)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(18)

        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(220)
        self.nav_list.setSpacing(6)
        for label in [
            "Dashboard",
            "Device Pairing",
            "Settings",
            "Daily Focus Report",
            "Camera Preview",
            "Manual Control",
        ]:
            QListWidgetItem(label, self.nav_list)
        self.nav_list.setCurrentRow(0)

        nav_shell = QFrame()
        nav_shell.setStyleSheet("QFrame {background: #132033; border-radius: 22px;}")
        nav_layout = QVBoxLayout(nav_shell)
        nav_layout.setContentsMargins(18, 20, 18, 20)
        nav_layout.setSpacing(18)
        brand = QLabel("Study Optimizer")
        brand.setStyleSheet("color: white; font-size: 24px; font-weight: 800;")
        strap = QLabel("Product console")
        strap.setStyleSheet("color: #9FB1C8; font-size: 13px;")
        nav_layout.addWidget(brand)
        nav_layout.addWidget(strap)
        nav_layout.addWidget(self.nav_list)
        nav_layout.addStretch()
        root.addWidget(nav_shell)

        content_shell = QFrame()
        content_shell.setStyleSheet("QFrame {background: #F4F7FB; border-radius: 26px;}")
        content_layout = QVBoxLayout(content_shell)
        content_layout.setContentsMargins(20, 20, 20, 20)

        self.stack = QStackedWidget()
        self.dashboard_page = DashboardPage()
        self.device_page = DevicePairingPage()
        self.settings_page = SettingsPage(self.config)
        self.reports_page = DailyFocusReportPage()
        self.preview_page = CameraPreviewPage()
        self.manual_page = ManualControlPage()
        for page in [
            self.dashboard_page,
            self.device_page,
            self.settings_page,
            self.reports_page,
            self.preview_page,
            self.manual_page,
        ]:
            self.stack.addWidget(page)

        content_layout.addWidget(self.stack)
        root.addWidget(content_shell, 1)

    def _wire_events(self):
        self.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.device_page.pair_button.clicked.connect(self.repair_device)
        self.device_page.sync_button.clicked.connect(self.sync_device_settings)
        self.device_page.refresh_button.clicked.connect(self.refresh_device_status)
        self.settings_page.apply_button.clicked.connect(self.apply_settings)
        self.manual_page.mode_button.clicked.connect(self.toggle_mode)
        self.manual_page.silent_button.clicked.connect(self.toggle_silent_mode)
        self.manual_page.fan_button.clicked.connect(self.toggle_fan)
        self.manual_page.posture_alert_button.clicked.connect(self.trigger_posture_alert)
        self.manual_page.drowsy_alert_button.clicked.connect(self.trigger_drowsy_alert)
        self.manual_page.lamp_slider.valueChanged.connect(self.set_lamp_brightness)

    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_ComputerIcon), self)
        self.tray_icon.setToolTip("Smart Study Optimizer")
        tray_menu = self.menuBar().addMenu("Hidden")
        tray_menu.menuAction().setVisible(False)
        restore_action = QAction("Restore", self)
        quit_action = QAction("Quit", self)
        restore_action.triggered.connect(self.restore_from_tray)
        quit_action.triggered.connect(self.quit_application)
        popup = tray_menu
        popup.addAction(restore_action)
        popup.addAction(quit_action)
        self.tray_icon.setContextMenu(popup)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def apply_theme(self):
        self.setStyleSheet(
            """
            QMainWindow { background: #E7EDF6; }
            QLabel { color: #142033; }
            QListWidget {
                background: transparent;
                color: #C7D2E2;
                border: none;
                outline: none;
                font-size: 15px;
                font-weight: 600;
            }
            QListWidget::item {
                padding: 14px 16px;
                border-radius: 14px;
            }
            QListWidget::item:selected {
                background: #E9F0FF;
                color: #132033;
            }
            QPushButton {
                background: #1F6FEB;
                color: white;
                border: none;
                border-radius: 12px;
                padding: 10px 16px;
                font-weight: 700;
            }
            QPushButton:hover { background: #185BCC; }
            QLineEdit, QComboBox, QSpinBox, QTextEdit, QTableWidget {
                background: white;
                border: 1px solid #D4DBE7;
                border-radius: 12px;
                padding: 8px 10px;
                font-size: 14px;
            }
            QHeaderView::section {
                background: #EEF3F9;
                color: #415066;
                font-weight: 700;
                border: none;
                padding: 8px;
            }
            """
        )

    def process_frame(self):
        if not self.cap.isOpened():
            self.preview_page.preview_label.setText("Camera unavailable")
            return

        ret, frame = self.cap.read()
        if not ret:
            self.preview_page.preview_label.setText("Camera feed lost")
            return

        frame = cv2.flip(frame, 1)
        height, width = frame.shape[:2]
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        try:
            self.state.posture, pose_landmarks = self.pose_analyzer.analyze(frame_rgb, self.state.posture, height, width)
            self.pose_analyzer.draw(frame, pose_landmarks, width, height)
        except Exception as exc:
            logger.debug("Pose error: %s", exc)

        try:
            self.state.fatigue, self.state.posture, face_landmarks = self.face_analyzer.analyze(
                frame_rgb,
                self.state.fatigue,
                self.state.posture,
                height,
                width,
            )
            self.face_analyzer.draw(frame, face_landmarks, self.state.fatigue, width, height)
        except Exception as exc:
            logger.debug("Face error: %s", exc)

        self.update_environment(frame_rgb)
        self.run_automation()
        self.alerts.check(self.state)

        now = time.time()
        self.current_fps = 1.0 / (now - self.previous_tick + 1e-6)
        self.previous_tick = now
        cv2.putText(frame, f"{self.current_fps:.0f} fps", (width - 100, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 40, 60), 2)

        image = QImage(frame.data, width, height, frame.strides[0], QImage.Format_BGR888).copy()
        self.preview_page.set_frame(image)
        self.refresh_all_views()

    def update_environment(self, frame_rgb):
        brightness = int(frame_rgb.mean() / 255 * 100)
        self.state.environment.room_light_level = brightness
        self.state.environment.room_light_status = "Low" if brightness < 42 else "Good"

    def run_automation(self):
        if not self.config.AUTO_MODE:
            return

        if self.config.AUTO_LAMP:
            target_lamp = 85 if self.state.environment.room_light_status == "Low" else 35
            if target_lamp != self.state.device.lamp_brightness:
                self.state.device.lamp_brightness = target_lamp
                self.esp32.set_lamp_brightness(target_lamp)

        if self.config.AUTO_FAN:
            target_fan = self.state.environment.temperature_c >= 30 or self.state.fatigue.is_drowsy
            if target_fan != self.state.device.fan_on:
                self.state.device.fan_on = target_fan
                self.esp32.set_fan(target_fan)

    def metrics(self) -> dict:
        posture_bad = self.state.posture.is_slouching or self.state.posture.is_forward_head or self.state.posture.is_too_close
        posture_detail = "Shoulders aligned"
        if self.state.posture.is_slouching:
            posture_detail = f"Shoulder angle {self.state.posture.shoulder_angle:.1f} deg"
        elif self.state.posture.is_forward_head:
            posture_detail = f"Head ratio {self.state.posture.head_forward_ratio:.2f}"
        elif self.state.posture.is_too_close:
            posture_detail = "Face too close to screen"

        fatigue_status = "Drowsy" if self.state.fatigue.is_drowsy else "Alert"
        fatigue_detail = "Blink pattern stable"
        if self.state.fatigue.is_drowsy:
            fatigue_detail = f"EAR {self.state.fatigue.ear_avg:.3f} / {self.state.fatigue.blink_rate:.1f} min"

        return {
            "posture_status": "Bad" if posture_bad else "Good",
            "posture_detail": posture_detail,
            "fatigue_status": fatigue_status,
            "fatigue_detail": fatigue_detail,
            "room_light": self.state.environment.room_light_status,
            "room_light_level": self.state.environment.room_light_level,
            "temperature_c": self.state.environment.temperature_c,
            "fan_on": self.state.device.fan_on,
            "fan_detail": "Auto cooling active" if self.state.device.fan_on else "Fan idle",
            "lamp_brightness": self.state.device.lamp_brightness,
            "lamp_detail": f"{'Auto' if self.config.AUTO_MODE else 'Manual'} brightness control",
            "today_alerts": self.state.total_alerts,
            "session_time": session_clock(self.state.session_start),
            "blink_rate": self.state.fatigue.blink_rate,
            "esp32_connected": self.state.esp32_connected,
            "fps": self.current_fps,
            "last_command_status": self.state.device.last_command_status,
        }

    def refresh_all_views(self):
        metrics = self.metrics()
        self.dashboard_page.update_metrics(metrics)
        self.preview_page.update_metrics(metrics)
        self.manual_page.sync_state(
            self.config.AUTO_MODE,
            self.config.SILENT_MODE,
            self.state.device.fan_on,
            self.state.device.lamp_brightness,
        )
        endpoint = self.config.ESP32_HTTP_URL if self.config.ESP32_MODE == "http" else self.config.ESP32_SERIAL_PORT
        self.device_page.update_status(
            self.state.esp32_connected,
            self.config.ESP32_MODE,
            endpoint,
            self.state.device.paired_device_name,
            self.config.DEVICE_WIFI_SSID,
        )

    def refresh_reports(self):
        history = self.session_logger.load_history()
        daily = self.session_logger.load_daily_summary()
        self.reports_page.load_reports(daily, history)

    def refresh_device_status(self):
        status = self.esp32.get_status()
        self.state.esp32_connected = bool(status.get("connected"))
        if self.state.esp32_connected:
            self.state.device.last_command_status = "Device reachable"
            self.state.device.paired_device_name = status.get("device_name", "ESP32 Desk Node")
            self.state.device.fan_on = status.get("fan_on", self.state.device.fan_on)
            self.state.device.lamp_brightness = status.get("lamp_brightness", self.state.device.lamp_brightness)
            self.state.environment.temperature_c = status.get("temperature_c", self.state.environment.temperature_c)
        else:
            self.state.device.last_command_status = "Simulation mode"
        self.refresh_all_views()

    def repair_device(self):
        mode = self.device_page.mode_combo.currentText()
        endpoint = self.device_page.endpoint_input.text().strip()
        self.config.DEVICE_WIFI_SSID = self.device_page.wifi_ssid_input.text().strip()
        self.config.DEVICE_WIFI_PASSWORD = self.device_page.wifi_password_input.text().strip()
        if endpoint:
            update_device_connection(self.config, mode, endpoint)
        self.esp32.close()
        self.esp32 = pair_device(self.config)
        self.alerts = AlertManager(self.config, self.esp32)
        self.state.esp32_connected = self.esp32.connected
        self.state.device.paired_device_name = "ESP32 Desk Node" if self.state.esp32_connected else "Simulation Device"
        self.state.device.wifi_ssid = self.config.DEVICE_WIFI_SSID
        self.state.device.last_command_status = "Pairing updated"
        save_config(self.config)
        self.manual_page.append_log(f"Pairing refreshed in {mode.upper()} mode")
        self.refresh_all_views()

    def sync_device_settings(self):
        self.config.DEVICE_WIFI_SSID = self.device_page.wifi_ssid_input.text().strip()
        if self.device_page.wifi_password_input.text().strip():
            self.config.DEVICE_WIFI_PASSWORD = self.device_page.wifi_password_input.text().strip()
        payload = build_device_settings_payload(self.config)
        sent = self.esp32.push_settings(payload)
        self.state.device.last_command_status = "Device settings synced" if sent else "Device settings simulated"
        save_config(self.config)
        self.manual_page.append_log("Device settings pushed over /settings")
        self.refresh_all_views()

    def apply_settings(self):
        old_camera_index = self.config.CAMERA_INDEX
        self.config.CAMERA_INDEX = self.settings_page.camera_index.value()
        self.config.POSTURE_SENSITIVITY = self.settings_page.posture_sensitivity.value()
        self.config.FATIGUE_SENSITIVITY = self.settings_page.fatigue_sensitivity.value()
        self.config.BREAK_REMINDER_MINUTES = self.settings_page.break_minutes.value()
        self.config.DEFAULT_TEMPERATURE_C = float(self.settings_page.temperature_spin.value())
        self.config.AUTO_MODE = self.settings_page.auto_mode.isChecked()
        self.config.SILENT_MODE = self.settings_page.silent_mode.isChecked()
        self.config.FATIGUE_ALERT_ENABLED = self.settings_page.fatigue_alert.isChecked()
        self.config.AUTO_FAN = self.settings_page.auto_fan.isChecked()
        self.config.AUTO_LAMP = self.settings_page.auto_lamp.isChecked()
        self.config.STARTUP_AUTO_LAUNCH = self.settings_page.startup_auto_launch.isChecked()
        self.config.MINIMIZE_TO_TRAY = self.settings_page.minimize_to_tray.isChecked()
        self.config.apply_runtime_rules()

        self.state.environment.temperature_c = self.config.DEFAULT_TEMPERATURE_C
        self.state.device.auto_mode = self.config.AUTO_MODE
        self.state.device.silent_mode = self.config.SILENT_MODE
        set_windows_startup(self.config.STARTUP_AUTO_LAUNCH)

        if old_camera_index != self.config.CAMERA_INDEX:
            if self.cap.isOpened():
                self.cap.release()
            self.cap = cv2.VideoCapture(self.config.CAMERA_INDEX)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.FRAME_WIDTH)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.FRAME_HEIGHT)
            self.cap.set(cv2.CAP_PROP_FPS, self.config.FPS)

        save_config(self.config)
        self.state.device.last_command_status = "Settings applied"
        self.manual_page.append_log("Settings saved to config.json")
        self.refresh_all_views()

    def toggle_mode(self):
        self.config.AUTO_MODE = not self.config.AUTO_MODE
        self.state.device.auto_mode = self.config.AUTO_MODE
        self.state.device.last_command_status = "Auto mode enabled" if self.config.AUTO_MODE else "Manual mode enabled"
        save_config(self.config)
        self.manual_page.append_log(self.state.device.last_command_status)
        self.refresh_all_views()

    def toggle_silent_mode(self):
        self.config.SILENT_MODE = not self.config.SILENT_MODE
        self.state.device.silent_mode = self.config.SILENT_MODE
        self.state.device.last_command_status = "Silent mode enabled" if self.config.SILENT_MODE else "Silent mode disabled"
        save_config(self.config)
        self.manual_page.append_log(self.state.device.last_command_status)
        self.refresh_all_views()

    def toggle_fan(self):
        self.state.device.fan_on = not self.state.device.fan_on
        sent = self.esp32.set_fan(self.state.device.fan_on)
        self.state.device.last_command_status = "Fan command sent" if sent else "Fan simulated"
        self.manual_page.append_log(f"Fan {'enabled' if self.state.device.fan_on else 'disabled'}")
        self.refresh_all_views()

    def set_lamp_brightness(self, brightness: int):
        self.state.device.lamp_brightness = brightness
        self.config.DEFAULT_LAMP_BRIGHTNESS = brightness
        sent = self.esp32.set_lamp_brightness(brightness)
        self.state.device.last_command_status = "Lamp brightness sent" if sent else "Lamp simulated"
        save_config(self.config)
        self.refresh_all_views()

    def trigger_posture_alert(self):
        if self.config.SILENT_MODE:
            self.state.device.last_command_status = "Silent mode skipped posture buzzer"
        else:
            sent = self.esp32.posture_buzz()
            self.state.device.last_command_status = "Posture test sent" if sent else "Posture test simulated"
        self.manual_page.append_log("Manual posture alert triggered")
        self.refresh_all_views()

    def trigger_drowsy_alert(self):
        if self.config.SILENT_MODE:
            self.state.device.last_command_status = "Silent mode skipped drowsy buzzer"
        else:
            sent = self.esp32.drowsy_buzz()
            self.state.device.last_command_status = "Drowsy test sent" if sent else "Drowsy test simulated"
        self.manual_page.append_log("Manual drowsy alert triggered")
        self.refresh_all_views()

    def check_break_reminder(self):
        elapsed_minutes = (time.time() - self.state.last_break_at) / 60.0
        if elapsed_minutes < self.config.BREAK_REMINDER_MINUTES:
            return
        self.state.last_break_at = time.time()
        self.state.break_due = True
        message = "Break reminder: stand up, stretch, and reset your posture."
        self.manual_page.append_log(message)
        if self.tray_icon.isVisible():
            self.tray_icon.showMessage("Smart Study Optimizer", message, QSystemTrayIcon.Information, 5000)
        else:
            QMessageBox.information(self, "Break Reminder", message)

    def save_session_report(self):
        if self._reports_saved:
            return
        summary = self.session_logger.build_summary(self.state)
        self.session_logger.append_summary(summary)
        self._reports_saved = True
        logger.info(format_session_report(summary))
        self.refresh_reports()

    def restore_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.restore_from_tray()

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange and self.isMinimized() and self.config.MINIMIZE_TO_TRAY:
            QTimer.singleShot(0, self.hide)
            self.tray_icon.showMessage("Smart Study Optimizer", "App is still running in the system tray.", QSystemTrayIcon.Information, 3000)
        super().changeEvent(event)

    def quit_application(self):
        self._closing = True
        self.close()

    def closeEvent(self, event):
        if not self._closing and self.config.MINIMIZE_TO_TRAY:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage("Smart Study Optimizer", "Window minimized to system tray.", QSystemTrayIcon.Information, 3000)
            return

        self.timer.stop()
        self.break_timer.stop()
        self.save_session_report()
        if self.cap.isOpened():
            self.cap.release()
        self.pose_analyzer.close()
        self.face_analyzer.close()
        self.esp32.close()
        self.tray_icon.hide()
        super().closeEvent(event)

    def run(self):
        self.show()
        return self


def launch_app():
    app = QApplication.instance() or QApplication([])
    window = SmartStudyOptimizer()
    window.show()
    return app.exec()
