import os
import glob
import csv
import re
import json
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from collections import defaultdict

import pandas as pd

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from tv_scraper import row_has_indicator_data

DEFAULT_AUTO_SCHEDULE = {
    "enabled": False,
    "hour": 7,
    "minute": 30,
    "run_on_startup": True,
}
_scheduler = BackgroundScheduler()

# Opóźnienie przed startem scrapera po włączeniu uvicorn (sekundy)
STARTUP_SCRAPER_DELAY_SEC = 15


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    reschedule_auto_scraper()
    _scheduler.start()
    _schedule_startup_scrape()
    yield
    _scheduler.shutdown(wait=False)


app = FastAPI(title="Trading Analyser API", lifespan=_app_lifespan)

# Setup CORS (credentials + wildcard is invalid in browsers; API does not need cookies)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Root directory for scanning CSVs
ROOT_DIR = "."
RESULTS_DIR = "results"
DATA_DIR = "data"
CSV_PREFIX = "tradingview_results_"
CONFIG_FILE = "scraper_config.json"
STATUS_FILE = "scraper_status.json"

# Allowed CSV stem after prefix: YYYY-MM-DD or YYYY-MM-DD_HH-MM-SS
RESULTS_DATE_ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(_\d{2}-\d{2}-\d{2})?$")


def validate_results_date_id(date_id: str) -> None:
    if not date_id or not RESULTS_DATE_ID_PATTERN.match(date_id):
        raise HTTPException(status_code=400, detail="Invalid date id")

class HistoryResponse(BaseModel):
    dates: List[Dict[str, str]]

# --- Config Management ---
def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
    else:
        cfg = {
            "tickers": [],
            "intervals": ["1D", "1W", "1M"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
        }
    if "auto_schedule" not in cfg:
        cfg["auto_schedule"] = DEFAULT_AUTO_SCHEDULE.copy()
    else:
        for k, v in DEFAULT_AUTO_SCHEDULE.items():
            cfg["auto_schedule"].setdefault(k, v)
    return cfg


def save_config(config: Dict[str, Any]):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# --- Scraper subprocess (współdzielone: API + harmonogram) ---
_scraper_process: Optional[subprocess.Popen] = None


def start_scraper_subprocess(tickers: Optional[List[str]] = None) -> Dict[str, Any]:
    """Uruchamia `tv_scraper.py` w tle. Pusta lista tickerów = pełna lista z konfiguracji."""
    global _scraper_process
    if _scraper_process is not None and _scraper_process.poll() is None:
        return {"status": "already_running", "message": "Scraper jest już uruchomiony."}
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
        return {
            "status": "started",
            "pid": _scraper_process.pid,
            "tickers_count": len(tickers) if tickers else "all",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _scheduled_scraper_run():
    start_scraper_subprocess(None)


def _schedule_startup_scrape() -> None:
    """Po starcie serwera uruchamia pełny odczyt na bieżący dzień (jeśli włączone w konfiguracji)."""
    if os.environ.get("PYTEST_RUNNING"):
        return
    cfg = load_config()
    if not (cfg.get("auto_schedule") or {}).get("run_on_startup", True):
        return

    def _run():
        start_scraper_subprocess(None)

    t = threading.Timer(STARTUP_SCRAPER_DELAY_SEC, _run)
    t.daemon = True
    t.start()


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


# --- Watchlist Loader ---
def load_watchlist() -> Dict[str, Dict[str, str]]:
    """Load the most recent Portfel_Watchlist CSV and return a dict keyed by ticker Symbol."""
    pattern = os.path.join(DATA_DIR, "Portfel_Watchlist_*.csv")
    files = glob.glob(pattern)
    if not files:
        return {}
    # Use the most recent file
    files.sort(reverse=True)
    watchlist_file = files[0]
    
    lookup = {}
    try:
        # Use utf-8-sig to handle BOM character in CSV header
        with open(watchlist_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            headers = next(reader)
            
            # Build column index map — handle duplicate names by tracking occurrence count
            # "Daily" appears twice: first = signal rating, second = daily % change
            col_map = {}
            daily_count = 0
            for i, h in enumerate(headers):
                h_clean = h.strip()
                if h_clean == "Daily":
                    daily_count += 1
                    if daily_count == 1:
                        col_map["Daily_Signal"] = i  # First "Daily" = signal
                    else:
                        col_map["Daily_Chg"] = i      # Second "Daily" = daily % change
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
        print(f"[!] Error loading watchlist: {e}")
    return lookup

# Cache watchlist on startup
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
    # Dirty if it starts with the ticker followed by numbers/price
    if company_name.startswith(ticker):
        return True
    # Dirty if it contains price change patterns like "▼ −4.78%"
    if "▼" in company_name or "▲" in company_name or "%" in company_name:
        return True
    return False

def clean_company_name(ticker: str, raw_name: str, watchlist: Dict) -> str:
    """Try to get a clean company name from watchlist, fallback to cleaned raw name."""
    # Direct match in watchlist
    if ticker in watchlist:
        return watchlist[ticker].get("Name", raw_name)
    
    # Try matching without exchange suffix (e.g., "PLTR" matches "PLTR.O")
    for wl_symbol, wl_data in watchlist.items():
        base_symbol = wl_symbol.split(".")[0]
        if base_symbol == ticker:
            return wl_data.get("Name", raw_name)
    
    # If raw_name is dirty, try to extract something useful
    if is_dirty_company_name(ticker, raw_name):
        return ticker  # Fallback to just the ticker
    
    return raw_name


def row_allows_watchlist_signals(row: Dict[str, Any], indicators: List[str]) -> bool:
    """
    Te same kryteria co kompletny wiersz w tv_scraper: status OK oraz każdy wskaźnik
    z konfiguracji ma realne dane (nie wystarczy jeden HTS/MacD ze „śmieciowego” DOM).
    """
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
    """
    Sygnały D / W / M z eksportu watchlisty: osobno na interwał, tylko gdy wiersz 1D/1W/1M
    ma pełny zestaw wskaźników z konfiguracji (zgodnie z row_has_indicator_data).
    """
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
    files.sort(reverse=True) # Newest first
    return files

# ===================== API ROUTES =====================

@app.get("/api/history", response_model=HistoryResponse)
def get_history():
    files = get_csv_files()
    dates = []
    for f in files:
        base = os.path.basename(f)
        date_id = base.replace(CSV_PREFIX, "").replace(".csv", "")
        formatted_date = parse_date_from_filename(f)
        dates.append({
            "id": date_id,
            "label": formatted_date,
            "filename": base
        })
    return {"dates": dates}

@app.get("/api/results/{date_id}")
def get_results(date_id: str):
    validate_results_date_id(date_id)
    filename = f"{CSV_PREFIX}{date_id}.csv"
    filepath = os.path.normpath(os.path.join(RESULTS_DIR, filename))
    if os.path.commonpath([os.path.abspath(filepath), os.path.abspath(RESULTS_DIR)]) != os.path.abspath(RESULTS_DIR):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Data not found for this date")
    
    watchlist = get_watchlist()
    indicators = load_config().get("indicators") or ["PCA", "HTS Panel", "MacD"]

    results = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row.get("Ticker", "")
                raw_name = row.get("Company_Name", "")
                
                # Enrich with clean company name from watchlist
                row["Company_Name"] = clean_company_name(ticker, raw_name, watchlist)
                
                # Enrich with watchlist metadata if available
                wl_data = watchlist.get(ticker, {})
                if not wl_data:
                    # Try matching without exchange suffix
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    return {"data": results}

@app.get("/api/watchlist")
def get_watchlist_data():
    """Return the full watchlist data for frontend use."""
    watchlist = get_watchlist()
    return {"data": watchlist}

@app.get("/api/watchlist/reload")
def reload_watchlist():
    """Force reload the watchlist cache."""
    global _watchlist_cache
    _watchlist_cache = load_watchlist()
    return {"status": "reloaded", "count": len(_watchlist_cache)}

# ===================== CONFIG API =====================

@app.get("/api/config")
def get_config():
    """Return the current scraper config."""
    return load_config()

@app.put("/api/config")
async def update_config(request: Request):
    """Update the scraper config."""
    body = await request.json()
    config = load_config()
    
    if "tickers" in body:
        config["tickers"] = [t.strip() for t in body["tickers"] if t.strip()]
    if "intervals" in body:
        config["intervals"] = body["intervals"]
    if "indicators" in body:
        config["indicators"] = [i.strip() for i in body["indicators"] if i.strip()]
    if "auto_schedule" in body and isinstance(body["auto_schedule"], dict):
        a = body["auto_schedule"]
        config["auto_schedule"] = {
            "enabled": bool(a.get("enabled", False)),
            "hour": max(0, min(23, int(a.get("hour", 7)))),
            "minute": max(0, min(59, int(a.get("minute", 0)))),
            "run_on_startup": bool(a.get("run_on_startup", True)),
        }

    save_config(config)
    reschedule_auto_scraper()
    return {"status": "saved", "config": config}

# ===================== SCRAPER API =====================

@app.post("/api/scraper/run")
async def run_scraper(request: Request):
    """Start the scraper as a background subprocess."""
    body = await request.json()
    tickers = body.get("tickers", [])  # Empty = use config
    return start_scraper_subprocess(tickers if tickers else None)

@app.get("/api/scraper/status")
def get_scraper_status():
    """Return the current scraper status."""
    global _scraper_process
    
    # Check process state
    process_alive = _scraper_process is not None and _scraper_process.poll() is None
    
    # Read status file
    status_data = {"status": "idle", "progress": "", "current_ticker": "", "error": ""}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r') as f:
                status_data = json.load(f)
        except Exception:
            pass
    
    # If process died but status says running, mark as done or error
    if not process_alive and status_data.get("status") == "running":
        if _scraper_process is not None:
            rc = _scraper_process.returncode
            if rc == 0:
                status_data["status"] = "done"
            else:
                status_data["status"] = "error"
                status_data["error"] = f"Process exited with code {rc}"
    
    status_data["process_alive"] = process_alive
    return status_data

@app.post("/api/scraper/stop")
def stop_scraper():
    """Stop the running scraper."""
    global _scraper_process
    
    if _scraper_process is not None and _scraper_process.poll() is None:
        _scraper_process.terminate()
        _scraper_process.wait(timeout=5)
        return {"status": "stopped"}
    
    return {"status": "not_running"}

# Mount static files (this needs to be after API routes to avoid catching everything)
os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
