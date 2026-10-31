"""Unit tests for the incremental FileCache."""

from app.analyzer.cache import FileCache


def test_roundtrip(tmp_path):
    cache = FileCache(tmp_path)
    cache.store("src/a.js", "export const x = 1", {"rel": "src/a.js", "module": "a"})
    assert cache.load("src/a.js", "export const x = 1") == {"rel": "src/a.js", "module": "a"}
    assert cache.stats["files_reused"] == 1


def test_content_change_is_a_miss(tmp_path):
    cache = FileCache(tmp_path)
    cache.store("src/a.js", "v1", {"rel": "src/a.js"})
    cache.misses = 0
    assert cache.load("src/a.js", "v2") is None
    assert cache.load("src/a.js", "v1") is not None


def test_path_is_part_of_the_key(tmp_path):
    cache = FileCache(tmp_path)
    cache.store("src/a.js", "same", {"rel": "src/a.js"})
    assert cache.load("other/a.js", "same") is None


def test_corrupt_entry_is_a_miss(tmp_path):
    cache = FileCache(tmp_path)
    cache.store("a.js", "x", {"rel": "a.js"})
    for f in tmp_path.glob("*.json"):
        f.write_text("{not json", encoding="utf-8")
    assert cache.load("a.js", "x") is None


def test_disabled_cache_never_reads_or_writes(tmp_path):
    cache = FileCache(tmp_path, use_cache=False)
    cache.store("a.js", "x", {"rel": "a.js"})
    assert cache.load("a.js", "x") is None
    assert not any(tmp_path.iterdir())


def test_env_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVDNA_INCREMENTAL_CACHE", "0")
    cache = FileCache(tmp_path)
    cache.store("a.js", "x", {"rel": "a.js"})
    assert cache.load("a.js", "x") is None
    assert cache.stats["enabled"] is False


def test_pickle_format(tmp_path):
    import ast

    cache = FileCache(tmp_path, fmt="pickle")
    tree = ast.parse("x = 1")
    cache.store("a.py", "x = 1", {"tree": tree, "lines": ["x = 1"]})
    loaded = cache.load("a.py", "x = 1")
    assert isinstance(loaded["tree"], ast.Module)
    assert ast.dump(loaded["tree"]) == ast.dump(tree)


def test_store_failure_never_raises(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("I am a file", encoding="utf-8")
    cache = FileCache(blocker)  # mkdir on an existing file raises FileExistsError
    cache.store("a.js", "x", {"rel": "a.js"})  # must not raise
    assert cache.stats["cache_writes_failed"] >= 1


def test_entry_requires_matching_rel(tmp_path):
    import json

    cache = FileCache(tmp_path)
    payload = tmp_path / "deadbeef.json"
    payload.write_text(json.dumps({"rel": "other.js", "module": "x"}), encoding="utf-8")
    assert cache.load("a.js", "x") is None
