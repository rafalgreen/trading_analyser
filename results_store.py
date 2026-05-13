"""
Wsp\u00f3lny modu\u0142 I/O dla plik\u00f3w wynik\u00f3w scrapera.

Zawiera sta\u0142e kolumn meta (`CSV_META_COLUMNS`), upsert po kluczu
(`Ticker`, `Interval`), helpery odczytu oraz predykaty okre\u015blaj\u0105ce czy
wiersz jest kompletny w \u015bwietle konfiguracji wska\u017anik\u00f3w. U\u017cywane przez
``tv_scraper.py``, ``app.py`` oraz ``scripts/repair_results_csv.py``.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Iterable, List, Optional, Tuple

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


def row_interval_complete(row, indicators: Iterable[str]) -> bool:
    """Wiersz OK dla (ticker, interwa\u0142): SKIPPED = nie dotykamy; OK = wszystkie wska\u017aniki z konfiguracji."""
    if row is None:
        return False
    raw = row["Scrape_Status"] if "Scrape_Status" in row.index else None
    if raw is not None and pd.notna(raw):
        st = str(raw).strip().upper()
    else:
        st = ""
    if st == "SKIPPED":
        return True
    if st and st not in ("OK",):
        return False
    for ind in indicators:
        if not row_has_indicator_data(row, ind):
            return False
    return True


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


def merge_existing_row_into_row_data(row_data: dict, erow) -> None:
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
        if str(col) in preserve_cols:
            continue
        try:
            v = erow[col]
            if pd.notna(v):
                row_data[str(col)] = v
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


def apply_final_scrape_status(row_data: dict, indicators: Iterable[str]) -> None:
    _ser = pd.Series(row_data)
    if all(row_has_indicator_data(_ser, ind) for ind in indicators):
        row_data["Scrape_Status"] = "OK"
        row_data["Scrape_Error"] = ""
    else:
        row_data["Scrape_Status"] = "NO_DATA"
        row_data["Scrape_Error"] = (
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
