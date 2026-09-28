// ==========================================
// slider.js — СЛАЙДЕРЫ
// Обновление визуального заполнения слайдеров
// ==========================================

function updateSliderFill(el) {
    if (!el) return;
    const min = parseFloat(el.min) || 0;
    const max = parseFloat(el.max) || 100;
    const val = parseFloat(el.value) || min;
    const percent = ((val - min) / (max - min)) * 100;
    const container = el.closest('.slider-container');
    if (!container) return;
    const fill = container.querySelector('.slider-fill');
    const thumb = container.querySelector('.slider-thumb');
    const label = container.querySelector('.slider-label');
    if (fill) fill.style.width = percent + '%';
    if (thumb) thumb.style.left = percent + '%';
    if (label) {
        if (el.id === 'volRange') {
            label.textContent = '$' + fmt(val);
        } else if (el.id === 'changeRange') {
            label.textContent = val + '%';
        }
    }
}