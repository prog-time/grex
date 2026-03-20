# Лицензирование и дистрибуция

Руководство для разработчика по управлению лицензиями, сборке релизов и обновлению у клиентов.

---

## Как работает система

```
Клиент запускает register.py
  → ключ = HMAC(MAC-адрес, SECRET)
  → сохраняется в license.json
  → клиент присылает ключ тебе

Ты добавляешь ключ в licenses.json на GitHub
  → python licensing/generate.py activate --key "..." --customer "..." --updates-until "..."
  → git commit + git push

При каждом запуске приложение:
  1. Вычисляет HMAC(MAC) и сравнивает с ключом в license.json
     → если не совпадает — license.json скопирован на чужую машину, стоп
  2. Скачивает licenses.json с GitHub, проверяет статус ключа
     → pending / blocked → стоп
     → active → запуск
```

**Ключ криптографически привязан к MAC-адресу.** Скопировать `license.json` на другую машину бесполезно — проверка не пройдёт.

---

## Структура файлов

```
register.py              — клиент запускает один раз для регистрации
license.json             — генерируется register.py, не трогать вручную
licenses.json            — твой файл на GitHub, список активных ключей
licensing/
├── checker.py           — проверка при запуске (обфусцируется)
├── generate.py          — управление licenses.json (только у тебя!)
└── __init__.py
update.sh                — обновление у клиента
Makefile                 — сборка релиза
```

---

## Перед первым релизом (один раз)

### 1. Убедись что _SECRET одинаковый в обоих файлах

```python
# register.py          — строка _SECRET
# licensing/checker.py — строка _SECRET
_SECRET = "одно-и-то-же-значение"
```

> Не меняй `_SECRET` после первого релиза — все выданные ключи перестанут работать.

### 2. Укажи URL до своего licenses.json

```python
# licensing/checker.py
_ONLINE_CHECK_URL = "https://raw.githubusercontent.com/prog-time/grex/main/licenses.json"
```

### 3. Создай пустой licenses.json в репозитории

```json
{}
```

### 4. Укажи репозиторий в update.sh

```bash
GITHUB_REPO="prog-time/grex"
```

---

## Продажа нового клиента

### Шаг 1. Клиент запускает регистрацию

```bash
python register.py
```

Выводит ключ вида:
```
  Ваш ключ активации:

      a3f9c2d1e8b4...7f2e1a0b9c3d
```

Клиент присылает тебе этот ключ.

### Шаг 2. Активируешь ключ

```bash
python licensing/generate.py activate \
  --key "a3f9c2d1e8b4...7f2e1a0b9c3d" \
  --customer "ООО Ромашка" \
  --updates-until "2027-03-21"
```

### Шаг 3. Публикуешь изменения

```bash
git add licenses.json
git commit -m "activate: ООО Ромашка"
git push
```

Через ~1 минуту (GitHub CDN) клиент может запускать `./start.sh`.

---

## Управление лицензиями

```bash
# Посмотреть все лицензии
python licensing/generate.py list

# Заблокировать клиента
python licensing/generate.py block --key "a3f9c2d1e8b4..."
git add licenses.json && git commit -m "block: ООО Ромашка" && git push

# Продлить подписку на обновления — повторная активация с новой датой
python licensing/generate.py activate \
  --key "a3f9c2d1e8b4..." \
  --customer "ООО Ромашка" \
  --updates-until "2028-03-21"
git add licenses.json && git commit -m "renew: ООО Ромашка" && git push
```

---

## Формат licenses.json

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
| `pending` / отсутствует | Приложение не запускается, просит прислать ключ |

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
2. Обфусцирует `crawlers/`, `services/`, `licensing/checker.py` через PyArmor
3. Копирует остальные файлы (`api.py`, `register.py`, `start.sh`, `update.sh` и др.)
4. `licensing/generate.py` **не попадает** в архив
5. Упаковывает в `release-v1.1.0.tar.gz`

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
| `licensing/generate.py` | **Не входит** | Только для разработчика |
| `api.py`, `config.py` | Как есть | Нужны для диагностики |
| `start.sh`, `update.sh` | Как есть | Должны быть читаемы |

---

## Разработка на своей машине

```bash
# Зарегистрировать свою машину
python register.py

# Добавить себя в licenses.json
python licensing/generate.py activate \
  --key "$(python -c "import json; print(json.load(open('license.json'))['key'])")" \
  --customer "Dev" \
  --updates-until "2030-01-01"

# Запустить (licenses.json должен быть закоммичен)
./start.sh
```

---

## Шпаргалка команд

```bash
# Лицензии
python licensing/generate.py list                    # список
python licensing/generate.py activate --help         # активация
python licensing/generate.py block --key "..."       # блокировка

# Сборка
make build                                           # обфусцировать → dist/
make release VERSION=1.2.0                          # собрать архив
make clean                                          # удалить dist/

# Публикация
gh release create v1.2.0 release-v1.2.0.tar.gz
```
