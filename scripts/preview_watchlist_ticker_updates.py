#!/usr/bin/env python3
"""Preview TradingView ticker updates for a Portfel_Watchlist CSV.

Read-only: this script does not modify ``scraper_config.json``. It creates a
CSV report with suggested TradingView symbols based on watchlist company names.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from company_names import search_symbols  # noqa: E402


DEFAULT_WATCHLIST = os.path.join(ROOT, "Portfel_Watchlist_05122026.csv")
DEFAULT_CONFIG = os.path.join(ROOT, "scraper_config.json")
DEFAULT_OUTPUT = os.path.join(ROOT, "data", "watchlist_ticker_update_preview.csv")

REPORT_COLUMNS = [
    "Name",
    "Watchlist_Symbol",
    "Current_Config_Ticker",
    "Config_Match_Method",
    "Suggested_TV_Ticker",
    "Exchange",
    "Description",
    "Confidence",
    "Score",
    "Reason",
    "Action",
    "Alternatives",
]

RIC_EXCHANGE_HINTS = {
    "WA": ["GPW"],
    "O": ["NASDAQ"],
    "OQ": ["NASDAQ"],
    "K": ["NYSE", "AMEX", "ARCA"],
    "N": ["NYSE"],
    "A": ["AMEX"],
    "DE": ["XETR", "GETTEX", "FWB"],
    "F": ["FWB", "GETTEX", "XETR"],
    "BE": ["BER"],
    "DU": ["DUS"],
    "HM": ["HAM"],
    "SG": ["SWB"],
    "KS": ["KRX"],
    "KQ": ["KOSDAQ"],
    "HK": ["HKEX"],
    "SI": ["SGX"],
    "AX": ["ASX"],
    "L": ["LSE"],
    "TO": ["TSX"],
    "V": ["TSXV"],
    "PA": ["EURONEXT"],
    "AS": ["EURONEXT"],
    "MI": ["MIL"],
    "MC": ["BME"],
    "SW": ["SIX"],
}

BARE_US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA", "OTC", "OTCQX", "OTCQB"}

NON_EQUITY_TERMS = {
    "FUTURES",
    "FUTURE",
    "FRN",
    "BOND",
    "NOTE",
    "NOTES",
    "TURBO",
    "CERTIFICATE",
    "WARRANT",
}

STOP_WORDS = {
    "a",
    "ab",
    "adr",
    "ag",
    "anonima",
    "class",
    "co",
    "company",
    "corp",
    "corporation",
    "etf",
    "fund",
    "group",
    "holdings",
    "inc",
    "index",
    "ltd",
    "nv",
    "plc",
    "sa",
    "shares",
    "spa",
    "the",
    "trust",
    "ucits",
    "usd",
}


def normalize_words(text: str) -> str:
    text = (text or "").replace("\u00a0", " ")
    text = re.sub(r"[^A-Za-z0-9]+", " ", text.lower())
    words = [w for w in text.split() if w and w not in STOP_WORDS]
    return " ".join(words)


def compact_symbol(sym: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (sym or "").upper())


def watchlist_base_symbol(sym: str) -> str:
    s = (sym or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    if ":" in s:
        s = s.split(":", 1)[-1]
    return s


def watchlist_exchange_hints(sym: str) -> List[str]:
    s = (sym or "").strip().upper()
    if ":" in s:
        return [s.split(":", 1)[0]]
    if "." not in s:
        return []
    suffix = s.rsplit(".", 1)[-1]
    return list(RIC_EXCHANGE_HINTS.get(suffix, []))


def config_match_variants(sym: str) -> set:
    s = (sym or "").strip().upper()
    no_exch = s.split(":", 1)[-1]
    base = watchlist_base_symbol(s)
    vals = {
        s,
        no_exch,
        base,
        compact_symbol(s),
        compact_symbol(no_exch),
        compact_symbol(base),
    }
    return {v for v in vals if v}


def find_current_config_ticker(
    watchlist_symbol: str,
    config_tickers: List[str],
    row_index: int,
) -> Tuple[str, str]:
    upper = [str(t or "").strip().upper() for t in config_tickers]
    wl_u = (watchlist_symbol or "").strip().upper()
    if wl_u in upper:
        return config_tickers[upper.index(wl_u)], "exact"

    variants = config_match_variants(wl_u)
    matches = [
        i
        for i, cfg in enumerate(upper)
        if variants.intersection(config_match_variants(cfg))
    ]
    if len(matches) == 1:
        return config_tickers[matches[0]], "variant"
    return "", ""


def format_tv_ticker(symbol: str, exchange: str) -> str:
    sym = (symbol or "").strip().upper()
    exch = (exchange or "").strip().upper()
    if not sym:
        return ""
    if exch and exch not in BARE_US_EXCHANGES:
        return f"{exch}:{sym}"
    return sym


def score_candidate(
    name: str,
    watchlist_symbol: str,
    candidate: Dict[str, Any],
) -> Tuple[float, List[str], float]:
    descr = str(candidate.get("description") or "")
    symbol = str(candidate.get("symbol") or "").upper()
    exchange = str(candidate.get("exchange") or "").upper()
    typ = str(candidate.get("type") or "").lower()

    name_norm = normalize_words(name)
    descr_norm = normalize_words(descr)
    name_ratio = SequenceMatcher(None, name_norm, descr_norm).ratio() if name_norm and descr_norm else 0.0
    score = name_ratio * 70.0
    reasons = [f"name={name_ratio:.2f}"]

    hints = watchlist_exchange_hints(watchlist_symbol)
    if exchange and exchange in hints:
        score += 20
        reasons.append(f"exchange={exchange}")

    wl_base = compact_symbol(watchlist_base_symbol(watchlist_symbol))
    cand_compact = compact_symbol(symbol)
    if wl_base and cand_compact:
        if wl_base == cand_compact:
            score += 25
            reasons.append("symbol=exact")
        elif wl_base.startswith(cand_compact) or cand_compact.startswith(wl_base):
            score += 10
            reasons.append("symbol=prefix")

    if typ in {"stock", "fund", "etf", "dr"}:
        score += 3
        reasons.append(f"type={typ}")

    descr_upper = descr.upper()
    sym_upper = symbol.upper()
    if any(term in descr_upper or term in sym_upper for term in NON_EQUITY_TERMS):
        score -= 35
        reasons.append("non_equity_penalty")

    return score, reasons, name_ratio


def choose_suggestion(
    name: str,
    watchlist_symbol: str,
    candidates: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    ranked = []
    for cand in candidates:
        score, reasons, name_ratio = score_candidate(name, watchlist_symbol, cand)
        ranked.append((score, name_ratio, reasons, cand))
    ranked.sort(key=lambda x: x[0], reverse=True)

    if not ranked:
        return {
            "candidate": None,
            "confidence": "no_match",
            "score": 0,
            "reason": "no candidates",
            "alternatives": "",
        }

    best_score, best_ratio, reasons, best = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    margin = best_score - second_score
    if best_score >= 80 and best_ratio >= 0.55 and (margin >= 8 or len(ranked) == 1):
        confidence = "high_confidence"
    else:
        confidence = "needs_review"

    alternatives = []
    for score, _ratio, _reasons, cand in ranked[1:4]:
        tv = format_tv_ticker(cand.get("symbol", ""), cand.get("exchange", ""))
        desc = cand.get("description", "")
        alternatives.append(f"{tv} ({score:.1f}; {desc})")

    return {
        "candidate": best,
        "confidence": confidence,
        "score": round(best_score, 1),
        "reason": "; ".join(reasons) + (f"; margin={margin:.1f}" if len(ranked) > 1 else ""),
        "alternatives": " | ".join(alternatives),
    }


def dedupe_candidates(candidates: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    seen = set()
    for cand in candidates:
        key = (
            str(cand.get("exchange") or "").upper(),
            str(cand.get("symbol") or "").upper(),
            str(cand.get("description") or "").upper(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out


def search_candidates_for_row(
    name: str,
    watchlist_symbol: str,
    search_fn=search_symbols,
) -> List[Dict[str, Any]]:
    """Search by company name and by watchlist base symbol.

    Name search catches symbol changes (`PAS1.WA` -> `GPW:PAS`), while base
    symbol search avoids futures/bond false positives when the exact equity
    symbol exists (`PKO.WA` -> `GPW:PKO`, not `GPW:FPKO`).
    """
    candidates = list(search_fn(name))
    base = watchlist_base_symbol(watchlist_symbol)
    if base and base.upper() not in {name.upper(), compact_symbol(name)}:
        candidates.extend(search_fn(base))
    return dedupe_candidates(candidates)


def action_for(current_config: str, suggested: str, confidence: str) -> str:
    if confidence == "no_match":
        return "no_match"
    if not suggested:
        return "no_match"
    if current_config and current_config.upper() == suggested.upper():
        return "keep"
    if confidence == "high_confidence":
        return "change_config" if current_config else "add_or_review"
    return "needs_review"


def read_watchlist(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [
            {"Name": (row.get("Name") or "").strip(), "Symbol": (row.get("Symbol") or "").strip()}
            for row in csv.DictReader(f)
            if (row.get("Name") or "").strip() and (row.get("Symbol") or "").strip()
        ]


def read_config_tickers(path: str) -> List[str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("tickers") or [])


def build_preview_rows(
    watchlist_rows: List[Dict[str, str]],
    config_tickers: List[str],
    *,
    search_fn=search_symbols,
    max_rows: Optional[int] = None,
) -> List[Dict[str, Any]]:
    out = []
    rows = watchlist_rows[:max_rows] if max_rows else watchlist_rows
    for idx, row in enumerate(rows):
        name = row["Name"]
        wl_symbol = row["Symbol"]
        current_config, match_method = find_current_config_ticker(wl_symbol, config_tickers, idx)
        candidates = search_candidates_for_row(name, wl_symbol, search_fn=search_fn)
        suggestion = choose_suggestion(name, wl_symbol, candidates)
        cand = suggestion["candidate"] or {}
        suggested = format_tv_ticker(cand.get("symbol", ""), cand.get("exchange", ""))
        if not current_config and suggested:
            current_config, match_method = find_current_config_ticker(
                suggested,
                config_tickers,
                idx,
            )
            if match_method:
                match_method = f"suggested_{match_method}"
        out.append(
            {
                "Name": name,
                "Watchlist_Symbol": wl_symbol,
                "Current_Config_Ticker": current_config,
                "Config_Match_Method": match_method,
                "Suggested_TV_Ticker": suggested,
                "Exchange": cand.get("exchange", ""),
                "Description": cand.get("description", ""),
                "Confidence": suggestion["confidence"],
                "Score": suggestion["score"],
                "Reason": suggestion["reason"],
                "Action": action_for(current_config, suggested, suggestion["confidence"]),
                "Alternatives": suggestion["alternatives"],
            }
        )
    return out


def write_report(path: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        key = str(row.get("Confidence") or "unknown")
        out[key] = out.get(key, 0) + 1
    for row in rows:
        key = f"action:{row.get('Action') or 'unknown'}"
        out[key] = out.get(key, 0) + 1
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--watchlist", default=DEFAULT_WATCHLIST)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--max-rows", type=int, default=0, help="Debug: process only first N rows")
    args = ap.parse_args(argv)

    watchlist_rows = read_watchlist(args.watchlist)
    config_tickers = read_config_tickers(args.config)
    rows = build_preview_rows(
        watchlist_rows,
        config_tickers,
        max_rows=args.max_rows or None,
    )
    write_report(args.output, rows)

    summary = summarize(rows)
    print(f"Wrote {len(rows)} rows to {args.output}")
    for key in sorted(summary):
        print(f"{key}: {summary[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
