document.addEventListener('DOMContentLoaded', () => {
    // Elements
    const resultsGrid = document.getElementById('results-grid');
    const cardTemplate = document.getElementById('ticker-card-template');
    const loadingOverlay = document.getElementById('loading-overlay');
    const errorMessage = document.getElementById('error-message');
    const errorText = document.getElementById('error-text');
    const currentDateTitle = document.getElementById('current-date-title');
    const recordCount = document.getElementById('record-count');
    const refreshBtn = document.getElementById('refresh-btn');
    const expandAllBtn = document.getElementById('expand-all-btn');
    const searchInput = document.getElementById('search-input');
    const sortSelect = document.getElementById('sort-select');
    const verdictFilter = document.getElementById('verdict-filter');
    const fundPeMax = document.getElementById('fund-pe-max');
    const fundRoeMin = document.getElementById('fund-roe-min');
    const fundFcfPositive = document.getElementById('fund-fcf-positive');
    const fundDeMax = document.getElementById('fund-de-max');
    const fundFiltersPanel = document.getElementById('fund-filters-panel');
    const fundFiltersCount = document.getElementById('fund-filters-count');
    const intervalFilter = document.getElementById('interval-filter');
    const chartPanel = document.getElementById('chart-panel');
    const pcaChartCanvas = document.getElementById('pcaChart');
    const chartEmptyEl = document.getElementById('chart-empty');
    const chartEmptyTextEl = document.getElementById('chart-empty-text');
    const chartTitle = document.getElementById('chart-title');
    const chartIntervalToggle = document.getElementById('chart-interval-toggle');
    const chartMetricSelect = document.getElementById('chart-metric-select');
    const wlFilterToolbar = document.getElementById('wl-filter-toolbar');
    const strategyDescriptionEl = document.getElementById('strategy-description');
    const strategyEmptyBannerEl = document.getElementById('strategy-empty-banner');
    const globalBanner = document.getElementById('global-scrape-banner');
    const globalBannerText = document.getElementById('global-scrape-banner-text');
    const globalBannerFill = document.getElementById('global-scrape-banner-fill');
    const toastContainer = document.getElementById('toast-container');

    // ----- UI prefs (localStorage) -----
    const UI_KEYS = {
        sortMode: 'ta_sort_mode',
        chartInterval: 'ta_chart_interval',
        chartMetric: 'ta_chart_metric',
        intervalFilter: 'ta_interval_filter',
        activeView: 'ta_active_view',
        collapsedCards: 'ta_collapsed_cards',
        signalStrategy: 'ta_signal_strategy',
        signalInterval: 'ta_signal_interval',
        consensusFilter: 'ta_consensus_filter',
        verdictFilter: 'ta_verdict_filter',
        fundPeMax: 'ta_fund_pe_max',
        fundRoeMin: 'ta_fund_roe_min',
        fundFcfPositive: 'ta_fund_fcf_positive',
        fundDeMax: 'ta_fund_de_max',
        renamedHidden: 'ta_renamed_hidden',
    };

    function loadPref(key, fallback) {
        try {
            const raw = localStorage.getItem(key);
            if (raw == null) return fallback;
            return JSON.parse(raw);
        } catch (e) {
            return fallback;
        }
    }

    function savePref(key, value) {
        try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* quota */ }
    }

    let currentData = [];
    let configTickerCount = 0;
    let pcaChartInstance = null;
    let historyChartInstance = null;

    const METRIC_TO_FIELD = {
        pca: 'PCA_Value',
        macd_line: 'MacD_Line',
        macd_hist: 'MacD_Histogram',
        hts_fh: 'HTS Panel_Fast_High',
        hts_sh: 'HTS Panel_Slow_High',
        pe: 'Fund_PE',
        pb: 'Fund_PB',
        ev_ebitda: 'Fund_EV_EBITDA',
        roe: 'Fund_ROE',
        fcf: 'Fund_FCF',
    };

    const FUND_CHART_METRICS = new Set(['pe', 'pb', 'ev_ebitda', 'roe', 'fcf']);

    const METRIC_LABELS = {
        pca: 'PCA',
        macd_line: 'MacD Line',
        macd_hist: 'MacD Histogram',
        hts_fh: 'HTS Fast High',
        hts_sh: 'HTS Slow High',
        pe: 'P/E',
        pb: 'P/B',
        ev_ebitda: 'EV/EBITDA',
        roe: 'ROE',
        fcf: 'FCF',
    };

    const ALLOWED_SORT = new Set([
        'data-status', 'default', 'pca-desc', 'pca-asc',
        'macd-desc', 'macd-asc', 'pe-desc', 'pe-asc', 'roe-desc', 'roe-asc', 'fcf-desc', 'fcf-asc',
        'consensus-bullish', 'consensus-bearish',
        'verdict-kup-first', 'composite-desc',
        'ticker-asc', 'ticker-desc',
    ]);
    const ALLOWED_VERDICT_FILTERS = new Set(['all', 'kup', 'obserwuj', 'unikaj']);
    const VERDICT_SORT_RANK = { kup: 0, obserwuj: 1, unikaj: 2 };
    const ALLOWED_INTERVAL = new Set(['1D', '1W', '1M']);
    const ALLOWED_SIGNAL_INTERVALS = new Set(['D', 'W', 'M']);
    const BUY_SIGNALS = new Set(['buy', 'strong buy']);
    const SELL_SIGNALS = new Set(['sell', 'strong sell']);
    const ALLOWED_CONSENSUS_FILTERS = new Set(['all', 'bullish', 'bearish', 'neutral']);
    const ALL_STRATEGY_IDS = ['trend_only', 'cross_priority', 'pca_buckets', 'scoring'];
    const ALLOWED_STRATEGIES = new Set([...ALL_STRATEGY_IDS, 'all']);

    const STRATEGY_LABEL_FALLBACK = {
        trend_only: 'Trendy + PCA',
        cross_priority: 'Crossy (priorytet)',
        pca_buckets: 'PCA (kosze)',
        scoring: 'Punktowy',
    };
    const STRATEGY_DESCRIPTIONS = {
        all: 'Pokazuje wszystkie tickery — bez filtra strategii. Badże na karcie wyświetlają wynik każdej z 4 strategii.',
        trend_only: '2× Wzrostowy + PCA ≥ 60 → Strong Buy; 2× Wzrostowy → Buy; mieszane → Neutral; 2× Spadkowy → Sell; 2× Spadkowy + PCA ≤ 40 → Strong Sell. Wymaga jednoznacznego trendu w obu modułach (HTS Trend i MacD Trend).',
        cross_priority: 'Sygnały przecięcia (BULL/BEAR CROSS) z HTS i MacD przeważają nad trendem. PCA jako tie-breaker (≥60 → buy, ≤40 → sell). Gdy brak crossów — fallback na strategię trendową.',
        pca_buckets: 'Wyłącznie z wartości PCA: ≤20 → Strong Buy, 20–40 → Buy, 40–60 → Neutral, 60–80 → Sell, ≥80 → Strong Sell. Najprostsza i najbardziej kontr-trendowa.',
        scoring: 'HTS Trend (±1) + MacD Trend (±1) + PCA (≥60 ⇒ −1, ≤40 ⇒ +1). Suma w zakresie [−3..+3] mapowana na 5 koszyków: ≥+2 Strong Buy, +1 Buy, 0 Neutral, −1 Sell, ≤−2 Strong Sell.',
    };

    let currentSortMode = ALLOWED_SORT.has(loadPref(UI_KEYS.sortMode, 'data-status'))
        ? loadPref(UI_KEYS.sortMode, 'data-status') : 'data-status';
    function sanitizeChartMetric(raw) {
        const val = typeof raw === 'string' ? raw : 'pca';
        if (!METRIC_TO_FIELD[val]) return 'pca';
        if (chartMetricSelect && !chartMetricSelect.querySelector(`option[value="${val}"]`)) return 'pca';
        return val;
    }

    function sanitizeChartInterval(raw) {
        return ALLOWED_INTERVAL.has(raw) ? raw : '1D';
    }

    let currentChartInterval = sanitizeChartInterval(loadPref(UI_KEYS.chartInterval, '1D'));
    let currentChartMetric = sanitizeChartMetric(loadPref(UI_KEYS.chartMetric, 'pca'));
    if (currentChartMetric !== loadPref(UI_KEYS.chartMetric, 'pca')) {
        savePref(UI_KEYS.chartMetric, currentChartMetric);
    }
    if (currentChartInterval !== loadPref(UI_KEYS.chartInterval, '1D')) {
        savePref(UI_KEYS.chartInterval, currentChartInterval);
    }
    let currentIntervalFilter = loadPref(UI_KEYS.intervalFilter, 'All');
    if (!['All', '1D', '1W', '1M'].includes(currentIntervalFilter)) currentIntervalFilter = 'All';

    const storedCollapsed = loadPref(UI_KEYS.collapsedCards, []);
    const collapsedCards = new Set(Array.isArray(storedCollapsed) ? storedCollapsed : []);

    let signalStrategy = ALLOWED_STRATEGIES.has(loadPref(UI_KEYS.signalStrategy, 'all'))
        ? loadPref(UI_KEYS.signalStrategy, 'all') : 'all';
    let signalInterval = ALLOWED_SIGNAL_INTERVALS.has(loadPref(UI_KEYS.signalInterval, 'D'))
        ? loadPref(UI_KEYS.signalInterval, 'D') : 'D';
    let consensusFilter = ALLOWED_CONSENSUS_FILTERS.has(loadPref(UI_KEYS.consensusFilter, 'all'))
        ? loadPref(UI_KEYS.consensusFilter, 'all') : 'all';
    let currentVerdictFilter = ALLOWED_VERDICT_FILTERS.has(loadPref(UI_KEYS.verdictFilter, 'all'))
        ? loadPref(UI_KEYS.verdictFilter, 'all') : 'all';
    let fundFilterPeMax = loadPref(UI_KEYS.fundPeMax, '') || '';
    let fundFilterRoeMin = loadPref(UI_KEYS.fundRoeMin, '') || '';
    let fundFilterFcfPositive = Boolean(loadPref(UI_KEYS.fundFcfPositive, false));
    let fundFilterDeMax = loadPref(UI_KEYS.fundDeMax, '') || '';
    let availableSignalStrategies = ALL_STRATEGY_IDS.map(id => ({ id, label: id }));

    function persistCollapsed() {
        savePref(UI_KEYS.collapsedCards, Array.from(collapsedCards));
    }

    // Tickery ukryte po rename (stare nazwy). Persist w localStorage jako mapa
    // old → new, żeby reload nie przywracał starej karty.
    const storedRenamed = loadPref(UI_KEYS.renamedHidden, {});
    const renamedHidden = (storedRenamed && typeof storedRenamed === 'object' && !Array.isArray(storedRenamed))
        ? { ...storedRenamed } : {};
    function persistRenamedHidden() {
        savePref(UI_KEYS.renamedHidden, renamedHidden);
    }
    function markTickerRenamed(oldTicker, newTicker) {
        if (!oldTicker) return;
        renamedHidden[String(oldTicker).toUpperCase()] = String(newTicker || '').toUpperCase();
        persistRenamedHidden();
    }
    function isTickerHidden(ticker) {
        return Object.prototype.hasOwnProperty.call(renamedHidden, String(ticker || '').toUpperCase());
    }

    // Track tickers currently being re-scraped via the per-card button
    const rerunningTickers = new Set();
    let currentHistoryTicker = null;
    let currentHistoryInterval = '1D';
    let currentHistoryMetric = 'PCA';

    function escapeHtml(str) {
        if (str == null || str === '') return '';
        const d = document.createElement('div');
        d.textContent = String(str);
        return d.innerHTML;
    }

    /** Zwraca bezpieczne CSS `color` albo fallback #555. Odrzuca ', ", ; , { } itd. */
    function sanitizeCssColor(raw) {
        if (!raw) return '#555';
        const s = String(raw).trim();
        if (/[;'"<>(){}\\]/.test(s) && !/^rgba?\([^()'"<>;\\]+\)$/i.test(s)) {
            return '#555';
        }
        if (/^#[0-9a-f]{3,8}$/i.test(s)) return s;
        if (/^rgba?\([\d\s,.%]+\)$/i.test(s)) return s;
        if (/^[a-zA-Z]+$/.test(s)) return s;
        return '#555';
    }

    // ----- Toast / Banner helpers -----
    function showToast({ type = 'info', title = '', message = '', duration = 5000 } = {}) {
        if (!toastContainer) return;
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        const icon = type === 'success' ? 'ph-check-circle'
            : type === 'error' ? 'ph-warning-circle'
            : 'ph-info';
        toast.innerHTML = `
            <i class="ph ${icon}"></i>
            <div class="toast-body">
                ${title ? `<div class="toast-title">${escapeHtml(title)}</div>` : ''}
                ${message ? `<div class="toast-msg">${escapeHtml(message)}</div>` : ''}
            </div>
        `;
        const dismiss = () => {
            if (!toast.isConnected) return;
            toast.classList.add('leaving');
            setTimeout(() => toast.remove(), 220);
        };
        toast.addEventListener('click', dismiss);
        toastContainer.appendChild(toast);
        if (duration > 0) setTimeout(dismiss, duration);
    }

    /**
     * In-app modal potwierdzenia. Zwraca Promise<boolean>. Nie używa natywnego
     * `confirm()` — dzięki temu nie blokuje go ustawienie „nie pokazuj więcej
     * okien dialogowych" w przeglądarce.
     */
    function confirmDialog({
        title = 'Potwierdź',
        message = '',
        confirmLabel = 'OK',
        cancelLabel = 'Anuluj',
        danger = false,
    } = {}) {
        return new Promise((resolve) => {
            let overlay = document.getElementById('app-confirm-dialog');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.id = 'app-confirm-dialog';
                overlay.className = 'modal-overlay';
                overlay.setAttribute('role', 'dialog');
                overlay.setAttribute('aria-modal', 'true');
                overlay.innerHTML = `
                    <div class="modal-dialog modal-dialog-sm">
                        <div class="modal-header">
                            <div><h3 data-role="title"></h3>
                            <div class="modal-subtitle" data-role="msg"></div></div>
                            <button type="button" class="modal-close" data-role="close" aria-label="Zamknij">&times;</button>
                        </div>
                        <div class="modal-body">
                            <div class="rename-actions">
                                <button type="button" class="btn btn-secondary" data-role="cancel">Anuluj</button>
                                <button type="button" class="btn btn-primary" data-role="ok">OK</button>
                            </div>
                        </div>
                    </div>
                `;
                document.body.appendChild(overlay);
            }
            overlay.querySelector('[data-role="title"]').textContent = title;
            const msgEl = overlay.querySelector('[data-role="msg"]');
            msgEl.textContent = message;
            msgEl.style.display = message ? '' : 'none';
            const okBtn = overlay.querySelector('[data-role="ok"]');
            const cancelBtn = overlay.querySelector('[data-role="cancel"]');
            const closeBtn = overlay.querySelector('[data-role="close"]');
            okBtn.textContent = confirmLabel;
            cancelBtn.textContent = cancelLabel;
            okBtn.className = danger ? 'btn btn-danger' : 'btn btn-primary';

            const close = (value) => {
                overlay.classList.remove('visible');
                overlay.setAttribute('aria-hidden', 'true');
                okBtn.onclick = null;
                cancelBtn.onclick = null;
                closeBtn.onclick = null;
                overlay.onclick = null;
                document.removeEventListener('keydown', onKey);
                resolve(value);
            };
            const onKey = (e) => {
                if (e.key === 'Escape') { e.preventDefault(); close(false); }
                else if (e.key === 'Enter') { e.preventDefault(); close(true); }
            };
            okBtn.onclick = () => close(true);
            cancelBtn.onclick = () => close(false);
            closeBtn.onclick = () => close(false);
            overlay.onclick = (e) => { if (e.target === overlay) close(false); };
            document.addEventListener('keydown', onKey);
            overlay.classList.add('visible');
            overlay.setAttribute('aria-hidden', 'false');
            setTimeout(() => okBtn.focus(), 50);
        });
    }

    /**
     * Modal dla niedokończonego runu: 3 przyciski (Wznów / Od nowa / Anuluj).
     * Resolve: 'resume' | 'fresh' | 'cancel'.
     * `pending` to obiekt z /api/scraper/pending_run.
     */
    function pendingRunDialog(pending) {
        return new Promise((resolve) => {
            let overlay = document.getElementById('app-pending-run-dialog');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.id = 'app-pending-run-dialog';
                overlay.className = 'modal-overlay';
                overlay.setAttribute('role', 'dialog');
                overlay.setAttribute('aria-modal', 'true');
                overlay.innerHTML = `
                    <div class="modal-dialog modal-dialog-sm">
                        <div class="modal-header">
                            <div>
                                <h3>Niedokończony scraping</h3>
                                <div class="modal-subtitle" data-role="msg"></div>
                            </div>
                            <button type="button" class="modal-close" data-role="close" aria-label="Zamknij">&times;</button>
                        </div>
                        <div class="modal-body">
                            <div class="pending-run-meta" data-role="meta"></div>
                            <div class="rename-actions pending-run-actions">
                                <button type="button" class="btn btn-secondary" data-role="cancel">Anuluj</button>
                                <button type="button" class="btn btn-secondary" data-role="resume">Wznów</button>
                                <button type="button" class="btn btn-primary" data-role="fresh">Zacznij od nowa</button>
                            </div>
                        </div>
                    </div>
                `;
                document.body.appendChild(overlay);
            }

            const msgEl = overlay.querySelector('[data-role="msg"]');
            const metaEl = overlay.querySelector('[data-role="meta"]');
            const resumeBtn = overlay.querySelector('[data-role="resume"]');
            const freshBtn = overlay.querySelector('[data-role="fresh"]');
            const cancelBtn = overlay.querySelector('[data-role="cancel"]');
            const closeBtn = overlay.querySelector('[data-role="close"]');

            msgEl.textContent =
                'Wykryto niedokończony run scrapera (po crashu lub po Stop). ' +
                'Wznowienie dopisze do tego samego pliku — „Od nowa" utworzy nowy z dzisiejszą datą.';

            const file = (pending?.current_file || '').replace(/^results\//, '');
            const fileDate = pending?.current_file_date || '';
            const proc = Number(pending?.processed_count || 0);
            const total = Number(pending?.total_in_config || 0);
            const remaining = Number(pending?.remaining_count || (total - proc));

            const lines = [];
            if (file) {
                lines.push(`<div><span class="pending-run-label">Plik:</span> <code>${escapeHtml(file)}</code>${fileDate ? ` <span class="pending-run-meta-date">(${escapeHtml(fileDate)})</span>` : ''}</div>`);
            }
            if (total > 0) {
                lines.push(`<div><span class="pending-run-label">Postęp:</span> ${proc}/${total} (pozostało: ${remaining})</div>`);
            } else if (proc > 0) {
                lines.push(`<div><span class="pending-run-label">Przetworzonych:</span> ${proc}</div>`);
            }
            metaEl.innerHTML = lines.join('');

            // Heurystyka: state >1h od ostatniej modyfikacji → focus na „Od nowa"
            // (user pewnie zapomniał o starym runie). Inaczej focus na „Wznów".
            const mtime = Number(pending?.state_mtime || 0) * 1000;
            const isStale = mtime > 0 && (Date.now() - mtime) > 60 * 60 * 1000;
            const defaultBtn = isStale ? freshBtn : resumeBtn;

            const close = (value) => {
                overlay.classList.remove('visible');
                overlay.setAttribute('aria-hidden', 'true');
                resumeBtn.onclick = null;
                freshBtn.onclick = null;
                cancelBtn.onclick = null;
                closeBtn.onclick = null;
                overlay.onclick = null;
                document.removeEventListener('keydown', onKey);
                resolve(value);
            };
            const onKey = (e) => {
                if (e.key === 'Escape') { e.preventDefault(); close('cancel'); }
                else if (e.key === 'Enter') { e.preventDefault(); close(isStale ? 'fresh' : 'resume'); }
            };
            resumeBtn.onclick = () => close('resume');
            freshBtn.onclick = () => close('fresh');
            cancelBtn.onclick = () => close('cancel');
            closeBtn.onclick = () => close('cancel');
            overlay.onclick = (e) => { if (e.target === overlay) close('cancel'); };
            document.addEventListener('keydown', onKey);
            overlay.classList.add('visible');
            overlay.setAttribute('aria-hidden', 'false');
            setTimeout(() => defaultBtn.focus(), 50);
        });
    }

    function setGlobalBanner(visible, { text = 'Scraper w toku…', progressPct = null } = {}) {
        if (!globalBanner) return;
        if (visible) {
            globalBanner.classList.add('visible');
            if (globalBannerText) globalBannerText.textContent = text;
            if (globalBannerFill) {
                if (progressPct != null && Number.isFinite(progressPct)) {
                    globalBannerFill.style.width = Math.min(100, Math.max(0, progressPct)) + '%';
                } else {
                    globalBannerFill.style.width = '0%';
                }
            }
        } else {
            globalBanner.classList.remove('visible');
        }
    }

    function updateFundFiltersUI() {
        let active = 0;
        if (fundFilterPeMax) active += 1;
        if (fundFilterRoeMin) active += 1;
        if (fundFilterFcfPositive) active += 1;
        if (fundFilterDeMax) active += 1;
        if (fundFiltersCount) {
            if (active > 0) {
                fundFiltersCount.hidden = false;
                fundFiltersCount.textContent = String(active);
            } else {
                fundFiltersCount.hidden = true;
                fundFiltersCount.textContent = '';
            }
        }
        if (fundFiltersPanel && active > 0) {
            fundFiltersPanel.open = true;
        }
    }

    // Initialize App
    async function init() {
        if (sortSelect) sortSelect.value = currentSortMode;
        if (verdictFilter) verdictFilter.value = currentVerdictFilter;
        if (fundPeMax) fundPeMax.value = fundFilterPeMax;
        if (fundRoeMin) fundRoeMin.value = fundFilterRoeMin;
        if (fundFcfPositive) fundFcfPositive.checked = fundFilterFcfPositive;
        if (fundDeMax) fundDeMax.value = fundFilterDeMax;
        updateFundFiltersUI();
        if (intervalFilter) intervalFilter.value = currentIntervalFilter;
        if (chartMetricSelect) chartMetricSelect.value = currentChartMetric;
        syncChartIntervalToggleVisibility();
        updateChartTitle();
        if (chartIntervalToggle) {
            chartIntervalToggle.querySelectorAll('.interval-toggle-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.interval === currentChartInterval);
            });
        }
        syncWlFilterChipsUI();

        await fetchDashboard();
        const persistedView = loadPref(UI_KEYS.activeView, 'dashboard-view');
        if (persistedView === 'config-view') {
            switchView('config-view');
        }
        startGlobalScraperPolling();
    }

    refreshBtn.addEventListener('click', () => {
        refreshBtn.classList.add('spinning');
        fetchDashboard().finally(() => {
            setTimeout(() => refreshBtn.classList.remove('spinning'), 500);
        });
    });

    searchInput.addEventListener('input', (e) => {
        const term = e.target.value.toLowerCase().trim();
        filterAndRenderCards(term);
    });

    sortSelect.addEventListener('change', (e) => {
        const val = e.target.value;
        currentSortMode = ALLOWED_SORT.has(val) ? val : 'default';
        savePref(UI_KEYS.sortMode, currentSortMode);
        const term = searchInput.value.toLowerCase().trim();
        filterAndRenderCards(term);
    });

    verdictFilter?.addEventListener('change', (e) => {
        const val = e.target.value;
        currentVerdictFilter = ALLOWED_VERDICT_FILTERS.has(val) ? val : 'all';
        savePref(UI_KEYS.verdictFilter, currentVerdictFilter);
        filterAndRenderCards(searchInput.value.toLowerCase().trim());
    });

    function syncFundFilterPrefs() {
        savePref(UI_KEYS.fundPeMax, fundFilterPeMax);
        savePref(UI_KEYS.fundRoeMin, fundFilterRoeMin);
        savePref(UI_KEYS.fundFcfPositive, fundFilterFcfPositive);
        savePref(UI_KEYS.fundDeMax, fundFilterDeMax);
        updateFundFiltersUI();
    }

    fundPeMax?.addEventListener('change', (e) => {
        fundFilterPeMax = e.target.value || '';
        syncFundFilterPrefs();
        filterAndRenderCards(searchInput.value.toLowerCase().trim());
    });
    fundRoeMin?.addEventListener('change', (e) => {
        fundFilterRoeMin = e.target.value || '';
        syncFundFilterPrefs();
        filterAndRenderCards(searchInput.value.toLowerCase().trim());
    });
    fundFcfPositive?.addEventListener('change', (e) => {
        fundFilterFcfPositive = Boolean(e.target.checked);
        syncFundFilterPrefs();
        filterAndRenderCards(searchInput.value.toLowerCase().trim());
    });
    fundDeMax?.addEventListener('change', (e) => {
        fundFilterDeMax = e.target.value || '';
        syncFundFilterPrefs();
        filterAndRenderCards(searchInput.value.toLowerCase().trim());
    });

    intervalFilter?.addEventListener('change', (e) => {
        const val = e.target.value;
        currentIntervalFilter = ['All', '1D', '1W', '1M'].includes(val) ? val : 'All';
        savePref(UI_KEYS.intervalFilter, currentIntervalFilter);
        if (currentIntervalFilter !== 'All' && ALLOWED_INTERVAL.has(currentIntervalFilter)) {
            currentChartInterval = currentIntervalFilter;
            savePref(UI_KEYS.chartInterval, currentChartInterval);
            if (chartIntervalToggle) {
                chartIntervalToggle.querySelectorAll('.interval-toggle-btn').forEach(b => {
                    b.classList.toggle('active', b.dataset.interval === currentChartInterval);
                });
            }
            updateChartTitle();
        }
        filterAndRenderCards(searchInput.value.toLowerCase().trim());
    });

    chartMetricSelect?.addEventListener('change', (e) => {
        const val = sanitizeChartMetric(e.target.value);
        currentChartMetric = val;
        if (chartMetricSelect.value !== val) chartMetricSelect.value = val;
        savePref(UI_KEYS.chartMetric, currentChartMetric);
        syncChartIntervalToggleVisibility();
        updateChartTitle();
        filterAndRenderCards(searchInput.value.toLowerCase().trim());
    });

    expandAllBtn?.addEventListener('click', () => toggleExpandAll());

    function toggleExpandAll(forceCollapse) {
        const cards = resultsGrid.querySelectorAll('.ticker-card');
        if (cards.length === 0) return;
        const shouldCollapse = (typeof forceCollapse === 'boolean')
            ? forceCollapse
            : Array.from(cards).some(c => !c.classList.contains('collapsed'));
        cards.forEach(card => {
            const ticker = card.dataset.ticker || '';
            card.classList.toggle('collapsed', shouldCollapse);
            if (ticker) {
                if (shouldCollapse) collapsedCards.add(ticker);
                else collapsedCards.delete(ticker);
            }
        });
        persistCollapsed();
    }

    // Chart interval toggle
    chartIntervalToggle.addEventListener('click', (e) => {
        const btn = e.target.closest('.interval-toggle-btn');
        if (!btn) return;
        const interval = btn.dataset.interval;
        if (!ALLOWED_INTERVAL.has(interval) || interval === currentChartInterval) return;

        currentChartInterval = interval;
        savePref(UI_KEYS.chartInterval, currentChartInterval);

        chartIntervalToggle.querySelectorAll('.interval-toggle-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        updateChartTitle();
        const term = searchInput.value.toLowerCase().trim();
        filterAndRenderCards(term);
    });

    function syncChartIntervalToggleVisibility() {
        if (!chartIntervalToggle) return;
        const hide = FUND_CHART_METRICS.has(currentChartMetric);
        chartIntervalToggle.style.display = hide ? 'none' : '';
    }

    function updateChartTitle() {
        if (!chartTitle) return;
        const label = METRIC_LABELS[currentChartMetric] || currentChartMetric;
        if (FUND_CHART_METRICS.has(currentChartMetric)) {
            chartTitle.textContent = `${label} dla tickerów`;
        } else {
            chartTitle.textContent = `${label} (${getEffectiveChartInterval()}) dla tickerów`;
        }
    }

    function hasDashboardTickerData() {
        return currentData.some(row => !isTickerHidden(row['Ticker']));
    }

    function getEffectiveChartInterval() {
        if (currentIntervalFilter && currentIntervalFilter !== 'All') {
            return currentIntervalFilter;
        }
        return currentChartInterval;
    }

    function syncChartIntervalToggleUI() {
        if (!chartIntervalToggle) return;
        chartIntervalToggle.querySelectorAll('.interval-toggle-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.interval === currentChartInterval);
        });
    }

    function setChartPanelVisible(visible) {
        if (!chartPanel) return;
        chartPanel.classList.toggle('hidden', !visible);
    }

    function showChartEmptyState(message) {
        if (pcaChartInstance) {
            pcaChartInstance.destroy();
            pcaChartInstance = null;
        }
        if (pcaChartCanvas) pcaChartCanvas.style.display = 'none';
        if (chartEmptyTextEl && message) chartEmptyTextEl.textContent = message;
        if (chartEmptyEl) chartEmptyEl.classList.remove('hidden');
    }

    function hideChartEmptyState() {
        if (chartEmptyEl) chartEmptyEl.classList.add('hidden');
        if (pcaChartCanvas) pcaChartCanvas.style.display = '';
    }

    wlFilterToolbar?.addEventListener('click', (e) => {
        const chip = e.target.closest('.filter-chip');
        if (!chip) return;
        if (chip.hasAttribute('data-filter-strategy')) {
            const key = chip.dataset.filterStrategy;
            if (ALLOWED_STRATEGIES.has(key)) {
                signalStrategy = key;
                savePref(UI_KEYS.signalStrategy, signalStrategy);
            }
        } else if (chip.hasAttribute('data-filter-interval')) {
            const iv = chip.dataset.filterInterval;
            if (ALLOWED_SIGNAL_INTERVALS.has(iv)) {
                signalInterval = iv;
                savePref(UI_KEYS.signalInterval, signalInterval);
            }
        } else if (chip.hasAttribute('data-filter-consensus')) {
            const cf = chip.dataset.filterConsensus;
            if (ALLOWED_CONSENSUS_FILTERS.has(cf)) {
                consensusFilter = cf;
                savePref(UI_KEYS.consensusFilter, consensusFilter);
            }
        }
        syncWlFilterChipsUI();
        filterAndRenderCards(searchInput.value.toLowerCase().trim());
    });

    function syncWlFilterChipsUI() {
        if (!wlFilterToolbar) return;
        wlFilterToolbar.querySelectorAll('.filter-chip[data-filter-strategy]').forEach(chip => {
            chip.classList.toggle('active', chip.dataset.filterStrategy === signalStrategy);
        });
        wlFilterToolbar.querySelectorAll('.filter-chip[data-filter-interval]').forEach(chip => {
            chip.classList.toggle('active', chip.dataset.filterInterval === signalInterval);
        });
        wlFilterToolbar.querySelectorAll('.filter-chip[data-filter-consensus]').forEach(chip => {
            chip.classList.toggle('active', chip.dataset.filterConsensus === consensusFilter);
        });
        updateStrategyDescription();
    }

    function strategyLabel(id) {
        if (!id || id === 'all') return 'Wszystkie';
        const meta = (availableSignalStrategies || []).find(s => s.id === id);
        return (meta && meta.label) || STRATEGY_LABEL_FALLBACK[id] || id;
    }

    function updateStrategyDescription() {
        if (!strategyDescriptionEl) return;
        const isToolbarVisible = wlFilterToolbar && !wlFilterToolbar.hidden;
        if (!isToolbarVisible) {
            strategyDescriptionEl.hidden = true;
            strategyDescriptionEl.textContent = '';
            return;
        }
        const desc = STRATEGY_DESCRIPTIONS[signalStrategy] || STRATEGY_DESCRIPTIONS.all;
        const label = strategyLabel(signalStrategy);
        strategyDescriptionEl.hidden = false;
        strategyDescriptionEl.innerHTML = '';
        const strong = document.createElement('strong');
        strong.textContent = label + ':';
        strategyDescriptionEl.appendChild(strong);
        strategyDescriptionEl.appendChild(document.createTextNode(' ' + desc));
        if (consensusFilter && consensusFilter !== 'all') {
            strategyDescriptionEl.appendChild(document.createTextNode(
                ` Consensus: pokazuję tylko tickery z przewagą „${consensusFilter}” na interwale ${signalInterval}.`
            ));
        }
    }

    function updateStrategyEmptyBanner(filteredCount) {
        if (!strategyEmptyBannerEl) return;
        const isToolbarVisible = wlFilterToolbar && !wlFilterToolbar.hidden;
        const hasSignalFilter = signalStrategy && signalStrategy !== 'all';
        const hasConsensusFilter = consensusFilter && consensusFilter !== 'all';
        if (isToolbarVisible && (hasSignalFilter || hasConsensusFilter) && filteredCount === 0 && currentData.length > 0) {
            const label = strategyLabel(signalStrategy);
            const consensusText = hasConsensusFilter ? ` oraz consensus „${consensusFilter}”` : '';
            strategyEmptyBannerEl.hidden = false;
            strategyEmptyBannerEl.textContent =
                `Brak tickerów spełniających strategię „${label}”${consensusText} dla interwału ${signalInterval}. ` +
                `Spróbuj inny interwał (D/W/M) lub inną strategię — to nie błąd, po prostu żaden ticker w tej dacie nie ma sygnału Buy/Strong Buy w tej kombinacji.`;
        } else {
            strategyEmptyBannerEl.hidden = true;
            strategyEmptyBannerEl.textContent = '';
        }
    }

    function intervalCodeForSignal() {
        return signalInterval === 'W' ? '1W' : signalInterval === 'M' ? '1M' : '1D';
    }

    function rowSignalForStrategy(row, strategyId) {
        if (!row || !strategyId) return '';
        return (row[`Computed_Signal_${strategyId}`] || '').toLowerCase().trim();
    }

    function tickerHasBuySignalForStrategy(rows, strategyId) {
        const targetIv = intervalCodeForSignal();
        return (rows || []).some(r => {
            const iv = (r?.['Interval'] || '').trim().toUpperCase();
            if (iv && iv !== targetIv) return false;
            return BUY_SIGNALS.has(rowSignalForStrategy(r, strategyId));
        });
    }

    function consensusForRows(rows) {
        const targetIv = intervalCodeForSignal();
        const row = (rows || []).find(r => (r?.['Interval'] || '').trim().toUpperCase() === targetIv);
        if (!row) {
            return { direction: 'none', bullish: 0, bearish: 0, neutral: 0, total: 0, score: 0 };
        }
        let bullish = 0;
        let bearish = 0;
        let neutral = 0;
        ALL_STRATEGY_IDS.forEach(id => {
            const sig = rowSignalForStrategy(row, id);
            if (BUY_SIGNALS.has(sig)) bullish += 1;
            else if (SELL_SIGNALS.has(sig)) bearish += 1;
            else if (sig === 'neutral') neutral += 1;
        });
        const total = bullish + bearish + neutral;
        let direction = 'none';
        if (total > 0) {
            if (bullish > bearish) direction = 'bullish';
            else if (bearish > bullish) direction = 'bearish';
            else direction = 'neutral';
        }
        return { direction, bullish, bearish, neutral, total, score: bullish - bearish };
    }

    function tickerMatchesConsensus(rows) {
        if (!consensusFilter || consensusFilter === 'all') return true;
        return consensusForRows(rows).direction === consensusFilter;
    }

    function consensusLabel(c) {
        if (!c || c.total === 0 || c.direction === 'none') return '';
        if (c.direction === 'bullish') return `${c.bullish}/${ALL_STRATEGY_IDS.length} bullish`;
        if (c.direction === 'bearish') return `${c.bearish}/${ALL_STRATEGY_IDS.length} bearish`;
        return `${c.neutral}/${ALL_STRATEGY_IDS.length} neutral`;
    }

    function flattenDashboardTickers(tickerEntries) {
        const rows = [];
        (tickerEntries || []).forEach(entry => {
            const ticker = entry.ticker || '';
            const companyName = entry.company_name || ticker;
            const exchange = entry.exchange || '';
            const fundamentals = entry.fundamentals || {};
            const intervals = entry.intervals || {};
            Object.entries(intervals).forEach(([interval, bucket]) => {
                const base = bucket?.row ? { ...bucket.row } : {
                    Ticker: ticker,
                    Company_Name: companyName,
                    Exchange: exchange,
                    Interval: interval,
                    Scrape_Status: '',
                };
                base.Ticker = ticker;
                base.Company_Name = base.Company_Name || companyName;
                base.Exchange = base.Exchange || exchange;
                base.Interval = base.Interval || interval;
                base.In_Config = true;
                base.Config_Match = ticker;
                base.Config_Status = 'exact';
                base.Config_Candidates = [];
                if (bucket?.last_refresh) {
                    base.Last_Refresh = bucket.last_refresh;
                }
                Object.assign(base, fundamentals);
                rows.push(base);
            });
        });
        return rows;
    }

    async function fetchDashboard() {
        showLoading();
        hideError();
        try {
            const res = await fetch('/api/dashboard');
            if (!res.ok) throw new Error('Nie udało się pobrać dashboardu.');
            const data = await res.json();
            currentData = Array.isArray(data.data)
                ? data.data
                : flattenDashboardTickers(data.tickers);
            configTickerCount = Number.isFinite(Number(data.config_ticker_count))
                ? Number(data.config_ticker_count)
                : new Set(currentData.map(r => r['Ticker'] || '')).size;
            if (Array.isArray(data.signal_strategies) && data.signal_strategies.length) {
                availableSignalStrategies = data.signal_strategies;
            }
            if (currentDateTitle) currentDateTitle.textContent = 'Dashboard';
            if (wlFilterToolbar) {
                wlFilterToolbar.hidden = currentData.length === 0;
            }
            syncWlFilterChipsUI();
            searchInput.value = '';
            filterAndRenderCards('');
            hideLoading();
        } catch (e) {
            console.error(e);
            showError(e.message || 'Błąd pobierania dashboardu.');
            hideLoading();
        }
    }

    function parseRefreshTimestamp(raw) {
        if (!raw) return NaN;
        const s = String(raw).trim();
        const withTime = s.match(/^(\d{4}-\d{2}-\d{2})[ _](\d{2}[:-]\d{2}[:-]\d{2})/);
        if (withTime) {
            return Date.parse(`${withTime[1]}T${withTime[2].replace(/-/g, ':')}`);
        }
        return Date.parse(`${s.slice(0, 10)}T12:00:00`);
    }

    function cardIsStale(rows) {
        let newest = NaN;
        (rows || []).forEach(r => {
            const ts = parseRefreshTimestamp(r['Last_Refresh']);
            if (Number.isFinite(ts)) newest = Number.isFinite(newest) ? Math.max(newest, ts) : ts;
        });
        if (!Number.isFinite(newest)) return true;
        return (Date.now() - newest) / 3_600_000 > 24;
    }

    function getTickerFundRow(rows) {
        return (rows || []).find(r => r['Fund_PE'] != null || r['Fund_ROE'] != null || r['Fund_FCF'] != null
            || (r['Fund_Source'] && String(r['Fund_Source']).toLowerCase() !== 'none'))
            || (rows && rows[0]) || null;
    }

    function getCompositeVerdict(rows) {
        const row = (rows || []).find(r => r['Composite_Verdict']) || (rows && rows[0]);
        return String(row?.['Composite_Verdict'] || '').toLowerCase();
    }

    function getCompositeScore(rows) {
        const row = (rows || []).find(r => r['Composite_Score'] != null && r['Composite_Score'] !== '')
            || (rows && rows[0]);
        const n = Number(row?.['Composite_Score']);
        return Number.isFinite(n) ? n : NaN;
    }

    function passesFundFilters(rows) {
        const fund = getTickerFundRow(rows);
        if (!fund) return true;
        if (fundFilterPeMax) {
            const pe = extractNumericField(fund, 'Fund_PE');
            const maxPe = Number(fundFilterPeMax);
            if (!Number.isFinite(pe) || pe > maxPe) return false;
        }
        if (fundFilterRoeMin) {
            let roe = extractNumericField(fund, 'Fund_ROE');
            if (Number.isFinite(roe) && Math.abs(roe) <= 1) roe *= 100;
            const minRoe = Number(fundFilterRoeMin);
            if (!Number.isFinite(roe) || roe < minRoe) return false;
        }
        if (fundFilterFcfPositive) {
            const fcf = extractNumericField(fund, 'Fund_FCF');
            if (!Number.isFinite(fcf) || fcf <= 0) return false;
        }
        if (fundFilterDeMax) {
            const de = extractNumericField(fund, 'Fund_DE');
            const maxDe = Number(fundFilterDeMax);
            if (!Number.isFinite(de) || de > maxDe) return false;
        }
        return true;
    }

    function formatCompositeTooltip(row) {
        if (!row) return '';
        const score = row['Composite_Score'];
        const bFund = row['Composite_Breakdown_Fund'];
        const bTech = row['Composite_Breakdown_Tech'];
        const bCons = row['Composite_Breakdown_Consensus'];
        const flags = Array.isArray(row['Composite_Flags']) ? row['Composite_Flags'] : [];
        let tip = `Composite: ${score != null ? score : '—'}`;
        tip += `\nFund: ${bFund != null ? bFund : '—'} · Tech 1W: ${bTech != null ? bTech : '—'} · Consensus D/W/M: ${bCons != null ? bCons : '—'}`;
        if (flags.length) tip += `\nFlagi: ${flags.join(', ')}`;
        return tip;
    }

    function addCompositeVerdictBadge(cardClone, rows) {
        const badge = cardClone.querySelector('.composite-verdict-badge');
        if (!badge) return;
        const row = (rows || []).find(r => r['Composite_Verdict']) || rows[0];
        const verdict = String(row?.['Composite_Verdict'] || '').toLowerCase();
        if (!verdict) {
            badge.hidden = true;
            badge.textContent = '';
            badge.className = 'composite-verdict-badge wl-badge';
            badge.removeAttribute('title');
            return;
        }
        badge.hidden = false;
        badge.className = `composite-verdict-badge wl-badge verdict-${verdict}`;
        badge.textContent = verdict === 'kup' ? 'Kup' : (verdict === 'unikaj' ? 'Unikaj' : 'Obserwuj');
        badge.title = formatCompositeTooltip(row);
    }

    function cmpVerdictGroups(aTicker, bTicker, groupedData) {
        const av = getCompositeVerdict(groupedData[aTicker]);
        const bv = getCompositeVerdict(groupedData[bTicker]);
        const ar = VERDICT_SORT_RANK[av] ?? 99;
        const br = VERDICT_SORT_RANK[bv] ?? 99;
        if (ar !== br) return ar - br;
        const as = getCompositeScore(groupedData[aTicker]);
        const bs = getCompositeScore(groupedData[bTicker]);
        if (Number.isFinite(as) && Number.isFinite(bs) && as !== bs) return bs - as;
        return aTicker.localeCompare(bTicker);
    }

    function cmpCompositeDesc(aTicker, bTicker, groupedData) {
        const as = getCompositeScore(groupedData[aTicker]);
        const bs = getCompositeScore(groupedData[bTicker]);
        const aOk = Number.isFinite(as);
        const bOk = Number.isFinite(bs);
        if (aOk && bOk && as !== bs) return bs - as;
        if (aOk !== bOk) return aOk ? -1 : 1;
        return cmpVerdictGroups(aTicker, bTicker, groupedData);
    }

    function filterAndRenderCards(searchTerm) {
        // Odfiltruj tickery ukryte po rename (zachowujemy wiersze w CSV, ale
        // w UI nie chcemy widzieć już starej nazwy).
        let filteredData = currentData.filter(row => !isTickerHidden(row['Ticker']));
        if (currentIntervalFilter && currentIntervalFilter !== 'All') {
            filteredData = filteredData.filter(row => (row['Interval'] || '') === currentIntervalFilter);
        }
        const hiddenCount = currentData.length - currentData.filter(row => !isTickerHidden(row['Ticker'])).length;
        const hiddenTickerCount = new Set(
            currentData
                .filter(row => isTickerHidden(row['Ticker']))
                .map(row => row['Ticker'] || '')
                .filter(Boolean)
        ).size;
        if (searchTerm) {
            filteredData = filteredData.filter(row => {
                const ticker = (row['Ticker'] || '').toLowerCase();
                const company = (row['Company_Name'] || '').toLowerCase();
                return ticker.includes(searchTerm) || company.includes(searchTerm);
            });
        }

        if (signalStrategy && signalStrategy !== 'all') {
            const byTicker = new Map();
            filteredData.forEach(row => {
                const t = row['Ticker'] || '';
                if (!byTicker.has(t)) byTicker.set(t, []);
                byTicker.get(t).push(row);
            });
            const allowedTickers = new Set();
            byTicker.forEach((rows, ticker) => {
                if (tickerHasBuySignalForStrategy(rows, signalStrategy)) {
                    allowedTickers.add(ticker);
                }
            });
            filteredData = filteredData.filter(row => allowedTickers.has(row['Ticker'] || ''));
        }

        if (consensusFilter && consensusFilter !== 'all') {
            const byTicker = new Map();
            filteredData.forEach(row => {
                const t = row['Ticker'] || '';
                if (!byTicker.has(t)) byTicker.set(t, []);
                byTicker.get(t).push(row);
            });
            const allowedTickers = new Set();
            byTicker.forEach((rows, ticker) => {
                if (tickerMatchesConsensus(rows)) {
                    allowedTickers.add(ticker);
                }
            });
            filteredData = filteredData.filter(row => allowedTickers.has(row['Ticker'] || ''));
        }

        if (currentVerdictFilter && currentVerdictFilter !== 'all') {
            const byTicker = new Map();
            filteredData.forEach(row => {
                const t = row['Ticker'] || '';
                if (!byTicker.has(t)) byTicker.set(t, []);
                byTicker.get(t).push(row);
            });
            const allowedTickers = new Set();
            byTicker.forEach((rows, ticker) => {
                if (getCompositeVerdict(rows) === currentVerdictFilter) {
                    allowedTickers.add(ticker);
                }
            });
            filteredData = filteredData.filter(row => allowedTickers.has(row['Ticker'] || ''));
        }

        if (fundFilterPeMax || fundFilterRoeMin || fundFilterFcfPositive || fundFilterDeMax) {
            const byTicker = new Map();
            filteredData.forEach(row => {
                const t = row['Ticker'] || '';
                if (!byTicker.has(t)) byTicker.set(t, []);
                byTicker.get(t).push(row);
            });
            const allowedTickers = new Set();
            byTicker.forEach((rows, ticker) => {
                if (passesFundFilters(rows)) allowedTickers.add(ticker);
            });
            filteredData = filteredData.filter(row => allowedTickers.has(row['Ticker'] || ''));
        }

        const distinctTickers = new Set(filteredData.map(r => r['Ticker'] || '')).size;
        const totalDistinctTickers = new Set(currentData.map(r => r['Ticker'] || '')).size;
        updateStrategyEmptyBanner(distinctTickers);

        recordCount.textContent = '';
        const visibleTickers = new Set(filteredData.map(r => r['Ticker'] || '')).size;
        const configTickers = configTickerCount || new Set(currentData.map(r => r['Ticker'] || '')).size;
        recordCount.appendChild(document.createTextNode(
            `${visibleTickers} tickerów / ${filteredData.length} wierszy (z ${configTickers} w konfiguracji)`
        ));
        if (hiddenCount > 0) {
            const hiddenInfo = hiddenTickerCount > 0
                ? `${hiddenTickerCount} tickerów / ${hiddenCount} wierszy`
                : `${hiddenCount} wierszy`;
            recordCount.appendChild(document.createTextNode(
                ` · ${hiddenInfo} ukrytych po zmianie nazwy `
            ));
            const link = document.createElement('a');
            link.href = '#';
            link.textContent = '(pokaż)';
            link.className = 'record-count-action';
            link.addEventListener('click', (ev) => {
                ev.preventDefault();
                Object.keys(renamedHidden).forEach(k => delete renamedHidden[k]);
                persistRenamedHidden();
                filterAndRenderCards(searchInput?.value?.toLowerCase().trim() || '');
            });
            recordCount.appendChild(link);
        }
        renderCards(filteredData);
        renderChart(filteredData);
        if (typeof syncRepairBtnVisibility === 'function') {
            try { syncRepairBtnVisibility(); } catch (e) { /* repair UI may not be wired yet */ }
        }
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
        if (fieldName === 'PCA_Value' || fieldName === 'PCA_Values') {
            const { valText } = parsePCA(raw || row['PCA_Values'] || '');
            return parsePolishDecimal(valText);
        }
        if (typeof raw === 'string' && raw.includes('(')) {
            return parsePolishDecimal(String(raw).split('(')[0]);
        }
        return parsePolishDecimal(raw);
    }

    function getGroupSortValue(rows, fieldName) {
        const targetRow = rows.find(r => r['Interval'] === currentChartInterval) || rows[0];
        if (!targetRow) return NaN;
        return extractNumericField(targetRow, fieldName);
    }

    // Process and Render Chart
    function renderChart(dataRows) {
        if (!hasDashboardTickerData()) {
            setChartPanelVisible(false);
            showChartEmptyState('');
            return;
        }

        setChartPanelVisible(true);

        const field = METRIC_TO_FIELD[currentChartMetric] || 'PCA_Value';
        const useFund = FUND_CHART_METRICS.has(currentChartMetric);
        const chartInterval = getEffectiveChartInterval();
        const filteredRows = dataRows.filter(r => {
            if (useFund) return true;
            return r['Interval'] === chartInterval;
        });

        const byTicker = new Map();
        filteredRows.forEach(row => {
            const t = row['Ticker'] || '';
            if (!byTicker.has(t)) byTicker.set(t, row);
        });

        const chartData = [];
        byTicker.forEach((row, ticker) => {
            let numVal = NaN;
            let color = null;
            if (currentChartMetric === 'pca') {
                const { valText, colorHex } = parsePCA(row['PCA_Values'] || row['PCA_Value'] || '');
                numVal = parsePolishDecimal(valText);
                color = colorHex;
            } else {
                numVal = extractNumericField(row, field);
            }
            if (!Number.isFinite(numVal)) return;
            chartData.push({ ticker, value: numVal, color });
        });

        chartData.sort((a, b) => b.value - a.value);

        const metricLabel = METRIC_LABELS[currentChartMetric] || currentChartMetric;
        const intervalSuffix = useFund ? '' : ` (${chartInterval})`;

        if (chartData.length === 0) {
            const emptyMsg = useFund
                ? `Brak danych ${metricLabel} dla widocznych tickerów.`
                : `Brak danych ${metricLabel} (${chartInterval}) dla widocznych tickerów.`;
            showChartEmptyState(emptyMsg);
            return;
        }

        hideChartEmptyState();

        const values = chartData.map(d => d.value);
        const median = values.slice().sort((a, b) => a - b)[Math.floor(values.length / 2)] || 0;

        const bgColors = chartData.map(d => {
            if (currentChartMetric === 'pca' && d.color) return d.color;
            if (d.value >= median) return 'rgba(16, 185, 129, 0.75)';
            return 'rgba(239, 68, 68, 0.75)';
        });

        if (pcaChartInstance) pcaChartInstance.destroy();

        const ctx = pcaChartCanvas.getContext('2d');
        Chart.defaults.color = '#94a3b8';
        Chart.defaults.font.family = 'Inter';

        pcaChartInstance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: chartData.map(d => d.ticker),
                datasets: [{
                    label: `${metricLabel}${intervalSuffix}`,
                    data: chartData.map(d => d.value),
                    backgroundColor: bgColors,
                    borderRadius: 4,
                    borderWidth: 0,
                    barPercentage: 0.6,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(15, 17, 21, 0.9)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: 'rgba(255, 255, 255, 0.1)',
                        borderWidth: 1,
                        padding: 12,
                        displayColors: false,
                        callbacks: {
                            label: (context) => `${metricLabel}: ${context.parsed.y}`,
                        },
                    },
                },
                scales: {
                    y: {
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        border: { dash: [4, 4], display: false },
                    },
                    x: {
                        grid: { display: false },
                        border: { display: false },
                        ticks: { maxRotation: 45, minRotation: 45 },
                    },
                },
            },
        });
    }

    /** Polskie / TV: "61,33", "1 234,56", "1 234,56" (NBSP), "−3,88" (unicode minus) */
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

    // Get PCA value for a ticker group (based on current chart interval) for sorting.
    // Returns NaN if there's no value to sort by — sorters treat NaN as "na dół".
    function getGroupPCA(rows) {
        const targetRow = rows.find(r => r['Interval'] === currentChartInterval);
        if (!targetRow || !targetRow['PCA_Values']) return NaN;
        const { valText } = parsePCA(targetRow['PCA_Values']);
        const num = parsePolishDecimal(valText);
        return Number.isFinite(num) ? num : NaN;
    }

    function cmpPcaDesc(a, b) {
        const aNaN = Number.isNaN(a);
        const bNaN = Number.isNaN(b);
        if (aNaN && bNaN) return 0;
        if (aNaN) return 1;
        if (bNaN) return -1;
        return b - a;
    }

    function cmpPcaAsc(a, b) {
        const aNaN = Number.isNaN(a);
        const bNaN = Number.isNaN(b);
        if (aNaN && bNaN) return 0;
        if (aNaN) return 1;
        if (bNaN) return -1;
        return a - b;
    }

    function signalRank(signal) {
        const s = String(signal || '').toLowerCase().trim();
        if (s === 'strong buy') return 5;
        if (s === 'buy') return 4;
        if (s === 'neutral') return 3;
        if (s === 'sell') return 2;
        if (s === 'strong sell') return 1;
        return 0;
    }

    function groupCurrentIntervalRow(rows) {
        return (rows || []).find(r => (r['Interval'] || '').toUpperCase() === currentChartInterval)
            || (rows || [])[0]
            || null;
    }

    function groupMissingCount(rows) {
        return (rows || []).reduce((sum, row) => {
            if (Array.isArray(row['Missing_Indicators'])) return sum + row['Missing_Indicators'].length;
            if (row['All_Indicators_Missing'] === true) return sum + 3;
            return sum;
        }, 0);
    }

    function groupDataStatusRank(rows) {
        const rs = rows || [];
        if (getConfigIssue(rs)) return 0;
        if (rs.some(r => (r['Scrape_Status'] || '').toUpperCase() === 'SKIPPED')) return 1;
        if (rs.some(r => (r['Scrape_Status'] || '').toUpperCase() === 'NO_DATA') || rs.every(r => r['All_Indicators_Missing'] === true)) return 2;
        if (groupMissingCount(rs) > 0) return 3;
        return 4;
    }

    function groupSignalRank(rows) {
        const target = groupCurrentIntervalRow(rows);
        if (!target) return 0;
        const ids = signalStrategy && signalStrategy !== 'all'
            ? [signalStrategy]
            : ALL_STRATEGY_IDS;
        return Math.max(...ids.map(id => signalRank(target[`Computed_Signal_${id}`])), 0);
    }

    function cmpConsensusGroups(aTicker, bTicker, groupedData, mode) {
        const a = consensusForRows(groupedData[aTicker] || []);
        const b = consensusForRows(groupedData[bTicker] || []);
        const aPrimary = mode === 'bearish' ? a.bearish : a.bullish;
        const bPrimary = mode === 'bearish' ? b.bearish : b.bullish;
        if (aPrimary !== bPrimary) return bPrimary - aPrimary;
        const aAbs = Math.abs(a.score);
        const bAbs = Math.abs(b.score);
        if (aAbs !== bAbs) return bAbs - aAbs;
        if (a.total !== b.total) return b.total - a.total;
        return aTicker.localeCompare(bTicker);
    }

    function cmpDataStatusGroups(aTicker, bTicker, groupedData) {
        const aRows = groupedData[aTicker] || [];
        const bRows = groupedData[bTicker] || [];
        const aStatus = groupDataStatusRank(aRows);
        const bStatus = groupDataStatusRank(bRows);
        if (aStatus !== bStatus) return aStatus - bStatus;

        const aMissing = groupMissingCount(aRows);
        const bMissing = groupMissingCount(bRows);
        if (aMissing !== bMissing) return bMissing - aMissing;

        const sigDiff = groupSignalRank(bRows) - groupSignalRank(aRows);
        if (sigDiff !== 0) return sigDiff;

        const pcaDiff = cmpPcaDesc(getGroupPCA(aRows), getGroupPCA(bRows));
        if (pcaDiff !== 0) return pcaDiff;

        return aTicker.localeCompare(bTicker);
    }

    // Build compact summary pills for collapsed header
    function buildSummaryPills(rows) {
        const sortOrder = {"1D": 1, "1W": 2, "1M": 3};
        const sorted = [...rows].sort((a,b) => (sortOrder[a['Interval']] || 99) - (sortOrder[b['Interval']] || 99));
        
        const pills = [];
        sorted.forEach(row => {
            const interval = row['Interval'] || '?';
            const htsTrend = row['HTS Panel_Trend'] || '';
            const htsCross = row['HTS Panel_Cross'] || '';
            const pcaRaw = row['PCA_Values'] || '';
            const { valText, colorHex } = parsePCA(pcaRaw);
            
            // Determine trend direction
            let trendDir = '';
            let trendClass = '';
            if (htsTrend.toLowerCase().includes('wzrostowy') || htsTrend.toLowerCase() === 'up') {
                trendDir = '▲';
                trendClass = 'up';
            } else if (htsTrend.toLowerCase().includes('spadkowy') || htsTrend.toLowerCase() === 'down') {
                trendDir = '▼';
                trendClass = 'down';
            }
            
            // Cross indicator: colored dot (bull/bear), avoids emoji rendering issues
            let crossDotClass = '';
            if (htsCross.toLowerCase().includes('bull')) crossDotClass = 'pill-bull';
            else if (htsCross.toLowerCase().includes('bear')) crossDotClass = 'pill-bear';

            // PCA color dot
            const dotColor = colorHex || '#555';
            const pcaDisplay = valText !== '--' ? valText : '';

            const safeDotColor = sanitizeCssColor(dotColor);
            let pillHTML = `<span class="summary-pill">`;
            pillHTML += `<span class="pill-label">${escapeHtml(interval)}</span>`;
            if (pcaDisplay) {
                pillHTML += `<span class="pill-dot" style="background:${safeDotColor}; box-shadow: 0 0 4px ${safeDotColor};"></span>`;
                pillHTML += `<span>${escapeHtml(pcaDisplay)}</span>`;
            }
            if (trendDir) {
                pillHTML += `<span class="pill-trend ${trendClass}">${escapeHtml(trendDir)}</span>`;
            }
            if (crossDotClass) {
                const crossLabel = crossDotClass === 'pill-bull' ? 'Bull cross' : 'Bear cross';
                pillHTML += `<span class="${crossDotClass}" title="${crossLabel}" aria-label="${crossLabel}"></span>`;
            }
            pillHTML += `</span>`;
            
            pills.push(pillHTML);
        });
        
        return pills.join('');
    }

    function pickBestCompanyName(rows, ticker) {
        const tickerU = String(ticker || '').trim().toUpperCase();
        const candidates = (rows || [])
            .map(r => String(r['Company_Name'] || '').trim())
            .filter(v => v && v !== 'Nieznana' && v !== '—');

        let best = '';
        for (const c of candidates) {
            if (c.toUpperCase() === tickerU) continue;
            if (!best || c.length > best.length) best = c;
        }
        if (best) return best;

        const any = candidates[0] || '';
        if (!any || any.toUpperCase() === tickerU) return '—';
        return any;
    }

    function pickBestExchange(rows, ticker) {
        // Najpierw niepuste wartości z kolumny Exchange w wierszach.
        const fromRows = (rows || [])
            .map(r => String(r['Exchange'] || '').trim().toUpperCase())
            .filter(Boolean);
        if (fromRows.length) return fromRows[0];
        // Fallback: prefix w tickerze, np. "GPW:ATC" → "GPW".
        const t = String(ticker || '');
        if (t.includes(':')) {
            return t.split(':', 1)[0].trim().toUpperCase();
        }
        return '';
    }

    function isConfigPresentValue(value) {
        return value === true || String(value || '').toLowerCase() === 'true';
    }

    function getConfigIssue(rows) {
        const row = (rows || []).find(r => !isConfigPresentValue(r['In_Config']));
        if (!row) return null;
        const candidates = Array.isArray(row['Config_Candidates']) ? row['Config_Candidates'] : [];
        return {
            status: String(row['Config_Status'] || 'unknown'),
            match: String(row['Config_Match'] || ''),
            candidates,
        };
    }

    function addConfigIssueBanner(summaryContainer, ticker, issue) {
        if (!summaryContainer || !issue) return;
        const banner = document.createElement('span');
        banner.className = 'config-stale-banner';
        const candidates = issue.candidates || [];
        const oneCandidate = candidates.length === 1 ? String(candidates[0]?.ticker || '').trim() : '';
        const statusLabel = issue.status === 'unknown'
            ? 'Symbol z CSV nie jest w konfiguracji'
            : 'Stary symbol z CSV';
        banner.appendChild(document.createTextNode(statusLabel));

        if (oneCandidate) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'inline-action-btn';
            btn.textContent = `Użyj ${oneCandidate}`;
            btn.title = `Ukryj ${ticker} i pobierz ${oneCandidate}`;
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                markTickerRenamed(ticker, oneCandidate);
                showToast({
                    type: 'success',
                    title: 'Używam symbolu z konfiguracji',
                    message: `${ticker} ukryty, pobieram ${oneCandidate}.`,
                });
                filterAndRenderCards(searchInput?.value?.toLowerCase().trim() || '');
                requestRescrapeTicker(oneCandidate, null);
            });
            banner.appendChild(btn);
        } else {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'inline-action-btn';
            btn.textContent = 'Ukryj z widoku';
            btn.title = `Ukryj ${ticker} w tym widoku`;
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                markTickerRenamed(ticker, '');
                filterAndRenderCards(searchInput?.value?.toLowerCase().trim() || '');
            });
            banner.appendChild(btn);
        }
        summaryContainer.prepend(document.createTextNode(' '));
        summaryContainer.prepend(banner);
    }

    // Process and Render Cards
    function renderCards(dataRows) {
        resultsGrid.innerHTML = '';
        if (dataRows.length === 0) {
            const hint = currentData.length === 0
                ? 'Brak danych w dashboardzie — uruchom scraper lub odśwież fundamentale.'
                : 'Dostosuj filtr lub wyszukiwanie, aby zobaczyć wyniki.';
            resultsGrid.innerHTML = `
                <div class="empty-state" style="grid-column: 1 / -1;">
                    <i class="ph ph-magnifying-glass"></i>
                    <div class="empty-title">Brak wyników</div>
                    <div class="empty-hint">${escapeHtml(hint)}</div>
                </div>
            `;
            return;
        }

        hideError();

        // Group rows by Ticker
        const groupedData = {};
        const tickerOrder = [];
        dataRows.forEach(row => {
            const ticker = row['Ticker'] || 'Nieznany';
            if (!groupedData[ticker]) {
                groupedData[ticker] = [];
                tickerOrder.push(ticker);
            }
            groupedData[ticker].push(row);
        });

        // Sort groups based on selected sort mode
        let sortedKeys = [...tickerOrder];
        switch (currentSortMode) {
            case 'data-status':
                sortedKeys.sort((a, b) => cmpDataStatusGroups(a, b, groupedData));
                break;
            case 'pca-desc':
                sortedKeys.sort((a, b) => cmpPcaDesc(getGroupPCA(groupedData[a]), getGroupPCA(groupedData[b])));
                break;
            case 'pca-asc':
                sortedKeys.sort((a, b) => cmpPcaAsc(getGroupPCA(groupedData[a]), getGroupPCA(groupedData[b])));
                break;
            case 'macd-desc':
                sortedKeys.sort((a, b) => cmpPcaDesc(getGroupSortValue(groupedData[a], 'MacD_Line'), getGroupSortValue(groupedData[b], 'MacD_Line')));
                break;
            case 'macd-asc':
                sortedKeys.sort((a, b) => cmpPcaAsc(getGroupSortValue(groupedData[a], 'MacD_Line'), getGroupSortValue(groupedData[b], 'MacD_Line')));
                break;
            case 'pe-desc':
                sortedKeys.sort((a, b) => cmpPcaDesc(getGroupSortValue(groupedData[a], 'Fund_PE'), getGroupSortValue(groupedData[b], 'Fund_PE')));
                break;
            case 'pe-asc':
                sortedKeys.sort((a, b) => cmpPcaAsc(getGroupSortValue(groupedData[a], 'Fund_PE'), getGroupSortValue(groupedData[b], 'Fund_PE')));
                break;
            case 'roe-desc':
                sortedKeys.sort((a, b) => cmpPcaDesc(getGroupSortValue(groupedData[a], 'Fund_ROE'), getGroupSortValue(groupedData[b], 'Fund_ROE')));
                break;
            case 'roe-asc':
                sortedKeys.sort((a, b) => cmpPcaAsc(getGroupSortValue(groupedData[a], 'Fund_ROE'), getGroupSortValue(groupedData[b], 'Fund_ROE')));
                break;
            case 'fcf-desc':
                sortedKeys.sort((a, b) => cmpPcaDesc(getGroupSortValue(groupedData[a], 'Fund_FCF'), getGroupSortValue(groupedData[b], 'Fund_FCF')));
                break;
            case 'fcf-asc':
                sortedKeys.sort((a, b) => cmpPcaAsc(getGroupSortValue(groupedData[a], 'Fund_FCF'), getGroupSortValue(groupedData[b], 'Fund_FCF')));
                break;
            case 'consensus-bullish':
                sortedKeys.sort((a, b) => cmpConsensusGroups(a, b, groupedData, 'bullish'));
                break;
            case 'consensus-bearish':
                sortedKeys.sort((a, b) => cmpConsensusGroups(a, b, groupedData, 'bearish'));
                break;
            case 'verdict-kup-first':
                sortedKeys.sort((a, b) => cmpVerdictGroups(a, b, groupedData));
                break;
            case 'composite-desc':
                sortedKeys.sort((a, b) => cmpCompositeDesc(a, b, groupedData));
                break;
            case 'ticker-asc':
                sortedKeys.sort((a, b) => a.localeCompare(b));
                break;
            case 'ticker-desc':
                sortedKeys.sort((a, b) => b.localeCompare(a));
                break;
            default:
                break; // Keep CSV order
        }

        const columnTemplate = document.getElementById('interval-column-template');

        // Render one card per Ticker
        sortedKeys.forEach(ticker => {
            const rows = groupedData[ticker];
            const skipRow = rows.find(r => (r['Scrape_Status'] || '').toUpperCase() === 'SKIPPED');
            const companyName = pickBestCompanyName(rows, ticker);
            
            // Clone Ticker Card template
            const cardClone = cardTemplate.content.cloneNode(true);
            const cardEl = cardClone.querySelector('.ticker-card');
            cardEl.dataset.ticker = ticker;
            if (cardIsStale(rows)) {
                cardEl.classList.add('card-stale');
            }

            cardClone.querySelector('.ticker-name').textContent = ticker;
            addCompositeVerdictBadge(cardClone, rows);
            const companyEl = cardClone.querySelector('.company-name');
            const hasRealName = companyName && companyName !== '—'
                && companyName.toUpperCase() !== ticker.toUpperCase();
            if (hasRealName) {
                companyEl.textContent = companyName;
                companyEl.title = companyName;
            } else {
                companyEl.textContent = '';
                companyEl.removeAttribute('title');
            }

            const exchangeEl = cardClone.querySelector('.exchange-badge');
            if (exchangeEl) {
                const exch = pickBestExchange(rows, ticker);
                if (exch) {
                    exchangeEl.textContent = exch;
                    exchangeEl.title = `Giełda: ${exch}`;
                    exchangeEl.hidden = false;
                } else {
                    exchangeEl.textContent = '';
                    exchangeEl.hidden = true;
                    exchangeEl.removeAttribute('title');
                }
            }

            // Restore collapsed state from prefs (default = collapsed from template)
            if (!collapsedCards.has(ticker) && collapsedCards.size > 0) {
                cardEl.classList.remove('collapsed');
            }

            const rescrapeBtn = cardClone.querySelector('.card-rescrape-btn');
            const rescrapeMenuBtn = cardClone.querySelector('.card-rescrape-menu-btn');
            const rescrapeMenu = cardClone.querySelector('.card-rescrape-menu');
            const historyBtn = cardClone.querySelector('.card-history-btn');
            const renameBtn = cardClone.querySelector('.card-rename-btn');
            const deleteBtn = cardClone.querySelector('.card-delete-btn');
            if (rescrapeBtn) {
                if (rerunningTickers.has(ticker)) {
                    cardClone.querySelectorAll('.card-rescrape-wrap .card-action-btn').forEach(btn => {
                        btn.classList.add('spinning');
                        btn.disabled = true;
                    });
                }
                rescrapeBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    requestRescrapeTicker(ticker, rescrapeBtn, { indicators: '' });
                });
            }
            wireDropdownMenu(rescrapeMenuBtn, rescrapeMenu, (item) => {
                requestRescrapeTicker(ticker, rescrapeBtn, {
                    indicators: item.dataset.rescrapeIndicators || '',
                });
            });
            if (historyBtn) {
                historyBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openHistoryModal(ticker);
                });
            }
            if (renameBtn) {
                renameBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openRenameModal(ticker);
                });
            }
            if (deleteBtn) {
                deleteBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openDeleteTickerModal(ticker);
                });
            }
            
            // Build summary pills for collapsed view
            const summaryContainer = cardClone.querySelector('.card-summary');
            if (skipRow) {
                cardEl.classList.add('ticker-skipped');
                const err = skipRow['Scrape_Error'] || 'Nie udało się pobrać danych';
                summaryContainer.innerHTML = `<span class="skip-error-banner">⚠ Pominięty ticker</span>`;
                {
                    const ce = cardClone.querySelector('.company-name');
                    ce.textContent = '';
                    ce.removeAttribute('title');
                }
            } else {
                // Traktuj jako "no data" gdy Scrape_Status=NO_DATA LUB backend wykrył brak
                // wszystkich wskaźników we wszystkich wierszach (legacy OK z poprzednich runów).
                const noDataByStatus = rows.find(r => (r['Scrape_Status'] || '').toUpperCase() === 'NO_DATA');
                const everyRowAllMissing = rows.every(r => r['All_Indicators_Missing'] === true);
                const noDataRow = noDataByStatus
                    || (everyRowAllMissing ? rows[0] : null);

                let summaryHtml = buildSummaryPills(rows);
                if (noDataRow) {
                    cardEl.classList.add('ticker-no-data');
                    const baseHint = noDataRow['Scrape_Error'] || 'Brak danych wskaźników na wykresie';
                    const actionHint = 'Sprawdź symbol na TradingView — możesz zmienić nazwę (ołówek) lub pobrać ponownie.';
                    summaryHtml =
                        `<span class="no-data-banner" title="${escapeHtml(baseHint + ' · ' + actionHint)}">` +
                        `⚠ Brak danych` +
                        `<span class="no-data-hint">${escapeHtml(actionHint)}</span>` +
                        `</span> ` +
                        summaryHtml;
                }
                summaryContainer.innerHTML = summaryHtml;
            }

            const configIssue = getConfigIssue(rows);
            if (configIssue) {
                cardEl.classList.add('ticker-config-stale');
                addConfigIssueBanner(summaryContainer, ticker, configIssue);
            }

            const fundRow = rows.find(r => r['Fund_PE'] || r['Fund_Source']) || rows[0];
            const setFund = (sel, val) => {
                const el = cardClone.querySelector(sel);
                if (el) el.textContent = (val && String(val).trim() && String(val).toUpperCase() !== 'N/A') ? val : '—';
            };
            if (fundRow) {
                setFund('.fund-pe', fundRow['Fund_PE']);
                setFund('.fund-pb', fundRow['Fund_PB']);
                setFund('.fund-ev', fundRow['Fund_EV_EBITDA']);
                setFund('.fund-roe', fundRow['Fund_ROE']);
                setFund('.fund-margin', fundRow['Fund_NetMargin']);
                setFund('.fund-de', fundRow['Fund_DE']);
                setFund('.fund-fcf', fundRow['Fund_FCF']);
                const fundUpdated = cardClone.querySelector('.fund-updated-at');
                if (fundUpdated && fundRow['Fund_Updated_At']) {
                    fundUpdated.hidden = false;
                    fundUpdated.textContent = `Fundamentale: ${fundRow['Fund_Updated_At']} · ${fundRow['Fund_Source'] || ''}`;
                }
            }
            
            const badgesContainer = cardClone.querySelector('.watchlist-badges');
            const dRow = rows.find(r => (r['Interval'] || '').toUpperCase() === '1D') || rows[0];
            const wRow = rows.find(r => (r['Interval'] || '').toUpperCase() === '1W');
            const mRow = rows.find(r => (r['Interval'] || '').toUpperCase() === '1M');
            addConsensusBadge(badgesContainer, rows);
            const strategiesToShow = signalStrategy === 'all'
                ? availableSignalStrategies.map(s => s.id)
                : [signalStrategy];
            strategiesToShow.forEach(sid => {
                const col = `Computed_Signal_${sid}`;
                const meta = availableSignalStrategies.find(s => s.id === sid);
                const prefix = strategiesToShow.length > 1 && meta ? `${meta.label} · ` : '';
                if (dRow && dRow[col]) addSignalBadge(badgesContainer, `${prefix}D`, dRow[col]);
                if (wRow && wRow[col]) addSignalBadge(badgesContainer, `${prefix}W`, wRow[col]);
                if (mRow && mRow[col]) addSignalBadge(badgesContainer, `${prefix}M`, mRow[col]);
            });

            // Click header to toggle collapse (persist state)
            const header = cardClone.querySelector('.card-header');
            header.addEventListener('click', (e) => {
                if (e.target.closest('.card-action-btn')) return;
                cardEl.classList.toggle('collapsed');
                if (cardEl.classList.contains('collapsed')) {
                    collapsedCards.add(ticker);
                } else {
                    collapsedCards.delete(ticker);
                }
                persistCollapsed();
            });

            const intervalsContainer = cardClone.querySelector('.intervals-container');

            if (skipRow) {
                const err = skipRow['Scrape_Error'] || '';
                intervalsContainer.innerHTML = `<div class="skip-detail">${escapeHtml(err)}</div>`;
                resultsGrid.appendChild(cardClone);
                return;
            }
            
            // Sort rows by interval (1D -> 1W -> 1M)
            const sortOrder = {"1D": 1, "1W": 2, "1M": 3};
            rows.sort((a,b) => (sortOrder[a['Interval']] || 99) - (sortOrder[b['Interval']] || 99));

            rows.forEach(row => {
                // Clone Interval Column template
                const colClone = columnTemplate.content.cloneNode(true);
                
                colClone.querySelector('.interval-badge').textContent = row['Interval'] || '1D';
                const lastRefreshEl = colClone.querySelector('[data-role="last-refresh"]');
                if (lastRefreshEl) {
                    const lr = row['Last_Refresh'] || '';
                    lastRefreshEl.textContent = lr ? `Ostatnio: ${lr}` : '';
                    lastRefreshEl.title = lr ? `Dane techniczne z ${lr}` : '';
                }

                // Per-interval brakujące wskaźniki — adnotacja z backendu.
                const missing = Array.isArray(row['Missing_Indicators'])
                    ? row['Missing_Indicators'] : [];
                const missingEl = colClone.querySelector('[data-role="interval-missing"]');
                if (missingEl && missing.length > 0) {
                    missingEl.classList.remove('hidden');
                    missingEl.innerHTML =
                        `<span><span class="missing-label">Brak danych:</span>` +
                        ` ${escapeHtml(missing.join(', '))}</span>`;
                }
                // Wyszarz sekcje wskaźników bez danych (po nazwie w .section-title).
                const missingSet = new Set(missing.map(s => String(s).toLowerCase()));
                colClone.querySelectorAll('.indicator-section').forEach(sec => {
                    const title = sec.querySelector('.section-title');
                    const name = (title?.textContent || '').trim().toLowerCase();
                    if (missingSet.has(name)) {
                        sec.classList.add('no-data');
                    }
                });

                // -- HTS Panel --
                const htsTrendNode = colClone.querySelector('.hts-trend');
                setTrendTextAndColor(htsTrendNode, row['HTS Panel_Trend'] || 'Brak');
                
                const htsCrossNode = colClone.querySelector('.hts-cross');
                setCrossTag(htsCrossNode, row['HTS Panel_Cross'] || 'Brak');

                const htsTrendChangeRow = colClone.querySelector('.hts-trend-change-row');
                const htsTrendChangeVal = row['HTS Panel_Trend_Change'] || '';
                if (htsTrendChangeRow) {
                    if (htsTrendChangeVal.trim()) {
                        htsTrendChangeRow.classList.remove('hidden');
                        const tcNode = htsTrendChangeRow.querySelector('.hts-trend-change');
                        if (tcNode) tcNode.innerHTML = parseValueWithColor(htsTrendChangeVal);
                    } else {
                        htsTrendChangeRow.classList.add('hidden');
                    }
                }

                colClone.querySelector('.hts-fast-high').innerHTML = parseValueWithColor(row['HTS Panel_Fast_High']);
                colClone.querySelector('.hts-fast-low').innerHTML = parseValueWithColor(row['HTS Panel_Fast_Low']);
                colClone.querySelector('.hts-slow-high').innerHTML = parseValueWithColor(row['HTS Panel_Slow_High']);
                colClone.querySelector('.hts-slow-low').innerHTML = parseValueWithColor(row['HTS Panel_Slow_Low']);

                // -- MacD Panel --
                const macdTrendNode = colClone.querySelector('.macd-trend');
                setTrendTextAndColor(macdTrendNode, row['MacD_Trend'] || 'Brak');

                const macdCrossNode = colClone.querySelector('.macd-cross');
                setCrossTag(macdCrossNode, row['MacD_Cross'] || 'Brak');

                const macdLine = row['MacD_Line'] || row['MacD_Fast_High'];
                const macdSignal = row['MacD_Signal'] || row['MacD_Slow_Low'];
                const macdCrossVal = row['MacD_Cross_Value'] || '';

                const macdLineNode = colClone.querySelector('.macd-line') || colClone.querySelector('.macd-fast');
                const macdSignalNode = colClone.querySelector('.macd-signal') || colClone.querySelector('.macd-slow');
                if (macdLineNode) macdLineNode.innerHTML = parseValueWithColor(macdLine);
                if (macdSignalNode) macdSignalNode.innerHTML = parseValueWithColor(macdSignal);

                const macdCrossValBox = colClone.querySelector('.macd-cross-value-box');
                const macdCrossValNode = colClone.querySelector('.macd-cross-value');
                if (macdCrossValBox && macdCrossValNode) {
                    if (macdCrossVal.trim()) {
                        macdCrossValBox.classList.remove('hidden');
                        macdCrossValNode.innerHTML = parseValueWithColor(macdCrossVal);
                    } else {
                        macdCrossValBox.classList.add('hidden');
                    }
                }

                // -- PCA Panel --
                const pcaStr = row['PCA_Values'] || '';
                const { valText, colorHex } = parsePCA(pcaStr);
                colClone.querySelector('.pca-value').textContent = valText || 'Brak danych';
                
                if (colorHex) {
                    colClone.querySelector('.pca-color-bar').style.backgroundColor = colorHex;
                    colClone.querySelector('.pca-color-bar').style.boxShadow = pcaColorToGlowShadow(colorHex);
                }
                
                intervalsContainer.appendChild(colClone);
            });

            resultsGrid.appendChild(cardClone);
        });
    }

    // Helpers
    function showLoading() { loadingOverlay.classList.remove('hidden'); }
    function hideLoading() { loadingOverlay.classList.add('hidden'); }
    function showError(msg) { 
        errorMessage.classList.remove('hidden'); 
        errorText.textContent = msg;
    }
    function hideError() { errorMessage.classList.add('hidden'); }

    function addSignalBadge(container, label, signal) {
        const badge = document.createElement('span');
        badge.className = 'wl-badge';
        const signalLower = signal.toLowerCase().trim();
        
        if (signalLower === 'strong buy') {
            badge.classList.add('strong-buy');
        } else if (signalLower === 'buy') {
            badge.classList.add('buy');
        } else if (signalLower === 'neutral') {
            badge.classList.add('neutral');
        } else if (signalLower === 'sell') {
            badge.classList.add('sell');
        } else if (signalLower === 'strong sell') {
            badge.classList.add('strong-sell');
        } else {
            return; // Don't render empty signals
        }
        
        badge.textContent = `${label}: ${signal}`;
        container.appendChild(badge);
    }

    function addConsensusBadge(container, rows) {
        if (!container) return;
        const c = consensusForRows(rows);
        const label = consensusLabel(c);
        if (!label) return;
        const badge = document.createElement('span');
        badge.className = `wl-badge consensus-badge consensus-${c.direction}`;
        badge.textContent = `Consensus ${signalInterval}: ${label}`;
        badge.title = `Bullish ${c.bullish}, bearish ${c.bearish}, neutral ${c.neutral} z ${ALL_STRATEGY_IDS.length} strategii`;
        container.appendChild(badge);
    }

    function setTrendTextAndColor(node, val) {
        val = (val == null ? '' : String(val)).trim();
        node.textContent = val;
        node.className = 'value trend-value'; // reset
        const low = val.toLowerCase();
        if (low.includes('wzrostowy') || low === 'up') {
            node.classList.add('trend-up');
        } else if (low.includes('spadkowy') || low === 'down') {
            node.classList.add('trend-down');
        } else if (low.includes('brak trendu') || low.includes('mieszany')) {
            node.classList.add('trend-neutral');
        } else {
            node.classList.add('trend-neutral');
        }
    }

    function setCrossTag(node, val) {
        val = (val == null ? '' : String(val)).trim();
        let displayVal = val;
        if (val.includes('(')) {
            displayVal = val.split('(')[0].trim();
            node.title = val;
        }

        node.textContent = displayVal;
        node.className = 'value tag'; // reset
        const low = val.toLowerCase();
        if (low.includes('bull')) {
            node.classList.add('bull');
        } else if (low.includes('bear')) {
            node.classList.add('bear');
        } else {
            node.classList.add('neutral');
            node.textContent = 'Brak';
        }
    }

    // Parse values like "74 635,86 (Niebieski)" or "-1 216,62 (color: rgb(0, 255, 0);)"
    function parseValueWithColor(rawStr) {
        if (!rawStr || rawStr === 'NaN' || rawStr === 'undefined') return '--';
        const safeRaw = escapeHtml(rawStr);
        const match = String(rawStr).match(/(.*?)\s*\((.*?)\)/);
        if (match) {
            const val = match[1].trim();
            const colorInfo = match[2].trim();
            let colorSpan = '';

            const rgbMatch = colorInfo.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
            if (rgbMatch) {
                const r = rgbMatch[1];
                const g = rgbMatch[2];
                const b = rgbMatch[3];
                colorSpan = `<span style="display:inline-block; width:8px; height:8px; border-radius:50%; background-color: rgb(${r},${g},${b}); margin-left: 6px; box-shadow: 0 0 5px rgba(${r},${g},${b},0.8);"></span>`;
            } else if (colorInfo.toLowerCase() !== 'brak') {
                let mappedColor = '#fff';
                if (colorInfo.toLowerCase().includes('niebieski')) mappedColor = '#3b82f6';
                if (colorInfo.toLowerCase().includes('zielony')) mappedColor = '#10b981';
                if (colorInfo.toLowerCase().includes('czerwony')) mappedColor = '#ef4444';

                colorSpan = `<span style="display:inline-block; width:8px; height:8px; border-radius:50%; background-color: ${mappedColor}; margin-left: 6px;"></span>`;
            }
            return `${escapeHtml(val)} ${colorSpan}`;
        }
        return safeRaw;
    }

    /** Valid CSS for box-shadow glow; appends "80" to rgb() which is invalid. */
    function pcaColorToGlowShadow(cssColor, blurPx = 10, alpha = 0.5) {
        if (!cssColor) return '';
        const m = String(cssColor).match(/^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$/i);
        if (m) {
            return `0 0 ${blurPx}px rgba(${m[1]}, ${m[2]}, ${m[3]}, ${alpha})`;
        }
        return `0 0 ${blurPx}px ${cssColor}`;
    }

    // Parse PCA value like "61,33 (color: rgb(255, 243, 0);)" lub "(color: rgba(...))"
    function parsePCA(rawStr) {
        if (!rawStr || rawStr === 'Brak danych na wykresie') return { valText: '--', colorHex: null };
        const s = String(rawStr).trim();

        // value (color: rgb(...) lub rgba(...))
        const matchColor = s.match(
            /^(.*?)\s*\(\s*color:\s*(rgba?\([^)]+\))\s*;?\s*\)/i
        );
        if (matchColor) {
            let vt = matchColor[1]?.trim();
            if (/^ok$/i.test(vt)) vt = '--';
            return { valText: vt, colorHex: matchColor[2]?.trim() };
        }

        // value (rgb(...)) / (rgba(...)) bez słowa "color:" — częsty format w eksporcie TV
        const matchBareRgb = s.match(/^(.*?)\s*\(\s*(rgba?\(\s*\d[^)]*\))\s*\)\s*$/i);
        if (matchBareRgb) {
            let vt = matchBareRgb[1]?.trim();
            if (/^ok$/i.test(vt)) vt = '--';
            return { valText: vt, colorHex: matchBareRgb[2]?.trim() };
        }

        // value (ColorName)
        const namedMatch = s.match(/^(.*?)\s*\(([^)]+)\)/);
        if (namedMatch) {
            const inner = namedMatch[2].trim();
            const colorName = inner.toLowerCase();
            let mappedColor = null;
            if (colorName.includes('czerwon')) mappedColor = 'rgb(239, 68, 68)';
            else if (colorName.includes('niebiesk')) mappedColor = 'rgb(59, 130, 246)';
            else if (colorName.includes('zielon')) mappedColor = 'rgb(16, 185, 129)';
            else if (colorName.includes('pomarańcz')) mappedColor = 'rgb(245, 158, 11)';
            else if (/^rgba?\(/i.test(inner)) mappedColor = inner;

            let vt = namedMatch[1]?.trim();
            if (/^ok$/i.test(vt)) vt = '--';
            return {
                valText: vt,
                colorHex: mappedColor
            };
        }

        let vt = s.trim();
        if (/^ok$/i.test(vt)) vt = '--';
        return { valText: vt, colorHex: null };
    }

    // ==========================================
    // CONFIG & SCRAPER PANEL LOGIC
    // ==========================================
    
    let currentConfig = {
        tickers: [],
        intervals: [],
        indicators: [],
        auto_schedule: { enabled: false, hour: 7, minute: 30, run_on_startup: true },
    };
    let statusInterval = null;

    // Elements Config
    const navLinks = document.querySelectorAll('.nav-link');
    const viewPanels = document.querySelectorAll('.view-panel');
    const tickersListEl = document.getElementById('config-tickers-list');
    const tickersCountEl = document.getElementById('tickers-count');
    const newTickerInput = document.getElementById('new-ticker-input');
    const btnAddTicker = document.getElementById('btn-add-ticker');
    const selectAllTickers = document.getElementById('select-all-tickers');
    const intervalsCheckboxes = document.querySelectorAll('.config-interval-cb');
    const indicatorsListEl = document.getElementById('config-indicators-list');
    const newIndicatorInput = document.getElementById('new-indicator-input');
    const btnAddIndicator = document.getElementById('btn-add-indicator');
    const btnSaveConfig = document.getElementById('btn-save-config');
    const configSaveStatus = document.getElementById('config-save-status');
    const autoScheduleEnabled = document.getElementById('auto-schedule-enabled');
    const autoScheduleHour = document.getElementById('auto-schedule-hour');
    const autoScheduleMinute = document.getElementById('auto-schedule-minute');
    const autoScheduleRunOnStartup = document.getElementById('auto-schedule-run-on-startup');

    // Scraper elements
    const statusText = document.getElementById('scraper-status-text');
    const progressContainer = document.getElementById('scraper-progress-container');
    const progressText = document.getElementById('scraper-progress-text');
    const currentTickerLabel = document.getElementById('scraper-current-ticker');
    const progressFill = document.getElementById('scraper-progress-fill');
    const btnRunAll = document.getElementById('btn-run-scraper-all');
    const btnRunNoData = document.getElementById('btn-run-scraper-no-data');
    const btnRunSelected = document.getElementById('btn-run-scraper-selected');
    const btnStopScraper = document.getElementById('btn-stop-scraper');
    const btnRefreshFundamentalsAll = document.getElementById('btn-refresh-fundamentals-all');

    function switchView(targetId) {
        navLinks.forEach(l => {
            l.classList.toggle('active', l.getAttribute('data-target') === targetId);
        });
        viewPanels.forEach(panel => {
            panel.classList.toggle('active', panel.id === targetId);
        });
        savePref(UI_KEYS.activeView, targetId);

        if (targetId === 'config-view') {
            loadConfig();
            pollScraperStatus();
        } else if (targetId === 'dashboard-view') {
            if (statusInterval) {
                clearInterval(statusInterval);
                statusInterval = null;
            }
            fetchDashboard();
        }
    }

    // Tab Navigation
    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = link.getAttribute('data-target');
            if (targetId) switchView(targetId);
        });
    });

    // --- Loading & Rendering Config ---

    async function loadConfig() {
        try {
            const res = await fetch('/api/config');
            if (res.ok) {
                currentConfig = await res.json();
                renderConfigUI();
            }
        } catch (e) {
            console.error("Failed to load config", e);
        }
    }

    function renderConfigUI() {
        // Render Tickers
        tickersCountEl.textContent = currentConfig.tickers.length;
        tickersListEl.innerHTML = '';
        currentConfig.tickers.forEach(t => {
            const safe = escapeHtml(t);
            const item = document.createElement('div');
            item.className = 'ticker-item';
            item.innerHTML = `
                <div class="ticker-item-left">
                    <label class="checkbox-container">
                        <input type="checkbox" class="ticker-select-cb" value="${safe}">
                        <span class="checkmark"></span>
                    </label>
                    <span class="ticker-name-bold" style="font-weight: 500">${safe}</span>
                </div>
                <button class="btn-remove-ticker" data-ticker="${safe}"><i class="ph ph-trash"></i></button>
            `;
            tickersListEl.appendChild(item);
        });

        // Add remove handlers
        document.querySelectorAll('.btn-remove-ticker').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const button = e.target.closest('.btn-remove-ticker');
                const t = button.getAttribute('data-ticker');
                currentConfig.tickers = currentConfig.tickers.filter(x => x !== t);
                renderConfigUI();
            });
        });

        // Ensure "Select All" is unchecked on render
        selectAllTickers.checked = false;

        // Render Intervals
        intervalsCheckboxes.forEach(cb => {
            cb.checked = currentConfig.intervals.includes(cb.value);
        });

        // Render Indicators
        indicatorsListEl.innerHTML = '';
        currentConfig.indicators.forEach(ind => {
            const safe = escapeHtml(ind);
            const tag = document.createElement('span');
            tag.className = 'tag-item';
            tag.innerHTML = `
                ${safe} 
                <button class="tag-remove" data-ind="${safe}"><i class="ph ph-x"></i></button>
            `;
            indicatorsListEl.appendChild(tag);
        });

        document.querySelectorAll('.tag-remove').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const button = e.target.closest('.tag-remove');
                const ind = button.getAttribute('data-ind');
                currentConfig.indicators = currentConfig.indicators.filter(x => x !== ind);
                renderConfigUI();
            });
        });

        const sched = currentConfig.auto_schedule || { enabled: false, hour: 7, minute: 30, run_on_startup: true };
        autoScheduleEnabled.checked = !!sched.enabled;
        autoScheduleHour.value = String(Math.max(0, Math.min(23, parseInt(sched.hour, 10) || 7)));
        autoScheduleMinute.value = String(Math.max(0, Math.min(59, parseInt(sched.minute, 10) || 0)));
        autoScheduleRunOnStartup.checked = sched.run_on_startup !== false;
    }

    // --- Config Actions ---

    btnAddTicker.addEventListener('click', () => {
        const val = newTickerInput.value.trim().toUpperCase();
        if (val && !currentConfig.tickers.includes(val)) {
            currentConfig.tickers.push(val);
            newTickerInput.value = '';
            renderConfigUI();
        }
    });

    newTickerInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') btnAddTicker.click();
    });

    selectAllTickers.addEventListener('change', (e) => {
        const cbs = document.querySelectorAll('.ticker-select-cb');
        cbs.forEach(cb => cb.checked = e.target.checked);
    });

    btnAddIndicator.addEventListener('click', () => {
        const val = newIndicatorInput.value.trim();
        if (val && !currentConfig.indicators.includes(val)) {
            currentConfig.indicators.push(val);
            newIndicatorInput.value = '';
            renderConfigUI();
        }
    });

    newIndicatorInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') btnAddIndicator.click();
    });

    btnSaveConfig.addEventListener('click', async () => {
        // Collect intervals
        const selectedIntervals = Array.from(intervalsCheckboxes)
            .filter(cb => cb.checked).map(cb => cb.value);
        currentConfig.intervals = selectedIntervals;

        currentConfig.auto_schedule = {
            enabled: autoScheduleEnabled.checked,
            hour: Math.max(0, Math.min(23, parseInt(autoScheduleHour.value, 10) || 0)),
            minute: Math.max(0, Math.min(59, parseInt(autoScheduleMinute.value, 10) || 0)),
            run_on_startup: autoScheduleRunOnStartup.checked,
        };

        btnSaveConfig.disabled = true;
        configSaveStatus.textContent = 'Zapisywanie...';
        configSaveStatus.className = 'save-status';

        try {
            const res = await fetch('/api/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentConfig)
            });

            if (res.ok) {
                configSaveStatus.textContent = 'Zapisano pomyślnie!';
                configSaveStatus.classList.add('success');
            } else {
                throw new Error("API błąd");
            }
        } catch (e) {
            configSaveStatus.textContent = 'Błąd zapisywania.';
            configSaveStatus.classList.add('error');
        } finally {
            setTimeout(() => {
                configSaveStatus.textContent = '';
                btnSaveConfig.disabled = false;
            }, 3000);
        }
    });

    // --- Scraper Control ---
    const errorMsgContainer = document.getElementById('scraper-error-msg');
    let previousScraperStatus = 'idle';

    function parseScraperProgress(progressStr) {
        // "45/78 · ticker 15/26 · wsk. 2/3" — pierwsza para to postęp łączny
        if (!progressStr || typeof progressStr !== 'string') return 0;
        const m = progressStr.match(/^(\d+)\s*\/\s*(\d+)/);
        if (!m) return 0;
        const current = parseInt(m[1], 10);
        const total = parseInt(m[2], 10);
        if (!Number.isFinite(current) || !Number.isFinite(total) || total <= 0) return 0;
        return (current / total) * 100;
    }

    function formatScraperProgressLabel(progressStr, currentTicker, currentIndicator) {
        if (currentIndicator && currentTicker) {
            return `${currentTicker} · ${currentIndicator}`;
        }
        if (!progressStr) return currentTicker ? `(${currentTicker})` : '';
        const detail = progressStr.includes('·')
            ? progressStr.split('·').slice(1).join('·').trim()
            : '';
        if (currentTicker && detail) {
            return `${currentTicker} (${detail})`;
        }
        if (currentTicker) return currentTicker;
        return progressStr;
    }

    function formatScraperStartToast(tickers, indicators) {
        const indList = Array.isArray(indicators)
            ? indicators.filter(Boolean)
            : (indicators ? [indicators] : []);
        const tickerLabel = Array.isArray(tickers) && tickers.length === 1
            ? tickers[0]
            : (Array.isArray(tickers) && tickers.length > 1
                ? `${tickers.length} tickerów`
                : 'wszystkie tickery');
        if (indList.length === 1) {
            return `Uruchomiono scraper: ${indList[0]} dla ${tickerLabel}`;
        }
        if (indList.length > 1) {
            return `Uruchomiono scraper: ${indList.join(', ')} dla ${tickerLabel}`;
        }
        return `Uruchomiono scraper dla ${tickerLabel}`;
    }

    function parseIndicatorSelection(raw) {
        const token = String(raw || '').trim();
        if (!token) return [];
        return token.split(',').map(s => s.trim()).filter(Boolean);
    }

    function wireDropdownMenu(toggleBtn, menuEl, onSelect) {
        if (!toggleBtn || !menuEl || toggleBtn.dataset.menuWired === '1') return;
        toggleBtn.dataset.menuWired = '1';
        const close = () => {
            menuEl.classList.add('hidden');
            toggleBtn.setAttribute('aria-expanded', 'false');
        };
        toggleBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const open = menuEl.classList.contains('hidden');
            document.querySelectorAll('.card-rescrape-menu:not(.hidden), .scraper-dropdown-menu:not(.hidden)')
                .forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('[aria-haspopup="true"][aria-expanded="true"]')
                .forEach(el => el.setAttribute('aria-expanded', 'false'));
            if (open) {
                menuEl.classList.remove('hidden');
                toggleBtn.setAttribute('aria-expanded', 'true');
            }
        });
        menuEl.querySelectorAll('[role="menuitem"]').forEach(item => {
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                close();
                onSelect(item);
            });
        });
    }

    if (!window.__dropdownMenuCloseBound) {
        window.__dropdownMenuCloseBound = true;
        document.addEventListener('click', () => {
            document.querySelectorAll('.card-rescrape-menu:not(.hidden), .scraper-dropdown-menu:not(.hidden)')
                .forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('[aria-haspopup="true"][aria-expanded="true"]')
                .forEach(el => el.setAttribute('aria-expanded', 'false'));
        });
    }

    async function pollScraperStatus() {
        if (statusInterval) clearInterval(statusInterval);
        
        const fetchStatus = async () => {
            try {
                const res = await fetch('/api/scraper/status');
                if (res.ok) {
                    const data = await res.json();

                    if (previousScraperStatus === 'running' && data.status === 'done') {
                        fetchDashboard();
                    }
                    previousScraperStatus = data.status;

                    statusText.className = 'status-badge ' + data.status;
                    
                    if (data.status === 'running') {
                        errorMsgContainer.classList.add('hidden');
                        statusText.textContent = 'Pracuje';
                        progressContainer.classList.remove('hidden');
                        btnRunAll.disabled = true;
                        if (btnRunNoData) btnRunNoData.disabled = true;
                        btnRunSelected.disabled = true;
                        btnStopScraper.classList.remove('hidden');
                        
                        progressText.textContent = data.progress || "Uruchamianie...";
                        currentTickerLabel.textContent = formatScraperProgressLabel(
                            data.progress,
                            data.current_ticker || "",
                            data.current_indicator || ""
                        );
                        
                        const pct = parseScraperProgress(data.progress);
                        progressFill.style.width = pct + '%';
                    } else {
                        progressContainer.classList.add('hidden');
                        btnRunAll.disabled = false;
                        if (btnRunNoData) btnRunNoData.disabled = false;
                        btnRunSelected.disabled = false;
                        btnStopScraper.classList.add('hidden');
                        
                        if (data.status === 'done') {
                            statusText.textContent = data.duration_human
                                ? `Zakończono (${data.duration_human})`
                                : 'Zakończono';
                            errorMsgContainer.classList.add('hidden');
                        } else if (data.status === 'error') {
                            statusText.textContent = 'Błąd';
                            const durErr = data.duration_human ? ` [${data.duration_human}]` : '';
                            errorMsgContainer.textContent = (data.error || "Wystąpił nieznany błąd podczas działania.") + durErr;
                            errorMsgContainer.classList.remove('hidden');
                        } else {
                            statusText.textContent = 'Gotowy';
                            errorMsgContainer.classList.add('hidden');
                        }
                    }
                }
            } catch (e) {
                console.error("Status check error", e);
            }
        };

        await fetchStatus();
        statusInterval = setInterval(fetchStatus, 3000); // Check every 3 seconds
    }

    async function startScraper(tickersOverride = [], options = {}) {
        try {
            const payload = { tickers: tickersOverride };
            if (options?.noDataOnly) payload.no_data_only = true;
            if (options?.fresh) payload.fresh = true;
            const indicators = parseIndicatorSelection(options?.indicators);
            if (indicators.length) payload.indicators = indicators;
            const res = await fetch('/api/scraper/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.status === 'started' || data.status === 'already_running') {
                const toastIndicators = data.indicators?.length
                    ? data.indicators
                    : indicators;
                const toastTickers = data.tickers?.length
                    ? data.tickers
                    : tickersOverride;
                if (options?.noDataOnly && Number.isFinite(Number(data.count))) {
                    showToast({
                        type: 'success',
                        title: 'Odświeżanie Brak Danych',
                        message: toastIndicators.length
                            ? `${formatScraperStartToast(toastTickers.length ? toastTickers : [''], toastIndicators)} (${data.count} tickerów).`
                            : `Uruchomiono scraper dla ${data.count} tickerów z „Brak danych”.`,
                    });
                } else if (toastIndicators.length || (toastTickers && toastTickers.length)) {
                    showToast({
                        type: 'success',
                        title: 'Scraper',
                        message: formatScraperStartToast(
                            toastTickers?.length ? toastTickers : [],
                            toastIndicators,
                        ),
                    });
                }
                pollScraperStatus();
            } else if (data.status === 'no_data_empty') {
                const count = Number.isFinite(Number(data.count)) ? Number(data.count) : 0;
                showToast({
                    type: 'info',
                    title: 'Brak tickerów',
                    message: data.message || `Nie znaleziono tickerów z „Brak danych” do odświeżenia (${count}).`,
                });
            } else {
                alert("Błąd: " + data.message);
            }
        } catch(e) {
            alert("Błąd połączenia z API");
        }
    }

    btnRunAll.addEventListener('click', async () => {
        let pending = null;
        try {
            const r = await fetch('/api/scraper/pending_run');
            if (r.ok) pending = await r.json();
        } catch (e) { /* ignore — fallback do confirmDialog */ }

        let fresh = false;
        if (pending && pending.has_pending) {
            const choice = await pendingRunDialog(pending);
            if (choice === 'cancel') return;
            fresh = (choice === 'fresh');
        } else {
            const ok = await confirmDialog({
                title: 'Pobrać dane dla wszystkich tickerów?',
                message: 'Operacja może potrwać kilka minut. Zostanie uruchomione pełne pobieranie.',
                confirmLabel: 'Uruchom',
                cancelLabel: 'Anuluj',
            });
            if (!ok) return;
            // Brak pending state'u — fresh i tak bez efektu (backend no-op),
            // ale wysyłamy true, żeby intencja była jednoznaczna.
            fresh = true;
        }
        startScraper([], { fresh });
    });

    btnRunNoData?.addEventListener('click', async () => {
        await runNoDataScrape([]);
    });

    const btnRunNoDataMenu = document.getElementById('btn-run-scraper-no-data-menu');
    const scraperNoDataMenu = document.getElementById('scraper-no-data-menu');
    wireDropdownMenu(btnRunNoDataMenu, scraperNoDataMenu, async (item) => {
        const indicators = parseIndicatorSelection(item.dataset.noDataIndicators);
        await runNoDataScrape(indicators);
    });

    async function runNoDataScrape(indicators) {
        let previewCount = null;
        try {
            const previewRes = await fetch('/api/tickers/no_data');
            if (previewRes.ok) {
                const preview = await previewRes.json();
                if (Number.isFinite(Number(preview.count))) {
                    previewCount = Number(preview.count);
                }
            }
        } catch (e) { /* fallback — confirm bez licznika */ }

        const indHint = indicators.length === 1
            ? ` (tylko ${indicators[0]})`
            : (indicators.length > 1 ? ` (${indicators.join(', ')})` : '');
        const countHint = previewCount === null
            ? 'Tickery zostaną wybrane według stanu dashboardu (Brak danych / brak wierszy w CSV).'
            : (previewCount === 0
                ? 'Na dashboardzie nie ma obecnie tickerów z „Brak danych”.'
                : `Znaleziono ${previewCount} tickerów z „Brak danych” na dashboardzie.`);

        const ok = await confirmDialog({
            title: `Odświeżyć tylko tickery z „Brak Danych”${indHint}?`,
            message: countHint,
            confirmLabel: previewCount === 0 ? 'Sprawdź ponownie' : 'Odśwież',
            cancelLabel: 'Anuluj',
        });
        if (!ok) return;
        if (previewCount === 0) {
            showToast({
                type: 'info',
                title: 'Brak tickerów',
                message: 'Na dashboardzie nie ma tickerów z „Brak danych” do odświeżenia (0).',
            });
            return;
        }
        startScraper([], {
            noDataOnly: true,
            indicators: indicators.length ? indicators.join(',') : undefined,
        });
    }

    btnRunSelected.addEventListener('click', () => {
        const cbs = document.querySelectorAll('.ticker-select-cb:checked');
        const selectedTickers = Array.from(cbs).map(cb => cb.value);
        if (selectedTickers.length === 0) {
            alert("Nie zaznaczono żadnych tickerów!");
            return;
        }
        startScraper(selectedTickers);
    });

    btnRefreshFundamentalsAll?.addEventListener('click', async () => {
        const btn = btnRefreshFundamentalsAll;
        const original = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="ph ph-spinner"></i> Pobieranie fundamentów…';
        try {
            const res = await fetch('/api/fundamentals/refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ all: true }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(data.detail || data.message || 'Nie udało się odświeżyć fundamentów.');
            }
            const withData = data.with_data ?? 0;
            const count = data.count ?? 0;
            const toastType = data.status === 'partial' ? 'warning' : 'success';
            showToast({
                type: toastType,
                title: 'Fundamentale',
                message: data.message || (
                    withData === count
                        ? `Zaktualizowano ${withData} tickerów.`
                        : `Przetworzono ${count}, z danymi ${withData}.`
                ),
            });
            await fetchDashboard();
        } catch (e) {
            showToast({
                type: 'error',
                title: 'Fundamentale',
                message: String(e.message || e),
            });
        } finally {
            btn.innerHTML = original;
            btn.disabled = false;
        }
    });

    btnStopScraper.addEventListener('click', async () => {
        const ok = await confirmDialog({
            title: 'Zatrzymać scraper?',
            message: 'Aktualny odczyt zostanie przerwany. Częściowe wyniki pozostaną zapisane.',
            confirmLabel: 'Zatrzymaj',
            cancelLabel: 'Anuluj',
            danger: true,
        });
        if (!ok) return;
        btnStopScraper.disabled = true;
        const originalLabel = btnStopScraper.innerHTML;
        btnStopScraper.innerHTML = '<i class="ph ph-spinner"></i> Zatrzymywanie…';
        try {
            const res = await fetch('/api/scraper/stop', { method: 'POST' });
            const data = await res.json().catch(() => ({}));
            if (data.status === 'stopped') {
                const pids = Array.isArray(data.pids_found) ? data.pids_found : [];
                const extra = data.orphan_killed && pids.length
                    ? ` (PID: ${pids.join(', ')})`
                    : '';
                showToast({
                    type: 'info',
                    title: 'Scraper zatrzymany',
                    message: `Proces został zakończony${extra}.`,
                    duration: 7000,
                });
            } else if (data.status === 'not_running') {
                showToast({
                    type: 'info',
                    title: 'Scraper nie działa',
                    message: 'Nie znaleziono aktywnego procesu. Jeżeli nadal widzisz postęp — zrestartuj uvicorn.',
                    duration: 9000,
                });
            } else {
                showToast({ type: 'error', title: 'Problem ze zatrzymaniem', message: data.message || 'Nieznany błąd' });
            }
        } catch (e) {
            console.error(e);
            showToast({ type: 'error', title: 'Błąd połączenia', message: String(e.message || e) });
        } finally {
            btnStopScraper.innerHTML = originalLabel;
            btnStopScraper.disabled = false;
            // Szybki re-poll (kilka razy) aby UI błyskawicznie wrócił do "Gotowy".
            setTimeout(pollScraperStatus, 300);
            setTimeout(pollScraperStatus, 1500);
        }
    });

    // ==========================================
    // PER-TICKER RE-SCRAPE
    // ==========================================
    async function waitForScraperCompletion(timeoutMs = 600_000) {
        const deadline = Date.now() + timeoutMs;
        let lastStatus = 'running';
        while (Date.now() < deadline) {
            const res = await fetch('/api/scraper/status');
            if (!res.ok) break;
            const data = await res.json();
            lastStatus = data?.status || 'idle';
            if (lastStatus !== 'running') {
                return data;
            }
            await new Promise(r => setTimeout(r, 1000));
        }
        return { status: lastStatus };
    }

    async function requestRescrapeTicker(ticker, btnEl, options = {}) {
        if (!ticker || rerunningTickers.has(ticker)) return;
        const indicators = parseIndicatorSelection(options?.indicators);
        const indicatorOnly = indicators.length === 1;
        rerunningTickers.add(ticker);
        if (btnEl) {
            btnEl.classList.add('spinning');
            btnEl.disabled = true;
        }
        const wrap = btnEl?.closest('.card-rescrape-wrap');
        if (wrap) {
            wrap.querySelectorAll('.card-action-btn').forEach(b => {
                b.classList.add('spinning');
                b.disabled = true;
            });
        }
        try {
            const payload = { tickers: [ticker] };
            if (indicators.length) payload.indicators = indicators;
            const res = await fetch('/api/scraper/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (data.status === 'started') {
                startGlobalScraperPolling();
                const startMsg = formatScraperStartToast([ticker], indicators);
                showToast({ type: 'info', title: 'Pobieranie', message: startMsg });
                const finalStatus = await waitForScraperCompletion();
                if (finalStatus?.status === 'error') {
                    showToast({
                        type: 'error',
                        title: 'Błąd scrapera',
                        message: finalStatus.error || `Nie udało się pobrać techniki dla ${ticker}.`,
                        duration: 10000,
                    });
                } else if (finalStatus?.status === 'stopped') {
                    showToast({ type: 'info', title: 'Scraper zatrzymany', message: `${ticker}: pobieranie przerwane.` });
                }
            } else if (data.status === 'already_running') {
                let running = '';
                try {
                    const st = await fetch('/api/scraper/status').then(r => r.json());
                    if (st && st.current_ticker) {
                        running = ` (aktualnie: ${st.current_ticker}${st.progress ? ', ' + st.progress : ''})`;
                    }
                } catch (_) { /* ignore */ }
                showToast({
                    type: 'warning',
                    title: 'Scraper zajęty',
                    message:
                        `Trwa inny pobór danych${running}. ` +
                        `Aby uruchomić tylko dla ${ticker}, kliknij najpierw „Zatrzymaj" w Konfiguracji.`,
                });
                return;
            } else {
                showToast({ type: 'error', title: 'Błąd', message: data.message || 'Nie udało się uruchomić pobierania.' });
                return;
            }

            try {
                if (!indicatorOnly) {
                    await fetch('/api/fundamentals/refresh', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ tickers: [ticker] }),
                    });
                }
            } catch (_) { /* fundamentals optional */ }

            await fetchDashboard();
            const doneMsg = indicatorOnly
                ? `${ticker}: zaktualizowano ${indicators[0]}.`
                : `${ticker}: dane techniczne i fundamentale zaktualizowane.`;
            showToast({ type: 'success', title: 'Odświeżono', message: doneMsg });
        } catch (e) {
            showToast({ type: 'error', title: 'Błąd połączenia', message: String(e.message || e) });
        } finally {
            rerunningTickers.delete(ticker);
            clearRescrapeSpinner(ticker);
        }
    }

    function cssEscape(value) {
        if (window.CSS && typeof CSS.escape === 'function') return CSS.escape(value);
        return String(value).replace(/["\\]/g, '\\$&');
    }

    function clearRescrapeSpinner(ticker) {
        rerunningTickers.delete(ticker);
        const card = document.querySelector(`.ticker-card[data-ticker="${cssEscape(ticker)}"]`);
        if (!card) return;
        card.querySelectorAll('.card-rescrape-wrap .card-action-btn').forEach(btn => {
            btn.classList.remove('spinning');
            btn.disabled = false;
        });
    }

    // ==========================================
    // HISTORICAL PCA CHART MODAL
    // ==========================================
    const historyModal = document.getElementById('ticker-history-modal');
    const historyModalTitle = document.getElementById('ticker-history-title');
    const historyModalSubtitle = document.getElementById('ticker-history-subtitle');
    const historyModalClose = document.getElementById('ticker-history-close');
    const historyIntervalToggle = document.getElementById('ticker-history-interval-toggle');
    const historyMetricSelect = document.getElementById('ticker-history-metric');
    const historyCanvas = document.getElementById('ticker-history-chart');
    const historyEmptyEl = document.getElementById('ticker-history-empty');

    const HISTORY_METRIC_LABELS = {
        PCA: 'PCA',
        MacD_Line: 'MacD Line',
        MacD_Histogram: 'MacD Histogram',
        Fund_PE: 'Fund P/E',
        Fund_PB: 'Fund P/B',
        Fund_EV_EBITDA: 'Fund EV/EBITDA',
        Fund_ROE: 'Fund ROE',
        Fund_FCF: 'Fund FCF',
    };

    function syncHistoryIntervalVisibility() {
        if (!historyIntervalToggle) return;
        const hide = String(currentHistoryMetric || '').startsWith('Fund_');
        historyIntervalToggle.style.display = hide ? 'none' : '';
    }

    function openHistoryModal(ticker) {
        if (!historyModal || !ticker) return;
        currentHistoryTicker = ticker;
        currentHistoryInterval = ALLOWED_INTERVAL.has(currentChartInterval) ? currentChartInterval : '1D';
        currentHistoryMetric = historyMetricSelect?.value || 'PCA';
        syncHistoryIntervalVisibility();
        historyModalTitle.textContent = `Historia — ${ticker}`;
        historyModalSubtitle.textContent = 'Ładowanie…';
        if (historyMetricSelect) historyMetricSelect.value = currentHistoryMetric;
        if (historyIntervalToggle) {
            historyIntervalToggle.querySelectorAll('.interval-toggle-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.interval === currentHistoryInterval);
            });
        }
        historyModal.classList.add('visible');
        historyModal.setAttribute('aria-hidden', 'false');
        fetchTickerHistory();
    }

    function closeHistoryModal() {
        if (!historyModal) return;
        historyModal.classList.remove('visible');
        historyModal.setAttribute('aria-hidden', 'true');
        currentHistoryTicker = null;
        if (historyChartInstance) {
            historyChartInstance.destroy();
            historyChartInstance = null;
        }
    }

    historyModalClose?.addEventListener('click', closeHistoryModal);
    historyModal?.addEventListener('click', (e) => {
        if (e.target === historyModal) closeHistoryModal();
    });

    historyMetricSelect?.addEventListener('change', (e) => {
        currentHistoryMetric = e.target.value || 'PCA';
        syncHistoryIntervalVisibility();
        fetchTickerHistory();
    });

    historyIntervalToggle?.addEventListener('click', (e) => {
        const btn = e.target.closest('.interval-toggle-btn');
        if (!btn) return;
        const iv = btn.dataset.interval;
        if (!ALLOWED_INTERVAL.has(iv) || iv === currentHistoryInterval) return;
        currentHistoryInterval = iv;
        historyIntervalToggle.querySelectorAll('.interval-toggle-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        fetchTickerHistory();
    });

    async function fetchTickerHistory() {
        if (!currentHistoryTicker) return;
        historyEmptyEl?.classList.add('hidden');
        try {
            const res = await fetch(
                `/api/ticker/${encodeURIComponent(currentHistoryTicker)}/history?interval=${encodeURIComponent(currentHistoryInterval)}&metric=${encodeURIComponent(currentHistoryMetric)}`
            );
            if (!res.ok) throw new Error('Błąd pobierania historii');
            const data = await res.json();
            renderHistoryChart(data.history || [], data.metric || currentHistoryMetric);
        } catch (e) {
            console.error(e);
            if (historyModalSubtitle) historyModalSubtitle.textContent = 'Błąd pobierania historii';
            renderHistoryChart([]);
        }
    }

    function renderHistoryChart(points, metricKey) {
        if (historyChartInstance) {
            historyChartInstance.destroy();
            historyChartInstance = null;
        }
        const metric = metricKey || currentHistoryMetric || 'PCA';
        const metricLabel = HISTORY_METRIC_LABELS[metric] || metric;
        const clean = points.filter(p => Number.isFinite(p.value));
        if (historyModalSubtitle) {
            const ivLabel = String(metric).startsWith('Fund_') ? '' : ` · interwał ${currentHistoryInterval}`;
            historyModalSubtitle.textContent = clean.length === 0
                ? 'Brak punktów danych'
                : `${clean.length} punktów — ${metricLabel}${ivLabel}`;
        }
        if (clean.length === 0) {
            historyEmptyEl?.classList.remove('hidden');
            return;
        }
        historyEmptyEl?.classList.add('hidden');

        const labels = clean.map(p => p.date);
        const values = clean.map(p => p.value);
        const dotColors = clean.map(p => sanitizeCssColor(p.color || '#60a5fa'));

        Chart.defaults.color = '#94a3b8';
        Chart.defaults.font.family = 'Inter';
        const ctx = historyCanvas.getContext('2d');
        historyChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: `${metricLabel}${String(metric).startsWith('Fund_') ? '' : ` (${currentHistoryInterval})`}`,
                    data: values,
                    borderColor: 'rgba(59, 130, 246, 0.8)',
                    backgroundColor: 'rgba(59, 130, 246, 0.12)',
                    pointBackgroundColor: dotColors,
                    pointBorderColor: dotColors,
                    pointRadius: 4,
                    pointHoverRadius: 6,
                    tension: 0.3,
                    fill: true,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(15, 17, 21, 0.9)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: 'rgba(255, 255, 255, 0.1)',
                        borderWidth: 1,
                        padding: 10,
                        callbacks: {
                            label: (c) => `${metricLabel}: ${c.parsed.y}`,
                        },
                    },
                },
                scales: {
                    y: {
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        border: { display: false },
                    },
                    x: {
                        grid: { display: false },
                        border: { display: false },
                        ticks: { maxTicksLimit: 10, maxRotation: 0 },
                    },
                },
            },
        });
    }

    // ==========================================
    // GLOBAL SCRAPER POLLING + TOASTS
    // ==========================================
    let globalPollInterval = null;
    let lastGlobalStatus = 'idle';

    function todayDateId() {
        const d = new Date();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        return `${d.getFullYear()}-${mm}-${dd}`;
    }

    function startGlobalScraperPolling() {
        if (globalPollInterval) return;
        const tick = async () => {
            try {
                const res = await fetch('/api/scraper/status');
                if (!res.ok) return;
                const data = await res.json();
                handleGlobalStatus(data);
            } catch (e) { /* ignore */ }
        };
        tick();
        globalPollInterval = setInterval(tick, 5000);
    }

    function handleGlobalStatus(data) {
        const status = data?.status || 'idle';
        if (status === 'running') {
            const pct = parseScraperProgress(data.progress);
            const overall = data.progress ? data.progress.split('·')[0].trim() : '';
            const detail = formatScraperProgressLabel(
                data.progress,
                data.current_ticker || '',
                data.current_indicator || '',
            );
            const parts = [];
            if (overall) parts.push(overall);
            if (detail) parts.push(detail);
            setGlobalBanner(true, {
                text: `Scraper w toku${parts.length ? ' — ' + parts.join(' · ') : ''}`,
                progressPct: pct,
            });
        } else {
            setGlobalBanner(false);
        }

        // Transitions
        if (lastGlobalStatus === 'running' && status === 'done') {
            const durMsg = data.duration_human ? ` Czas: ${data.duration_human}.` : '';
            showToast({ type: 'success', title: 'Scraper zakończony', message: `Dane zostały odświeżone.${durMsg}` });
            rerunningTickers.clear();
            document.querySelectorAll('.card-rescrape-btn.spinning').forEach(btn => {
                btn.classList.remove('spinning');
                btn.disabled = false;
            });
            autoReloadAfterScrape();
        } else if (lastGlobalStatus === 'running' && status === 'error') {
            const durErr = data.duration_human ? ` (po ${data.duration_human})` : '';
            showToast({ type: 'error', title: 'Błąd scrapera', message: (data.error || 'Nieznany błąd') + durErr, duration: 10000 });
            rerunningTickers.clear();
            document.querySelectorAll('.card-rescrape-btn.spinning').forEach(btn => {
                btn.classList.remove('spinning');
                btn.disabled = false;
            });
            autoReloadAfterScrape();
        } else if (lastGlobalStatus === 'running' && status === 'stopped') {
            showToast({ type: 'info', title: 'Scraper zatrzymany' });
        }

        // Highlight currently processed card (if any)
        document.querySelectorAll('.card-rescraping-badge').forEach(el => el.remove());
        if (status === 'running' && data.current_ticker) {
            const card = document.querySelector(`.ticker-card[data-ticker="${cssEscape(data.current_ticker)}"]`);
            if (card) {
                const right = card.querySelector('.card-header-right');
                if (right && !right.querySelector('.card-rescraping-badge')) {
                    const badge = document.createElement('span');
                    badge.className = 'card-rescraping-badge';
                    badge.textContent = 'pobieram…';
                    right.insertBefore(badge, right.firstChild);
                }
            }
        }

        lastGlobalStatus = status;
    }

    function autoReloadAfterScrape() {
        fetchDashboard();
    }

    // ==========================================
    // KEYBOARD SHORTCUTS
    // ==========================================
    let focusedCardIndex = -1;

    function visibleCards() {
        return Array.from(resultsGrid.querySelectorAll('.ticker-card'));
    }

    function focusCardByIndex(idx) {
        const cards = visibleCards();
        if (cards.length === 0) return;
        focusedCardIndex = Math.max(0, Math.min(cards.length - 1, idx));
        cards.forEach((c, i) => c.classList.toggle('focused-card', i === focusedCardIndex));
        cards[focusedCardIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
        setTimeout(() => {
            cards[focusedCardIndex]?.classList.remove('focused-card');
        }, 1500);
    }

    document.addEventListener('keydown', (e) => {
        const tgt = e.target;
        const inInput = tgt && (
            tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA' || tgt.isContentEditable
        );

        if (e.key === 'Escape') {
            const renameOpen = document.getElementById('ticker-rename-modal')?.classList.contains('visible');
            if (renameOpen) {
                document.getElementById('ticker-rename-modal')?.classList.remove('visible');
                document.getElementById('ticker-rename-modal')?.setAttribute('aria-hidden', 'true');
                e.preventDefault();
                return;
            }
            const deleteOpen = document.getElementById('ticker-delete-modal')?.classList.contains('visible');
            if (deleteOpen) {
                closeDeleteTickerModal();
                e.preventDefault();
                return;
            }
            if (historyModal?.classList.contains('visible')) {
                closeHistoryModal();
                e.preventDefault();
                return;
            }
            if (document.activeElement === searchInput) {
                searchInput.value = '';
                searchInput.blur();
                filterAndRenderCards('');
                e.preventDefault();
            }
            return;
        }

        if (inInput) return;
        if (e.metaKey || e.ctrlKey || e.altKey) return;

        // Only active when dashboard view is visible
        const dashActive = document.getElementById('dashboard-view')?.classList.contains('active');
        if (!dashActive) return;

        switch (e.key) {
            case '/':
                e.preventDefault();
                searchInput.focus();
                searchInput.select();
                break;
            case 'j':
                e.preventDefault();
                focusCardByIndex(focusedCardIndex + 1);
                break;
            case 'k':
                e.preventDefault();
                focusCardByIndex(focusedCardIndex - 1);
                break;
            case 'e':
                e.preventDefault();
                toggleExpandAll();
                break;
            case 'r':
                e.preventDefault();
                refreshBtn?.click();
                break;
        }
    });

    // ==========================================
    // DELETE TICKER MODAL
    // ==========================================
    const deleteModal = document.getElementById('ticker-delete-modal');
    const deleteCloseBtn = document.getElementById('ticker-delete-close');
    const deleteCancelBtn = document.getElementById('ticker-delete-cancel');
    const deleteConfirmBtn = document.getElementById('ticker-delete-confirm');
    const deleteLoadingEl = document.getElementById('ticker-delete-loading');
    const deleteErrorEl = document.getElementById('ticker-delete-error');
    const deletePreviewEl = document.getElementById('ticker-delete-preview');
    const deleteSubtitleEl = document.getElementById('ticker-delete-subtitle');
    let deleteTickerValue = '';

    function setDeleteError(msg) {
        if (!deleteErrorEl) return;
        if (!msg) {
            deleteErrorEl.textContent = '';
            deleteErrorEl.classList.add('hidden');
        } else {
            deleteErrorEl.textContent = msg;
            deleteErrorEl.classList.remove('hidden');
        }
    }

    function closeDeleteTickerModal() {
        if (!deleteModal) return;
        deleteModal.classList.remove('visible');
        deleteModal.setAttribute('aria-hidden', 'true');
        deleteTickerValue = '';
    }

    function renderDeletePreview(preview) {
        if (!deletePreviewEl) return;
        const files = Array.isArray(preview?.files) ? preview.files : [];
        const rowsCount = Number(preview?.rows_count || 0);
        const filesCount = Number(preview?.files_count || 0);
        const configCount = Number(preview?.config_removed_count || 0);
        const fileItems = files.slice(0, 8).map(f => (
            `<li><strong>${escapeHtml(f.filename || '')}</strong>: ${Number(f.rows || 0)} wiersz(e)</li>`
        )).join('');
        const extra = files.length > 8
            ? `<li>… oraz ${files.length - 8} kolejnych plików</li>`
            : '';
        deletePreviewEl.innerHTML = `
            <div class="delete-warning">
                Ta operacja jest trwała: usunie <strong>${escapeHtml(preview?.ticker || deleteTickerValue)}</strong>
                z konfiguracji i fizycznie wytnie jego wiersze z historycznych CSV.
            </div>
            <div class="delete-preview-grid">
                <div><span>W configu</span><strong>${configCount}</strong></div>
                <div><span>Pliki CSV</span><strong>${filesCount}</strong></div>
                <div><span>Wiersze</span><strong>${rowsCount}</strong></div>
            </div>
            ${files.length ? `<ul class="delete-file-list">${fileItems}${extra}</ul>` : '<p class="delete-muted">Nie znaleziono wierszy w historycznych CSV.</p>'}
        `;
        deletePreviewEl.hidden = false;
    }

    async function openDeleteTickerModal(ticker) {
        if (!deleteModal || !ticker) return;
        deleteTickerValue = String(ticker || '').trim().toUpperCase();
        if (deleteSubtitleEl) {
            deleteSubtitleEl.textContent = `Usuwanie ${deleteTickerValue} z konfiguracji i wszystkich CSV.`;
        }
        if (deletePreviewEl) {
            deletePreviewEl.innerHTML = '';
            deletePreviewEl.hidden = true;
        }
        if (deleteLoadingEl) deleteLoadingEl.hidden = false;
        if (deleteConfirmBtn) deleteConfirmBtn.disabled = true;
        setDeleteError('');
        deleteModal.classList.add('visible');
        deleteModal.setAttribute('aria-hidden', 'false');

        try {
            const res = await fetch(`/api/tickers/${encodeURIComponent(deleteTickerValue)}/delete_preview`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(data?.detail || data?.message || `Błąd ${res.status}`);
            }
            if (deleteLoadingEl) deleteLoadingEl.hidden = true;
            renderDeletePreview(data);
            if (deleteConfirmBtn) {
                deleteConfirmBtn.disabled = !(Number(data.rows_count || 0) > 0 || Number(data.config_removed_count || 0) > 0);
            }
        } catch (err) {
            if (deleteLoadingEl) deleteLoadingEl.hidden = true;
            setDeleteError(String(err?.message || err || 'Nie udało się pobrać preview usunięcia.'));
        }
    }

    deleteCloseBtn?.addEventListener('click', closeDeleteTickerModal);
    deleteCancelBtn?.addEventListener('click', closeDeleteTickerModal);
    deleteModal?.addEventListener('click', (e) => {
        if (e.target === deleteModal) closeDeleteTickerModal();
    });
    deleteConfirmBtn?.addEventListener('click', async () => {
        const ticker = deleteTickerValue;
        if (!ticker) return;
        deleteConfirmBtn.disabled = true;
        setDeleteError('');
        try {
            const res = await fetch(`/api/tickers/${encodeURIComponent(ticker)}`, { method: 'DELETE' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(data?.detail || data?.message || `Błąd ${res.status}`);
            }
            delete renamedHidden[ticker];
            persistRenamedHidden();
            currentData = (currentData || []).filter(row => String(row['Ticker'] || '').trim().toUpperCase() !== ticker);
            showToast({
                type: 'success',
                title: 'Ticker usunięty',
                message: `${ticker}: usunięto ${Number(data.rows_removed || 0)} wierszy z ${Number(data.files_modified || 0)} plików.`,
            });
            closeDeleteTickerModal();
            await fetchDashboard();
            filterAndRenderCards(searchInput?.value?.toLowerCase().trim() || '');
        } catch (err) {
            setDeleteError(String(err?.message || err || 'Nie udało się usunąć tickera.'));
            deleteConfirmBtn.disabled = false;
        }
    });

    // ==========================================
    // RENAME TICKER MODAL
    // ==========================================
    const renameModal = document.getElementById('ticker-rename-modal');
    const renameModalClose = document.getElementById('ticker-rename-close');
    const renameCancelBtn = document.getElementById('ticker-rename-cancel');
    const renameForm = document.getElementById('ticker-rename-form');
    const renameOldInput = document.getElementById('ticker-rename-old');
    const renameNewInput = document.getElementById('ticker-rename-new');
    const renameErrorEl = document.getElementById('ticker-rename-error');
    const renameSubmitBtn = document.getElementById('ticker-rename-submit');
    const TICKER_RE_CLIENT = /^[A-Z0-9._:-]{1,24}$/;
    let renameOpenedTicker = '';

    function openRenameModal(ticker) {
        if (!renameModal || !ticker) return;
        renameOpenedTicker = String(ticker || '').trim().toUpperCase();
        if (renameOldInput) renameOldInput.value = ticker;
        if (renameNewInput) {
            renameNewInput.value = '';
            renameNewInput.disabled = false;
        }
        setRenameError('');
        if (renameSubmitBtn) renameSubmitBtn.disabled = false;
        renameModal.classList.add('visible');
        renameModal.setAttribute('aria-hidden', 'false');
        setTimeout(() => renameNewInput?.focus(), 50);
    }

    function closeRenameModal() {
        if (!renameModal) return;
        renameModal.classList.remove('visible');
        renameModal.setAttribute('aria-hidden', 'true');
        renameOpenedTicker = '';
    }

    function setRenameError(msg) {
        if (!renameErrorEl) return;
        if (!msg) {
            renameErrorEl.textContent = '';
            renameErrorEl.classList.add('hidden');
        } else {
            renameErrorEl.textContent = msg;
            renameErrorEl.classList.remove('hidden');
        }
    }

    function showRenameCandidateError(detail, requestedOld) {
        if (!renameErrorEl || !detail || typeof detail !== 'object') return false;
        const candidates = Array.isArray(detail.candidates) ? detail.candidates : [];
        if (!candidates.length) return false;

        renameErrorEl.textContent = '';
        renameErrorEl.classList.remove('hidden');

        const msg = document.createElement('span');
        const requested = detail.requested_old || requestedOld;
        const names = candidates.map(c => c?.ticker).filter(Boolean);
        msg.textContent = `${requested} jest w wynikach CSV, ale nie ma go w konfiguracji. `;
        renameErrorEl.appendChild(msg);

        if (candidates.length === 1 && names[0]) {
            const candidate = names[0];
            const hint = document.createElement('span');
            hint.textContent = `Podobny wpis w configu: ${candidate}. `;
            renameErrorEl.appendChild(hint);

            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'record-count-action rename-candidate-btn';
            btn.textContent = `Użyj ${candidate}`;
            btn.addEventListener('click', () => {
                const original = String(requested || renameOpenedTicker || '').trim().toUpperCase();
                const target = String(candidate || '').trim().toUpperCase();
                if (!original || !target) return;

                // Ten przypadek oznacza: karta pochodzi ze starego CSV, ale config
                // ma już właściwy symbol. Nie robimy rename w configu, tylko chowamy
                // błędną kartę CSV i odpalamy pobranie właściwego symbolu.
                markTickerRenamed(original, target);
                showToast({
                    type: 'success',
                    title: 'Używam symbolu z konfiguracji',
                    message: `${original} ukryty. Pobieram dane dla ${target}…`,
                });
                closeRenameModal();
                filterAndRenderCards(searchInput?.value || '');
                requestRescrapeTicker(target, null);
            });
            renameErrorEl.appendChild(btn);
        } else {
            const hint = document.createElement('span');
            hint.textContent = `Podobne wpisy w configu: ${names.join(', ')}. Wybierz właściwy wpis w konfiguracji albo zmień ręcznie.`;
            renameErrorEl.appendChild(hint);
        }
        return true;
    }

    renameModalClose?.addEventListener('click', closeRenameModal);
    renameCancelBtn?.addEventListener('click', closeRenameModal);
    renameModal?.addEventListener('click', (e) => {
        if (e.target === renameModal) closeRenameModal();
    });

    renameForm?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const oldTicker = (renameOldInput?.value || '').trim().toUpperCase();
        const newTicker = (renameNewInput?.value || '').trim().toUpperCase();
        setRenameError('');

        if (!oldTicker || !newTicker) {
            setRenameError('Podaj nową nazwę tickera.');
            return;
        }
        if (oldTicker === newTicker) {
            setRenameError('Nowa nazwa jest identyczna ze starą.');
            return;
        }
        if (!TICKER_RE_CLIENT.test(newTicker)) {
            setRenameError('Dozwolone: A–Z, 0–9, kropka, podkreślnik, myślnik, dwukropek (np. GPW:ATC; max 24 znaki).');
            return;
        }

        if (renameSubmitBtn) renameSubmitBtn.disabled = true;
        try {
            const res = await fetch('/api/tickers/rename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ old: oldTicker, new: newTicker }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const detail = (data && (data.detail || data.message)) || `Błąd ${res.status}`;
                if (!showRenameCandidateError(detail, oldTicker)) {
                    setRenameError(
                        typeof detail === 'string'
                            ? detail
                            : (detail?.message || 'Nie udało się zmienić nazwy.')
                    );
                }
                if (renameSubmitBtn) renameSubmitBtn.disabled = false;
                return;
            }
            // Ukryj starą kartę w bieżącym widoku (CSV zostaje nietknięty,
            // ale użytkownik nie chce już widzieć starego symbolu). Ukrywamy
            // zarówno to, co wpisał użytkownik, jak i realnie zmatchowany
            // symbol z configu (np. LULU.O → LULU), żeby nic nie zostało.
            const matchedOld = (data && typeof data.old === 'string' && data.old) || oldTicker;
            const openedOld = renameOpenedTicker;
            markTickerRenamed(oldTicker, newTicker);
            if (matchedOld && matchedOld.toUpperCase() !== oldTicker) {
                markTickerRenamed(matchedOld, newTicker);
            }
            if (openedOld && openedOld !== oldTicker && openedOld !== String(matchedOld || '').toUpperCase()) {
                markTickerRenamed(openedOld, newTicker);
            }
            // Dodatkowo ukryj wszystkie wiersze z tą samą bazą (prefiks przed
            // pierwszą kropką) — spójnie z logiką fuzzy-match w backendzie.
            const baseOld = (openedOld || oldTicker).split('.', 1)[0];
            if (baseOld) {
                (currentData || []).forEach(row => {
                    const t = String(row['Ticker'] || '').toUpperCase();
                    if (t && t.split('.', 1)[0] === baseOld) {
                        markTickerRenamed(t, newTicker);
                    }
                });
            }

            showToast({
                type: 'success',
                title: 'Nazwa zmieniona',
                message: `${matchedOld} → ${newTicker}. Pobieram dane dla nowej nazwy…`,
            });
            closeRenameModal();

            // Odśwież widok kart natychmiast, żeby stara karta zniknęła
            // jeszcze przed pojawieniem się nowych danych.
            filterAndRenderCards(searchInput?.value || '');

            // Automatycznie zleć pobranie nowego tickera, aby użytkownik od razu
            // zobaczył, czy nowy symbol działa.
            requestRescrapeTicker(newTicker, null);
        } catch (err) {
            setRenameError(String(err?.message || err || 'Błąd połączenia'));
            if (renameSubmitBtn) renameSubmitBtn.disabled = false;
        }
    });

    // ========================================================================
    // REPAIR SYMBOLS (no-data tickery → prefix giełdy z TV REST)
    // ========================================================================
    const repairBtn = document.getElementById('repair-symbols-btn');
    const repairModal = document.getElementById('repair-symbols-modal');
    const repairClose = document.getElementById('repair-symbols-close');
    const repairCancel = document.getElementById('repair-symbols-cancel');
    const repairSubmit = document.getElementById('repair-symbols-submit');
    const repairSubmitRerun = document.getElementById('repair-symbols-submit-rerun');
    const repairListEl = document.getElementById('repair-symbols-list');
    const repairLoading = document.getElementById('repair-symbols-loading');
    const repairEmpty = document.getElementById('repair-symbols-empty');
    const repairError = document.getElementById('repair-symbols-error');

    const TICKER_RE_REPAIR = /^[A-Z0-9._:-]{1,24}$/;

    function repairRowHasSelection(row) {
        if (!row) return false;
        const cb = row.querySelector('input[type="checkbox"]');
        if (!cb || !cb.checked) return false;
        if (row.classList.contains('has-match') || row.classList.contains('has-other-match')) {
            const oldT = row.dataset.old;
            const radio = row.querySelector('input[type="radio"]:checked');
            const newT = radio
                ? radio.value
                : (row.querySelector('.repair-new')?.textContent || '');
            return !!(oldT && newT);
        }
        if (row.classList.contains('no-match')) {
            const manual = row.querySelector('.repair-manual-input');
            const val = (manual?.value || '').trim().toUpperCase();
            return !!(row.dataset.old && val && TICKER_RE_REPAIR.test(val));
        }
        return false;
    }

    function syncRepairSubmitButtons() {
        if (!repairListEl) return;
        const anySelected = Array.from(repairListEl.querySelectorAll('.repair-row'))
            .some(repairRowHasSelection);
        if (repairSubmit) repairSubmit.disabled = !anySelected;
        if (repairSubmitRerun) repairSubmitRerun.disabled = !anySelected;
    }

    function rowIsNoData(row) {
        if (!row) return false;
        const sts = (row['Scrape_Status'] || '').toUpperCase();
        if (sts === 'NO_DATA') return true;
        return row['All_Indicators_Missing'] === true;
    }

    function noDataTickersWithoutPrefix() {
        const byTicker = new Map();
        (currentData || []).forEach(row => {
            const t = (row['Ticker'] || '').trim();
            if (!t) return;
            if (!byTicker.has(t)) byTicker.set(t, []);
            byTicker.get(t).push(row);
        });
        const out = [];
        byTicker.forEach((rows, ticker) => {
            if (ticker.includes(':')) return;
            const allNoData = rows.every(rowIsNoData);
            if (allNoData) out.push(ticker);
        });
        return out;
    }

    function syncRepairBtnVisibility() {
        if (!repairBtn) return;
        const candidates = noDataTickersWithoutPrefix();
        repairBtn.hidden = candidates.length === 0;
        if (!repairBtn.hidden) {
            repairBtn.title = `Napraw symbole bez prefixu giełdy (kandydatów: ${candidates.length})`;
        }
    }

    function setRepairError(msg) {
        if (!repairError) return;
        if (!msg) {
            repairError.textContent = '';
            repairError.classList.add('hidden');
        } else {
            repairError.textContent = msg;
            repairError.classList.remove('hidden');
        }
    }

    function closeRepairModal() {
        if (!repairModal) return;
        repairModal.classList.remove('visible');
        repairModal.setAttribute('aria-hidden', 'true');
    }

    function openRepairModal() {
        if (!repairModal) return;
        repairModal.classList.add('visible');
        repairModal.setAttribute('aria-hidden', 'false');
        setRepairError('');
        if (repairListEl) {
            repairListEl.hidden = true;
            repairListEl.innerHTML = '';
        }
        if (repairEmpty) repairEmpty.hidden = true;
        if (repairLoading) repairLoading.hidden = false;
        if (repairSubmit) repairSubmit.disabled = true;
        if (repairSubmitRerun) repairSubmitRerun.disabled = true;
        loadRepairPreview();
    }

    function buildRepairCandidateRadios(item, idx, newEl, candListClass) {
        const candList = document.createElement('div');
        candList.className = candListClass || 'repair-row-candidates';
        const allCands = [
            ...(item.candidates || []),
            ...(item.other_candidates || []),
        ];
        allCands.forEach((c, ci) => {
            const lbl = document.createElement('label');
            lbl.className = 'repair-candidate';
            const radio = document.createElement('input');
            radio.type = 'radio';
            radio.name = `repair-cand-${idx}`;
            radio.value = c.new;
            if (ci === 0) radio.checked = true;
            radio.addEventListener('change', () => {
                if (newEl) newEl.textContent = c.new;
                syncRepairSubmitButtons();
            });
            lbl.appendChild(radio);
            const exch = document.createElement('span');
            exch.className = 'repair-exchange';
            exch.textContent = c.exchange || '';
            lbl.appendChild(exch);
            lbl.appendChild(document.createTextNode(`${c.new} · ${c.description || '—'}`));
            candList.appendChild(lbl);
        });
        return candList;
    }

    function renderRepairList(items) {
        if (!repairListEl) return;
        repairListEl.innerHTML = '';
        const usable = (items || []).filter(
            i => Array.isArray(i.candidates) && i.candidates.length > 0
        );
        const otherOnly = (items || []).filter(
            i => (!i.candidates || i.candidates.length === 0)
                && Array.isArray(i.other_candidates)
                && i.other_candidates.length > 0
        );
        const skipped = (items || []).filter(
            i => (!i.candidates || i.candidates.length === 0)
                && (!i.other_candidates || i.other_candidates.length === 0)
                && !i.skipped
        );

        usable.forEach((item, idx) => {
            const row = document.createElement('div');
            row.className = 'repair-row has-match';
            row.dataset.old = item.old;

            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = true;
            cb.id = `repair-cb-${idx}`;
            cb.setAttribute('aria-label', `Zastosuj rename dla ${item.old}`);
            cb.addEventListener('change', syncRepairSubmitButtons);
            row.appendChild(cb);

            const body = document.createElement('div');
            body.className = 'repair-row-body';

            const title = document.createElement('div');
            title.className = 'repair-row-title';
            const oldEl = document.createElement('span');
            oldEl.className = 'repair-old';
            oldEl.textContent = item.old;
            title.appendChild(oldEl);
            const arrow = document.createElement('span');
            arrow.className = 'repair-arrow';
            arrow.textContent = '→';
            title.appendChild(arrow);
            const newEl = document.createElement('span');
            newEl.className = 'repair-new';
            newEl.textContent = item.candidates[0].new;
            title.appendChild(newEl);
            body.appendChild(title);

            if (item.candidates.length === 1) {
                const meta = document.createElement('div');
                meta.className = 'repair-row-meta';
                const exch = document.createElement('span');
                exch.className = 'repair-exchange';
                exch.textContent = item.candidates[0].exchange || '';
                meta.appendChild(exch);
                meta.appendChild(document.createTextNode(item.candidates[0].description || '—'));
                body.appendChild(meta);
            } else {
                body.appendChild(buildRepairCandidateRadios(item, idx, newEl));
            }

            row.appendChild(body);
            repairListEl.appendChild(row);
        });

        otherOnly.forEach((item, idx) => {
            const rowIdx = usable.length + idx;
            const row = document.createElement('div');
            row.className = 'repair-row has-other-match';
            row.dataset.old = item.old;

            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = false;
            cb.id = `repair-cb-other-${rowIdx}`;
            cb.setAttribute('aria-label', `Zastosuj rename dla ${item.old}`);
            cb.addEventListener('change', syncRepairSubmitButtons);
            row.appendChild(cb);

            const body = document.createElement('div');
            body.className = 'repair-row-body';

            const title = document.createElement('div');
            title.className = 'repair-row-title';
            const oldEl = document.createElement('span');
            oldEl.className = 'repair-old';
            oldEl.textContent = item.old;
            title.appendChild(oldEl);
            const arrow = document.createElement('span');
            arrow.className = 'repair-arrow';
            arrow.textContent = '→';
            title.appendChild(arrow);
            const newEl = document.createElement('span');
            newEl.className = 'repair-new';
            newEl.textContent = item.other_candidates[0].new;
            title.appendChild(newEl);
            body.appendChild(title);

            const meta = document.createElement('div');
            meta.className = 'repair-row-meta repair-row-note';
            meta.textContent = item.note || 'Dopasowanie poza skonfigurowanymi giełdami';
            body.appendChild(meta);

            body.appendChild(buildRepairCandidateRadios(
                { candidates: [], other_candidates: item.other_candidates },
                rowIdx,
                newEl,
            ));

            const manualWrap = document.createElement('div');
            manualWrap.className = 'repair-manual-wrap';
            const manualInput = document.createElement('input');
            manualInput.type = 'text';
            manualInput.className = 'repair-manual-input';
            manualInput.placeholder = 'Lub wpisz ręcznie, np. GPW:SYMBOL';
            manualInput.spellcheck = false;
            manualInput.addEventListener('input', () => {
                const val = manualInput.value.trim().toUpperCase();
                if (val && TICKER_RE_REPAIR.test(val)) {
                    newEl.textContent = val;
                    cb.checked = true;
                    row.querySelectorAll('input[type="radio"]').forEach(r => { r.checked = false; });
                }
                syncRepairSubmitButtons();
            });
            manualWrap.appendChild(manualInput);
            body.appendChild(manualWrap);

            row.appendChild(body);
            repairListEl.appendChild(row);
        });

        skipped.forEach((item, idx) => {
            const row = document.createElement('div');
            row.className = 'repair-row no-match';
            row.dataset.old = item.old;

            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.disabled = true;
            cb.addEventListener('change', syncRepairSubmitButtons);
            row.appendChild(cb);

            const body = document.createElement('div');
            body.className = 'repair-row-body';
            const title = document.createElement('div');
            title.className = 'repair-row-title';
            title.textContent = item.old;
            body.appendChild(title);
            const meta = document.createElement('div');
            meta.className = 'repair-row-meta repair-row-note';
            meta.textContent = item.note || 'Brak match-a';
            body.appendChild(meta);

            const manualWrap = document.createElement('div');
            manualWrap.className = 'repair-manual-wrap';
            const manualInput = document.createElement('input');
            manualInput.type = 'text';
            manualInput.className = 'repair-manual-input';
            manualInput.placeholder = 'Wpisz poprawny symbol, np. SSE:601088';
            manualInput.spellcheck = false;
            manualInput.addEventListener('input', () => {
                const val = manualInput.value.trim().toUpperCase();
                const valid = !!(val && TICKER_RE_REPAIR.test(val));
                cb.disabled = !valid;
                if (valid) cb.checked = true;
                syncRepairSubmitButtons();
            });
            manualWrap.appendChild(manualInput);

            const editBtn = document.createElement('button');
            editBtn.type = 'button';
            editBtn.className = 'btn btn-link repair-edit-btn';
            editBtn.textContent = 'Edytuj ręcznie';
            editBtn.addEventListener('click', () => {
                closeRepairModal();
                openRenameModal(item.old);
            });
            manualWrap.appendChild(editBtn);
            body.appendChild(manualWrap);

            row.appendChild(body);
            repairListEl.appendChild(row);
        });

        (items || []).filter(i => i.skipped).forEach(item => {
            const row = document.createElement('div');
            row.className = 'repair-row no-match skipped';
            const body = document.createElement('div');
            body.className = 'repair-row-body';
            const title = document.createElement('div');
            title.className = 'repair-row-title';
            title.textContent = item.old;
            body.appendChild(title);
            const meta = document.createElement('div');
            meta.className = 'repair-row-meta';
            meta.textContent = item.note || 'Pominięto';
            body.appendChild(meta);
            row.appendChild(body);
            repairListEl.appendChild(row);
        });

        if (repairListEl.children.length > 0) {
            repairListEl.hidden = false;
        }
        if (repairEmpty) {
            repairEmpty.hidden = repairListEl.children.length > 0;
        }
        syncRepairSubmitButtons();
    }

    function collectRepairRenames() {
        if (!repairListEl) return [];
        const renames = [];
        repairListEl.querySelectorAll('.repair-row.has-match, .repair-row.has-other-match').forEach(row => {
            if (!repairRowHasSelection(row)) return;
            const oldT = row.dataset.old;
            const radio = row.querySelector('input[type="radio"]:checked');
            const manual = row.querySelector('.repair-manual-input');
            const manualVal = (manual?.value || '').trim().toUpperCase();
            let newT = '';
            if (manualVal && TICKER_RE_REPAIR.test(manualVal)) {
                newT = manualVal;
            } else if (radio) {
                newT = radio.value;
            } else {
                newT = row.querySelector('.repair-new')?.textContent || '';
            }
            if (oldT && newT) renames.push({ old: oldT, new: newT.trim().toUpperCase() });
        });
        repairListEl.querySelectorAll('.repair-row.no-match:not(.skipped)').forEach(row => {
            if (!repairRowHasSelection(row)) return;
            const oldT = row.dataset.old;
            const manualVal = (row.querySelector('.repair-manual-input')?.value || '').trim().toUpperCase();
            if (oldT && manualVal) renames.push({ old: oldT, new: manualVal });
        });
        return renames;
    }

    async function submitRepairRenames(rerun) {
        const renames = collectRepairRenames();
        if (renames.length === 0) {
            setRepairError('Zaznacz co najmniej jedną propozycję lub wpisz symbol ręcznie.');
            return;
        }
        setRepairError('');
        if (repairSubmit) repairSubmit.disabled = true;
        if (repairSubmitRerun) repairSubmitRerun.disabled = true;
        try {
            const body = {
                renames,
                rerun: !!rerun,
                date_id: null,
            };
            const res = await fetch('/api/tickers/repair_no_data', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const detail = data?.detail || `HTTP ${res.status}`;
                throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
            }

            const appliedCount = (data.applied || []).length;
            const errorCount = (data.errors || []).length;
            renames.forEach(({ old, new: newT }) => {
                markTickerRenamed(old, newT);
            });
            if (appliedCount > 0) {
                if (rerun) {
                    showToast(`Naprawiono ${appliedCount} symbol(i). Scraper uruchomiony.`, 'success');
                } else {
                    showToast(`Zapisano ${appliedCount} symbol(i). Uruchom scraper ręcznie, gdy gotowe.`, 'success');
                }
            }
            if (errorCount > 0) {
                showToast(`Nie udało się naprawić ${errorCount} symbol(i).`, 'error');
            }
            if (rerun && data.scraper && data.scraper.status && data.scraper.status === 'no_data_empty') {
                showToast('Brak no-data tickerów do scrapera po renamie.', 'info');
            }
            closeRepairModal();
            await fetchDashboard();
        } catch (err) {
            setRepairError(String(err?.message || err || 'Błąd zapisu'));
            syncRepairSubmitButtons();
        }
    }

    async function loadRepairPreview() {
        try {
            const url = '/api/tickers/repair_no_data';
            const res = await fetch(url);
            if (!res.ok) {
                const txt = await res.text();
                throw new Error(`HTTP ${res.status}: ${txt}`);
            }
            const data = await res.json();
            if (repairLoading) repairLoading.hidden = true;
            renderRepairList(data.items || []);
        } catch (err) {
            if (repairLoading) repairLoading.hidden = true;
            setRepairError(`Nie udało się wczytać propozycji: ${err?.message || err}`);
        }
    }

    repairBtn?.addEventListener('click', () => {
        openRepairModal();
    });
    repairClose?.addEventListener('click', closeRepairModal);
    repairCancel?.addEventListener('click', closeRepairModal);
    repairModal?.addEventListener('click', (e) => {
        if (e.target === repairModal) closeRepairModal();
    });

    repairSubmit?.addEventListener('click', () => submitRepairRenames(false));
    repairSubmitRerun?.addEventListener('click', () => submitRepairRenames(true));

    // Start
    init();

});
