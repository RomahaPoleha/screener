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
    const btn = document.getElementById('toggleTableBtn');

    if (tableHidden) {
        if (panel) panel.classList.add('table-hidden');
        if (peekTab) peekTab.classList.add('visible');
        if (btn) btn.textContent = '◀ Показать таблицу';
    } else {
        if (panel) panel.classList.remove('table-hidden');
        if (peekTab) peekTab.classList.remove('visible');
        if (btn) btn.textContent = '▶ Скрыть таблицу';
    }

    // Обновить размер графика после анимации
    setTimeout(() => {
        if (chart && els.chartWrapper) {
            chart.applyOptions({
                width: els.chartWrapper.clientWidth,
                height: els.chartWrapper.clientHeight
            });
        }
        // Также для коллажа
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

// Горячая клавиша H
document.addEventListener('keydown', (e) => {
    // Не срабатывает в полях ввода
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
    if (e.key === 'h' || e.key === 'H' || e.key === 'р' || e.key === 'Р') {
        e.preventDefault();
        toggleTable();
    }
});

// Применить сохранённое состояние при загрузке
document.addEventListener('DOMContentLoaded', () => {
    applyTableState();
});
PYEOF