# Сценарии

Как части системы взаимодействуют во времени в процессах, которые трудно понять по коду.

## Разбор товара Wildberries

Цепочка запасных путей в `WildberriesCrawler.parse()` (`crawlers/wildberries.py`). Внутренний API и токен описаны в ADR 0004.

```mermaid
sequenceDiagram
    participant S as ParsingService
    participant W as WildberriesCrawler
    participant CDN as basket CDN (card.json)
    participant I as www.wildberries.ru/__internal
    participant B as Chromium (отдельный процесс)

    S->>W: parse(url)
    W->>CDN: card.json по артикулу
    CDN-->>W: название, описание, характеристики, число фото
    W->>I: u-card/cards/v4/detail (cookie x_wbaas_token + заголовки браузера)
    alt токена нет или ответ 401/403/498
        W->>B: _process_get_api_session()
        B-->>W: cookie и заголовки (сохраняются в data/wb_api_session.json)
        W->>I: повтор запроса
    end
    I-->>W: цены в копейках (basic, product)
    opt цены не получены
        W->>W: card.wb.ru, затем search.wb.ru (сейчас отвечают 403)
        W->>B: _process_get_prices(): цены со страницы
    end
    W-->>S: ProductCard
    S->>S: журнал в jobs, фото в очередь Redis
```

## Фоновая задача /v1/jobs

`services/api_jobs.py`. Состояния задачи: [диаграмма](states.md#задача-api-api_jobsstatus).

```mermaid
sequenceDiagram
    participant C as Клиент API
    participant A as api.py
    participant J as services/api_jobs.py
    participant P as ParsingService
    participant D as SQLite

    C->>A: POST /v1/jobs {urls}
    A->>J: create_job(api_key_id, urls)
    J->>D: под блокировкой ключа: число незавершённых < 5? вставка api_jobs (pending)
    J-->>A: задача
    A-->>C: 202 {id, status: pending}
    J->>D: status = running
    loop каждая ссылка, параллельно до batch.concurrency
        J->>P: parse_product(url)
        P-->>J: результат
        J->>D: api_job_items + счётчики done/succeeded/failed
    end
    J->>D: status = completed
    C->>A: GET /v1/jobs/{id}
    A-->>C: прогресс и готовые результаты
```

- Если сервер перезапустился во время задачи, при старте `reset_stale_jobs()` помечает её `failed`.
- Отзыв ключа отменяет его задачи, они получают `failed` с ошибкой «API key revoked» (PR #46).

## Проверка монитора и изменение цены

`services/monitoring.py`, см. [компоненты мониторинга](c4-components.md#мониторинг-services).

```mermaid
sequenceDiagram
    participant T as monitor_scheduler
    participant M as monitoring
    participant P as ParsingService
    participant D as SQLite
    participant R as reporting
    participant TG as Telegram

    T->>M: run_due_once()
    M->>D: цели с monitor_enabled и next_run_at <= now
    loop каждая цель
        M->>D: предыдущая цена
        M->>P: parse_product(url)
        P-->>M: карточка
        M->>D: price_snapshots
        alt цена изменилась больше drop_percent / rise_percent
            M->>D: price_alerts
        end
        M->>D: next_run_at, last_status
    end
    T->>R: ensure_report_fresh(), deliver_due()
    R->>D: получатели, у которых подошла частота
    R->>TG: sendDocument (Excel-отчёт)
    R->>D: last_sent_at
```
