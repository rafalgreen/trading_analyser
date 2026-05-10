"""Strategie liczenia sygnału kup/sprzedaj z 3 wskaźników (PCA, HTS Panel, MacD).

Każda strategia konsumuje wiersz wyników (jako ``dict``) i zwraca jedną z pięciu
kategorii: ``"strong buy"``, ``"buy"``, ``"neutral"``, ``"sell"``, ``"strong sell"``.
Pusty string oznacza brak sygnału (np. niekompletny wiersz).

Identyfikatory strategii i ich semantyka są spójne z UI (filtr/badże).
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from results_store import parse_pca_number


SIGNAL_STRONG_BUY = "strong buy"
SIGNAL_BUY = "buy"
SIGNAL_NEUTRAL = "neutral"
SIGNAL_SELL = "sell"
SIGNAL_STRONG_SELL = "strong sell"

ALL_SIGNALS = (
    SIGNAL_STRONG_BUY,
    SIGNAL_BUY,
    SIGNAL_NEUTRAL,
    SIGNAL_SELL,
    SIGNAL_STRONG_SELL,
)


def _trend(value: object) -> Optional[str]:
    s = str(value or "").strip().lower()
    if not s:
        return None
    if "wzrost" in s or "bull" in s or "up" in s:
        return "up"
    if "spadk" in s or "bear" in s or "down" in s:
        return "down"
    return None


def _cross(value: object) -> Optional[str]:
    s = str(value or "").strip().upper()
    if not s:
        return None
    if "BULL CROSS" in s or "BULL" in s:
        return "up"
    if "BEAR CROSS" in s or "BEAR" in s:
        return "down"
    return None


def _pca_value(row: Dict[str, object]) -> Optional[float]:
    candidates = (row.get("PCA_Value"), row.get("PCA_Values"))
    for raw in candidates:
        if raw is None or str(raw).strip() == "":
            continue
        v, _ = parse_pca_number(raw)
        if v is not None:
            return float(v)
    return None


def _row_inputs(row: Dict[str, object]) -> Dict[str, Optional[object]]:
    return {
        "hts_trend": _trend(row.get("HTS Panel_Trend")),
        "macd_trend": _trend(row.get("MacD_Trend")),
        "hts_cross": _cross(row.get("HTS Panel_Cross")),
        "macd_cross": _cross(row.get("MacD_Cross")),
        "pca": _pca_value(row),
    }


def _bucket_score(score: float) -> str:
    """Przekłada wynik [-3..+3] na 5 koszyków."""
    if score >= 2:
        return SIGNAL_STRONG_BUY
    if score >= 1:
        return SIGNAL_BUY
    if score <= -2:
        return SIGNAL_STRONG_SELL
    if score <= -1:
        return SIGNAL_SELL
    return SIGNAL_NEUTRAL


def strategy_trend_only(row: Dict[str, object]) -> str:
    """2× Wzrostowy + PCA≥60 → Strong Buy; 2× Wzrostowy → Buy; 2× Spadkowy + PCA≤40 → Strong Sell; 2× Spadkowy → Sell; reszta → Neutral."""
    i = _row_inputs(row)
    h, m, p = i["hts_trend"], i["macd_trend"], i["pca"]
    if h is None and m is None:
        return ""
    ups = sum(1 for x in (h, m) if x == "up")
    downs = sum(1 for x in (h, m) if x == "down")
    if ups >= 2:
        if p is not None and p >= 60:
            return SIGNAL_STRONG_BUY
        return SIGNAL_BUY
    if downs >= 2:
        if p is not None and p <= 40:
            return SIGNAL_STRONG_SELL
        return SIGNAL_SELL
    return SIGNAL_NEUTRAL


def strategy_cross_priority(row: Dict[str, object]) -> str:
    """Crossy mają priorytet; PCA jako tie-breaker (≥60 buy, ≤40 sell)."""
    i = _row_inputs(row)
    cross_up = sum(1 for x in (i["hts_cross"], i["macd_cross"]) if x == "up")
    cross_down = sum(1 for x in (i["hts_cross"], i["macd_cross"]) if x == "down")
    p = i["pca"]
    if cross_up >= 2:
        return SIGNAL_STRONG_BUY
    if cross_down >= 2:
        return SIGNAL_STRONG_SELL
    if cross_up == 1 and cross_down == 0:
        if p is not None and p >= 60:
            return SIGNAL_STRONG_BUY
        return SIGNAL_BUY
    if cross_down == 1 and cross_up == 0:
        if p is not None and p <= 40:
            return SIGNAL_STRONG_SELL
        return SIGNAL_SELL
    h, m = i["hts_trend"], i["macd_trend"]
    if h is None and m is None and p is None:
        return ""
    ups = sum(1 for x in (h, m) if x == "up")
    downs = sum(1 for x in (h, m) if x == "down")
    if ups >= 2:
        return SIGNAL_BUY
    if downs >= 2:
        return SIGNAL_SELL
    if p is not None:
        if p >= 60:
            return SIGNAL_BUY
        if p <= 40:
            return SIGNAL_SELL
    return SIGNAL_NEUTRAL


def strategy_pca_buckets(row: Dict[str, object]) -> str:
    """Tylko PCA: ≤20 Strong Buy, 20–40 Buy, 40–60 Neutral, 60–80 Sell, ≥80 Strong Sell.

    PCA jest ``risk-on`` skalą — wyższe = drożej / przegrzane → bliżej do
    Sell-a; niższe = okazja → Buy. Zgodne z ustawieniami w „PCA-RI" w TV.
    """
    p = _pca_value(row)
    if p is None:
        return ""
    if p <= 20:
        return SIGNAL_STRONG_BUY
    if p <= 40:
        return SIGNAL_BUY
    if p < 60:
        return SIGNAL_NEUTRAL
    if p < 80:
        return SIGNAL_SELL
    return SIGNAL_STRONG_SELL


def strategy_scoring(row: Dict[str, object]) -> str:
    """HTS Trend (±1) + MacD Trend (±1) + PCA (≥60 ⇒ −1, ≤40 ⇒ +1, inaczej 0). Suma ∈ [−3..+3]."""
    i = _row_inputs(row)
    score = 0.0
    have_any = False
    if i["hts_trend"] == "up":
        score += 1
        have_any = True
    elif i["hts_trend"] == "down":
        score -= 1
        have_any = True
    if i["macd_trend"] == "up":
        score += 1
        have_any = True
    elif i["macd_trend"] == "down":
        score -= 1
        have_any = True
    p = i["pca"]
    if p is not None:
        have_any = True
        if p <= 40:
            score += 1
        elif p >= 60:
            score -= 1
    if not have_any:
        return ""
    return _bucket_score(score)


STRATEGIES: Dict[str, Callable[[Dict[str, object]], str]] = {
    "trend_only": strategy_trend_only,
    "cross_priority": strategy_cross_priority,
    "pca_buckets": strategy_pca_buckets,
    "scoring": strategy_scoring,
}

STRATEGY_LABELS: Dict[str, str] = {
    "trend_only": "Trendy + PCA",
    "cross_priority": "Crossy (priorytet)",
    "pca_buckets": "PCA (kosze)",
    "scoring": "Punktowy",
}


def compute_signals(row: Dict[str, object]) -> Dict[str, str]:
    """Zwraca słownik ``{strategy_id: signal}`` dla wszystkich strategii."""
    out: Dict[str, str] = {}
    for name, fn in STRATEGIES.items():
        try:
            out[name] = fn(row) or ""
        except Exception:
            out[name] = ""
    return out
