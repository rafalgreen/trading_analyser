"""Composite verdict (Kup / Obserwuj / Unikaj) from fundamentals, 1W scoring, D/W/M consensus."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fundamentals import FUND_KEYS
from signal_strategies import strategy_scoring


SIGNAL_SCORES = {
    "strong buy": 2,
    "buy": 1,
    "neutral": 0,
    "sell": -1,
    "strong sell": -2,
}

WEIGHT_FUND = 0.40
WEIGHT_TECH = 0.40
WEIGHT_CONSENSUS = 0.20

VERDICT_KUP = "kup"
VERDICT_OBSERWUJ = "obserwuj"
VERDICT_UNIKAJ = "unikaj"


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return None
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def signal_to_numeric(signal: str) -> Optional[float]:
    s = (signal or "").strip().lower()
    if not s:
        return None
    return SIGNAL_SCORES.get(s)


def signal_to_layer_score(signal: str) -> float:
    """Map signal (−2..+2) to layer score (−100..+100)."""
    n = signal_to_numeric(signal)
    if n is None:
        return 0.0
    return n * 50.0


def _fund_subscore_pe(pe: float) -> float:
    if pe <= 15:
        return 100.0
    if pe <= 25:
        return 100.0 - (pe - 15) * 5.0
    if pe <= 40:
        return max(0.0, 50.0 - (pe - 25) * (50.0 / 15.0))
    return 0.0


def _fund_subscore_pb(pb: float) -> float:
    if pb <= 1:
        return 100.0
    if pb <= 3:
        return max(0.0, 100.0 - (pb - 1) * 50.0)
    return 0.0


def _fund_subscore_ev(ev: float) -> float:
    if ev <= 10:
        return 100.0
    if ev <= 12:
        return 90.0
    if ev <= 20:
        return max(0.0, 90.0 - (ev - 12) * (90.0 / 8.0))
    return 0.0


def _fund_subscore_roe(roe: float) -> float:
    if roe >= 0.15:
        return 100.0
    if roe >= 0.10:
        return 60.0 + (roe - 0.10) * (40.0 / 0.05)
    if roe >= 0:
        return max(0.0, roe / 0.10 * 60.0)
    return 0.0


def _fund_subscore_margin(margin: float) -> float:
    if margin >= 0.20:
        return 100.0
    if margin >= 0.10:
        return 60.0 + (margin - 0.10) * (40.0 / 0.10)
    if margin >= 0:
        return max(0.0, margin / 0.10 * 60.0)
    return 0.0


def _fund_subscore_de(de: float) -> float:
    """D/E from yfinance is percent (100 ≈ ratio 1.0)."""
    if de <= 100:
        return 100.0
    if de <= 200:
        return max(0.0, 100.0 - (de - 100.0))
    return 0.0


def _fund_subscore_fcf(fcf: float) -> float:
    if fcf > 0:
        return 100.0
    if fcf == 0:
        return 50.0
    return 0.0


def _fundamentals_subscores(fundamentals: Dict[str, Any]) -> List[float]:
    scores: List[float] = []
    pe = _num(fundamentals.get("Fund_PE"))
    if pe is not None and pe > 0:
        scores.append(_fund_subscore_pe(pe))
    pb = _num(fundamentals.get("Fund_PB"))
    if pb is not None and pb > 0:
        scores.append(_fund_subscore_pb(pb))
    ev = _num(fundamentals.get("Fund_EV_EBITDA"))
    if ev is not None and ev > 0:
        scores.append(_fund_subscore_ev(ev))
    roe = _num(fundamentals.get("Fund_ROE"))
    if roe is not None:
        scores.append(_fund_subscore_roe(roe))
    margin = _num(fundamentals.get("Fund_NetMargin"))
    if margin is not None:
        scores.append(_fund_subscore_margin(margin))
    de = _num(fundamentals.get("Fund_DE"))
    if de is not None and de >= 0:
        scores.append(_fund_subscore_de(de))
    fcf = _num(fundamentals.get("Fund_FCF"))
    if fcf is not None:
        scores.append(_fund_subscore_fcf(fcf))
    return scores


def _is_crypto_fundamentals(fundamentals: Dict[str, Any]) -> bool:
    return all(_num(fundamentals.get(k)) is None for k in FUND_KEYS)


def compute_fundamentals_layer_score(fundamentals: Dict[str, Any]) -> float:
    """Fund layer in −100..+100; crypto / no data → neutral 0."""
    if _is_crypto_fundamentals(fundamentals):
        return 0.0
    subscores = _fundamentals_subscores(fundamentals)
    if not subscores:
        return 0.0
    avg = sum(subscores) / len(subscores)
    return (avg - 50.0) * 2.0


def compute_technical_layer_score(interval_rows: Dict[str, Dict[str, Any]]) -> float:
    row = interval_rows.get("1W") or interval_rows.get("1w")
    if not row:
        return 0.0
    return signal_to_layer_score(strategy_scoring(row))


def compute_consensus_layer_score(interval_rows: Dict[str, Dict[str, Any]]) -> float:
    nums: List[float] = []
    for iv in ("1D", "1W", "1M"):
        row = interval_rows.get(iv)
        if not row:
            continue
        n = signal_to_numeric(strategy_scoring(row))
        if n is not None:
            nums.append(n)
    if not nums:
        return 0.0
    return (sum(nums) / len(nums)) * 50.0


def check_red_flags(fundamentals: Dict[str, Any]) -> List[str]:
    flags: List[str] = []
    roe = _num(fundamentals.get("Fund_ROE"))
    fcf = _num(fundamentals.get("Fund_FCF"))
    de = _num(fundamentals.get("Fund_DE"))
    if roe is not None and roe < 0:
        flags.append("ROE<0")
    if fcf is not None and fcf < 0 and de is not None and de > 200:
        flags.append("FCF<0&D/E>200")
    return flags


def compute_verdict(
    score: float, flags: List[str]
) -> str:
    if flags or score <= -20:
        return VERDICT_UNIKAJ
    if score >= 40:
        return VERDICT_KUP
    return VERDICT_OBSERWUJ


def compute_composite_verdict(
    ticker: str,
    interval_rows: Dict[str, Dict[str, Any]],
    fundamentals: Dict[str, Any],
) -> Dict[str, Any]:
    """Composite Kup / Obserwuj / Unikaj for one ticker."""
    del ticker  # reserved for logging / future use

    fund = compute_fundamentals_layer_score(fundamentals)
    tech = compute_technical_layer_score(interval_rows)
    consensus = compute_consensus_layer_score(interval_rows)
    score = round(WEIGHT_FUND * fund + WEIGHT_TECH * tech + WEIGHT_CONSENSUS * consensus, 2)
    flags = check_red_flags(fundamentals)
    verdict = compute_verdict(score, flags)

    return {
        "verdict": verdict,
        "score": score,
        "breakdown": {
            "fund": round(fund, 2),
            "tech": round(tech, 2),
            "consensus": round(consensus, 2),
        },
        "flags": flags,
    }
