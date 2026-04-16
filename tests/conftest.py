import os

os.environ["PYTEST_RUNNING"] = "1"

import pytest
from fastapi.testclient import TestClient

# Skrypty integracyjne (Playwright + CDP) — nie uruchamiać w zwykłym CI
collect_ignore = ["test_clear.py", "test_macd.py", "test_add_indicator.py"]


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    import app as m

    res = tmp_path / "results"
    res.mkdir()
    dat = tmp_path / "data"
    dat.mkdir()
    monkeypatch.setattr(m, "RESULTS_DIR", str(res))
    monkeypatch.setattr(m, "DATA_DIR", str(dat))
    monkeypatch.setattr(m, "_watchlist_cache", None)
    yield m, res, dat


@pytest.fixture
def client(app_env):
    m, _res, _dat = app_env
    return TestClient(m.app)
