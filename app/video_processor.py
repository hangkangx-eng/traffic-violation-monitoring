from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from app.detector import VehicleDetector
from app.rules import RuleEngine
from app.schemas import MonitorConfig
from app.tracker import CentroidTracker, Track
from app.ultralytics_tracker import UltralyticsTracker


class VideoProcessor:
    def __init__(self, snapshot_dir: Path, frame_size: tuple[int, int] = (960, 540), *, strict_yolo: bool = False) -> None:
        self.snapshot_dir = snapshot_dir
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.frame_width, self.frame_height = frame_size

        self._use_ultralytics_tracker = True
        self._tracker_init_error = ""
        self.detector: VehicleDetector | None = None
        try:
            self.tracker = UltralyticsTracker(
                model_path="yolo11s.pt",
                tracker_config="bytetrack.yaml",
                device="auto",
            )
        except Exception as exc:
            if strict_yolo:
                raise RuntimeError(f"YOLOv11 initialization failed: {exc}") from exc
            self._use_ultralytics_tracker = False
            self._tracker_init_error = str(exc)
            self.detector = VehicleDetector()
            self.tracker = CentroidTracker()
        self.rules = RuleEngine()

        self._running = False
        self._source = ""
        self._camera_id = "cam_001"
        self._status_message = "idle"
        self._config = MonitorConfig()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._latest_frame_jpeg: bytes | None = None
        self._latest_frame_ts: float | None = None
        self._events: deque[dict] = deque(maxlen=500)

        self._lock = threading.Lock()

    def start(self, source: str, camera_id: str, config: MonitorConfig) -> None:
        self.stop()
        with self._lock:
            self._source = source
            self._camera_id = camera_id
            self._config = config
            self._status_message = "starting"
            self._events.clear()
            self._latest_frame_jpeg = None
            self._latest_frame_ts = None
            self._running = True
        self.tracker.reset()
        self.rules.reset()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        thread = self._thread
        if thread and thread.is_alive():
            self._stop_event.set()
            thread.join(timeout=3.0)
        with self._lock:
            self._running = False
            if self._status_message not in {"source_open_failed", "stream_ended"}:
                self._status_message = "stopped"

    def update_config(self, config: MonitorConfig) -> None:
        with self._lock:
            self._config = config

    def get_latest_frame(self) -> bytes | None:
        with self._lock:
            return self._latest_frame_jpeg

    def get_events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            if not self._events:
                return []
            size = min(limit, len(self._events))
            items = list(self._events)[-size:]
        items.reverse()
        return [self._to_builtin(item) for item in items]

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running": self._running and not self._stop_event.is_set(),
                "source": self._source,
                "camera_id": self._camera_id,
                "status_message": self._status_message,
                "tracker_backend": "ultralytics" if self._use_ultralytics_tracker else "legacy",
                "tracker_device": getattr(self.tracker, "device", "legacy"),
                "speed_mode": self._config.speed.mode,
                "frame_width": self.frame_width,
                "frame_height": self.frame_height,
                "latest_frame_ts": self._latest_frame_ts,
            }

    def _run(self) -> None:
        with self._lock:
            source = self._source
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            self._push_system_event("source_open_failed", f"无法打开视频源: {source}")
            with self._lock:
                self._running = False
                self._status_message = "source_open_failed"
            return

        is_local_file = Path(source).exists()
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0 or fps > 240:
            fps = 25.0
        frame_interval = 1.0 / fps

        with self._lock:
            self._status_message = "running"
        if not self._use_ultralytics_tracker and self._tracker_init_error:
            self._push_system_event(
                "tracker_fallback",
                f"Ultralytics tracker unavailable, fallback enabled: {self._tracker_init_error}",
            )

        while not self._stop_event.is_set():
            loop_start = time.time()
            ok, frame = cap.read()
            if not ok:
                if is_local_file:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                self._push_system_event("stream_ended", "视频流结束或中断。")
                with self._lock:
                    self._status_message = "stream_ended"
                break

            frame = cv2.resize(frame, (self.frame_width, self.frame_height))
            ts = time.time()
            if self._use_ultralytics_tracker:
                try:
                    tracks = self.tracker.update(frame, ts)
                except Exception as exc:
                    self._push_system_event("tracker_error", f"Tracker failed: {exc}")
                    with self._lock:
                        self._status_message = "tracker_error"
                    break
            else:
                assert self.detector is not None
                detections = self.detector.detect(frame)
                tracks = self.tracker.update(detections, ts)
            config = self._get_config_copy()

            events: list[dict] = []
            for track in tracks:
                events.extend(self.rules.evaluate(track, ts, config))

            self._draw_config(frame, config)
            track_map: dict[int, Track] = {t.track_id: t for t in tracks}
            speed_items: list[tuple[int, float]] = []
            for track in tracks:
                metrics = self.rules.get_track_metrics(track.track_id)
                self._draw_track(frame, track, metrics, config, ts)
                line_speed = metrics.get("speed_line_kmh")
                if line_speed is not None:
                    speed_items.append((track.track_id, float(line_speed)))
            self._draw_speed_summary(frame, speed_items)

            for event in events:
                self._push_event(event, frame, track_map)

            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok:
                with self._lock:
                    self._latest_frame_jpeg = encoded.tobytes()
                    self._latest_frame_ts = ts

            elapsed = time.time() - loop_start
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

        cap.release()
        with self._lock:
            self._running = False
            if self._status_message == "running":
                self._status_message = "stopped"

    def _get_config_copy(self) -> MonitorConfig:
        with self._lock:
            return MonitorConfig(**self._config.model_dump())

    def _push_system_event(self, event_type: str, message: str) -> None:
        event = {
            "id": uuid.uuid4().hex[:12],
            "event_type": event_type,
            "track_id": -1,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "message": message,
            "meta": {},
            "snapshot_path": "",
        }
        with self._lock:
            self._events.append(event)

    def _push_event(self, event: dict, frame: np.ndarray, track_map: dict[int, Track]) -> None:
        event = self._to_builtin(event)
        event_id = uuid.uuid4().hex[:12]
        track_id = int(event.get("track_id", -1))
        track = track_map.get(track_id)
        snapshot_path = self._save_snapshot(event_id, frame, track.bbox if track else None)
        new_event = {
            "id": event_id,
            "event_type": event["event_type"],
            "track_id": track_id,
            "timestamp": event["timestamp"],
            "message": event["message"],
            "meta": self._to_builtin(event.get("meta", {})),
            "snapshot_path": str(snapshot_path),
        }
        with self._lock:
            self._events.append(new_event)

    @staticmethod
    def _to_builtin(value):
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {k: VideoProcessor._to_builtin(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set, deque)):
            return [VideoProcessor._to_builtin(v) for v in value]
        return value

    def _save_snapshot(
        self, event_id: str, frame: np.ndarray, bbox: tuple[int, int, int, int] | None
    ) -> Path:
        h, w = frame.shape[:2]
        crop = frame
        if bbox:
            x1, y1, x2, y2 = bbox
            pad = 16
            sx1 = max(0, x1 - pad)
            sy1 = max(0, y1 - pad)
            sx2 = min(w, x2 + pad)
            sy2 = min(h, y2 + pad)
            if sx2 > sx1 and sy2 > sy1:
                crop = frame[sy1:sy2, sx1:sx2]

        snapshot_path = self.snapshot_dir / f"{event_id}.jpg"
        cv2.imwrite(str(snapshot_path), crop)
        return snapshot_path

    @staticmethod
    def _draw_polygon(frame: np.ndarray, polygon: list[list[float]], color: tuple[int, int, int]) -> None:
        if len(polygon) < 2:
            return
        pts = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(frame, [pts], isClosed=len(polygon) >= 3, color=color, thickness=2)

    @staticmethod
    def _draw_line(frame: np.ndarray, line: list[list[float]], color: tuple[int, int, int]) -> None:
        if len(line) != 2:
            return
        p1 = (int(line[0][0]), int(line[0][1]))
        p2 = (int(line[1][0]), int(line[1][1]))
        cv2.line(frame, p1, p2, color, 2)

    def _draw_config(self, frame: np.ndarray, config: MonitorConfig) -> None:
        if config.speed.enabled:
            color_a = (0, 255, 255)  # yellow
            color_b = (255, 80, 80)  # blue-ish
            self._draw_line(frame, config.speed.line_a, color_a)
            self._draw_line(frame, config.speed.line_b, color_b)
            if len(config.speed.line_a) == 2:
                p = (int(config.speed.line_a[0][0]), int(config.speed.line_a[0][1]))
                cv2.putText(frame, "A", (p[0] + 6, p[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_a, 2)
            if len(config.speed.line_b) == 2:
                p = (int(config.speed.line_b[0][0]), int(config.speed.line_b[0][1]))
                cv2.putText(frame, "B", (p[0] + 6, p[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_b, 2)

        if config.parking.enabled:
            for roi in config.parking.rois:
                self._draw_polygon(frame, roi, (0, 140, 255))

        if config.reverse.enabled:
            for lane in config.reverse.lanes:
                self._draw_polygon(frame, lane.polygon, (255, 120, 0))
                self._draw_line(frame, lane.legal_direction, (255, 200, 0))
                if len(lane.legal_direction) == 2:
                    p1 = (int(lane.legal_direction[0][0]), int(lane.legal_direction[0][1]))
                    p2 = (int(lane.legal_direction[1][0]), int(lane.legal_direction[1][1]))
                    cv2.arrowedLine(frame, p1, p2, (255, 200, 0), 2, tipLength=0.2)

    @staticmethod
    def _put_text_with_bg(
        frame: np.ndarray,
        text: str,
        org: tuple[int, int],
        scale: float,
        fg: tuple[int, int, int],
        bg: tuple[int, int, int] = (0, 0, 0),
    ) -> None:
        thickness = 2
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        x, y = org
        x1 = max(0, x - 2)
        y1 = max(0, y - th - 4)
        x2 = min(frame.shape[1] - 1, x + tw + 4)
        y2 = min(frame.shape[0] - 1, y + baseline + 2)
        cv2.rectangle(frame, (x1, y1), (x2, y2), bg, -1)
        cv2.putText(
            frame,
            text,
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            fg,
            thickness,
            cv2.LINE_AA,
        )

    @staticmethod
    def _draw_speed_summary(frame: np.ndarray, speed_items: list[tuple[int, float]]) -> None:
        if not speed_items:
            return
        speed_items = sorted(speed_items, key=lambda x: x[0])[:6]
        y = 52
        VideoProcessor._put_text_with_bg(frame, "AB Interval Speed", (12, y), 0.58, (255, 255, 255))
        y += 24
        for track_id, speed in speed_items:
            VideoProcessor._put_text_with_bg(
                frame, f"ID {track_id}: {speed:.1f} km/h", (12, y), 0.52, (0, 255, 255)
            )
            y += 22

    @staticmethod
    def _draw_track(
        frame: np.ndarray, track: Track, metrics: dict, _config: MonitorConfig, ts: float
    ) -> None:
        x1, y1, x2, y2 = track.bbox
        cv2.rectangle(frame, (x1, y1), (x2, y2), (50, 220, 50), 2)
        label = f"ID {track.track_id}"

        next_row = min(frame.shape[0] - 8, y2 + 18)
        rt_speed = metrics.get("speed_rt_kmh")
        rt_ts = metrics.get("speed_rt_last_ts")
        rt_active = rt_speed is not None and rt_ts is not None and (ts - float(rt_ts)) <= 1.0
        if rt_active:
            VideoProcessor._put_text_with_bg(
                frame,
                f"RT {float(rt_speed):.1f} km/h",
                (x1, next_row),
                0.55,
                (0, 255, 255),
            )
            next_row = min(frame.shape[0] - 8, next_row + 20)

        cos_theta = metrics.get("reverse_cos")
        if cos_theta is not None:
            label += f" cos:{cos_theta:.2f}"
        reverse_lane = metrics.get("reverse_lane")
        if reverse_lane:
            label += f" lane:{reverse_lane}"
        VideoProcessor._put_text_with_bg(
            frame,
            label,
            (x1, max(18, y1 - 6)),
            0.5,
            (40, 255, 40),
        )
