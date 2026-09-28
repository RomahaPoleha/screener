// ==========================================
// sound.js — ЗВУКИ И ОПОВЕЩЕНИЯ
// Часовые звуки, алерт-бип, озвучка речи
// ==========================================

// --- Звуковые объекты ---
const sound5min = new Audio('/api/sound/alert_5min.mp3');
const sound1min = new Audio('/api/sound/alert_1min.mp3');
sound5min.preload = 'auto';
sound1min.preload = 'auto';

// --- Состояние ---
let soundEnabled = localStorage.getItem('soundEnabled') !== 'false';
let lastNotifiedMinute = -1;
let russianVoice = null;
let audioCtx = null;
let hourSoundVolume = parseFloat(localStorage.getItem('hourSoundVolume') || '1');

// ==========================================
// ЧАСОВЫЕ ЗВУКИ
// ==========================================
function playHourSound(minutesLeft) {
    if (!soundEnabled) return;
    const sound = minutesLeft === 5 ? sound5min : sound1min;
    sound.currentTime = 0;
    sound.volume = hourSoundVolume;
    sound.play().catch(err => {
        console.warn('Не удалось воспроизвести звук:', err);
        speak(minutesLeft === 5 ? 'До перехода на новый час осталось 5 минут' : 'Внимание, до перехода на новый час осталась 1 минута');
    });
}

// ==========================================
// АЛЕРТ-БИП (через Web Audio API)
// ==========================================
function playAlertSound() {
    try {
        if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain); gain.connect(audioCtx.destination);
        osc.frequency.value = 880; osc.type = 'sine';
        gain.gain.setValueAtTime(alertBeepVolume, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.5);
        osc.start(audioCtx.currentTime); osc.stop(audioCtx.currentTime + 0.5);
    } catch(e) { console.warn('Ошибка звука:', e); }
}

// ==========================================
// ПЕРЕКЛЮЧЕНИЕ ЗВУКА (из модалки настроек)
// ==========================================
function toggleSound() {
    const checkbox = document.getElementById('soundToggleModal');
    soundEnabled = checkbox.checked;
    localStorage.setItem('soundEnabled', soundEnabled);
}

// ==========================================
// ОЗВУЧКА РЕЧИ (SpeechSynthesis)
// ==========================================
function initVoices() {
    const voices = speechSynthesis.getVoices();
    russianVoice = voices.find(v => v.lang.startsWith('ru')) || voices.find(v => v.lang.includes('ru')) || null;
}

if ('speechSynthesis' in window) { initVoices(); speechSynthesis.onvoiceschanged = initVoices; }

function speak(text) {
    if (!soundEnabled || !('speechSynthesis' in window)) return;
    try {
        speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.lang = 'ru-RU'; u.rate = 0.9; u.pitch = 0.7; u.volume = 0.8;
        if (russianVoice) u.voice = russianVoice;
        speechSynthesis.speak(u);
    } catch (e) { console.warn('Ошибка озвучки:', e); }
}

// ==========================================
// ЧАСОВОЙ ТОСТ
// ==========================================
function showHourToast(title) {
    document.getElementById('toastTitle').textContent = title;
    const toast = document.getElementById('hourToast');
    toast.classList.add('show');
    setTimeout(() => { toast.classList.remove('show'); }, 5000);
}

// ==========================================
// ПРОВЕРКА ПЕРЕХОДА ЧАСА (вызывается каждую секунду)
// ==========================================
function checkHourTransition() {
    const now = new Date();
    const currentMinuteKey = now.getHours() * 60 + now.getMinutes();
    if (now.getMinutes() === 55 && now.getSeconds() < 3 && lastNotifiedMinute !== currentMinuteKey) {
        lastNotifiedMinute = currentMinuteKey;
        showHourToast('До нового часа 5 минут');
        playHourSound(5);
    }
    if (now.getMinutes() === 59 && now.getSeconds() < 3 && lastNotifiedMinute !== currentMinuteKey) {
        lastNotifiedMinute = currentMinuteKey;
        showHourToast('До нового часа 1 минута');
        playHourSound(1);
    }
}

setInterval(checkHourTransition, 1000);
