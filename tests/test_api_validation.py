import json
import sys
import textwrap
import subprocess


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


def test_scraper_run_no_data_only_starts_subset(client, app_env, monkeypatch):
    import app as m

    _m, res, _dat = app_env
    f = res / "tradingview_results_2026-05-04.csv"
    f.write_text(
        textwrap.dedent(
            """\
            Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,PCA_Values,HTS Panel_Values,MacD_Values
            AAA,AAA,,-,NO_DATA,,Brak danych na wykresie,Brak poprawnych danych,Brak danych na wykresie
            BBB,BBB,,1D,OK,,Brak danych na wykresie,Brak poprawnych danych,Brak danych na wykresie
            CCC,CCC,,1D,OK,,12.3 (Niebieski),ok,ok
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        m, "load_config", lambda: {"indicators": ["PCA", "HTS Panel", "MacD"]}
    )

    called = {}

    def fake(tickers=None):
        called["tickers"] = list(tickers or [])
        return {"status": "started", "pid": 321, "count": len(tickers or []), "scope": "subset"}

    monkeypatch.setattr(m, "start_scraper_subprocess", fake)
    r = client.post("/api/scraper/run", json={"no_data_only": True})
    assert r.status_code == 200
    assert called["tickers"] == ["AAA", "BBB"]
    body = r.json()
    assert body["status"] == "started"
    assert body["scope"] == "subset"
    assert body["count"] == 2


def test_scraper_run_no_data_only_empty_when_no_results(client, app_env, monkeypatch):
    import app as m

    monkeypatch.setattr(
        m, "start_scraper_subprocess", lambda tickers=None: {"status": "started"}
    )
    r = client.post("/api/scraper/run", json={"no_data_only": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "no_data_empty"
    assert body["count"] == 0


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


def _write_results_csv(res_dir, date_id, rows):
    path = res_dir / f"tradingview_results_{date_id}.csv"
    header = "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,PCA_Values\n"
    body = "".join(
        f"{r['ticker']},{r.get('company','Name')},,{r['interval']},OK,,\"{r['pca']}\"\n"
        for r in rows
    )
    path.write_text(header + body, encoding="utf-8")


def test_ticker_history_returns_sorted_series(client, app_env):
    _m, res, _dat = app_env
    _write_results_csv(res, "2026-04-12", [
        {"ticker": "AAPL", "interval": "1D", "pca": "61,33 (color: rgb(255, 243, 0);)"},
    ])
    _write_results_csv(res, "2026-04-10", [
        {"ticker": "AAPL", "interval": "1D", "pca": "-5,1 (color: rgb(239, 68, 68);)"},
        {"ticker": "AAPL", "interval": "1W", "pca": "12,0 (Zielony)"},
    ])
    _write_results_csv(res, "2026-04-11", [
        {"ticker": "AAPL", "interval": "1D", "pca": "3,5 (Niebieski)"},
    ])

    r = client.get("/api/ticker/AAPL/history?interval=1D")
    assert r.status_code == 200
    payload = r.json()
    assert payload["ticker"] == "AAPL"
    assert payload["interval"] == "1D"
    dates = [p["date"] for p in payload["history"]]
    assert dates == sorted(dates)
    assert len(payload["history"]) == 3
    assert payload["history"][0]["value"] == -5.1
    assert payload["history"][-1]["value"] == 61.33


def test_ticker_history_rejects_bad_ticker(client):
    r = client.get("/api/ticker/lowercase/history")
    assert r.status_code == 400


def test_ticker_history_rejects_bad_interval(client):
    r = client.get("/api/ticker/AAPL/history?interval=4H")
    assert r.status_code == 400


def test_ticker_history_empty_when_no_files(client, app_env):
    r = client.get("/api/ticker/AAPL/history?interval=1D")
    assert r.status_code == 200
    assert r.json()["history"] == []


# --- Ticker rename --------------------------------------------------------

def test_rename_ticker_happy_path(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({
            "tickers": ["AAPL", "PAS1", "MSFT"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    r = client.post("/api/tickers/rename", json={"old": "PAS1", "new": "PAS1.WA"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "renamed"
    assert data["new"] == "PAS1.WA"
    assert data["tickers"] == ["AAPL", "PAS1.WA", "MSFT"]

    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["tickers"] == ["AAPL", "PAS1.WA", "MSFT"]


def test_rename_ticker_case_insensitive_match(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({"tickers": ["aapl"], "intervals": ["1D"], "indicators": ["PCA"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    r = client.post("/api/tickers/rename", json={"old": "aapl", "new": "AAPL.US"})
    assert r.status_code == 200
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["tickers"] == ["AAPL.US"]


def test_rename_ticker_rejects_invalid_new(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({"tickers": ["AAPL"], "intervals": ["1D"], "indicators": ["PCA"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    r = client.post("/api/tickers/rename", json={"old": "AAPL", "new": "has space"})
    assert r.status_code == 400


def test_rename_ticker_matches_by_base_symbol(client, app_env, tmp_path, monkeypatch):
    """LULU.O w karcie/CSV powinno zmatchować wpis 'LULU' w configu."""
    m, _res, _dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({
            "tickers": ["AAPL", "LULU", "MSFT.O"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    r = client.post("/api/tickers/rename", json={"old": "LULU.O", "new": "LULU.US"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "renamed"
    assert data["old"] == "LULU"
    assert data["requested_old"] == "LULU.O"
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["tickers"] == ["AAPL", "LULU.US", "MSFT.O"]


def test_rename_ticker_ambiguous_base_match(client, app_env, tmp_path, monkeypatch):
    """Gdy w configu są dwa wpisy o tym samym 'base', backend zwraca 409."""
    m, _res, _dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({
            "tickers": ["LULU", "LULU.WA"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    r = client.post("/api/tickers/rename", json={"old": "LULU.O", "new": "LULU.US"})
    assert r.status_code == 409


def test_rename_ticker_not_found(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({"tickers": ["AAPL"], "intervals": ["1D"], "indicators": ["PCA"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    r = client.post("/api/tickers/rename", json={"old": "MSFT", "new": "MSFT.US"})
    assert r.status_code == 404


def test_rename_ticker_conflict(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({
            "tickers": ["AAPL", "MSFT"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    r = client.post("/api/tickers/rename", json={"old": "AAPL", "new": "MSFT"})
    assert r.status_code == 409


def test_rename_ticker_same_name(client):
    r = client.post("/api/tickers/rename", json={"old": "AAPL", "new": "AAPL"})
    assert r.status_code == 400


# --- Missing_Indicators annotation in /api/results ------------------------

def test_results_annotates_missing_indicators(client, app_env, monkeypatch):
    m, res, _dat = app_env
    # Overwrite indicators list via config file
    import json as _json
    cfg_path = res.parent / "scraper_config.json"
    cfg_path.write_text(
        _json.dumps({
            "tickers": ["PAS1"],
            "intervals": ["1D"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_path))

    # Write CSV where Scrape_Status=OK but all indicator columns are missing/placeholder.
    date_id = "2026-04-17"
    path = res / f"tradingview_results_{date_id}.csv"
    header = (
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,"
        "HTS Panel_Values,MacD_Values,PCA_Values\n"
    )
    body = (
        'PAS1,PAS1,,1D,OK,,'
        '"Brak poprawnych danych","Brak poprawnych danych","Brak danych na wykresie"\n'
    )
    path.write_text(header + body, encoding="utf-8")

    r = client.get(f"/api/results/{date_id}")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert len(rows) == 1
    row = rows[0]
    assert set(row.get("Missing_Indicators") or []) == {"PCA", "HTS Panel", "MacD"}
    assert row.get("All_Indicators_Missing") is True


# --- Stop scraper ---------------------------------------------------------

def test_stop_scraper_when_idle(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    monkeypatch.setattr(m, "STATUS_FILE", str(tmp_path / "scraper_status.json"))
    # _scraper_process is None → not_running (UI sprząta)
    monkeypatch.setattr(m, "_scraper_process", None)
    r = client.post("/api/scraper/stop")
    assert r.status_code == 200
    assert r.json()["status"] in {"not_running", "stopped"}


def test_stop_scraper_kills_orphan_via_pgrep(client, app_env, tmp_path, monkeypatch):
    """Gdy _scraper_process = None i status file nie zawiera PID (legacy),
    Stop musi znaleźć scraper po nazwie polecenia (pgrep fallback).
    """
    import os as _os
    import pytest
    if _os.name != "posix":
        pytest.skip("pgrep fallback is POSIX-only")

    # W niektórych środowiskach sandboxowych pgrep nie potrafi odczytać listy
    # procesów (np. macOS sysmond). W takim wypadku pomijamy test.
    probe = subprocess.run(["pgrep", "-f", "python"], capture_output=True, text=True)
    if probe.returncode not in (0, 1):
        pytest.skip("pgrep unavailable in this environment")

    m, _res, _dat = app_env
    status_file = tmp_path / "scraper_status.json"
    monkeypatch.setattr(m, "STATUS_FILE", str(status_file))

    # Imitujemy proces scrapera — musi zawierać w argv "tv_scraper.py", żeby pgrep -f go znalazł.
    fake_script = tmp_path / "tv_scraper.py"
    fake_script.write_text(
        "import time\nwhile True: time.sleep(1)\n", encoding="utf-8"
    )

    popen_kwargs = dict(
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    proc = subprocess.Popen([sys.executable, str(fake_script)], **popen_kwargs)
    try:
        monkeypatch.setattr(m, "_scraper_process", None)
        # status file celowo bez "pid" — wymusza fallback po nazwie.
        status_file.write_text(
            json.dumps({"status": "running"}), encoding="utf-8"
        )

        r = client.post("/api/scraper/stop")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "stopped"
        assert body.get("orphan_killed") is True

        rc = proc.wait(timeout=5)
        assert rc is not None
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            pass


def test_stop_scraper_kills_running_process(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    status_file = tmp_path / "scraper_status.json"
    monkeypatch.setattr(m, "STATUS_FILE", str(status_file))

    # Startujemy prawdziwy, długo działający subprocess w nowej grupie.
    popen_kwargs = dict(
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    import os as _os
    if _os.name == "posix":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time\nwhile True: time.sleep(1)"],
        **popen_kwargs,
    )
    try:
        monkeypatch.setattr(m, "_scraper_process", proc)
        # Ustaw plik statusu jakby scraper pracował.
        status_file.write_text(
            json.dumps({"status": "running", "pid": proc.pid}),
            encoding="utf-8",
        )

        r = client.post("/api/scraper/stop")
        assert r.status_code == 200
        assert r.json()["status"] == "stopped"

        # Proces musi być martwy w krótkim czasie.
        rc = proc.wait(timeout=5)
        assert rc is not None

        saved = json.loads(status_file.read_text(encoding="utf-8"))
        assert saved["status"] == "stopped"
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            pass
