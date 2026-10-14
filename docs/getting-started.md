# Getting Started — Grex Parser

## Требования

- Python 3.12+
- Redis 7+ (опционально — нужен для статистики и кэша прокси в админке)
- Chromium через Patchright (нужен для Ozon и MegaMarket)

---

## 1. Установка Python

### macOS

```bash
brew install python@3.12
python3 --version
```

> На macOS команда `python` по умолчанию недоступна — используйте `python3`.
> Чтобы не писать цифру, добавьте алиасы в `~/.zshrc`:
> ```bash
> echo "alias python=python3" >> ~/.zshrc
> source ~/.zshrc
> ```

### Linux (Ubuntu/Debian)

```bash
sudo apt update && sudo apt install python3.12 python3.12-venv python3-pip -y
python3 --version
```

### Windows

Скачайте установщик с [python.org](https://www.python.org/downloads/) и при установке отметьте **"Add Python to PATH"**.

---

## 2. Запуск

```bash
./start.sh
```

Скрипт автоматически:
- Проверяет версию Python
- Создаёт виртуальное окружение `.venv`
- Устанавливает зависимости
- Устанавливает браузер Chromium (для Ozon/MegaMarket)
- Проверяет лицензию через API и загружает список доступных источников
- Запускает сервер на `http://localhost:8004`

| URL | Описание |
|-----|----------|
| `http://localhost:8004/` | Корневой эндпоинт |
| `http://localhost:8004/v1/health` | Статус сервиса |
| `http://localhost:8004/docs` | Swagger UI (только при `debug.active: true`) |
| `http://localhost:8004/admin` | Админ-панель |

---

## 4. Конфигурация

Все настройки находятся в `data/settings.yml`.

```yaml
# Хост и порт API
app:
  host: "localhost"
  port: 8004

# Оставьте пустым: ключи API создаются и отзываются в админке (Настройки → API-ключи).
# Непустое значение один раз переносится туда при запуске — только для перехода со старых версий.
security:
  api_key: ""

# Админка. При первом запуске start.sh сам записывает сюда случайные
# secret_key и password и показывает пароль в терминале.
# secret_key подписывает вход в панель и шифрует токен Telegram-бота:
# если его сменить, токен придётся ввести заново.
admin:
  secret_key: "случайная-строка"
  db_url: "sqlite:///data/admin.db"
  username: "admin"
  password: "пароль"

# Redis (опционально — для статистики и кэша прокси)
redis:
  host: "localhost"
  port: 6379
  db: 0
  # password: ""

# Rate limit (запросов в минуту на маркетплейс)
sources:
  ozon:
    rate_limit: 60
  wildberries:
    rate_limit: 60
  yandex_market:
    rate_limit: 60
  megamarket:
    rate_limit: 60

# Прокси
proxy:
  active: false     # true — использовать прокси из data/proxy.txt
  type: "http"      # http или socks5

# Debug (включает /docs и /redoc, логирование в файл)
debug:
  active: true
  logging_in_file: true
```

### Прокси (опционально)

Если `proxy.active: true` — добавьте прокси в `data/proxy.txt`, по одному на строку:

```
login:password@host:port
login:password@host:port
```

---

## 5. Redis (опционально)

Redis нужен для статистики в дашборде и кэша прокси. Без него API работает в штатном режиме.

### macOS

```bash
brew install redis
brew services start redis
```

### Linux

```bash
sudo apt install redis-server -y
sudo systemctl start redis
```

### Docker

```bash
docker run -d -p 6379:6379 --name redis redis:7-alpine
```

Проверить:

```bash
redis-cli ping
# Ответ: PONG
```

---

## 6. Использование API

Все пути API начинаются с `/v1` (старые пути без версии устарели). Запросы к `/v1/parse`, `/v1/parse/batch` и `/v1/jobs` требуют заголовок `X-API-Key`. Ключ создаётся в админке: **Настройки → API-ключи → Создать ключ**. Ключ показывается один раз — сразу скопируйте его.

### Парсинг одного товара

```bash
curl -X POST http://localhost:8004/v1/parse \
  -H "X-API-Key: ваш-ключ" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.wildberries.ru/catalog/17386348/detail.aspx"}'
```

### Пакетный парсинг

```bash
curl -X POST http://localhost:8004/v1/parse/batch \
  -H "X-API-Key: ваш-ключ" \
  -H "Content-Type: application/json" \
  -d '{
    "urls": [
      "https://www.wildberries.ru/catalog/17386348/detail.aspx",
      "https://ozon.ru/product/example-123456"
    ],
    "concurrency": 2
  }'
```

### Определить маркетплейс по URL

```bash
curl "http://localhost:8004/v1/parse/detect?url=https://ozon.ru/product/example-123456"
```

### Список поддерживаемых маркетплейсов

```bash
curl http://localhost:8004/v1/marketplaces
```

Поддерживаемые маркетплейсы: **Wildberries**, **Ozon**, **Яндекс Маркет**, **MegaMarket**.

---

## 7. Структура проекта

```
parser_python/
├── api.py                  # FastAPI приложение, точка входа
├── config.py               # Загрузка settings.yml
├── start.sh                # Запуск сервера
├── update.sh               # Обновление приложения
├── Makefile                # run / stop / restart (для разработчика)
├── requirements.txt        # Зависимости
│
├── crawlers/               # Парсеры маркетплейсов
│   ├── base.py             # BaseCrawler + CookieCrawler
│   ├── registry.py         # CrawlerRegistry
│   ├── wildberries.py
│   ├── ozon.py
│   ├── yandex_market.py
│   └── megamarket.py
│
├── services/               # Бизнес-логика
│   ├── parsing.py          # ParsingService
│   ├── stats.py            # Статистика (Redis)
│   └── redis_client.py     # Redis клиент
│
├── admin/                  # Админ-панель (Starlette Admin)
│   ├── __init__.py
│   ├── dashboard.py
│   └── resources/          # targets, jobs, proxies, logs, users, products, links
│
├── models/                 # Pydantic + ORM модели
│   ├── product.py
│   ├── admin.py
│   └── job.py
│
├── core/
│   ├── managers.py         # ProxyManager
│   └── exceptions.py
│
├── utils/
│   ├── logger.py           # Loguru setup
│   └── helpers.py
│
└── data/
    ├── settings.yml        # Конфигурация
    ├── admin.db            # SQLite БД (создаётся автоматически)
    ├── proxy.txt           # Список прокси (опционально)
    └── *_cookies.json      # Куки маркетплейсов (создаются автоматически)
```

---

## Возможные проблемы

**`patchright` не находит браузер**
— Запустите: `.venv/bin/patchright install chromium`

**Redis недоступен**
— Приложение запустится, но статистика в дашборде работать не будет. Установите и запустите Redis (см. раздел 5).

**`zsh: command not found: python`**
— Используйте `python3`, либо настройте алиас (см. раздел 1).
