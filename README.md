# Grex Parser

REST API для парсинга карточек товаров с российских маркетплейсов. По ссылке на товар возвращает название, артикул, цены и изображения.

## Поддерживаемые маркетплейсы

| Маркетплейс | Домены |
|---|---|
| Wildberries | `wildberries.ru`, `.by`, `.kz` |
| Ozon | `ozon.ru`, `.by`, `.kz` |
| Яндекс Маркет | `market.yandex.ru`, `.by` |
| MegaMarket | `megamarket.ru` |

---

## Установка

### Требования

- Python 3.12+
- Redis 7+ (опционально — для статистики и кэша прокси)

### Шаг 1. Регистрация лицензии

```bash
python register.py
```

Скрипт создаст `license.json` и выведет ключ активации. Отправьте ключ разработчику — после активации можно запускать приложение.

### Шаг 2. Запуск

```bash
./start.sh
```

Скрипт автоматически создаст виртуальное окружение, установит зависимости, браузер Chromium и запустит сервер.

Сервер запустится на `http://localhost:8004`.

---

## Конфигурация

Все настройки в `data/settings.yml`:

```yaml
app:
  host: "localhost"
  port: 8004

security:
  api_key: "ваш-секретный-ключ"   # передаётся в заголовке X-API-Key

admin:
  secret_key: "замените-в-продакшн"
  username: "admin"
  password: "admin"

redis:
  host: "localhost"
  port: 6379

proxy:
  active: false       # true — использовать прокси из data/proxy.txt
  type: "http"        # http или socks5

debug:
  active: true        # включает /docs и /redoc
  logging_in_file: true
```

### Прокси

Добавьте прокси в `data/proxy.txt` по одному на строку:

```
login:password@host:port
```

### Куки (Ozon и MegaMarket)

При первом запросе к Ozon или MegaMarket автоматически открывается браузер, получает куки и сохраняет их в `data/`. При следующих запросах куки берутся из файла.

---

## API

### Аутентификация

Запросы к `/parse` и `/parse/batch` требуют заголовок:

```
X-API-Key: ваш-ключ
```

### Эндпоинты

| Метод | URL | Описание |
|---|---|---|
| `GET` | `/` | Информация о сервисе |
| `GET` | `/health` | Статус и uptime |
| `GET` | `/marketplaces` | Список маркетплейсов и примеры URL |
| `GET` | `/parse/detect?url=...` | Определить маркетплейс по URL |
| `POST` | `/parse` | Парсинг одного товара |
| `POST` | `/parse/batch` | Пакетный парсинг |

---

### POST /parse

```bash
curl -X POST http://localhost:8004/parse \
  -H "X-API-Key: ваш-ключ" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.wildberries.ru/catalog/17386348/detail.aspx"}'
```

Ответ:

```json
{
  "success": true,
  "marketplace": "wildberries",
  "processing_time": 1.23,
  "data": {
    "title": "Название товара",
    "sku": "17386348",
    "original_price": 1500.0,
    "current_price": 1200.0,
    "images": ["https://..."],
    "source_url": "https://..."
  }
}
```

---

### POST /parse/batch

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

- `urls` — список ссылок (до 100)
- `concurrency` — количество параллельных запросов

---

### Пример на Python

```python
import requests

API_URL = "http://localhost:8004"
API_KEY = "ваш-ключ"

response = requests.post(
    f"{API_URL}/parse",
    headers={"X-API-Key": API_KEY},
    json={"url": "https://www.wildberries.ru/catalog/17386348/detail.aspx"},
)

data = response.json()
if data["success"]:
    product = data["data"]
    print(product["title"], product["current_price"])
```

---

## Обновление

```bash
./update.sh
```

Скрипт проверит подписку, скачает последний релиз, создаст бэкап и применит обновление.

---

## Структура проекта

```
parser_python/
├── register.py             # Регистрация лицензии (запустить один раз)
├── start.sh                # Запуск сервера
├── update.sh               # Обновление приложения
├── api.py                  # FastAPI приложение
├── config.py               # Загрузка настроек
│
├── licensing/
│   └── checker.py          # Проверка лицензии
│
├── crawlers/               # Парсеры маркетплейсов
│   ├── base.py
│   ├── wildberries.py
│   ├── ozon.py
│   ├── yandex_market.py
│   └── megamarket.py
│
├── services/
│   ├── parsing.py          # Основная логика парсинга
│   ├── stats.py            # Статистика
│   └── redis_client.py
│
├── admin/                  # Админ-панель (/admin)
├── models/                 # Pydantic и ORM модели
├── core/                   # ProxyManager, исключения
├── utils/                  # Логирование, утилиты
│
└── data/
    ├── settings.yml        # Конфигурация
    ├── admin.db            # SQLite БД
    ├── proxy.txt           # Список прокси
    └── *_cookies.json      # Куки маркетплейсов
```

---

## Возможные ошибки

| Ошибка | Решение |
|---|---|
| `ОШИБКА ЛИЦЕНЗИИ: Файл license.json не найден` | Запустите `python register.py` |
| `ОШИБКА ЛИЦЕНЗИИ: Лицензия не активирована` | Отправьте ключ разработчику и дождитесь активации |
| `ОШИБКА ЛИЦЕНЗИИ: Лицензия выдана для другого устройства` | Удалите `license.json`, запустите `register.py` заново |
| `ОШИБКА ЛИЦЕНЗИИ: Не удалось проверить лицензию` | Проверьте интернет-соединение |
| Redis недоступен | Сервер запустится, но статистика в дашборде не будет работать |
| Patchright не находит браузер | Запустите `.venv/bin/patchright install chromium` |
