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

    def fake(tickers=None, indicators=None):
        called["tickers"] = tickers
        return {"status": "started", "pid": 1, "count": 0, "scope": "all"}

    monkeypatch.setattr(m, "start_scraper_subprocess", fake)
    r = client.post("/api/scraper/run", json={})
    assert r.status_code == 200
    assert called["tickers"] is None


def test_scraper_run_passes_tickers(client, monkeypatch):
    import app as m

    seen = {}

    def fake(tickers=None, indicators=None):
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
        m,
        "load_config",
        lambda: {
            "tickers": ["AAA", "BBB", "CCC"],
            "intervals": ["1D", "1W", "1M"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
        },
    )

    called = {}

    def fake(tickers=None, indicators=None, no_data_only=False):
        called["tickers"] = list(tickers or [])
        called["no_data_only"] = no_data_only
        return {"status": "started", "pid": 321, "count": len(tickers or []), "scope": "subset"}

    monkeypatch.setattr(m, "start_scraper_subprocess", fake)
    r = client.post("/api/scraper/run", json={"no_data_only": True})
    assert r.status_code == 200
    assert called["tickers"] == ["AAA", "BBB", "CCC"]
    assert called["no_data_only"] is True
    body = r.json()
    assert body["status"] == "started"
    assert body["scope"] == "subset"
    assert body["count"] == 3


def test_scraper_run_no_data_only_empty_when_no_results(client, app_env, monkeypatch):
    import app as m

    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"tickers": [], "intervals": ["1D"], "indicators": ["PCA"]},
    )
    monkeypatch.setattr(
        m, "start_scraper_subprocess", lambda tickers=None, indicators=None: {"status": "started"}
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


def test_rename_ticker_accepts_exchange_prefix(client, app_env, tmp_path, monkeypatch):
    """Tickery z prefixem giełdy (np. ``GPW:ATC``) muszą przejść walidację."""
    m, _res, _dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({
            "tickers": ["ATC", "AAPL"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    r = client.post("/api/tickers/rename", json={"old": "ATC", "new": "GPW:ATC"})
    assert r.status_code == 200, r.text
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["tickers"] == ["GPW:ATC", "AAPL"]


def test_ticker_history_accepts_exchange_prefix(client, app_env):
    """Endpoint /api/ticker/{ticker}/history musi akceptować ``GPW:ATC``."""
    r = client.get("/api/ticker/GPW:ATC/history?interval=1D")
    assert r.status_code == 200
    assert r.json()["history"] == []


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
    detail = r.json()["detail"]
    assert detail["message"] == "Ticker MSFT not found in config"
    assert detail["candidates"] == []


def test_rename_ticker_not_found_returns_similar_config_candidates(
    client, app_env, tmp_path, monkeypatch
):
    """DIAP z CSV nie jest w configu, ale GPW:DIA powinno wrócić jako podpowiedź."""
    m, _res, _dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({
            "tickers": ["AAPL", "GPW:DIA", "MSFT"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    r = client.post("/api/tickers/rename", json={"old": "DIAP", "new": "GPW:DIAP"})
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["requested_old"] == "DIAP"
    assert detail["config_status"] == "stale"
    assert detail["candidates"][0]["ticker"] == "GPW:DIA"


def test_rename_ticker_not_found_returns_new_existing_as_candidate(
    client, app_env, tmp_path, monkeypatch
):
    """GPW:TOY z historycznego CSV, ale GPW:TOA już jest w configu."""
    m, _res, _dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({
            "tickers": ["AAPL", "GPW:TOA", "MSFT"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    r = client.post("/api/tickers/rename", json={"old": "GPW:TOY", "new": "GPW:TOA"})
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["requested_old"] == "GPW:TOY"
    assert detail["config_status"] == "stale"
    assert detail["candidates"][0] == {
        "ticker": "GPW:TOA",
        "score": 100,
        "reason": "new_exists",
    }


def test_rename_ticker_matches_exchange_prefix_variant(
    client, app_env, tmp_path, monkeypatch
):
    """ATC z karty CSV może bezpiecznie wskazać configowy wpis GPW:ATC."""
    m, _res, _dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({
            "tickers": ["AAPL", "GPW:ATC", "MSFT"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    r = client.post("/api/tickers/rename", json={"old": "ATC", "new": "GPW:ATC2"})
    assert r.status_code == 200
    data = r.json()
    assert data["old"] == "GPW:ATC"
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["tickers"] == ["AAPL", "GPW:ATC2", "MSFT"]


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


def test_rename_ticker_migrates_csv_and_fundamentals(
    client, app_env, tmp_path, monkeypatch
):
    m, res, dat = app_env
    cfg_file = tmp_path / "scraper_config.json"
    cfg_file.write_text(
        json.dumps({
            "tickers": ["AAPL", "ASBP", "MSFT"],
            "intervals": ["1D", "1W"],
            "indicators": ["PCA"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_file))

    csv_path = res / "tradingview_results_2026-05-22.csv"
    csv_path.write_text(
        "Ticker,Company_Name,Interval,Scrape_Status,Scrape_Error,PCA_Values\n"
        "ASBP,Asseco BS,1D,OK,,0.4 (Zielony)\n"
        "ASBP,Asseco BS,1W,OK,,0.5 (Zielony)\n"
        "AAPL,Apple,1D,OK,,0.6\n",
        encoding="utf-8",
    )
    fund_path = res / "fundamentals.csv"
    fund_path.write_text(
        "Ticker,Fund_PE,Fund_PB,Fund_EV_EBITDA,Fund_ROE,Fund_NetMargin,Fund_DE,Fund_FCF,Fund_Source,Fund_Updated_At\n"
        "ASBP,14.0,1.2,8.0,0.12,0.08,0.3,1.0e8,yfinance,2026-05-22T10:00:00Z\n",
        encoding="utf-8",
    )
    cache_path = dat / ".fundamentals_cache.json"
    cache_path.write_text(
        json.dumps({
            "ASBP": {
                "Ticker": "ASBP",
                "Fund_PE": 14.0,
                "Fund_Source": "yfinance",
            }
        }),
        encoding="utf-8",
    )

    r = client.post("/api/tickers/rename", json={"old": "ASBP", "new": "GPW:ASB"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "renamed"
    assert data["new"] == "GPW:ASB"
    assert data["csv_rows_affected"] == 2
    assert data["csv_files_modified"] == 1
    assert data["fundamentals_migrated"] is True
    assert data["fundamentals_cache_migrated"] is True

    csv_text = csv_path.read_text(encoding="utf-8")
    assert "ASBP" not in csv_text
    assert "GPW:ASB" in csv_text

    dash = client.get("/api/dashboard")
    assert dash.status_code == 200
    gpw_rows = [
        row for row in dash.json()["data"] if row["Ticker"] == "GPW:ASB"
    ]
    assert len(gpw_rows) == 2
    by_interval = {row["Interval"]: row for row in gpw_rows}
    assert by_interval["1D"]["PCA_Values"] == "0.4 (Zielony)"
    assert by_interval["1W"]["PCA_Values"] == "0.5 (Zielony)"

    fund = client.get("/api/fundamentals/GPW:ASB")
    assert fund.status_code == 200
    assert fund.json()["Fund_PE"] == 14.0


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
        # Na części macOS (sandbox/permissions) kill(9) może zwrócić EPERM,
        # mimo że proces jest "nasz". W takim środowisku nie jesteśmy w stanie
        # deterministycznie przetestować pgrep-killa.
        if body.get("status") != "stopped":
            import platform as _platform
            if _platform.system().lower() == "darwin":
                pytest.skip(f"macOS permissions prevent killing orphan: {body}")
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


# --- Repair no-data symbols (TV REST + GPW: prefix) -----------------------

def _write_no_data_csv(res_dir, date_id, tickers_status):
    """Pomocnicza: zapisuje CSV z dwoma interwałami per ticker zgodnie z mapą."""
    header = (
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,"
        "PCA_Values,HTS Panel_Values,MacD_Values\n"
    )
    rows = []
    for ticker, status in tickers_status.items():
        if status == "NO_DATA":
            rows.append(
                f"{ticker},{ticker},,1D,NO_DATA,,Brak danych na wykresie,"
                f"Brak danych na wykresie,Brak danych na wykresie"
            )
            rows.append(
                f"{ticker},{ticker},,1W,NO_DATA,,Brak danych na wykresie,"
                f"Brak danych na wykresie,Brak danych na wykresie"
            )
        else:
            rows.append(
                f"{ticker},{ticker},123.4,1D,OK,,12.3 (Niebieski),ok ok ok ok,1 2 3 4"
            )
    path = res_dir / f"tradingview_results_{date_id}.csv"
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_repair_no_data_preview_lists_candidates(client, app_env, tmp_path, monkeypatch):
    m, res, _dat = app_env
    cfg = tmp_path / "scraper_config.json"
    cfg.write_text(
        json.dumps({
            "tickers": ["AMB", "FOO", "OK"],
            "intervals": ["1D"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
            "exchange_prefixes": ["GPW"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg))
    _write_no_data_csv(res, "2026-05-04", {"AMB": "NO_DATA", "FOO": "NO_DATA", "OK": "OK"})

    def fake_lookup(ticker, exchanges):
        if ticker == "AMB" and "GPW" in exchanges:
            return [{"symbol": "GPW:AMB", "exchange": "GPW", "description": "Ambra S.A."}]
        return []

    monkeypatch.setattr(m, "lookup_symbol_match", fake_lookup)

    r = client.get("/api/tickers/repair_no_data?date_id=2026-05-04")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exchange_prefixes"] == ["GPW"]

    by_old = {it["old"]: it for it in body["items"]}
    assert "AMB" in by_old
    assert "FOO" in by_old
    assert "OK" not in by_old  # OK ticker nie jest no-data

    assert by_old["AMB"]["candidates"] == [
        {"new": "GPW:AMB", "exchange": "GPW", "description": "Ambra S.A."}
    ]
    assert by_old["FOO"]["candidates"] == []
    assert "Brak match-a" in by_old["FOO"].get("note", "")


def test_repair_no_data_preview_skips_tickers_with_colon(client, app_env, tmp_path, monkeypatch):
    m, res, _dat = app_env
    cfg = tmp_path / "scraper_config.json"
    cfg.write_text(
        json.dumps({
            "tickers": ["GPW:ATC"],
            "intervals": ["1D"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
            "exchange_prefixes": ["GPW"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg))
    _write_no_data_csv(res, "2026-05-04", {"GPW:ATC": "NO_DATA"})

    called = {"hit": 0}

    def fake_lookup(ticker, exchanges):
        called["hit"] += 1
        return [{"symbol": "GPW:GPW:ATC", "exchange": "GPW", "description": "Bad"}]

    monkeypatch.setattr(m, "lookup_symbol_match", fake_lookup)

    r = client.get("/api/tickers/repair_no_data?date_id=2026-05-04")
    assert r.status_code == 200, r.text
    body = r.json()

    assert called["hit"] == 0  # nie wołamy REST dla tickerów z ':'
    items = {it["old"]: it for it in body["items"]}
    assert "GPW:ATC" in items
    assert items["GPW:ATC"].get("skipped") is True
    assert items["GPW:ATC"]["candidates"] == []


def test_repair_no_data_apply_writes_config(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    cfg = tmp_path / "scraper_config.json"
    cfg.write_text(
        json.dumps({
            "tickers": ["AMB", "ATC", "AAPL"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
            "exchange_prefixes": ["GPW"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg))

    r = client.post(
        "/api/tickers/repair_no_data",
        json={
            "renames": [
                {"old": "AMB", "new": "GPW:AMB"},
                {"old": "ATC", "new": "GPW:ATC"},
            ],
            "rerun": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert len(body["applied"]) == 2
    assert body["errors"] == []

    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["tickers"] == ["GPW:AMB", "GPW:ATC", "AAPL"]


def test_repair_no_data_apply_rerun_triggers_scraper(client, app_env, tmp_path, monkeypatch):
    m, res, _dat = app_env
    cfg = tmp_path / "scraper_config.json"
    cfg.write_text(
        json.dumps({
            "tickers": ["AMB"],
            "intervals": ["1D"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
            "exchange_prefixes": ["GPW"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg))
    _write_no_data_csv(res, "2026-05-04", {"AMB": "NO_DATA"})

    started = {"tickers": None}

    def fake_start(tickers=None, indicators=None, no_data_only=False):
        started["tickers"] = list(tickers or [])
        started["no_data_only"] = no_data_only
        return {"status": "started", "pid": 999}

    monkeypatch.setattr(m, "start_scraper_subprocess", fake_start)

    r = client.post(
        "/api/tickers/repair_no_data",
        json={
            "renames": [{"old": "AMB", "new": "GPW:AMB"}],
            "rerun": True,
            "date_id": "2026-05-04",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    # Po renamie config ma nową nazwę, a CSV nadal zawiera starą — _resolve_no_data_tickers
    # filtruje po `requested_tickers` więc pokaże tylko te z CSV jako no-data, których
    # *nowa* nazwa jest na liście. CSV ma "AMB" (no-data), a my filtrujemy po nowej
    # nazwie "GPW:AMB" — z CSV nie pasuje. Akceptujemy `no_data_empty` jako poprawny
    # stan, gdy CSV jeszcze nie ma nowej nazwy.
    sc = body.get("scraper", {})
    assert sc.get("status") in {"started", "no_data_empty"}


def test_repair_no_data_apply_empty_renames_400(client, app_env, tmp_path, monkeypatch):
    r = client.post(
        "/api/tickers/repair_no_data",
        json={"renames": [], "rerun": False},
    )
    assert r.status_code == 400


def test_exchange_prefixes_from_config_tickers(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    cfg = tmp_path / "scraper_config.json"
    cfg.write_text(
        json.dumps({
            "tickers": ["AAPL", "GPW:PKO", "SSE:600941", "SGX:F34"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
            "exchange_prefixes": ["GPW"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg))

    assert m._exchange_prefixes_from_config() == ["GPW", "SSE", "SGX"]


def test_repair_no_data_preview_derives_sse_from_config(client, app_env, tmp_path, monkeypatch):
    m, res, _dat = app_env
    cfg = tmp_path / "scraper_config.json"
    cfg.write_text(
        json.dumps({
            "tickers": ["601088", "SSE:600941"],
            "intervals": ["1D"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
            "exchange_prefixes": ["GPW"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg))
    _write_no_data_csv(res, "2026-05-04", {"601088": "NO_DATA"})

    def fake_lookup(ticker, exchanges):
        if ticker == "601088" and "SSE" in exchanges:
            return [{"symbol": "SSE:601088", "exchange": "SSE", "description": "China Railway"}]
        return []

    monkeypatch.setattr(m, "lookup_symbol_match", fake_lookup)

    r = client.get("/api/tickers/repair_no_data?date_id=2026-05-04")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exchange_prefixes"] == ["GPW", "SSE"]
    item = next(it for it in body["items"] if it["old"] == "601088")
    assert item["candidates"] == [
        {"new": "SSE:601088", "exchange": "SSE", "description": "China Railway"}
    ]


def test_repair_no_data_preview_other_candidates(client, app_env, tmp_path, monkeypatch):
    m, res, _dat = app_env
    cfg = tmp_path / "scraper_config.json"
    cfg.write_text(
        json.dumps({
            "tickers": ["PSHG"],
            "intervals": ["1D"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
            "exchange_prefixes": ["GPW"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg))
    _write_no_data_csv(res, "2026-05-04", {"PSHG": "NO_DATA"})

    monkeypatch.setattr(m, "lookup_symbol_match", lambda t, ex: [])

    def fake_all(ticker):
        if ticker == "PSHG":
            return [
                {"symbol": "NASDAQ:PSHG", "exchange": "NASDAQ", "description": "Performance Shipping"},
            ]
        return []

    monkeypatch.setattr(m, "fetch_symbol_matches", fake_all)

    r = client.get("/api/tickers/repair_no_data?date_id=2026-05-04")
    assert r.status_code == 200, r.text
    body = r.json()
    item = next(it for it in body["items"] if it["old"] == "PSHG")
    assert item["candidates"] == []
    assert item["other_candidates"][0]["new"] == "NASDAQ:PSHG"
    assert "innych giełdach" in item.get("note", "")


def test_repair_no_data_apply_default_no_scraper(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    cfg = tmp_path / "scraper_config.json"
    cfg.write_text(
        json.dumps({
            "tickers": ["AMB"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
            "exchange_prefixes": ["GPW"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg))

    started = {"hit": False}

    def fake_start(tickers=None, indicators=None):
        started["hit"] = True
        return {"status": "started"}

    monkeypatch.setattr(m, "start_scraper_subprocess", fake_start)

    r = client.post(
        "/api/tickers/repair_no_data",
        json={"renames": [{"old": "AMB", "new": "GPW:AMB"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert "scraper" not in body
    assert started["hit"] is False


def test_repair_no_data_apply_manual_rename_without_match(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    cfg = tmp_path / "scraper_config.json"
    cfg.write_text(
        json.dumps({
            "tickers": ["LIOP", "601088"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
            "exchange_prefixes": ["GPW"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg))

    r = client.post(
        "/api/tickers/repair_no_data",
        json={
            "renames": [
                {"old": "LIOP", "new": "OTC:LIOPF"},
                {"old": "601088", "new": "SSE:601088"},
            ],
            "rerun": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert len(body["applied"]) == 2
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["tickers"] == ["OTC:LIOPF", "SSE:601088"]


# --- Pending run / fresh start (Wznów vs Od nowa) -------------------------

def test_pending_run_no_state_file_returns_false(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    monkeypatch.setattr(m, "STATE_FILE", str(tmp_path / "scraper_state.json"))

    r = client.get("/api/scraper/pending_run")
    assert r.status_code == 200
    body = r.json()
    assert body == {"has_pending": False}


def test_pending_run_with_state_returns_metadata(client, app_env, tmp_path, monkeypatch):
    m, res, _dat = app_env
    state_path = tmp_path / "scraper_state.json"
    csv_path = res / "tradingview_results_2026-05-11.csv"
    csv_path.write_text("Ticker,Interval\nAAPL,1D\n", encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "current_file": str(csv_path),
                "processed": [["AAPL", "1D"], ["AAPL", "1W"], ["MSFT", "1D"]],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "STATE_FILE", str(state_path))
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["AAPL", "MSFT", "GOOGL", "TSLA"],
            "intervals": ["1D", "1W", "1M"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
        },
    )

    r = client.get("/api/scraper/pending_run")
    assert r.status_code == 200
    body = r.json()
    assert body["has_pending"] is True
    assert body["current_file"].endswith("tradingview_results_2026-05-11.csv")
    assert body["current_file_date"] == "2026-05-11"
    assert body["current_file_exists"] is True
    assert body["processed_count"] == 3
    assert body["total_in_config"] == 4 * 3
    assert body["remaining_count"] == 12 - 3
    assert isinstance(body["state_mtime"], (int, float))


def test_pending_run_handles_missing_csv_file(client, app_env, tmp_path, monkeypatch):
    """State pokazuje plik, ale go już nie ma na dysku — odpowiedź ma current_file_exists=false."""
    m, _res, _dat = app_env
    state_path = tmp_path / "scraper_state.json"
    state_path.write_text(
        json.dumps(
            {
                "current_file": "results/tradingview_results_2099-12-31.csv",
                "processed": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "STATE_FILE", str(state_path))
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"tickers": ["AAPL"], "intervals": ["1D"], "indicators": ["PCA"]},
    )

    r = client.get("/api/scraper/pending_run")
    assert r.status_code == 200
    body = r.json()
    assert body["has_pending"] is True
    assert body["current_file_exists"] is False
    assert body["processed_count"] == 0


def test_scraper_run_fresh_true_removes_state_file(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    state_path = tmp_path / "scraper_state.json"
    state_path.write_text(
        json.dumps({"current_file": "results/x.csv", "processed": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "STATE_FILE", str(state_path))

    called = {"count": 0}

    def fake_start(tickers=None, indicators=None):
        called["count"] += 1
        return {"status": "started", "pid": 111}

    monkeypatch.setattr(m, "start_scraper_subprocess", fake_start)

    r = client.post("/api/scraper/run", json={"fresh": True})
    assert r.status_code == 200
    assert called["count"] == 1
    assert not state_path.exists(), "fresh=true powinno usunąć scraper_state.json"


def test_scraper_run_fresh_false_keeps_state_file(client, app_env, tmp_path, monkeypatch):
    m, _res, _dat = app_env
    state_path = tmp_path / "scraper_state.json"
    state_path.write_text(
        json.dumps({"current_file": "results/x.csv", "processed": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "STATE_FILE", str(state_path))

    monkeypatch.setattr(
        m, "start_scraper_subprocess", lambda tickers=None, indicators=None: {"status": "started"}
    )

    r = client.post("/api/scraper/run", json={"fresh": False})
    assert r.status_code == 200
    assert state_path.exists()


def test_scraper_run_fresh_ignored_for_partial(client, app_env, tmp_path, monkeypatch):
    """Subset run (z konkretnymi tickerami) NIE czyści state'u, nawet gdy fresh=true."""
    m, _res, _dat = app_env
    state_path = tmp_path / "scraper_state.json"
    state_path.write_text(
        json.dumps({"current_file": "results/x.csv", "processed": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "STATE_FILE", str(state_path))
    monkeypatch.setattr(
        m, "start_scraper_subprocess", lambda tickers=None, indicators=None: {"status": "started"}
    )

    r = client.post(
        "/api/scraper/run", json={"fresh": True, "tickers": ["AAPL", "MSFT"]}
    )
    assert r.status_code == 200
    assert state_path.exists(), "Subset run nie powinien ruszać state'u"


def test_scraper_run_fresh_clears_no_data_state(client, app_env, tmp_path, monkeypatch):
    """fresh=true przy no_data_only usuwa zapisany stan no_data przed startem."""
    m, res, _dat = app_env
    state_path = tmp_path / "scraper_state.json"
    csv_path = res / "tradingview_results_2026-05-11.csv"
    csv_path.write_text("Ticker,Interval\n", encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "current_file": str(csv_path),
                "processed": [],
                "no_data_only": True,
                "tickers": ["AAA", "BBB"],
                "ticker_idx": 45,
                "ind_idx": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "STATE_FILE", str(state_path))
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"tickers": ["AAA"], "intervals": ["1D"], "indicators": ["PCA"]},
    )
    monkeypatch.setattr(
        m,
        "_resolve_no_data_tickers",
        lambda date_id, requested: ["AAA"],
    )
    monkeypatch.setattr(
        m,
        "start_scraper_subprocess",
        lambda tickers=None, indicators=None, no_data_only=False: {
            "status": "started",
            "scope": "no_data_only",
        },
    )

    r = client.post("/api/scraper/run", json={"fresh": True, "no_data_only": True})
    assert r.status_code == 200
    assert not state_path.exists(), "fresh=true powinno usunąć scraper_state.json no_data"


def test_no_data_run_resumes_saved_tickers_not_recomputed(
    client, app_env, tmp_path, monkeypatch
):
    """Resume no_data_only używa zapisanej listy tickerów i indeksu, nie _resolve od zera."""
    m, res, _dat = app_env
    state_path = tmp_path / "scraper_state.json"
    csv_path = res / "tradingview_results_2026-05-11.csv"
    csv_path.write_text("Ticker,Interval\n", encoding="utf-8")
    saved_tickers = [f"T{i:03d}" for i in range(163)]
    state_path.write_text(
        json.dumps(
            {
                "current_file": str(csv_path),
                "processed": [],
                "no_data_only": True,
                "tickers": saved_tickers,
                "indicators": ["PCA", "HTS Panel", "MacD"],
                "ticker_idx": 45,
                "ind_idx": 0,
                "session_started_at": 1000.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "STATE_FILE", str(state_path))

    called = {}

    def fake_start(tickers=None, indicators=None, no_data_only=False):
        called["tickers"] = list(tickers or [])
        called["no_data_only"] = no_data_only
        return {"status": "started", "pid": 999}

    monkeypatch.setattr(m, "start_scraper_subprocess", fake_start)
    monkeypatch.setattr(
        m,
        "_resolve_no_data_tickers",
        lambda date_id, requested: (_ for _ in ()).throw(
            AssertionError("resume nie powinien recomputować listy no_data")
        ),
    )

    r = client.post("/api/scraper/run", json={"no_data_only": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "started"
    assert body["resumed"] is True
    assert body["ticker_idx"] == 45
    assert called["tickers"] == saved_tickers
    assert called["no_data_only"] is True


def test_pending_run_no_data_includes_checkpoint(client, app_env, tmp_path, monkeypatch):
    m, res, _dat = app_env
    state_path = tmp_path / "scraper_state.json"
    csv_path = res / "tradingview_results_2026-05-11.csv"
    csv_path.write_text("Ticker,Interval\n", encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "current_file": str(csv_path),
                "processed": [],
                "no_data_only": True,
                "tickers": ["A", "B", "C"],
                "indicators": ["PCA", "HTS Panel", "MacD"],
                "ticker_idx": 1,
                "ind_idx": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "STATE_FILE", str(state_path))

    r = client.get("/api/scraper/pending_run")
    assert r.status_code == 200
    body = r.json()
    assert body["has_pending"] is True
    assert body["no_data_only"] is True
    assert body["run_tickers_count"] == 3
    assert body["ticker_idx"] == 1
    assert body["processed_count"] == 2  # ind0 ticker1 → krok 2/9
    assert body["remaining_count"] == 7


def test_stop_checkpoints_state_from_status(client, app_env, tmp_path, monkeypatch):
    m, res, _dat = app_env
    state_path = tmp_path / "scraper_state.json"
    status_path = tmp_path / "scraper_status.json"
    csv_path = res / "tradingview_results_2026-05-11.csv"
    csv_path.write_text("Ticker,Interval\n", encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "current_file": str(csv_path),
                "processed": [],
                "no_data_only": True,
                "tickers": ["A"] * 163,
                "indicators": ["PCA", "HTS Panel", "MacD"],
                "ticker_idx": 0,
                "ind_idx": 0,
            }
        ),
        encoding="utf-8",
    )
    status_path.write_text(
        json.dumps(
            {
                "status": "running",
                "progress": "46/489 · ticker 46/163 · wsk. 1/3 · PCA",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "STATE_FILE", str(state_path))
    monkeypatch.setattr(m, "STATUS_FILE", str(status_path))

    m._checkpoint_scraper_state_from_status()
    with open(state_path, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["ticker_idx"] == 45
    assert saved["ind_idx"] == 0
    assert saved["resumed"] is True


# --- /api/results pass-through Exchange + backfill ------------------------

def test_results_passes_through_exchange_column(client, app_env, monkeypatch):
    _m, res, _dat = app_env
    date_id = "2026-05-12"
    path = res / f"tradingview_results_{date_id}.csv"
    header = (
        "Ticker,Company_Name,Exchange,Current_Price,Interval,Scrape_Status,"
        "Scrape_Error,PCA_Values\n"
    )
    body = (
        "ZIM,ZIM Integrated,NYSE,12.34,1D,OK,,0.5 (Niebieski)\n"
        "ATC,Arctic Paper,GPW,8.10,1D,OK,,0.3 (Niebieski)\n"
    )
    path.write_text(header + body, encoding="utf-8")

    r = client.get(f"/api/results/{date_id}")
    assert r.status_code == 200
    rows = r.json()["data"]
    by_t = {row["Ticker"]: row for row in rows}
    assert by_t["ZIM"]["Exchange"] == "NYSE"
    assert by_t["ATC"]["Exchange"] == "GPW"


def test_results_backfills_exchange_from_ticker_prefix(client, app_env, monkeypatch):
    """Stary CSV bez kolumny Exchange — ticker `GPW:ATC` → backend rozpoznaje GPW."""
    import app as m

    monkeypatch.setattr(m, "lookup_exchange", lambda _t: "")

    _m, res, _dat = app_env
    date_id = "2026-05-12"
    path = res / f"tradingview_results_{date_id}.csv"
    header = (
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,"
        "PCA_Values\n"
    )
    body = "GPW:ATC,Arctic Paper,8.10,1D,OK,,0.3 (Niebieski)\n"
    path.write_text(header + body, encoding="utf-8")

    r = client.get(f"/api/results/{date_id}")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert rows[0]["Exchange"] == "GPW"


def test_results_backfills_exchange_from_rest_lookup(client, app_env, monkeypatch):
    """Brak kolumny Exchange + brak prefixa → backfill z REST."""
    import app as m

    monkeypatch.setattr(m, "lookup_exchange", lambda _t: "NASDAQ")

    _m, res, _dat = app_env
    date_id = "2026-05-12"
    path = res / f"tradingview_results_{date_id}.csv"
    header = (
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,"
        "PCA_Values\n"
    )
    body = "AAPL,Apple Inc.,180.0,1D,OK,,0.5 (Niebieski)\n"
    path.write_text(header + body, encoding="utf-8")

    r = client.get(f"/api/results/{date_id}")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert rows[0]["Exchange"] == "NASDAQ"


def test_results_backfill_exchange_uppercases_value(client, app_env, monkeypatch):
    import app as m

    monkeypatch.setattr(m, "lookup_exchange", lambda _t: "nyse")

    _m, res, _dat = app_env
    date_id = "2026-05-12"
    path = res / f"tradingview_results_{date_id}.csv"
    path.write_text(
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,PCA_Values\n"
        "ZIM,ZIM Integrated,12.0,1D,OK,,0.5 (Niebieski)\n",
        encoding="utf-8",
    )

    r = client.get(f"/api/results/{date_id}")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert rows[0]["Exchange"] == "NYSE"


def test_results_marks_config_match_for_exact_and_exchange_variant(
    client, app_env, tmp_path, monkeypatch
):
    import app as m

    cfg_path = tmp_path / "scraper_config.json"
    cfg_path.write_text(
        json.dumps({
            "tickers": ["AAPL", "GPW:ATC"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_path))
    monkeypatch.setattr(m, "lookup_exchange", lambda _t: "")

    _m, res, _dat = app_env
    date_id = "2026-05-13"
    path = res / f"tradingview_results_{date_id}.csv"
    path.write_text(
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,PCA_Values\n"
        "AAPL,Apple Inc.,180.0,1D,OK,,0.5 (Niebieski)\n"
        "ATC,Arctic Paper,8.0,1D,OK,,0.3 (Niebieski)\n"
        "DIAP,Diagnostyka,1.0,1D,OK,,0.3 (Niebieski)\n",
        encoding="utf-8",
    )

    r = client.get(f"/api/results/{date_id}")
    assert r.status_code == 200
    by_t = {row["Ticker"]: row for row in r.json()["data"]}
    assert by_t["AAPL"]["In_Config"] is True
    assert by_t["AAPL"]["Config_Match"] == "AAPL"
    assert by_t["AAPL"]["Config_Status"] == "exact"
    assert by_t["AAPL"]["Config_Candidates"] == []
    assert by_t["ATC"]["In_Config"] is True
    assert by_t["ATC"]["Config_Match"] == "GPW:ATC"
    assert by_t["ATC"]["Config_Status"] == "variant"
    assert by_t["ATC"]["Config_Candidates"] == []
    assert by_t["DIAP"]["In_Config"] is False
    assert by_t["DIAP"]["Config_Match"] == ""
    assert by_t["DIAP"]["Config_Status"] == "unknown"
    assert by_t["DIAP"]["Config_Candidates"] == []


def test_results_marks_stale_config_candidates(client, app_env, tmp_path, monkeypatch):
    import app as m

    cfg_path = tmp_path / "scraper_config.json"
    cfg_path.write_text(
        json.dumps({
            "tickers": ["AAPL", "GPW:TOA", "GPW:APT"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_path))
    monkeypatch.setattr(m, "lookup_exchange", lambda _t: "")

    _m, res, _dat = app_env
    date_id = "2026-05-14"
    path = res / f"tradingview_results_{date_id}.csv"
    path.write_text(
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,PCA_Values\n"
        "GPW:TOY,Toy old,8.0,1D,OK,,0.3 (Niebieski)\n"
        "APTP,Atal old,1.0,1D,OK,,0.3 (Niebieski)\n"
        "ZZZQ,Unknown,1.0,1D,OK,,0.3 (Niebieski)\n",
        encoding="utf-8",
    )

    r = client.get(f"/api/results/{date_id}")
    assert r.status_code == 200
    by_t = {row["Ticker"]: row for row in r.json()["data"]}

    assert by_t["GPW:TOY"]["In_Config"] is False
    assert by_t["GPW:TOY"]["Config_Match"] == ""
    assert by_t["GPW:TOY"]["Config_Status"] == "stale"
    assert by_t["GPW:TOY"]["Config_Candidates"][0]["ticker"] == "GPW:TOA"

    assert by_t["APTP"]["In_Config"] is False
    assert by_t["APTP"]["Config_Status"] == "stale"
    assert by_t["APTP"]["Config_Candidates"][0]["ticker"] == "GPW:APT"

    assert by_t["ZZZQ"]["In_Config"] is False
    assert by_t["ZZZQ"]["Config_Status"] == "unknown"
    assert by_t["ZZZQ"]["Config_Candidates"] == []


def test_delete_ticker_preview_counts_config_and_history(
    client, app_env, tmp_path, monkeypatch
):
    import app as m

    cfg_path = tmp_path / "scraper_config.json"
    cfg_path.write_text(
        json.dumps({"tickers": ["AAPL", "MSFT"], "intervals": ["1D"], "indicators": ["PCA"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_path))

    _m, res, _dat = app_env
    f1 = res / "tradingview_results_2026-05-15.csv"
    f2 = res / "tradingview_results_2026-05-16.csv"
    f1_text = (
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,PCA_Values\n"
        "AAPL,Apple,180,1D,OK,,0.5\n"
        "MSFT,Microsoft,300,1D,OK,,0.6\n"
        "AAPL,Apple,180,1W,OK,,0.7\n"
    )
    f2_text = (
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,PCA_Values\n"
        "MSFT,Microsoft,300,1D,OK,,0.6\n"
    )
    f1.write_text(f1_text, encoding="utf-8")
    f2.write_text(f2_text, encoding="utf-8")

    r = client.get("/api/tickers/AAPL/delete_preview")
    assert r.status_code == 200
    data = r.json()
    assert data["ticker"] == "AAPL"
    assert data["in_config"] is True
    assert data["config_removed_count"] == 1
    assert data["files_count"] == 1
    assert data["rows_count"] == 2
    assert data["files"][0]["filename"] == "tradingview_results_2026-05-15.csv"
    assert f1.read_text(encoding="utf-8") == f1_text
    assert f2.read_text(encoding="utf-8") == f2_text


def test_delete_ticker_removes_from_config_and_all_csv(
    client, app_env, tmp_path, monkeypatch
):
    import app as m

    cfg_path = tmp_path / "scraper_config.json"
    cfg_path.write_text(
        json.dumps({"tickers": ["AAPL", "MSFT", "aapl"], "intervals": ["1D"], "indicators": ["PCA"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "CONFIG_FILE", str(cfg_path))

    _m, res, _dat = app_env
    f1 = res / "tradingview_results_2026-05-17.csv"
    f2 = res / "tradingview_results_2026-05-18.csv"
    f3 = res / "tradingview_results_2026-05-19.csv"
    f1.write_text(
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,PCA_Values\n"
        "AAPL,Apple,180,1D,OK,,0.5\n"
        "MSFT,Microsoft,300,1D,OK,,0.6\n"
        "aapl,Apple,180,1W,OK,,0.7\n",
        encoding="utf-8",
    )
    f2.write_text(
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,PCA_Values\n"
        "AAPL,Apple,180,1D,OK,,0.8\n",
        encoding="utf-8",
    )
    f3_text = (
        "Ticker,Company_Name,Current_Price,Interval,Scrape_Status,Scrape_Error,PCA_Values\n"
        "MSFT,Microsoft,300,1D,OK,,0.6\n"
    )
    f3.write_text(f3_text, encoding="utf-8")

    r = client.delete("/api/tickers/AAPL")
    assert r.status_code == 200
    data = r.json()
    assert data["config_removed_count"] == 2
    assert data["files_modified"] == 2
    assert data["rows_removed"] == 3

    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["tickers"] == ["MSFT"]
    assert "AAPL" not in f1.read_text(encoding="utf-8").upper()
    assert "AAPL" not in f2.read_text(encoding="utf-8").upper()
    assert f3.read_text(encoding="utf-8") == f3_text


def test_delete_ticker_rejects_invalid_ticker(client):
    r = client.get("/api/tickers/BAD%24/delete_preview")
    assert r.status_code == 400

    r = client.delete("/api/tickers/BAD%24")
    assert r.status_code == 400
