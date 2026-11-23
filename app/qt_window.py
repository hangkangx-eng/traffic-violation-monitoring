from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygon,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
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
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config_store import ConfigStore
from app.schemas import LaneConfig, MonitorConfig
from app.video_processor import VideoProcessor


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = DATA_DIR / "configs"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

FRAME_WIDTH = 960
FRAME_HEIGHT = 540


TOOL_NONE = "none"
TOOL_PARKING_ROI = "parking_roi"
TOOL_LANE_POLYGON = "lane_polygon"
TOOL_LANE_DIRECTION = "lane_direction"
TOOL_SPEED_LINE_A = "speed_line_a"
TOOL_SPEED_LINE_B = "speed_line_b"


def make_default_config(camera_id: str) -> MonitorConfig:
    config = MonitorConfig(camera_id=camera_id or "cam_001")
    if not config.reverse.lanes:
        config.reverse.lanes = [LaneConfig(name="lane_1")]
    return config


def ensure_lane(config: MonitorConfig) -> None:
    if not config.reverse.lanes:
        config.reverse.lanes = [LaneConfig(name="lane_1")]


class VideoCanvas(QWidget):
    config_changed = Signal()

    def __init__(
        self,
        config_provider: Callable[[], MonitorConfig],
        active_tool_provider: Callable[[], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_provider = config_provider
        self._active_tool_provider = active_tool_provider
        self._frame = QImage()
        self._draft_points: list[list[int]] = []
        self._line_start: list[int] | None = None
        self._draw_rect = QRect(0, 0, 0, 0)
        self.setMouseTracking(True)
        self.setMinimumSize(760, 430)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def clear_draft(self) -> None:
        self._draft_points.clear()
        self._line_start = None
        self.update()

    def finish_current_polygon(self) -> None:
        if len(self._draft_points) < 3:
            return
        config = self._config_provider()
        tool = self._active_tool_provider()
        if tool == TOOL_PARKING_ROI and config.parking.enabled:
            config.parking.rois.append([list(p) for p in self._draft_points])
        elif tool == TOOL_LANE_POLYGON and config.reverse.enabled:
            ensure_lane(config)
            config.reverse.lanes[0].polygon = [list(p) for p in self._draft_points]
        self._draft_points.clear()
        self.config_changed.emit()
        self.update()

    def clear_current_shape(self) -> None:
        if self._draft_points:
            self._draft_points.clear()
            self.update()
            return
        config = self._config_provider()
        tool = self._active_tool_provider()
        if tool == TOOL_SPEED_LINE_A:
            config.speed.line_a = []
        elif tool == TOOL_SPEED_LINE_B:
            config.speed.line_b = []
        elif tool == TOOL_PARKING_ROI and config.parking.rois:
            config.parking.rois.pop()
        elif tool == TOOL_LANE_POLYGON:
            ensure_lane(config)
            config.reverse.lanes[0].polygon = []
        elif tool == TOOL_LANE_DIRECTION:
            ensure_lane(config)
            config.reverse.lanes[0].legal_direction = []
        self.config_changed.emit()
        self.update()

    def set_frame_bytes(self, jpeg_bytes: bytes) -> None:
        if not jpeg_bytes:
            return
        image = QImage.fromData(jpeg_bytes, "JPG")
        if image.isNull():
            return
        self._frame = image
        self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.RightButton:
            self.finish_current_polygon()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        tool = self._active_tool_provider()
        if tool == TOOL_NONE:
            return
        point = self._map_view_to_frame(event.position().toPoint())
        if point is None:
            return
        config = self._config_provider()

        if tool in {TOOL_PARKING_ROI, TOOL_LANE_POLYGON}:
            self._draft_points.append([point[0], point[1]])
            self.update()
            return

        if self._line_start is None:
            self._line_start = [point[0], point[1]]
            self.update()
            return

        x1, y1 = self._line_start
        x2, y2 = point
        line = [[x1, y1], [x2, y2]]
        if tool in {TOOL_SPEED_LINE_A, TOOL_SPEED_LINE_B}:
            line = [[x1, y1], [x2, y1]]

        if tool == TOOL_SPEED_LINE_A and config.speed.enabled:
            config.speed.line_a = line
        elif tool == TOOL_SPEED_LINE_B and config.speed.enabled:
            config.speed.line_b = line
        elif tool == TOOL_LANE_DIRECTION and config.reverse.enabled:
            ensure_lane(config)
            config.reverse.lanes[0].legal_direction = line
        self._line_start = None
        self.config_changed.emit()
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#0F1720"))

        frame = self._frame
        if frame.isNull():
            frame = QImage(FRAME_WIDTH, FRAME_HEIGHT, QImage.Format.Format_RGB32)
            frame.fill(QColor("#111827"))

        frame_ratio = frame.width() / max(1, frame.height())
        area_ratio = self.width() / max(1, self.height())
        if area_ratio > frame_ratio:
            draw_h = self.height()
            draw_w = int(draw_h * frame_ratio)
        else:
            draw_w = self.width()
            draw_h = int(draw_w / frame_ratio)
        offset_x = (self.width() - draw_w) // 2
        offset_y = (self.height() - draw_h) // 2
        self._draw_rect = QRect(offset_x, offset_y, draw_w, draw_h)

        painter.drawImage(self._draw_rect, frame)
        self._draw_overlay(painter)

    def _draw_overlay(self, painter: QPainter) -> None:
        config = self._config_provider()
        ensure_lane(config)
        if config.speed.enabled:
            self._draw_line(painter, config.speed.line_a, QColor("#F6C453"), 2)
            self._draw_line(painter, config.speed.line_b, QColor("#F97316"), 2)
        if config.parking.enabled:
            for roi in config.parking.rois:
                self._draw_polygon(painter, roi, QColor("#F97316"))
        if config.reverse.enabled:
            for lane in config.reverse.lanes:
                self._draw_polygon(painter, lane.polygon, QColor("#38BDF8"))
                self._draw_arrow(painter, lane.legal_direction, QColor("#60A5FA"))
        if self._draft_points:
            self._draw_polygon(painter, self._draft_points, QColor("#FFFFFF"))
        if self._line_start:
            self._draw_point(painter, self._line_start, QColor("#FFFFFF"))

    def _draw_polygon(self, painter: QPainter, points: list[list[float]], color: QColor) -> None:
        if not points:
            return
        view_points = [self._map_frame_to_view(p[0], p[1]) for p in points]
        pen = QPen(color, 2)
        painter.setPen(pen)
        path = QPainterPath()
        path.moveTo(view_points[0])
        for pt in view_points[1:]:
            path.lineTo(pt)
        if len(view_points) >= 3:
            path.closeSubpath()
        painter.drawPath(path)
        for pt in view_points:
            painter.setBrush(color)
            painter.drawEllipse(pt, 3, 3)

    def _draw_line(
        self, painter: QPainter, line: list[list[float]], color: QColor, width: int = 2
    ) -> None:
        if len(line) != 2:
            return
        p1 = self._map_frame_to_view(line[0][0], line[0][1])
        p2 = self._map_frame_to_view(line[1][0], line[1][1])
        painter.setPen(QPen(color, width))
        painter.drawLine(p1, p2)

    def _draw_arrow(self, painter: QPainter, line: list[list[float]], color: QColor) -> None:
        if len(line) != 2:
            return
        p1 = self._map_frame_to_view(line[0][0], line[0][1])
        p2 = self._map_frame_to_view(line[1][0], line[1][1])
        painter.setPen(QPen(color, 2))
        painter.drawLine(p1, p2)

        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length = max((dx * dx + dy * dy) ** 0.5, 1.0)
        ux, uy = dx / length, dy / length
        arrow_len = 14
        left = QPoint(
            int(p2.x() - ux * arrow_len - uy * arrow_len * 0.5),
            int(p2.y() - uy * arrow_len + ux * arrow_len * 0.5),
        )
        right = QPoint(
            int(p2.x() - ux * arrow_len + uy * arrow_len * 0.5),
            int(p2.y() - uy * arrow_len - ux * arrow_len * 0.5),
        )
        painter.setBrush(color)
        painter.drawPolygon(QPolygon([p2, left, right]))

    def _draw_point(self, painter: QPainter, point: list[float], color: QColor) -> None:
        p = self._map_frame_to_view(point[0], point[1])
        painter.setBrush(color)
        painter.setPen(QPen(color, 1))
        painter.drawEllipse(p, 4, 4)

    def _map_frame_to_view(self, x: float, y: float) -> QPoint:
        if self._draw_rect.width() <= 0 or self._draw_rect.height() <= 0:
            return QPoint(int(x), int(y))
        fx = x / FRAME_WIDTH
        fy = y / FRAME_HEIGHT
        vx = self._draw_rect.x() + int(fx * self._draw_rect.width())
        vy = self._draw_rect.y() + int(fy * self._draw_rect.height())
        return QPoint(vx, vy)

    def _map_view_to_frame(self, point: QPoint) -> list[int] | None:
        if not self._draw_rect.contains(point):
            return None
        rel_x = (point.x() - self._draw_rect.x()) / max(1, self._draw_rect.width())
        rel_y = (point.y() - self._draw_rect.y()) / max(1, self._draw_rect.height())
        frame_x = int(round(rel_x * FRAME_WIDTH))
        frame_y = int(round(rel_y * FRAME_HEIGHT))
        frame_x = max(0, min(FRAME_WIDTH - 1, frame_x))
        frame_y = max(0, min(FRAME_HEIGHT - 1, frame_y))
        return [frame_x, frame_y]


class MonitorMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("车辆行为监测系统")
        self.resize(1560, 920)

        self.config_store = ConfigStore(CONFIG_DIR)
        self.processor = VideoProcessor(snapshot_dir=SNAPSHOT_DIR, frame_size=(FRAME_WIDTH, FRAME_HEIGHT))

        self._camera_id = "cam_001"
        self._config = make_default_config(self._camera_id)
        self._tool = TOOL_NONE

        self._build_ui()
        self._bind_events()
        self._apply_theme()
        self._apply_config_to_form()
        self._sync_enable_state()

        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(40)
        self.frame_timer.timeout.connect(self._refresh_frame)
        self.frame_timer.start()

        self.status_timer = QTimer(self)
        self.status_timer.setInterval(1200)
        self.status_timer.timeout.connect(self._refresh_status_and_events)
        self.status_timer.start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.processor.stop()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        header = QFrame()
        header.setObjectName("headerCard")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 18, 12)
        header_layout.setSpacing(12)
        title = QLabel("车辆行为监测系统")
        title.setObjectName("titleLabel")
        self.status_label = QLabel("状态: idle")
        self.status_label.setObjectName("statusLabel")
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)
        outer.addWidget(header)

        content = QHBoxLayout()
        content.setSpacing(14)
        outer.addLayout(content, stretch=1)

        left_card = self._card_container("视频与标注")
        left_layout = left_card.layout()

        self.canvas = VideoCanvas(
            config_provider=self._get_config,
            active_tool_provider=self._get_tool,
        )
        left_layout.addWidget(self.canvas, stretch=1)

        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        tool_label = QLabel("绘制模式")
        self.tool_combo = QComboBox()
        self.tool_combo.addItem("无", TOOL_NONE)
        self.tool_combo.addItem("违停区域", TOOL_PARKING_ROI)
        self.tool_combo.addItem("车道区域", TOOL_LANE_POLYGON)
        self.tool_combo.addItem("车道方向箭头", TOOL_LANE_DIRECTION)
        self.tool_combo.addItem("测速线 A", TOOL_SPEED_LINE_A)
        self.tool_combo.addItem("测速线 B", TOOL_SPEED_LINE_B)
        self.finish_btn = QPushButton("完成多边形")
        self.clear_current_btn = QPushButton("清除当前")
        self.clear_all_btn = QPushButton("清空全部")
        tool_row.addWidget(tool_label)
        tool_row.addWidget(self.tool_combo, stretch=1)
        tool_row.addWidget(self.finish_btn)
        tool_row.addWidget(self.clear_current_btn)
        tool_row.addWidget(self.clear_all_btn)
        left_layout.addLayout(tool_row)

        hint = QLabel("提示: 多边形左键逐点绘制，右键或“完成多边形”结束；线段工具点击两次完成。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        left_layout.addWidget(hint)
        content.addWidget(left_card, stretch=3)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        source_card = self._card_container("视频源")
        source_grid = QGridLayout()
        source_grid.setHorizontalSpacing(8)
        source_grid.setVerticalSpacing(8)
        self.camera_id_input = QLineEdit("cam_001")
        self.source_input = QLineEdit()
        self.source_input.setPlaceholderText(r"例如 D:\videos\demo.mp4 或 rtsp://...")
        self.browse_btn = QPushButton("选择文件")
        self.start_btn = QPushButton("启动监测")
        self.start_btn.setObjectName("primaryButton")
        self.stop_btn = QPushButton("停止监测")
        source_grid.addWidget(QLabel("摄像头 ID"), 0, 0)
        source_grid.addWidget(self.camera_id_input, 0, 1, 1, 2)
        source_grid.addWidget(QLabel("本地路径或流地址"), 1, 0)
        source_grid.addWidget(self.source_input, 1, 1, 1, 2)
        source_grid.addWidget(self.browse_btn, 2, 1)
        source_grid.addWidget(self.start_btn, 3, 1)
        source_grid.addWidget(self.stop_btn, 3, 2)
        source_card.layout().addLayout(source_grid)
        right_layout.addWidget(source_card)

        rules_card = self._card_container("规则参数")
        rules_layout = QGridLayout()
        rules_layout.setHorizontalSpacing(8)
        rules_layout.setVerticalSpacing(8)

        self.speed_enabled = QCheckBox("开启超速检测")
        self.speed_limit = QDoubleSpinBox()
        self.speed_limit.setRange(1.0, 300.0)
        self.speed_limit.setValue(60.0)
        self.speed_limit.setSuffix(" km/h")
        self.real_distance = QDoubleSpinBox()
        self.real_distance.setRange(0.1, 10000.0)
        self.real_distance.setDecimals(2)
        self.real_distance.setValue(15.0)
        self.real_distance.setSuffix(" m")
        self.meter_per_pixel = QDoubleSpinBox()
        self.meter_per_pixel.setRange(0.000001, 10.0)
        self.meter_per_pixel.setDecimals(6)
        self.meter_per_pixel.setSingleStep(0.001)
        self.meter_per_pixel.setValue(0.05)
        self.parking_enabled = QCheckBox("开启违停检测")
        self.parking_min_stay = QDoubleSpinBox()
        self.parking_min_stay.setRange(1.0, 36000.0)
        self.parking_min_stay.setValue(60.0)
        self.parking_min_stay.setSuffix(" s")
        self.parking_max_disp = QDoubleSpinBox()
        self.parking_max_disp.setRange(1.0, 1000.0)
        self.parking_max_disp.setValue(20.0)
        self.parking_max_disp.setSuffix(" px")
        self.reverse_enabled = QCheckBox("开启逆行检测")
        self.reverse_cos = QDoubleSpinBox()
        self.reverse_cos.setRange(-1.0, 1.0)
        self.reverse_cos.setSingleStep(0.05)
        self.reverse_cos.setValue(-0.3)
        self.reverse_min_duration = QDoubleSpinBox()
        self.reverse_min_duration.setRange(0.5, 120.0)
        self.reverse_min_duration.setSingleStep(0.5)
        self.reverse_min_duration.setValue(2.0)
        self.reverse_min_duration.setSuffix(" s")
        self.reverse_min_disp = QDoubleSpinBox()
        self.reverse_min_disp.setRange(1.0, 1000.0)
        self.reverse_min_disp.setValue(40.0)
        self.reverse_min_disp.setSuffix(" px")

        row = 0
        rules_layout.addWidget(self.speed_enabled, row, 0, 1, 2)
        row += 1
        rules_layout.addWidget(QLabel("限速"), row, 0)
        rules_layout.addWidget(self.speed_limit, row, 1)
        row += 1
        rules_layout.addWidget(QLabel("测速两线实际距离"), row, 0)
        rules_layout.addWidget(self.real_distance, row, 1)
        row += 1
        rules_layout.addWidget(QLabel("像素与实际距离比值"), row, 0)
        rules_layout.addWidget(self.meter_per_pixel, row, 1)
        row += 1
        rules_layout.addWidget(self.parking_enabled, row, 0, 1, 2)
        row += 1
        rules_layout.addWidget(QLabel("违停最小时长"), row, 0)
        rules_layout.addWidget(self.parking_min_stay, row, 1)
        row += 1
        rules_layout.addWidget(QLabel("违停最大位移"), row, 0)
        rules_layout.addWidget(self.parking_max_disp, row, 1)
        row += 1
        rules_layout.addWidget(self.reverse_enabled, row, 0, 1, 2)
        row += 1
        rules_layout.addWidget(QLabel("逆行 cos 阈值"), row, 0)
        rules_layout.addWidget(self.reverse_cos, row, 1)
        row += 1
        rules_layout.addWidget(QLabel("逆行最短持续"), row, 0)
        rules_layout.addWidget(self.reverse_min_duration, row, 1)
        row += 1
        rules_layout.addWidget(QLabel("逆行最小位移"), row, 0)
        rules_layout.addWidget(self.reverse_min_disp, row, 1)

        self.load_config_btn = QPushButton("加载配置")
        self.save_config_btn = QPushButton("保存配置")
        rules_layout.addWidget(self.load_config_btn, row + 1, 0)
        rules_layout.addWidget(self.save_config_btn, row + 1, 1)
        rules_card.layout().addLayout(rules_layout)
        right_layout.addWidget(rules_card)

        events_card = self._card_container("实时告警")
        self.event_list = QListWidget()
        self.event_list.setAlternatingRowColors(True)
        events_card.layout().addWidget(self.event_list)
        right_layout.addWidget(events_card, stretch=1)

        right_layout.addStretch()
        right_scroll.setWidget(right_panel)
        content.addWidget(right_scroll, stretch=2)

    def _card_container(self, title: str) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        layout.addWidget(title_label)
        return card

    def _bind_events(self) -> None:
        self.tool_combo.currentIndexChanged.connect(self._on_tool_changed)
        self.finish_btn.clicked.connect(self.canvas.finish_current_polygon)
        self.clear_current_btn.clicked.connect(self.canvas.clear_current_shape)
        self.clear_all_btn.clicked.connect(self._clear_all_shapes)
        self.browse_btn.clicked.connect(self._pick_source_file)
        self.start_btn.clicked.connect(self._start_task)
        self.stop_btn.clicked.connect(self._stop_task)
        self.load_config_btn.clicked.connect(self._load_config)
        self.save_config_btn.clicked.connect(self._save_config)
        self.event_list.itemDoubleClicked.connect(self._open_event_snapshot)

        self.canvas.config_changed.connect(self._save_form_to_config)
        self.speed_enabled.stateChanged.connect(self._on_rule_toggle)
        self.parking_enabled.stateChanged.connect(self._on_rule_toggle)
        self.reverse_enabled.stateChanged.connect(self._on_rule_toggle)

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        font = QFont("Fira Sans")
        font.setPointSize(10)
        app.setFont(font)
        app.setStyleSheet(
            """
            QWidget {
                background-color: #F8FAFC;
                color: #334155;
            }
            QMainWindow {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #EEF2F7,
                    stop:1 #F8FAFC
                );
            }
            #headerCard {
                background-color: rgba(255, 255, 255, 0.9);
                border: 1px solid #D7DFEA;
                border-radius: 14px;
            }
            #titleLabel {
                font-size: 20px;
                font-weight: 700;
                color: #1E293B;
            }
            #statusLabel {
                color: #475569;
                font-size: 13px;
            }
            #card {
                background-color: #FFFFFF;
                border: 1px solid #D7DFEA;
                border-radius: 14px;
            }
            #cardTitle {
                font-size: 15px;
                font-weight: 650;
                color: #1E293B;
            }
            #hintLabel {
                color: #64748B;
                font-size: 12px;
            }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget {
                background-color: #FDFEFF;
                border: 1px solid #CBD5E1;
                border-radius: 9px;
                padding: 6px 8px;
                selection-background-color: #94A3B8;
                selection-color: #0F172A;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #64748B;
            }
            QPushButton {
                border: 1px solid #CBD5E1;
                border-radius: 9px;
                padding: 7px 10px;
                background-color: #FFFFFF;
                color: #334155;
            }
            QPushButton:hover {
                border-color: #94A3B8;
                background-color: #F8FAFC;
            }
            QPushButton:pressed {
                background-color: #E2E8F0;
            }
            QPushButton#primaryButton {
                background-color: #F97316;
                border-color: #EA580C;
                color: #FFFFFF;
                font-weight: 600;
            }
            QPushButton#primaryButton:hover {
                background-color: #EA580C;
            }
            QCheckBox {
                spacing: 7px;
                color: #1E293B;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #CBD5E1;
                border-radius: 4px;
                background: #FFFFFF;
            }
            QCheckBox::indicator:checked {
                border: 1px solid #F97316;
                background: #F97316;
            }
            QListWidget::item {
                border-bottom: 1px solid #E2E8F0;
                padding: 6px;
            }
            QListWidget::item:selected {
                background-color: #E2E8F0;
                color: #0F172A;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            """
        )

    def _get_config(self) -> MonitorConfig:
        return self._config

    def _get_tool(self) -> str:
        return self._tool

    def _on_tool_changed(self) -> None:
        tool = self.tool_combo.currentData()
        self._tool = str(tool or TOOL_NONE)
        self.canvas.clear_draft()
        self._enforce_tool_availability()

    def _enforce_tool_availability(self) -> None:
        disallowed: set[str] = set()
        if not self.speed_enabled.isChecked():
            disallowed.update({TOOL_SPEED_LINE_A, TOOL_SPEED_LINE_B})
        if not self.parking_enabled.isChecked():
            disallowed.add(TOOL_PARKING_ROI)
        if not self.reverse_enabled.isChecked():
            disallowed.update({TOOL_LANE_POLYGON, TOOL_LANE_DIRECTION})
        if self._tool in disallowed:
            self._tool = TOOL_NONE
            self.tool_combo.setCurrentIndex(0)
            self.canvas.clear_draft()

    def _save_form_to_config(self) -> None:
        self._camera_id = self.camera_id_input.text().strip() or "cam_001"
        self._config.camera_id = self._camera_id
        ensure_lane(self._config)

        self._config.speed.enabled = self.speed_enabled.isChecked()
        self._config.speed.speed_limit_kmh = float(self.speed_limit.value())
        self._config.speed.real_distance_m = float(self.real_distance.value())
        self._config.speed.meter_per_pixel = float(self.meter_per_pixel.value())

        self._config.parking.enabled = self.parking_enabled.isChecked()
        self._config.parking.min_stay_sec = float(self.parking_min_stay.value())
        self._config.parking.max_displacement_px = float(self.parking_max_disp.value())

        self._config.reverse.enabled = self.reverse_enabled.isChecked()
        self._config.reverse.cos_threshold = float(self.reverse_cos.value())
        self._config.reverse.lanes[0].min_duration_sec = float(self.reverse_min_duration.value())
        self._config.reverse.lanes[0].min_displacement_px = float(self.reverse_min_disp.value())

    def _apply_config_to_form(self) -> None:
        ensure_lane(self._config)
        self.camera_id_input.setText(self._config.camera_id)
        self.speed_enabled.setChecked(bool(self._config.speed.enabled))
        self.speed_limit.setValue(float(self._config.speed.speed_limit_kmh))
        self.real_distance.setValue(float(self._config.speed.real_distance_m))
        self.meter_per_pixel.setValue(float(self._config.speed.meter_per_pixel))

        self.parking_enabled.setChecked(bool(self._config.parking.enabled))
        self.parking_min_stay.setValue(float(self._config.parking.min_stay_sec))
        self.parking_max_disp.setValue(float(self._config.parking.max_displacement_px))

        self.reverse_enabled.setChecked(bool(self._config.reverse.enabled))
        self.reverse_cos.setValue(float(self._config.reverse.cos_threshold))
        self.reverse_min_duration.setValue(float(self._config.reverse.lanes[0].min_duration_sec))
        self.reverse_min_disp.setValue(float(self._config.reverse.lanes[0].min_displacement_px))

    def _sync_enable_state(self) -> None:
        speed_on = self.speed_enabled.isChecked()
        parking_on = self.parking_enabled.isChecked()
        reverse_on = self.reverse_enabled.isChecked()

        self.speed_limit.setEnabled(speed_on)
        self.real_distance.setEnabled(speed_on)
        self.meter_per_pixel.setEnabled(speed_on)

        self.parking_min_stay.setEnabled(parking_on)
        self.parking_max_disp.setEnabled(parking_on)

        self.reverse_cos.setEnabled(reverse_on)
        self.reverse_min_duration.setEnabled(reverse_on)
        self.reverse_min_disp.setEnabled(reverse_on)

        self._enforce_tool_availability()
        self.canvas.update()

    def _on_rule_toggle(self) -> None:
        self._save_form_to_config()
        self._sync_enable_state()

    def _pick_source_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            str(BASE_DIR),
            "Video Files (*.mp4 *.avi *.mov *.mkv *.flv *.wmv);;All Files (*.*)",
        )
        if path:
            self.source_input.setText(path)

    def _start_task(self) -> None:
        source = self.source_input.text().strip()
        if not source:
            QMessageBox.warning(self, "输入缺失", "请输入本地路径或流地址。")
            return
        self._save_form_to_config()
        self.processor.start(source=source, camera_id=self._camera_id, config=self._config)

    def _stop_task(self) -> None:
        self.processor.stop()

    def _load_config(self) -> None:
        camera_id = self.camera_id_input.text().strip() or "cam_001"
        loaded = self.config_store.load(camera_id)
        self._config = loaded if loaded else make_default_config(camera_id)
        self._apply_config_to_form()
        self._sync_enable_state()
        self.canvas.clear_draft()
        self.canvas.update()

    def _save_config(self) -> None:
        self._save_form_to_config()
        path = self.config_store.save(self._config)
        self.processor.update_config(self._config)
        QMessageBox.information(self, "保存成功", f"配置已保存到:\n{path}")

    def _clear_all_shapes(self) -> None:
        camera_id = self.camera_id_input.text().strip() or "cam_001"
        self._config = make_default_config(camera_id)
        self._apply_config_to_form()
        self._sync_enable_state()
        self.canvas.clear_draft()
        self.canvas.update()

    def _refresh_frame(self) -> None:
        frame = self.processor.get_latest_frame()
        if frame:
            self.canvas.set_frame_bytes(frame)

    def _refresh_status_and_events(self) -> None:
        status = self.processor.get_status()
        running = "运行中" if status.get("running") else "未运行"
        message = status.get("status_message", "idle")
        backend = status.get("tracker_backend", "unknown")
        device = status.get("tracker_device", "unknown")
        self.status_label.setText(f"状态: {running} | {message} | {backend}/{device}")

        events = self.processor.get_events(limit=40)
        self.event_list.clear()
        for event in events:
            text = (
                f"{event.get('timestamp', '-')}"
                f" | {event.get('event_type', '-')}"
                f" | ID {event.get('track_id', '-')}\n"
                f"{event.get('message', '')}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, str(event.get("snapshot_path", "") or ""))
            self.event_list.addItem(item)

    def _open_event_snapshot(self, item: QListWidgetItem) -> None:
        snapshot = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not snapshot:
            return
        path = Path(snapshot)
        if not path.exists():
            QMessageBox.warning(self, "文件不存在", f"截图未找到:\n{snapshot}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))


def run_qt_app() -> int:
    app = QApplication(sys.argv)
    window = MonitorMainWindow()
    window.show()
    return app.exec()
