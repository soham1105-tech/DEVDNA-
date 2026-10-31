import os
import sys
import tempfile
from pathlib import Path

# Isolate every test session: temporary data dir/database, and never a real API key.
_TMP = tempfile.mkdtemp(prefix="devdna-test-")
os.environ["DEVDNA_DATA_DIR"] = _TMP
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("DEVDNA_ALLOWED_LOCAL_ROOTS", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app import db as dbmod  # noqa: E402
from app.config import settings  # noqa: E402
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("DEVDNA_ALLOWED_LOCAL_ROOTS", None)
settings.allowed_local_roots = []

BACKEND = Path(__file__).resolve().parent.parent
SAMPLE = BACKEND / "sample_project"


@pytest.fixture(autouse=True)
def _rate_limit_fresh():
    """Every test starts with an empty rate-limit bucket; the burst test
    overrides the limit itself and restores the default afterwards."""
    from app.api import guard

    guard.reset_rate_limit()
    guard.set_rate_limit(None)
    yield
    guard.reset_rate_limit()
    guard.set_rate_limit(None)


@pytest.fixture(scope="session", autouse=True)
def _engine():
    settings.data_dir = Path(_TMP)
    dbmod.init_engine(f"sqlite:///{Path(_TMP, 'test.db').as_posix()}")
    yield


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def sample_project(client):
    """Load and analyse the bundled sample once (background tasks run synchronously in TestClient)."""
    r = client.post("/api/projects/sample")
    assert r.status_code == 201, r.text
    pid = r.json()["project"]["id"]
    run = client.get(f"/api/runs/{r.json()['run']['id']}").json()
    assert run["status"] == "completed", run
    return pid


@pytest.fixture(scope="session")
def sample_analysis():
    from app.analyzer.python_analyzer import analyse_project

    return analyse_project(SAMPLE)


@pytest.fixture(scope="session")
def sample_graph(sample_analysis):
    from app.graph.engine import build_graph

    return build_graph(sample_analysis["components"], sample_analysis["edges"])
