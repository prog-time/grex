#!/usr/bin/env bash
set -e

# ── Цвета ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
info() { echo -e "${CYAN}  → $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }

echo ""
echo -e "${CYAN}  Grex Parser — Startup${NC}"
echo ""

# ── Директория скрипта ───────────────────────────────────────────────────────
cd "$(dirname "$0")"

# ── 1. Python ────────────────────────────────────────────────────────────────
info "Проверка Python..."

PYTHON=""
# py — the Python launcher on Windows.
for cmd in python3.12 python3 python py; do
    if command -v "$cmd" &>/dev/null; then
        # `|| true`: on Windows python3/python may be the Microsoft Store alias,
        # which prints an install hint instead of a version and exits non-zero —
        # without it `set -e` would end the script here with no message.
        VERSION=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1 || true)
        [ -z "$VERSION" ] && continue
        MAJOR=$(echo "$VERSION" | cut -d. -f1)
        MINOR=$(echo "$VERSION" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 12 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "         macOS:   brew install python@3.12"
    echo "         Windows: установите с python.org, отметив «Add python.exe to PATH»,"
    echo "                  и отключите псевдонимы python/python3 в «Параметры → Приложения →"
    echo "                  Дополнительные параметры приложений → Псевдонимы выполнения приложений»"
    fail "Python 3.12+ не найден"
fi
ok "Используется $($PYTHON --version)"

# ── 2. Виртуальное окружение ─────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    info "Создание виртуального окружения (.venv)..."
    $PYTHON -m venv .venv
    ok "Виртуальное окружение создано"
else
    ok "Виртуальное окружение уже существует"
fi

# ── Пути venv (Windows / Unix) ───────────────────────────────────────────────
if [ -f ".venv/Scripts/activate" ]; then
    VENV_ACTIVATE=".venv/Scripts/activate"
    VENV_PYTHON=".venv/Scripts/python"
    VENV_PIP=".venv/Scripts/pip"
    VENV_PATCHRIGHT=".venv/Scripts/patchright"
else
    VENV_ACTIVATE=".venv/bin/activate"
    VENV_PYTHON=".venv/bin/python"
    VENV_PIP=".venv/bin/pip"
    VENV_PATCHRIGHT=".venv/bin/patchright"
fi

source "$VENV_ACTIVATE"

# ── 3. Зависимости ───────────────────────────────────────────────────────────
info "Установка зависимостей из requirements.txt..."
"$VENV_PIP" install -r requirements.txt -q
ok "Зависимости установлены"

# ── 4. Patchright (браузер для Ozon / MegaMarket) ────────────────────────────
info "Проверка браузера Patchright..."
if "$VENV_PYTHON" -c "import patchright" &>/dev/null; then
    "$VENV_PATCHRIGHT" install chromium \
        && ok "Браузер Chromium готов" \
        || warn "Не удалось установить Chromium — парсинг Ozon/MegaMarket может не работать"
else
    warn "patchright не установлен — парсинг Ozon/MegaMarket недоступен"
fi

# ── 5. Директория данных ─────────────────────────────────────────────────────
info "Проверка директории data/..."
mkdir -p data
ok "data/ готова"

# settings.yml is not in git (it holds local secrets); a fresh clone has only
# the example, and the app cannot start without the real file.
if [ ! -f "data/settings.yml" ]; then
    if [ -f "data/settings.yml.example" ]; then
        cp data/settings.yml.example data/settings.yml
        ok "data/settings.yml создан из примера (data/settings.yml.example)"
        # The example's secret_key and admin/admin password are public. Replace
        # them now, while nothing is encrypted with the key yet: secret_key also
        # encrypts the saved Telegram bot token (services/secrets.py).
        ADMIN_PASSWORD=$("$VENV_PYTHON" - <<'PY'
import re
import secrets

path = "data/settings.yml"
text = open(path, encoding="utf-8").read()
password = secrets.token_urlsafe(12)
text = re.sub(r'^(\s+secret_key:\s*).*$', lambda m: f'{m.group(1)}"{secrets.token_urlsafe(32)}"', text, count=1, flags=re.M)
text = re.sub(r'^(\s+password:\s*).*$', lambda m: f'{m.group(1)}"{password}"', text, count=1, flags=re.M)
open(path, "w", encoding="utf-8").write(text)
print(password)
PY
)
        ok "Сгенерированы secret_key и пароль администратора"
        echo ""
        echo -e "${YELLOW}  Вход в панель: логин admin, пароль ${ADMIN_PASSWORD}${NC}"
        echo -e "${YELLOW}  Пароль сохранён в data/settings.yml (admin.password), сменить его можно в «Настройки → Пользователи».${NC}"
        echo ""
    else
        fail "Нет ни data/settings.yml, ни data/settings.yml.example — сервис не сможет запуститься"
    fi
elif grep -q 'change-me-in-production-secret-key' data/settings.yml; then
    # Not replaced automatically: a new key makes the saved Telegram bot token unreadable.
    warn "В data/settings.yml стандартный admin.secret_key — с ним можно войти в панель без пароля."
    echo "         Замените его на случайную строку и перезапустите сервис. После этого токен"
    echo "         Telegram-бота придётся заново ввести в «Настройки → Параметры мониторинга»."
fi

if [ ! -f "data/ozon_cookies.json" ]; then
    warn "data/ozon_cookies.json не найден — при первом парсинге Ozon откроется браузер для получения кук"
fi

if [ ! -f "data/mega_market_cookies.json" ]; then
    warn "data/mega_market_cookies.json не найден — при первом парсинге MegaMarket откроется браузер"
fi

if [ ! -f "data/ym_cookies.json" ]; then
    warn "data/ym_cookies.json не найден — при первом парсинге Яндекс Маркета откроется браузер, реши капчу вручную"
fi

# ── 6. Redis ──────────────────────────────────────────────────────────────────
info "Проверка Redis..."
if command -v redis-cli &>/dev/null && redis-cli ping &>/dev/null 2>&1; then
    ok "Redis запущен"
else
    warn "Redis недоступен — настройки источников (enable/rate-limit/fields) не сохранятся между перезапусками"
    echo "         macOS:  brew services start redis"
    echo "         Linux:  sudo systemctl start redis"
    echo "         Docker: docker run -d -p 6379:6379 redis:7-alpine"
fi

# ── 7. БД ─────────────────────────────────────────────────────────────────────
# data/admin.db (SQLite) создаётся автоматически SQLAlchemy при первом запуске.
# Таблицы: jobs (история парсинга), parse_targets (ссылки для парсинга).
ok "SQLite admin.db инициализируется автоматически при старте"

# ── 8. Запуск ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  API:      http://localhost:8004${NC}"
echo -e "${GREEN}  Swagger:  http://localhost:8004/docs${NC}"
echo -e "${GREEN}  Админка:  http://localhost:8004/admin${NC}"
echo -e "${GREEN}           /admin/sources  — вкл/выкл, rate limit, поля парсинга${NC}"
echo -e "${GREEN}           /admin/targets  — ссылки: Parse (одна) / Run all${NC}"
echo -e "${GREEN}           /admin/job      — история задач${NC}"
echo -e "${GREEN}  Стоп:     Ctrl+C${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

exec "$VENV_PYTHON" api.py
