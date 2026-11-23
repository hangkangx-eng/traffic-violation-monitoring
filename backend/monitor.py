from __future__ import annotations

import logging
from pathlib import Path
import threading
import time
import uuid

import cv2
from app.video_processor import VideoProcessor

log = logging.getLogger(__name__)


class WebVideoProcessor(VideoProcessor):
    """Reuse the desktop detector, tracker, rules and rendering with Web telemetry.

    File timestamps advance with the video rather than inference wall time, so
    CPU speed does not change calculated vehicle speed or parking duration.
    Local files stop at EOF instead of looping trajectories over a discontinuity.
    """
    def __init__(self, snapshots, on_event, on_end):
        super().__init__(snapshot_dir=snapshots, strict_yolo=True)
        self.on_event = on_event
        self.on_end = on_end
        self.telemetry = {'processed_frames': 0, 'active_tracks': 0, 'fps': 0,
                          'position_sec': 0, 'duration_sec': 0, 'vehicle_count': 0}
        self._telemetry_lock = threading.Lock()

    def get_status(self):
        status = super().get_status()
        with self._telemetry_lock:
            status.update(self.telemetry)
        if not status['running']:
            status['active_tracks'] = 0
        return status

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=20)
            if self._thread.is_alive():
                raise RuntimeError('视频线程仍在退出，请稍后重试；未启动重复检测线程')
        super().stop()

    def _push_event(self, event, frame, track_map):
        super()._push_event(event, frame, track_map)
        self.on_event(self.get_events(1)[0])

    def _push_system_event(self, event_type, message):
        super()._push_system_event(event_type, message)
        self.on_event(self.get_events(1)[0])

    def _run(self):
        cap = None
        try:
            cap = cv2.VideoCapture(self._source)
            if not cap.isOpened():
                raise RuntimeError('无法打开视频源，请检查路径、格式或 RTSP 连接')
            local = Path(self._source).is_file()
            fps = cap.get(cv2.CAP_PROP_FPS)
            fps = fps if 0 < fps <= 240 else 25
            total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            started = time.monotonic()
            epoch = time.time()
            seen_ids = set()
            index = 0
            with self._lock:
                self._status_message = 'running'
            while not self._stop_event.is_set():
                tick = time.monotonic()
                ok, frame = cap.read()
                if not ok:
                    with self._lock:
                        self._status_message = 'completed' if local else 'stream_ended'
                    if not local:
                        self._push_system_event('stream_ended', '视频流结束或中断')
                    break
                index += 1
                position = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                if local and (not position or position < (index - 2) / fps):
                    position = (index - 1) / fps
                ts = epoch + position if local else time.time()
                frame = cv2.resize(frame, (self.frame_width, self.frame_height))
                tracks = self.tracker.update(frame, ts)
                config = self._get_config_copy()
                events = []
                for track in tracks:
                    events.extend(self.rules.evaluate(track, ts, config))
                    seen_ids.add(track.track_id)
                self._draw_config(frame, config)
                speeds = []
                for track in tracks:
                    metrics = self.rules.get_track_metrics(track.track_id)
                    self._draw_track(frame, track, metrics, config, ts)
                    if metrics.get('speed_line_kmh') is not None:
                        speeds.append((track.track_id, metrics['speed_line_kmh']))
                self._draw_speed_summary(frame, speeds)
                track_map = {track.track_id: track for track in tracks}
                for event in events:
                    self._push_event(event, frame, track_map)
                ok, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if ok:
                    with self._lock:
                        self._latest_frame_jpeg = jpeg.tobytes()
                        self._latest_frame_ts = time.time()
                with self._telemetry_lock:
                    self.telemetry = {'processed_frames': index, 'active_tracks': len(tracks),
                                      'fps': round(index / max(.001, time.monotonic() - started), 1),
                                      'position_sec': round(position, 2) if local else 0,
                                      'duration_sec': round(total_frames / fps, 2) if local else 0,
                                      'vehicle_count': len(seen_ids)}
                self._stop_event.wait(max(0, 1 / fps - (time.monotonic() - tick)))
        except Exception as exc:
            log.exception('Video processing failed')
            with self._lock:
                self._status_message = 'error'
            self._push_system_event('processor_error', str(exc))
        finally:
            if cap is not None:
                cap.release()
            with self._lock:
                self._running = False
                if self._status_message == 'running':
                    self._status_message = 'stopped'
            self.on_end(self._status_message)


class Monitor:
    def __init__(self, root, store):
        self.root = root
        self.store = store
        self.processor = None
        self.session_id = None
        self._control_lock = threading.Lock()

    def start(self, source, camera_id, config):
        with self._control_lock:
            if self.processor and self.processor.get_status()['running']:
                raise ValueError('已有监测任务运行中，请先停止当前任务')
            if self.processor:
                self.processor.stop()
            if not (self.root / 'yolo11s.pt').is_file():
                raise ValueError('项目根目录缺少 yolo11s.pt 模型文件')
            source = source.strip().strip('"')
            if source.lower().startswith(('rtsp://', 'rtsps://')):
                pass
            elif not Path(source).is_file():
                raise ValueError('本地视频文件不存在；请选择已有视频或上传文件')
            session_id = uuid.uuid4().hex
            callback = lambda event: self.store.add_event(event, session_id, camera_id)
            on_end = lambda status: self.store.end_session(session_id, status)
            # Initialization is deliberately lazy: the dashboard works before YOLO is loaded.
            processor = WebVideoProcessor(self.root / 'data' / 'web' / 'snapshots', callback, on_end)
            if not processor._use_ultralytics_tracker:
                raise ValueError('YOLO 初始化失败：' + processor._tracker_init_error)
            self.store.start_session(session_id, camera_id, source)
            self.processor = processor
            self.session_id = session_id
            try:
                processor.start(source, camera_id, config)
            except Exception:
                self.store.end_session(session_id, 'error')
                with processor._lock:
                    processor._running = False
                    processor._status_message = 'error'
                raise
            return self.status()

    def stop(self):
        with self._control_lock:
            if self.processor:
                self.processor.stop()
            return self.status()

    def status(self):
        status = self.processor.get_status() if self.processor else {
            'running': False, 'status_message': 'idle', 'tracker_backend': 'not_loaded',
            'tracker_device': 'not_loaded', 'processed_frames': 0, 'active_tracks': 0,
            'fps': 0, 'vehicle_count': 0, 'position_sec': 0, 'duration_sec': 0,
            'source': '', 'camera_id': 'cam_001', 'latest_frame_ts': None,
            'frame_width': 960, 'frame_height': 540}
        return dict(status, session_id=self.session_id)

    def frame(self):
        return self.processor.get_latest_frame() if self.processor else None

    def update_config(self, config):
        with self._control_lock:
            if self.processor and self.processor.get_status()['camera_id'] == config.camera_id:
                self.processor.update_config(config)
