"""Base connector protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseConnector(ABC):
    connector_id: str = "base"
    platform: str = "custom"

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @abstractmethod
    def collect_comments(self, target: str, **kwargs: Any) -> list[dict[str, Any]]:
        ...
