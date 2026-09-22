from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import numpy as np
import pytest

from agentroots.retrieval import SemanticRetriever


class FakeModel:
    def __init__(self) -> None:
        self.document_batches: list[list[str]] = []

    def embed(self, values: list[str], **_: object) -> list[np.ndarray]:
        self.document_batches.append(values)
        return [np.asarray([1.0, 0.0]) for _ in values]

    def query_embed(self, _: str) -> list[np.ndarray]:
        return [np.asarray([1.0, 0.0])]


class NoEmbedModel(FakeModel):
    def embed(self, values: list[str], **_: object) -> list[np.ndarray]:
        raise AssertionError(f"persistent index should avoid re-embedding {len(values)} records")


def record(record_id: str, revision: int) -> dict[str, object]:
    return {
        "id": record_id,
        "revision": revision,
        "type": "finding",
        "status": "accepted",
        "title": f"title {record_id}",
        "body": "body",
    }


def test_vectors_survive_project_switch_and_revision_replaces_old_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "auto")
    retriever = SemanticRetriever()
    model = FakeModel()
    retriever._model = model
    retriever._available = True
    first = record("one", 1)
    second = record("two", 1)

    assert retriever.rank([first], "query", 1)
    assert retriever.rank([second], "query", 1)
    assert retriever.rank([first], "query", 1)
    assert len(model.document_batches) == 2
    assert ("one", 1) in retriever._vectors
    assert ("two", 1) in retriever._vectors

    revised = record("one", 2)
    assert retriever.rank([revised], "query", 1)
    assert len(model.document_batches) == 3
    assert ("one", 1) not in retriever._vectors
    assert ("one", 2) in retriever._vectors
    assert ("two", 1) in retriever._vectors


def test_large_index_is_persisted_across_retriever_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "auto")
    records = [{**record(str(index), 1), "project": "persistent"} for index in range(256)]
    first = SemanticRetriever(cache_dir=tmp_path)
    first._model = FakeModel()
    first._available = True
    assert first.rank(records, "query", 1), first.error
    indexes = list((tmp_path / "vector-indexes").glob("*.npy"))
    assert indexes

    indexes[0].write_bytes(b"corrupt")
    repaired = SemanticRetriever(cache_dir=tmp_path)
    repaired_model = FakeModel()
    repaired._model = repaired_model
    repaired._available = True
    assert repaired.rank(records, "query", 1)
    assert repaired_model.document_batches

    restarted = SemanticRetriever(cache_dir=tmp_path)
    restarted._model = NoEmbedModel()
    restarted._available = True
    assert restarted.rank(records, "query", 1)


def test_concurrent_large_index_build_embeds_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "auto")
    records = [{**record(str(index), 1), "project": "concurrent"} for index in range(256)]
    retriever = SemanticRetriever(cache_dir=tmp_path)
    model = FakeModel()
    retriever._model = model
    retriever._available = True
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: retriever.rank(records, "query", 1), range(4)))
    assert all(results)
    assert len(model.document_batches) == 1


def test_cold_rank_returns_fallback_while_model_warms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "auto")
    retriever = SemanticRetriever()
    model = FakeModel()
    started = Event()
    release = Event()

    def slow_load() -> FakeModel:
        started.set()
        assert release.wait(timeout=2)
        retriever._model = model
        retriever._available = True
        return model

    monkeypatch.setattr(retriever, "_load", slow_load)

    assert retriever.rank([record("cold", 1)], "query", 1) is None
    assert started.wait(timeout=1)
    release.set()
    assert retriever._warmup_thread is not None
    retriever._warmup_thread.join(timeout=2)
    assert retriever.rank([record("cold", 1)], "query", 1)


def test_concurrent_cold_calls_start_one_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "auto")
    retriever = SemanticRetriever()
    started = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()

    def slow_load() -> FakeModel:
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        assert release.wait(timeout=2)
        retriever._model = FakeModel()
        retriever._available = True
        return retriever._model

    monkeypatch.setattr(retriever, "_load", slow_load)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(
            pool.map(lambda _: retriever.rank([record("cold", 1)], "query", 1), range(20))
        )
    assert results == [None] * 20
    assert started.wait(timeout=1)
    assert calls == 1
    release.set()
    assert retriever._warmup_thread is not None
    retriever._warmup_thread.join(timeout=2)


def test_background_loader_exception_fails_open_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "auto")
    retriever = SemanticRetriever()
    calls = 0

    def broken_load() -> None:
        nonlocal calls
        calls += 1
        raise KeyError("broken model metadata")

    monkeypatch.setattr(retriever, "_load", broken_load)
    assert retriever.rank([record("broken", 1)], "query", 1) is None
    assert retriever._warmup_thread is not None
    retriever._warmup_thread.join(timeout=2)
    assert retriever.backend == "fts_fallback"
    assert retriever.error == "KeyError: 'broken model metadata'"
    assert retriever.rank([record("broken", 1)], "query", 1) is None
    assert calls == 1


def test_warmup_then_rank_uses_semantic_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "auto")
    retriever = SemanticRetriever()
    model = FakeModel()

    def load() -> FakeModel:
        retriever._model = model
        retriever._available = True
        return model

    monkeypatch.setattr(retriever, "_load", load)
    assert retriever.warmup() is True
    assert retriever.rank([record("daemon", 1)], "query", 1)


def test_concurrent_small_rank_embeds_documents_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "auto")
    retriever = SemanticRetriever()
    model = FakeModel()
    retriever._model = model
    retriever._available = True
    records = [record(str(index), 1) for index in range(10)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: retriever.rank(records, "query", 1), range(8)))
    assert all(results)
    assert len(model.document_batches) == 1


def test_inference_failure_does_not_retry_on_every_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "auto")

    class BrokenModel(FakeModel):
        attempts = 0

        def query_embed(self, _: str) -> list[np.ndarray]:
            self.attempts += 1
            raise RuntimeError("inference unavailable")

    retriever = SemanticRetriever()
    model = BrokenModel()
    retriever._model = model
    retriever._available = True
    for _ in range(3):
        assert retriever.rank([record("one", 1)], "query", 1) is None
    assert model.attempts == 1
    assert retriever.backend == "fts_fallback"
    assert retriever.error == "RuntimeError: inference unavailable"


def test_semantic_opt_out_is_respected_after_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "auto")
    retriever = SemanticRetriever()
    model = FakeModel()
    retriever._model = model
    retriever._available = True
    assert retriever.rank([record("one", 1)], "query", 1)
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "off")
    assert retriever.rank([record("two", 1)], "query", 1) is None
    assert len(model.document_batches) == 1
    assert retriever.backend == "fts_fallback"
    assert retriever.error == "disabled by AGENTROOTS_SEMANTIC"
