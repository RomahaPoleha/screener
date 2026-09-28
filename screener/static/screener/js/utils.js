// ==========================================
// utils.js — УТИЛИТЫ
// Чистые функции форматирования и преобразования
// ==========================================

const fmtThreshold = (v) => v >= 1000 ? `$${(v/1000).toFixed(0)}K` : `$${v}`;

const fmt = (num) => {
    if (!num) return '0';
    if (num >= 1e9) return (num / 1e9).toFixed(1) + 'B';
    if (num >= 1e6) return (num / 1e6).toFixed(1) + 'M';
    if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K';
    return Number(num).toFixed(2);
};

const safeTime = (t) => t > 10000000000 ? Math.floor(t / 1000) : Math.floor(t);

const getNatrClass = (val) => val > 1.0 ? 'natr-high' : val > 0.3 ? 'natr-mid' : 'natr-low';

function formatAge(seconds) {
    const totalMinutes = Math.floor(seconds / 60);
    const hours = Math.floor(totalMinutes / 60);
    const mins = totalMinutes % 60;
    return hours > 0 ? `${hours}ч ${mins}м` : `${mins}м`;
}

function formatVolumeText(volume) {
    if (volume >= 1000000) return `${(volume / 1000000).toFixed(2)}M`;
    if (volume >= 1000) return `${(volume / 1000).toFixed(1)}K`;
    return `${volume.toFixed(0)}`;
}

function hexToRgb(hex) {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? `${parseInt(result[1], 16)}, ${parseInt(result[2], 16)}, ${parseInt(result[3], 16)}` : '245, 158, 11';
}