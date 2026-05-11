document.addEventListener('DOMContentLoaded', () => {
    // Elements
    const historyList = document.getElementById('history-list');
    const resultsGrid = document.getElementById('results-grid');
    const cardTemplate = document.getElementById('ticker-card-template');
    const loadingOverlay = document.getElementById('loading-overlay');
    const errorMessage = document.getElementById('error-message');
    const errorText = document.getElementById('error-text');
    const currentDateTitle = document.getElementById('current-date-title');
    const recordCount = document.getElementById('record-count');
    const freshnessEl = document.getElementById('data-freshness');
    const refreshBtn = document.getElementById('refresh-btn');
    const expandAllBtn = document.getElementById('expand-all-btn');
    const searchInput = document.getElementById('search-input');
    const sortSelect = document.getElementById('sort-select');
    const chartPanel = document.getElementById('chart-panel');
    const pcaChartCanvas = document.getElementById('pcaChart');
    const chartTitle = document.getElementById('chart-title');
    const chartIntervalToggle = document.getElementById('chart-interval-toggle');
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
        activeView: 'ta_active_view',
        collapsedCards: 'ta_collapsed_cards',
        signalStrategy: 'ta_signal_strategy',
        signalInterval: 'ta_signal_interval',
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

    let currentDates = [];
    let activeDateId = null;
    let currentData = []; // Store raw rows for filtering
    let pcaChartInstance = null;
    let historyChartInstance = null;

    const ALLOWED_SORT = new Set(['default', 'pca-desc', 'pca-asc', 'ticker-asc', 'ticker-desc']);
    const ALLOWED_INTERVAL = new Set(['1D', '1W', '1M']);
    const ALLOWED_SIGNAL_INTERVALS = new Set(['D', 'W', 'M']);
    const BUY_SIGNALS = new Set(['buy', 'strong buy']);
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

    let currentSortMode = ALLOWED_SORT.has(loadPref(UI_KEYS.sortMode, 'default'))
        ? loadPref(UI_KEYS.sortMode, 'default') : 'default';
    let currentChartInterval = ALLOWED_INTERVAL.has(loadPref(UI_KEYS.chartInterval, '1D'))
        ? loadPref(UI_KEYS.chartInterval, '1D') : '1D';

    const storedCollapsed = loadPref(UI_KEYS.collapsedCards, []);
    const collapsedCards = new Set(Array.isArray(storedCollapsed) ? storedCollapsed : []);

    let signalStrategy = ALLOWED_STRATEGIES.has(loadPref(UI_KEYS.signalStrategy, 'all'))
        ? loadPref(UI_KEYS.signalStrategy, 'all') : 'all';
    let signalInterval = ALLOWED_SIGNAL_INTERVALS.has(loadPref(UI_KEYS.signalInterval, 'D'))
        ? loadPref(UI_KEYS.signalInterval, 'D') : 'D';
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

    // Initialize App
    async function init() {
        // Apply persisted sort + chart interval to controls before first render
        if (sortSelect) sortSelect.value = currentSortMode;
        if (chartIntervalToggle) {
            chartIntervalToggle.querySelectorAll('.interval-toggle-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.interval === currentChartInterval);
            });
        }
        if (chartTitle) chartTitle.textContent = `Wartość PCA (${currentChartInterval}) dla tickerów`;
        syncWlFilterChipsUI();

        await fetchHistory();
        const persistedView = loadPref(UI_KEYS.activeView, 'dashboard-view');
        if (persistedView === 'config-view') {
            switchView('config-view');
        }

        if (currentDates.length > 0) {
            selectDate(currentDates[0].id, currentDates[0].label);
        } else {
            showError("Brak dostępnych plików z danymi.");
            hideLoading();
        }

        startGlobalScraperPolling();
    }

    refreshBtn.addEventListener('click', () => {
        refreshBtn.classList.add('spinning');
        fetchHistory().then(() => {
            if (activeDateId) {
                const dateObj = currentDates.find(d => d.id === activeDateId);
                if(dateObj) selectDate(activeDateId, dateObj.label);
            }
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

        chartTitle.textContent = `Wartość PCA (${interval}) dla tickerów`;

        const term = searchInput.value.toLowerCase().trim();
        filterAndRenderCards(term);
    });

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
    }

    function updateStrategyEmptyBanner(filteredCount) {
        if (!strategyEmptyBannerEl) return;
        const isToolbarVisible = wlFilterToolbar && !wlFilterToolbar.hidden;
        if (isToolbarVisible && signalStrategy && signalStrategy !== 'all' && filteredCount === 0 && currentData.length > 0) {
            const label = strategyLabel(signalStrategy);
            strategyEmptyBannerEl.hidden = false;
            strategyEmptyBannerEl.textContent =
                `Brak tickerów spełniających strategię „${label}” dla interwału ${signalInterval}. ` +
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

    // Fetch History Dates
    async function fetchHistory() {
        try {
            const res = await fetch('/api/history');
            if (!res.ok) throw new Error("Nie udało się pobrać historii.");
            const data = await res.json();
            currentDates = data.dates;
            renderHistoryList();
        } catch (e) {
            console.error(e);
            showError("Błąd pobierania historii list CSV.");
        }
    }

    function renderHistoryList() {
        historyList.innerHTML = '';
        if (currentDates.length === 0) {
            historyList.innerHTML = '<p style="color:var(--text-muted); padding:10px;">Brak danych</p>';
            return;
        }

        currentDates.forEach(date => {
            const item = document.createElement('div');
            item.className = 'history-item';
            if (date.id === activeDateId) item.classList.add('active');
            
            item.innerHTML = `
                <i class="ph ph-calendar-blank"></i>
                <span>${escapeHtml(date.label)}</span>
            `;
            
            item.addEventListener('click', () => selectDate(date.id, date.label));
            historyList.appendChild(item);
        });
    }

    // Select a Date and Fetch Results
    async function selectDate(dateId, label) {
        activeDateId = dateId;
        renderHistoryList(); // Update active class
        
        currentDateTitle.textContent = label;
        resultsGrid.innerHTML = '';
        showLoading();
        hideError();

        try {
            const res = await fetch(`/api/results/${dateId}`);
            if (!res.ok) throw new Error("Brak danych dla wybranej daty.");
            const data = await res.json();
            
            currentData = data.data;
            if (Array.isArray(data.signal_strategies) && data.signal_strategies.length) {
                availableSignalStrategies = data.signal_strategies;
            }

            updateFreshnessIndicator(dateId);
            if (wlFilterToolbar) {
                wlFilterToolbar.hidden = currentData.length === 0;
            }
            syncWlFilterChipsUI();

            searchInput.value = '';
            filterAndRenderCards('');
            
            hideLoading();
        } catch (e) {
            console.error(e);
            showError(e.message);
            hideLoading();
        }
    }

    function filterAndRenderCards(searchTerm) {
        // Odfiltruj tickery ukryte po rename (zachowujemy wiersze w CSV, ale
        // w UI nie chcemy widzieć już starej nazwy).
        let filteredData = currentData.filter(row => !isTickerHidden(row['Ticker']));
        const hiddenCount = currentData.length - filteredData.length;
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

        const distinctTickers = new Set(filteredData.map(r => r['Ticker'] || '')).size;
        updateStrategyEmptyBanner(distinctTickers);

        recordCount.textContent = '';
        recordCount.appendChild(document.createTextNode(
            `${filteredData.length} rekordów (z ${currentData.length})`
        ));
        if (hiddenCount > 0) {
            recordCount.appendChild(document.createTextNode(
                ` · ${hiddenCount} ukrytych po zmianie nazwy `
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

    function updateFreshnessIndicator(dateId) {
        if (!freshnessEl) return;
        if (!dateId) { freshnessEl.hidden = true; return; }
        const datePart = String(dateId).slice(0, 10);
        const parsed = Date.parse(datePart + 'T00:00:00Z');
        if (!Number.isFinite(parsed)) { freshnessEl.hidden = true; return; }
        const deltaH = (Date.now() - parsed) / 3_600_000;
        let cls = 'fresh', label = 'Aktualne', icon = 'ph-check-circle';
        if (deltaH > 24) { cls = 'outdated'; label = 'Przeterminowane'; icon = 'ph-warning-circle'; }
        else if (deltaH > 6) { cls = 'stale'; label = 'Wczorajsze'; icon = 'ph-clock'; }
        freshnessEl.className = `data-freshness ${cls}`;
        freshnessEl.hidden = false;
        freshnessEl.title = `Dane z ${datePart}`;
        freshnessEl.textContent = label;
    }
    
    // Process and Render Chart — now uses currentChartInterval
    function renderChart(dataRows) {
        const filteredRows = dataRows.filter(r => r['Interval'] === currentChartInterval && r['PCA_Values']);
        
        if (filteredRows.length === 0) {
            chartPanel.classList.add('hidden');
            return;
        }
        
        chartPanel.classList.remove('hidden');
        
        // Parse and sort by PCA value descending
        const chartData = filteredRows.map(row => {
            const { valText, colorHex } = parsePCA(row['PCA_Values']);
            const numVal = parsePolishDecimal(valText);
            return {
                ticker: row['Ticker'],
                value: Number.isFinite(numVal) ? numVal : NaN,
                color: colorHex || 'rgba(59, 130, 246, 0.8)'
            };
        }).filter(d => Number.isFinite(d.value)) // tylko poprawnie sparsowane liczby (nie ukrywaj całego wykresu przy 0)
          .sort((a,b) => b.value - a.value);

        if (chartData.length === 0) {
            chartPanel.classList.add('hidden');
            return;
        }

        const labels = chartData.map(d => d.ticker);
        const data = chartData.map(d => d.value);
        const bgColors = chartData.map(d => d.color);

        if (pcaChartInstance) {
            pcaChartInstance.destroy();
        }

        const ctx = pcaChartCanvas.getContext('2d');
        Chart.defaults.color = '#94a3b8';
        Chart.defaults.font.family = 'Inter';
        
        pcaChartInstance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: `PCA (${currentChartInterval})`,
                    data: data,
                    backgroundColor: bgColors,
                    borderRadius: 4,
                    borderWidth: 0,
                    barPercentage: 0.6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        backgroundColor: 'rgba(15, 17, 21, 0.9)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: 'rgba(255, 255, 255, 0.1)',
                        borderWidth: 1,
                        padding: 12,
                        displayColors: false,
                        callbacks: {
                            label: function(context) {
                                return `PCA: ${context.parsed.y}`;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.05)',
                        },
                        border: {
                            dash: [4, 4],
                            display: false
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        },
                        border: {
                            display: false
                        },
                        ticks: {
                            maxRotation: 45,
                            minRotation: 45
                        }
                    }
                }
            }
        });
    }

    /** Polskie / TV: "61,33", "1 234,56", "1 234,56" (NBSP) */
    function parsePolishDecimal(text) {
        if (text == null || text === '' || text === '--') return NaN;
        let s = String(text).trim().replace(/\u00a0/g, ' ').replace(/\s/g, '');
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

    // Process and Render Cards
    function renderCards(dataRows) {
        resultsGrid.innerHTML = '';
        if (dataRows.length === 0) {
            const hint = currentData.length === 0
                ? 'Brak danych dla wybranej daty.'
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
            case 'pca-desc':
                sortedKeys.sort((a, b) => cmpPcaDesc(getGroupPCA(groupedData[a]), getGroupPCA(groupedData[b])));
                break;
            case 'pca-asc':
                sortedKeys.sort((a, b) => cmpPcaAsc(getGroupPCA(groupedData[a]), getGroupPCA(groupedData[b])));
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

            cardClone.querySelector('.ticker-name').textContent = ticker;
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

            // Restore collapsed state from prefs (default = collapsed from template)
            if (!collapsedCards.has(ticker) && collapsedCards.size > 0) {
                cardEl.classList.remove('collapsed');
            }

            const rescrapeBtn = cardClone.querySelector('.card-rescrape-btn');
            const historyBtn = cardClone.querySelector('.card-history-btn');
            const renameBtn = cardClone.querySelector('.card-rename-btn');
            if (rescrapeBtn) {
                if (rerunningTickers.has(ticker)) {
                    rescrapeBtn.classList.add('spinning');
                    rescrapeBtn.disabled = true;
                }
                rescrapeBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    requestRescrapeTicker(ticker, rescrapeBtn);
                });
            }
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
            
            const badgesContainer = cardClone.querySelector('.watchlist-badges');
            const dRow = rows.find(r => (r['Interval'] || '').toUpperCase() === '1D') || rows[0];
            const wRow = rows.find(r => (r['Interval'] || '').toUpperCase() === '1W');
            const mRow = rows.find(r => (r['Interval'] || '').toUpperCase() === '1M');
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

                colClone.querySelector('.hts-fast-high').innerHTML = parseValueWithColor(row['HTS Panel_Fast_High']);
                colClone.querySelector('.hts-fast-low').innerHTML = parseValueWithColor(row['HTS Panel_Fast_Low']);
                colClone.querySelector('.hts-slow-high').innerHTML = parseValueWithColor(row['HTS Panel_Slow_High']);
                colClone.querySelector('.hts-slow-low').innerHTML = parseValueWithColor(row['HTS Panel_Slow_Low']);

                // -- MacD Panel --
                const macdTrendNode = colClone.querySelector('.macd-trend');
                setTrendTextAndColor(macdTrendNode, row['MacD_Trend'] || 'Brak');

                const macdCrossNode = colClone.querySelector('.macd-cross');
                setCrossTag(macdCrossNode, row['MacD_Cross'] || 'Brak');

                colClone.querySelector('.macd-fast').innerHTML = parseValueWithColor(row['MacD_Fast_High']);
                colClone.querySelector('.macd-slow').innerHTML = parseValueWithColor(row['MacD_Slow_Low']); 
                
                if (row['MacD_Fast_Low']) colClone.querySelector('.macd-fast').innerHTML += ` <br/> ` + parseValueWithColor(row['MacD_Fast_Low']);
                if (row['MacD_Slow_High']) {
                     colClone.querySelector('.macd-slow').innerHTML = parseValueWithColor(row['MacD_Slow_High']) + ` <br/> ` + parseValueWithColor(row['MacD_Slow_Low']);
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

    function setTrendTextAndColor(node, val) {
        val = (val == null ? '' : String(val)).trim();
        node.textContent = val;
        node.className = 'value trend-value'; // reset
        const low = val.toLowerCase();
        if (low.includes('wzrostowy') || low === 'up') {
            node.classList.add('trend-up');
        } else if (low.includes('spadkowy') || low === 'down') {
            node.classList.add('trend-down');
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
            fetchHistory();
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
        // progressStr might be "15/175"
        if (!progressStr || !progressStr.includes('/')) return 0;
        const parts = progressStr.split('/');
        const current = parseInt(parts[0], 10);
        const total = parseInt(parts[1], 10);
        if (total === 0) return 0;
        return (current / total) * 100;
    }

    async function pollScraperStatus() {
        if (statusInterval) clearInterval(statusInterval);
        
        const fetchStatus = async () => {
            try {
                const res = await fetch('/api/scraper/status');
                if (res.ok) {
                    const data = await res.json();

                    if (previousScraperStatus === 'running' && data.status === 'done') {
                        fetchHistory();
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
                        currentTickerLabel.textContent = data.current_ticker || "";
                        
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
            const res = await fetch('/api/scraper/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.status === 'started' || data.status === 'already_running') {
                pollScraperStatus();
            } else if (data.status === 'no_data_empty') {
                showToast({
                    type: 'info',
                    title: 'Brak tickerów',
                    message: data.message || 'Nie znaleziono tickerów z Brak Danych do odświeżenia.',
                });
            } else {
                alert("Błąd: " + data.message);
            }
        } catch(e) {
            alert("Błąd połączenia z API");
        }
    }

    btnRunAll.addEventListener('click', async () => {
        const ok = await confirmDialog({
            title: 'Pobrać dane dla wszystkich tickerów?',
            message: 'Operacja może potrwać kilka minut. Zostanie uruchomione pełne pobieranie.',
            confirmLabel: 'Uruchom',
            cancelLabel: 'Anuluj',
        });
        if (ok) startScraper([]);
    });

    btnRunNoData?.addEventListener('click', async () => {
        const ok = await confirmDialog({
            title: 'Odświeżyć tylko tickery z „Brak Danych”?',
            message: 'Uruchomione zostaną tylko tickery oznaczone jako Brak Danych / NO_DATA w najnowszym pliku wynikowym.',
            confirmLabel: 'Odśwież',
            cancelLabel: 'Anuluj',
        });
        if (ok) startScraper([], { noDataOnly: true });
    });

    btnRunSelected.addEventListener('click', () => {
        const cbs = document.querySelectorAll('.ticker-select-cb:checked');
        const selectedTickers = Array.from(cbs).map(cb => cb.value);
        if (selectedTickers.length === 0) {
            alert("Nie zaznaczono żadnych tickerów!");
            return;
        }
        startScraper(selectedTickers);
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
    async function requestRescrapeTicker(ticker, btnEl) {
        if (!ticker || rerunningTickers.has(ticker)) return;
        rerunningTickers.add(ticker);
        if (btnEl) {
            btnEl.classList.add('spinning');
            btnEl.disabled = true;
        }
        try {
            const res = await fetch('/api/scraper/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tickers: [ticker] }),
            });
            const data = await res.json();
            if (data.status === 'started') {
                showToast({ type: 'info', title: 'Pobieranie', message: `${ticker}: zlecono ponowne pobranie.` });
            } else if (data.status === 'already_running') {
                // Scraper już coś robi — najprawdopodobniej pełny run. Pokaż
                // użytkownikowi co leci i zaproponuj zatrzymanie.
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
            } else {
                showToast({ type: 'error', title: 'Błąd', message: data.message || 'Nie udało się uruchomić pobierania.' });
            }
        } catch (e) {
            showToast({ type: 'error', title: 'Błąd połączenia', message: String(e.message || e) });
        } finally {
            // Spinner gaśnie po zakończeniu scrapera (globalny polling) lub po 60s jako bezpiecznik
            setTimeout(() => {
                rerunningTickers.delete(ticker);
                const stillBtn = document.querySelector(`.ticker-card[data-ticker="${cssEscape(ticker)}"] .card-rescrape-btn`);
                if (stillBtn) { stillBtn.classList.remove('spinning'); stillBtn.disabled = false; }
            }, 60_000);
        }
    }

    function cssEscape(value) {
        if (window.CSS && typeof CSS.escape === 'function') return CSS.escape(value);
        return String(value).replace(/["\\]/g, '\\$&');
    }

    function clearRescrapeSpinner(ticker) {
        rerunningTickers.delete(ticker);
        const btn = document.querySelector(`.ticker-card[data-ticker="${cssEscape(ticker)}"] .card-rescrape-btn`);
        if (btn) { btn.classList.remove('spinning'); btn.disabled = false; }
    }

    // ==========================================
    // HISTORICAL PCA CHART MODAL
    // ==========================================
    const historyModal = document.getElementById('ticker-history-modal');
    const historyModalTitle = document.getElementById('ticker-history-title');
    const historyModalSubtitle = document.getElementById('ticker-history-subtitle');
    const historyModalClose = document.getElementById('ticker-history-close');
    const historyIntervalToggle = document.getElementById('ticker-history-interval-toggle');
    const historyCanvas = document.getElementById('ticker-history-chart');
    const historyEmptyEl = document.getElementById('ticker-history-empty');

    function openHistoryModal(ticker) {
        if (!historyModal || !ticker) return;
        currentHistoryTicker = ticker;
        currentHistoryInterval = ALLOWED_INTERVAL.has(currentChartInterval) ? currentChartInterval : '1D';
        historyModalTitle.textContent = `Historia PCA — ${ticker}`;
        historyModalSubtitle.textContent = 'Ładowanie…';
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
            const res = await fetch(`/api/ticker/${encodeURIComponent(currentHistoryTicker)}/history?interval=${encodeURIComponent(currentHistoryInterval)}`);
            if (!res.ok) throw new Error('Błąd pobierania historii');
            const data = await res.json();
            renderHistoryChart(data.history || []);
        } catch (e) {
            console.error(e);
            if (historyModalSubtitle) historyModalSubtitle.textContent = 'Błąd pobierania historii';
            renderHistoryChart([]);
        }
    }

    function renderHistoryChart(points) {
        if (historyChartInstance) {
            historyChartInstance.destroy();
            historyChartInstance = null;
        }
        const clean = points.filter(p => Number.isFinite(p.value));
        if (historyModalSubtitle) {
            historyModalSubtitle.textContent = clean.length === 0
                ? 'Brak punktów danych'
                : `${clean.length} punktów — interwał ${currentHistoryInterval}`;
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
                    label: `PCA (${currentHistoryInterval})`,
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
                            label: (c) => `PCA: ${c.parsed.y}`,
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
            const cur = data.current_ticker ? ` — ${data.current_ticker}` : '';
            setGlobalBanner(true, {
                text: `Scraper w toku${cur} ${data.progress ? '(' + data.progress + ')' : ''}`.trim(),
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
        fetchHistory().then(() => {
            if (!activeDateId) return;
            if (activeDateId.slice(0, 10) === todayDateId()) {
                const obj = currentDates.find(d => d.id === activeDateId);
                if (obj) selectDate(activeDateId, obj.label);
            }
        });
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

    function openRenameModal(ticker) {
        if (!renameModal || !ticker) return;
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
                setRenameError(typeof detail === 'string' ? detail : 'Nie udało się zmienić nazwy.');
                if (renameSubmitBtn) renameSubmitBtn.disabled = false;
                return;
            }
            // Ukryj starą kartę w bieżącym widoku (CSV zostaje nietknięty,
            // ale użytkownik nie chce już widzieć starego symbolu). Ukrywamy
            // zarówno to, co wpisał użytkownik, jak i realnie zmatchowany
            // symbol z configu (np. LULU.O → LULU), żeby nic nie zostało.
            const matchedOld = (data && typeof data.old === 'string' && data.old) || oldTicker;
            markTickerRenamed(oldTicker, newTicker);
            if (matchedOld && matchedOld.toUpperCase() !== oldTicker) {
                markTickerRenamed(matchedOld, newTicker);
            }
            // Dodatkowo ukryj wszystkie wiersze z tą samą bazą (prefiks przed
            // pierwszą kropką) — spójnie z logiką fuzzy-match w backendzie.
            const baseOld = oldTicker.split('.', 1)[0];
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
    const repairListEl = document.getElementById('repair-symbols-list');
    const repairLoading = document.getElementById('repair-symbols-loading');
    const repairEmpty = document.getElementById('repair-symbols-empty');
    const repairError = document.getElementById('repair-symbols-error');
    const repairRerun = document.getElementById('repair-symbols-rerun');

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
        loadRepairPreview();
    }

    function renderRepairList(items) {
        if (!repairListEl) return;
        repairListEl.innerHTML = '';
        const usable = (items || []).filter(i => Array.isArray(i.candidates) && i.candidates.length > 0);
        const skipped = (items || []).filter(i => !Array.isArray(i.candidates) || i.candidates.length === 0);

        usable.forEach((item, idx) => {
            const row = document.createElement('div');
            row.className = 'repair-row has-match';
            row.dataset.old = item.old;

            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = true;
            cb.id = `repair-cb-${idx}`;
            cb.setAttribute('aria-label', `Zastosuj rename dla ${item.old}`);
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
                const candList = document.createElement('div');
                candList.className = 'repair-row-candidates';
                item.candidates.forEach((c, ci) => {
                    const lbl = document.createElement('label');
                    lbl.className = 'repair-candidate';
                    const radio = document.createElement('input');
                    radio.type = 'radio';
                    radio.name = `repair-cand-${idx}`;
                    radio.value = c.new;
                    if (ci === 0) radio.checked = true;
                    radio.addEventListener('change', () => {
                        newEl.textContent = c.new;
                    });
                    lbl.appendChild(radio);
                    const exch = document.createElement('span');
                    exch.className = 'repair-exchange';
                    exch.textContent = c.exchange || '';
                    lbl.appendChild(exch);
                    lbl.appendChild(document.createTextNode(`${c.new} · ${c.description || '—'}`));
                    candList.appendChild(lbl);
                });
                body.appendChild(candList);
            }

            row.appendChild(body);
            repairListEl.appendChild(row);
        });

        skipped.forEach(item => {
            const row = document.createElement('div');
            row.className = 'repair-row no-match';

            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.disabled = true;
            row.appendChild(cb);

            const body = document.createElement('div');
            body.className = 'repair-row-body';
            const title = document.createElement('div');
            title.className = 'repair-row-title';
            title.textContent = item.old;
            body.appendChild(title);
            const meta = document.createElement('div');
            meta.className = 'repair-row-meta';
            meta.textContent = item.note || 'Brak match-a';
            body.appendChild(meta);

            row.appendChild(body);
            repairListEl.appendChild(row);
        });

        if (repairListEl.children.length > 0) {
            repairListEl.hidden = false;
        }
        if (repairEmpty) {
            repairEmpty.hidden = usable.length > 0 || skipped.length > 0;
        }
        if (repairSubmit) repairSubmit.disabled = usable.length === 0;
    }

    async function loadRepairPreview() {
        try {
            const url = activeDateId
                ? `/api/tickers/repair_no_data?date_id=${encodeURIComponent(activeDateId)}`
                : '/api/tickers/repair_no_data';
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

    repairSubmit?.addEventListener('click', async () => {
        if (!repairListEl) return;
        const renames = [];
        repairListEl.querySelectorAll('.repair-row.has-match').forEach(row => {
            const cb = row.querySelector('input[type="checkbox"]');
            if (!cb || !cb.checked) return;
            const oldT = row.dataset.old;
            const radio = row.querySelector('input[type="radio"]:checked');
            const newT = radio ? radio.value : (row.querySelector('.repair-new')?.textContent || '');
            if (oldT && newT) renames.push({ old: oldT, new: newT });
        });
        if (renames.length === 0) {
            setRepairError('Zaznacz co najmniej jedną propozycję.');
            return;
        }
        setRepairError('');
        repairSubmit.disabled = true;
        try {
            const body = {
                renames,
                rerun: !!repairRerun?.checked,
                date_id: activeDateId || null,
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
                showToast(`Naprawiono ${appliedCount} symbol(i).`, 'success');
            }
            if (errorCount > 0) {
                showToast(`Nie udało się naprawić ${errorCount} symbol(i).`, 'error');
            }
            if (data.scraper && data.scraper.status && data.scraper.status !== 'no_data_empty') {
                showToast('Scraper uruchomiony dla naprawionych tickerów.', 'info');
            }
            closeRepairModal();
            await fetchHistory();
            const obj = currentDates.find(d => d.id === activeDateId);
            if (obj) selectDate(activeDateId, obj.label);
        } catch (err) {
            setRepairError(String(err?.message || err || 'Błąd zapisu'));
            repairSubmit.disabled = false;
        }
    });

    // Start
    init();

});
