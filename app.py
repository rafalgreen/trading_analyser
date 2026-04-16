import asyncio
import os
import glob
import csv
import re
import json
import logging
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from collections import defaultdict

import pandas as pd

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from results_store import row_has_indicator_data

logger = logging.getLogger("trading_analyser.app")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(
        getattr(logging, os.environ.get("TV_LOG_LEVEL", "INFO").upper(), logging.INFO)
    )
    logger.propagate = False

DEFAULT_AUTO_SCHEDULE = {
    "enabled": False,
    "hour": 7,
    "minute": 30,
    "run_on_startup": True,
}
_scheduler = BackgroundScheduler()

STARTUP_SCRAPER_DELAY_SEC = 15

_startup_scrape_scheduled = False
_scraper_lock = threading.Lock()


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    _reset_stale_status_file()
    reschedule_auto_scraper()
    _scheduler.start()
    _schedule_startup_scrape()
    yield
    _scheduler.shutdown(wait=False)


app = FastAPI(title="Trading Analyser API", lifespan=_app_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_DIR = "."
RESULTS_DIR = "results"
DATA_DIR = "data"
CSV_PREFIX = "tradingview_results_"
CONFIG_FILE = "scraper_config.json"
STATUS_FILE = "scraper_status.json"

RESULTS_DATE_ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(_\d{2}-\d{2}-\d{2})?$")


def validate_results_date_id(date_id: str) -> None:
    if not date_id or not RESULTS_DATE_ID_PATTERN.match(date_id):
        raise HTTPException(status_code=400, detail="Invalid date id")


class HistoryResponse(BaseModel):
    dates: List[Dict[str, str]]


class AutoScheduleModel(BaseModel):
    enabled: bool = False
    hour: int = Field(7, ge=0, le=23)
    minute: int = Field(30, ge=0, le=59)
    run_on_startup: bool = True


class ConfigUpdateRequest(BaseModel):
    tickers: Optional[List[str]] = None
    intervals: Optional[List[str]] = None
    indicators: Optional[List[str]] = None
    auto_schedule: Optional[AutoScheduleModel] = None


class ScraperRunRequest(BaseModel):
    tickers: List[str] = Field(default_factory=list)


def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
    else:
        cfg = {
            "tickers": [],
            "intervals": ["1D", "1W", "1M"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
        }
    sched = cfg.get("auto_schedule")
    if not isinstance(sched, dict):
        cfg["auto_schedule"] = DEFAULT_AUTO_SCHEDULE.copy()
    else:
        for k, v in DEFAULT_AUTO_SCHEDULE.items():
            cfg["auto_schedule"].setdefault(k, v)
    return cfg


def save_config(config: Dict[str, Any]):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


_scraper_process: Optional[subprocess.Popen] = None


def _reset_stale_status_file() -> None:
    """Po starcie serwera czyścimy plik statusu, jeśli wisi 'running' a procesu nie ma."""
    if not os.path.exists(STATUS_FILE):
        return
    try:
        with open(STATUS_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        return
    if (data or {}).get("status") == "running":
        data["status"] = "idle"
        data["progress"] = ""
        data["current_ticker"] = ""
        data["error"] = ""
        data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(STATUS_FILE, "w") as f:
                json.dump(data, f)
        except Exception:
            pass


def _write_status(status: str, **extra) -> None:
    data = {
        "status": status,
        "progress": "",
        "current_ticker": "",
        "error": "",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    data.update(extra)
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def start_scraper_subprocess(tickers: Optional[List[str]] = None) -> Dict[str, Any]:
    """Uruchamia `tv_scraper.py` w tle. Pusta lista tickerów = pełna lista z konfiguracji."""
    global _scraper_process
    with _scraper_lock:
        if _scraper_process is not None and _scraper_process.poll() is None:
            return {
                "status": "already_running",
                "message": "Scraper jest już uruchomiony.",
            }
        cmd = [sys.executable, "tv_scraper.py"]
        if tickers:
            cmd.extend(["--ticker", ",".join(tickers)])
        try:
            _scraper_process = subprocess.Popen(
                cmd,
                cwd=ROOT_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            count = len(tickers) if tickers else 0
            return {
                "status": "started",
                "pid": _scraper_process.pid,
                "count": count,
                "scope": "subset" if tickers else "all",
            }
        except Exception as e:
            logger.exception("Nie udało się uruchomić scrapera: %s", e)
            return {"status": "error", "message": str(e)}


def _scheduled_scraper_run():
    start_scraper_subprocess(None)


def _schedule_startup_scrape() -> None:
    """Po starcie serwera uruchamia pełny odczyt na bieżący dzień (jeśli włączone w konfiguracji)."""
    global _startup_scrape_scheduled
    if os.environ.get("PYTEST_RUNNING"):
        return
    if _startup_scrape_scheduled:
        return
    cfg = load_config()
    if not (cfg.get("auto_schedule") or {}).get("run_on_startup", True):
        return

    def _run():
        start_scraper_subprocess(None)

    t = threading.Timer(STARTUP_SCRAPER_DELAY_SEC, _run)
    t.daemon = True
    t.start()
    _startup_scrape_scheduled = True


def reschedule_auto_scraper() -> None:
    """Przeładuje harmonogram codziennego scrapera z pliku konfiguracji."""
    _scheduler.remove_all_jobs()
    cfg = load_config()
    sched = cfg.get("auto_schedule") or {}
    if not sched.get("enabled"):
        return
    h = max(0, min(23, int(sched.get("hour", 7))))
    m = max(0, min(59, int(sched.get("minute", 0))))
    _scheduler.add_job(
        _scheduled_scraper_run,
        CronTrigger(hour=h, minute=m),
        id="daily_tv_scraper",
        replace_existing=True,
    )


def load_watchlist() -> Dict[str, Dict[str, str]]:
    """Load the most recent Portfel_Watchlist CSV and return a dict keyed by ticker Symbol."""
    pattern = os.path.join(DATA_DIR, "Portfel_Watchlist_*.csv")
    files = glob.glob(pattern)
    if not files:
        return {}
    files.sort(reverse=True)
    watchlist_file = files[0]

    lookup: Dict[str, Dict[str, str]] = {}
    try:
        with open(watchlist_file, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)

            # "Daily" appears twice: first = signal rating, second = daily % change
            col_map: Dict[str, int] = {}
            daily_count = 0
            for i, h in enumerate(headers):
                h_clean = h.strip()
                if h_clean == "Daily":
                    daily_count += 1
                    if daily_count == 1:
                        col_map["Daily_Signal"] = i
                    else:
                        col_map["Daily_Chg"] = i
                elif h_clean not in col_map:
                    col_map[h_clean] = i

            def get_val(row, key):
                idx = col_map.get(key)
                if idx is not None and idx < len(row):
                    return row[idx].strip()
                return ""

            for row in reader:
                if len(row) < 3:
                    continue
                symbol = get_val(row, "Symbol")
                if not symbol:
                    continue
                lookup[symbol] = {
                    "Name": get_val(row, "Name"),
                    "Last": get_val(row, "Last"),
                    "Market_Cap": get_val(row, "Market Cap"),
                    "PE_Ratio": get_val(row, "P/E Ratio"),
                    "EPS": get_val(row, "EPS"),
                    "Beta": get_val(row, "Beta"),
                    "Revenue": get_val(row, "Revenue"),
                    "Daily_Signal": get_val(row, "Daily_Signal"),
                    "Weekly_Signal": get_val(row, "Weekly"),
                    "Monthly_Signal": get_val(row, "Monthly"),
                    "Chg_Pct": get_val(row, "Chg. %"),
                    "YTD": get_val(row, "YTD"),
                    "1Y": get_val(row, "1 Year"),
                }
    except Exception as e:
        logger.warning("Error loading watchlist: %s", e)
    return lookup


_watchlist_cache: Optional[Dict[str, Dict[str, str]]] = None


def get_watchlist() -> Dict[str, Dict[str, str]]:
    global _watchlist_cache
    if _watchlist_cache is None:
        _watchlist_cache = load_watchlist()
    return _watchlist_cache


def is_dirty_company_name(ticker: str, company_name: str) -> bool:
    """Check if company_name looks like it contains raw TradingView title garbage."""
    if not company_name or company_name == "Nieznana":
        return True
    if company_name.startswith(ticker):
        return True
    if "▼" in company_name or "▲" in company_name or "%" in company_name:
        return True
    return False


def clean_company_name(ticker: str, raw_name: str, watchlist: Dict) -> str:
    """Try to get a clean company name from watchlist, fallback to cleaned raw name."""
    if ticker in watchlist:
        return watchlist[ticker].get("Name", raw_name)

    for wl_symbol, wl_data in watchlist.items():
        base_symbol = wl_symbol.split(".")[0]
        if base_symbol == ticker:
            return wl_data.get("Name", raw_name)

    if is_dirty_company_name(ticker, raw_name):
        return ticker

    return raw_name


def row_allows_watchlist_signals(row: Dict[str, Any], indicators: List[str]) -> bool:
    """Kryteria kompletnego wiersza na potrzeby pokazywania sygnałów watchlisty."""
    if (row.get("Scrape_Status") or "").strip().upper() != "OK":
        return False
    if not indicators:
        return False
    ser = pd.Series(row)
    try:
        return all(row_has_indicator_data(ser, ind) for ind in indicators)
    except Exception:
        return False


def wl_signal_visibility_for_ticker(
    rows: List[Dict[str, Any]],
    indicators: Optional[List[str]] = None,
) -> Dict[str, bool]:
    """Sygnały D / W / M z eksportu watchlisty: osobno na interwał."""
    inds = indicators if indicators is not None else ["PCA", "HTS Panel", "MacD"]
    if not rows:
        return {"daily": False, "weekly": False, "monthly": False}

    def iv(r: Dict[str, Any]) -> str:
        return (r.get("Interval") or "").strip().upper()

    def allow_for(interval: str) -> bool:
        return any(
            row_allows_watchlist_signals(r, inds) and iv(r) == interval for r in rows
        )

    return {
        "daily": allow_for("1D"),
        "weekly": allow_for("1W"),
        "monthly": allow_for("1M"),
    }


def parse_date_from_filename(filename: str) -> str:
    base = os.path.basename(filename)
    date_str = base.replace(CSV_PREFIX, "").replace(".csv", "")
    for fmt in ("%Y-%m-%d_%H-%M-%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if fmt == "%Y-%m-%d":
                return dt.strftime("%Y-%m-%d")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return date_str


def get_csv_files():
    pattern = os.path.join(RESULTS_DIR, f"{CSV_PREFIX}*.csv")
    files = glob.glob(pattern)
    files.sort(reverse=True)
    return files


@app.get("/api/history", response_model=HistoryResponse)
def get_history():
    files = get_csv_files()
    dates = []
    for f in files:
        base = os.path.basename(f)
        date_id = base.replace(CSV_PREFIX, "").replace(".csv", "")
        formatted_date = parse_date_from_filename(f)
        dates.append({"id": date_id, "label": formatted_date, "filename": base})
    return {"dates": dates}


@app.get("/api/results/{date_id}")
def get_results(date_id: str):
    validate_results_date_id(date_id)
    filename = f"{CSV_PREFIX}{date_id}.csv"
    filepath = os.path.normpath(os.path.join(RESULTS_DIR, filename))
    if os.path.commonpath(
        [os.path.abspath(filepath), os.path.abspath(RESULTS_DIR)]
    ) != os.path.abspath(RESULTS_DIR):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Data not found for this date")

    watchlist = get_watchlist()
    indicators = load_config().get("indicators") or ["PCA", "HTS Panel", "MacD"]

    results = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row.get("Ticker", "")
                raw_name = row.get("Company_Name", "")

                row["Company_Name"] = clean_company_name(ticker, raw_name, watchlist)

                wl_data = watchlist.get(ticker, {})
                if not wl_data:
                    for wl_sym, wl_d in watchlist.items():
                        if wl_sym.split(".")[0] == ticker:
                            wl_data = wl_d
                            break

                if wl_data:
                    row["WL_Market_Cap"] = wl_data.get("Market_Cap", "")
                    row["WL_PE_Ratio"] = wl_data.get("PE_Ratio", "")
                    row["WL_Daily_Signal"] = wl_data.get("Daily_Signal", "")
                    row["WL_Weekly_Signal"] = wl_data.get("Weekly_Signal", "")
                    row["WL_Monthly_Signal"] = wl_data.get("Monthly_Signal", "")
                    row["WL_Chg_Pct"] = wl_data.get("Chg_Pct", "")
                    row["WL_YTD"] = wl_data.get("YTD", "")
                    row["WL_1Y"] = wl_data.get("1Y", "")

                results.append(row)

        by_ticker: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in results:
            by_ticker[row.get("Ticker", "") or ""].append(row)
        wl_vis = {
            t: wl_signal_visibility_for_ticker(rs, indicators)
            for t, rs in by_ticker.items()
        }
        for row in results:
            t = row.get("Ticker", "") or ""
            vis = wl_vis.get(t) or {
                "daily": False,
                "weekly": False,
                "monthly": False,
            }
            if not vis["daily"]:
                row["WL_Daily_Signal"] = ""
            if not vis["weekly"]:
                row["WL_Weekly_Signal"] = ""
            if not vis["monthly"]:
                row["WL_Monthly_Signal"] = ""
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Nie udało się wczytać wyników %s: %s", date_id, e)
        raise HTTPException(
            status_code=500, detail="Nie udało się wczytać danych dla tej daty."
        )

    return {"data": results}


@app.get("/api/watchlist")
def get_watchlist_data():
    watchlist = get_watchlist()
    return {"data": watchlist}


@app.get("/api/watchlist/reload")
def reload_watchlist():
    global _watchlist_cache
    _watchlist_cache = load_watchlist()
    return {"status": "reloaded", "count": len(_watchlist_cache)}


@app.get("/api/config")
def get_config():
    return load_config()


@app.put("/api/config")
def update_config(body: ConfigUpdateRequest):
    config = load_config()

    if body.tickers is not None:
        config["tickers"] = [t.strip() for t in body.tickers if t and t.strip()]
    if body.intervals is not None:
        config["intervals"] = [i for i in body.intervals if isinstance(i, str)]
    if body.indicators is not None:
        config["indicators"] = [
            i.strip() for i in body.indicators if i and i.strip()
        ]
    if body.auto_schedule is not None:
        config["auto_schedule"] = body.auto_schedule.model_dump()

    save_config(config)
    reschedule_auto_scraper()
    return {"status": "saved", "config": config}


@app.post("/api/scraper/run")
def run_scraper(body: ScraperRunRequest):
    tickers = [t.strip() for t in body.tickers if t and t.strip()]
    return start_scraper_subprocess(tickers if tickers else None)


@app.get("/api/scraper/status")
def get_scraper_status():
    global _scraper_process

    process_alive = _scraper_process is not None and _scraper_process.poll() is None

    status_data: Dict[str, Any] = {
        "status": "idle",
        "progress": "",
        "current_ticker": "",
        "error": "",
    }
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f:
                status_data = json.load(f)
        except Exception:
            pass

    if not process_alive and status_data.get("status") == "running":
        if _scraper_process is not None:
            rc = _scraper_process.returncode
            if rc == 0:
                status_data["status"] = "done"
            else:
                status_data["status"] = "error"
                status_data["error"] = f"Process exited with code {rc}"
        else:
            status_data["status"] = "idle"

    status_data["process_alive"] = process_alive
    return status_data


@app.post("/api/scraper/stop")
async def stop_scraper():
    global _scraper_process

    proc = _scraper_process
    if proc is None or proc.poll() is not None:
        return {"status": "not_running"}

    def _terminate_and_wait() -> None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Scraper nie zakończył się w 5s — wysyłam kill.")
            proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Błąd zatrzymywania procesu scrapera: %s", exc)

    await asyncio.to_thread(_terminate_and_wait)
    _write_status("stopped")
    return {"status": "stopped"}


os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
