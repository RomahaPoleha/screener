// ==========================================
// init.js — ИНИЦИАЛИЗАЦИЯ
// Event listeners, DOMContentLoaded, горячие клавиши
// ==========================================

// Вспомогательная функция для debounce (защита от частых вызовов resize)
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// ==========================================
// DOMContentLoaded — ОСНОВНАЯ ИНИЦИАЛИЗАЦИЯ
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
    // 1. Безопасная проверка: если els не определен или элементов нет, останавливаемся
    if (typeof els === 'undefined' || !els.tradesThresholdSlider || !els.search) {
        console.error('⚠️ Критическая ошибка: DOM-элементы или объект els не найдены. Проверьте HTML и порядок загрузки скриптов.');
        return;
    }

    // 2. Слайдер порога крупных сделок
    els.tradesThresholdSlider.addEventListener('input', (e) => {
        currentThreshold = parseInt(e.target.value);
        els.tradesThresholdValue.textContent = fmtThreshold(currentThreshold);
    });

    // 3. Копирование символа по клику
    const chartTitle = document.getElementById('chart-title');
    if (chartTitle) chartTitle.addEventListener('click', copySymbolToClipboard);

    // 4. Кнопки таймфреймов
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const newTF = e.target.dataset.tf;
            if (!newTF || newTF === currentTF) return;

            document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            currentTF = newTF;

            if (collageState) {
                renderCollagePage();
                return;
            }
            if (currentSymbol && chart) {
                loadChartData(currentSymbol, currentTF);
                startCandleWebSocket(currentSymbol, currentTF);
                updateWatermark();
            }
        });
    });

    // 5. Слайдеры фильтров (ТЕПЕРЬ ТОЛЬКО ОДИН РАЗ!)
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

    // 6. Поиск
    els.search.addEventListener('input', (e) => {
        showSearchDropdown(e.target.value);
        applyLocalFilters();
    });
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.search-wrapper')) hideSearchDropdown();
    });

    // 7. Ресайз с DEBOUNCE (оптимизация производительности)
    window.addEventListener('resize', debounce(() => {
        if (collageState) {
            collageCharts.forEach(entry => {
                if (entry.container && entry.chart) {
                    entry.chart.applyOptions({
                        width: entry.container.clientWidth,
                        height: entry.container.clientHeight
                    });
                }
            });
            return;
        }
        if (chart && els.chartWrapper && els.chartWrapper.classList.contains('active')) {
            chart.applyOptions({
                width: els.chartWrapper.clientWidth,
                height: els.chartWrapper.clientHeight
            });
            setTimeout(() => {
                if (typeof initPencilCanvas === 'function') initPencilCanvas();
            }, 200);
        }
    }, 150)); // Задержка 150мс

    // 8. Горячие клавиши
    document.addEventListener('keydown', (e) => {
        // Проверка, что переменные существуют, чтобы избежать ReferenceError
        const isEraser = typeof isEraserEnabled !== 'undefined' ? isEraserEnabled : false;
        const isTrend = typeof isTrendLineEnabled !== 'undefined' ? isTrendLineEnabled : false;
        const isHoriz = typeof isHorizontalLineEnabled !== 'undefined' ? isHorizontalLineEnabled : false;
        const isPencil = typeof isPencilEnabled !== 'undefined' ? isPencilEnabled : false;

        if (e.ctrlKey && e.key === 's') { e.preventDefault(); if (typeof toggleTrendLine === 'function') toggleTrendLine(); }
        if (e.altKey && e.key === 'ArrowLeft') { e.preventDefault(); if (typeof togglePencil === 'function') togglePencil(); }
        if (e.key === 'b' || e.key === 'B') { if (typeof toggleAlertMode === 'function') toggleAlertMode(); }
        if (e.key === 'h' || e.key === 'H') { if (typeof toggleHorizontalLine === 'function') toggleHorizontalLine(); }
        if (e.shiftKey && e.key === 'E' && !isEraser) { e.preventDefault(); if (typeof toggleEraser === 'function') toggleEraser(); }
        if (e.shiftKey && e.key === 'S' && !isTrend) { e.preventDefault(); if (typeof toggleTrendLine === 'function') { toggleTrendLine(); window.trendLineHotkeyActive = true; } }
        if (e.shiftKey && e.key === 'D' && !isHoriz) { e.preventDefault(); if (typeof toggleHorizontalLine === 'function') { toggleHorizontalLine(); window.horizontalLineHotkeyActive = true; } }
        if (e.shiftKey && e.key === 'P' && !isPencil) { e.preventDefault(); if (typeof togglePencil === 'function') { togglePencil(); window.pencilHotkeyActive = true; } }
    });

    document.addEventListener('keyup', (e) => {
        if (e.key === 'Shift') {
            if (typeof isEraserEnabled !== 'undefined' && isEraserEnabled) {
                isEraserEnabled = false;
                if (typeof updateToolUI === 'function') updateToolUI('eraserBtn', false);
                if (chart) chart.applyOptions({ handleScroll: { mouseWheel: true, pressedMouseMove: true } });
            }
            if (window.trendLineHotkeyActive && typeof toggleTrendLine === 'function') { toggleTrendLine(); window.trendLineHotkeyActive = false; }
            if (window.horizontalLineHotkeyActive && typeof toggleHorizontalLine === 'function') { toggleHorizontalLine(); window.horizontalLineHotkeyActive = false; }
            if (window.pencilHotkeyActive && typeof togglePencil === 'function') { togglePencil(); window.pencilHotkeyActive = false; }
        }
    });

    // 9. Инициализация SpeechSynthesis по первому клику (хак для разблокировки аудио в Safari/iOS)
    document.addEventListener('click', function initSpeech() {
        if ('speechSynthesis' in window) {
            speechSynthesis.cancel(); // Отмена вместо пустого speak надежнее в некоторых браузерах
            // или speechSynthesis.speak(new SpeechSynthesisUtterance(' '));
        }
        document.removeEventListener('click', initSpeech);
    }, { once: true });

    // 10. Панель инструментов рисования
    if (els.drawingToolsPanel) {
        els.drawingToolsPanel.style.display = (typeof showDrawingTools !== 'undefined' && showDrawingTools) ? 'flex' : 'none';
    }

    // 11. Кнопки скальпа (модалки)
    const openScalpBtn = document.getElementById('openScalpSettingsBtn');
    if (openScalpBtn) openScalpBtn.addEventListener('click', () => { if (typeof openScalpSettingsModal === 'function') openScalpSettingsModal(); });

    const applyScalpBtn = document.getElementById('applyScalpSettingsBtn');
    if (applyScalpBtn) applyScalpBtn.addEventListener('click', () => { if (typeof applyScalpSettings === 'function') applyScalpSettings(); });

    // 12. Видимость истории алертов
    if (typeof updateAlertHistoryVisibility === 'function') updateAlertHistoryVisibility();

    // 13. Инициализация заполнения ползунков (если они уже имеют значения из localStorage)
    if (els.vol) updateSliderFill(els.vol);
    if (els.change) updateSliderFill(els.change);

    // ==========================================
    // ЗАПУСК ПРОЦЕССОВ (в самом конце, когда всё готово)
    // ==========================================

    // Загрузка данных
    if (typeof loadAllData === 'function') loadAllData();

    // NATR автообновление
    if (typeof startNatrAutoUpdate === 'function') startNatrAutoUpdate();

    // Запуск мониторинга алертов
    if (typeof AlertManager !== 'undefined' && AlertManager.startAll) AlertManager.startAll();

    // Если импульс включён — запускаем WS
    if (typeof volumeAlertEnabled !== 'undefined' && volumeAlertEnabled) {
        if (typeof startImpulseWebSocket === 'function') startImpulseWebSocket();
    }
});