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
for cmd in python3.12 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VERSION=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        MAJOR=$(echo "$VERSION" | cut -d. -f1)
        MINOR=$(echo "$VERSION" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 12 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python 3.12+ не найден. Установите: brew install python@3.12"
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

if [ ! -f "data/settings.yml" ]; then
    warn "data/settings.yml не найден — используются значения по умолчанию"
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
