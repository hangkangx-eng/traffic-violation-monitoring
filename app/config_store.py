from __future__ import annotations

import json
from pathlib import Path

from app.schemas import MonitorConfig


class ConfigStore:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _config_path(self, camera_id: str) -> Path:
        safe_camera_id = camera_id.replace("/", "_").replace("\\", "_")
        return self.config_dir / f"{safe_camera_id}.json"

    def load(self, camera_id: str) -> MonitorConfig | None:
        path = self._config_path(camera_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return MonitorConfig(**raw)

    def save(self, config: MonitorConfig) -> Path:
        path = self._config_path(config.camera_id)
        path.write_text(
            json.dumps(config.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
