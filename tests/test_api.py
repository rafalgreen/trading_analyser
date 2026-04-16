import csv

from fastapi.testclient import TestClient


def test_history_empty(client: TestClient):
    r = client.get("/api/history")
    assert r.status_code == 200
    assert r.json() == {"dates": []}


def test_results_not_found(client: TestClient):
    r = client.get("/api/results/2026-01-01")
    assert r.status_code == 404


def test_results_invalid_date_id(client: TestClient):
    r = client.get("/api/results/not-a-valid-id")
    assert r.status_code == 400


def test_results_ok(app_env, client: TestClient):
    _m, res, _dat = app_env
    fp = res / "tradingview_results_2026-04-01.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Company_Name", "Interval", "PCA_Values"],
        )
        w.writeheader()
        w.writerow(
            {
                "Ticker": "AAA",
                "Company_Name": "TestCo",
                "Interval": "1D",
                "PCA_Values": "10 (Niebieski)",
            }
        )
    r = client.get("/api/results/2026-04-01")
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["Ticker"] == "AAA"


def test_config_get(client: TestClient):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "intervals" in body
    assert "indicators" in body
