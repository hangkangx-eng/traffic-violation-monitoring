from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Point = list[float]
Line = list[Point]
Polygon = list[Point]


class SpeedConfig(BaseModel):
    enabled: bool = True
    mode: Literal["hybrid_ab_realtime"] = "hybrid_ab_realtime"
    speed_limit_kmh: float = 60.0
    line_a: Line = Field(default_factory=list)
    line_b: Line = Field(default_factory=list)
    real_distance_m: float = 15.0
    meter_per_pixel: float = 0.05

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, _value: Any) -> str:
        # Keep compatibility with historical configs that stored selectable modes.
        return "hybrid_ab_realtime"

    @field_validator("line_a", "line_b")
    @classmethod
    def _validate_line(cls, value: Line) -> Line:
        if value and len(value) != 2:
            raise ValueError("Line must contain exactly two points.")
        # Force speed lines to horizontal to match the product requirement.
        if value and len(value) == 2:
            x1, y1 = float(value[0][0]), float(value[0][1])
            x2 = float(value[1][0])
            return [[x1, y1], [x2, y1]]
        return value


class ParkingConfig(BaseModel):
    enabled: bool = True
    rois: list[Polygon] = Field(default_factory=list)
    min_stay_sec: float = 60.0
    max_displacement_px: float = 20.0


class LaneConfig(BaseModel):
    name: str = "lane_1"
    polygon: Polygon = Field(default_factory=list)
    legal_direction: Line = Field(default_factory=list)
    min_duration_sec: float = 2.0
    min_displacement_px: float = 40.0

    @field_validator("legal_direction")
    @classmethod
    def _validate_direction_line(cls, value: Line) -> Line:
        if value and len(value) != 2:
            raise ValueError("legal_direction must contain exactly two points.")
        return value


class ReverseConfig(BaseModel):
    enabled: bool = True
    lanes: list[LaneConfig] = Field(default_factory=list)
    cos_threshold: float = -0.3


class MonitorConfig(BaseModel):
    camera_id: str = "cam_001"
    speed: SpeedConfig = Field(default_factory=SpeedConfig)
    parking: ParkingConfig = Field(default_factory=ParkingConfig)
    reverse: ReverseConfig = Field(default_factory=ReverseConfig)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class StartTaskRequest(BaseModel):
    camera_id: str = "cam_001"
    source: str = Field(..., description="Local file path or stream URL.")
    config: MonitorConfig | None = None


class SaveConfigRequest(BaseModel):
    config: MonitorConfig
