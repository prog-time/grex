# Лицензирование и дистрибуция

Руководство для разработчика по управлению лицензиями, сборке релизов и обновлению у клиентов.

---

## Как работает система

```
Клиент запускает register.py
  → ключ = HMAC(MAC-адрес, SECRET)
  → сохраняется в license.json
  → клиент присылает ключ тебе

Ты добавляешь ключ в grex/licenses.json на GitHub (prog-time/licenses)
  → редактируешь файл вручную или через браузер на GitHub
  → коммитишь

При каждом запуске приложение:
  1. Вычисляет HMAC(MAC) и сравнивает с ключом в license.json
     → если не совпадает — license.json скопирован на чужую машину, стоп
  2. Скачивает grex/licenses.json с GitHub, проверяет статус ключа
     → pending / blocked / отсутствует → стоп
     → active → запуск
```

**Ключ криптографически привязан к MAC-адресу.** Скопировать `license.json` на другую машину бесполезно — проверка не пройдёт.

---

## Структура файлов

```
register.py              — клиент запускает один раз для регистрации
license.json             — генерируется register.py, не трогать вручную
licensing/
├── checker.py           — проверка при запуске (обфусцируется)
└── __init__.py
update.sh                — обновление у клиента
Makefile                 — сборка релиза

github.com/prog-time/licenses  — репозиторий лицензий
└── grex/
    └── licenses.json    — список активных ключей
```

---

## Перед первым релизом (один раз)

### 1. Убедись что `_SECRET` одинаковый в обоих файлах

```python
# register.py          — строка _SECRET
# licensing/checker.py — строка _SECRET
_SECRET = "одно-и-то-же-значение"
```

> Не меняй `_SECRET` после первого релиза — все выданные ключи перестанут работать.

### 2. Создай пустой `grex/licenses.json` в репозитории `prog-time/licenses`

```json
{}
```

### 3. Укажи репозиторий в `update.sh`

```bash
GITHUB_REPO="prog-time/grex"
```

---

## Продажа нового клиента

### Шаг 1. Клиент запускает регистрацию

```bash
python register.py
```

Выводит:
```
  Ваш ключ:  a3f9c2d1e8b4...7f2e1a0b9c3d

  Отправьте ключ разработчику для активации.
```

Клиент присылает тебе этот ключ.

### Шаг 2. Добавляешь ключ в `grex/licenses.json`

Открываешь `github.com/prog-time/licenses/blob/main/grex/licenses.json` → редактируешь → коммитишь.

```json
{
  "a3f9c2d1e8b4...7f2e1a0b9c3d": {
    "status": "active",
    "customer": "ООО Ромашка",
    "updates_until": "2027-03-21"
  }
}
```

Через ~1 минуту (GitHub CDN) клиент может запускать `./start.sh`.

---

## Управление лицензиями

Всё делается через редактирование `grex/licenses.json` в репозитории `prog-time/licenses`.

**Заблокировать клиента:**
```json
"a3f9c2d1e8b4...": { "status": "blocked", "customer": "ООО Ромашка", "updates_until": "2027-03-21" }
```

**Продлить подписку на обновления** — изменить `updates_until`:
```json
"a3f9c2d1e8b4...": { "status": "active", "customer": "ООО Ромашка", "updates_until": "2028-03-21" }
```

---

## Формат `licenses.json`

```json
{
  "a3f9c2d1e8b4...": {
    "status": "active",
    "customer": "ООО Ромашка",
    "updates_until": "2027-03-21"
  },
  "b7e1f3a2c9d5...": {
    "status": "blocked",
    "customer": "ИП Иванов",
    "updates_until": "2026-12-31"
  }
}
```

| Статус | Поведение |
|--------|-----------|
| `active` | Приложение запускается |
| `blocked` | Приложение не запускается |
| отсутствует | Приложение не запускается, просит прислать ключ |

---

## Проверки при запуске

| # | Проверка | Блокирует запуск | Блокирует обновления |
|---|----------|:---:|:---:|
| 1 | Файл `license.json` существует | Да | Да |
| 2 | Ключ соответствует MAC этой машины | Да | — |
| 3 | Ключ найден в `licenses.json` со статусом `active` | Да | — |
| 4 | `updates_until` не истёк | Нет | Да |

> После истечения `updates_until` приложение продолжает работать — клиент не может скачать обновления до продления подписки.

---

## Разработка на своей машине

```bash
# Зарегистрировать свою машину
python register.py

# Добавить свой ключ в grex/licenses.json на GitHub вручную
# status: active, updates_until: 2030-01-01

# Запустить
./start.sh
```

---

## Сборка и выпуск обновления

### Шаг 1. Обнови версию

```yaml
# data/settings.yml
soft_version: "1.1.0"
```

### Шаг 2. Собери релиз

```bash
make release VERSION=1.1.0
```

Команда:
1. Удаляет старый `dist/`
2. Обфусцирует `crawlers/`, `services/`, `licensing/checker.py`, `register.py` через PyArmor
3. Копирует остальные файлы (`api.py`, `start.sh`, `update.sh` и др.)
4. Упаковывает в `release-v1.1.0.tar.gz`

> Если PyArmor не установлен: `pip install pyarmor`

### Шаг 3. Загрузи на GitHub Releases

```bash
gh release create v1.1.0 release-v1.1.0.tar.gz \
  --title "v1.1.0" \
  --notes "Описание изменений"
```

### Шаг 4. Клиент обновляется

```bash
./update.sh
```

---

## Что обфусцируется, что нет

| Файл / папка | В релизе | Причина |
|---|---|---|
| `crawlers/` | Обфусцирован | Бизнес-логика |
| `services/` | Обфусцирован | Бизнес-логика |
| `licensing/checker.py` | Обфусцирован | Скрывает `_SECRET` |
| `register.py` | Обфусцирован | Скрывает `_SECRET` |
| `api.py`, `config.py` | Как есть | Нужны для диагностики |
| `start.sh`, `update.sh` | Как есть | Должны быть читаемы |

---

## Шпаргалка команд

```bash
# Сборка
make build                          # обфусцировать → dist/
make release VERSION=1.2.0         # собрать архив релиза
make clean                         # удалить dist/

# Публикация
gh release create v1.2.0 release-v1.2.0.tar.gz
```
