// ==========================================
// coin_groups.js — ГРУППЫ МОНЕТ ПО ЦВЕТАМ
// Пикер цвета, модалка групп, открытие из группы
// ==========================================

// --- Переменные ---
let coinColors = {};
try {
    coinColors = JSON.parse(localStorage.getItem('coinColors') || '{}');
} catch(e) { coinColors = {}; }

const COIN_COLOR_OPTIONS = [
    { id: 'red',    hex: '#ef4444', label: 'Красный' },
    { id: 'green',  hex: '#22c55e', label: 'Зелёный' },
    { id: 'blue',   hex: '#3b82f6', label: 'Синий' },
    { id: 'yellow', hex: '#f59e0b', label: 'Жёлтый' },
];

// ==========================================
// ПИКЕР ЦВЕТА
// ==========================================
function closeColorPicker() {
    const picker = document.getElementById('colorPickerPopup');
    if (picker) picker.remove();
}

function openColorPicker(event, symbol) {
    closeColorPicker();
    const picker = document.createElement('div');
    picker.id = 'colorPickerPopup';
    picker.style.cssText = 'position:fixed; z-index:10002; background:#1a1a1a; border:1px solid #444444; padding:6px; display:flex; gap:6px; box-shadow:0 4px 12px rgba(0,0,0,0.6);';

    const rect = event.target.getBoundingClientRect();
    picker.style.left = Math.min(rect.left, window.innerWidth - 170) + 'px';
    picker.style.top = (rect.bottom + 4) + 'px';

    COIN_COLOR_OPTIONS.forEach(c => {
        const sw = document.createElement('div');
        sw.style.cssText = `width:20px; height:20px; cursor:pointer; background:${c.hex}; border:2px solid ${coinColors[symbol] === c.id ? '#ffffff' : 'transparent'};`;
        sw.title = c.label;
        sw.onclick = (e) => { e.stopPropagation(); setCoinColor(symbol, c.id); closeColorPicker(); };
        picker.appendChild(sw);
    });

    const reset = document.createElement('div');
    reset.style.cssText = 'width:20px; height:20px; cursor:pointer; background:#2a2a2a; border:1px solid #555555; color:#999999; font-size:12px; display:flex; align-items:center; justify-content:center;';
    reset.title = 'Убрать цвет';
    reset.textContent = '✕';
    reset.onclick = (e) => { e.stopPropagation(); setCoinColor(symbol, null); closeColorPicker(); };
    picker.appendChild(reset);

    document.body.appendChild(picker);

    document.addEventListener('click', function closeHandler(e) {
        const p = document.getElementById('colorPickerPopup');
        if (!p) { document.removeEventListener('click', closeHandler); return; }
        if (!p.contains(e.target)) {
            p.remove();
            document.removeEventListener('click', closeHandler);
        }
    });
}

function setCoinColor(symbol, colorId) {
    if (colorId) coinColors[symbol] = colorId;
    else delete coinColors[symbol];
    localStorage.setItem('coinColors', JSON.stringify(coinColors));
    applyLocalFilters();
    renderGroupsModal();
}

// ==========================================
// МОДАЛКА ГРУПП
// ==========================================
function openGroupsModal() {
    renderGroupsModal();
    new bootstrap.Modal(document.getElementById('coinGroupsModal')).show();
}

function renderGroupsModal() {
    const body = document.getElementById('coinGroupsBody');
    if (!body) return;

    const entries = Object.entries(coinColors);
    if (entries.length === 0) {
        body.innerHTML = '<div style="color:#6b7280; text-align:center; padding:20px;">Нет монет в группах. Нажми на точку слева от монеты и выбери цвет.</div>';
        return;
    }

    const byColor = {};
    for (const [symbol, colorId] of entries) {
        (byColor[colorId] = byColor[colorId] || []).push(symbol);
    }

    let html = '';
    for (const c of COIN_COLOR_OPTIONS) {
        const symbols = byColor[c.id];
        if (!symbols || symbols.length === 0) continue;
        symbols.sort();

        html += `<div style="margin-bottom:14px;">
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
                <span style="width:12px; height:12px; background:${c.hex}; display:inline-block;"></span>
                <span style="font-size:11px; font-weight:700; color:${c.hex}; text-transform:uppercase; letter-spacing:1px;">${c.label} (${symbols.length})</span>
                ${symbols.length >= 2 ? `<button onclick="openCollageFromModal('${c.id}')" style="margin-left:auto; padding:2px 8px; background:#2a2a2a; border:1px solid #444444; color:#ffffff; font-size:10px; font-weight:700; cursor:pointer; text-transform:uppercase; letter-spacing:0.5px;">Коллаж</button>` : ''}
            </div>`;

        for (const s of symbols) {
            html += `<div style="display:flex; align-items:center; justify-content:space-between; padding:6px 10px; border-bottom:1px solid #1f1f1f; cursor:pointer; font-size:13px;" onclick="openChartFromGroup('${s}')">
                <span style="font-weight:700; color:#ffffff;">${s}</span>
                <span style="color:#999999; font-size:11px;" onclick="event.stopPropagation(); setCoinColor('${s}', null);">убрать</span>
            </div>`;
        }
        html += '</div>';
    }
    body.innerHTML = html;
}

function openChartFromGroup(symbol) {
    if (collageState) exitCollage();
    bootstrap.Modal.getInstance(document.getElementById('coinGroupsModal')).hide();
    openChart(symbol);
}