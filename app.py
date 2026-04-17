import asyncio
import os
import glob
import csv
import re
import json
import logging
import signal
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

from results_store import parse_pca_number, row_has_indicator_data

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
    # Domyślnie nie uruchamiamy pełnego scrape'u przy starcie procesu —
    # jest to destrukcyjne dla trwającego dnia (wypiera wyniki ręcznych
    # „rescrape" pojedynczych tickerów) i zaskakuje przy każdym restarcie
    # uvicorn. Ustaw na true świadomie w panelu Konfiguracji.
    "run_on_startup": False,
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
SCRAPER_LOG_FILE = "scraper.log"
SCRAPER_LOG_MAX_BYTES = 2_000_000

RESULTS_DATE_ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(_\d{2}-\d{2}-\d{2})?$")
TICKER_PATTERN = re.compile(r"^[A-Z0-9._-]{1,20}$")
ALLOWED_HISTORY_INTERVALS = {"1D", "1W", "1M"}


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


class TickerRenameRequest(BaseModel):
    old: str = Field(..., min_length=1, max_length=20)
    new: str = Field(..., min_length=1, max_length=20)


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
        cmd = [sys.executable, "-u", "tv_scraper.py"]
        if tickers:
            cmd.extend(["--ticker", ",".join(tickers)])
        # Przytnij poprzedni log scrapera, jeśli urósł — chcemy zobaczyć
        # przebieg bieżącego runu bez mieszania z setkami starych linii.
        try:
            if (
                os.path.exists(SCRAPER_LOG_FILE)
                and os.path.getsize(SCRAPER_LOG_FILE) > SCRAPER_LOG_MAX_BYTES
            ):
                os.replace(SCRAPER_LOG_FILE, SCRAPER_LOG_FILE + ".1")
        except Exception:
            pass
        try:
            log_fh = open(SCRAPER_LOG_FILE, "ab", buffering=0)
            log_fh.write(
                (
                    f"\n===== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  "
                    f"start  tickers={tickers or 'ALL'}  =====\n"
                ).encode("utf-8")
            )
        except Exception:
            log_fh = subprocess.DEVNULL
        try:
            popen_kwargs = dict(
                cwd=ROOT_DIR,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
            )
            # Uruchamiamy scrapera w osobnej grupie procesów, aby Stop mógł
            # zabić cały poddrzew (scraper + spawnowane przez playwright
            # helpery). Na Windows zastępnikiem jest CREATE_NEW_PROCESS_GROUP.
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True
            else:
                popen_kwargs["creationflags"] = getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                )
            _scraper_process = subprocess.Popen(cmd, **popen_kwargs)
            # Subprocess dostał już swój deskryptor (dup), w parent zamykamy.
            if hasattr(log_fh, "close"):
                try:
                    log_fh.close()
                except Exception:
                    pass
            count = len(tickers) if tickers else 0
            # Zapisujemy PID w pliku statusu, aby ewentualne Stop po restarcie
            # serwera wciąż miało co killnąć (orphan scraper).
            try:
                pid = _scraper_process.pid
                data = {}
                if os.path.exists(STATUS_FILE):
                    try:
                        with open(STATUS_FILE, "r") as f:
                            data = json.load(f) or {}
                    except Exception:
                        data = {}
                data.update({
                    "status": data.get("status") or "running",
                    "pid": pid,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                with open(STATUS_FILE, "w") as f:
                    json.dump(data, f)
            except Exception:
                pass
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

            # Per-row diagnostyka brakujących wskaźników (niezależnie od Scrape_Status w CSV)
            try:
                ser = pd.Series(row)
                missing = [
                    ind for ind in indicators if not row_has_indicator_data(ser, ind)
                ]
            except Exception:
                missing = []
            row["Missing_Indicators"] = missing
            row["All_Indicators_Missing"] = bool(
                indicators and len(missing) == len(indicators)
            )
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


def _kill_pid_tree(pid: int, timeout_s: float = 2.0) -> bool:
    """Zabija proces (wraz z grupą) — SIGTERM z timeoutem, potem SIGKILL.

    Zwraca True gdy proces przestał istnieć.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != "posix":
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            return False
        return True

    try:
        pgid = os.getpgid(pid)
    except Exception:
        pgid = None

    def _signal(sig: int) -> None:
        try:
            if pgid is not None:
                os.killpg(pgid, sig)
            else:
                os.kill(pid, sig)
        except ProcessLookupError:
            return
        except Exception as exc:
            logger.warning("Nie udało się wysłać sygnału %s do PID %s: %s", sig, pid, exc)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except Exception:
        pass

    _signal(signal.SIGTERM)
    import time as _time
    deadline = _time.time() + max(0.2, float(timeout_s))
    while _time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except Exception:
            break
        _time.sleep(0.1)

    _signal(signal.SIGKILL)
    _time.sleep(0.3)
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except Exception:
        return True


def _find_scraper_pids_by_name() -> List[int]:
    """Ostatnia deska ratunku: znajdź procesy ``tv_scraper.py`` przez ``pgrep``.

    Działa gdy ``_scraper_process`` jest ``None`` (np. po restarcie uvicorna)
    a w pliku statusu brakuje PID (legacy format).
    """
    if os.name != "posix":
        return []
    try:
        result = subprocess.run(
            ["pgrep", "-f", "tv_scraper.py"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return []
    if result.returncode not in (0, 1):
        return []
    pids: List[int] = []
    my_pid = os.getpid()
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid == my_pid:
            continue
        pids.append(pid)
    return pids


@app.post("/api/scraper/stop")
async def stop_scraper():
    global _scraper_process

    proc = _scraper_process
    if proc is None or proc.poll() is not None:
        # Brak procesu w pamięci (np. restart uvicorna). Próbujemy
        # kolejno: PID z pliku statusu → skan pgrep po "tv_scraper.py".
        orphan_killed = False
        try:
            pids_to_kill: List[int] = []
            if os.path.exists(STATUS_FILE):
                try:
                    with open(STATUS_FILE, "r") as f:
                        cur = json.load(f) or {}
                except Exception:
                    cur = {}
                pid = cur.get("pid")
                if isinstance(pid, int) and pid > 0:
                    pids_to_kill.append(pid)
            else:
                cur = {}

            # Fallback: poszukaj po nazwie procesu
            for extra in _find_scraper_pids_by_name():
                if extra not in pids_to_kill:
                    pids_to_kill.append(extra)

            if pids_to_kill:
                def _kill_all():
                    killed_any = False
                    for p in pids_to_kill:
                        if _kill_pid_tree(p):
                            killed_any = True
                    return killed_any

                orphan_killed = await asyncio.to_thread(_kill_all)

            if (cur.get("status") or "").lower() == "running" or orphan_killed:
                _write_status("stopped")
        except Exception:
            logger.exception("Błąd porządkowania orphan scrapera")
        _scraper_process = None
        return {
            "status": "stopped" if orphan_killed else "not_running",
            "orphan_killed": orphan_killed,
            "pids_found": pids_to_kill if 'pids_to_kill' in locals() else [],
        }

    def _terminate_and_wait() -> None:
        """Spróbuj zatrzymać całą grupę procesów (scraper + playwright helpers).

        Playwright potrafi spawnować helpery, które nie reagują na SIGTERM
        wysłany tylko do scraper-python. Killujemy więc całą grupę, a jeśli
        nie zareaguje w 2s – SIGKILL.
        """
        pgid: Optional[int] = None
        if os.name == "posix":
            try:
                pgid = os.getpgid(proc.pid)
            except Exception:
                pgid = None

        def _signal(sig: int) -> None:
            try:
                if pgid is not None:
                    os.killpg(pgid, sig)
                else:
                    if sig == signal.SIGKILL:
                        proc.kill()
                    else:
                        proc.terminate()
            except ProcessLookupError:
                pass
            except Exception as exc:
                logger.warning("Błąd wysyłania sygnału %s do scrapera: %s", sig, exc)

        try:
            _signal(signal.SIGTERM)
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            logger.warning("Scraper nie zakończył się w 2s — wysyłam SIGKILL.")
            _signal(signal.SIGKILL)
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Błąd zatrzymywania procesu scrapera: %s", exc)

    await asyncio.to_thread(_terminate_and_wait)
    _write_status("stopped")
    _scraper_process = None
    return {"status": "stopped"}


@app.post("/api/tickers/rename")
def rename_ticker(body: TickerRenameRequest):
    """Zmienia nazwę tickera w konfiguracji (zachowuje pozycję na liście).

    Dopasowanie ``old`` do wpisu w configu odbywa się w trzech krokach:
      1) exact (po normalizacji do upper),
      2) bazowy symbol (prefiks przed pierwszą kropką, np. ``LULU.O`` ↔ ``LULU``),
      3) gdy w configu jest dokładnie jeden kandydat z tym samym basem — używamy go.

    Nie dotyka historycznych plików CSV — pozostają pod starą nazwą.
    """
    old = (body.old or "").strip().upper()
    new = (body.new or "").strip().upper()
    if not TICKER_PATTERN.match(old):
        raise HTTPException(status_code=400, detail="Invalid old ticker")
    if not TICKER_PATTERN.match(new):
        raise HTTPException(status_code=400, detail="Invalid new ticker")
    if old == new:
        raise HTTPException(status_code=400, detail="Old and new ticker are identical")

    config = load_config()
    tickers = list(config.get("tickers") or [])
    upper_list = [str(t).strip().upper() for t in tickers]

    def _base(sym: str) -> str:
        return (sym or "").split(".", 1)[0]

    if old in upper_list:
        idx = upper_list.index(old)
    else:
        old_base = _base(old)
        candidates = [
            i for i, t in enumerate(upper_list) if _base(t) == old_base and old_base
        ]
        if len(candidates) == 0:
            raise HTTPException(
                status_code=404, detail=f"Ticker {old} not found in config"
            )
        if len(candidates) > 1:
            matched = [tickers[i] for i in candidates]
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Niejednoznaczne dopasowanie dla {old}: {matched}. "
                    "Usuń duplikaty w konfiguracji."
                ),
            )
        idx = candidates[0]

    if new in upper_list and upper_list.index(new) != idx:
        raise HTTPException(
            status_code=409, detail=f"Ticker {new} already exists in config"
        )

    matched_old = tickers[idx]
    tickers[idx] = new
    config["tickers"] = tickers
    save_config(config)
    return {
        "status": "renamed",
        "old": matched_old,
        "requested_old": old,
        "new": new,
        "tickers": tickers,
    }


@app.get("/api/ticker/{ticker}/history")
def get_ticker_history(ticker: str, interval: str = "1D"):
    """Zwraca histori\u0119 PCA danego tickera dla wybranego interwa\u0142u (sortowan\u0105 rosn\u0105co po dacie)."""
    if not ticker or not TICKER_PATTERN.match(ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker")
    if interval not in ALLOWED_HISTORY_INTERVALS:
        raise HTTPException(status_code=400, detail="Invalid interval")

    files = get_csv_files()
    series: List[Dict[str, Any]] = []
    seen_dates: set = set()

    for f in sorted(files):
        base = os.path.basename(f)
        date_id = base.replace(CSV_PREFIX, "").replace(".csv", "")
        if not RESULTS_DATE_ID_PATTERN.match(date_id):
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    if (row.get("Ticker") or "").strip() != ticker:
                        continue
                    if (row.get("Interval") or "").strip() != interval:
                        continue
                    raw = row.get("PCA_Values") or row.get("PCA_Value") or ""
                    value, color = parse_pca_number(raw)
                    if value is None and not color:
                        continue
                    key = date_id[:10]
                    if key in seen_dates:
                        continue
                    seen_dates.add(key)
                    series.append({
                        "date": key,
                        "value": value,
                        "color": color,
                    })
                    break
        except Exception as e:
            logger.warning("Nie mo\u017cna odczyta\u0107 historii z %s: %s", f, e)
            continue

    series.sort(key=lambda r: r["date"])
    return {"ticker": ticker, "interval": interval, "history": series}


os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
