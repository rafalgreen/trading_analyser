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
import time
import platform
import urllib.request
import urllib.error
from contextlib import asynccontextmanager
from datetime import datetime
from difflib import SequenceMatcher
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

import pandas as pd

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from results_store import (
    FUNDAMENTALS_COLUMNS,
    count_ticker_rows_in_csv,
    fundamentals_csv_path,
    get_fundamentals_for_ticker,
    load_fundamentals_dataframe,
    load_results_dataframe,
    parse_pca_number,
    remove_ticker_rows_from_csv,
    rename_fundamentals_ticker,
    rename_ticker_rows_in_csv,
    row_has_indicator_data,
    save_fundamentals_row,
    config_tickers_with_no_data,
    build_indicator_errors,
)
from fundamentals import (
    FUND_KEYS,
    FUND_SECTOR_KEYS,
    check_yfinance_available,
    fetch_fundamentals,
    fundamentals_fetch_attempted,
    fundamentals_row_has_values,
    is_crypto,
    configure_yfinance_logging,
    compute_sector_median_pe,
    load_all_fundamentals_rows,
    enrich_fundamentals_with_sector_pe,
)
from signal_strategies import (
    STRATEGIES as SIGNAL_STRATEGIES,
    STRATEGY_LABELS as SIGNAL_STRATEGY_LABELS,
    compute_signals as compute_row_signals,
)
from composite_score import compute_composite_verdict
from company_names import (
    fetch_symbol_matches,
    lookup_company_name,
    lookup_exchange,
    lookup_symbol_match,
)
from tv_scraper import cdp_find_tradingview_chart_url

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
STARTUP_FUNDAMENTALS_DELAY_SEC = 8
DASHBOARD_FUND_SYNC_MAX = 30

_startup_scrape_scheduled = False
_startup_fundamentals_scheduled = False
_fundamentals_refresh_lock = threading.Lock()
_scraper_lock = threading.Lock()


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    configure_yfinance_logging()
    _reset_stale_status_file()
    reschedule_auto_scraper()
    _scheduler.start()
    _schedule_startup_scrape()
    _schedule_startup_fundamentals_refresh()
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
STATE_FILE = "scraper_state.json"
SCRAPER_LOG_FILE = "scraper.log"
SCRAPER_LOG_MAX_BYTES = 2_000_000

RESULTS_DATE_ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(_\d{2}-\d{2}-\d{2})?$")
TICKER_PATTERN = re.compile(r"^[A-Z0-9._:\-]{1,24}$")
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
    indicators: Optional[List[str]] = None
    no_data_only: bool = False
    date_id: Optional[str] = None
    fresh: bool = False


class TickerRenameRequest(BaseModel):
    old: str = Field(..., min_length=1, max_length=24)
    new: str = Field(..., min_length=1, max_length=24)


class RepairRenamePair(BaseModel):
    old: str = Field(..., min_length=1, max_length=24)
    new: str = Field(..., min_length=1, max_length=24)


class RepairNoDataRequest(BaseModel):
    renames: List[RepairRenamePair] = Field(default_factory=list, max_length=200)
    rerun: bool = False
    date_id: Optional[str] = None


class FundamentalsRefreshRequest(BaseModel):
    tickers: List[str] = Field(default_factory=list)
    all: bool = False


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
    # CDP (remote debug) — domyślnie 9222 jak w README.
    cfg.setdefault("cdp_port", 9222)
    # Automatyczne uruchomienie przeglądarki z CDP (lokalny macOS).
    cfg.setdefault("auto_start_cdp_browser", True)
    cfg.setdefault("cdp_browser_preference", "brave")  # brave|chrome
    # Czy używać Twojego istniejącego, zalogowanego profilu Brave/Chrome
    # (zamiast czystego dedykowanego). Domyślnie tak — wtedy TV jest już
    # zalogowany. Aplikacja w razie potrzeby uprzejmie zamknie Brave/Chrome
    # przed startem CDP (sesja kart i tak zostanie odtworzona).
    cfg.setdefault("cdp_use_system_profile", True)
    cfg.setdefault("cdp_auto_quit_browser", True)
    # Niezawodny override: jawna ścieżka --user-data-dir (np. własny profil).
    cfg.setdefault("cdp_user_data_dir", "")
    # Sub-profil w katalogu user-data-dir (Brave/Chrome: "Default", "Profile 1"…).
    cfg.setdefault("cdp_profile_directory", "Default")
    # URL otwierany przy auto-starcie przeglądarki (np. konkretny wykres TV).
    cfg.setdefault("cdp_startup_url", "https://www.tradingview.com/chart/")
    # Lista prefixów giełd używana przy "Napraw symbole": gdy ticker jest no-data,
    # próbujemy znaleźć go na jednej z tych giełd przez TV symbol-search REST.
    if not isinstance(cfg.get("exchange_prefixes"), list):
        cfg["exchange_prefixes"] = ["GPW"]
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


def _cdp_is_listening(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=0.8
        ) as r:
            return 200 <= int(getattr(r, "status", 200)) < 300
    except Exception:
        return False


def _running_cdp_user_data_dir(port: int) -> Optional[str]:
    """Wyciąga ``--user-data-dir`` z aktualnie działającego procesu CDP.

    macOS-owy ``pgrep -a`` nie zwraca command-line — używamy ``pgrep -f`` po
    PID i ``ps -p <pid> -ww -o args=`` po pełnym argv.
    """
    try:
        pgrep = subprocess.run(
            ["pgrep", "-f", f"remote-debugging-port={port}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return None
    pids = [p.strip() for p in (pgrep.stdout or "").splitlines() if p.strip()]
    for pid in pids:
        try:
            ps = subprocess.run(
                ["ps", "-p", pid, "-ww", "-o", "args="],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception:
            continue
        argline = (ps.stdout or "").strip()
        if not argline:
            continue
        if "MacOS/Brave Browser" not in argline and "MacOS/Google Chrome" not in argline:
            continue
        m = re.search(r"--user-data-dir=([^\s\"']+)", argline)
        if m:
            return os.path.abspath(os.path.expanduser(m.group(1)))
    return None


CDP_USER_DATA_DIR = os.path.join(DATA_DIR, ".cdp-profile")

_BROWSER_BINARIES_MAC = {
    "brave": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
}

_BROWSER_APP_NAMES_MAC = {
    "brave": "Brave Browser",
    "chrome": "Google Chrome",
}

_BROWSER_DEFAULT_PROFILES_MAC = {
    "brave": "~/Library/Application Support/BraveSoftware/Brave-Browser",
    "chrome": "~/Library/Application Support/Google/Chrome",
}


def _system_profile_dir(preference: str) -> str:
    """Zwraca ścieżkę do istniejącego profilu Brave/Chrome lub ``""``."""
    pref = (preference or "brave").strip().lower()
    order = ["brave", "chrome"] if pref != "chrome" else ["chrome", "brave"]
    for key in order:
        raw = _BROWSER_DEFAULT_PROFILES_MAC.get(key, "")
        if not raw:
            continue
        path = os.path.expanduser(raw)
        if os.path.isdir(path):
            return path
    return ""


def _is_mac_app_running(app_name: str) -> bool:
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                f'tell application "System Events" to (name of processes) contains "{app_name}"',
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.stdout.strip().lower() == "true"
    except Exception:
        return False


def _quit_mac_app(app_name: str, timeout_s: float = 8.0) -> bool:
    """Politely quit a macOS app, force-kill remaining helpers if needed.

    Sam ``osascript quit`` często zostawia procesy pomocnicze (Brave Helper,
    Helper (Renderer), Helper (GPU)) — one trzymają singleton lock w katalogu
    user-data-dir, przez co kolejna instancja startuje w trybie awaryjnym
    bez dostępu do cookies / sesji.
    """
    if not _is_mac_app_running(app_name):
        _force_kill_app_helpers(app_name)
        return True

    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to quit'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        pass

    deadline = time.time() + max(0.5, float(timeout_s))
    while time.time() < deadline:
        if not _is_mac_app_running(app_name):
            break
        time.sleep(0.25)

    # Niezależnie od osascript — wyczyść osierocone procesy pomocnicze i wszelkie
    # pozostałe instancje binarki.
    _force_kill_app_helpers(app_name)

    deadline = time.time() + 4.0
    while time.time() < deadline:
        if not _is_mac_app_running(app_name) and not _has_app_processes(app_name):
            return True
        time.sleep(0.25)
    return not _is_mac_app_running(app_name) and not _has_app_processes(app_name)


def _has_app_processes(app_name: str) -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"/{app_name}.app/Contents/MacOS/"],
            capture_output=True,
            timeout=3,
        )
        return result.returncode == 0
    except Exception:
        return False


def _force_kill_app_helpers(app_name: str) -> None:
    """Twardo (-9) zabija pozostałe procesy Brave/Chrome (main + helpery)."""
    try:
        subprocess.run(
            ["pkill", "-9", "-f", f"/{app_name}.app/Contents/MacOS/"],
            capture_output=True,
            timeout=4,
        )
    except Exception:
        pass


def _purge_chromium_singletons(profile_dir: str) -> None:
    """Usuwa singleton lock-files chromium, blokujące poprawny start."""
    if not profile_dir or not os.path.isdir(profile_dir):
        return
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        path = os.path.join(profile_dir, name)
        try:
            if os.path.lexists(path):
                os.remove(path)
        except Exception as e:
            logger.debug("Nie udało się usunąć %s: %s", path, e)


def _resolve_cdp_browser_mac(
    preference: str,
    explicit_user_data_dir: str,
    use_system_profile: bool,
) -> Dict[str, str]:
    """Decyduje, którą binarkę uruchomić i jaki profil użyć.

    Zwraca słownik z polami ``key`` (brave/chrome), ``binary``, ``app_name``,
    ``profile_dir``, ``profile_kind`` (system/dedicated/custom).
    """
    pref = (preference or "brave").strip().lower()
    order = ["brave", "chrome"] if pref != "chrome" else ["chrome", "brave"]

    explicit = (explicit_user_data_dir or "").strip()
    explicit_path = (
        os.path.abspath(os.path.expanduser(explicit)) if explicit else ""
    )

    for key in order:
        binary = _BROWSER_BINARIES_MAC.get(key, "")
        if not binary or not os.path.exists(binary):
            continue
        if explicit_path:
            return {
                "key": key,
                "binary": binary,
                "app_name": _BROWSER_APP_NAMES_MAC.get(key, ""),
                "profile_dir": explicit_path,
                "profile_kind": "custom",
            }
        if use_system_profile:
            sys_path = os.path.expanduser(
                _BROWSER_DEFAULT_PROFILES_MAC.get(key, "")
            )
            if sys_path and os.path.isdir(sys_path):
                return {
                    "key": key,
                    "binary": binary,
                    "app_name": _BROWSER_APP_NAMES_MAC.get(key, ""),
                    "profile_dir": sys_path,
                    "profile_kind": "system",
                }
        return {
            "key": key,
            "binary": binary,
            "app_name": _BROWSER_APP_NAMES_MAC.get(key, ""),
            "profile_dir": os.path.abspath(CDP_USER_DATA_DIR),
            "profile_kind": "dedicated",
        }
    raise RuntimeError(
        "Nie znaleziono Brave ani Chrome w /Applications. "
        "Zainstaluj jedno z nich albo uruchom ręcznie z --remote-debugging-port."
    )


def _start_cdp_browser_mac(
    port: int,
    preference: str = "brave",
    user_data_dir: str = "",
    startup_url: str = "https://www.tradingview.com/chart/",
    use_system_profile: bool = True,
    auto_quit_running: bool = True,
    profile_directory: str = "Default",
) -> Dict[str, str]:
    """Startuje Brave/Chrome z CDP na macOS, używając wybranego profilu.

    Zwraca info o wybranej przeglądarce/profilu (do logów / odpowiedzi API).
    """
    info = _resolve_cdp_browser_mac(preference, user_data_dir, use_system_profile)

    profile_dir = info["profile_dir"]
    if info["profile_kind"] == "dedicated":
        try:
            os.makedirs(profile_dir, exist_ok=True)
        except Exception:
            pass

    if auto_quit_running and info["app_name"]:
        running = _is_mac_app_running(info["app_name"]) or _has_app_processes(
            info["app_name"]
        )
        if running:
            logger.info(
                "Zamykam %s, aby uruchomić ją ponownie z CDP (profil=%s, dir=%s)…",
                info["app_name"],
                info["profile_kind"],
                profile_directory,
            )
            if not _quit_mac_app(info["app_name"], timeout_s=8.0):
                raise RuntimeError(
                    f"Nie udało się zamknąć {info['app_name']}. Zamknij ręcznie i spróbuj ponownie."
                )

    # Po killu pozostają singleton-locki — bez ich usunięcia Brave startuje w trybie
    # odciętym od cookies/sesji (efekt: TV pokazuje reklamy "Join for free").
    _purge_chromium_singletons(profile_dir)

    args = [
        info["binary"],
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        f"--profile-directory={profile_directory or 'Default'}",
        "--no-first-run",
        "--no-default-browser-check",
        "--restore-last-session=false",
    ]
    if startup_url:
        args.append(startup_url)

    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    info["profile_directory"] = profile_directory or "Default"
    return info


def ensure_cdp_ready() -> None:
    """Przygotowuje sesję CDP: reuse istniejącej Brave/Chrome albo uruchomienie nowej (macOS).

    Gdy port CDP już nasłuchuje, nie zamykamy przeglądarki — scraper podłączy się
    do otwartej karty TradingView (jeśli jest) albo otworzy wykres w tej samej sesji.
    """
    cfg = load_config()
    try:
        port = int(cfg.get("cdp_port", 9222))
    except Exception:
        port = 9222

    if _cdp_is_listening(port):
        try:
            tv_url = cdp_find_tradingview_chart_url(port)
        except Exception as exc:
            logger.debug("Nie udało się odczytać listy kart CDP: %s", exc)
            tv_url = None
        if tv_url:
            logger.info(
                "Istniejąca sesja CDP — używam karty TradingView: %s", tv_url
            )
        else:
            logger.info(
                "Istniejąca sesja CDP na porcie %s — brak karty TradingView; "
                "scraper otworzy wykres w tej przeglądarce.",
                port,
            )
        return

    auto = cfg.get("auto_start_cdp_browser", True)
    env_override = (os.environ.get("TV_AUTO_START_CDP") or "").strip().lower()
    if env_override in {"0", "false", "no", "off"}:
        auto = False
    if env_override in {"1", "true", "yes", "on"}:
        auto = True

    if not auto:
        raise RuntimeError(
            f"Brak nasłuchu CDP pod http://127.0.0.1:{port}. "
            f"Uruchom Brave/Chrome z --remote-debugging-port={port} i otwórz wykres TradingView."
        )

    if platform.system().lower() != "darwin":
        raise RuntimeError(
            f"Brak nasłuchu CDP pod http://127.0.0.1:{port}. "
            "Auto-start CDP jest obsługiwany tylko na macOS (darwin)."
        )

    pref = cfg.get("cdp_browser_preference", "brave")
    user_data_dir = cfg.get("cdp_user_data_dir", "") or ""
    use_system_profile = bool(cfg.get("cdp_use_system_profile", True))
    auto_quit_running = bool(cfg.get("cdp_auto_quit_browser", True))
    profile_directory = str(cfg.get("cdp_profile_directory") or "Default")
    startup_url = cfg.get("cdp_startup_url", "https://www.tradingview.com/chart/")
    logger.info(
        "Brak nasłuchu CDP — uruchamiam nową instancję Brave/Chrome (%s) na porcie %s…",
        pref,
        port,
    )
    try:
        info = _start_cdp_browser_mac(
            port,
            str(pref),
            str(user_data_dir),
            str(startup_url),
            use_system_profile=use_system_profile,
            auto_quit_running=auto_quit_running,
            profile_directory=profile_directory,
        )
        logger.info(
            "Uruchamiam %s, profil=%s (%s, dir=%s)",
            info.get("app_name") or info.get("binary"),
            info.get("profile_kind"),
            info.get("profile_dir"),
            info.get("profile_directory"),
        )
    except Exception as e:
        raise RuntimeError(
            f"Nie udało się uruchomić przeglądarki: {e}. "
            f"Uruchom ręcznie ./scripts/start_browser_debug.sh"
        )

    for _ in range(120):
        if _cdp_is_listening(port):
            logger.info("CDP działa na http://127.0.0.1:%s", port)
            return
        time.sleep(0.25)

    raise RuntimeError(
        f"Nie udało się uruchomić CDP na http://127.0.0.1:{port}. "
        f"Sprawdź ręcznie: curl -sS http://127.0.0.1:{port}/json/version | head -c 200"
    )


def _normalize_scraper_indicators(
    indicators: Optional[List[str]],
) -> Optional[List[str]]:
    """Waliduje i normalizuje listę wskaźników względem konfiguracji."""
    if indicators is None:
        return None
    cfg_inds = [str(i).strip() for i in load_config().get("indicators") or [] if str(i).strip()]
    if not cfg_inds:
        cfg_inds = ["PCA", "HTS Panel", "MacD"]
    requested = [str(i).strip() for i in indicators if i and str(i).strip()]
    if not requested:
        raise HTTPException(
            status_code=422,
            detail="Lista indicators nie może być pusta.",
        )
    cfg_set = {i.casefold(): i for i in cfg_inds}
    out: List[str] = []
    unknown: List[str] = []
    for ind in requested:
        canon = cfg_set.get(ind.casefold())
        if canon:
            if canon not in out:
                out.append(canon)
        else:
            unknown.append(ind)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Nieznane wskaźniki: {', '.join(unknown)}. "
                f"Dozwolone: {', '.join(cfg_inds)}."
            ),
        )
    return out


def start_scraper_subprocess(
    tickers: Optional[List[str]] = None,
    indicators: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Uruchamia `tv_scraper.py` w tle. Pusta lista tickerów = pełna lista z konfiguracji."""
    global _scraper_process
    with _scraper_lock:
        if _scraper_process is not None and _scraper_process.poll() is None:
            return {
                "status": "already_running",
                "message": "Scraper jest już uruchomiony.",
            }
        try:
            ensure_cdp_ready()
        except Exception as e:
            _write_status("error", error=str(e))
            return {"status": "error", "message": str(e)}
        cmd = [sys.executable, "-u", "tv_scraper.py"]
        if tickers:
            cmd.extend(["--ticker", ",".join(tickers)])
        if indicators:
            cmd.extend(["--indicators", ",".join(indicators)])
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
                    f"start  tickers={tickers or 'ALL'}  "
                    f"indicators={indicators or 'ALL'}  =====\n"
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
            scope = "all"
            if tickers and indicators:
                scope = "subset_indicators"
            elif tickers:
                scope = "subset"
            elif indicators:
                scope = "indicators_only"
            return {
                "status": "started",
                "pid": _scraper_process.pid,
                "count": count,
                "scope": scope,
                "indicators": indicators or [],
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
    cu = company_name.strip()
    tu = (ticker or "").strip()
    if not tu:
        return True
    if cu.upper() == tu.upper():
        return True
    # Ticker + sama cena/liczba (np. ``MSFT 123``) — nie mylić z ``MSFT Corp``.
    if cu.upper().startswith(tu.upper()) and len(cu) > len(tu):
        suffix = cu[len(tu) :].lstrip()
        if suffix and re.match(r"^[\d\s\.,+%▼▲N/A]+$", suffix, re.IGNORECASE):
            return True
    if "▼" in company_name or "▲" in company_name or "%" in company_name:
        return True
    return False


def clean_company_name(ticker: str, raw_name: str, watchlist: Dict) -> str:
    """Watchlista (niepusta kolumna Name) ma pierwszeństwo; potem oczyszczanie surowej nazwy z CSV."""
    t = (ticker or "").strip()
    raw = raw_name or ""

    def _watchlist_display_name(sym: str) -> str:
        if not sym or not watchlist:
            return ""
        row = watchlist.get(sym)
        if row:
            n = (row.get("Name") or "").strip()
            if n:
                return n
        su = sym.upper()
        for wl_sym, wl_data in watchlist.items():
            ws = str(wl_sym).strip()
            if ws.upper() == su:
                n = (wl_data.get("Name") or "").strip()
                if n:
                    return n
            base = ws.split(".")[0].upper()
            if base == su or base == sym.split(".")[0].strip().upper():
                n = (wl_data.get("Name") or "").strip()
                if n:
                    return n
        return ""

    wl = _watchlist_display_name(t)
    if wl:
        return wl

    if is_dirty_company_name(t, raw):
        return t or ""

    return raw.strip() if raw.strip() else (t or "")


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


def _fundamentals_csv_path() -> str:
    """Ścieżka do CSV z fundamentami (zależna od aktualnego RESULTS_DIR)."""
    return fundamentals_csv_path(RESULTS_DIR)


def _fundamentals_cache_path() -> str:
    """Ścieżka do JSON cache fundamentów (zależna od DATA_DIR)."""
    return os.path.join(DATA_DIR, ".fundamentals_cache.json")


def _fundamentals_config() -> Dict[str, Any]:
    cfg = load_config()
    fund = cfg.get("fundamentals")
    if not isinstance(fund, dict):
        fund = {"enabled": True, "cache_ttl_hours": 24, "tv_fallback": True}
    return fund


def _scalarize_fund_value(value: Any) -> Any:
    """JSON-friendly serializacja wartości fundamentów (None pozostaje None)."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    if s == "" or s.upper() in ("N/A", "NAN", "NONE"):
        return None
    try:
        return float(s)
    except ValueError:
        return s


def _fundamentals_row_has_values(data: Optional[Dict[str, Any]]) -> bool:
    """Czy wiersz fundamentów ma choć jedną sensowną wartość wskaźnika."""
    return fundamentals_row_has_values(data)


def _fundamentals_from_cache(ticker: str) -> Optional[Dict[str, Any]]:
    """Odczyt pojedynczego tickera z JSON cache (gdy CSV jeszcze pusty)."""
    cache_path = _fundamentals_cache_path()
    if not cache_path or not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(cache, dict):
        return None
    entry = cache.get(str(ticker or "").strip().upper())
    if not isinstance(entry, dict):
        return None
    out: Dict[str, Any] = {"Ticker": str(ticker).strip()}
    for k in FUND_KEYS:
        out[k] = _scalarize_fund_value(entry.get(k))
    src = entry.get("Fund_Source")
    out["Fund_Source"] = (
        str(src).strip() if src is not None and str(src).strip() else "none"
    )
    upd = entry.get("Fund_Updated_At")
    out["Fund_Updated_At"] = (
        str(upd).strip() if upd is not None and str(upd).strip() else None
    )
    return out


def _fundamentals_dict_to_api(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalizuje słownik fundamentów do odpowiedzi API / dashboardu."""
    out: Dict[str, Any] = {k: None for k in FUND_KEYS}
    for k in FUND_SECTOR_KEYS:
        out[k] = None
    out["Fund_Source"] = "none"
    out["Fund_Updated_At"] = None
    if not data:
        return out
    for k in FUND_KEYS:
        out[k] = _scalarize_fund_value(data.get(k))
    for k in FUND_SECTOR_KEYS:
        raw = data.get(k)
        if k == "Fund_PE_vs_Sector":
            out[k] = _scalarize_fund_value(raw)
        else:
            s = str(raw or "").strip()
            out[k] = s or None
    if data.get("Fund_Source"):
        out["Fund_Source"] = str(data.get("Fund_Source"))
    if data.get("Fund_Updated_At"):
        out["Fund_Updated_At"] = str(data.get("Fund_Updated_At"))
    return out


def _load_sector_median_pe_map() -> Dict[str, float]:
    """Mediana P/E per sektor z cache + CSV fundamentów."""
    try:
        rows = load_all_fundamentals_rows(
            cache_path=Path(_fundamentals_cache_path()),
            csv_path=Path(_fundamentals_csv_path()),
        )
        return compute_sector_median_pe(rows)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Nie udało się policzyć median sektorowych P/E: %s", exc)
        return {}


def _row_fundamentals(ticker: str) -> Dict[str, Any]:
    """Zwraca dict z polami Fund_* dla tickera (puste gdy brak)."""
    data = get_fundamentals_for_ticker(ticker, path=_fundamentals_csv_path())
    if not data:
        data = _fundamentals_from_cache(ticker)
    return _fundamentals_dict_to_api(data or {})


def _fetch_and_persist_fundamentals(
    ticker: str, *, force_refresh: bool = False, tv_http_fallback: bool = False
) -> Dict[str, Any]:
    """Pobiera fundamentale (yfinance + cache) i zapisuje do CSV."""
    ticker = str(ticker or "").strip()
    if not ticker:
        raise ValueError("Empty ticker")

    fund_cfg = _fundamentals_config()
    if not fund_cfg.get("enabled", True) or is_crypto(ticker):
        return _row_fundamentals(ticker)

    ttl = float(fund_cfg.get("cache_ttl_hours", 24))
    use_tv_http = tv_http_fallback and bool(fund_cfg.get("tv_fallback", True))
    try:
        data = fetch_fundamentals(
            ticker,
            tv_fallback_page=None,
            tv_http_fallback=use_tv_http,
            cache_path=_fundamentals_cache_path(),
            ttl_hours=ttl,
            force_refresh=force_refresh,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("fundamentals fetch %s zawiódł: %s", ticker, exc)
        return _row_fundamentals(ticker)

    save_fundamentals_row(
        {
            "Ticker": ticker,
            **{k: data.get(k) for k in FUND_KEYS},
            **{k: data.get(k) for k in FUND_SECTOR_KEYS},
            "Fund_Source": data.get("Fund_Source") or "none",
            "Fund_Updated_At": data.get("Fund_Updated_At") or "",
        },
        path=_fundamentals_csv_path(),
    )
    return _fundamentals_dict_to_api(data)


def _refresh_fundamentals_batch(
    tickers: List[str], *, force_refresh: bool = False
) -> Tuple[int, int]:
    """Odświeża brakujące fundamentale. Zwraca (przetworzone, z_danymi)."""
    if not tickers:
        return 0, 0
    fund_cfg = _fundamentals_config()
    if not fund_cfg.get("enabled", True):
        return 0, 0

    csv_path = _fundamentals_csv_path()
    tv_http = bool(fund_cfg.get("tv_fallback", True))
    processed = 0
    with_data = 0
    for ticker in tickers:
        t = str(ticker or "").strip()
        if not t or is_crypto(t):
            continue
        if not force_refresh:
            existing = get_fundamentals_for_ticker(t, path=csv_path)
            if _fundamentals_row_has_values(existing):
                continue
            if fundamentals_fetch_attempted(existing):
                continue
        api_row = _fetch_and_persist_fundamentals(
            t, force_refresh=force_refresh, tv_http_fallback=tv_http
        )
        processed += 1
        if _fundamentals_row_has_values(api_row):
            with_data += 1
    return processed, with_data


def _schedule_startup_fundamentals_refresh() -> None:
    """Po starcie serwera uzupełnia brakujące fundamentale w tle (yfinance)."""
    global _startup_fundamentals_scheduled
    if os.environ.get("PYTEST_RUNNING"):
        return
    if _startup_fundamentals_scheduled:
        return
    fund_cfg = _fundamentals_config()
    if not fund_cfg.get("enabled", True):
        return

    def _run() -> None:
        if not _fundamentals_refresh_lock.acquire(blocking=False):
            return
        try:
            cfg = load_config()
            tickers = [
                str(t).strip() for t in (cfg.get("tickers") or []) if str(t).strip()
            ]
            count, with_data = _refresh_fundamentals_batch(tickers, force_refresh=False)
            if count:
                logger.info(
                    "Startup fundamentals refresh: przetworzono %s, z danymi %s",
                    count,
                    with_data,
                )
        finally:
            _fundamentals_refresh_lock.release()

    t = threading.Timer(STARTUP_FUNDAMENTALS_DELAY_SEC, _run)
    t.daemon = True
    t.start()
    _startup_fundamentals_scheduled = True


def _sync_missing_fundamentals_for_dashboard(
    tickers: List[str], *, priority: Optional[set] = None
) -> None:
    """Synchronizuje brakujące fundamentale (limit na żądanie dashboardu)."""
    fund_cfg = _fundamentals_config()
    if not fund_cfg.get("enabled", True):
        return

    csv_path = _fundamentals_csv_path()
    missing: List[str] = []
    for ticker in tickers:
        t = str(ticker or "").strip()
        if not t or is_crypto(t):
            continue
        existing = get_fundamentals_for_ticker(t, path=csv_path)
        if _fundamentals_row_has_values(existing):
            continue
        if fundamentals_fetch_attempted(existing):
            continue
        cached = _fundamentals_from_cache(t)
        if fundamentals_fetch_attempted(cached):
            continue
        missing.append(t)

    if not missing:
        return

    priority = priority or set()
    missing.sort(
        key=lambda t: (
            0 if t.upper() in priority else 1,
            tickers.index(t) if t in tickers else 9999,
        )
    )
    batch = missing[:DASHBOARD_FUND_SYNC_MAX]

    from concurrent.futures import ThreadPoolExecutor, as_completed

    synced = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(_fetch_and_persist_fundamentals, t, force_refresh=False)
            for t in batch
        ]
        for fut in as_completed(futures):
            try:
                fut.result()
                synced += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug("Dashboard fundamentals sync: %s", exc)

    if synced:
        logger.info(
            "Dashboard fundamentals sync: pobrano %s/%s brakujących (pozostało %s)",
            synced,
            len(batch),
            max(0, len(missing) - len(batch)),
        )


def _resolve_results_file_for_refresh(date_id: Optional[str]) -> Optional[str]:
    """Date-scoped plik wynikowy; bez date_id bierze najnowszy."""
    if date_id:
        validate_results_date_id(date_id)
        filename = f"{CSV_PREFIX}{date_id}.csv"
        path = os.path.normpath(os.path.join(RESULTS_DIR, filename))
        if os.path.commonpath(
            [os.path.abspath(path), os.path.abspath(RESULTS_DIR)]
        ) != os.path.abspath(RESULTS_DIR):
            raise HTTPException(status_code=400, detail="Invalid path")
        return path if os.path.exists(path) else None
    files = get_csv_files()
    return files[0] if files else None


def _latest_results_csv_snapshot() -> Tuple[Optional[str], List[str]]:
    """Zwraca (data_etykieta, lista tickerów) z najnowszego pliku wyników."""
    files = get_csv_files()
    if not files:
        return None, []
    latest = files[0]
    label = parse_date_from_filename(latest)
    tickers: List[str] = []
    try:
        with open(latest, "r", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                t = str(row.get("Ticker") or "").strip()
                if t:
                    tickers.append(t)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nie można odczytać tickerów z %s: %s", latest, exc)
    return label, tickers


def _resolve_no_data_tickers(
    date_id: Optional[str], requested_tickers: Optional[List[str]] = None
) -> List[str]:
    """Tickery kwalifikujące się do trybu „Odśwież Brak Danych” (zgodnie z dashboardem)."""
    del date_id  # zachowane w API; źródłem prawdy jest stan dashboardu, nie jeden plik CSV
    config = load_config()
    config_tickers = [
        str(t).strip() for t in (config.get("tickers") or []) if str(t).strip()
    ]
    if requested_tickers:
        wanted = {str(t).strip().upper() for t in requested_tickers if str(t).strip()}
        config_tickers = [t for t in config_tickers if str(t).strip().upper() in wanted]

    latest_label, latest_tickers = _latest_results_csv_snapshot()
    dashboard = build_dashboard(sync_fundamentals=False)
    return config_tickers_with_no_data(
        config_tickers,
        dashboard.get("data") or [],
        latest_scrape_date=latest_label,
        tickers_in_latest_csv=latest_tickers,
    )


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
    config = load_config()
    indicators = config.get("indicators") or ["PCA", "HTS Panel", "MacD"]
    config_tickers = list(config.get("tickers") or [])

    results = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row.get("Ticker", "")
                raw_name = row.get("Company_Name", "")

                cleaned = clean_company_name(ticker, raw_name, watchlist)
                tkr_norm = (ticker or "").strip().upper()
                if not cleaned or cleaned.strip().upper() == tkr_norm:
                    rest_name = lookup_company_name(ticker)
                    if rest_name:
                        cleaned = rest_name
                row["Company_Name"] = cleaned

                # Backfill Exchange dla starych CSV-ek bez kolumny i dla świeżych,
                # które nie złapały giełdy ze scrapera.
                exch_raw = str(row.get("Exchange") or "").strip().upper()
                if not exch_raw:
                    if ":" in ticker:
                        prefix = ticker.split(":", 1)[0].strip().upper()
                        if prefix:
                            exch_raw = prefix
                    if not exch_raw:
                        bare = ticker.split(":", 1)[-1].strip()
                        try:
                            exch_raw = (lookup_exchange(bare) or "").strip().upper()
                        except Exception:  # noqa: BLE001
                            exch_raw = ""
                row["Exchange"] = exch_raw

                config_resolution = _resolve_config_symbol(ticker, config_tickers)
                row["In_Config"] = bool(config_resolution.get("in_config"))
                row["Config_Match"] = str(config_resolution.get("match") or "")
                row["Config_Status"] = str(config_resolution.get("status") or "unknown")
                row["Config_Candidates"] = config_resolution.get("candidates") or []

                wl_data = watchlist.get(ticker, {})
                if not wl_data:
                    for wl_sym, wl_d in watchlist.items():
                        if wl_sym.split(".")[0] == ticker:
                            wl_data = wl_d
                            break

                if wl_data:
                    row["WL_Market_Cap"] = wl_data.get("Market_Cap", "")
                    row["WL_PE_Ratio"] = wl_data.get("PE_Ratio", "")
                    row["WL_Chg_Pct"] = wl_data.get("Chg_Pct", "")
                    row["WL_YTD"] = wl_data.get("YTD", "")
                    row["WL_1Y"] = wl_data.get("1Y", "")

                # Dolepiamy fundamentale per ticker (jeden wiersz na ticker w
                # results/fundamentals.csv niezależnie od interwału).
                fund = _row_fundamentals(ticker)
                for k, v in fund.items():
                    row[k] = v

                results.append(row)

        for row in results:
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
            row["Indicator_Errors"] = build_indicator_errors(row, indicators)

            try:
                sig_map = compute_row_signals(row, indicators=indicators)
            except Exception:
                sig_map = {}
            for strat_id in SIGNAL_STRATEGIES.keys():
                row[f"Computed_Signal_{strat_id}"] = sig_map.get(strat_id, "")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Nie udało się wczytać wyników %s: %s", date_id, e)
        raise HTTPException(
            status_code=500, detail="Nie udało się wczytać danych dla tej daty."
        )

    return {
        "data": results,
        "signal_strategies": [
            {"id": k, "label": SIGNAL_STRATEGY_LABELS[k]}
            for k in SIGNAL_STRATEGIES.keys()
        ],
    }


@app.get("/api/fundamentals")
def get_fundamentals_all():
    """Lista wszystkich wierszy z fundamentals.csv."""
    df = load_fundamentals_dataframe(_fundamentals_csv_path())
    if df is None or df.empty:
        return {"data": []}
    rows: List[Dict[str, Any]] = []
    for rec in df.to_dict(orient="records"):
        clean: Dict[str, Any] = {}
        ticker = str(rec.get("Ticker") or "").strip()
        if not ticker:
            continue
        clean["Ticker"] = ticker
        for k in FUND_KEYS:
            clean[k] = _scalarize_fund_value(rec.get(k))
        src = rec.get("Fund_Source")
        clean["Fund_Source"] = (str(src).strip() if src is not None and str(src).strip() else "none")
        upd = rec.get("Fund_Updated_At")
        clean["Fund_Updated_At"] = (str(upd).strip() if upd is not None and str(upd).strip() else None)
        rows.append(clean)
    return {"data": rows}


@app.get("/api/fundamentals/{ticker}")
def get_fundamentals_one(ticker: str):
    if not ticker or not TICKER_PATTERN.match(ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker")
    data = get_fundamentals_for_ticker(ticker, path=_fundamentals_csv_path())
    if not data:
        raise HTTPException(status_code=404, detail="Brak fundamentów dla tickera")
    out: Dict[str, Any] = {"Ticker": ticker}
    for k in FUND_KEYS:
        out[k] = _scalarize_fund_value(data.get(k))
    src = data.get("Fund_Source")
    out["Fund_Source"] = (
        str(src).strip() if src is not None and str(src).strip() else "none"
    )
    upd = data.get("Fund_Updated_At")
    out["Fund_Updated_At"] = (
        str(upd).strip() if upd is not None and str(upd).strip() else None
    )
    return out


@app.post("/api/fundamentals/refresh")
def refresh_fundamentals(body: FundamentalsRefreshRequest):
    """Odśwież fundamentale (yfinance + opcjonalny TV HTTP fallback dla GPW).

    Body:
      * ``{"tickers": ["AAPL", "GPW:TXT"]}`` — odśwież podaną listę.
      * ``{"all": true}`` — odśwież wszystkie tickery z ``scraper_config.json``.
    """
    cfg = load_config()
    fund_cfg = _fundamentals_config()
    tv_http = bool(fund_cfg.get("tv_fallback", True))

    if body.all:
        targets = [str(t).strip() for t in (cfg.get("tickers") or []) if str(t).strip()]
    else:
        targets = [str(t).strip() for t in (body.tickers or []) if str(t).strip()]

    if not targets:
        raise HTTPException(status_code=400, detail="No tickers specified")

    yf_ok, yf_err = check_yfinance_available()
    if not yf_ok and not tv_http:
        raise HTTPException(
            status_code=503,
            detail=(
                f"yfinance niedostępny ({yf_err or 'import failed'}). "
                "Zainstaluj: pip install yfinance"
            ),
        )

    refreshed: List[Dict[str, Any]] = []
    with_data = 0
    skipped_crypto = 0
    sources: Dict[str, int] = defaultdict(int)

    for ticker in targets:
        if is_crypto(ticker):
            skipped_crypto += 1
            continue
        api_row = _fetch_and_persist_fundamentals(
            ticker, force_refresh=True, tv_http_fallback=tv_http
        )
        src = str(api_row.get("Fund_Source") or "none").strip().lower() or "none"
        sources[src] += 1
        if _fundamentals_row_has_values(api_row):
            with_data += 1
        refreshed.append({"Ticker": ticker, **api_row})

    count = len(refreshed)
    without_data = count - with_data

    if with_data == 0:
        parts = [f"Brak danych fundamentalnych dla {count} tickerów."]
        if not yf_ok:
            parts.append(
                f"yfinance niedostępny ({yf_err or 'import failed'}). "
                "Zainstaluj: pip install yfinance"
            )
        raise HTTPException(status_code=503, detail=" ".join(parts))

    if without_data > 0:
        status = "partial"
        message = f"Przetworzono {count} tickerów, z danymi {with_data}."
    else:
        status = "ok"
        message = f"Zaktualizowano {with_data} tickerów."

    return {
        "status": status,
        "message": message,
        "count": count,
        "with_data": with_data,
        "without_data": without_data,
        "skipped_crypto": skipped_crypto,
        "yfinance_available": yf_ok,
        "sources": dict(sources),
        "refreshed": refreshed,
    }


def _config_ticker_for_csv_symbol(
    csv_ticker: str, config_tickers: List[str]
) -> Optional[str]:
    """Mapuje symbol z CSV na kanoniczny ticker z configu (lub ``None`` gdy brak)."""
    resolution = _resolve_config_symbol(csv_ticker, config_tickers, allow_similar_match=True)
    if resolution.get("in_config") and resolution.get("match"):
        return str(resolution["match"])
    return None


def _scan_latest_rows_per_ticker_interval(
    tickers: List[str],
    intervals: List[str],
    indicators: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Dla każdej pary (ticker, interval) zwróć najlepszy wiersz (merge per wskaźnik).

    Skanuje pliki ``results/tradingview_results_*.csv`` od najnowszego do
    najstarszego. Najnowszy wiersz z kompletnymi lub częściowymi danymi jest
    bazą; brakujące wskaźniki są uzupełniane ze starszych plików. Wiersze
    ``Scrape_Status=SKIPPED`` oraz całkowicie puste są pomijane; częściowe
    wiersze z nowszego pliku nie blokują starszych wartości HTS/PCA/MacD.
    """
    from results_store import (
        merge_indicator_into_row,
        normalize_served_scrape_status,
        row_has_all_configured_indicators,
        row_has_indicator_data,
        row_skipped_for_dashboard,
    )

    inds = indicators or ["PCA", "HTS Panel", "MacD"]
    wanted_intervals = {str(i).strip() for i in intervals if str(i).strip()}
    found: Dict[str, Dict[str, Dict[str, Any]]] = {}

    files = get_csv_files()
    for fpath in files:
        base = os.path.basename(fpath)
        date_id = base.replace(CSV_PREFIX, "").replace(".csv", "")
        if not RESULTS_DATE_ID_PATTERN.match(date_id):
            continue
        refresh_label = parse_date_from_filename(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    csv_ticker = str(row.get("Ticker") or "").strip()
                    interval = str(row.get("Interval") or "").strip()
                    if not csv_ticker or not interval:
                        continue
                    config_ticker = _config_ticker_for_csv_symbol(csv_ticker, tickers)
                    if not config_ticker:
                        continue
                    t_u = config_ticker.upper()
                    if wanted_intervals and interval not in wanted_intervals:
                        continue
                    if str(row.get("Scrape_Status") or "").strip().upper() == "SKIPPED":
                        continue

                    normalized = normalize_served_scrape_status(dict(row), inds)
                    ser = pd.Series(normalized)
                    has_any_indicator = any(
                        row_has_indicator_data(ser, ind) for ind in inds
                    )
                    scrape_st = str(normalized.get("Scrape_Status") or "").strip().upper()
                    if not has_any_indicator and scrape_st != "NO_DATA":
                        continue

                    bucket = found.setdefault(t_u, {}).get(interval)
                    if not has_any_indicator and scrape_st == "NO_DATA":
                        if bucket is None:
                            found[t_u][interval] = {
                                "row": normalized,
                                "last_refresh": refresh_label,
                                "_refresh_dates": {refresh_label},
                            }
                        continue

                    if bucket is None:
                        found[t_u][interval] = {
                            "row": normalized,
                            "last_refresh": refresh_label,
                            "_refresh_dates": {refresh_label},
                        }
                        continue

                    target_row = bucket["row"]
                    target_ser = pd.Series(target_row)
                    merged_any = False
                    for ind in inds:
                        if row_has_indicator_data(target_ser, ind):
                            continue
                        if not row_has_indicator_data(ser, ind):
                            continue
                        merge_indicator_into_row(target_row, normalized, ind)
                        merged_any = True

                    refresh_dates = bucket.setdefault("_refresh_dates", set())
                    refresh_dates.add(refresh_label)
                    if merged_any:
                        bucket["row"] = normalize_served_scrape_status(target_row, inds)
                        if row_has_all_configured_indicators(bucket["row"], inds):
                            bucket["row"]["Scrape_Status"] = "OK"
                            bucket["row"]["Scrape_Error"] = ""
                        bucket["last_refresh"] = refresh_label
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dashboard scan: nie można odczytać %s: %s", fpath, exc)
            continue

    for ticker_buckets in found.values():
        for bucket in ticker_buckets.values():
            bucket.pop("_refresh_dates", None)
    return found


def _pick_current_price_from_intervals(
    intervals_data: Dict[str, Any], intervals: List[str]
) -> str:
    """Prefer 1D, then first interval row with non-empty Current_Price."""
    prefer = ["1D"] + [i for i in intervals if i != "1D"]
    for iv in prefer:
        bucket = intervals_data.get(iv) or {}
        row = bucket.get("row") if bucket else None
        if row:
            price = str(row.get("Current_Price") or "").strip()
            if price:
                return price
    return ""


def _flatten_dashboard_to_rows(
    dashboard_tickers: List[Dict[str, Any]], indicators: List[str]
) -> List[Dict[str, Any]]:
    """Spłaszcza strukturę tickers → płaskie wiersze dla frontendu (data.data)."""
    rows: List[Dict[str, Any]] = []
    for entry in dashboard_tickers:
        ticker = entry["ticker"]
        company_name = entry.get("company_name") or ticker
        exchange = entry.get("exchange") or ""
        fundamentals = entry.get("fundamentals") or {}
        for interval, bucket in (entry.get("intervals") or {}).items():
            raw_row = bucket.get("row") if bucket else None
            if raw_row:
                row = dict(raw_row)
            else:
                row = {
                    "Ticker": ticker,
                    "Company_Name": company_name,
                    "Exchange": exchange,
                    "Interval": interval,
                    "Scrape_Status": "",
                }
            # Config jest źródłem prawdy — nie pokazujemy surowego symbolu z CSV.
            row["Ticker"] = ticker
            row["Company_Name"] = row.get("Company_Name") or company_name
            row["Exchange"] = row.get("Exchange") or exchange
            row["Interval"] = row.get("Interval") or interval
            row["In_Config"] = True
            row["Config_Match"] = ticker
            row["Config_Status"] = "exact"
            row["Config_Candidates"] = []
            last_refresh = bucket.get("last_refresh") if bucket else None
            if last_refresh:
                row["Last_Refresh"] = last_refresh
            composite = entry.get("composite") or {}
            for k, v in fundamentals.items():
                row[k] = v
            row["Composite_Verdict"] = composite.get("verdict") or ""
            row["Composite_Score"] = composite.get("score")
            breakdown = composite.get("breakdown") or {}
            row["Composite_Breakdown_Fund"] = breakdown.get("fund")
            row["Composite_Breakdown_Tech"] = breakdown.get("tech")
            row["Composite_Breakdown_Consensus"] = breakdown.get("consensus")
            row["Composite_Flags"] = composite.get("flags") or []
            rows.append(row)

    for row in rows:
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
        row["Indicator_Errors"] = build_indicator_errors(row, indicators)
        try:
            sig_map = compute_row_signals(row, indicators=indicators)
        except Exception:
            sig_map = {}
        for strat_id in SIGNAL_STRATEGIES.keys():
            row[f"Computed_Signal_{strat_id}"] = sig_map.get(strat_id, "")

    return rows


def build_dashboard(*, sync_fundamentals: bool = True) -> Dict[str, Any]:
    """Backbone /api/dashboard — wystawiony do bezpośrednich testów."""
    config = load_config()
    tickers = [str(t).strip() for t in (config.get("tickers") or []) if str(t).strip()]
    intervals = [
        str(i).strip()
        for i in (config.get("intervals") or ["1D", "1W", "1M"])
        if str(i).strip()
    ]
    watchlist = get_watchlist()

    indicators = config.get("indicators") or ["PCA", "HTS Panel", "MacD"]
    latest = _scan_latest_rows_per_ticker_interval(tickers, intervals, indicators)

    if sync_fundamentals:
        _sync_missing_fundamentals_for_dashboard(
            tickers, priority=set(latest.keys())
        )

    sector_medians = _load_sector_median_pe_map()

    dashboard_tickers: List[Dict[str, Any]] = []
    for ticker in tickers:
        t_u = ticker.upper()
        intervals_data: Dict[str, Any] = {}
        last_refresh_any: Optional[str] = None
        company_name: str = ""
        exchange: str = ""

        for interval in intervals:
            bucket = latest.get(t_u, {}).get(interval)
            if bucket:
                row = bucket["row"]
                last_refresh = bucket["last_refresh"]
                if not company_name:
                    raw_name = row.get("Company_Name", "")
                    cn = clean_company_name(ticker, raw_name, watchlist)
                    if not cn or cn.strip().upper() == t_u:
                        cn = lookup_company_name(ticker) or cn
                    company_name = cn or ""
                if not exchange:
                    exch_raw = str(row.get("Exchange") or "").strip().upper()
                    if not exch_raw and ":" in ticker:
                        exch_raw = ticker.split(":", 1)[0].strip().upper()
                    if not exch_raw:
                        bare = ticker.split(":", 1)[-1].strip()
                        try:
                            exch_raw = (lookup_exchange(bare) or "").strip().upper()
                        except Exception:  # noqa: BLE001
                            exch_raw = ""
                    exchange = exch_raw
                intervals_data[interval] = {
                    "row": row,
                    "last_refresh": last_refresh,
                }
                if last_refresh and (
                    last_refresh_any is None or last_refresh > last_refresh_any
                ):
                    last_refresh_any = last_refresh
            else:
                intervals_data[interval] = {
                    "row": None,
                    "last_refresh": None,
                }

        if not company_name:
            cn = clean_company_name(ticker, "", watchlist)
            if not cn or cn.strip().upper() == t_u:
                cn = lookup_company_name(ticker) or ""
            company_name = cn or ticker
        if not exchange and ":" in ticker:
            exchange = ticker.split(":", 1)[0].strip().upper()

        fundamentals = enrich_fundamentals_with_sector_pe(
            _row_fundamentals(ticker), sector_medians
        )
        fundamentals = _fundamentals_dict_to_api(fundamentals)
        interval_rows: Dict[str, Dict[str, Any]] = {}
        for iv, bucket in intervals_data.items():
            raw = (bucket or {}).get("row")
            if raw:
                interval_rows[iv] = raw
        composite = compute_composite_verdict(
            ticker, interval_rows, fundamentals, indicators=indicators
        )
        current_price = _pick_current_price_from_intervals(intervals_data, intervals)
        dashboard_tickers.append(
            {
                "ticker": ticker,
                "company_name": company_name,
                "exchange": exchange,
                "current_price": current_price,
                "intervals": intervals_data,
                "fundamentals": fundamentals,
                "composite": composite,
                "last_refresh_any": last_refresh_any,
            }
        )

    flat_rows = _flatten_dashboard_to_rows(dashboard_tickers, indicators)
    return {
        "tickers": dashboard_tickers,
        "data": flat_rows,
        "config_ticker_count": len(tickers),
        "signal_strategies": [
            {"id": k, "label": SIGNAL_STRATEGY_LABELS[k]}
            for k in SIGNAL_STRATEGIES.keys()
        ],
    }


@app.get("/api/dashboard")
def get_dashboard():
    try:
        return build_dashboard()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Nie udało się zbudować dashboardu: %s", exc)
        raise HTTPException(status_code=500, detail="Nie udało się zbudować dashboardu.")


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


@app.get("/api/health")
def api_health():
    """Status CDP, karty TradingView i yfinance."""
    cfg = load_config()
    try:
        port = int(cfg.get("cdp_port", 9222))
    except (TypeError, ValueError):
        port = 9222

    cdp_ok = _cdp_is_listening(port)
    if cdp_ok:
        cdp_message = f"Nasłuch CDP aktywny na porcie {port}"
    else:
        cdp_message = f"Brak nasłuchu CDP na porcie {port}"

    tv_url: Optional[str] = None
    tv_ok = False
    tv_message = "Brak połączenia CDP — nie można sprawdzić kart"
    if cdp_ok:
        try:
            tv_url = cdp_find_tradingview_chart_url(port)
            tv_ok = bool(tv_url)
            if tv_ok:
                tv_message = "Znaleziono kartę z wykresem TradingView"
            else:
                tv_message = "Brak otwartej karty TradingView z wykresem"
        except Exception as exc:  # noqa: BLE001
            tv_message = f"Nie udało się odczytać listy kart CDP: {exc}"

    yf_ok, yf_err = check_yfinance_available()
    yf_message = "Pakiet yfinance dostępny" if yf_ok else (yf_err or "yfinance niedostępny")

    return {
        "cdp": {"ok": cdp_ok, "port": port, "message": cdp_message},
        "tradingview_tab": {
            "ok": tv_ok,
            "url": tv_url or "",
            "message": tv_message,
        },
        "yfinance": {"ok": yf_ok, "message": yf_message},
    }


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


@app.get("/api/tickers/no_data")
def get_no_data_tickers():
    """Lista tickerów z configu oznaczonych na dashboardzie jako „Brak danych”."""
    tickers = _resolve_no_data_tickers(None, None)
    return {"tickers": tickers, "count": len(tickers)}


@app.post("/api/scraper/run")
def run_scraper(body: ScraperRunRequest):
    tickers = [t.strip() for t in body.tickers if t and t.strip()]
    run_indicators = _normalize_scraper_indicators(body.indicators)
    if body.no_data_only:
        target_tickers = _resolve_no_data_tickers(body.date_id, tickers or None)
        if not target_tickers:
            return {
                "status": "no_data_empty",
                "count": 0,
                "scope": "no_data_only",
                "tickers": [],
                "message": (
                    "Brak tickerów z „Brak danych” na dashboardzie (0 do odświeżenia)."
                ),
            }
        started = start_scraper_subprocess(target_tickers, run_indicators)
        if isinstance(started, dict):
            started.setdefault("scope", "no_data_only")
            started["count"] = len(target_tickers)
            started["tickers"] = target_tickers
        return started

    # Fresh start dla pełnego runu (bez tickers / bez subset wskaźników) — usuwamy
    # ewentualny pending state, żeby scraper nie dopisywał do wczorajszego pliku.
    if body.fresh and not tickers and not run_indicators:
        try:
            if os.path.exists(STATE_FILE):
                os.remove(STATE_FILE)
                logger.info("Fresh start: usunięto %s", STATE_FILE)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Fresh start: nie udało się usunąć %s: %s", STATE_FILE, exc
            )
    return start_scraper_subprocess(
        tickers if tickers else None,
        run_indicators,
    )


@app.get("/api/scraper/pending_run")
def scraper_pending_run():
    """Zwraca info o niedokończonym runie z scraper_state.json.

    State file pozostaje, gdy poprzedni run zakończył się awaryjnie (crash / Stop
    w trakcie). Po pełnym sukcesie scraper sam usuwa state. UI używa tego, żeby
    przed „Uruchom wszystkie" zapytać użytkownika: Wznów czy zacznij od nowa.
    """
    if not os.path.exists(STATE_FILE):
        return {"has_pending": False}

    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("pending_run: cannot read %s: %s", STATE_FILE, exc)
        return {"has_pending": False}

    current_file = str(state.get("current_file") or "")
    if not current_file:
        return {"has_pending": False}

    file_exists = os.path.exists(current_file)

    current_file_date = ""
    base = os.path.basename(current_file)
    m = re.match(rf"^{re.escape(CSV_PREFIX)}(\d{{4}}-\d{{2}}-\d{{2}})", base)
    if m:
        current_file_date = m.group(1)

    processed = state.get("processed") or []
    processed_count = len(processed) if isinstance(processed, list) else 0

    cfg = load_config()
    cfg_tickers = list(cfg.get("tickers") or [])
    cfg_intervals = list(cfg.get("intervals") or ["1D", "1W", "1M"])
    total_in_config = len(cfg_tickers) * max(len(cfg_intervals), 1)
    remaining_count = max(total_in_config - processed_count, 0)

    mtime = None
    try:
        mtime = os.path.getmtime(STATE_FILE)
    except OSError:
        pass

    return {
        "has_pending": True,
        "current_file": current_file,
        "current_file_exists": file_exists,
        "current_file_date": current_file_date,
        "processed_count": processed_count,
        "total_in_config": total_in_config,
        "remaining_count": remaining_count,
        "state_mtime": mtime,
    }


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


class TickerRenameError(Exception):
    """Błąd logiki rename — z kodem HTTP do zmapowania w warstwie endpointu."""

    def __init__(self, status_code: int, detail: Any):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _ticker_base_dot(sym: str) -> str:
    return (sym or "").split(".", 1)[0]


def _ticker_without_exchange(sym: str) -> str:
    return (sym or "").split(":", 1)[-1]


def _ticker_compact(sym: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (sym or "").upper())


def _ticker_match_variants(sym: str) -> set[str]:
    """Warianty porównawcze dla symboli z CSV vs config.

    Celowo nie służy do automatycznego rename dla fuzzy matchy — pomaga znaleźć
    kandydatów i bezpieczne exact/base trafienia (`GPW:ATC` ↔ `ATC`,
    `LULU.O` ↔ `LULU`).
    """
    s = (sym or "").strip().upper()
    no_exch = _ticker_without_exchange(s)
    vals = {
        s,
        _ticker_base_dot(s),
        no_exch,
        _ticker_base_dot(no_exch),
        _ticker_compact(s),
        _ticker_compact(no_exch),
    }
    return {v for v in vals if v}


def _rename_candidates(old_u: str, tickers: List[str]) -> List[Dict[str, Any]]:
    """Zwraca podpowiedzi z configu dla symbolu, którego nie znaleziono exact.

    `score=100` oznacza bezpieczne trafienie po wariancie (`ATC` vs `GPW:ATC`).
    Niższy score to tylko podpowiedź podobieństwa (`DIAP` vs `GPW:DIA`).
    """
    old_variants = _ticker_match_variants(old_u)
    old_compacts = {_ticker_compact(v) for v in old_variants if _ticker_compact(v)}
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for raw in tickers:
        sym = str(raw or "").strip()
        sym_u = sym.upper()
        if not sym or sym_u in seen:
            continue
        seen.add(sym_u)
        variants = _ticker_match_variants(sym_u)
        reason = ""
        score = 0
        if old_variants.intersection(variants):
            reason = "match_variant"
            score = 100
        else:
            for v in variants:
                c = _ticker_compact(v)
                for old_compact in old_compacts:
                    if not old_compact or not c or min(len(old_compact), len(c)) < 3:
                        continue
                    if (
                        abs(len(old_compact) - len(c)) <= 2
                        and (old_compact.startswith(c) or c.startswith(old_compact))
                    ):
                        reason = "similar_prefix"
                        score = max(score, min(len(old_compact), len(c)))
                    ratio = SequenceMatcher(None, old_compact, c).ratio()
                    if ratio >= 0.75:
                        reason = "similar_symbol"
                        score = max(score, int(round(ratio * 100)))
        if score:
            out.append({"ticker": sym, "score": score, "reason": reason})

    out.sort(key=lambda x: (-int(x.get("score") or 0), str(x.get("ticker") or "")))
    return out[:5]


def _resolve_config_symbol(
    ticker: str,
    config_tickers: List[str],
    preferred_new: str = "",
    *,
    allow_similar_match: bool = False,
) -> Dict[str, Any]:
    """Rozpoznaje relację tickera z CSV/requestu do aktualnego configu.

    Zwraca jeden format dla ``/api/results`` i ``/api/tickers/rename``.
    ``In_Config=True`` tylko dla exact albo bezpiecznego jednoznacznego wariantu.
    Stare symbole z CSV dostają ``Config_Status=stale`` i listę kandydatów.

    Gdy ``allow_similar_match=True`` (skan dashboardu), pojedynczy kandydat
    z wysokim score może zostać zmapowany na config (np. ``ASBP`` → ``GPW:ASB``).
    """
    t_u = (ticker or "").strip().upper()
    upper = [str(t or "").strip().upper() for t in config_tickers]
    if t_u in upper:
        return {
            "in_config": True,
            "match": str(config_tickers[upper.index(t_u)]),
            "status": "exact",
            "candidates": [],
        }
    variants = _ticker_match_variants(t_u)
    matches = [
        i for i, cfg in enumerate(upper)
        if variants.intersection(_ticker_match_variants(cfg))
    ]
    if len(matches) == 1:
        return {
            "in_config": True,
            "match": str(config_tickers[matches[0]]),
            "status": "variant",
            "candidates": [],
        }

    candidates = _rename_candidates(t_u, config_tickers)
    preferred_u = (preferred_new or "").strip().upper()
    if preferred_u in upper:
        preferred = {
            "ticker": config_tickers[upper.index(preferred_u)],
            "score": 100,
            "reason": "new_exists",
        }
        candidates = [
            c for c in candidates
            if str(c.get("ticker") or "").strip().upper() != preferred_u
        ]
        candidates.insert(0, preferred)

    if allow_similar_match:
        strong = [
            c for c in candidates if int(c.get("score") or 0) >= 100
        ]
        if len(strong) == 1:
            return {
                "in_config": True,
                "match": str(strong[0]["ticker"]),
                "status": "variant",
                "candidates": [],
            }

        if len(candidates) == 1 and int(candidates[0].get("score") or 0) >= 75:
            return {
                "in_config": True,
                "match": str(candidates[0]["ticker"]),
                "status": "variant",
                "candidates": [],
            }

    return {
        "in_config": False,
        "match": "",
        "status": "stale" if candidates else "unknown",
        "candidates": candidates,
    }


def _config_match_for_ticker(ticker: str, config_tickers: List[str]) -> str:
    """Najlepszy nieinwazyjny match CSV tickera do configu; pusty string gdy brak."""
    return str(_resolve_config_symbol(ticker, config_tickers).get("match") or "")


def _apply_ticker_rename(config: Dict[str, Any], old: str, new: str) -> Dict[str, Any]:
    """In-place rename w ``config['tickers']``. Modyfikuje przekazany dict.

    Zwraca słownik z metadanymi (``matched_old``, ``new``, ``requested_old``,
    ``tickers``). Rzuca ``TickerRenameError`` przy walidacji / konfliktach.
    """
    old_u = (old or "").strip().upper()
    new_u = (new or "").strip().upper()
    if not TICKER_PATTERN.match(old_u):
        raise TickerRenameError(400, "Invalid old ticker")
    if not TICKER_PATTERN.match(new_u):
        raise TickerRenameError(400, "Invalid new ticker")
    if old_u == new_u:
        raise TickerRenameError(400, "Old and new ticker are identical")

    tickers = list(config.get("tickers") or [])
    upper_list = [str(t).strip().upper() for t in tickers]

    resolution = _resolve_config_symbol(old_u, tickers, preferred_new=new_u)
    if resolution.get("in_config") and resolution.get("match"):
        match_u = str(resolution["match"]).strip().upper()
        idx = upper_list.index(match_u)
    else:
        hints = list(resolution.get("candidates") or [])
        hard_matches = [
            h for h in hints
            if int(h.get("score") or 0) >= 100
            and str(h.get("reason") or "") in {"match_variant", "base_match"}
        ]
        if len(hard_matches) > 1:
            matched = [h.get("ticker") for h in hard_matches]
            raise TickerRenameError(
                409,
                {
                    "message": (
                        f"Niejednoznaczne dopasowanie dla {old_u}: {matched}. "
                        "Usuń duplikaty w konfiguracji."
                    ),
                    "requested_old": old_u,
                    "config_status": resolution.get("status") or "unknown",
                    "candidates": hints,
                },
            )
        raise TickerRenameError(
            404,
            {
                "message": f"Ticker {old_u} not found in config",
                "requested_old": old_u,
                "config_status": resolution.get("status") or "unknown",
                "candidates": hints,
            },
        )

    if new_u in upper_list and upper_list.index(new_u) != idx:
        raise TickerRenameError(409, f"Ticker {new_u} already exists in config")

    matched_old = tickers[idx]
    tickers[idx] = new_u
    config["tickers"] = tickers
    return {
        "status": "renamed",
        "old": matched_old,
        "requested_old": old_u,
        "new": new_u,
        "tickers": tickers,
    }


def _rename_fundamentals_cache_ticker(old_ticker: str, new_ticker: str) -> bool:
    """Przenosi wpis tickera w JSON cache fundamentów (``data/.fundamentals_cache.json``)."""
    old_u = str(old_ticker or "").strip().upper()
    new_u = str(new_ticker or "").strip().upper()
    if not old_u or not new_u or old_u == new_u:
        return False
    cache_path = _fundamentals_cache_path()
    if not cache_path or not os.path.exists(cache_path):
        return False
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            cache = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nie można odczytać cache fundamentów do rename: %s", exc)
        return False
    if not isinstance(cache, dict):
        return False

    old_entry = cache.pop(old_u, None)
    if not isinstance(old_entry, dict):
        return False

    new_entry = cache.get(new_u)
    if isinstance(new_entry, dict):
        for key, val in old_entry.items():
            if key == "Ticker":
                continue
            new_val = new_entry.get(key)
            if new_val in (None, "") and val not in (None, ""):
                new_entry[key] = val
        old_entry = new_entry
    old_entry["Ticker"] = new_u
    cache[new_u] = old_entry

    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nie można zapisać cache fundamentów po rename: %s", exc)
        return False
    return True


def _migrate_ticker_symbol_everywhere(old_ticker: str, new_ticker: str) -> Dict[str, Any]:
    """Przepisuje historyczne wiersze CSV i fundamenty ze starego symbolu."""
    old_u = str(old_ticker or "").strip().upper()
    new_u = str(new_ticker or "").strip().upper()
    if not old_u or not new_u or old_u == new_u:
        return {
            "csv_files_modified": 0,
            "csv_rows_affected": 0,
            "fundamentals_migrated": False,
            "fundamentals_cache_migrated": False,
            "files": [],
        }

    files_out: List[Dict[str, Any]] = []
    rows_affected = 0
    files_modified = 0
    for path in sorted(get_csv_files()):
        affected, remaining = rename_ticker_rows_in_csv(path, old_u, new_u)
        if affected <= 0:
            continue
        rows_affected += affected
        files_modified += 1
        base = os.path.basename(path)
        files_out.append(
            {
                "filename": base,
                "date_id": base.replace(CSV_PREFIX, "").replace(".csv", ""),
                "rows_affected": affected,
                "remaining_rows": remaining,
            }
        )

    fundamentals_migrated = rename_fundamentals_ticker(
        old_u,
        new_u,
        path=_fundamentals_csv_path(),
    )
    fundamentals_cache_migrated = _rename_fundamentals_cache_ticker(old_u, new_u)

    return {
        "csv_files_modified": files_modified,
        "csv_rows_affected": rows_affected,
        "fundamentals_migrated": fundamentals_migrated,
        "fundamentals_cache_migrated": fundamentals_cache_migrated,
        "files": files_out,
    }


@app.post("/api/tickers/rename")
def rename_ticker(body: TickerRenameRequest):
    """Zmienia nazwę tickera w konfiguracji (zachowuje pozycję na liście).

    Dopasowanie ``old`` do wpisu w configu odbywa się w trzech krokach:
      1) exact (po normalizacji do upper),
      2) bazowy symbol (prefiks przed pierwszą kropką, np. ``LULU.O`` ↔ ``LULU``),
      3) gdy w configu jest dokładnie jeden kandydat z tym samym basem — używamy go.

    Po rename przepisuje też wiersze historycznych CSV oraz fundamenty ze
    starego symbolu na nowy, żeby dashboard od razu widział dane pod nową nazwą.
    """
    config = load_config()
    try:
        result = _apply_ticker_rename(config, body.old or "", body.new or "")
    except TickerRenameError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    save_config(config)
    migration = _migrate_ticker_symbol_everywhere(result["old"], result["new"])
    result.update(migration)
    return result


def _normalize_delete_ticker(ticker: str) -> str:
    ticker_u = str(ticker or "").strip().upper()
    if not ticker_u or not TICKER_PATTERN.match(ticker_u):
        raise HTTPException(status_code=400, detail="Invalid ticker")
    return ticker_u


def _ticker_delete_preview(ticker: str) -> Dict[str, Any]:
    ticker_u = _normalize_delete_ticker(ticker)
    config = load_config()
    config_tickers = list(config.get("tickers") or [])
    config_matches = [
        str(t)
        for t in config_tickers
        if str(t or "").strip().upper() == ticker_u
    ]

    files: List[Dict[str, Any]] = []
    rows_total = 0
    for path in sorted(get_csv_files()):
        rows = count_ticker_rows_in_csv(path, ticker_u)
        if rows <= 0:
            continue
        base = os.path.basename(path)
        date_id = base.replace(CSV_PREFIX, "").replace(".csv", "")
        rows_total += rows
        files.append(
            {
                "filename": base,
                "date_id": date_id,
                "rows": rows,
            }
        )

    return {
        "ticker": ticker_u,
        "in_config": bool(config_matches),
        "config_matches": config_matches,
        "config_removed_count": len(config_matches),
        "files_count": len(files),
        "rows_count": rows_total,
        "files": files,
    }


@app.get("/api/tickers/{ticker}/delete_preview")
def ticker_delete_preview(ticker: str):
    return _ticker_delete_preview(ticker)


@app.delete("/api/tickers/{ticker}")
def delete_ticker_everywhere(ticker: str):
    """Trwale usuwa ticker z configu i ze wszystkich historycznych CSV."""
    ticker_u = _normalize_delete_ticker(ticker)
    preview = _ticker_delete_preview(ticker_u)

    config = load_config()
    old_tickers = list(config.get("tickers") or [])
    new_tickers = [
        t for t in old_tickers
        if str(t or "").strip().upper() != ticker_u
    ]
    config_removed = len(old_tickers) - len(new_tickers)
    if config_removed:
        config["tickers"] = new_tickers
        save_config(config)
        reschedule_auto_scraper()

    files: List[Dict[str, Any]] = []
    rows_removed = 0
    for path in sorted(get_csv_files()):
        removed, remaining = remove_ticker_rows_from_csv(path, ticker_u)
        if removed <= 0:
            continue
        rows_removed += removed
        base = os.path.basename(path)
        files.append(
            {
                "filename": base,
                "date_id": base.replace(CSV_PREFIX, "").replace(".csv", ""),
                "removed_rows": removed,
                "remaining_rows": remaining,
            }
        )

    return {
        "status": "deleted",
        "ticker": ticker_u,
        "preview": preview,
        "config_removed_count": config_removed,
        "files_modified": len(files),
        "rows_removed": rows_removed,
        "files": files,
        "config": config,
    }


def _exchange_prefixes_from_config(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """Giełdy do auto-match: ``exchange_prefixes`` + prefixy z tickerów w configu."""
    if cfg is None:
        cfg = load_config()
    out: List[str] = []
    for p in cfg.get("exchange_prefixes") or []:
        s = str(p or "").strip().upper()
        if s and s not in out:
            out.append(s)
    for t in cfg.get("tickers") or []:
        ts = str(t or "").strip()
        if ":" not in ts:
            continue
        prefix = ts.split(":", 1)[0].strip().upper()
        if prefix and prefix not in out:
            out.append(prefix)
    return out


def _exchange_prefixes() -> List[str]:
    return _exchange_prefixes_from_config()


@app.get("/api/tickers/repair_no_data")
def repair_no_data_preview(date_id: Optional[str] = None):
    """Lista propozycji renamów dla no-data tickerów.

    Dla każdego no-data tickera bez ``:`` (czyli surowego) próbujemy znaleźć
    dopasowanie na giełdach z ``config['exchange_prefixes']`` przez TV REST.
    Tickery które już mają prefix giełdy są pomijane.
    """
    if date_id is not None:
        validate_results_date_id(date_id)
    prefixes = _exchange_prefixes()
    dashboard = build_dashboard(sync_fundamentals=False)
    config = load_config()
    config_tickers = [
        str(t).strip() for t in (config.get("tickers") or []) if str(t).strip()
    ]
    no_data = config_tickers_with_no_data(
        config_tickers,
        dashboard.get("data") or [],
        include_stale_and_partial=False,
    )

    items: List[Dict[str, Any]] = []
    for ticker in no_data:
        t = str(ticker or "").strip()
        if not t:
            continue
        if ":" in t:
            items.append(
                {
                    "old": t,
                    "candidates": [],
                    "skipped": True,
                    "note": "Ticker już ma prefix giełdy",
                }
            )
            continue
        try:
            matches = lookup_symbol_match(t, prefixes)
        except Exception as exc:  # noqa: BLE001
            logger.debug("repair preview: lookup failed for %s: %s", t, exc)
            matches = []
        candidates = [
            {
                "new": m.get("symbol") or "",
                "exchange": m.get("exchange") or "",
                "description": m.get("description") or "",
            }
            for m in matches
            if m.get("symbol")
        ]
        entry: Dict[str, Any] = {"old": t, "candidates": candidates}
        if not candidates:
            try:
                all_matches = fetch_symbol_matches(t)
            except Exception as exc:  # noqa: BLE001
                logger.debug("repair preview: all-match lookup failed for %s: %s", t, exc)
                all_matches = []
            allowed = {p.upper() for p in prefixes}
            other = [
                {
                    "new": m.get("symbol") or "",
                    "exchange": m.get("exchange") or "",
                    "description": m.get("description") or "",
                }
                for m in all_matches
                if m.get("symbol")
                and str(m.get("exchange") or "").upper() not in allowed
            ]
            if other:
                entry["other_candidates"] = other
                entry["note"] = (
                    f"Brak match-a na {prefixes or '[]'}; "
                    f"TradingView znalazło {len(other)} dopasowań na innych giełdach"
                )
            elif all_matches:
                entry["note"] = f"Brak match-a na {prefixes or '[]'}"
            else:
                entry["note"] = (
                    f"Brak match-a na {prefixes or '[]'} — "
                    "TradingView nie zwraca tego symbolu (sprawdź literówkę lub wpisz ręcznie)"
                )
        items.append(entry)

    return {
        "exchange_prefixes": prefixes,
        "items": items,
    }


@app.post("/api/tickers/repair_no_data")
def repair_no_data_apply(body: RepairNoDataRequest):
    """Hurtowy rename + opcjonalny rerun scrapera (no_data_only) na nowych nazwach."""
    if not body.renames:
        raise HTTPException(status_code=400, detail="Empty renames list")

    config = load_config()
    applied: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for pair in body.renames:
        try:
            res = _apply_ticker_rename(config, pair.old, pair.new)
        except TickerRenameError as exc:
            errors.append(
                {
                    "old": pair.old,
                    "new": pair.new,
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                }
            )
            continue
        applied.append(
            {
                "old": res["old"],
                "requested_old": res["requested_old"],
                "new": res["new"],
            }
        )

    if applied:
        save_config(config)

    response: Dict[str, Any] = {
        "status": "ok" if not errors else ("partial" if applied else "failed"),
        "applied": applied,
        "errors": errors,
        "tickers": list(config.get("tickers") or []),
    }

    if body.rerun and applied:
        new_names = [a["new"] for a in applied]
        target_tickers = _resolve_no_data_tickers(body.date_id, new_names)
        if target_tickers:
            started = start_scraper_subprocess(target_tickers)
            if isinstance(started, dict):
                started.setdefault("scope", "no_data_only")
                started["count"] = len(target_tickers)
                started["tickers"] = target_tickers
            response["scraper"] = started
        else:
            response["scraper"] = {
                "status": "no_data_empty",
                "message": "Brak no-data tickerów do uruchomienia po renamie.",
            }

    return response


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
