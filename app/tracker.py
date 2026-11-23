from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from app.detector import Detection


@dataclass
class Track:
    track_id: int
    bbox: tuple[int, int, int, int]
    centroid: tuple[float, float]
    confidence: float
    label: str
    history: deque[tuple[float, float, float]] = field(default_factory=lambda: deque(maxlen=120))
    missed_frames: int = 0
    last_seen: float = 0.0
    alert_tags: set[str] = field(default_factory=set)


class CentroidTracker:
    def __init__(self, max_distance: float = 60.0, max_missed_frames: int = 20) -> None:
        self.max_distance = max_distance
        self.max_missed_frames = max_missed_frames
        self._next_track_id = 1
        self._tracks: dict[int, Track] = {}

    @staticmethod
    def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
        dx = a[0] - b[0]
        dy = a[1] - b[1]
        return (dx * dx + dy * dy) ** 0.5

    def update(self, detections: list[Detection], timestamp: float) -> list[Track]:
        updated_track_ids: set[int] = set()
        track_ids = list(self._tracks.keys())
        unmatched_detections = set(range(len(detections)))

        pairs: list[tuple[float, int, int]] = []
        for tid in track_ids:
            track = self._tracks[tid]
            for didx, det in enumerate(detections):
                dist = self._distance(track.centroid, det.centroid)
                pairs.append((dist, tid, didx))

        pairs.sort(key=lambda x: x[0])
        used_tracks: set[int] = set()
        used_dets: set[int] = set()

        for dist, tid, didx in pairs:
            if dist > self.max_distance:
                continue
            if tid in used_tracks or didx in used_dets:
                continue
            track = self._tracks[tid]
            det = detections[didx]
            track.bbox = det.bbox
            track.centroid = det.centroid
            track.confidence = det.confidence
            track.label = det.label
            track.missed_frames = 0
            track.last_seen = timestamp
            track.history.append((timestamp, det.centroid[0], det.centroid[1]))
            used_tracks.add(tid)
            used_dets.add(didx)
            updated_track_ids.add(tid)
            unmatched_detections.discard(didx)

        for didx in unmatched_detections:
            det = detections[didx]
            tid = self._next_track_id
            self._next_track_id += 1
            track = Track(
                track_id=tid,
                bbox=det.bbox,
                centroid=det.centroid,
                confidence=det.confidence,
                label=det.label,
                last_seen=timestamp,
            )
            track.history.append((timestamp, det.centroid[0], det.centroid[1]))
            self._tracks[tid] = track
            updated_track_ids.add(tid)

        stale_track_ids: list[int] = []
        for tid, track in self._tracks.items():
            if tid in updated_track_ids:
                continue
            track.missed_frames += 1
            if track.missed_frames > self.max_missed_frames:
                stale_track_ids.append(tid)

        for tid in stale_track_ids:
            self._tracks.pop(tid, None)

        return [self._tracks[tid] for tid in updated_track_ids if tid in self._tracks]

    def all_tracks(self) -> list[Track]:
        return list(self._tracks.values())

    def reset(self) -> None:
        self._tracks.clear()
        self._next_track_id = 1
