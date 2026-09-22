from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_network_model_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "off")
