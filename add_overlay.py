with open('screener/static/screener/js/collage.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Add collageOverlayMode flag
content = content.replace(
    'window.collageState = null;',
    'window.collageState = null;\nwindow.collageOverlayMode = false;',
    1
)

# Modify expand button
old_expand = "                exitCollage();\n                openChart(sym);"
new_expand = "                openChartOverlay(sym);"
if old_expand in content:
    content = content.replace(old_expand, new_expand)
    print("Expand button updated")
else:
    print("Expand pattern not found")

# Add openChartOverlay and closeChartOverlay functions after exitCollage
overlay_funcs = '''
function openChartOverlay(symbol) {
    window.collageOverlayMode = true;
    els.chartWrapper.style.display = '';
    els.chartWrapper.classList.add('active');
    els.chartWrapper.style.position = 'absolute';
    els.chartWrapper.style.inset = '0';
    els.chartWrapper.style.zIndex = '10';

    const titleWrap = document.getElementById('chart-title') ? document.getElementById('chart-title').parentElement : null;
    if (titleWrap) titleWrap.style.display = 'flex';
    const resetBtn = document.querySelector('.chart-reset-btn');
    if (resetBtn) { resetBtn.style.display = ''; resetBtn.textContent = '✕ Закрыть'; resetBtn.onclick = closeChartOverlay; }
    els.drawingToolsPanel.style.display = showDrawingTools ? 'flex' : 'none';
    els.chartWatermark.style.display = '';
    els.pencilCanvas.style.display = '';
    const historyBtn = document.getElementById('loadHistoryBtn');
    if (historyBtn) historyBtn.style.display = '';

    openChart(symbol);
}

function closeChartOverlay() {
    window.collageOverlayMode = false;
    // Очистить график
    if (wsCandles) { wsCandles.onclose = null; wsCandles.close(); wsCandles = null; }
    if (wsTrades) { wsTrades.onclose = null; wsTrades.onmessage = null; wsTrades.onerror = null; wsTrades.close(); wsTrades = null; }
    stopReconUpdates();
    if (densityUpdateTimer) { clearInterval(densityUpdateTimer); densityUpdateTimer = null; }
    if (scalpUpdateTimer) { clearInterval(scalpUpdateTimer); scalpUpdateTimer = null; }

    els.chartWrapper.style.display = 'none';
    els.chartWrapper.style.position = '';
    els.chartWrapper.style.inset = '';
    els.chartWrapper.style.zIndex = '';
    const titleWrap = document.getElementById('chart-title') ? document.getElementById('chart-title').parentElement : null;
    if (titleWrap) titleWrap.style.display = 'none';
    const resetBtn = document.querySelector('.chart-reset-btn');
    if (resetBtn) { resetBtn.style.display = 'none'; resetBtn.textContent = '✕ Сброс'; resetBtn.onclick = function() { closeChart(); }; }
    els.drawingToolsPanel.style.display = 'none';
    els.chartWatermark.style.display = 'none';
    els.pencilCanvas.style.display = 'none';
    const historyBtn = document.getElementById('loadHistoryBtn');
    if (historyBtn) historyBtn.style.display = 'none';
}
'''

# Insert after exitCollage function
exit_end = content.find('function openCollageFromModal')
if exit_end > 0:
    content = content[:exit_end] + overlay_funcs + '\n' + content[exit_end:]
    print("Overlay functions added")
else:
    print("openCollageFromModal not found")

with open('screener/static/screener/js/collage.js', 'w', encoding='utf-8') as f:
    f.write(content)
print("Done")
PYEOF