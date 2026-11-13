"""Единые лимиты памяти для всех мониторов скальпа."""

# Максимум мониторимых символов на рынок (futures/spot) в одном мониторе
MAX_MONITORED_SYMBOLS = 50

# Максимум ценовых уровней на сторону (bids/asks) при инициализации стакана
MAX_ORDER_BOOK_LEVELS = 500

# Максимум записей в *_volume_stats на символ
MAX_STATS_PER_SYMBOL = 100

# TTL записей в *_density_timestamps (секунды)
MAX_TS_AGE_SEC = 1800

# Максимум записей в *_removed на символ
MAX_REMOVED_PER_SYMBOL = 50

# Длина истории mid-цены на символ
MAX_MID_HISTORY = 20

# Максимальный возраст окна свечи в impulse_monitor (секунды)
MAX_CANDLE_AGE_SEC = 600

# Максимальный размер deque в volume_history (уже 120, зафиксировано)
MAX_VOLUME_HISTORY_DEQUE = 120

# Максимальный размер очереди WebSocket-сообщений
MAX_WS_QUEUE_SIZE = 10000
