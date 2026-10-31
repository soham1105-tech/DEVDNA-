"""Unit tests for the incremental FileCache."""

import json

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


def test_tampered_payload_is_rejected(tmp_path):
    cache = FileCache(tmp_path)
    cache.store("a.py", "x = 1", {"rel": "a.py", "lines": ["x = 1"]})
    path = next(tmp_path.glob("*.json"))
    magic, header, payload = path.read_bytes().split(b"\n", 2)
    model = json.loads(payload)
    model["lines"] = ["tampered = True"]
    path.write_bytes(magic + b"\n" + header + b"\n" + json.dumps(model).encode())
    assert cache.load("a.py", "x = 1") is None  # HMAC mismatch -> miss


def test_pickle_payload_is_rejected_not_executed(tmp_path):
    """A pickle blob left at an entry path is never loaded, let alone executed."""
    import pickle

    class Detonate:
        def __reduce__(self):
            # raising during pickle.loads proves the payload was executed;
            # __reduce__ itself only builds the reduction at dump time
            return (RuntimeError, ("pickle execution attempted",))

    cache = FileCache(tmp_path)
    cache.store("a.py", "x = 1", {"rel": "a.py"})
    path = next(tmp_path.glob("*.json"))
    path.write_bytes(pickle.dumps({"rel": "a.py", "x": Detonate()}))
    assert cache.load("a.py", "x = 1") is None


def test_unsigned_json_is_rejected(tmp_path):
    import json

    cache = FileCache(tmp_path)
    payload = tmp_path / "deadbeef.json"
    payload.write_text(json.dumps({"rel": "a.py", "lines": ["x = 1"]}), encoding="utf-8")
    assert cache.load("a.py", "x = 1") is None  # unsigned entry -> miss


def test_store_failure_never_raises(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("I am a file", encoding="utf-8")
    cache = FileCache(blocker)  # mkdir on an existing file raises FileExistsError
    cache.store("a.js", "x", {"rel": "a.js"})  # must not raise
    assert cache.stats["cache_writes_failed"] >= 1


def test_entry_requires_matching_rel(tmp_path):
    cache = FileCache(tmp_path)
    cache.store("a.js", "x", {"rel": "a.js"})
    assert cache.load("other.js", "x") is None


def test_cache_files_never_contain_secret_literals(tmp_path):
    """password = "hunter2hunter2" must leave no secret literal in any cache
    file: models hold only redacted lines, and entries are signed JSON."""
    import json as jsonlib

    from app.analyzer.dispatch import analyse_repository

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "cfg.py").write_text(
        'password = "hunter2hunter2"\nAPI_TOKEN = "sk-live-abcdef1234567890XYZ"\n',
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"
    analyse_repository(repo, cache_dir=cache_dir)

    entries = [p for p in cache_dir.rglob("*") if p.is_file() and p.suffix == ".json"]
    assert entries  # models were actually written
    for entry in entries:
        blob = entry.read_bytes()
        assert b"hunter2hunter2" not in blob
        assert b"sk-live" not in blob and b"sk_live" not in blob
        assert blob.startswith(b"devdna-cache-v1\n")  # signed JSON, never pickle
        assert not blob.startswith(b"\x80")
        jsonlib.loads(blob.split(b"\n", 2)[2])  # payload is valid JSON

    # and the API-facing evidence is redacted too
    result = analyse_repository(repo, cache_dir=cache_dir)
    assert "hunter2hunter2" not in str(result["components"]) + str(result["secret_hits"])
