# Модель данных (ERD)

База: SQLite, файл `data/admin.db` (путь задаёт `admin.db_url` в `data/settings.yml`). Таблицы описаны моделями SQLAlchemy в `models/` и создаются при старте в `init_db()` (`models/job.py`).

Внешний ключ в базе объявлен только один: `api_job_items.job_id` → `api_jobs.id` (с `ON DELETE CASCADE`). Остальные связи на схеме логические: колонка хранит `id` другой таблицы, но база это не проверяет. Целостность держит код. Например, при удалении API-ключа его мониторы, история цен и задачи удаляются явно в `admin/resources/api_keys.py`.

```mermaid
erDiagram
    api_keys {
        string id PK
        string name
        string key_hash UK "SHA-256 от ключа, сам ключ не хранится"
        string prefix "первые символы для показа в панели"
        datetime created_at
        datetime last_used_at
        datetime revoked_at "NULL, пока ключ активен"
    }
    parse_targets {
        string id PK
        string name
        text url
        string marketplace
        bool enabled
        datetime created_at
        bool monitor_enabled
        int interval_seconds "NULL = monitoring.default_interval_seconds"
        datetime next_run_at
        datetime last_checked_at
        string last_status "ok | failed"
        string api_key_id "NULL = цель админки, иначе монитор API"
    }
    parse_sessions {
        string id PK
        string status "pending | running | completed | failed"
        int total
        int succeeded
        int failed_count
        datetime started_at
        datetime finished_at
    }
    parsed_products {
        string id PK
        string session_id
        string target_id
        text url
        string marketplace
        text title
        string sku
        float original_price
        float current_price
        text images "JSON-массив"
        text description
        text characteristics "JSON-объект"
        datetime parsed_at
    }
    price_snapshots {
        string id PK
        string target_id
        text url
        string marketplace
        float current_price
        float original_price
        string available "зарезервировано"
        datetime parsed_at
    }
    price_alerts {
        string id PK
        string target_id
        text url
        string marketplace
        text title
        string kind "drop | rise"
        float old_price
        float new_price
        float change_pct
        datetime created_at
    }
    alert_sources {
        string id PK
        string type "telegram"
        text recipient
        string chat_id
        string frequency
        datetime created_at
        datetime last_sent_at
    }
    jobs {
        string id PK
        text url
        string marketplace
        string status "success | failed"
        datetime started_at
        datetime finished_at
        float processing_time
        text error
    }
    api_jobs {
        string id PK
        string api_key_id
        string status "pending | running | completed | failed"
        int total
        int done
        int succeeded
        int failed_count
        text error
        datetime created_at
        datetime started_at
        datetime finished_at
    }
    api_job_items {
        int id PK
        string job_id FK
        int position "порядок URL в запросе"
        text url
        int success
        string marketplace
        text data "JSON карточки товара"
        text error
        float processing_time
    }

    api_keys ||--o{ parse_targets : "владеет мониторами (api_key_id)"
    api_keys ||--o{ api_jobs : "создаёт (api_key_id)"
    api_jobs ||--|{ api_job_items : "FK, ON DELETE CASCADE"
    parse_targets ||--o{ price_snapshots : "история цены (target_id)"
    parse_targets ||--o{ price_alerts : "изменения цены (target_id)"
    parse_targets ||--o| parsed_products : "последняя карточка (target_id, по URL)"
    parse_sessions ||--o{ parsed_products : "запуск, в котором разобрана (session_id)"
```

## Что важно знать о связях

- **Владелец цели.** `parse_targets.api_key_id` = NULL означает цель админ-панели, непустое значение — монитор публичного API (`/v1/monitors`). Админка и отчёты показывают только свои цели через фильтры `admin_owned()` и `not_api_owned()` из `models/parse_target.py`. Любой новый запрос к целям, истории цен или оповещениям должен использовать эти фильтры.
- **История цен админки связана ещё и по URL.** Для целей админки предыдущая цена ищется по `price_snapshots.url` (так удалённая и заново добавленная цель продолжает свой график), для мониторов API — только по `target_id` (`_previous_price` в `services/monitoring.py`).
- **`parsed_products`** хранит одну строку на URL для раздела «Товары»: при повторном разборе строка с тем же `url` перезаписывается (`_upsert_parsed_product` в `admin/resources/targets.py`), а `session_id` указывает на последний запуск. Мониторы API туда не пишут, их цены лежат только в `price_snapshots`.
- **`jobs`** — журнал каждого вызова парсера (раздел «История задач»), пишется в `ParsingService._save_job`. С другими таблицами не связан.
- **`alert_sources`** — куда отправлять оповещения и отчёты (Telegram), с таблицами целей не связан.

## Как меняется схема

Миграций (Alembic) нет. `Base.metadata.create_all()` создаёт недостающие таблицы, но не меняет существующие. Новая колонка в уже существующей таблице добавляется так:

1. Колонка объявляется в модели.
2. Та же колонка с SQL-типом добавляется в `_ADDED_COLUMNS` в `models/job.py`. При старте `_migrate_added_columns()` выполнит `ALTER TABLE … ADD COLUMN` для отсутствующих колонок.

Так можно только добавлять колонки. Переименование, удаление колонки или смена типа требуют отдельного решения (ADR) и ручного переноса данных. Почему так, описано в [ADR 0001](../adr/0001-sqlite-without-migrations.md).

## Что хранится вне SQLite

| Где | Что |
|---|---|
| Redis `proxy:pool` | кэш списка прокси (TTL 60 с) |
| Redis `grex:image_queue` | очередь скачивания фотографий товаров |
| Redis `stats:*` | счётчики парсинга |
| `data/settings.yml` | настройки, логин и пароль панели, `secret_key` |
| `data/proxy.txt`, `data/proxies.json` | прокси |
| `data/*_cookies.json` | куки маркетплейсов |
| `data/wb_upstreams.json` | карта CDN-хостов Wildberries |
| `data/wb_api_session.json` | антибот-токен Wildberries и заголовки браузера |
| `data/reports/` | Excel-отчёты мониторинга |
