# Состояния

## Задача API (`api_jobs.status`)

`services/api_jobs.py`.

```mermaid
stateDiagram-v2
    [*] --> pending: POST /v1/jobs
    pending --> running: фоновая задача стартовала
    running --> completed: все ссылки обработаны (часть может быть с ошибкой)
    running --> failed: исключение во всей задаче
    running --> failed: ключ отозван
    pending --> failed: ключ отозван
    pending --> failed: перезапуск сервера (reset_stale_jobs)
    running --> failed: перезапуск сервера (reset_stale_jobs)
    completed --> [*]
    failed --> [*]
```

- Ошибка отдельной ссылки не переводит задачу в `failed`: она видна в `results` со `success: false`, а задача завершается как `completed`.
- Пока у ключа 5 задач в `pending` или `running`, новая получает `429`.

## Запуск парсинга из панели (`parse_sessions.status`)

`admin/resources/targets.py`: «Запустить все» и «Запустить выбранные».

```mermaid
stateDiagram-v2
    [*] --> pending: run-all / run-selected
    pending --> running: фоновый разбор стартовал
    running --> completed: все цели обработаны
    pending --> failed: перезапуск сервера (сброс при старте в api.py)
    running --> failed: перезапуск сервера (сброс при старте в api.py)
    completed --> [*]
    failed --> [*]
```
