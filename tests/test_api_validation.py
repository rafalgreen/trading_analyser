import json


def test_scraper_run_rejects_non_list_tickers(client):
    r = client.post("/api/scraper/run", json={"tickers": "AAPL,MSFT"})
    assert r.status_code == 422


def test_scraper_run_accepts_empty_body_as_all(client, monkeypatch):
    import app as m

    called = {}

    def fake(tickers=None):
        called["tickers"] = tickers
        return {"status": "started", "pid": 1, "count": 0, "scope": "all"}

    monkeypatch.setattr(m, "start_scraper_subprocess", fake)
    r = client.post("/api/scraper/run", json={})
    assert r.status_code == 200
    assert called["tickers"] is None


def test_scraper_run_passes_tickers(client, monkeypatch):
    import app as m

    seen = {}

    def fake(tickers=None):
        seen["tickers"] = tickers
        return {"status": "started", "pid": 2, "count": len(tickers or []), "scope": "subset"}

    monkeypatch.setattr(m, "start_scraper_subprocess", fake)
    r = client.post("/api/scraper/run", json={"tickers": ["AAPL", " ", "MSFT"]})
    assert r.status_code == 200
    assert seen["tickers"] == ["AAPL", "MSFT"]


def test_config_put_rejects_bad_schedule(client):
    r = client.put(
        "/api/config",
        json={"auto_schedule": {"enabled": True, "hour": 99, "minute": 30}},
    )
    assert r.status_code == 422


def test_config_put_rejects_non_list_tickers(client):
    r = client.put("/api/config", json={"tickers": "AAPL"})
    assert r.status_code == 422


def test_config_put_accepts_valid_payload(client, tmp_path, monkeypatch):
    import app as m

    cfg_file = tmp_path / "scraper_config.json"
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(m, "reschedule_auto_scraper", lambda: None)

    r = client.put(
        "/api/config",
        json={
            "tickers": ["AAPL", " ", "MSFT "],
            "intervals": ["1D", "1W"],
            "indicators": ["PCA", "MacD"],
            "auto_schedule": {
                "enabled": True,
                "hour": 7,
                "minute": 30,
                "run_on_startup": True,
            },
        },
    )
    assert r.status_code == 200
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["tickers"] == ["AAPL", "MSFT"]
    assert saved["auto_schedule"]["enabled"] is True
