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
    const refreshBtn = document.getElementById('refresh-btn');
    const searchInput = document.getElementById('search-input');
    const sortSelect = document.getElementById('sort-select');
    const chartPanel = document.getElementById('chart-panel');
    const pcaChartCanvas = document.getElementById('pcaChart');
    const chartTitle = document.getElementById('chart-title');
    const chartIntervalToggle = document.getElementById('chart-interval-toggle');

    let currentDates = [];
    let activeDateId = null;
    let currentData = []; // Store raw rows for filtering
    let pcaChartInstance = null;
    let currentSortMode = 'default';
    let currentChartInterval = '1D'; // Current chart interval

    // Initialize App
    async function init() {
        await fetchHistory();
        if (currentDates.length > 0) {
            selectDate(currentDates[0].id, currentDates[0].label);
        } else {
            showError("Brak dostępnych plików z danymi.");
            hideLoading();
        }
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
        currentSortMode = e.target.value;
        const term = searchInput.value.toLowerCase().trim();
        filterAndRenderCards(term);
    });

    // Chart interval toggle
    chartIntervalToggle.addEventListener('click', (e) => {
        const btn = e.target.closest('.interval-toggle-btn');
        if (!btn) return;
        
        const interval = btn.dataset.interval;
        if (interval === currentChartInterval) return;
        
        currentChartInterval = interval;
        
        // Update active button
        chartIntervalToggle.querySelectorAll('.interval-toggle-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        
        // Update chart title
        chartTitle.textContent = `Wartość PCA (${interval}) dla tickerów`;
        
        // Re-render chart with filtered data
        const term = searchInput.value.toLowerCase().trim();
        let filteredData = currentData;
        if (term) {
            filteredData = currentData.filter(row => {
                const ticker = (row['Ticker'] || '').toLowerCase();
                const company = (row['Company_Name'] || '').toLowerCase();
                return ticker.includes(term) || company.includes(term);
            });
        }
        renderChart(filteredData);
    });

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
                <span>${date.label}</span>
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
            
            currentData = data.data; // Store for filtering
            
            // clear search text on new load
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
        let filteredData = currentData;
        if (searchTerm) {
            filteredData = currentData.filter(row => {
                const ticker = (row['Ticker'] || '').toLowerCase();
                const company = (row['Company_Name'] || '').toLowerCase();
                return ticker.includes(searchTerm) || company.includes(searchTerm);
            });
        }
        
        recordCount.textContent = `${filteredData.length} rekordów (z ${currentData.length})`;
        renderCards(filteredData);
        renderChart(filteredData);
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
            const numVal = parseFloat(valText.replace(',', '.')); // Handle polish commas
            return {
                ticker: row['Ticker'],
                value: isNaN(numVal) ? 0 : numVal,
                color: colorHex || 'rgba(59, 130, 246, 0.8)'
            };
        }).filter(d => d.value !== 0) // Remove zero-value entries from chart
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

    // Get PCA value for a ticker group (based on current chart interval) for sorting
    function getGroupPCA(rows) {
        const targetRow = rows.find(r => r['Interval'] === currentChartInterval);
        if (!targetRow || !targetRow['PCA_Values']) return 0;
        const { valText } = parsePCA(targetRow['PCA_Values']);
        const num = parseFloat(valText.replace(',', '.'));
        return isNaN(num) ? 0 : num;
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
            
            // Cross indicator
            let crossIcon = '';
            if (htsCross.toLowerCase().includes('bull')) crossIcon = '🟢';
            else if (htsCross.toLowerCase().includes('bear')) crossIcon = '🔴';
            
            // PCA color dot
            const dotColor = colorHex || '#555';
            const pcaDisplay = valText !== '--' ? valText : '';
            
            let pillHTML = `<span class="summary-pill">`;
            pillHTML += `<span class="pill-label">${interval}</span>`;
            if (pcaDisplay) {
                pillHTML += `<span class="pill-dot" style="background:${dotColor}; box-shadow: 0 0 4px ${dotColor};"></span>`;
                pillHTML += `<span>${pcaDisplay}</span>`;
            }
            if (trendDir) {
                pillHTML += `<span class="pill-trend ${trendClass}">${trendDir}</span>`;
            }
            if (crossIcon) pillHTML += ` ${crossIcon}`;
            pillHTML += `</span>`;
            
            pills.push(pillHTML);
        });
        
        return pills.join('');
    }

    // Process and Render Cards
    function renderCards(dataRows) {
        resultsGrid.innerHTML = '';
        if (dataRows.length === 0) {
            resultsGrid.innerHTML = `
                <div class="empty-state" style="grid-column: 1 / -1;">
                    <i class="ph ph-magnifying-glass"></i>
                    <p>Brak wyników dla tego wyszukiwania.</p>
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
                sortedKeys.sort((a, b) => getGroupPCA(groupedData[b]) - getGroupPCA(groupedData[a]));
                break;
            case 'pca-asc':
                sortedKeys.sort((a, b) => getGroupPCA(groupedData[a]) - getGroupPCA(groupedData[b]));
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

        function escapeHtml(str) {
            if (str == null || str === '') return '';
            const d = document.createElement('div');
            d.textContent = String(str);
            return d.innerHTML;
        }

        // Render one card per Ticker
        sortedKeys.forEach(ticker => {
            const rows = groupedData[ticker];
            const skipRow = rows.find(r => (r['Scrape_Status'] || '').toUpperCase() === 'SKIPPED');
            const companyName = rows[0]['Company_Name'] || 'Nieznana';
            
            // Clone Ticker Card template
            const cardClone = cardTemplate.content.cloneNode(true);
            const cardEl = cardClone.querySelector('.ticker-card');
            
            cardClone.querySelector('.ticker-name').textContent = ticker;
            cardClone.querySelector('.company-name').textContent = companyName;
            
            // Build summary pills for collapsed view
            const summaryContainer = cardClone.querySelector('.card-summary');
            if (skipRow) {
                cardEl.classList.add('ticker-skipped');
                const err = skipRow['Scrape_Error'] || 'Nie udało się pobrać danych';
                summaryContainer.innerHTML = `<span class="skip-error-banner">⚠ Pominięty ticker</span>`;
                cardClone.querySelector('.company-name').textContent = '—';
            } else {
                summaryContainer.innerHTML = buildSummaryPills(rows);
            }
            
            // Add watchlist signal badges if available
            const badgesContainer = cardClone.querySelector('.watchlist-badges');
            const firstRow = rows[0];
            if (firstRow['WL_Daily_Signal']) {
                addSignalBadge(badgesContainer, 'D', firstRow['WL_Daily_Signal']);
            }
            if (firstRow['WL_Weekly_Signal']) {
                addSignalBadge(badgesContainer, 'W', firstRow['WL_Weekly_Signal']);
            }
            if (firstRow['WL_Monthly_Signal']) {
                addSignalBadge(badgesContainer, 'M', firstRow['WL_Monthly_Signal']);
            }

            // Click header to toggle collapse
            const header = cardClone.querySelector('.card-header');
            header.addEventListener('click', () => {
                cardEl.classList.toggle('collapsed');
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
        val = val.trim();
        node.textContent = val;
        node.className = 'value trend-value'; // reset
        if (val.toLowerCase().includes('wzrostowy') || val.toLowerCase() === 'up') {
            node.classList.add('trend-up');
        } else if (val.toLowerCase().includes('spadkowy') || val.toLowerCase() === 'down') {
            node.classList.add('trend-down');
        } else {
            node.classList.add('trend-neutral');
        }
    }

    function setCrossTag(node, val) {
        val = val.trim();
        // Remove parenthesis explanation for cleaner UI if present
        let displayVal = val;
        if(val.includes('(')) {
            displayVal = val.split('(')[0].trim();
            node.title = val; // Put full text in tooltip
        }
        
        node.textContent = displayVal;
        node.className = 'value tag'; // reset
        
        if (val.toLowerCase().includes('bull')) {
            node.classList.add('bull');
        } else if (val.toLowerCase().includes('bear')) {
            node.classList.add('bear');
        } else {
            node.classList.add('neutral');
            node.textContent = 'Brak';
        }
    }

    // Parse values like "74 635,86 (Niebieski)" or "-1 216,62 (color: rgb(0, 255, 0);)"
    function parseValueWithColor(rawStr) {
        if (!rawStr || rawStr === 'NaN' || rawStr === 'undefined') return '--';
        
        const match = rawStr.match(/(.*?)\s*\((.*?)\)/);
        if (match) {
            const val = match[1].trim();
            const colorInfo = match[2].trim();
            let colorSpan = '';
            
            // Extract RGB if present
            const rgbMatch = colorInfo.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
            if (rgbMatch) {
                const r = rgbMatch[1];
                const g = rgbMatch[2];
                const b = rgbMatch[3];
                colorSpan = `<span style="display:inline-block; width:8px; height:8px; border-radius:50%; background-color: rgb(${r},${g},${b}); margin-left: 6px; box-shadow: 0 0 5px rgba(${r},${g},${b},0.8);"></span>`;
            } else if (colorInfo.toLowerCase() !== 'brak') {
                // If text color like "Niebieski"
                let mappedColor = '#fff';
                if(colorInfo.toLowerCase().includes('niebieski')) mappedColor = '#3b82f6';
                if(colorInfo.toLowerCase().includes('zielony')) mappedColor = '#10b981';
                if(colorInfo.toLowerCase().includes('czerwony')) mappedColor = '#ef4444';
                
                colorSpan = `<span style="display:inline-block; width:8px; height:8px; border-radius:50%; background-color: ${mappedColor}; margin-left: 6px;"></span>`;
            }
            return `${val} ${colorSpan}`;
        }
        return rawStr;
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

    // Parse PCA value like "61,33 (color: rgb(255, 243, 0);)" — improved regex
    function parsePCA(rawStr) {
        if (!rawStr || rawStr === 'Brak danych na wykresie') return { valText: '--', colorHex: null };
        
        // Try matching: value (color: rgb(R, G, B);)
        const match = rawStr.match(/^(.*?)\s*\(color:\s*(rgb\(\d+,\s*\d+,\s*\d+\))\s*;?\s*\)/);
        if (match) {
            return {
                valText: match[1]?.trim(),
                colorHex: match[2]?.trim()
            };
        }
        
        // Try matching: value (ColorName)
        const namedMatch = rawStr.match(/^(.*?)\s*\(([^)]+)\)/);
        if (namedMatch) {
            const colorName = namedMatch[2].trim().toLowerCase();
            let mappedColor = null;
            if (colorName.includes('czerwon')) mappedColor = 'rgb(239, 68, 68)';
            else if (colorName.includes('niebiesk')) mappedColor = 'rgb(59, 130, 246)';
            else if (colorName.includes('zielon')) mappedColor = 'rgb(16, 185, 129)';
            else if (colorName.includes('pomarańcz')) mappedColor = 'rgb(245, 158, 11)';
            
            return {
                valText: namedMatch[1]?.trim(),
                colorHex: mappedColor
            };
        }
        
        // Fallback: just the raw string as value, no color
        return { valText: rawStr.trim(), colorHex: null };
    }

    // ==========================================
    // CONFIG & SCRAPER PANEL LOGIC
    // ==========================================
    
    let currentConfig = {
        tickers: [],
        intervals: [],
        indicators: [],
        auto_schedule: { enabled: false, hour: 7, minute: 30 },
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

    // Scraper elements
    const statusText = document.getElementById('scraper-status-text');
    const progressContainer = document.getElementById('scraper-progress-container');
    const progressText = document.getElementById('scraper-progress-text');
    const currentTickerLabel = document.getElementById('scraper-current-ticker');
    const progressFill = document.getElementById('scraper-progress-fill');
    const btnRunAll = document.getElementById('btn-run-scraper-all');
    const btnRunSelected = document.getElementById('btn-run-scraper-selected');
    const btnStopScraper = document.getElementById('btn-stop-scraper');

    // Tab Navigation
    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = link.getAttribute('data-target');
            
            // Switch active link
            navLinks.forEach(l => l.classList.remove('active'));
            link.classList.add('active');
            
            // Switch view
            viewPanels.forEach(panel => {
                if (panel.id === targetId) {
                    panel.classList.add('active');
                } else {
                    panel.classList.remove('active');
                }
            });

            // Special actions on tab open
            if (targetId === 'config-view') {
                loadConfig();
                pollScraperStatus(); // Start polling if we check config tab
            } else if (targetId === 'dashboard-view') {
                fetchHistory();
            }
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
            const item = document.createElement('div');
            item.className = 'ticker-item';
            item.innerHTML = `
                <div class="ticker-item-left">
                    <label class="checkbox-container">
                        <input type="checkbox" class="ticker-select-cb" value="${t}">
                        <span class="checkmark"></span>
                    </label>
                    <span class="ticker-name-bold" style="font-weight: 500">${t}</span>
                </div>
                <button class="btn-remove-ticker" data-ticker="${t}"><i class="ph ph-trash"></i></button>
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
            const tag = document.createElement('span');
            tag.className = 'tag-item';
            tag.innerHTML = `
                ${ind} 
                <button class="tag-remove" data-ind="${ind}"><i class="ph ph-x"></i></button>
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

        const sched = currentConfig.auto_schedule || { enabled: false, hour: 7, minute: 30 };
        autoScheduleEnabled.checked = !!sched.enabled;
        autoScheduleHour.value = String(Math.max(0, Math.min(23, parseInt(sched.hour, 10) || 7)));
        autoScheduleMinute.value = String(Math.max(0, Math.min(59, parseInt(sched.minute, 10) || 0)));
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
                        btnRunSelected.disabled = true;
                        btnStopScraper.classList.remove('hidden');
                        
                        progressText.textContent = data.progress || "Uruchamianie...";
                        currentTickerLabel.textContent = data.current_ticker || "";
                        
                        const pct = parseScraperProgress(data.progress);
                        progressFill.style.width = pct + '%';
                    } else {
                        progressContainer.classList.add('hidden');
                        btnRunAll.disabled = false;
                        btnRunSelected.disabled = false;
                        btnStopScraper.classList.add('hidden');
                        
                        if (data.status === 'done') {
                            statusText.textContent = 'Zakończono';
                            errorMsgContainer.classList.add('hidden');
                        } else if (data.status === 'error') {
                            statusText.textContent = 'Błąd';
                            errorMsgContainer.textContent = data.error || "Wystąpił nieznany błąd podczas działania.";
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

    async function startScraper(tickersOverride = []) {
        try {
            const res = await fetch('/api/scraper/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tickers: tickersOverride })
            });
            const data = await res.json();
            if (data.status === 'started' || data.status === 'already_running') {
                pollScraperStatus();
            } else {
                alert("Błąd: " + data.message);
            }
        } catch(e) {
            alert("Błąd połączenia z API");
        }
    }

    btnRunAll.addEventListener('click', () => {
        if(confirm("Czy na pewno chcesz pobrać dane dla wszystkich tickerów? Może to potrwać długo.")) {
            startScraper([]); // empty array = read from config by backend
        }
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
        if(confirm("Zatrzymać aktualnie działający scraper?")) {
            try {
                await fetch('/api/scraper/stop', { method: 'POST' });
                // Do one final poll right away
                setTimeout(pollScraperStatus, 1000);
            } catch(e) {
                console.error(e);
            }
        }
    });

    // Start
    init();

});
