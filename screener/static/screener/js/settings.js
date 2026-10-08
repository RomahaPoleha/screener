// ==========================================
// settings.js — НАСТРОЙКИ
// Открытие/применение модалки, вкладки, карточки скальпа
// Включена полная поддержка Binance Alpha (динамические поля)
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
    if (volHist) volHist.checked = (typeof volumeHistogramEnabled !== 'undefined' ? volumeHistogramEnabled : false);

    const drawTools = document.getElementById('showDrawingTools');
    if (drawTools) drawTools.checked = (typeof showDrawingTools !== 'undefined' ? showDrawingTools : false);

    const reconToggle = document.getElementById('reconPanelToggle');
    if (reconToggle) {
        reconToggle.checked = (typeof reconEnabled !== 'undefined' ? reconEnabled : false);
        if (typeof renderReconSettings === 'function') renderReconSettings();
    }

    // Отрисовка карточек скальпа (теперь динамически поддерживает Alpha)
    if (typeof renderScalpCards === 'function') renderScalpCards();

    const priceImpulseThr = document.getElementById('priceImpulseThreshold');
    if (priceImpulseThr) priceImpulseThr.value = (typeof priceImpulseThreshold !== 'undefined' ? priceImpulseThreshold : 0);

    const priceImpulseWin = document.getElementById('priceImpulseWindow');
    if (priceImpulseWin) priceImpulseWin.value = (typeof priceImpulseWindow !== 'undefined' ? priceImpulseWindow : 60);

    const soundCheckbox = document.getElementById('soundToggleModal');
    if (soundCheckbox) soundCheckbox.checked = (typeof soundEnabled !== 'undefined' ? soundEnabled : false);

    const volAlertToggle = document.getElementById('volumeAlertToggle');
    if (volAlertToggle) volAlertToggle.checked = (typeof volumeAlertEnabled !== 'undefined' ? volumeAlertEnabled : false);

    const beepSlider = document.getElementById('alertBeepVolume');
    if (beepSlider) beepSlider.value = (typeof alertBeepVolume !== 'undefined' ? alertBeepVolume : 0.5);

    const hourSlider = document.getElementById('hourSoundVolume');
    if (hourSlider) hourSlider.value = (typeof hourSoundVolume !== 'undefined' ? hourSoundVolume : 0.5);

    initSettingsTabs();
    const modalEl = document.getElementById('settingsModal');
    if (modalEl) {
        const modal = new bootstrap.Modal(modalEl);
        modal.show();
    }
}

// ==========================================
// ПРИМЕНЕНИЕ НАСТРОЕК
// ==========================================
function applySettings() {
    // 1. Гистограмма объёма
    const volHist = document.getElementById('showVolumeHistogram');
    if (volHist) {
        volumeHistogramEnabled = volHist.checked;
        localStorage.setItem('volumeHistogramEnabled', volumeHistogramEnabled);
        if (typeof volumeSeries !== 'undefined' && volumeSeries) {
            volumeSeries.applyOptions({ visible: volumeHistogramEnabled });
        }
    }

    // 2. Дельта
    const deltaHist = document.getElementById('showDeltaHistogram');
    if (deltaHist) {
        deltaEnabled = deltaHist.checked;
        localStorage.setItem('deltaEnabled', deltaEnabled);
        if (typeof deltaSeries !== 'undefined' && deltaSeries) {
            deltaSeries.applyOptions({ visible: deltaEnabled });
        }
    }

    // 3. Инструменты рисования
    const drawTools = document.getElementById('showDrawingTools');
    if (drawTools) {
        showDrawingTools = drawTools.checked;
        localStorage.setItem('showDrawingTools', showDrawingTools);
        if (typeof els !== 'undefined' && els && els.drawingToolsPanel) {
            els.drawingToolsPanel.style.display = showDrawingTools ? 'flex' : 'none';
        }
    }

    // 4. Recon (ИСПРАВЛЕНО: убран window. перед const/let переменными)
    const reconToggle = document.getElementById('reconPanelToggle');
    if (reconToggle) {
        reconEnabled = reconToggle.checked;
        localStorage.setItem('reconEnabled', reconEnabled);

        if (typeof RECON_EXCHANGES !== 'undefined' && typeof reconMinVolumes !== 'undefined') {
            for (const ex of RECON_EXCHANGES) {
                const f = document.getElementById(`reconMinF_${ex.id}`);
                const s = document.getElementById(`reconMinS_${ex.id}`);
                const a = document.getElementById(`reconMin_alpha_${ex.id}`); // Поддержка Alpha

                if (f && reconMinVolumes[ex.id]) reconMinVolumes[ex.id].futures = Math.max(10000, parseInt(f.value) || 10000);
                if (s && reconMinVolumes[ex.id]) reconMinVolumes[ex.id].spot = Math.max(10000, parseInt(s.value) || 10000);
                if (a && reconMinVolumes[ex.id]) reconMinVolumes[ex.id].alpha = Math.max(10000, parseInt(a.value) || 50000);
            }
            localStorage.setItem('reconMinVolumes', JSON.stringify(reconMinVolumes));
        }
    }

    // 5. Синхронизация настроек импульса с сервером
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

    // 6. Применяем настройки скальпа (ДИНАМИЧЕСКИ для futures, spot, alpha)
    if (typeof EXCHANGES_CONFIG !== 'undefined' && typeof scalpExchanges !== 'undefined') {
        EXCHANGES_CONFIG.forEach(ex => {
            const enabledCheckbox = document.getElementById(`scalp-${ex.id}-toggle`);

            if (!scalpExchanges[ex.id]) {
                scalpExchanges[ex.id] = { enabled: false, markets: { futures: false, spot: false, alpha: false }, minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 };
            }

            scalpExchanges[ex.id].enabled = enabledCheckbox ? enabledCheckbox.checked : false;

            // Динамически читаем все возможные рынки
            ['futures', 'spot', 'alpha'].forEach(market => {
                const checkbox = document.getElementById(`scalp-${ex.id}-${market}`);
                const input = document.getElementById(`scalp-${ex.id}-${market}v`);
                const minVolKey = `minVolume${market.charAt(0).toUpperCase() + market.slice(1)}`;
                const defaultVol = market === 'alpha' ? 50000 : (market === 'futures' ? 200000 : 100000);

                if (checkbox) scalpExchanges[ex.id].markets[market] = checkbox.checked;
                if (input) {
                    scalpExchanges[ex.id][minVolKey] = input.value ? Math.max(10000, parseInt(input.value) || defaultVol) : defaultVol;
                }
            });
        });

        localStorage.setItem('scalpExchanges', JSON.stringify(scalpExchanges));

        // Обновляем глобальный флаг
        scalpEnabled = Object.values(scalpExchanges).some(cfg =>
            cfg.enabled && (cfg.markets.futures || cfg.markets.spot || cfg.markets.alpha)
        );

        if (typeof currentSymbol !== 'undefined' && currentSymbol && typeof candleSeries !== 'undefined' && candleSeries) {
            if (typeof clearScalpLines === 'function') clearScalpLines();
            previousScalpData = {};
        }

        if (typeof currentSymbol !== 'undefined' && currentSymbol) {
            if (scalpEnabled) {
                if (typeof startScalpUpdates === 'function') startScalpUpdates(currentSymbol);
            } else {
                if (typeof scalpUpdateTimer !== 'undefined' && scalpUpdateTimer) {
                    clearInterval(scalpUpdateTimer);
                    scalpUpdateTimer = null;
                }
            }
        }
    }

    // 7. Оповещения и звуки
    const volAlertToggle = document.getElementById('volumeAlertToggle');
    if (volAlertToggle) {
        volumeAlertEnabled = volAlertToggle.checked;
        localStorage.setItem('volumeAlertEnabled', volumeAlertEnabled);
    }

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

    if (typeof updateAlertHistoryVisibility === 'function') updateAlertHistoryVisibility();

    // 8. Обновление кнопки настроек
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

    // 9. Перезапуск Recon
    if (typeof currentSymbol !== 'undefined' && currentSymbol) {
        if (reconEnabled && typeof startReconUpdates === 'function') {
            startReconUpdates(currentSymbol);
        } else if (typeof stopReconUpdates === 'function') {
            stopReconUpdates();
        }
    }

    // 10. Управление импульсом
    if (volumeAlertEnabled) {
        if (typeof impulsePollingEnabled !== 'undefined' && !impulsePollingEnabled && typeof startImpulseWebSocket === 'function') {
            startImpulseWebSocket();
        }
    } else {
        if (typeof impulsePollingEnabled !== 'undefined' && impulsePollingEnabled && typeof stopImpulseWebSocket === 'function') {
            stopImpulseWebSocket();
        }
    }

    // 11. Закрытие модалки
    const modalEl = document.getElementById('settingsModal');
    if (modalEl) {
        const modalInstance = bootstrap.Modal.getInstance(modalEl);
        if (modalInstance) modalInstance.hide();
    }
}

// ==========================================
// КАРТОЧКИ SCALP В НАСТРОЙКАХ (ДИНАМИЧЕСКИЕ)
// ==========================================
function renderScalpCards() {
    const container = document.getElementById('scalpExchangesContainer');
    if (!container) return;

    container.innerHTML = EXCHANGES_CONFIG.map(ex => {
        const cfg = scalpExchanges[ex.id] || { enabled: false, markets: { futures: false, spot: false, alpha: false }, minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 };
        const isEnabled = cfg.enabled !== false;

        // Определяем, какие рынки отображать для этой биржи
        const marketsToRender = [];
        if (ex.id === 'binance_alpha') {
            marketsToRender.push({ key: 'alpha', label: 'A', minVolKey: 'minVolumeAlpha', defaultVol: 50000, minAttr: 10000 });
        } else {
            marketsToRender.push({ key: 'futures', label: 'F', minVolKey: 'minVolumeFutures', defaultVol: 200000, minAttr: 10000 });
            marketsToRender.push({ key: 'spot', label: 'S', minVolKey: 'minVolumeSpot', defaultVol: 100000, minAttr: 10000 });
        }

        // Генерируем HTML для рынков
        const marketsHtml = marketsToRender.map(m => {
            const mEnabled = cfg.markets && cfg.markets[m.key];
            const vol = cfg[m.minVolKey] || m.defaultVol;
            return `
                <span style="font-size:11px;color:#94a3b8;min-width:10px;">${m.label}:</span>
                <input type="number" id="scalp-${ex.id}-${m.key}v" value="${vol}" min="${m.minAttr}" step="10000" style="width:70px;background:#1e293b;border:1px solid #475569;color:#fff;padding:4px 6px;border-radius:3px;font-size:12px;" ${!mEnabled || !isEnabled ? 'disabled' : ''}>
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:12px;color:#e2e8f0;">
                    <input type="checkbox" id="scalp-${ex.id}-${m.key}" ${mEnabled ? 'checked' : ''} ${!isEnabled ? 'disabled' : ''} style="accent-color:${ex.color};width:14px;height:14px;" onchange="document.getElementById('scalp-${ex.id}-${m.key}v').disabled = !this.checked">
                    <span>${m.label}</span>
                </label>
            `;
        }).join('');

        return `<div style="display:flex;align-items:center;gap:8px; flex-wrap: wrap;">
            <img src="https://www.google.com/s2/favicons?domain=${ex.domain}&sz=32" onerror="this.style.display='none'" style="width:16px;height:16px;border-radius:2px;flex-shrink:0;">
            <span style="font-weight:600;font-size:12px;color:${ex.color};min-width:24px;">${ex.label || ex.name.substring(0, 2).toUpperCase()}</span>
            ${marketsHtml}
            <label style="position:relative;display:inline-block;width:36px;height:20px;cursor:pointer; margin-left: auto;" title="Включить/выключить биржу">
                <input type="checkbox" id="scalp-${ex.id}-toggle" ${isEnabled ? 'checked' : ''} style="opacity:0;width:0;height:0;" onchange="toggleScalpExchange('${ex.id}', this.checked)">
                <span style="position:absolute;top:0;left:0;right:0;bottom:0;background:${isEnabled ? ex.color : '#475569'};border-radius:20px;transition:.3s;">
                    <span style="position:absolute;height:14px;width:14px;left:3px;bottom:3px;background:#ffffff;border-radius:50%;transition:.3s;transform:${isEnabled ? 'translateX(16px)' : 'translateX(0)'};"></span>
                </span>
            </label>
        </div>`;
    }).join('');
}

function toggleScalpExchange(exchangeId, enabled) {
    if (!scalpExchanges[exchangeId]) {
        scalpExchanges[exchangeId] = { enabled: false, markets: { futures: false, spot: false, alpha: false }, minVolumeFutures: 200000, minVolumeSpot: 100000, minVolumeAlpha: 50000 };
    }
    scalpExchanges[exchangeId].enabled = enabled;
    localStorage.setItem('scalpExchanges', JSON.stringify(scalpExchanges));

    // Динамически обновляем состояние чекбоксов и инпутов
    ['futures', 'spot', 'alpha'].forEach(market => {
        const checkbox = document.getElementById(`scalp-${exchangeId}-${market}`);
        const input = document.getElementById(`scalp-${exchangeId}-${market}v`);

        if (checkbox) checkbox.disabled = !enabled;
        if (input) {
            input.disabled = !enabled ? true : !checkbox.checked;
        }
    });

    renderScalpCards(); // Перерисовываем, чтобы обновить стили disabled/enabled
    applyScalpSettingsSilent();
}

function applyScalpSettingsSilent() {
    scalpEnabled = Object.values(scalpExchanges).some(cfg =>
        cfg.enabled && (cfg.markets.futures || cfg.markets.spot || cfg.markets.alpha)
    );

    if (typeof currentSymbol !== 'undefined' && currentSymbol && typeof candleSeries !== 'undefined' && candleSeries) {
        if (typeof clearScalpLines === 'function') clearScalpLines();
        previousScalpData = {};
    }

    if (typeof currentSymbol !== 'undefined' && currentSymbol) {
        if (scalpEnabled) {
            if (typeof startScalpUpdates === 'function') startScalpUpdates(currentSymbol);
        } else {
            if (typeof scalpUpdateTimer !== 'undefined' && scalpUpdateTimer) {
                clearInterval(scalpUpdateTimer);
                scalpUpdateTimer = null;
            }
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