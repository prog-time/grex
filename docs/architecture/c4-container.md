# C4, уровень 2: контейнеры

Из каких частей состоит установка и как они связаны. Всё приложение работает одним процессом Python (`python api.py`), браузеры запускаются отдельными процессами.

```mermaid
flowchart TB
    owner(["Владелец"])
    integrator(["Клиент API"])

    subgraph app["Процесс приложения: python api.py (FastAPI, uvicorn, один воркер)"]
        api["REST API /v1<br/>api.py"]
        panel["Веб-панель /admin<br/>starlette-admin + admin/resources/*"]
        parsing["ParsingService<br/>services/parsing.py"]
        crawlers["Парсеры площадок<br/>crawlers/"]
        sched["Планировщик мониторинга<br/>services/monitor_scheduler.py"]
        jobs["Фоновые задачи /v1/jobs<br/>services/api_jobs.py"]
        imgw["Воркер скачивания фото<br/>services/image_queue.py"]
    end

    browser["Chromium (Patchright)<br/>отдельные процессы, до browser.max_concurrent"]
    db[("SQLite<br/>data/admin.db")]
    redis[("Redis<br/>необязателен")]
    files[("Файлы data/<br/>settings.yml, куки, прокси, отчёты, фото")]

    marketplaces[["Маркетплейсы"]]
    telegram[["Telegram"]]

    owner --> panel
    integrator --> api
    api --> parsing
    api --> jobs
    panel --> parsing
    jobs --> parsing
    sched --> parsing
    parsing --> crawlers
    crawlers -- "HTTP (curl_cffi)" --> marketplaces
    crawlers -- "_run_in_process()" --> browser
    browser --> marketplaces

    api --> db
    panel --> db
    jobs --> db
    sched --> db
    parsing -- "журнал jobs" --> db
    parsing -- "очередь фото" --> redis
    imgw --> redis
    imgw --> files
    panel --> files
    crawlers --> files
    crawlers -- "кэш прокси" --> redis
    sched -- "Excel-отчёт" --> files
    sched -- "отправка отчёта" --> telegram
```

## За что отвечает каждая часть

| Часть | Где в коде | Ответственность |
|---|---|---|
| REST API | `api.py` | Публичные маршруты `/v1/*`, проверка `X-API-Key`. Старые пути без `/v1` работают как устаревшие |
| Веб-панель | `admin/__init__.py`, `admin/resources/*.py`, `admin/templates/` | Страницы starlette-admin и JSON-маршруты `/admin/*` под сессией администратора |
| ParsingService | `services/parsing.py` | Выбор парсера по URL, нормализация ссылки, пакетный разбор с ограничением параллельности, журнал `jobs`, постановка фото в очередь |
| Парсеры | `crawlers/` | Разбор карточки и сбор ссылок на одной площадке, см. [компоненты](c4-components.md) |
| Планировщик мониторинга | `services/monitor_scheduler.py`, `services/monitoring.py`, `services/reporting.py` | Перепроверка целей по расписанию, история цен, изменения цены, Excel-отчёт и его отправка в Telegram |
| Задачи /v1/jobs | `services/api_jobs.py` | Фоновый разбор до 1000 ссылок с сохранением каждого результата |
| Воркер фото | `services/image_queue.py`, `services/image_downloader.py` | Скачивание фотографий товаров из очереди Redis |
| Chromium | `crawlers/base.py` `_run_in_process()` | Страницы, которые не отдаются обычным HTTP: антиботы, капча, куки (ADR 0009) |
| SQLite | `models/` | Цели, товары, история цен, ключи и задачи API, см. [ERD](erd.md) |
| Redis | `services/redis_client.py` | Очередь фото, кэш прокси, счётчики. Без него приложение работает, но фото не скачиваются |

## Что запускается при старте

`lifespan()` в `api.py`: подключение к Redis и воркер фото → `setup_admin()` (база, импорт ключа из настроек, маршруты панели) → планировщик мониторинга → сброс «зависших» сессий парсинга и задач API, оставшихся от прошлого запуска.
