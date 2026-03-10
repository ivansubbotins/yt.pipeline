"""Pipeline state management — tracks progress of each video project."""

import json
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from config import DATA_DIR

logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    WAITING = "waiting"  # ожидание ручного этапа (съёмка/монтаж)
    APPROVED = "approved"
    FAILED = "failed"


PIPELINE_STEPS = [
    "research",
    "content_plan",
    "references",
    "script",
    "teleprompter",
    "covers",
    "description",
    "shooting",      # ручной этап — Иван
    "editing",       # ручной этап — монтажёр
    "publish",
]


class PipelineState:
    """Manages state for a single video project."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.state_file = DATA_DIR / project_id / "state.json"
        self.project_dir = DATA_DIR / project_id
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self._state = self._load()

    def _load(self) -> dict:
        if self.state_file.exists():
            with open(self.state_file) as f:
                return json.load(f)
        return self._init_state()

    def _init_state(self) -> dict:
        state = {
            "project_id": self.project_id,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "topic": "",
            "current_step": PIPELINE_STEPS[0],
            "steps": {step: {"status": StepStatus.PENDING, "data": {}, "log": []} for step in PIPELINE_STEPS},
        }
        self._save(state)
        return state

    def _save(self, state: dict | None = None):
        if state is None:
            state = self._state
        state["updated_at"] = datetime.now().isoformat()
        with open(self.state_file, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, default=str)

    @property
    def topic(self) -> str:
        return self._state.get("topic", "")

    @topic.setter
    def topic(self, value: str):
        self._state["topic"] = value
        self._save()

    @property
    def current_step(self) -> str:
        return self._state["current_step"]

    def get_step(self, step_name: str) -> dict:
        return self._state["steps"][step_name]

    def set_step_status(self, step_name: str, status: StepStatus):
        self._state["steps"][step_name]["status"] = status
        logger.info(f"[{self.project_id}] {step_name} → {status}")
        self._log_step(step_name, f"Status changed to {status}")
        self._save()

    def set_step_data(self, step_name: str, data: dict):
        self._state["steps"][step_name]["data"] = data
        self._save()

    def update_step_data(self, step_name: str, key: str, value: Any):
        self._state["steps"][step_name]["data"][key] = value
        self._save()

    def _log_step(self, step_name: str, message: str):
        entry = {"time": datetime.now().isoformat(), "message": message}
        self._state["steps"][step_name]["log"].append(entry)

    def advance(self):
        """Move to the next step in the pipeline."""
        idx = PIPELINE_STEPS.index(self.current_step)
        if idx < len(PIPELINE_STEPS) - 1:
            self.set_step_status(self.current_step, StepStatus.COMPLETED)
            self._state["current_step"] = PIPELINE_STEPS[idx + 1]
            self.set_step_status(PIPELINE_STEPS[idx + 1], StepStatus.IN_PROGRESS)
            self._save()
            return PIPELINE_STEPS[idx + 1]
        return None

    def mark_waiting(self, step_name: str):
        """Mark a manual step as waiting for human action."""
        self.set_step_status(step_name, StepStatus.WAITING)

    def mark_approved(self, step_name: str):
        """Mark a step as approved by Ivan."""
        self.set_step_status(step_name, StepStatus.APPROVED)

    def summary(self) -> str:
        lines = [f"Проект: {self.project_id} | Тема: {self.topic}", f"Текущий шаг: {self.current_step}", ""]
        for step in PIPELINE_STEPS:
            info = self._state["steps"][step]
            status = info["status"]
            marker = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "waiting": "⏳", "approved": "👍", "failed": "❌"}.get(status, "?")
            lines.append(f"  {marker} {step}: {status}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return self._state.copy()


def list_projects() -> list[str]:
    """Return list of existing project IDs."""
    if not DATA_DIR.exists():
        return []
    return [d.name for d in DATA_DIR.iterdir() if d.is_dir() and (d / "state.json").exists()]
