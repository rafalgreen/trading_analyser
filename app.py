import os
import glob
import csv
import re
import json
import subprocess
import sys
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

app = FastAPI(title="Trading Analyser API")

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
            return json.load(f)
    return {"tickers": [], "intervals": ["1D", "1W", "1M"], "indicators": ["PCA", "HTS Panel", "MacD"]}

def save_config(config: Dict[str, Any]):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

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
    
    save_config(config)
    return {"status": "saved", "config": config}

# ===================== SCRAPER API =====================

# Track the running scraper process
_scraper_process: Optional[subprocess.Popen] = None

@app.post("/api/scraper/run")
async def run_scraper(request: Request):
    """Start the scraper as a background subprocess."""
    global _scraper_process
    
    # Check if already running
    if _scraper_process is not None and _scraper_process.poll() is None:
        return {"status": "already_running", "message": "Scraper jest już uruchomiony."}
    
    body = await request.json()
    tickers = body.get("tickers", [])  # Empty = use config
    
    # Build command
    cmd = [sys.executable, "tv_scraper.py"]
    if tickers:
        cmd.extend(["--ticker", ",".join(tickers)])
    
    try:
        # Start scraper in background
        _scraper_process = subprocess.Popen(
            cmd,
            cwd=ROOT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        
        return {
            "status": "started",
            "pid": _scraper_process.pid,
            "tickers_count": len(tickers) if tickers else "all"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

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
