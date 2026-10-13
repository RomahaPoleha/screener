// ==========================================
// data_loading.js — ЗАГРУЗКА ДАННЫХ С СЕРВЕРА
// Загрузка монет, NATR и автообновление
// ==========================================

// ==========================================
// ЗАГРУЗКА ВСЕХ МОНЕТ
// ==========================================
async function loadAllData() {
    try {
        const res = await fetch(`/api/data/`);
        if (!res.ok) throw new Error(`Ошибка сети: ${res.status}`);
        allCoins = await res.json();
        applyLocalFilters();
        updateChartStats();
    } catch (err) {
        console.error('Ошибка загрузки:', err);
        els.table.innerHTML = `<div style="color:#ef4444; text-align:center; padding:20px;">${err.message}</div>`;
    }
}

// ==========================================
// ЗАГРУЗКА NATR
// ==========================================
async function loadNatrData() {
    try {
        const res = await fetch(`/api/natr/`);
        if (!res.ok) throw new Error('Ошибка NATR');
        const response = await res.json();
        natrData = response.natr || {};
        applyLocalFilters();
        updateChartStats();
    } catch (err) { console.error(err); }
}

// ==========================================
// АВТООБНОВЛЕНИЕ NATR + МОНЕТ КАЖДЫЕ 15 СЕК
// ==========================================
function startNatrAutoUpdate() {
    if (natrAutoUpdateTimer) return;
    loadNatrData();
    loadAllData();   // ← первая загрузка allCoins
    natrAutoUpdateTimer = setInterval(() => {
        loadNatrData();
        loadAllData();   // ← обновляем allCoins вместе с NATR
    }, 15000);
}