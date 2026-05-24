from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import NewsItem


class Source(ABC):
    name: str = ""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def collect(self) -> list[NewsItem]:
        raise NotImplementedError
