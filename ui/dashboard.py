from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class StatusBadge(QLabel):
    def __init__(self, text: str, tone: str = "neutral"):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumWidth(104)
        self.setStyleSheet("border-radius: 13px; padding: 7px 13px; font-size: 12px; font-weight: 700;")
        self.set_tone(tone)

    def set_tone(self, tone: str):
        palette = {
            "good": ("rgba(52, 199, 89, 0.12)", "#177C37"),
            "warn": ("rgba(255, 159, 10, 0.14)", "#A15F00"),
            "bad": ("rgba(255, 59, 48, 0.12)", "#B3261E"),
            "neutral": ("rgba(120, 120, 128, 0.12)", "#566070"),
        }
        background, foreground = palette.get(tone, palette["neutral"])
        self.setStyleSheet(
            f"border-radius: 13px; padding: 7px 13px; font-size: 12px; font-weight: 700; background:{background}; color:{foreground};"
        )


class MetricCard(QFrame):
    def __init__(self, title: str, value: str, accent: str = "#0A84FF", subtitle: str = ""):
        super().__init__()
        self.accent = accent
        self.setObjectName("metricCard")
        self.setMinimumHeight(152)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            "QFrame#metricCard {"
            "background: rgba(255, 255, 255, 0.84);"
            "border: 1px solid rgba(15, 23, 42, 0.08);"
            "border-radius: 24px;"
            "}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #667085; font-size: 12px; font-weight: 600; letter-spacing: 0.2px;")
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        self.dot.setStyleSheet(f"background: {accent}; border-radius: 5px;")
        top.addWidget(self.title_label)
        top.addStretch()
        top.addWidget(self.dot)

        self.value_label = QLabel(value)
        self.value_label.setStyleSheet("color: #101828; font-size: 28px; font-weight: 700;")
        self.value_label.setWordWrap(True)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setStyleSheet("color: #8A94A6; font-size: 12px; font-weight: 500;")
        self.subtitle_label.setWordWrap(True)

        layout.addLayout(top)
        layout.addWidget(self.value_label)
        layout.addWidget(self.subtitle_label)
        layout.addStretch()

    def set_value(self, value: str, subtitle: str = ""):
        self.value_label.setText(value)
        self.subtitle_label.setText(subtitle)

    def set_accent(self, accent: str):
        self.accent = accent
        self.dot.setStyleSheet(f"background: {accent}; border-radius: 5px;")


class StatChip(QFrame):
    def __init__(self, label: str, value: str):
        super().__init__()
        self.setStyleSheet(
            "QFrame {background: rgba(248, 250, 252, 0.9); border: 1px solid rgba(15, 23, 42, 0.06); border-radius: 18px;}"
        )
        self.setMinimumHeight(92)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        label_widget = QLabel(label)
        label_widget.setStyleSheet("font-size: 12px; color: #8A94A6; font-weight: 600;")
        self.value_widget = QLabel(value)
        self.value_widget.setStyleSheet("font-size: 24px; color: #101828; font-weight: 700;")
        layout.addWidget(label_widget)
        layout.addWidget(self.value_widget)


class ResponsiveCardGrid(QWidget):
    def __init__(self):
        super().__init__()
        self.cards = []
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(18)

    def set_cards(self, cards: list[QWidget]):
        self.cards = cards
        self._rebuild()

    def resizeEvent(self, event):
        self._rebuild()
        super().resizeEvent(event)

    def _column_count(self) -> int:
        width = max(self.width(), 1)
        if width < 760:
            return 1
        if width < 1120:
            return 2
        return 3

    def _rebuild(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                self.grid.removeWidget(widget)

        cols = self._column_count()
        for index, card in enumerate(self.cards):
            row = index // cols
            col = index % cols
            self.grid.addWidget(card, row, col)

        for col in range(cols):
            self.grid.setColumnStretch(col, 1)


class DashboardPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        hero = QFrame()
        hero.setStyleSheet(
            "QFrame {"
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255,255,255,0.94), stop:1 rgba(242,244,247,0.88));"
            "border: 1px solid rgba(15, 23, 42, 0.06);"
            "border-radius: 28px;"
            "}"
        )
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(26, 22, 26, 22)
        hero_layout.setSpacing(18)

        header = QVBoxLayout()
        header.setSpacing(4)
        eyebrow = QLabel("Focused workspace intelligence")
        eyebrow.setStyleSheet("font-size: 12px; color: #8A94A6; font-weight: 700; letter-spacing: 0.3px;")
        title = QLabel("Workspace Dashboard")
        title.setStyleSheet("font-size: 28px; font-weight: 700; color: #101828;")
        subtitle = QLabel("A calm overview of posture, fatigue, device behavior, and ambient conditions.")
        subtitle.setStyleSheet("font-size: 14px; color: #667085; font-weight: 500;")
        subtitle.setWordWrap(True)
        header.addWidget(eyebrow)
        header.addWidget(title)
        header.addWidget(subtitle)

        right = QVBoxLayout()
        right.setSpacing(10)
        right.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.connection_badge = StatusBadge("Simulation", "neutral")
        self.session_capsule = QLabel("Session 00:00:00")
        self.session_capsule.setStyleSheet(
            "background: rgba(10, 132, 255, 0.08); color: #0A63C9; border-radius: 14px; padding: 8px 14px; font-size: 12px; font-weight: 700;"
        )
        right.addWidget(self.connection_badge, alignment=Qt.AlignRight)
        right.addWidget(self.session_capsule, alignment=Qt.AlignRight)

        hero_layout.addLayout(header, 1)
        hero_layout.addLayout(right)
        root.addWidget(hero)

        self.card_grid = ResponsiveCardGrid()
        self.posture_card = MetricCard("Posture Status", "Good", "#34C759", "Shoulders aligned")
        self.fatigue_card = MetricCard("Fatigue Status", "Alert", "#FF9F0A", "Blink rhythm stable")
        self.light_card = MetricCard("Room Light", "Good", "#FFD60A", "Camera brightness estimate")
        self.temp_card = MetricCard("Temperature", "30.0 C", "#0A84FF", "Ambient desk condition")
        self.fan_card = MetricCard("Fan", "ON", "#30B0C7", "Cooling is active")
        self.lamp_card = MetricCard("Lamp Brightness", "65%", "#BF5AF2", "Adaptive desk lighting")
        self.card_grid.set_cards(
            [
                self.posture_card,
                self.fatigue_card,
                self.light_card,
                self.temp_card,
                self.fan_card,
                self.lamp_card,
            ]
        )
        root.addWidget(self.card_grid)

        overview = QFrame()
        overview.setStyleSheet(
            "QFrame {"
            "background: rgba(255, 255, 255, 0.86);"
            "border: 1px solid rgba(15, 23, 42, 0.06);"
            "border-radius: 28px;"
            "}"
        )
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(24, 22, 24, 22)
        overview_layout.setSpacing(16)

        title_row = QHBoxLayout()
        section_title = QLabel("Today")
        section_title.setStyleSheet("font-size: 20px; font-weight: 700; color: #101828;")
        section_hint = QLabel("Quietly tracking your workspace rhythm")
        section_hint.setStyleSheet("font-size: 13px; color: #8A94A6;")
        title_row.addWidget(section_title)
        title_row.addSpacing(8)
        title_row.addWidget(section_hint)
        title_row.addStretch()
        overview_layout.addLayout(title_row)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(16)
        self.alert_total = StatChip("Today's Alerts", "0")
        self.session_time = StatChip("Session Time", "00:00:00")
        self.blink_rate = StatChip("Blink Rate", "0.0/min")
        stats_row.addWidget(self.alert_total)
        stats_row.addWidget(self.session_time)
        stats_row.addWidget(self.blink_rate)
        overview_layout.addLayout(stats_row)

        light_label = QLabel("Room Light Level")
        light_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #667085;")
        self.light_progress = QProgressBar()
        self.light_progress.setRange(0, 100)
        self.light_progress.setValue(60)
        self.light_progress.setTextVisible(False)
        self.light_progress.setStyleSheet(
            "QProgressBar {background: rgba(15, 23, 42, 0.06); border-radius: 9px; height: 18px;}"
            "QProgressBar::chunk {background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FFD60A, stop:1 #FFB340); border-radius: 9px;}"
        )
        overview_layout.addWidget(light_label)
        overview_layout.addWidget(self.light_progress)
        root.addWidget(overview)

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
        self.session_capsule.setText(f"Session {metrics['session_time']}")
        self.alert_total.value_widget.setText(str(metrics["today_alerts"]))
        self.session_time.value_widget.setText(metrics["session_time"])
        self.blink_rate.value_widget.setText(f"{metrics['blink_rate']:.1f}/min")
        self.light_progress.setValue(metrics["room_light_level"])

        self._style_card(self.posture_card, posture_bad, "#FF3B30", "#34C759")
        self._style_card(self.fatigue_card, fatigue_bad, "#FF3B30", "#FF9F0A")
        self._style_card(self.light_card, light_low, "#FF9F0A", "#FFD60A")

    def _style_card(self, card: MetricCard, bad: bool, bad_color: str, good_color: str):
        card.set_accent(bad_color if bad else good_color)
