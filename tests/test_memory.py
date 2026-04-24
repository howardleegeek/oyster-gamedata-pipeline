"""TrajectoryMemory — embedding store + top-k retrieval."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oyster_agent_runner.memory import (
    MemoryRecord,
    TrajectoryMemory,
    cosine_similarity,
    hashing_embedder,
)

# --- cosine_similarity -------------------------------------------------------


def test_cosine_similarity_identity() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_returns_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_similarity_rejects_dim_mismatch() -> None:
    with pytest.raises(ValueError, match="dimension mismatch"):
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


# --- hashing_embedder --------------------------------------------------------


def test_hashing_embedder_is_deterministic() -> None:
    e = hashing_embedder(dim=32)
    a = e("build a wooden house")
    b = e("build a wooden house")
    assert a == b


def test_hashing_embedder_unit_normalized() -> None:
    e = hashing_embedder(dim=16)
    vec = e("some text for testing")
    import math

    norm = math.sqrt(sum(x * x for x in vec))
    # empty-string edge case aside, normalized vectors have norm 1.
    assert norm == pytest.approx(1.0, rel=1e-6)


def test_hashing_embedder_similar_texts_are_close() -> None:
    e = hashing_embedder(dim=128)
    base = e("I punched an oak tree and got a piece of wood")
    close = e("I punched an oak tree and got wood")
    far = e("turn on the stove burner for the soup pot")
    s_close = cosine_similarity(base, close)
    s_far = cosine_similarity(base, far)
    # Similar texts should score measurably higher than unrelated ones.
    assert s_close > s_far + 0.2


# --- TrajectoryMemory --------------------------------------------------------


def test_memory_add_and_retrieve_returns_top_k() -> None:
    mem = TrajectoryMemory(embedder=hashing_embedder(dim=128))
    samples = [
        "I punched an oak tree and got 1 wood.",
        "I smelted iron in the furnace to make an ingot.",
        "I crafted a wooden pickaxe from 3 planks and 2 sticks.",
        "I explored the cave and found coal deposits.",
        "I fought a zombie with my iron sword.",
    ]
    for idx, text in enumerate(samples):
        mem.add(text, metadata={"index": idx}, step=idx)

    assert len(mem) == 5

    hits = mem.retrieve("how do I get wood", k=2)
    assert len(hits) == 2
    # Top hit should be one of the wood-related samples.
    top_texts = {h.text for h in hits}
    assert any("wood" in t or "tree" in t for t in top_texts)

    # Retrieval on a very different topic should still return k results
    # but with different top hit.
    cave_hits = mem.retrieve("caves and minerals underground", k=1)
    assert len(cave_hits) == 1


def test_memory_retrieve_k_clamped_to_available() -> None:
    mem = TrajectoryMemory(embedder=hashing_embedder(dim=16))
    mem.add("only record")
    hits = mem.retrieve("anything", k=100)
    assert len(hits) == 1


def test_memory_retrieve_empty_store() -> None:
    mem = TrajectoryMemory(embedder=hashing_embedder())
    assert mem.retrieve("anything", k=5) == []


def test_memory_retrieve_k_zero() -> None:
    mem = TrajectoryMemory(embedder=hashing_embedder())
    mem.add("x")
    assert mem.retrieve("x", k=0) == []


def test_memory_records_is_read_only_copy() -> None:
    mem = TrajectoryMemory(embedder=hashing_embedder())
    mem.add("a")
    snapshot = mem.records
    snapshot.clear()
    assert len(mem) == 1  # mutating the snapshot didn't drain the store


def test_memory_clear() -> None:
    mem = TrajectoryMemory(embedder=hashing_embedder())
    mem.extend(["a", "b", "c"])
    assert len(mem) == 3
    mem.clear()
    assert len(mem) == 0


# --- persistence -------------------------------------------------------------


def test_memory_save_and_load_roundtrip(tmp_path: Path) -> None:
    mem = TrajectoryMemory(embedder=hashing_embedder(dim=16))
    mem.add("first memory", metadata={"tag": "init"}, step=0)
    mem.add("second memory", metadata={"tag": "mid"}, step=5)

    path = tmp_path / "mem.jsonl"
    mem.save_jsonl(path)

    # File is valid JSONL.
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        json.loads(ln)  # must parse

    # Reload into a fresh memory.
    mem2 = TrajectoryMemory(embedder=hashing_embedder(dim=16))
    mem2.load_jsonl(path)
    assert len(mem2) == 2
    texts = [r.text for r in mem2.records]
    assert texts == ["first memory", "second memory"]
    assert mem2.records[0].metadata == {"tag": "init"}
    assert mem2.records[1].step == 5


def test_memory_load_append_mode(tmp_path: Path) -> None:
    mem = TrajectoryMemory(embedder=hashing_embedder(dim=16))
    mem.add("pre-existing")

    seed = TrajectoryMemory(embedder=hashing_embedder(dim=16))
    seed.extend(["loaded-1", "loaded-2"])
    path = tmp_path / "seed.jsonl"
    seed.save_jsonl(path)

    mem.load_jsonl(path, append=True)
    assert len(mem) == 3
    assert [r.text for r in mem.records] == ["pre-existing", "loaded-1", "loaded-2"]


def test_memory_load_missing_file_raises(tmp_path: Path) -> None:
    mem = TrajectoryMemory(embedder=hashing_embedder())
    with pytest.raises(FileNotFoundError):
        mem.load_jsonl(tmp_path / "nope.jsonl")


def test_memory_record_roundtrip() -> None:
    rec = MemoryRecord(
        text="hello",
        embedding=[0.1, 0.2, 0.3],
        metadata={"k": "v"},
        step=7,
    )
    back = MemoryRecord.from_json(rec.to_json())
    assert back == rec


# --- Ranking ordering stability ---------------------------------------------


def test_memory_retrieve_breaks_ties_by_insertion_order() -> None:
    """Two records with identical embeddings should preserve insertion order in ties."""

    def const_embed(_text: str) -> list[float]:
        # Every record maps to the same vector → all ties.
        return [1.0, 0.0]

    mem = TrajectoryMemory(embedder=const_embed)
    mem.add("first")
    mem.add("second")
    mem.add("third")
    hits = mem.retrieve("query", k=3)
    assert [h.text for h in hits] == ["first", "second", "third"]
