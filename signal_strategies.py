"""Strategie liczenia sygnału kup/sprzedaj z 3 wskaźników (PCA, HTS Panel, MacD).

Każda strategia konsumuje wiersz wyników (jako ``dict``) i zwraca jedną z pięciu
kategorii: ``"strong buy"``, ``"buy"``, ``"neutral"``, ``"sell"``, ``"strong sell"``.
Pusty string oznacza brak sygnału (np. niekompletny wiersz).

Identyfikatory strategii i ich semantyka są spójne z UI (filtr/badże).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, Optional

from results_store import (
    parse_legend_number,
    parse_pca_number,
    row_has_all_configured_indicators,
)


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
    # `PCA_Values` is the rich value displayed in the UI and written by the
    # current scraper. Older CSVs may still contain legacy `PCA_Value`; use it
    # only as a fallback so signals match the visible card value.
    candidates = (row.get("PCA_Values"), row.get("PCA_Value"))
    for raw in candidates:
        if raw is None or str(raw).strip() == "":
            continue
        v, _ = parse_pca_number(raw)
        if v is not None:
            return float(v)
    return None


def row_signals_allowed(
    row: Dict[str, object], indicators: Optional[Iterable[str]] = None
) -> bool:
    """True when every configured indicator has parseable data in the row."""
    if not indicators:
        return True
    return row_has_all_configured_indicators(row, indicators)


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
    """2× Wzrostowy + PCA≤40 → Strong Buy; 2× Wzrostowy → Buy; 2× Spadkowy + PCA≥60 → Strong Sell; 2× Spadkowy → Sell; reszta → Neutral.

    PCA jest skalą risk-on jak w ``pca_buckets``: niskie = okazja (bullish),
    wysokie = drogo/przegrzane (bearish).
    """
    i = _row_inputs(row)
    h, m, p = i["hts_trend"], i["macd_trend"], i["pca"]
    if h is None and m is None:
        return ""
    ups = sum(1 for x in (h, m) if x == "up")
    downs = sum(1 for x in (h, m) if x == "down")
    if ups >= 2:
        if p is not None and p <= 40:
            return SIGNAL_STRONG_BUY
        return SIGNAL_BUY
    if downs >= 2:
        if p is not None and p >= 60:
            return SIGNAL_STRONG_SELL
        return SIGNAL_SELL
    return SIGNAL_NEUTRAL


def strategy_cross_priority(row: Dict[str, object]) -> str:
    """Crossy mają priorytet; PCA jako tie-breaker (≤40 buy, ≥60 sell — jak ``pca_buckets``)."""
    i = _row_inputs(row)
    cross_up = sum(1 for x in (i["hts_cross"], i["macd_cross"]) if x == "up")
    cross_down = sum(1 for x in (i["hts_cross"], i["macd_cross"]) if x == "down")
    p = i["pca"]
    if cross_up >= 2:
        return SIGNAL_STRONG_BUY
    if cross_down >= 2:
        return SIGNAL_STRONG_SELL
    if cross_up == 1 and cross_down == 0:
        if p is not None and p <= 40:
            return SIGNAL_STRONG_BUY
        return SIGNAL_BUY
    if cross_down == 1 and cross_up == 0:
        if p is not None and p >= 60:
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
        if p <= 40:
            return SIGNAL_BUY
        if p >= 60:
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


def strategy_scoring(
    row: Dict[str, object], *, indicators: Optional[Iterable[str]] = None
) -> str:
    """HTS Trend (±1) + MacD Trend (±1) + PCA (≥60 ⇒ −1, ≤40 ⇒ +1, inaczej 0). Suma ∈ [−3..+3]."""
    if not row_signals_allowed(row, indicators):
        return ""
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


# ---------------------------------------------------------------------------
# Dotknięcie czerwonej wstęgi (HTS Slow band) + strategia band_touch
# ---------------------------------------------------------------------------

# Progi domyślne strategii band_touch (nadpisywalne w scraper_config.json →
# sekcja "signals").
BAND_TOUCH_DEFAULTS: Dict[str, Any] = {
    # „Prawie dotknięcie": odległość ceny od krawędzi wstęgi ≤ X% ceny.
    "tolerance_pct": 4.0,
    # „PCA prawie niebieskie" (niski risk) — warunek kupna.
    "buy_pca_max": 40.0,
    # Silne przegrzanie PCA wzmacnia sygnał sprzedaży.
    "sell_pca_min": 60.0,
    # Interwały, na których strategia generuje sygnały.
    "intervals": ("1D", "1W"),
}

BAND_TOUCH_STATE_TOUCH = "touch"
BAND_TOUCH_STATE_NEAR = "near"
BAND_TOUCH_STATE_NONE = "none"


def _pca_color_is_blueish(color: Optional[str]) -> bool:
    """Czy kolor CSS ``rgb(...)`` z legendy PCA jest niebieskawy (niski risk)."""
    if not color:
        return False
    import re as _re

    m = _re.search(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", color)
    if not m:
        return False
    r, g, b = (int(m.group(i)) for i in (1, 2, 3))
    return b > 120 and b > r and b >= g


def compute_band_touch(
    row: Dict[str, object], *, tolerance_pct: float = 2.0
) -> Dict[str, Any]:
    """Wskaźnik „dotknięcie czerwonej wstęgi" (HTS Slow band) dla wiersza.

    Zwraca dict:
      - ``state``: ``"touch"`` (cena wewnątrz wstęgi / na krawędzi),
        ``"near"`` (w odległości ≤ tolerance_pct% ceny od najbliższej
        krawędzi), ``"none"`` (daleko) albo ``""`` gdy brak danych
        (cena/wstęga nieparsowalne).
      - ``distance_pct``: znormalizowana odległość od najbliższej krawędzi
        w % ceny (0.0 przy dotknięciu; ``None`` gdy brak danych).
      - ``side``: ``"above"`` (cena nad wstęgą — typowe dla trendu
        wzrostowego), ``"below"`` (pod wstęgą), ``"inside"`` albo ``""``.
    """
    empty = {"state": "", "distance_pct": None, "side": ""}
    price = parse_legend_number(row.get("Current_Price"))
    slow_high = parse_legend_number(row.get("HTS Panel_Slow_High"))
    slow_low = parse_legend_number(row.get("HTS Panel_Slow_Low"))
    if price is None or not math.isfinite(price) or price <= 0:
        return empty
    if slow_high is None and slow_low is None:
        return empty
    if slow_high is None:
        slow_high = slow_low
    if slow_low is None:
        slow_low = slow_high
    if slow_low > slow_high:
        slow_low, slow_high = slow_high, slow_low

    if slow_low <= price <= slow_high:
        return {
            "state": BAND_TOUCH_STATE_TOUCH,
            "distance_pct": 0.0,
            "side": "inside",
        }

    if price > slow_high:
        distance = (price - slow_high) / price * 100.0
        side = "above"
    else:
        distance = (slow_low - price) / price * 100.0
        side = "below"

    state = (
        BAND_TOUCH_STATE_NEAR
        if distance <= float(tolerance_pct)
        else BAND_TOUCH_STATE_NONE
    )
    return {"state": state, "distance_pct": round(distance, 2), "side": side}


def strategy_band_touch(
    row: Dict[str, object], *, params: Optional[Dict[str, Any]] = None
) -> str:
    """Wstęga czerwona (HTS Slow) jako strefa wejścia/wyjścia.

    KUP: trend HTS Wzrostowy + cena dotyka (lub prawie dotyka) czerwonej
    wstęgi od góry + PCA Risk niski („prawie niebieski", ≤ buy_pca_max).
    Dotknięcie (nie tylko zbliżenie) → Strong Buy.

    SPRZEDAJ (odwrotność): trend HTS Spadkowy + cena dotyka / prawie dotyka
    czerwonej wstęgi od dołu (wstęga działa jak opór). Dotknięcie + PCA
    wysokie (≥ sell_pca_min) → Strong Sell.

    Sygnały tylko na interwałach z ``intervals`` (domyślnie 1D i 1W) —
    zgodnie z założeniem „D1 lub W1 musi być w trendzie".
    """
    p = dict(BAND_TOUCH_DEFAULTS)
    if params:
        p.update({k: v for k, v in params.items() if v is not None})

    interval = str(row.get("Interval") or "").strip().upper()
    allowed = {str(iv).strip().upper() for iv in (p.get("intervals") or ())}
    if allowed and interval and interval not in allowed:
        return ""

    trend = _trend(row.get("HTS Panel_Trend"))
    touch = compute_band_touch(row, tolerance_pct=float(p["tolerance_pct"]))
    state = touch["state"]
    if trend is None or not state:
        return ""
    if state == BAND_TOUCH_STATE_NONE:
        return SIGNAL_NEUTRAL

    pca_raw = row.get("PCA_Values") or row.get("PCA_Value")
    pca_value, pca_color = parse_pca_number(pca_raw)

    if trend == "up":
        pca_low = (
            pca_value is not None and pca_value <= float(p["buy_pca_max"])
        ) or (pca_value is None and _pca_color_is_blueish(pca_color))
        if not pca_low:
            return SIGNAL_NEUTRAL
        if state == BAND_TOUCH_STATE_TOUCH:
            return SIGNAL_STRONG_BUY
        return SIGNAL_BUY

    # trend == "down": odwrotność — wstęga jako opór, dotknięcie = sprzedaż.
    pca_high = pca_value is not None and pca_value >= float(p["sell_pca_min"])
    if state == BAND_TOUCH_STATE_TOUCH and pca_high:
        return SIGNAL_STRONG_SELL
    return SIGNAL_SELL


STRATEGIES: Dict[str, Callable[[Dict[str, object]], str]] = {
    "trend_only": strategy_trend_only,
    "cross_priority": strategy_cross_priority,
    "pca_buckets": strategy_pca_buckets,
    "scoring": strategy_scoring,
    "band_touch": strategy_band_touch,
}

STRATEGY_LABELS: Dict[str, str] = {
    "trend_only": "Trendy + PCA",
    "cross_priority": "Crossy (priorytet)",
    "pca_buckets": "PCA (kosze)",
    "scoring": "Punktowy",
    "band_touch": "Wstęga (touch)",
}


# Strategie liczone nawet przy niekompletnym wierszu — mają własne wymagania
# danych (band_touch nie używa MacD, więc jego brak nie powinien blokować).
_GATE_EXEMPT_STRATEGIES = frozenset({"band_touch"})


def compute_signals(
    row: Dict[str, object],
    *,
    indicators: Optional[Iterable[str]] = None,
    band_touch_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Zwraca słownik ``{strategy_id: signal}`` dla wszystkich strategii."""
    allowed = row_signals_allowed(row, indicators)
    out: Dict[str, str] = {}
    for name, fn in STRATEGIES.items():
        if not allowed and name not in _GATE_EXEMPT_STRATEGIES:
            out[name] = ""
            continue
        try:
            if name == "band_touch":
                out[name] = strategy_band_touch(row, params=band_touch_params) or ""
            else:
                out[name] = fn(row) or ""
        except Exception:
            out[name] = ""
    return out
