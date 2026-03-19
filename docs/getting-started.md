# Getting Started

## Требования

- Python 3.12+
- Redis 7+
- Chromium (для Patchright — нужен для Ozon и MegaMarket)

---

## 1. Установка Python

### macOS

```bash
# Через Homebrew
brew install python@3.12

# Проверить версию
python3 --version
```

> На macOS команда `python` по умолчанию недоступна — используйте `python3` и `pip3`.
> Чтобы не писать цифру, добавьте алиасы в `~/.zshrc`:
> ```bash
> echo "alias python=python3" >> ~/.zshrc
> echo "alias pip=pip3" >> ~/.zshrc
> source ~/.zshrc
> ```

### Windows

Скачайте установщик с [python.org](https://www.python.org/downloads/) и при установке отметьте **"Add Python to PATH"**.

```powershell
# Проверить версию
python --version
```

### Linux (Ubuntu/Debian)

```bash
sudo apt update && sudo apt install python3.12 python3.12-venv python3-pip -y
python3 --version
```

---

## 2. Виртуальное окружение

Рекомендуется работать в изолированном окружении.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
```

После активации в терминале появится префикс `(.venv)`. Все дальнейшие команды выполняются внутри окружения.

---

## 3. Установка зависимостей

```bash
pip install -r requirements.txt
```

Для работы **админки** дополнительно:

```bash
pip install redis[asyncio] tortoise-orm[aiosqlite] aerich fastapi-admin sse-starlette
```

> Без этих пакетов API парсера запустится в штатном режиме, но `/admin` будет недоступен.

Установить браузер для Patchright (один раз):

```bash
patchright install chromium
```

---

## 4. Конфигурация

Все настройки находятся в `data/settings.yml`.

```yaml
# Подключение к Redis
redis:
  host: "localhost"
  port: 6379
  db: 0
  # password: ""

# Админка
admin:
  secret_key: "замените-на-случайную-строку"   # обязательно поменять в prod
  db_url: "sqlite:///data/admin.db"              # или postgresql://user:pass@host/db

# Хост и порт API
app:
  host: "localhost"
  port: 8004

# API-ключ для /parse эндпоинтов
security:
  api_key: "ваш-секретный-ключ"

# Прокси
proxy:
  active: false      # true — использовать прокси из data/proxy.txt
  type: "http"       # http или socks5

# Debug (включает /docs и /redoc)
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

## 5. Запуск Redis

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

### Windows

Redis официально не поддерживается на Windows. Используйте Docker (см. ниже) или [WSL2](https://learn.microsoft.com/ru-ru/windows/wsl/install).

### Docker (любая ОС)

```bash
docker run -d -p 6379:6379 --name redis redis:7-alpine
```

Проверить подключение:

```bash
redis-cli ping
# Ответ: PONG
```

---

## 6. Миграции БД (только для админки)

Выполняется один раз перед первым запуском:

```bash
aerich init-db
aerich migrate
aerich upgrade
```

Если таблицы уже существуют — достаточно `aerich upgrade`.

---

## 7. Запуск API

### macOS / Linux

```bash
python3 api.py
```

### Windows

```powershell
python api.py
```

Сервер стартует на `http://localhost:8004`.

| URL | Описание |
|-----|----------|
| `http://localhost:8004/` | Корневой эндпоинт |
| `http://localhost:8004/health` | Статус сервиса |
| `http://localhost:8004/docs` | Swagger UI (только при `debug.active: true`) |
| `http://localhost:8004/admin` | Админ-панель |

---

## 8. Создание admin-пользователя

После первого запуска создайте пользователя для входа в `/admin`:

### macOS / Linux

```bash
python3 -c "
import asyncio
from tortoise import Tortoise
from models.admin import AdminUser

async def create_user():
    await Tortoise.init(db_url='sqlite:///data/admin.db', modules={'models': ['models.admin', 'models.job']})
    await AdminUser.create(username='admin', password=AdminUser.hash_password('ваш-пароль'))
    await Tortoise.close_connections()
    print('Admin user created')

asyncio.run(create_user())
"
```

### Windows

```powershell
python -c "
import asyncio
from tortoise import Tortoise
from models.admin import AdminUser

async def create_user():
    await Tortoise.init(db_url='sqlite:///data/admin.db', modules={'models': ['models.admin', 'models.job']})
    await AdminUser.create(username='admin', password=AdminUser.hash_password('ваш-пароль'))
    await Tortoise.close_connections()
    print('Admin user created')

asyncio.run(create_user())
"
```

---

## 9. Использование API

### Парсинг одного товара

```bash
curl -X POST http://localhost:8004/parse \
  -H "X-API-Key: ваш-секретный-ключ" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.wildberries.ru/catalog/17386348/detail.aspx"}'
```

### Пакетный парсинг

```bash
curl -X POST http://localhost:8004/parse/batch \
  -H "X-API-Key: ваш-секретный-ключ" \
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

Поддерживаемые маркетплейсы: **Wildberries**, **Ozon**, **YandexMarket**, **MegaMarket**.

---

## 10. Структура проекта

```
parser_python/
├── api.py                  # FastAPI приложение, точка входа
├── config.py               # Загрузка settings.yml
├── requirements.txt
├── aerich.ini              # Конфиг миграций TortoiseORM
│
├── crawlers/               # Модули источников
│   ├── base.py             # BaseCrawler + авторегистрация
│   ├── registry.py         # CrawlerRegistry
│   ├── ozon.py
│   ├── wildberries.py
│   ├── megamarket.py
│   └── yandex_market.py
│
├── services/               # Бизнес-логика
│   ├── parsing.py          # ParsingService
│   ├── stats.py            # StatsCollector (Redis)
│   └── redis_client.py     # Async Redis-клиент
│
├── admin/                  # fastapi-admin ресурсы
│   ├── __init__.py         # setup_admin / teardown_admin
│   ├── dashboard.py        # Dashboard виджеты
│   └── resources/
│       ├── sources.py      # Управление краулерами
│       ├── jobs.py         # История парсингов
│       ├── proxies.py      # Управление прокси
│       └── logs.py         # Real-time логи (SSE)
│
├── models/                 # Pydantic + TortoiseORM модели
│   ├── admin.py            # AdminUser
│   ├── job.py              # Job (история задач)
│   └── product.py          # ProductCard, ParseRequest/Response
│
├── core/
│   └── managers.py         # ProxyManager
│
├── utils/
│   └── logger.py           # Loguru setup + SSE sink
│
└── data/
    ├── settings.yml        # Конфигурация
    └── proxy.txt           # Список прокси (опционально)
```

---

## Переменные окружения (опционально)

Значения из `settings.yml` можно переопределить через переменные окружения — полезно для Docker/CI:

| Переменная | Соответствует |
|-----------|---------------|
| `REDIS_HOST` | `redis.host` |
| `REDIS_PORT` | `redis.port` |
| `REDIS_PASSWORD` | `redis.password` |
| `ADMIN_SECRET_KEY` | `admin.secret_key` |
| `API_KEY` | `security.api_key` |

---

## Возможные проблемы

**`zsh: command not found: python` / `zsh: command not found: pip`**
— На macOS используйте `python3` и `pip3`, либо настройте алиасы (см. раздел 1).

**`Admin setup failed: No module named 'fastapi_admin'`**
— Установите опциональные зависимости из шага 3. API продолжит работу без админки.

**`Redis init failed`**
— Убедитесь, что Redis запущен (`redis-cli ping` → `PONG`) и параметры в `settings.yml` корректны.

**`patchright` не находит браузер**
— Выполните `patchright install chromium`.

**`Table already exists` при миграциях**
— БД уже инициализирована, выполните только `aerich upgrade`.

**Windows: ошибка при установке `curl-cffi` или `patchright`**
— Установите [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) и повторите.
