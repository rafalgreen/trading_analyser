"""
Wsp\u00f3lny modu\u0142 I/O dla plik\u00f3w wynik\u00f3w scrapera.

Zawiera sta\u0142e kolumn meta (`CSV_META_COLUMNS`), upsert po kluczu
(`Ticker`, `Interval`), helpery odczytu oraz predykaty okre\u015blaj\u0105ce czy
wiersz jest kompletny w \u015bwietle konfiguracji wska\u017anik\u00f3w. U\u017cywane przez
``tv_scraper.py``, ``app.py`` oraz ``scripts/repair_results_csv.py``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd


logger = logging.getLogger(__name__)


CSV_META_COLUMNS: List[str] = [
    "Ticker",
    "Company_Name",
    "Exchange",
    "Current_Price",
    "Interval",
    "Scrape_Status",
    "Scrape_Error",
]


def order_result_columns(columns: Iterable[str]) -> List[str]:
    """Meta zawsze pierwsze, reszta (kolumny wska\u017anik\u00f3w) alfabetycznie."""
    cols = list(columns)
    meta_set = set(CSV_META_COLUMNS)
    rest = sorted(c for c in cols if c not in meta_set)
    ordered = [c for c in CSV_META_COLUMNS if c in cols]
    for c in rest:
        if c not in ordered:
            ordered.append(c)
    for c in cols:
        if c not in ordered:
            ordered.append(c)
    return ordered


def ensure_meta_columns(df: pd.DataFrame) -> pd.DataFrame:
    for c in CSV_META_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[order_result_columns(df.columns)]


def cell_nonempty(val) -> bool:
    if val is None:
        return False
    try:
        if pd.isna(val):
            return False
    except Exception:
        pass
    s = str(val).strip()
    return s != "" and s.lower() not in ("nan", "none")


def value_counts_as_indicator_data(val, col: str) -> bool:
    """Puste placeholdery parsowania nie licz\u0105 si\u0119 jako \u201es\u0105 dane wska\u017anika\u201d."""
    if not cell_nonempty(val):
        return False
    s = str(val).strip().lower()
    if "brak danych na wykresie" in s:
        return False
    if "brak poprawnych danych" in s:
        return False
    if s == "brak":
        return False
    if col == "PCA_Values" and s in ("ok", "\u2014", "-"):
        return False
    return True


def row_has_indicator_data(row, ind_name: str) -> bool:
    """Czy w wierszu CSV s\u0105 zapisane dane dla wska\u017anika (dowolna powi\u0105zana kolumna)."""
    if row is None:
        return False
    ind_name = (ind_name or "").strip()
    # MacD wymaga twardo niepustego MacD_Line — sam Trend/Cross to za mało,
    # bo to potencjalnie residual z innej fazy wskaźnika.
    if ind_name.lower() == "macd":
        line_val = None
        try:
            line_val = row.get("MacD_Line")  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            line_val = None
        if line_val is None and hasattr(row, "index") and "MacD_Line" in row.index:
            line_val = row["MacD_Line"]
        return value_counts_as_indicator_data(line_val, "MacD_Line")
    for col in row.index:
        c = str(col)
        if c in ("Scrape_Status", "Scrape_Error"):
            continue
        if ind_name == "PCA":
            if c in ("PCA_Values", "PCA_Value", "PCA_Color") and value_counts_as_indicator_data(
                row[col], c
            ):
                return True
        elif c.startswith(ind_name):
            if value_counts_as_indicator_data(row[col], c):
                return True
        elif ind_name.lower() in c.lower() and value_counts_as_indicator_data(row[col], c):
            return True
    return False


def row_has_all_configured_indicators(row, indicators: Iterable[str]) -> bool:
    """True when every configured indicator has parseable data in the row."""
    inds = [str(i).strip() for i in indicators or [] if str(i).strip()]
    if not inds:
        return False
    ser = row if hasattr(row, "index") else pd.Series(row)
    return all(row_has_indicator_data(ser, ind) for ind in inds)


def row_skipped_for_dashboard(row, indicators: Iterable[str]) -> bool:
    """True when a CSV row must not supply technical data to the dashboard.

    Rows are used only when all configured indicators have parseable values.
    Legacy ``Scrape_Status=NO_DATA`` rows with full indicator payloads are kept;
    partial rows (including mid-scrape ``Scrape_Status=""`` saves) are skipped so
    an older complete row or per-indicator merge can supply missing fields.
    """
    if row is None:
        return True
    try:
        raw = row.get("Scrape_Status") if hasattr(row, "get") else None  # type: ignore[union-attr]
    except (AttributeError, TypeError):
        raw = None
    if raw is None and hasattr(row, "index") and "Scrape_Status" in row.index:
        raw = row["Scrape_Status"]
    st = str(raw or "").strip().upper()
    if st == "SKIPPED":
        return True
    return not row_has_all_configured_indicators(row, indicators)


def normalize_served_scrape_status(row: dict, indicators: Iterable[str]) -> dict:
    """Fix legacy rows marked NO_DATA despite having indicator columns populated."""
    out = dict(row)
    st = str(out.get("Scrape_Status") or "").strip().upper()
    if st != "NO_DATA":
        return out
    inds = [str(i).strip() for i in indicators or [] if str(i).strip()]
    if inds and all(row_has_indicator_data(pd.Series(out), ind) for ind in inds):
        out["Scrape_Status"] = "OK"
        out["Scrape_Error"] = ""
    return out


def row_interval_complete(row, indicators: Iterable[str]) -> bool:
    """Wiersz OK dla (ticker, interwa\u0142): SKIPPED = nie dotykamy; inaczej wszystkie wska\u017aniki."""
    if row is None:
        return False
    raw = row["Scrape_Status"] if "Scrape_Status" in row.index else None
    if raw is not None and pd.notna(raw):
        st = str(raw).strip().upper()
    else:
        st = ""
    if st == "SKIPPED":
        return True
    return row_has_all_configured_indicators(row, indicators)


def load_results_dataframe(path: Optional[str]) -> Optional[pd.DataFrame]:
    if not path or not os.path.exists(path):
        return None
    try:
        return pd.read_csv(path, encoding="utf-8", on_bad_lines="skip")
    except Exception as e:
        logger.warning("Nie mo\u017cna odczyta\u0107 CSV wynik\u00f3w (%s): %s", path, e)
        return None


def count_ticker_rows_in_csv(path: str, ticker: str) -> int:
    """Liczy dokładne wystąpienia tickera w jednym CSV (case-insensitive)."""
    df = load_results_dataframe(path)
    if df is None or df.empty or "Ticker" not in df.columns:
        return 0
    ticker_u = str(ticker or "").strip().upper()
    if not ticker_u:
        return 0
    mask = df["Ticker"].astype(str).str.strip().str.upper() == ticker_u
    return int(mask.sum())


def remove_ticker_rows_from_csv(path: str, ticker: str) -> Tuple[int, int]:
    """Usuwa dokładne wiersze tickera z CSV, zapisując atomowo.

    Zwraca ``(removed_rows, remaining_rows)``. Jeśli ticker nie występuje, plik
    nie jest przepisywany. Nagłówki pozostają w pliku nawet gdy usunięto
    wszystkie wiersze.
    """
    if not path or not os.path.exists(path):
        return 0, 0
    ticker_u = str(ticker or "").strip().upper()
    if not ticker_u:
        return 0, 0

    try:
        df = pd.read_csv(path, encoding="utf-8", on_bad_lines="skip")
    except Exception as e:
        logger.warning("Nie można odczytać CSV do usunięcia tickera (%s): %s", path, e)
        return 0, 0

    if df.empty or "Ticker" not in df.columns:
        return 0, int(len(df))

    mask = df["Ticker"].astype(str).str.strip().str.upper() == ticker_u
    removed = int(mask.sum())
    if removed <= 0:
        return 0, int(len(df))

    out = df.loc[~mask].copy()
    out = out[df.columns]
    dir_name = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".delete-ticker-", suffix=".csv", dir=dir_name)
    os.close(fd)
    try:
        out.to_csv(tmp_path, index=False, encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        finally:
            raise
    return removed, int(len(out))


def rename_ticker_rows_in_csv(path: str, old_ticker: str, new_ticker: str) -> Tuple[int, int]:
    """Przepisuje wiersze tickera w CSV na nowy symbol (case-insensitive).

    Gdy dla tego samego ``Interval`` istnieje już wiersz z ``new_ticker``,
    stary wiersz jest usuwany zamiast duplikować parę (ticker, interval).
    Zwraca ``(rows_affected, remaining_rows)``.
    """
    if not path or not os.path.exists(path):
        return 0, 0
    old_u = str(old_ticker or "").strip().upper()
    new_u = str(new_ticker or "").strip().upper()
    if not old_u or not new_u or old_u == new_u:
        return 0, 0

    try:
        df = pd.read_csv(path, encoding="utf-8", on_bad_lines="skip")
    except Exception as e:
        logger.warning(
            "Nie można odczytać CSV do rename tickera (%s): %s", path, e
        )
        return 0, 0

    if df.empty or "Ticker" not in df.columns:
        return 0, int(len(df))

    ticker_col = df["Ticker"].astype(str).str.strip().str.upper()
    old_mask = ticker_col == old_u
    affected = int(old_mask.sum())
    if affected <= 0:
        return 0, int(len(df))

    if "Interval" in df.columns:
        new_mask = ticker_col == new_u
        new_intervals = set(
            df.loc[new_mask, "Interval"].astype(str).str.strip().tolist()
        )
        drop_old = []
        rename_idx = []
        for idx in df.index[old_mask]:
            interval = str(df.at[idx, "Interval"] or "").strip()
            if interval and interval in new_intervals:
                drop_old.append(idx)
            else:
                rename_idx.append(idx)
        if drop_old:
            df = df.drop(index=drop_old)
            old_mask = df["Ticker"].astype(str).str.strip().str.upper() == old_u
        for idx in rename_idx:
            if idx in df.index:
                df.at[idx, "Ticker"] = new_u
    else:
        df.loc[old_mask, "Ticker"] = new_u

    dir_name = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".rename-ticker-", suffix=".csv", dir=dir_name)
    os.close(fd)
    try:
        df.to_csv(tmp_path, index=False, encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        finally:
            raise
    return affected, int(len(df))


def rename_fundamentals_ticker(
    old_ticker: str,
    new_ticker: str,
    *,
    path: Optional[str] = None,
) -> bool:
    """Przepisuje wiersz fundamentów ze starego tickera na nowy.

    Gdy docelowy ticker już istnieje, scalamy wartości ze starego wiersza tylko
    tam, gdzie nowy nie ma danych. Zwraca ``True`` gdy plik został zmieniony.
    """
    old_u = str(old_ticker or "").strip().upper()
    new_u = str(new_ticker or "").strip().upper()
    if not old_u or not new_u or old_u == new_u:
        return False

    target = path or FUNDAMENTALS_CSV
    df = load_fundamentals_dataframe(target)
    if df is None or df.empty or "Ticker" not in df.columns:
        return False

    ticker_col = df["Ticker"].astype(str).str.strip().str.upper()
    old_mask = ticker_col == old_u
    if not old_mask.any():
        return False

    new_mask = ticker_col == new_u
    old_row = df.loc[old_mask].iloc[0]
    changed = False

    if new_mask.any():
        new_idx = df.index[new_mask][0]
        for col in FUNDAMENTALS_COLUMNS:
            if col in ("Ticker",):
                continue
            new_val = df.at[new_idx, col] if col in df.columns else ""
            old_val = old_row[col] if col in old_row.index else ""
            new_empty = pd.isna(new_val) or str(new_val).strip() == ""
            old_has = not (pd.isna(old_val) or str(old_val).strip() == "")
            if new_empty and old_has:
                df.at[new_idx, col] = old_val
                changed = True
        df = df.loc[~old_mask].copy()
        changed = True
    else:
        df.loc[old_mask, "Ticker"] = new_u
        changed = True

    if not changed:
        return False

    extras = [c for c in df.columns if c not in FUNDAMENTALS_COLUMNS]
    df = df[FUNDAMENTALS_COLUMNS + extras]
    target_dir = os.path.dirname(os.path.abspath(target)) or "."
    os.makedirs(target_dir, exist_ok=True)
    df.to_csv(target, index=False, encoding="utf-8")
    return True


def get_row_for_ticker_interval(df, ticker: str, interval: str):
    if df is None or "Ticker" not in df.columns or "Interval" not in df.columns:
        return None
    m = (df["Ticker"].astype(str) == str(ticker)) & (df["Interval"].astype(str) == str(interval))
    sub = df.loc[m]
    if sub.empty:
        return None
    return sub.iloc[0]


def ticker_marked_skipped_for_day(df, ticker: str) -> bool:
    if df is None or "Ticker" not in df.columns or "Scrape_Status" not in df.columns:
        return False
    sub = df[df["Ticker"].astype(str) == str(ticker)]
    if sub.empty:
        return False
    return (sub["Scrape_Status"].astype(str).str.upper() == "SKIPPED").any()


def ticker_fully_done_in_csv(df, ticker: str, intervals, indicators) -> bool:
    """Ticker nie wymaga pomiaru: wszystkie interwa\u0142y kompletne albo jeden wiersz SKIPPED na dzi\u015b."""
    if df is None:
        return False
    if ticker_marked_skipped_for_day(df, ticker):
        return True
    for interval in intervals:
        row = get_row_for_ticker_interval(df, ticker, interval)
        if not row_interval_complete(row, indicators):
            return False
    return True


def _is_indicator_column(col: str) -> bool:
    c = str(col)
    if c in ("PCA_Values", "PCA_Value", "PCA_Color"):
        return True
    for prefix in ("HTS Panel_", "MacD_"):
        if c.startswith(prefix):
            return True
    return False


def _indicator_source_columns(ind_name: str, source: Mapping[str, Any]) -> List[str]:
    """Kolumny CSV nale\u017c\u0105ce do jednego wska\u017anika w ``source``."""
    ind_name = (ind_name or "").strip()
    if ind_name == "PCA":
        return [c for c in source if str(c) in ("PCA_Values", "PCA_Value", "PCA_Color")]
    if ind_name.lower() == "macd":
        return [c for c in source if str(c).startswith("MacD_")]
    prefix = f"{ind_name}_"
    return [c for c in source if str(c).startswith(prefix)]


def merge_indicator_into_row(
    target: dict, source: Mapping[str, Any], ind_name: str
) -> None:
    """Kopiuje kolumny jednego wska\u017anika z ``source`` do ``target`` (in-place)."""
    for col in _indicator_source_columns(ind_name, source):
        val = source.get(col)
        if val is None:
            continue
        try:
            if pd.notna(val) and cell_nonempty(val):
                target[col] = val
        except Exception:
            if cell_nonempty(val):
                target[col] = val


def merge_existing_row_into_row_data(
    row_data: dict, erow, *, skip_indicator_merge: bool = False
) -> None:
    if erow is None:
        return
    # Świeżo odczytane metadane z bieżącego runu (np. nazwa spółki)
    # nie mogą być nadpisane starym CSV.
    preserve_cols = {
        "Ticker",
        "Interval",
        "Company_Name",
        "Exchange",
        "Current_Price",
        "Scrape_Status",
        "Scrape_Error",
    }
    for col in erow.index:
        c = str(col)
        if c in preserve_cols:
            continue
        if skip_indicator_merge and _is_indicator_column(c):
            continue
        try:
            v = erow[col]
            if pd.notna(v):
                row_data[c] = v
        except Exception:
            pass


def tickers_with_no_data(df, indicators: Iterable[str]) -> List[str]:
    """Zwraca tickery wymagające odświeżenia trybem „Brak danych”.

    Kryteria (zgodne z bannerem UI):
    - przynajmniej jeden wiersz ma ``Scrape_Status=NO_DATA``; albo
    - wszystkie wiersze tickera nie mają danych dla wszystkich wskaźników.
    """
    if df is None or df.empty or "Ticker" not in df.columns:
        return []

    inds = [str(i).strip() for i in indicators or [] if str(i).strip()]
    out: List[str] = []
    grouped = df.groupby(df["Ticker"].astype(str), sort=False)

    for ticker, g in grouped:
        t = str(ticker).strip()
        if not t:
            continue

        if "Scrape_Status" in g.columns:
            sts = g["Scrape_Status"].astype(str).str.upper()
            if (sts == "SKIPPED").any():
                # SKIPPED ma osobny status w UI (nie „Brak danych”).
                continue
            if (sts == "NO_DATA").any():
                out.append(t)
                continue

        # Legacy fallback: „Brak danych” gdy każdy wiersz ma brak wszystkich wskaźników.
        all_rows_missing = True
        for _, row in g.iterrows():
            ser = row if isinstance(row, pd.Series) else pd.Series(row)
            row_has_any = any(row_has_indicator_data(ser, ind) for ind in inds)
            if row_has_any:
                all_rows_missing = False
                break
        if all_rows_missing:
            out.append(t)

    return out


def _row_field(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    if isinstance(row, pd.Series):
        return row.get(key)
    return getattr(row, key, None)


def ticker_rows_show_no_data(rows: Iterable[Any]) -> bool:
    """Czy wiersze jednego tickera kwalifikują się do banneru „Brak danych” (jak UI)."""
    items = list(rows)
    if not items:
        return True
    if any(
        str(_row_field(r, "Scrape_Status") or "").strip().upper() == "SKIPPED"
        for r in items
    ):
        return False
    if any(
        str(_row_field(r, "Scrape_Status") or "").strip().upper() == "NO_DATA"
        for r in items
    ):
        return True
    if all(_row_field(r, "All_Indicators_Missing") is True for r in items):
        return True
    return False


def ticker_rows_need_refresh(
    rows: Iterable[Any],
    *,
    latest_scrape_date: Optional[str] = None,
    ticker_in_latest_csv: Optional[bool] = None,
) -> bool:
    """Czy ticker wymaga od\u015bwie\u017cenia (brak danych, cz\u0119\u015bciowe wska\u017aniki lub stale)."""
    items = list(rows)
    if ticker_rows_show_no_data(items):
        return True
    if ticker_in_latest_csv is False:
        return True
    if latest_scrape_date:
        latest_day = str(latest_scrape_date).strip()[:10]
        if latest_day:
            for row in items:
                lr = str(_row_field(row, "Last_Refresh") or "").strip()[:10]
                if lr and lr < latest_day:
                    return True
    for row in items:
        miss = _row_field(row, "Missing_Indicators")
        if isinstance(miss, list) and miss:
            return True
    return False


def config_tickers_with_no_data(
    config_tickers: Iterable[str],
    flat_rows: Iterable[Any],
    *,
    latest_scrape_date: Optional[str] = None,
    tickers_in_latest_csv: Optional[Iterable[str]] = None,
    include_stale_and_partial: bool = True,
) -> List[str]:
    """Zwraca tickery z configu wymagaj\u0105ce od\u015bwie\u017cenia (brak / cz\u0119\u015bciowe / stale)."""
    grouped: Dict[str, List[Any]] = {}
    for row in flat_rows:
        ticker = str(_row_field(row, "Ticker") or "").strip()
        if ticker:
            grouped.setdefault(ticker, []).append(row)

    latest_set: Optional[set] = None
    if tickers_in_latest_csv is not None:
        latest_set = {
            str(t or "").strip().upper()
            for t in tickers_in_latest_csv
            if str(t or "").strip()
        }

    out: List[str] = []
    seen: set = set()
    for ticker in config_tickers:
        t = str(ticker or "").strip()
        if not t:
            continue
        t_u = t.upper()
        if t_u in seen:
            continue
        rows = grouped.get(t, [])
        if ticker_rows_show_no_data(rows):
            out.append(t)
            seen.add(t_u)
            continue
        if not include_stale_and_partial:
            continue
        in_latest = None if latest_set is None else t_u in latest_set
        if ticker_rows_need_refresh(
            rows,
            latest_scrape_date=latest_scrape_date,
            ticker_in_latest_csv=in_latest,
        ):
            out.append(t)
            seen.add(t_u)
    return out


def apply_final_scrape_status(row_data: dict, indicators: Iterable[str]) -> None:
    _ser = pd.Series(row_data)
    if all(row_has_indicator_data(_ser, ind) for ind in indicators):
        row_data["Scrape_Status"] = "OK"
        row_data["Scrape_Error"] = ""
    else:
        row_data["Scrape_Status"] = "NO_DATA"
        row_data["Scrape_Error"] = format_scrape_error_message(row_data, indicators)


def _default_indicator_error(ind_name: str) -> str:
    return "brak danych w legendzie"


def parse_indicator_errors_from_scrape_error(scrape_error: str) -> Dict[str, str]:
    """Parsuje ``Scrape_Error`` w formacie ``MacD: timeout legendy; PCA: …``."""
    out: Dict[str, str] = {}
    if not scrape_error:
        return out
    for part in str(scrape_error).split(";"):
        chunk = part.strip()
        if not chunk or ":" not in chunk:
            continue
        name, _, msg = chunk.partition(":")
        name = name.strip()
        msg = msg.strip()
        if name and msg:
            out[name] = msg
    return out


def build_indicator_errors(row_data: dict, indicators: Iterable[str]) -> Dict[str, str]:
    """Mapa ``{wskaźnik: powód}`` dla brakujących wskaźników w wierszu."""
    stored = row_data.get("_indicator_errors")
    parsed_stored: Dict[str, str] = {}
    if isinstance(stored, dict):
        parsed_stored = {
            str(k).strip(): str(v).strip()
            for k, v in stored.items()
            if str(k).strip() and str(v).strip()
        }
    elif isinstance(stored, str) and stored.strip():
        try:
            loaded = json.loads(stored)
            if isinstance(loaded, dict):
                parsed_stored = {
                    str(k).strip(): str(v).strip()
                    for k, v in loaded.items()
                    if str(k).strip() and str(v).strip()
                }
        except Exception:
            parsed_stored = {}

    from_scrape = parse_indicator_errors_from_scrape_error(
        str(row_data.get("Scrape_Error") or "")
    )

    out: Dict[str, str] = {}
    _ser = pd.Series(row_data)
    for ind in indicators:
        ind_name = str(ind or "").strip()
        if not ind_name:
            continue
        if row_has_indicator_data(_ser, ind_name):
            continue
        reason = parsed_stored.get(ind_name) or from_scrape.get(ind_name)
        if not reason:
            reason = _default_indicator_error(ind_name)
        out[ind_name] = reason
    return out


def format_scrape_error_message(row_data: dict, indicators: Iterable[str]) -> str:
    """Buduje czytelny ``Scrape_Error`` z per-wskaźnikowych powodów."""
    errors = build_indicator_errors(row_data, indicators)
    if errors:
        return "; ".join(f"{k}: {v}" for k, v in errors.items())
    return (
        "Brak danych wska\u017anik\u00f3w na wykresie "
        "(np. niewidoczna legenda, z\u0142y symbol lub b\u0142\u0105d parsowania)."
    )


def save_results_row(current_run_file: str, row_data: dict) -> None:
    """Upsert jednego wiersza po (Ticker, Interval). Zawsze z kolumnami Scrape_* w nag\u0142\u00f3wku."""
    row_data = dict(row_data)
    for c in CSV_META_COLUMNS:
        row_data.setdefault(c, "")

    if not os.path.exists(current_run_file):
        df_row = pd.DataFrame([row_data])
        df_row = df_row[order_result_columns(df_row.columns)]
        df_row.to_csv(current_run_file, index=False, mode="w", encoding="utf-8")
        return

    try:
        df_existing = pd.read_csv(current_run_file, encoding="utf-8", on_bad_lines="skip")
        df_existing = ensure_meta_columns(df_existing)
        df_existing = df_existing.astype(object)
        mask = (df_existing["Ticker"] == row_data["Ticker"]) & (
            df_existing["Interval"] == row_data["Interval"]
        )
        if mask.any():
            for col, val in row_data.items():
                if col not in df_existing.columns:
                    df_existing[col] = ""
                df_existing.loc[mask, col] = val
        else:
            df_new_row = pd.DataFrame([row_data])
            for c in df_existing.columns:
                if c not in df_new_row.columns:
                    df_new_row[c] = ""
            for c in df_new_row.columns:
                if c not in df_existing.columns:
                    df_existing[c] = ""
            df_existing = pd.concat([df_existing, df_new_row], ignore_index=True)
        df_existing = df_existing[order_result_columns(df_existing.columns)]
        df_existing.to_csv(current_run_file, index=False, encoding="utf-8")
    except Exception as e:
        logger.error(
            "B\u0142\u0105d bezpiecznego zapisu (update) pliku CSV: %s. Zapisuj\u0119 kopi\u0119 awaryjn\u0105 obok.",
            e,
        )
        recovery_path = current_run_file + ".recovered.csv"
        df_row = pd.DataFrame([row_data])
        df_row = df_row[order_result_columns(df_row.columns)]
        header = not os.path.exists(recovery_path)
        df_row.to_csv(
            recovery_path,
            index=False,
            mode="a" if not header else "w",
            header=header,
            encoding="utf-8",
        )


_PCA_COLOR_NAMES = {
    "czerwon": "rgb(239, 68, 68)",
    "niebiesk": "rgb(59, 130, 246)",
    "zielon": "rgb(16, 185, 129)",
    "pomara": "rgb(245, 158, 11)",
    "\u017c\u00f3\u0142": "rgb(245, 158, 11)",
    "zolt": "rgb(245, 158, 11)",
}


def parse_pca_number(raw: object) -> Tuple[Optional[float], Optional[str]]:
    """Wyci\u0105ga (warto\u015b\u0107, kolor) z surowego pola PCA_Values.

    Warto\u015b\u0107 parsowana po polsku: ``61,33``, ``1 234,56`` (z NBSP), ``-2.5``.
    Zwraca ``(None, None)`` gdy warto\u015b\u0107 nie jest liczb\u0105 (np. ``OK`` / pusto).
    Kolor zwracany jako CSS (``rgb(...)``/``rgba(...)``) albo ``None``.
    """
    if raw is None:
        return None, None
    try:
        if pd.isna(raw):
            return None, None
    except Exception:
        pass
    s = str(raw).strip()
    if not s or s.lower() in ("ok", "--", "\u2014", "-") or "brak danych" in s.lower():
        color_match = re.search(r"rgba?\([^)]+\)", s, re.IGNORECASE)
        return None, color_match.group(0) if color_match else None

    color: Optional[str] = None
    rgb_match = re.search(r"rgba?\([^)]+\)", s, re.IGNORECASE)
    if rgb_match:
        color = rgb_match.group(0)

    value_part = s
    paren_match = re.match(r"^(.*?)\s*\(", s)
    if paren_match:
        value_part = paren_match.group(1)

    if color is None:
        paren_inner = re.search(r"\(([^)]+)\)", s)
        if paren_inner:
            inner_low = paren_inner.group(1).lower()
            for needle, css in _PCA_COLOR_NAMES.items():
                if needle in inner_low:
                    color = css
                    break

    vp = value_part.strip().replace("\u00a0", " ").replace("\u2212", "-")
    vp = vp.replace(" ", "")
    if not vp:
        return None, color
    if "," in vp and "." in vp:
        vp = vp.replace(".", "").replace(",", ".")
    elif "," in vp:
        vp = vp.replace(",", ".")
    try:
        return float(vp), color
    except (TypeError, ValueError):
        return None, color


FUNDAMENTALS_COLUMNS: List[str] = [
    "Ticker",
    "Fund_PE",
    "Fund_PB",
    "Fund_EV_EBITDA",
    "Fund_ROE",
    "Fund_NetMargin",
    "Fund_DE",
    "Fund_FCF",
    "Fund_DividendYield",
    "Fund_DividendRate",
    "Fund_Sector",
    "Fund_Industry",
    "Fund_PE_vs_Sector",
    "Fund_Source",
    "Fund_Updated_At",
]


def fundamentals_csv_path(results_dir: str = "results") -> str:
    """Ścieżka do CSV z fundamentami w danym katalogu wyników."""
    return os.path.join(results_dir, "fundamentals.csv")


FUNDAMENTALS_CSV = fundamentals_csv_path()

_fundamentals_read_notices: set = set()


def _empty_fundamentals_dataframe() -> pd.DataFrame:
    return pd.DataFrame(columns=FUNDAMENTALS_COLUMNS)


def _log_fundamentals_read_once(path: str, message: str) -> None:
    if path in _fundamentals_read_notices:
        return
    _fundamentals_read_notices.add(path)
    logger.debug("Fundamentals CSV (%s): %s", path, message)


def _ensure_fundamentals_csv_header(path: str) -> None:
    """Tworzy plik z samym nagłówkiem gdy brakuje lub jest pusty."""
    if path and os.path.exists(path) and os.path.getsize(path) > 0:
        return
    target_dir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(target_dir, exist_ok=True)
    _empty_fundamentals_dataframe().to_csv(path, index=False, encoding="utf-8")


def _normalize_cell(value) -> str:
    """Konwertuje wartość komórki do stringu nadającego się do CSV (None → "")."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        # Bez NaN-ów (już wyłapane wyżej). Tnij końcowe zera dla czytelności.
        s = ("%g" % value) if value == int(value) is False else str(value)
        if s.endswith(".0"):
            s = s[:-2]
        return s
    return str(value)


def load_fundamentals_dataframe(
    path: Optional[str] = None,
) -> pd.DataFrame:
    """Wczytuje plik z fundamentami; gdy nie istnieje, zwraca pusty DataFrame z kolumnami."""
    p = path or FUNDAMENTALS_CSV
    if not p or not os.path.exists(p):
        return _empty_fundamentals_dataframe()
    try:
        if os.path.getsize(p) == 0:
            _log_fundamentals_read_once(p, "pusty plik — zwracam pusty DataFrame")
            return _empty_fundamentals_dataframe()
        df = pd.read_csv(p, encoding="utf-8", on_bad_lines="skip")
    except pd.errors.EmptyDataError:
        _log_fundamentals_read_once(p, "brak kolumn (pusty/nagłówek) — zwracam pusty DataFrame")
        return _empty_fundamentals_dataframe()
    except Exception as e:  # noqa: BLE001
        _log_fundamentals_read_once(p, f"nie można odczytać: {e}")
        return _empty_fundamentals_dataframe()
    if df.empty:
        _log_fundamentals_read_once(p, "brak wierszy danych — zwracam pusty DataFrame z kolumnami")
        return _empty_fundamentals_dataframe()
    for col in FUNDAMENTALS_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    extras = [c for c in df.columns if c not in FUNDAMENTALS_COLUMNS]
    return df[FUNDAMENTALS_COLUMNS + extras]


def save_fundamentals_row(
    row: dict,
    *,
    path: Optional[str] = None,
) -> None:
    """Upsert pojedynczego wiersza fundamentów (klucz: ``Ticker``).

    Tworzy plik z nagłówkami przy pierwszym wywołaniu. Wartości ``None`` są
    zapisywane jako puste komórki (a nie ``"nan"``).
    """
    if not isinstance(row, dict):
        raise TypeError("row musi być słownikiem")
    ticker = str(row.get("Ticker") or "").strip()
    if not ticker:
        raise ValueError("row['Ticker'] jest wymagane")

    target = path or FUNDAMENTALS_CSV
    target_dir = os.path.dirname(os.path.abspath(target)) or "."
    os.makedirs(target_dir, exist_ok=True)
    _ensure_fundamentals_csv_header(target)

    df = load_fundamentals_dataframe(target)
    if df is None or df.empty:
        df = pd.DataFrame(columns=FUNDAMENTALS_COLUMNS)

    payload = {col: _normalize_cell(row.get(col, "")) for col in FUNDAMENTALS_COLUMNS}
    payload["Ticker"] = ticker

    # Trzymamy wszystkie kolumny jako object — fundamentale często mają mixed
    # typy (liczba, "N/A", "1.2B"), a pandas potrafi zrobić upcast/błąd dtype
    # przy upsercie ``df.loc[mask, col] = "13"`` do kolumny float64.
    df = df.astype(object) if not df.empty else df

    if "Ticker" not in df.columns:
        df["Ticker"] = ""
    mask = df["Ticker"].astype(str).str.strip() == ticker
    if mask.any():
        for col, val in payload.items():
            if col not in df.columns:
                df[col] = ""
            df.loc[mask, col] = val
    else:
        new_row_df = pd.DataFrame([payload])
        for c in df.columns:
            if c not in new_row_df.columns:
                new_row_df[c] = ""
        for c in new_row_df.columns:
            if c not in df.columns:
                df[c] = ""
        df = pd.concat([df, new_row_df], ignore_index=True)

    extras = [c for c in df.columns if c not in FUNDAMENTALS_COLUMNS]
    df = df[FUNDAMENTALS_COLUMNS + extras]
    df.to_csv(target, index=False, encoding="utf-8")


def _parse_numeric_fund(v) -> object:
    """Próba konwersji wartości z CSV do float. Zwraca raw string gdy się nie da."""
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip() if v is not None else ""
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return s


def get_fundamentals_for_ticker(
    ticker: str,
    *,
    path: Optional[str] = None,
) -> Optional[dict]:
    """Zwraca słownik fundamentów dla tickera albo ``None`` jeśli brak."""
    t = str(ticker or "").strip()
    if not t:
        return None
    df = load_fundamentals_dataframe(path)
    if df is None or df.empty or "Ticker" not in df.columns:
        return None
    mask = df["Ticker"].astype(str).str.strip() == t
    if not mask.any():
        mask = df["Ticker"].astype(str).str.strip().str.upper() == t.upper()
    if not mask.any():
        return None
    row = df.loc[mask].iloc[0]
    out: dict = {}
    for col in FUNDAMENTALS_COLUMNS:
        if col not in row.index:
            out[col] = None
            continue
        if col in ("Ticker", "Fund_Source", "Fund_Updated_At"):
            try:
                if pd.isna(row[col]):
                    out[col] = None
                    continue
            except (TypeError, ValueError):
                pass
            s = str(row[col]).strip()
            out[col] = s if s != "" else None
        else:
            out[col] = _parse_numeric_fund(row[col])
    return out


def record_skipped_ticker(current_run_file: str, ticker: str, reason: str) -> None:
    """Zapisuje jeden wiersz w CSV: ticker pomini\u0119ty (nie znaleziony / b\u0142\u0119dny symbol)."""
    row_base = {
        "Ticker": ticker,
        "Company_Name": "\u2014",
        "Exchange": "",
        "Current_Price": "",
        "Interval": "-",
        "Scrape_Status": "SKIPPED",
        "Scrape_Error": reason,
    }
    if os.path.exists(current_run_file):
        try:
            df = pd.read_csv(current_run_file, encoding="utf-8", on_bad_lines="skip")
        except Exception as e:
            logger.warning(
                "Nie mo\u017cna odczyta\u0107 CSV przy SKIPPED %s (%s) \u2014 tworz\u0119 nowy.",
                ticker,
                e,
            )
            df = pd.DataFrame()
        df = ensure_meta_columns(df) if not df.empty else pd.DataFrame(columns=CSV_META_COLUMNS)
        if "Scrape_Status" in df.columns and not df.empty:
            mask_skip = (df["Ticker"].astype(str) == ticker) & (
                df["Scrape_Status"].astype(str) == "SKIPPED"
            )
            df = df[~mask_skip]
        for col in df.columns:
            row_base.setdefault(col, "")
        ordered = {c: row_base.get(c, "") for c in df.columns}
        out = pd.concat([df, pd.DataFrame([ordered])], ignore_index=True)
    else:
        out = pd.DataFrame([row_base])
    out = ensure_meta_columns(out)
    out.to_csv(current_run_file, index=False, encoding="utf-8")
    logger.info("[CSV] Zapisano pomini\u0119ty ticker %s: %s", ticker, reason)
