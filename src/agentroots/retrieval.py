from __future__ import annotations

import hashlib
import importlib
import os
import threading
from pathlib import Path
from typing import Any

from platformdirs import user_cache_path

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class SemanticRetriever:
    """Lazy local embedding retriever with revision-aware in-memory vector caching."""

    def __init__(self, model_name: str | None = None, cache_dir: Path | None = None):
        self.model_name = model_name or os.environ.get("AGENTROOTS_EMBEDDING_MODEL", DEFAULT_MODEL)
        configured_cache = os.environ.get("AGENTROOTS_MODEL_CACHE")
        self.cache_dir = (
            cache_dir
            or (Path(configured_cache) if configured_cache else None)
            or user_cache_path("agentroots") / "models"
        )
        self._model: Any | None = None
        self._vectors: dict[tuple[str, int], Any] = {}
        self._indexes: dict[str, Any] = {}
        self._scope_keys: dict[str, list[str]] = {}
        self._available: bool | None = None
        self._error: str | None = None
        self._lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._warmup_lock = threading.Lock()
        self._warmup_thread: threading.Thread | None = None

    @property
    def backend(self) -> str:
        return "bge_hybrid" if self._available else "fts_fallback"

    @property
    def error(self) -> str | None:
        return self._error

    def warmup(self) -> bool:
        """Load model weights without performing or blocking a retrieval request."""
        return self._load() is not None

    def warmup_async(self) -> None:
        """Start one background model load so cold reads can use lexical fallback."""
        if self._model is not None:
            return
        with self._warmup_lock:
            if self._model is not None or self._available is False:
                return
            if self._warmup_thread is not None and self._warmup_thread.is_alive():
                return
            self._warmup_thread = threading.Thread(
                target=self._background_load,
                name="agentroots-semantic-warmup",
                daemon=True,
            )
            self._warmup_thread.start()

    def _background_load(self) -> None:
        try:
            self._load()
        except Exception as exc:  # noqa: BLE001 - background model failures must fail open
            self._available = False
            self._error = f"{type(exc).__name__}: {exc}"

    def _load(self) -> Any | None:
        setting = os.environ.get("AGENTROOTS_SEMANTIC", "auto").lower()
        if setting in {"0", "false", "off", "disabled"}:
            self._available = False
            self._error = "disabled by AGENTROOTS_SEMANTIC"
            return None
        if self._available is False:
            return None
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                fastembed = importlib.import_module("fastembed")
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = fastembed.TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=str(self.cache_dir),
                    threads=max(1, min(8, os.cpu_count() or 1)),
                )
                self._available = True
                return self._model
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                self._available = False
                self._error = f"{type(exc).__name__}: {exc}"
                return None

    @staticmethod
    def _text(record: dict[str, Any]) -> str:
        return (
            f"{record['type']} {record['status']} {record['title']} {record['body']}"
        )

    def _index_key(self, records: list[dict[str, Any]]) -> str:
        digest = hashlib.sha256(self.model_name.encode())
        digest.update(str(records[0].get("project", "global")).encode())
        for record in records:
            digest.update(str(record["id"]).encode())
            digest.update(b":")
            digest.update(str(record["revision"]).encode())
            digest.update(b"\0")
        return digest.hexdigest()

    def _persistent_matrix(self, model: Any, records: list[dict[str, Any]]) -> Any:
        import numpy as np

        key = self._index_key(records)
        cached = self._indexes.get(key)
        if cached is not None:
            return cached
        directory = self.cache_dir / "vector-indexes"
        directory.mkdir(parents=True, exist_ok=True)
        project = str(records[0].get("project", "global"))
        scope = hashlib.sha256(f"{self.model_name}:{project}".encode()).hexdigest()[:16]
        path = directory / f"{scope}-{key[:24]}.npy"
        if path.exists():
            try:
                matrix = np.load(path, mmap_mode="r")
                self._indexes[key] = matrix
                return matrix
            except (OSError, ValueError):
                try:
                    path.unlink()
                except OSError:
                    pass
        with self._inference_lock:
            if path.exists():
                try:
                    matrix = np.load(path, mmap_mode="r")
                except (OSError, ValueError):
                    path.unlink(missing_ok=True)
            if not path.exists():
                matrix = np.asarray(
                    list(
                        model.embed(
                            [self._text(record) for record in records],
                            batch_size=512,
                        )
                    ),
                    dtype=np.float32,
                )
                temporary = path.with_suffix(f".{os.getpid()}.tmp.npy")
                np.save(temporary, matrix, allow_pickle=False)
                os.replace(temporary, path)
        older = sorted(
            (candidate for candidate in directory.glob(f"{scope}-*.npy") if candidate != path),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )
        for old_path in older[2:]:
            try:
                old_path.unlink()
            except OSError:
                pass
        self._indexes[key] = matrix
        keys = [key] + [
            index_key for index_key in self._scope_keys.get(scope, []) if index_key != key
        ]
        self._scope_keys[scope] = keys[:3]
        for index_key in keys[3:]:
            self._indexes.pop(index_key, None)
        return matrix

    def rank(
        self, records: list[dict[str, Any]], query: str, limit: int
    ) -> list[dict[str, Any]] | None:
        if not records:
            return None
        # A loaded model must still respect explicit opt-out and the failure
        # circuit breaker. Retrying a broken model on every hook adds latency
        # while incorrectly reporting that lexical fallback is active.
        setting = os.environ.get("AGENTROOTS_SEMANTIC", "auto").lower()
        if setting in {"0", "false", "off", "disabled"}:
            self._available = False
            self._error = "disabled by AGENTROOTS_SEMANTIC"
            return None
        if self._available is False:
            return None
        model = self._model
        if model is None:
            self.warmup_async()
            return None
        try:
            import numpy as np

            if len(records) >= 256:
                matrix = self._persistent_matrix(model, records)
            else:
                with self._inference_lock:
                    missing = [
                        record
                        for record in records
                        if (record["id"], record["revision"]) not in self._vectors
                    ]
                    if missing:
                        missing_ids = {record["id"] for record in missing}
                        self._vectors = {
                            cache_key: vector
                            for cache_key, vector in self._vectors.items()
                            if cache_key[0] not in missing_ids
                        }
                        vectors = list(
                            model.embed(
                                [self._text(record) for record in missing],
                                batch_size=256,
                            )
                        )
                        for record, vector in zip(missing, vectors, strict=True):
                            self._vectors[(record["id"], record["revision"])] = vector
                    matrix = np.asarray(
                        [
                            self._vectors[(record["id"], record["revision"])]
                            for record in records
                        ]
                    )
            with self._inference_lock:
                query_vector = np.asarray(next(iter(model.query_embed(query))))
            order = np.argsort(-(matrix @ query_vector))[:limit]
            return [records[int(index)] for index in order]
        except (OSError, RuntimeError, ValueError) as exc:
            self._available = False
            self._error = f"{type(exc).__name__}: {exc}"
            return None
