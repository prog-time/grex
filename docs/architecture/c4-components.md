# C4, уровень 3: компоненты

Два места, устройство которых важно понимать при изменениях: парсеры и мониторинг.

## Парсеры (`crawlers/`)

```mermaid
flowchart TB
    service["ParsingService.parse_product()<br/>services/parsing.py"]
    registry["CrawlerRegistry<br/>crawlers/registry.py<br/>площадка по url_pattern"]
    base["BaseCrawler<br/>crawlers/base.py<br/>get_proxy(), normalize_url(), _run_in_process()"]
    cookie["CookieCrawler<br/>crawlers/base.py<br/>куки из браузера в data/*_cookies.json"]
    sem["Семафор браузеров<br/>browser.max_concurrent"]
    proxy["ProxyManager<br/>core/managers.py<br/>data/proxy.txt, кэш в Redis"]

    subgraph http["Парсеры на BaseCrawler"]
        wb["WildberriesCrawler"]
        mm["MmCrawler"]
        cit["CitilinkCrawler"]
        vse["VseInstrumentiCrawler"]
        cdek["CdekCrawler"]
        bigam["BigamCrawler"]
    end
    subgraph ck["Парсеры на CookieCrawler"]
        ozon["OzonCrawler"]
        ym["YandexMarketCrawler"]
        mega["MegaMarketCrawler"]
    end

    service --> registry
    registry --> http
    registry --> ck
    http --> base
    ck --> cookie
    cookie --> base
    base --> proxy
    base --> sem
```

- Новый парсер регистрируется сам при объявлении класса: достаточно задать `marketplace_name` и `url_pattern` (порядок описан в docstring `crawlers/base.py`).
- Каждый парсер идёт от быстрого способа к медленному: HTTP API → разбор HTML → браузер. Пример для Wildberries: [сценарий разбора](sequences.md#разбор-товара-wildberries).
- Браузерные функции выполняются в отдельном процессе, поэтому должны быть `@staticmethod` и получать всё через аргументы (ADR 0009).

## Мониторинг (`services/`)

```mermaid
flowchart LR
    tick["monitor_scheduler()<br/>цикл раз в monitoring.tick_seconds"]
    due["_due_targets()<br/>monitor_enabled и next_run_at <= now"]
    check["_check_one()"]
    prev["_previous_price()<br/>админка: по URL, API: по target_id"]
    parse["ParsingService.parse_product()"]
    upsert["_upsert_parsed_product()<br/>только цели админки"]
    snap["_record_snapshot()<br/>price_snapshots"]
    alert["_maybe_log_alert()<br/>price_alerts, если изменение больше порога"]
    resched["_reschedule()<br/>next_run_at = now + интервал + jitter"]
    report["ensure_report_fresh()<br/>Excel в data/reports/"]
    deliver["deliver_due()<br/>отчёт получателям, у которых подошла частота"]
    tg["services/telegram.py<br/>send_document"]

    tick --> due --> check
    check --> prev
    check --> parse
    check --> upsert
    check --> snap
    check --> alert
    check --> resched
    tick --> report --> deliver --> tg
```

- Изменения цены не отправляются в Telegram сразу. Они записываются в `price_alerts` (видны в панели и в `/v1/alerts`) и попадают на лист изменений в Excel-отчёте, который уходит по расписанию получателя (`alert_sources.frequency`).
- Мониторы API проходят тот же путь, но не пишут в `parsed_products` и не попадают в отчёт владельца (ADR 0003).
