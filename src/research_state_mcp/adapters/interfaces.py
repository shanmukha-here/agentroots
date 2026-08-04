"""Phase-4 interface markers. Flowcept/AiiDA/PostgreSQL support is roadmap-only."""

from typing import Protocol

from .base import Adapter


class FlowceptAdapter(Adapter, Protocol):
    pass


class AiiDAAdapter(Adapter, Protocol):
    pass
