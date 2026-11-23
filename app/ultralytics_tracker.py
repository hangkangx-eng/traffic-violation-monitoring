from __future__ import annotations

from collections import deque
from typing import Iterable

import numpy as np

from app.tracker import Track


class UltralyticsTracker:
    """Wrap Ultralytics `track()` and expose project Track objects."""

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        tracker_config: str = "bytetrack.yaml",
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        vehicle_class_ids: Iterable[int] | None = None,
        max_missed_frames: int = 30,
        device: str = "auto",
    ) -> None:
        self.model_path = model_path
        self.tracker_config = tracker_config
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.vehicle_class_ids = set(vehicle_class_ids or {2, 3, 5, 7})
        self.max_missed_frames = max_missed_frames
        self.device = self._resolve_device(device)

        self._tracks: dict[int, Track] = {}
        self._model = self._load_model(self.model_path)
        self._model.to(self.device)
        self._class_names = self._model.names if hasattr(self._model, "names") else {}

    @staticmethod
    def _load_model(model_path: str):
        try:
            from ultralytics import YOLO  # type: ignore
        except Exception as exc:
            raise RuntimeError("Ultralytics is unavailable. Run: pip install ultralytics") from exc

        try:
            return YOLO(model_path)
        except Exception as exc:
            raise RuntimeError(f"Failed to load YOLO model: {model_path}") from exc

    def reset(self) -> None:
        # Recreate model to reset internal tracker states between tasks.
        self._tracks.clear()
        self._model = self._load_model(self.model_path)
        self._model.to(self.device)
        self._class_names = self._model.names if hasattr(self._model, "names") else {}

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device and device.lower() != "auto":
            return device
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                return "cuda:0"
        except Exception:
            pass
        return "cpu"

    def _track_once(self, frame: np.ndarray):
        results = self._model.track(
            frame,
            persist=True,
            tracker=self.tracker_config,
            conf=self.conf_threshold,
            iou=self.iou_threshold,
            classes=sorted(self.vehicle_class_ids),
            device=self.device,
            verbose=False,
        )
        return results

    def update(self, frame: np.ndarray, timestamp: float) -> list[Track]:
        try:
            results = self._track_once(frame)
        except Exception:
            # If GPU inference fails at runtime, fallback to CPU once.
            if str(self.device).startswith("cuda"):
                self.device = "cpu"
                self._model.to(self.device)
                results = self._track_once(frame)
            else:
                raise

        if not results:
            self._mark_missed(set())
            return []

        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            self._mark_missed(set())
            return []

        if boxes.id is None:
            self._mark_missed(set())
            return []

        xyxy = boxes.xyxy.cpu().numpy()
        conf = boxes.conf.cpu().numpy() if boxes.conf is not None else np.ones((len(xyxy),))
        cls_arr = boxes.cls.cpu().numpy() if boxes.cls is not None else np.zeros((len(xyxy),))
        # Convert to builtin Python int to avoid downstream serialization issues.
        ids = boxes.id.cpu().numpy().astype(np.int64).tolist()

        updated_tracks: list[Track] = []
        active_ids: set[int] = set()
        for i, track_id in enumerate(ids):
            track_id = int(track_id)
            class_id = int(cls_arr[i])
            if class_id not in self.vehicle_class_ids:
                continue

            x1, y1, x2, y2 = [int(v) for v in xyxy[i].tolist()]
            # Use bottom-center as vehicle anchor point.
            centroid = ((x1 + x2) / 2.0, float(y2))
            label = str(self._class_names.get(class_id, f"cls_{class_id}"))
            confidence = float(conf[i])

            track = self._tracks.get(track_id)
            if track is None:
                track = Track(
                    track_id=track_id,
                    bbox=(x1, y1, x2, y2),
                    centroid=centroid,
                    confidence=confidence,
                    label=label,
                    history=deque(maxlen=120),
                    missed_frames=0,
                    last_seen=timestamp,
                )
                self._tracks[track_id] = track

            track.bbox = (x1, y1, x2, y2)
            track.centroid = centroid
            track.confidence = confidence
            track.label = label
            track.last_seen = timestamp
            track.missed_frames = 0
            track.history.append((timestamp, centroid[0], centroid[1]))
            updated_tracks.append(track)
            active_ids.add(track_id)

        self._mark_missed(active_ids)
        return updated_tracks

    def _mark_missed(self, active_ids: set[int]) -> None:
        stale_ids: list[int] = []
        for track_id, track in self._tracks.items():
            if track_id in active_ids:
                continue
            track.missed_frames += 1
            if track.missed_frames > self.max_missed_frames:
                stale_ids.append(track_id)

        for track_id in stale_ids:
            self._tracks.pop(track_id, None)
