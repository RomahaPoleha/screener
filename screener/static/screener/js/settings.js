// ==========================================
// settings.js — НАСТРОЙКИ
// Открытие/применение модалки, вкладки, карточки скальпа
// ==========================================

// ==========================================
// ВКЛАДКИ НАСТРОЕК
// ==========================================
function initSettingsTabs() {
    document.querySelectorAll('.settings-nav-item').forEach(tab => {
        tab.replaceWith(tab.cloneNode(true));
    });
    document.querySelectorAll('.settings-nav-item').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.settings-nav-item').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.settings-tab-content').forEach(c => c.classList.remove('active'));
            tab.classList.add('active');
            const target = document.getElementById('tab-' + tab.dataset.tab);
            if (target) target.classList.add('active');
        });
    });
}

// ==========================================
// ОТКРЫТИЕ МОДАЛКИ НАСТРОЕК
// ==========================================
function openSettingsModal() {
    document.querySelectorAll('.settings-nav-item').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.settings-tab-content').forEach(c => c.classList.remove('active'));
    const firstTab = document.querySelector('.settings-nav-item[data-tab="display"]');
    const firstContent = document.getElementById('tab-display');
    if (firstTab) firstTab.classList.add('active');
    if (firstContent) firstContent.classList.add('active');

    const volHist = document.getElementById('showVolumeHistogram');
    if (volHist) volHist.checked = volumeHistogramEnabled;
    const drawTools = document.getElementById('showDrawingTools');
    if (drawTools) drawTools.checked = showDrawingTools;

    const reconToggle = document.getElementById('reconPanelToggle');
    if (reconToggle) {
        reconToggle.checked = reconEnabled;
        renderReconSettings();
    }

    renderScalpCards();

    const priceImpulseThr = document.getElementById('priceImpulseThreshold');
    if (priceImpulseThr) priceImpulseThr.value = priceImpulseThreshold;
    const priceImpulseWin = document.getElementById('priceImpulseWindow');
    if (priceImpulseWin) priceImpulseWin.value = priceImpulseWindow;

    const soundCheckbox = document.getElementById('soundToggleModal');
    if (soundCheckbox) soundCheckbox.checked = soundEnabled;
    const volAlertToggle = document.getElementById('volumeAlertToggle');
    if (volAlertToggle) volAlertToggle.checked = volumeAlertEnabled;
    const beepSlider = document.getElementById('alertBeepVolume');
    if (beepSlider) beepSlider.value = alertBeepVolume;
    const hourSlider = document.getElementById('hourSoundVolume');
    if (hourSlider) hourSlider.value = hourSoundVolume;

    initSettingsTabs();
    const modal = new bootstrap.Modal(document.getElementById('settingsModal'));
    modal.show();
}

// ==========================================
// ПРИМЕНЕНИЕ НАСТРОЕК
// ==========================================
function applySettings() {
    volumeHistogramEnabled = document.getElementById('showVolumeHistogram').checked;
    localStorage.setItem('volumeHistogramEnabled', volumeHistogramEnabled);
    if (volumeSeries) volumeSeries.applyOptions({ visible: volumeHistogramEnabled });

    const deltaHist = document.getElementById('showDeltaHistogram');
    if (deltaHist) {
        deltaEnabled = deltaHist.checked;
        localStorage.setItem('deltaEnabled', deltaEnabled);
        if (deltaSeries) deltaSeries.applyOptions({ visible: deltaEnabled });
    }

    showDrawingTools = document.getElementById('showDrawingTools').checked;
    localStorage.setItem('showDrawingTools', showDrawingTools);
    if (els && els.drawingToolsPanel) {
        els.drawingToolsPanel.style.display = showDrawingTools ? 'flex' : 'none';
    }

    const reconToggle = document.getElementById('reconPanelToggle');
    if (reconToggle) {
        reconEnabled = reconToggle.checked;
        localStorage.setItem('reconEnabled', reconEnabled);
        for (const ex of RECON_EXCHANGES) {
            const f = document.getElementById(`reconMinF_${ex.id}`);
            const s = document.getElementById(`reconMinS_${ex.id}`);
            if (f) reconMinVolumes[ex.id].futures = Math.max(10000, parseInt(f.value) || 10000);
            if (s) reconMinVolumes[ex.id].spot = Math.max(10000, parseInt(s.value) || 10000);
        }
        localStorage.setItem('reconMinVolumes', JSON.stringify(reconMinVolumes));
    }

    // 🔥 Синхронизация настроек с сервером и сохранение в localStorage (ОБЪЕДИНЕНО)
const priceImpulseThr = document.getElementById('priceImpulseThreshold');
const priceImpulseWin = document.getElementById('priceImpulseWindow');
if (priceImpulseThr || priceImpulseWin) {
    const settingsPayload = {};
    if (priceImpulseThr) {
        const thr = parseFloat(priceImpulseThr.value);
        settingsPayload.threshold = thr;
        if (thr > 0) {
            priceImpulseThreshold = thr;
            localStorage.setItem('priceImpulseThreshold', priceImpulseThreshold);
        }
    }
    if (priceImpulseWin) {
        const win = parseInt(priceImpulseWin.value);
        const safeWin = (win >= 120) ? 300 : 60;
        settingsPayload.window = safeWin;
        priceImpulseWindow = safeWin;
        localStorage.setItem('priceImpulseWindow', safeWin);
    }
    fetch('/api/impulse-settings/update/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settingsPayload)
    }).catch(err => console.warn('Impulse settings sync failed:', err));
}

    // Применяем настройки скальпа
    EXCHANGES_CONFIG.forEach(ex => {
        const enabledCheckbox = document.getElementById(`scalp-${ex.id}-toggle`);
        const fCheckbox = document.getElementById(`scalp-${ex.id}-f`);
        const sCheckbox = document.getElementById(`scalp-${ex.id}-s`);
        const fInput = document.getElementById(`scalp-${ex.id}-fv`);
        const sInput = document.getElementById(`scalp-${ex.id}-sv`);

        if (!scalpExchanges[ex.id]) {
            scalpExchanges[ex.id] = { enabled: false, markets: { futures: false, spot: false }, minVolumeFutures: 300000, minVolumeSpot: 200000 };
        }
        scalpExchanges[ex.id].enabled = enabledCheckbox ? enabledCheckbox.checked : false;
        scalpExchanges[ex.id].markets.futures = fCheckbox ? fCheckbox.checked : false;
        scalpExchanges[ex.id].markets.spot = sCheckbox ? sCheckbox.checked : false;
        scalpExchanges[ex.id].minVolumeFutures = fInput ? Math.max(300000, parseInt(fInput.value) || 300000) : 300000;
        scalpExchanges[ex.id].minVolumeSpot = sInput ? Math.max(200000, parseInt(sInput.value) || 200000) : 200000;
    });
    localStorage.setItem('scalpExchanges', JSON.stringify(scalpExchanges));
    scalpEnabled = Object.values(scalpExchanges).some(cfg => cfg.enabled && (cfg.markets.futures || cfg.markets.spot));

    if (currentSymbol && candleSeries) {
        clearScalpLines();
        previousScalpData = {};
    }
    if (currentSymbol) {
        if (scalpEnabled) startScalpUpdates(currentSymbol);
        else {
            if (scalpUpdateTimer) { clearInterval(scalpUpdateTimer); scalpUpdateTimer = null; }
        }
    }

    const volAlertToggle = document.getElementById('volumeAlertToggle');
    if (volAlertToggle) {
        volumeAlertEnabled = volAlertToggle.checked;
        localStorage.setItem('volumeAlertEnabled', volumeAlertEnabled);
    }

    // Добавлена безопасная проверка на существование элементов перед чтением .value
    const beepSlider = document.getElementById('alertBeepVolume');
    if (beepSlider) {
        alertBeepVolume = parseFloat(beepSlider.value);
        localStorage.setItem('alertBeepVolume', alertBeepVolume);
    }

    const hourSlider = document.getElementById('hourSoundVolume');
    if (hourSlider) {
        hourSoundVolume = parseFloat(hourSlider.value);
        localStorage.setItem('hourSoundVolume', hourSoundVolume);
    }

    updateAlertHistoryVisibility();

    const btn = document.getElementById('settingsBtn');
    if (btn) {
        if (densityEnabled || scalpEnabled || reconEnabled) {
            btn.style.background = '#f59e0b';
            btn.style.color = '#000000';
        } else {
            btn.style.background = '#2a2a2a';
            btn.style.color = '#ffffff';
        }
    }

    if (currentSymbol) {
        if (reconEnabled) startReconUpdates(currentSymbol);
        else stopReconUpdates();
    }

        // Управление импульсом (ИСПРАВЛЕНО: добавлен window.)
    if (volumeAlertEnabled) {
        if (!window.impulsePollingEnabled) {
            startImpulseWebSocket();
        }
    } else {
        if (window.impulsePollingEnabled) {
            stopImpulseWebSocket();
        }
    }

    const modalInstance = bootstrap.Modal.getInstance(document.getElementById('settingsModal'));
    if (modalInstance) {
        modalInstance.hide();
    }
}

// ==========================================
// КАРТОЧКИ SCALP В НАСТРОЙКАХ
// ==========================================
function renderScalpCards() {
    const container = document.getElementById('scalpExchangesContainer');
    if (!container) return;
    container.innerHTML = EXCHANGES_CONFIG.map(ex => {
        const cfg = scalpExchanges[ex.id] || { enabled: false, markets: { futures: false, spot: false }, minVolumeFutures: 300000, minVolumeSpot: 200000 };
        const isEnabled = cfg.enabled !== false;
        const fEnabled = cfg.markets && cfg.markets.futures;
        const sEnabled = cfg.markets && cfg.markets.spot;
        const fVol = cfg.minVolumeFutures || 300000;
        const sVol = cfg.minVolumeSpot || 200000;
        return `<div style="display:flex;align-items:center;gap:6px;">
            <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=32" onerror="this.style.display='none'" style="width:16px;height:16px;border-radius:2px;flex-shrink:0;">
            <span style="font-weight:600;font-size:12px;color:${ex.color};min-width:24px;">${ex.label || ex.name.substring(0, 2).toUpperCase()}</span>
            <span style="font-size:11px;color:#94a3b8;min-width:10px;">F:</span>
            <input type="number" id="scalp-${ex.id}-fv" value="${fVol}" min="300000" step="10000" style="width:70px;background:#1e293b;border:1px solid #475569;color:#fff;padding:4px 6px;border-radius:3px;font-size:12px;" ${!fEnabled || !isEnabled ? 'disabled' : ''}>
            <span style="font-size:11px;color:#94a3b8;min-width:10px;">S:</span>
            <input type="number" id="scalp-${ex.id}-sv" value="${sVol}" min="200000" step="10000" style="width:70px;background:#1e293b;border:1px solid #475569;color:#fff;padding:4px 6px;border-radius:3px;font-size:12px;" ${!sEnabled || !isEnabled ? 'disabled' : ''}>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:12px;color:#e2e8f0;">
                <input type="checkbox" id="scalp-${ex.id}-f" ${fEnabled ? 'checked' : ''} ${!isEnabled ? 'disabled' : ''} style="accent-color:#f59e0b;width:14px;height:14px;" onchange="document.getElementById('scalp-${ex.id}-fv').disabled = !this.checked">
                <span>F</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:12px;color:#e2e8f0;">
                <input type="checkbox" id="scalp-${ex.id}-s" ${sEnabled ? 'checked' : ''} ${!isEnabled ? 'disabled' : ''} style="accent-color:#f59e0b;width:14px;height:14px;" onchange="document.getElementById('scalp-${ex.id}-sv').disabled = !this.checked">
                <span>S</span>
            </label>
            <label style="position:relative;display:inline-block;width:36px;height:20px;cursor:pointer;">
                <input type="checkbox" id="scalp-${ex.id}-toggle" ${isEnabled ? 'checked' : ''} style="opacity:0;width:0;height:0;" onchange="toggleScalpExchange('${ex.id}', this.checked)">
                <span style="position:absolute;top:0;left:0;right:0;bottom:0;background:${isEnabled ? '#f59e0b' : '#475569'};border-radius:20px;transition:.3s;">
                    <span style="position:absolute;height:14px;width:14px;left:3px;bottom:3px;background:#ffffff;border-radius:50%;transition:.3s;transform:${isEnabled ? 'translateX(16px)' : 'translateX(0)'};"></span>
                </span>
            </label>
        </div>`;
    }).join('');
}

function toggleScalpExchange(exchangeId, enabled) {
    if (!scalpExchanges[exchangeId]) {
        scalpExchanges[exchangeId] = { enabled: false, markets: { futures: false, spot: false }, minVolumeFutures: 300000, minVolumeSpot: 200000 };
    }
    scalpExchanges[exchangeId].enabled = enabled;
    localStorage.setItem('scalpExchanges', JSON.stringify(scalpExchanges));

    const fCheckbox = document.getElementById(`scalp-${exchangeId}-f`);
    const sCheckbox = document.getElementById(`scalp-${exchangeId}-s`);
    const fInput = document.getElementById(`scalp-${exchangeId}-fv`);
    const sInput = document.getElementById(`scalp-${exchangeId}-sv`);

    if (fCheckbox) fCheckbox.disabled = !enabled;
    if (sCheckbox) sCheckbox.disabled = !enabled;
    if (!enabled) {
        if (fInput) fInput.disabled = true;
        if (sInput) sInput.disabled = true;
    } else {
        if (fInput) fInput.disabled = !fCheckbox.checked;
        if (sInput) sInput.disabled = !sCheckbox.checked;
    }

    renderScalpCards();
    applyScalpSettingsSilent();
}

function applyScalpSettingsSilent() {
    scalpEnabled = Object.values(scalpExchanges).some(cfg => cfg.enabled && (cfg.markets.futures || cfg.markets.spot));

    if (currentSymbol && candleSeries) {
        clearScalpLines();
        previousScalpData = {};
    }
    if (currentSymbol) {
        if (scalpEnabled) startScalpUpdates(currentSymbol);
        else {
            if (scalpUpdateTimer) { clearInterval(scalpUpdateTimer); scalpUpdateTimer = null; }
        }
    }

    const btn = document.getElementById('settingsBtn');
    if (btn) {
        if (densityEnabled || scalpEnabled || reconEnabled) {
            btn.style.background = '#f59e0b';
            btn.style.color = '#000000';
        } else {
            btn.style.background = '#2a2a2a';
            btn.style.color = '#ffffff';
        }
    }
}