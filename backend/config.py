from __future__ import annotations

import math
import re
from pathlib import Path
import json
import threading

from pydantic import model_validator

from app.config_store import ConfigStore
from app.schemas import MonitorConfig


class WebConfig(MonitorConfig):
    @model_validator(mode='before')
    @classmethod
    def check_coordinates(cls, value):
        if not isinstance(value, dict):
            return value
        speed, parking, reverse = (value.get(k, {}) for k in ['speed', 'parking', 'reverse'])
        if not all(isinstance(section, dict) for section in [speed, parking, reverse]):
            raise ValueError('规则配置必须为对象')
        lanes = reverse.get('lanes', [])
        rois = parking.get('rois', [])
        if not isinstance(lanes, list) or not isinstance(rois, list) or not all(isinstance(lane, dict) for lane in lanes):
            raise ValueError('区域和车道必须为列表')
        shapes = [speed.get('line_a', []), speed.get('line_b', []), *rois]
        shapes.extend(lane.get(field, []) for lane in lanes for field in ['polygon', 'legal_direction'])
        for shape in shapes:
            if not isinstance(shape, list) or not all(isinstance(point, (list, tuple)) and len(point) == 2 for point in shape):
                raise ValueError('标注点必须为 [x,y] 坐标')
            for point in shape:
                if not all(isinstance(v, (int,float)) and not isinstance(v, bool) and math.isfinite(v) for v in point):
                    raise ValueError('标注坐标必须为有限数字')
        return value


def camera_id(value: str):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValueError('摄像头 ID 只能包含 1–64 位字母、数字、下划线或短横线')
    return value


def validate_config(config: MonitorConfig):
    camera_id(config.camera_id)
    ranges = [
        ('限速', config.speed.speed_limit_kmh, 1, 300),
        ('标定距离', config.speed.real_distance_m, .1, 1000),
        ('米/像素', config.speed.meter_per_pixel, .00001, 10),
        ('违停时长', config.parking.min_stay_sec, 1, 86400),
        ('停车位移阈值', config.parking.max_displacement_px, 0, 960),
        ('方向余弦阈值', config.reverse.cos_threshold, -1, 0),
    ]
    for lane in config.reverse.lanes:
        ranges.extend([('逆行持续时长', lane.min_duration_sec, .1, 3600),
                       ('逆行位移阈值', lane.min_displacement_px, 1, 1100)])
    for name, value, lower, upper in ranges:
        if not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError(f'{name}必须在 {lower}–{upper} 之间')
    polygons = [*config.parking.rois, *(lane.polygon for lane in config.reverse.lanes)]
    if any(poly and len(poly) < 3 for poly in polygons):
        raise ValueError('区域至少需要三个点')
    shapes = [config.speed.line_a, config.speed.line_b, *polygons,
              *(lane.legal_direction for lane in config.reverse.lanes)]
    for shape in shapes:
        for point in shape:
            if len(point) != 2 or not all(math.isfinite(v) for v in point) or not (0 <= point[0] <= 960 and 0 <= point[1] <= 540):
                raise ValueError('标注坐标必须位于 960×540 视频画面内')
    return config


class Configs:
    def __init__(self, root: Path):
        self.root = root
        self.store = ConfigStore(root / 'data' / 'configs')
        self.lock = threading.RLock()

    def load(self, name):
        with self.lock:
            return self.store.load(camera_id(name)) or MonitorConfig(camera_id=name)

    def save(self, config):
        validate_config(config)
        with self.lock:
            path = self.root / 'data' / 'configs' / (config.camera_id + '.json')
            temp = path.with_suffix('.web.tmp')
            temp.write_text(json.dumps(config.model_dump(), ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(path)

    def names(self):
        return sorted({p.stem for p in (self.root / 'data' / 'configs').glob('*.json')
                       if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', p.stem)} | {'cam_001'})
