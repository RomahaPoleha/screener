// ==========================================
// table_toggle.js — СКРЫТИЕ/ПОКАЗ ТАБЛИЦЫ МОНЕТ
// ==========================================

let tableHidden = localStorage.getItem('tableHidden') === 'true';

function toggleTable() {
    tableHidden = !tableHidden;
    localStorage.setItem('tableHidden', tableHidden);
    applyTableState();
}

function showTable() {
    tableHidden = false;
    localStorage.setItem('tableHidden', 'false');
    applyTableState();
}

function hideTable() {
    tableHidden = true;
    localStorage.setItem('tableHidden', 'true');
    applyTableState();
}

function applyTableState() {
    const panel = els.rightPanel || document.getElementById('rightPanel');
    const peekTab = document.getElementById('tablePeekTab');

    if (tableHidden) {
        if (panel) panel.classList.add('table-hidden');
        if (peekTab) peekTab.classList.add('visible');
    } else {
        if (panel) panel.classList.remove('table-hidden');
        if (peekTab) peekTab.classList.remove('visible');
    }

    // Обновить размер графика после анимации
    setTimeout(() => {
        if (chart && els.chartWrapper) {
            chart.applyOptions({
                width: els.chartWrapper.clientWidth,
                height: els.chartWrapper.clientHeight
            });
        }
        if (window.collageState) {
            for (const entry of collageCharts) {
                if (entry.chart && entry.container) {
                    entry.chart.applyOptions({
                        width: entry.container.clientWidth,
                        height: entry.container.clientHeight
                    });
                }
            }
        }
    }, 320);
}

// Применить сохранённое состояние при загрузке
document.addEventListener('DOMContentLoaded', () => {
    applyTableState();
});
