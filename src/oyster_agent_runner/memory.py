"""Simple RAG memory — retrieve top-k relevant past steps by cosine similarity.

Design goals
------------
* **Zero runtime deps** — no numpy, no FAISS, no sentence-transformers.
  Callers plug in any embedder that maps `str → list[float]`.
* **Stateless persistence** — one `.jsonl` file per memory store, one
  entry per line. Debuggable, grep-able, re-ingestible.
* **No eviction policy** — this is a scratchpad for a single long run,
  not a long-lived vector DB. Pair with a time-based cutoff if needed.

Typical embedder swap-in
------------------------
For Anthropic/OpenAI users:

    from anthropic import Anthropic
    client = Anthropic()

    def embed(text: str) -> list[float]:
        # pseudo — Anthropic doesn't expose embeddings yet; use an
        # OpenAI / Voyage / local model instead.
        ...

Or use sentence-transformers locally:

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")

    def embed(text: str) -> list[float]:
        return model.encode(text).tolist()
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Embedder = Callable[[str], Sequence[float]]


@dataclass
class MemoryRecord:
    """One stored memory — text + pre-computed embedding + free-form metadata."""

    text: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)
    step: int | None = None  # convenience — typical callers index by agent step

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "embedding": self.embedding,
            "metadata": self.metadata,
            "step": self.step,
        }

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> MemoryRecord:
        return cls(
            text=obj["text"],
            embedding=list(obj["embedding"]),
            metadata=obj.get("metadata", {}),
            step=obj.get("step"),
        )


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 for zero-norm vectors."""
    if len(a) != len(b):
        raise ValueError(f"embedding dimension mismatch: {len(a)} vs {len(b)}")
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# --- Deterministic fallback embedder ----------------------------------------


def hashing_embedder(dim: int = 64) -> Embedder:
    """Deterministic embedder suitable for tests and CI — no model required.

    Hashes tokens into `dim` buckets and normalizes. Similar strings produce
    similar vectors (bag-of-words with hashing trick); unrelated strings
    produce near-orthogonal vectors.

    This is emphatically NOT a production embedder — but it's enough for
    the RAG pipeline shape to be exercised end-to-end in tests that
    don't want to pull in a real embedding model.
    """

    def embed(text: str) -> list[float]:
        buckets = [0.0] * dim
        for token in text.lower().split():
            h = int.from_bytes(hashlib.sha1(token.encode("utf-8")).digest()[:8], "big")
            bucket = h % dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            buckets[bucket] += sign
        # Normalize so cosine similarity stays in [-1, 1].
        norm = math.sqrt(sum(x * x for x in buckets))
        if norm == 0.0:
            return buckets
        return [x / norm for x in buckets]

    return embed


# --- TrajectoryMemory --------------------------------------------------------


class TrajectoryMemory:
    """In-memory store of past-step embeddings with top-k retrieval.

    Usage
    -----
    >>> mem = TrajectoryMemory(embedder=hashing_embedder(dim=32))
    >>> mem.add("I punched an oak tree and got 1 wood.", metadata={"reward": 1.0}, step=0)
    >>> hits = mem.retrieve("how do I get wood", k=3)
    >>> [h.text for h in hits]
    ['I punched an oak tree and got 1 wood.']
    """

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self._records: list[MemoryRecord] = []

    # --- mutations -----------------------------------------------------------

    def add(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        step: int | None = None,
    ) -> MemoryRecord:
        """Embed `text` and store the record. Returns the stored record."""
        vec = list(self.embedder(text))
        rec = MemoryRecord(text=text, embedding=vec, metadata=metadata or {}, step=step)
        self._records.append(rec)
        return rec

    def extend(self, texts: Sequence[str]) -> list[MemoryRecord]:
        """Bulk-add; returns the stored records in the input order."""
        return [self.add(t) for t in texts]

    def clear(self) -> None:
        self._records.clear()

    # --- queries -------------------------------------------------------------

    @property
    def records(self) -> list[MemoryRecord]:
        """Read-only view of the underlying records (list copy)."""
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def retrieve(self, query: str, k: int = 5) -> list[MemoryRecord]:
        """Return the top-k most similar records to `query` by cosine sim.

        `k` is clamped to the number of stored records. Returns an empty
        list when the store is empty. Ties are broken by insertion order.
        """
        if k <= 0 or not self._records:
            return []
        q_vec = list(self.embedder(query))
        scored: list[tuple[float, int, MemoryRecord]] = []
        for idx, rec in enumerate(self._records):
            sim = cosine_similarity(q_vec, rec.embedding)
            scored.append((sim, idx, rec))
        # Sort by similarity DESC, then insertion order ASC for ties.
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [rec for _sim, _idx, rec in scored[:k]]

    # --- persistence ---------------------------------------------------------

    def save_jsonl(self, path: Path) -> None:
        """Persist records to a `.jsonl` file (one record per line)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for rec in self._records:
                f.write(json.dumps(rec.to_json()) + "\n")

    def load_jsonl(self, path: Path, *, append: bool = False) -> None:
        """Load records from a `.jsonl` file written by `save_jsonl`.

        If `append=False` (default), the in-memory store is replaced;
        otherwise records are appended to the existing list.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        loaded: list[MemoryRecord] = []
        with path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                loaded.append(MemoryRecord.from_json(json.loads(line)))
        if append:
            self._records.extend(loaded)
        else:
            self._records = loaded


__all__ = [
    "Embedder",
    "MemoryRecord",
    "TrajectoryMemory",
    "cosine_similarity",
    "hashing_embedder",
]
