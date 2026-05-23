import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "app.js"


def test_app_js_syntax_is_valid():
    node = shutil.which("node")
    if not node:
        return
    result = subprocess.run(
        [node, "--check", str(APP_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_chart_metric_field_mapping_present():
    source = APP_JS.read_text(encoding="utf-8")
    assert "macd_line: 'MacD_Line'" in source
    assert "macd_hist: 'MacD_Histogram'" in source


def test_parse_polish_decimal_handles_unicode_minus_for_macd_chart():
    node = shutil.which("node")
    if not node:
        return
    script = r"""
    function parsePolishDecimal(text) {
        if (text == null || text === '' || text === '--') return NaN;
        let s = String(text).trim().replace(/\u00a0/g, ' ').replace(/\s/g, '');
        s = s.replace(/\u2212/g, '-');
        if (!s) return NaN;
        if (s.includes(',') && s.includes('.')) {
            s = s.replace(/\./g, '').replace(',', '.');
        } else if (s.includes(',')) {
            s = s.replace(',', '.');
        }
        const n = parseFloat(s);
        return Number.isFinite(n) ? n : NaN;
    }
    function extractNumericField(row, fieldName) {
        if (!row || !fieldName) return NaN;
        let raw = row[fieldName];
        if ((raw == null || raw === '') && fieldName === 'MacD_Line') {
            raw = row['MacD_Fast_High'];
        }
        if ((raw == null || raw === '') && fieldName === 'MacD_Histogram') {
            raw = row['MacD_Fast_Low'];
        }
        if (typeof raw === 'string' && /brak danych/i.test(raw)) return NaN;
        if (typeof raw === 'string' && raw.includes('(')) {
            return parsePolishDecimal(String(raw).split('(')[0]);
        }
        return parsePolishDecimal(raw);
    }
    const row = {
        MacD_Line: '\u22123.88 (Czerwony)',
        MacD_Histogram: '\u22120,1340 (Czerwony)',
    };
    const line = extractNumericField(row, 'MacD_Line');
    const hist = extractNumericField(row, 'MacD_Histogram');
    if (!Number.isFinite(line) || line !== -3.88) process.exit(1);
    if (!Number.isFinite(hist) || Math.abs(hist + 0.134) > 1e-9) process.exit(2);
    """
    result = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_parse_scraper_progress_uses_overall_fraction():
    node = shutil.which("node")
    if not node:
        return
    script = r"""
    function parseScraperProgress(progressStr) {
        if (!progressStr || typeof progressStr !== 'string') return 0;
        const m = progressStr.match(/^(\d+)\s*\/\s*(\d+)/);
        if (!m) return 0;
        const current = parseInt(m[1], 10);
        const total = parseInt(m[2], 10);
        if (!Number.isFinite(current) || !Number.isFinite(total) || total <= 0) return 0;
        return (current / total) * 100;
    }
    const phase1 = parseScraperProgress('15/78 · ticker 15/26 · wsk. 1/3');
    const phase2 = parseScraperProgress('29/78 · ticker 3/26 · wsk. 2/3');
    if (Math.abs(phase1 - (15 / 78) * 100) > 1e-9) process.exit(1);
    if (phase2 <= phase1) process.exit(2);
    """
    result = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_repair_modal_decoupled_from_scraper_in_source():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    assert 'id="repair-symbols-submit-rerun"' in html
    assert 'repair-symbols-rerun' not in html
    assert "submitRepairRenames(false)" in js
    assert "Uruchom scraper ręcznie" in js
    assert "repair-manual-input" in js
    assert "Edytuj ręcznie" in js


def test_favorites_filter_and_storage_wired():
    js = APP_JS.read_text(encoding="utf-8")
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "ta_favorite_tickers" in js
    assert "favorites-first" in js
    assert 'id="favorites-filter"' in html
    assert "card-favorite-btn" in html
    assert "toggleFavoriteTicker" in js
    assert "favoritesOnly" in js


def test_system_health_ui_wired():
    js = APP_JS.read_text(encoding="utf-8")
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "/api/health" in js
    assert 'id="system-health-list"' in html
    assert "fetchSystemHealth" in js
