from __future__ import annotations

import math
from datetime import datetime

from app.geometry import cosine_similarity, point_in_polygon, vector_norm
from app.schemas import LaneConfig, MonitorConfig, SpeedConfig
from app.tracker import Track


class RuleEngine:
    def __init__(self) -> None:
        self._speed_line_state: dict[int, dict] = {}
        self._speed_emit_state: dict[int, float] = {}
        self._parking_state: dict[int, dict] = {}
        self._reverse_state: dict[int, dict] = {}
        self._track_metrics: dict[int, dict] = {}

    def reset(self) -> None:
        self._speed_line_state.clear()
        self._speed_emit_state.clear()
        self._parking_state.clear()
        self._reverse_state.clear()
        self._track_metrics.clear()

    def get_track_metrics(self, track_id: int) -> dict:
        return self._track_metrics.get(track_id, {})

    def evaluate(self, track: Track, ts: float, config: MonitorConfig) -> list[dict]:
        events: list[dict] = []
        self._track_metrics.setdefault(track.track_id, {})
        events.extend(self._check_speed(track, ts, config))
        events.extend(self._check_parking(track, ts, config))
        events.extend(self._check_reverse(track, ts, config))
        return events

    def _check_speed(self, track: Track, ts: float, config: MonitorConfig) -> list[dict]:
        speed_cfg = config.speed
        if not speed_cfg.enabled:
            return []
        events: list[dict] = []
        events.extend(self._check_speed_by_lines(track, ts, speed_cfg))
        if self._is_inside_ab_zone(track.centroid, speed_cfg):
            events.extend(self._check_speed_by_pixel(track, ts, speed_cfg))
        return events

    def _check_speed_by_lines(self, track: Track, ts: float, speed_cfg: SpeedConfig) -> list[dict]:
        if not self._speed_lines_ready(speed_cfg):
            return []
        if len(track.history) < 2:
            return []

        prev_ts, prev_x, prev_y = track.history[-2]
        curr_ts, curr_x, curr_y = track.history[-1]
        prev = (prev_x, prev_y)
        curr = (curr_x, curr_y)

        cross_ts_a = self._estimate_cross_ts_on_horizontal_line(
            prev, curr, float(prev_ts), float(curr_ts), speed_cfg.line_a
        )
        cross_ts_b = self._estimate_cross_ts_on_horizontal_line(
            prev, curr, float(prev_ts), float(curr_ts), speed_cfg.line_b
        )

        metrics = self._track_metrics.setdefault(track.track_id, {})
        if cross_ts_a is not None:
            metrics["line_cross_hint"] = "A"
            metrics["line_cross_hint_ts"] = ts
            metrics["line_a_cross_ts"] = cross_ts_a
        if cross_ts_b is not None:
            metrics["line_cross_hint"] = "B"
            metrics["line_cross_hint_ts"] = ts
            metrics["line_b_cross_ts"] = cross_ts_b

        state = self._speed_line_state.setdefault(
            track.track_id,
            {
                "last_a_ts": None,
                "last_b_ts": None,
                "last_pair_key": None,
            },
        )
        events: list[dict] = []

        if cross_ts_a is not None:
            state["last_a_ts"] = cross_ts_a
        if cross_ts_b is not None:
            state["last_b_ts"] = cross_ts_b

        candidate_pairs: list[tuple[float, float]] = []
        if cross_ts_a is not None and state["last_b_ts"] is not None:
            candidate_pairs.append((cross_ts_a, float(state["last_b_ts"])))
        if cross_ts_b is not None and state["last_a_ts"] is not None:
            candidate_pairs.append((cross_ts_b, float(state["last_a_ts"])))
        if not candidate_pairs:
            return events

        c1, c2 = min(candidate_pairs, key=lambda p: abs(p[0] - p[1]))
        delta_t = abs(float(c1) - float(c2))
        # Discard too-short interval (same instant / noise) and too-long interval.
        if delta_t <= 0.01 or delta_t > 10.0:
            return events

        pair_key = (round(min(c1, c2), 3), round(max(c1, c2), 3))
        if state["last_pair_key"] == pair_key:
            return events
        state["last_pair_key"] = pair_key

        speed = 3.6 * speed_cfg.real_distance_m / delta_t
        measure_ts = max(c1, c2)
        metrics["speed_line_kmh"] = round(speed, 1)
        metrics["speed_line_last_ts"] = measure_ts
        self._set_speed_metrics(track.track_id, speed, measure_ts, "line_horizontal")

        if speed > speed_cfg.speed_limit_kmh and self._check_speed_emit_cooldown(
            track.track_id, measure_ts, 3.0
        ):
            events.append(
                self._new_event(
                    event_type="overspeed",
                    track_id=track.track_id,
                    ts=measure_ts,
                    message=(
                        f"Vehicle {track.track_id} overspeed "
                        f"{speed:.1f} km/h (limit {speed_cfg.speed_limit_kmh:.1f})"
                    ),
                    meta={
                        "speed_kmh": round(speed, 2),
                        "speed_limit_kmh": speed_cfg.speed_limit_kmh,
                        "mode": "line_horizontal",
                    },
                )
            )

        return events

    def _check_speed_by_pixel(self, track: Track, ts: float, speed_cfg: SpeedConfig) -> list[dict]:
        if speed_cfg.meter_per_pixel <= 0 or not self._speed_lines_ready(speed_cfg):
            return []
        points = self._history_within_ab_zone_tail(track, speed_cfg, window_sec=0.6)
        if len(points) < 2:
            return []

        first = points[0]
        last = points[-1]
        delta_t = last[0] - first[0]
        if delta_t <= 0.05:
            return []

        disp_px = math.dist((first[1], first[2]), (last[1], last[2]))
        distance_m = disp_px * speed_cfg.meter_per_pixel
        speed = 3.6 * distance_m / delta_t

        metrics = self._track_metrics.setdefault(track.track_id, {})
        metrics["speed_rt_kmh"] = round(speed, 1)
        metrics["speed_rt_last_ts"] = ts
        metrics["speed_disp_px"] = round(disp_px, 1)
        self._set_speed_metrics(track.track_id, speed, ts, "pixel_displacement_ab")

        if speed > speed_cfg.speed_limit_kmh and self._check_speed_emit_cooldown(
            track.track_id, ts, 3.0
        ):
            return [
                self._new_event(
                    event_type="overspeed",
                    track_id=track.track_id,
                    ts=ts,
                    message=(
                        f"Vehicle {track.track_id} overspeed "
                        f"{speed:.1f} km/h (limit {speed_cfg.speed_limit_kmh:.1f})"
                    ),
                    meta={
                        "speed_kmh": round(speed, 2),
                        "speed_limit_kmh": speed_cfg.speed_limit_kmh,
                        "mode": "pixel_displacement_ab",
                        "displacement_px": round(disp_px, 2),
                        "meter_per_pixel": speed_cfg.meter_per_pixel,
                        "delta_t_sec": round(delta_t, 3),
                    },
                )
            ]
        return []

    @staticmethod
    def _speed_lines_ready(speed_cfg: SpeedConfig) -> bool:
        return len(speed_cfg.line_a) == 2 and len(speed_cfg.line_b) == 2

    @staticmethod
    def _is_inside_ab_zone(point: tuple[float, float], speed_cfg: SpeedConfig) -> bool:
        if not RuleEngine._speed_lines_ready(speed_cfg):
            return False

        x, y = float(point[0]), float(point[1])
        ay = float(speed_cfg.line_a[0][1])
        by = float(speed_cfg.line_b[0][1])
        y_min, y_max = min(ay, by), max(ay, by)
        if not (y_min <= y <= y_max):
            return False

        ax_min = min(float(speed_cfg.line_a[0][0]), float(speed_cfg.line_a[1][0]))
        ax_max = max(float(speed_cfg.line_a[0][0]), float(speed_cfg.line_a[1][0]))
        bx_min = min(float(speed_cfg.line_b[0][0]), float(speed_cfg.line_b[1][0]))
        bx_max = max(float(speed_cfg.line_b[0][0]), float(speed_cfg.line_b[1][0]))

        x_min = max(ax_min, bx_min)
        x_max = min(ax_max, bx_max)
        if x_min > x_max:
            x_min = min(ax_min, bx_min)
            x_max = max(ax_max, bx_max)
        return x_min <= x <= x_max

    def _history_within_ab_zone_tail(
        self, track: Track, speed_cfg: SpeedConfig, window_sec: float
    ) -> list[tuple[float, float, float]]:
        points = self._history_within(track, window_sec=window_sec)
        if len(points) < 2:
            return []

        tail: list[tuple[float, float, float]] = []
        for point in reversed(points):
            if self._is_inside_ab_zone((point[1], point[2]), speed_cfg):
                tail.append(point)
                continue
            if tail:
                break
        tail.reverse()
        return tail

    def _set_speed_metrics(self, track_id: int, speed: float, ts: float, mode: str) -> None:
        metrics = self._track_metrics.setdefault(track_id, {})
        last_ts = float(metrics.get("speed_last_ts", 0.0) or 0.0)
        if ts >= last_ts:
            metrics["speed_kmh"] = round(speed, 1)
            metrics["speed_mode"] = mode
            metrics["speed_last_ts"] = ts

    def _check_speed_emit_cooldown(self, track_id: int, ts: float, cooldown_sec: float) -> bool:
        last_emit_ts = float(self._speed_emit_state.get(track_id, 0.0))
        if not self._cooldown_passed(last_emit_ts, ts, cooldown_sec):
            return False
        self._speed_emit_state[track_id] = ts
        return True

    def _check_parking(self, track: Track, ts: float, config: MonitorConfig) -> list[dict]:
        parking_cfg = config.parking
        if not parking_cfg.enabled or not parking_cfg.rois:
            return []

        point = track.centroid
        in_roi = any(point_in_polygon(point, roi) for roi in parking_cfg.rois if len(roi) >= 3)
        state = self._parking_state.setdefault(
            track.track_id, {"enter_ts": None, "alerted": False, "last_emit_ts": 0.0}
        )

        if not in_roi:
            self._parking_state.pop(track.track_id, None)
            return []

        if state["enter_ts"] is None:
            state["enter_ts"] = ts
            return []

        stay_sec = ts - float(state["enter_ts"])
        if stay_sec < parking_cfg.min_stay_sec:
            return []

        points = self._history_within(track, parking_cfg.min_stay_sec)
        if len(points) < 2:
            return []

        first = points[0]
        last = points[-1]
        disp = math.dist((first[1], first[2]), (last[1], last[2]))
        self._track_metrics[track.track_id]["parking_disp_px"] = round(disp, 1)

        if (
            disp <= parking_cfg.max_displacement_px
            and not state["alerted"]
            and self._cooldown_passed(float(state["last_emit_ts"]), ts, 5.0)
        ):
            state["alerted"] = True
            state["last_emit_ts"] = ts
            return [
                self._new_event(
                    event_type="illegal_parking",
                    track_id=track.track_id,
                    ts=ts,
                    message=f"Vehicle {track.track_id} parked over {parking_cfg.min_stay_sec:.0f}s",
                    meta={
                        "stay_sec": round(stay_sec, 1),
                        "displacement_px": round(disp, 1),
                    },
                )
            ]
        return []

    def _check_reverse(self, track: Track, ts: float, config: MonitorConfig) -> list[dict]:
        reverse_cfg = config.reverse
        if not reverse_cfg.enabled or not reverse_cfg.lanes:
            return []

        lane = self._select_reverse_lane(track.centroid, reverse_cfg.lanes)
        if lane is None:
            self._reverse_state.pop(track.track_id, None)
            return []

        window_sec = max(lane.min_duration_sec, 1.0)
        points = self._history_within(track, window_sec)
        if len(points) < 2:
            return []

        first = points[0]
        last = points[-1]
        move_vec = (last[1] - first[1], last[2] - first[2])
        disp = vector_norm(move_vec)
        if disp < lane.min_displacement_px:
            self._reverse_state.pop(track.track_id, None)
            return []

        dir_line = lane.legal_direction
        lane_vec = (dir_line[1][0] - dir_line[0][0], dir_line[1][1] - dir_line[0][1])
        cos_theta = cosine_similarity(move_vec, lane_vec)
        self._track_metrics[track.track_id]["reverse_cos"] = round(cos_theta, 2)
        self._track_metrics[track.track_id]["reverse_lane"] = lane.name

        state = self._reverse_state.setdefault(
            track.track_id, {"suspect_start_ts": None, "alerted": False, "last_emit_ts": 0.0}
        )

        if cos_theta < reverse_cfg.cos_threshold:
            inferred_start_ts = float(first[0])
            if state["suspect_start_ts"] is None:
                # Start from the beginning of the analyzed motion window.
                state["suspect_start_ts"] = inferred_start_ts
                return []
            state["suspect_start_ts"] = min(float(state["suspect_start_ts"]), inferred_start_ts)
            if (
                ts - float(state["suspect_start_ts"]) >= lane.min_duration_sec
                and not state["alerted"]
                and self._cooldown_passed(float(state["last_emit_ts"]), ts, 5.0)
            ):
                state["alerted"] = True
                state["last_emit_ts"] = ts
                return [
                    self._new_event(
                    event_type="reverse_driving",
                    track_id=track.track_id,
                    ts=ts,
                    message=f"Vehicle {track.track_id} possible reverse (cos={cos_theta:.2f})",
                    meta={
                            "cos_theta": round(cos_theta, 3),
                            "lane": lane.name,
                            "duration_sec": round(ts - float(state["suspect_start_ts"]), 2),
                        },
                    )
                ]
            return []

        state["suspect_start_ts"] = None
        state["alerted"] = False
        return []

    @staticmethod
    def _select_reverse_lane(point: tuple[float, float], lanes: list[LaneConfig]) -> LaneConfig | None:
        fallback_global_lane: LaneConfig | None = None
        for lane in lanes:
            if len(lane.legal_direction) != 2:
                continue
            # Normal case: lane polygon + direction arrow.
            if len(lane.polygon) >= 3:
                if point_in_polygon(point, lane.polygon):
                    return lane
                continue
            # Fallback: no polygon drawn, but direction exists.
            if fallback_global_lane is None:
                fallback_global_lane = lane
        return fallback_global_lane

    @staticmethod
    def _history_within(track: Track, window_sec: float) -> list[tuple[float, float, float]]:
        if not track.history:
            return []
        end_ts = track.history[-1][0]
        begin_ts = end_ts - window_sec
        return [p for p in track.history if p[0] >= begin_ts]

    @staticmethod
    def _cooldown_passed(last_emit_ts: float, ts: float, cooldown_sec: float) -> bool:
        if last_emit_ts <= 0.0:
            return True
        return (ts - last_emit_ts) > cooldown_sec

    @staticmethod
    def _estimate_cross_ts_on_horizontal_line(
        prev_point: tuple[float, float],
        curr_point: tuple[float, float],
        prev_ts: float,
        curr_ts: float,
        line: list[list[float]],
    ) -> float | None:
        if len(line) != 2:
            return None
        if curr_ts <= prev_ts:
            return None

        y_line = float(line[0][1])
        x_line_min = min(float(line[0][0]), float(line[1][0]))
        x_line_max = max(float(line[0][0]), float(line[1][0]))
        x1 = float(prev_point[0])
        x2 = float(curr_point[0])
        y1 = float(prev_point[1])
        y2 = float(curr_point[1])
        dy = y2 - y1
        if abs(dy) < 1e-6:
            return None

        ratio = (y_line - y1) / dy
        if 0.0 <= ratio <= 1.0:
            x_cross = x1 + ratio * (x2 - x1)
            if not (x_line_min - 1e-6 <= x_cross <= x_line_max + 1e-6):
                return None
            return prev_ts + ratio * (curr_ts - prev_ts)
        return None

    @staticmethod
    def _new_event(
        event_type: str, track_id: int, ts: float, message: str, meta: dict | None = None
    ) -> dict:
        return {
            "event_type": event_type,
            "track_id": int(track_id),
            "timestamp": datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
            "message": message,
            "meta": meta or {},
        }
