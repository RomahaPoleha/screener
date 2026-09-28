// ==========================================
// init.js — ИНИЦИАЛИЗАЦИЯ
// Event listeners, DOMContentLoaded, горячие клавиши
// ==========================================

// Обработчики слайдеров (вне DOMContentLoaded — как в оригинале)
els.vol.addEventListener('input', (e) => {
    els.volVal.innerText = '$' + fmt(e.target.value);
    updateSliderFill(e.target);
    applyLocalFilters();
});

els.change.addEventListener('input', (e) => {
    els.changeVal.innerText = e.target.value + '%';
    updateSliderFill(e.target);
    applyLocalFilters();
});

// ==========================================
// DOMContentLoaded — ОСНОВНАЯ ИНИЦИАЛИЗАЦИЯ
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
    if (!els.tradesThresholdSlider || !els.search) {
        console.error('Критическая ошибка: DOM-элементы не найдены. Проверьте HTML.');
        return;
    }

    // Слайдер порога крупных сделок
    els.tradesThresholdSlider.addEventListener('input', (e) => {
        currentThreshold = parseInt(e.target.value);
        els.tradesThresholdValue.textContent = fmtThreshold(currentThreshold);
    });

    // Копирование символа по клику
    document.getElementById('chart-title').addEventListener('click', copySymbolToClipboard);

    // Кнопки таймфреймов
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const newTF = e.target.dataset.tf;
            if (!newTF) return;
            if (newTF === currentTF) return;
            document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            currentTF = newTF;
            if (collageState) { renderCollagePage(); return; }
            if (currentSymbol && chart) {
                loadChartData(currentSymbol, currentTF);
                startCandleWebSocket(currentSymbol, currentTF);
                updateWatermark();
            }
        });
    });

    // Слайдеры фильтров (дублируются из оригинала)
    els.vol.addEventListener('input', (e) => {
        updateSliderFill(e.target);
        applyLocalFilters();
    });
    els.change.addEventListener('input', (e) => {
        updateSliderFill(e.target);
        applyLocalFilters();
    });

    // Поиск
    els.search.addEventListener('input', (e) => { showSearchDropdown(e.target.value); applyLocalFilters(); });
    document.addEventListener('click', (e) => { if (!e.target.closest('.search-wrapper')) hideSearchDropdown(); });

    // Ресайз
    window.addEventListener('resize', () => {
        if (collageState) {
            collageCharts.forEach(entry => {
                if (entry.container) entry.chart.applyOptions({ width: entry.container.clientWidth, height: entry.container.clientHeight });
            });
            return;
        }
        if (chart && els.chartWrapper.classList.contains('active')) {
            chart.applyOptions({ width: els.chartWrapper.clientWidth, height: els.chartWrapper.clientHeight });
            setTimeout(() => { initPencilCanvas(); }, 200);
        }
    });

    // Горячие клавиши
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.key === 's') { e.preventDefault(); toggleTrendLine(); }
        if (e.altKey && e.key === 'ArrowLeft') { e.preventDefault(); togglePencil(); }
        if (e.key === 'b' || e.key === 'B') { toggleAlertMode(); }
        if (e.key === 'h' || e.key === 'H') { toggleHorizontalLine(); }
        if (e.shiftKey && e.key === 'E' && !isEraserEnabled) { e.preventDefault(); toggleEraser(); }
        if (e.shiftKey && e.key === 'S' && !isTrendLineEnabled) { e.preventDefault(); toggleTrendLine(); trendLineHotkeyActive = true; }
        if (e.shiftKey && e.key === 'D' && !isHorizontalLineEnabled) { e.preventDefault(); toggleHorizontalLine(); horizontalLineHotkeyActive = true; }
        if (e.shiftKey && e.key === 'P' && !isPencilEnabled) { e.preventDefault(); togglePencil(); pencilHotkeyActive = true; }
    });

    document.addEventListener('keyup', (e) => {
        if (e.key === 'Shift') {
            if (isEraserEnabled) {
                isEraserEnabled = false;
                updateToolUI('eraserBtn', false);
                if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: true } });
            }
            if (trendLineHotkeyActive) { toggleTrendLine(); trendLineHotkeyActive = false; }
            if (horizontalLineHotkeyActive) { toggleHorizontalLine(); horizontalLineHotkeyActive = false; }
            if (pencilHotkeyActive) { togglePencil(); pencilHotkeyActive = false; }
        }
    });

    // Инициализация SpeechSynthesis по первому клику
    document.addEventListener('click', function initSpeech() {
        if ('speechSynthesis' in window) speechSynthesis.speak(new SpeechSynthesisUtterance(''));
        document.removeEventListener('click', initSpeech);
    }, { once: true });

    // Панель инструментов рисования
    els.drawingToolsPanel.style.display = showDrawingTools ? 'flex' : 'none';

    // Кнопки скальпа (если есть отдельные модалки)
    const openScalpBtn = document.getElementById('openScalpSettingsBtn');
    if (openScalpBtn) openScalpBtn.addEventListener('click', openScalpSettingsModal);
    const applyScalpBtn = document.getElementById('applyScalpSettingsBtn');
    if (applyScalpBtn) applyScalpBtn.addEventListener('click', applyScalpSettings);

    // Видимость истории алертов
    updateAlertHistoryVisibility();

    // Загрузка данных
    loadAllData();

    // Инициализация заполнения ползунков
    if (els.vol) updateSliderFill(els.vol);
    if (els.change) updateSliderFill(els.change);

    // NATR автообновление
    startNatrAutoUpdate();

    // Запуск мониторинга алертов
    AlertManager.startAll();

    // Если импульс включён — запускаем WS и таймер
    if (volumeAlertEnabled) {
        startImpulseWebSocket();
        window.impulseCheckerTimer = setInterval(checkVolumeAlerts, 1000);
    }
});