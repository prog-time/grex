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

## 2. Регистрация лицензии

Перед первым запуском необходимо зарегистрировать устройство:

```bash
python register.py
```

Скрипт создаст `license.json` и выведет ключ активации:

```
  Grex Parser — Registration

  Ваш ключ:  a3f9c2d1e8b4...

  Отправьте ключ разработчику для активации.
  После активации запустите: ./start.sh
```

Отправьте ключ разработчику. После активации можно запускать приложение.

> **Важно:** `license.json` привязан к устройству — копировать на другую машину бесполезно.

---

## 3. Запуск

```bash
./start.sh
```

Скрипт автоматически:
- Проверяет версию Python
- Создаёт виртуальное окружение `.venv`
- Устанавливает зависимости
- Устанавливает браузер Chromium (для Ozon/MegaMarket)
- Проверяет лицензию
- Запускает сервер на `http://localhost:8004`

| URL | Описание |
|-----|----------|
| `http://localhost:8004/` | Корневой эндпоинт |
| `http://localhost:8004/health` | Статус сервиса |
| `http://localhost:8004/docs` | Swagger UI (только при `debug.active: true`) |
| `http://localhost:8004/admin` | Админ-панель |

---

## 4. Конфигурация

Все настройки находятся в `data/settings.yml`.

```yaml
# Версия приложения
soft_version: "1.0.0"

# Хост и порт API
app:
  host: "localhost"
  port: 8004

# API-ключ для /parse эндпоинтов
security:
  api_key: "замените-на-свой-ключ"

# Админка
admin:
  secret_key: "замените-на-случайную-строку"
  db_url: "sqlite:///data/admin.db"
  username: "admin"
  password: "admin"

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

Все запросы к `/parse` и `/parse/batch` требуют заголовок `X-API-Key`.

### Парсинг одного товара

```bash
curl -X POST http://localhost:8004/parse \
  -H "X-API-Key: ваш-ключ" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.wildberries.ru/catalog/17386348/detail.aspx"}'
```

### Пакетный парсинг

```bash
curl -X POST http://localhost:8004/parse/batch \
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
curl "http://localhost:8004/parse/detect?url=https://ozon.ru/product/example-123456"
```

### Список поддерживаемых маркетплейсов

```bash
curl http://localhost:8004/marketplaces
```

Поддерживаемые маркетплейсы: **Wildberries**, **Ozon**, **Яндекс Маркет**, **MegaMarket**.

---

## 7. Обновление

Если у вас активна подписка на обновления:

```bash
./update.sh
```

Скрипт проверит подписку, скачает последний релиз с GitHub, создаст бэкап и применит обновление.

---

## 8. Структура проекта

```
parser_python/
├── api.py                  # FastAPI приложение, точка входа
├── config.py               # Загрузка settings.yml
├── register.py             # Регистрация лицензии (запустить один раз)
├── start.sh                # Запуск сервера
├── update.sh               # Обновление приложения
├── Makefile                # Сборка релиза (для разработчика)
├── requirements.txt        # Зависимости
│
├── licensing/
│   ├── checker.py          # Проверка лицензии при запуске
│   └── __init__.py
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

**`ОШИБКА ЛИЦЕНЗИИ: Файл license.json не найден`**
— Запустите `python register.py` и отправьте ключ разработчику.

**`ОШИБКА ЛИЦЕНЗИИ: Лицензия не активирована`**
— Разработчик ещё не активировал ваш ключ. Подождите подтверждения.

**`ОШИБКА ЛИЦЕНЗИИ: Лицензия выдана для другого устройства`**
— Удалите `license.json`, запустите `python register.py` заново и отправьте новый ключ.

**`ОШИБКА ЛИЦЕНЗИИ: Не удалось проверить лицензию`**
— Нет интернета. Приложение требует подключения для проверки лицензии.

**`patchright` не находит браузер**
— Запустите: `.venv/bin/patchright install chromium`

**Redis недоступен**
— Приложение запустится, но статистика в дашборде работать не будет. Установите и запустите Redis (см. раздел 5).

**`zsh: command not found: python`**
— Используйте `python3`, либо настройте алиас (см. раздел 1).
