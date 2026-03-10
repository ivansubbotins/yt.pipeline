"""Base class for pipeline steps."""

import logging
from abc import ABC, abstractmethod

import anthropic

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from state import PipelineState, StepStatus

logger = logging.getLogger(__name__)


class BaseStep(ABC):
    """Base class for all pipeline steps."""

    step_name: str = ""

    def __init__(self, state: PipelineState):
        self.state = state
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = ANTHROPIC_MODEL

    def run(self) -> dict:
        """Execute the step with status tracking."""
        logger.info(f"[{self.state.project_id}] Starting step: {self.step_name}")
        self.state.set_step_status(self.step_name, StepStatus.IN_PROGRESS)

        try:
            result = self.execute()
            self.state.set_step_data(self.step_name, result)
            self.state.set_step_status(self.step_name, StepStatus.COMPLETED)
            logger.info(f"[{self.state.project_id}] Completed step: {self.step_name}")
            return result
        except Exception as e:
            logger.error(f"[{self.state.project_id}] Failed step {self.step_name}: {e}")
            self.state.set_step_status(self.step_name, StepStatus.FAILED)
            raise

    @abstractmethod
    def execute(self) -> dict:
        """Implement the actual step logic. Returns result data dict."""
        ...

    def ask_claude(self, system: str, prompt: str) -> str:
        """Send a prompt to Claude and return the response text."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def get_previous_step_data(self, step_name: str) -> dict:
        """Get data from a previously completed step."""
        return self.state.get_step(step_name).get("data", {})
