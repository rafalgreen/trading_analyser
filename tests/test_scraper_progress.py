"""Postęp scrapera — format monotoniczny między fazami wskaźników."""

import tv_scraper as tv


def test_format_scraper_progress_first_ticker_first_indicator():
    assert tv._format_scraper_progress(0, 26, 0, 3) == (
        "1/78 · ticker 1/26 · wsk. 1/3"
    )


def test_format_scraper_progress_mid_first_indicator_phase():
    assert tv._format_scraper_progress(14, 26, 0, 3) == (
        "15/78 · ticker 15/26 · wsk. 1/3"
    )


def test_format_scraper_progress_second_indicator_does_not_reset_overall():
    # Po zakończeniu fazy 1 (26/78), faza 2 ticker 3 → 29/78 (nie 3/26)
    assert tv._format_scraper_progress(2, 26, 1, 3) == (
        "29/78 · ticker 3/26 · wsk. 2/3"
    )


def test_format_scraper_progress_done():
    assert tv._format_scraper_progress(25, 26, 2, 3) == (
        "78/78 · ticker 26/26 · wsk. 3/3"
    )
