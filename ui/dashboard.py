from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)


class StatusBadge(QLabel):
    def __init__(self, text: str, tone: str = "neutral"):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumWidth(96)
        self.setStyleSheet("border-radius: 12px; padding: 6px 12px; font-weight: 700;")
        self.set_tone(tone)

    def set_tone(self, tone: str):
        palette = {
            "good": ("#D7F5E8", "#0F7B4D"),
            "warn": ("#FDEECF", "#A15C00"),
            "bad": ("#F9D8D7", "#A12622"),
            "neutral": ("#E6EAF2", "#415066"),
        }
        background, foreground = palette.get(tone, palette["neutral"])
        self.setStyleSheet(
            f"border-radius: 12px; padding: 6px 12px; font-weight: 700; background:{background}; color:{foreground};"
        )


class MetricCard(QFrame):
    def __init__(self, title: str, value: str, accent: str = "#2F6FED", subtitle: str = ""):
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{background: white; border: 1px solid #D9E0EA; border-top: 4px solid {accent}; border-radius: 18px;}}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setStyleSheet("color: #5A667A; font-size: 12px; font-weight: 600;")
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet("color: #142033; font-size: 24px; font-weight: 800;")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setStyleSheet("color: #7A8799; font-size: 12px;")

        layout.addWidget(title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.subtitle_label)

    def set_value(self, value: str, subtitle: str = ""):
        self.value_label.setText(value)
        self.subtitle_label.setText(subtitle)


class DashboardPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        header = QVBoxLayout()
        title = QLabel("Workspace Dashboard")
        title.setStyleSheet("font-size: 28px; font-weight: 800; color: #142033;")
        subtitle = QLabel("Real-time wellness, device, and environment overview")
        subtitle.setStyleSheet("font-size: 14px; color: #6D7787;")
        header.addWidget(title)
        header.addWidget(subtitle)
        root.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        self.posture_card = MetricCard("Posture Status", "Good", "#11875D", "Shoulders aligned")
        self.fatigue_card = MetricCard("Fatigue Status", "Alert", "#E38B15", "Blink pattern stable")
        self.light_card = MetricCard("Room Light", "Good", "#F3B33D", "Camera brightness estimate")
        self.temp_card = MetricCard("Temperature", "30.0 C", "#2F6FED", "Desk sensor / manual value")
        self.fan_card = MetricCard("Fan", "ON", "#0E9F6E", "Cooling is active")
        self.lamp_card = MetricCard("Lamp Brightness", "65%", "#9B59B6", "Manual or device driven")

        cards = [
            self.posture_card,
            self.fatigue_card,
            self.light_card,
            self.temp_card,
            self.fan_card,
            self.lamp_card,
        ]
        positions = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)]
        for card, (row, col) in zip(cards, positions):
            grid.addWidget(card, row, col)
        root.addLayout(grid)

        overview = QFrame()
        overview.setStyleSheet("QFrame {background: white; border: 1px solid #D9E0EA; border-radius: 18px;}")
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(20, 18, 20, 18)
        overview_layout.setSpacing(12)

        title_row = QHBoxLayout()
        section_title = QLabel("Today")
        section_title.setStyleSheet("font-size: 18px; font-weight: 700; color: #142033;")
        self.connection_badge = StatusBadge("Simulation", "neutral")
        title_row.addWidget(section_title)
        title_row.addStretch()
        title_row.addWidget(self.connection_badge)
        overview_layout.addLayout(title_row)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(24)
        self.alert_total = self._small_stat("Today's Alerts", "0")
        self.session_time = self._small_stat("Session Time", "00:00:00")
        self.blink_rate = self._small_stat("Blink Rate", "0.0/min")
        stats_row.addWidget(self.alert_total)
        stats_row.addWidget(self.session_time)
        stats_row.addWidget(self.blink_rate)
        overview_layout.addLayout(stats_row)

        light_label = QLabel("Room Light Level")
        light_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #415066;")
        self.light_progress = QProgressBar()
        self.light_progress.setRange(0, 100)
        self.light_progress.setValue(60)
        self.light_progress.setStyleSheet(
            "QProgressBar {background: #EEF2F7; border-radius: 8px; height: 14px; color: #142033;}"
            "QProgressBar::chunk {background: #F3B33D; border-radius: 8px;}"
        )
        overview_layout.addWidget(light_label)
        overview_layout.addWidget(self.light_progress)
        root.addWidget(overview)

    def _small_stat(self, label: str, value: str) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label_widget = QLabel(label)
        label_widget.setStyleSheet("font-size: 12px; color: #7A8799; font-weight: 600;")
        value_widget = QLabel(value)
        value_widget.setStyleSheet("font-size: 26px; color: #142033; font-weight: 800;")
        layout.addWidget(label_widget)
        layout.addWidget(value_widget)
        card.value_widget = value_widget
        return card

    def update_metrics(self, metrics: dict):
        posture_bad = metrics["posture_status"] == "Bad"
        fatigue_bad = metrics["fatigue_status"] == "Drowsy"
        light_low = metrics["room_light"] == "Low"

        self.posture_card.set_value(metrics["posture_status"], metrics["posture_detail"])
        self.fatigue_card.set_value(metrics["fatigue_status"], metrics["fatigue_detail"])
        self.light_card.set_value(metrics["room_light"], f"Level {metrics['room_light_level']}%")
        self.temp_card.set_value(f"{metrics['temperature_c']:.1f} C", "Ambient desk condition")
        self.fan_card.set_value("ON" if metrics["fan_on"] else "OFF", metrics["fan_detail"])
        self.lamp_card.set_value(f"{metrics['lamp_brightness']}%", metrics["lamp_detail"])

        self.connection_badge.setText("Connected" if metrics["esp32_connected"] else "Simulation")
        self.connection_badge.set_tone("good" if metrics["esp32_connected"] else "neutral")
        self.alert_total.value_widget.setText(str(metrics["today_alerts"]))
        self.session_time.value_widget.setText(metrics["session_time"])
        self.blink_rate.value_widget.setText(f"{metrics['blink_rate']:.1f}/min")
        self.light_progress.setValue(metrics["room_light_level"])

        self._style_card(self.posture_card, posture_bad, "#A12622", "#11875D")
        self._style_card(self.fatigue_card, fatigue_bad, "#A12622", "#E38B15")
        self._style_card(self.light_card, light_low, "#A15C00", "#F3B33D")

    def _style_card(self, card: MetricCard, bad: bool, bad_color: str, good_color: str):
        accent = bad_color if bad else good_color
        card.setStyleSheet(
            f"QFrame {{background: white; border: 1px solid #D9E0EA; border-top: 4px solid {accent}; border-radius: 18px;}}"
        )
