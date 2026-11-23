from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Detection:
    bbox: tuple[int, int, int, int]
    confidence: float
    label: str

    @property
    def centroid(self) -> tuple[float, float]:
        # Use bottom-center as vehicle anchor point.
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, float(y2))


class VehicleDetector:
    def __init__(self, model_path: str = "yolov8s.pt", conf_threshold: float = 0.25) -> None:
        self.conf_threshold = conf_threshold
        self.vehicle_class_ids = {2, 3, 5, 7}  # COCO: car, motorcycle, bus, truck
        self._model = None
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=25)

        try:
            from ultralytics import YOLO  # type: ignore

            self._model = YOLO(model_path)
        except Exception:
            self._model = None

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if self._model is not None:
            detections = self._detect_with_yolo(frame)
            if detections:
                return detections
        return self._detect_with_motion(frame)

    def _detect_with_yolo(self, frame: np.ndarray) -> list[Detection]:
        detections: list[Detection] = []
        results = self._model(frame, verbose=False)
        if not results:
            return detections

        boxes = results[0].boxes
        if boxes is None:
            return detections

        for box in boxes:
            cls_id = int(box.cls[0].item())
            conf = float(box.conf[0].item())
            if cls_id not in self.vehicle_class_ids or conf < self.conf_threshold:
                continue
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
            detections.append(Detection(bbox=(x1, y1, x2, y2), confidence=conf, label="vehicle"))
        return detections

    def _detect_with_motion(self, frame: np.ndarray) -> list[Detection]:
        detections: list[Detection] = []
        mask = self._bg_subtractor.apply(frame)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 1200:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            if h <= 0 or w <= 0:
                continue
            ratio = w / float(h)
            if ratio < 0.4 or ratio > 4.0:
                continue
            detections.append(
                Detection(
                    bbox=(x, y, x + w, y + h),
                    confidence=0.3,
                    label="vehicle_motion",
                )
            )
        return detections
