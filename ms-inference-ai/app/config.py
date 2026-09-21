import math
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    delay_seconds: float = 0
    failure_mode: str = "none"

    def __post_init__(self):
        if not math.isfinite(self.delay_seconds) or not 0 <= self.delay_seconds <= 10:
            raise ValueError("MOCK_DELAY_SECONDS debe estar entre 0 y 10")
        if self.failure_mode not in {"none", "unavailable"}:
            raise ValueError("MOCK_FAILURE_MODE debe ser none o unavailable")

    @classmethod
    def from_env(cls):
        return cls(
            delay_seconds=float(os.getenv("MOCK_DELAY_SECONDS", "0")),
            failure_mode=os.getenv("MOCK_FAILURE_MODE", "none"),
        )
