"""
Impulse Monitor — серверный расчёт ценовых импульсов
Слушает Binance Futures !miniTicker@arr (все монеты сразу)
"""
import asyncio
import json
import time
import threading
import websockets
from collections import deque
from django.core.cache import cache

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
HISTORY_SECONDS = 300       # 5 минут истории цен
CHECK_INTERVAL = 1.0        # проверка импульсов каждую секунду
MAX_ALERTS = 100            # максимум алертов в памяти и Redis
ALERTS_TTL = 3600           # TTL алертов в Redis (1 час)
COOLDOWN_SECONDS = 60       # кулдаун между алертами одной монеты

# Настройки по умолчанию (переопределяются из браузера)
DEFAULT_THRESHOLD = 1.0     # 1%
DEFAULT_WINDOW = 60         # 60 секунд

BINANCE_WS_URL = "wss://fstream.binance.com/ws"


class ImpulseMonitor:
    _instance = None

    def __init__(self):
        import queue as _queue
        self._queue_module = _queue
        self.price_history = {}     # symbol -> deque([(ts, price)])
        self.alerts = []            # последние N алертов (в памяти)
        self.alerts_lock = threading.Lock()
        self.cooldowns = {}         # symbol -> timestamp последнего алерта
        self.threshold = DEFAULT_THRESHOLD
        self.window = DEFAULT_WINDOW
        self.running = False
        self.log_func = print
        # 🔥 НОВОЕ: подписчики SSE
        self.subscribers = []       # список queue.Queue для SSE-клиентов
        self.subscribers_lock = threading.Lock()
        self.symbols = []

    def set_symbols(self, symbols):
        """Устанавливает список монет для мониторинга"""
        self.symbols = list(symbols)
        self.log_func(f"📊 Impulse: установлено {len(symbols)} монет для мониторинга")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_log_func(self, log_func):
        self.log_func = log_func

    def set_params(self, threshold, window):
        self.threshold = threshold
        self.window = window

    # 🔥 НОВОЕ: управление подписками
    def subscribe(self, q):
        with self.subscribers_lock:
            self.subscribers.append(q)
            self.log_func(f"📡 Impulse: новый SSE подписчик (всего: {len(self.subscribers)})")

    def unsubscribe(self, q):
        with self.subscribers_lock:
            if q in self.subscribers:
                self.subscribers.remove(q)
                self.log_func(f"📡 Impulse: SSE подписчик отключился (осталось: {len(self.subscribers)})")

    def _add_price(self, symbol, price):
        now = time.time()
        if symbol not in self.price_history:
            self.price_history[symbol] = deque(maxlen=2000)
        self.price_history[symbol].append((now, price))
        history = self.price_history[symbol]
        while history and (now - history[0][0]) > HISTORY_SECONDS:
            history.popleft()

    def _check_impulses(self):
        now = time.time()
        for symbol, history in self.price_history.items():
            if len(history) < 2:
                continue
            target_time = now - self.window
            reference_price = None
            for ts, price in reversed(history):
                if ts <= target_time:
                    reference_price = price
                    break
            if reference_price is None or reference_price == 0:
                continue
            current_price = history[-1][1]
            price_change = ((current_price - reference_price) / reference_price) * 100
            abs_change = abs(price_change)
            if abs_change < self.threshold:
                continue
            last_alert = self.cooldowns.get(symbol, 0)
            if now - last_alert < COOLDOWN_SECONDS:
                continue
            self.cooldowns[symbol] = now
            direction = '↑' if price_change > 0 else '↓'
            alert = {
                'symbol': symbol,
                'price': current_price,
                'change': round(abs_change, 2),
                'direction': direction,
                'time': now,
                'window': self.window,
            }
            self._publish_alert(alert)

    # 🔥 ИЗМЕНЕНО: теперь пушит алерт подписчикам
    def _publish_alert(self, alert):
        # Сохраняем в память
        with self.alerts_lock:
            self.alerts.insert(0, alert)
            if len(self.alerts) > MAX_ALERTS:
                self.alerts = self.alerts[:MAX_ALERTS]
        # Сохраняем в Redis
        try:
            cache.set('impulse:alerts', self.alerts, ALERTS_TTL)
        except Exception as e:
            self.log_func(f"⚠️ Impulse: не удалось сохранить в Redis: {e}")
        # 🔥 НОВОЕ: пушим всем SSE подписчикам
        with self.subscribers_lock:
            dead = []
            for q in self.subscribers:
                try:
                    q.put_nowait(alert)
                except self._queue_module.Full:
                    dead.append(q)
                except Exception:
                    dead.append(q)
            for q in dead:
                if q in self.subscribers:
                    self.subscribers.remove(q)

    def get_recent_alerts(self, limit=50, since=None):
        with self.alerts_lock:
            if since is None:
                return list(self.alerts[:limit])
            return [a for a in self.alerts if a['time'] > since][:limit]

    async def _listen_binance(self):
        """Слушаем Binance WS — подписываемся на конкретные монеты через @ticker"""
        while self.running:
            try:
                # 🔥 ИСПРАВЛЕНО: используем тот же URL, что в binance_monitor
                async with websockets.connect(BINANCE_WS_URL, ping_interval=20, ping_timeout=20) as ws:
                    # 🔥 ИСПРАВЛЕНО: подписываемся на @ticker для каждой монеты (как в binance_monitor)
                    # @ticker включает поле 'c' (текущая цена), что нам и нужно
                    args = [f"{s.lower()}usdt@ticker" for s in self.symbols]

                    if not args:
                        self.log_func("⚠️ Impulse: список монет пуст, ждём...")
                        await asyncio.sleep(5)
                        continue

                    await ws.send(json.dumps({
                        "method": "SUBSCRIBE",
                        "params": args,
                        "id": 1
                    }))
                    self.log_func(f"🔌 Impulse: Binance WS подключен ({len(args)} монет)")

                    while self.running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            continue

                        try:
                            data = json.loads(msg)

                            # @ticker возвращает объект, не массив
                            if isinstance(data, dict) and 's' in data:
                                symbol = data.get('s', '')
                                if not symbol.endswith('USDT'):
                                    continue
                                clean = symbol[:-4]
                                # Поле 'c' — текущая цена (close)
                                price = float(data.get('c', 0))
                                if price > 0:
                                    self._add_price(clean, price)
                        except Exception as e:
                            self.log_func(f"⚠️ Impulse parse error: {e}")
            except Exception as e:
                self.log_func(f"⚠️ Impulse Binance: {e}, reconnect через 3 сек...")
                await asyncio.sleep(3)

    async def _checker_loop(self):
        while self.running:
            try:
                self._check_impulses()
            except Exception as e:
                self.log_func(f"❌ Impulse checker error: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def _main_async(self):
        # 🔥 НОВОЕ: загружаем список монет из мастер-списка Binance
        while not self.symbols:
            try:
                symbols = cache.get('scalp:master:futures')
                if isinstance(symbols, list) and len(symbols) > 0:
                    self.symbols = symbols
                    self.log_func(f"📊 Impulse: загружено {len(symbols)} монет из мастер-списка")
                    break
            except Exception:
                pass
            self.log_func("⏳ Impulse: ожидание мастер-списка...")
            await asyncio.sleep(5)

        # Загружаем последние алерты из Redis
        try:
            saved = cache.get('impulse:alerts')
            if isinstance(saved, list):
                self.alerts = saved
                self.log_func(f"📜 Impulse: загружено {len(saved)} алертов из Redis")
        except Exception:
            pass

        self.running = True
        await asyncio.gather(
            self._listen_binance(),
            self._checker_loop(),
        )

    def start(self, log_func=print):
        self.set_log_func(log_func)
        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._main_async())
            except Exception as e:
                log_func(f"❌ Impulse Monitor упал: {e}")
                import traceback
                log_func(traceback.format_exc())
        threading.Thread(target=run, name='Impulse-Monitor', daemon=True).start()
        log_func("✅ Impulse Monitor поток запущен")


# Глобальный инстанс
impulse_monitor = ImpulseMonitor.get_instance()


def start_impulse_monitor(log_func=print):
    impulse_monitor.start(log_func)