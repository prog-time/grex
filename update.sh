#!/usr/bin/env bash
set -e

# ── Настройки ─────────────────────────────────────────────────────────────────
GITHUB_REPO="prog-time/grex"   # <-- замените на ваш GitHub репо
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
TMP_DIR="$(mktemp -d)"

# ── Цвета ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
info() { echo -e "${CYAN}  → $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║      Marketplace Parser — Updater        ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

cd "$INSTALL_DIR"

# ── 1. Проверка license.json ──────────────────────────────────────────────────
info "Проверка лицензии..."

if [ ! -f "license.json" ]; then
    fail "Файл license.json не найден. Обратитесь к поставщику."
fi

# Проверяем подпись и срок через checker.py
.venv/bin/python -c "from licensing import check_license; check_license()" \
    || exit 1

ok "Лицензия действительна"

# ── 2. Проверка подписки на обновления ───────────────────────────────────────
info "Проверка подписки на обновления..."

UPDATES_UNTIL=$(.venv/bin/python -c "
import json, sys
try:
    lic = json.load(open('license.json'))
    print(lic.get('updates_until', ''))
except Exception as e:
    print('')
" 2>/dev/null)

if [ -z "$UPDATES_UNTIL" ]; then
    fail "Поле updates_until отсутствует в license.json. Обратитесь к поставщику."
fi

TODAY=$(date +%Y-%m-%d)
if [[ "$TODAY" > "$UPDATES_UNTIL" ]]; then
    fail "Подписка на обновления истекла $UPDATES_UNTIL. Продлите подписку для получения обновлений."
fi

ok "Подписка на обновления действительна до $UPDATES_UNTIL"

# ── 3. Текущая версия ─────────────────────────────────────────────────────────
CURRENT_VERSION="unknown"
if [ -f "data/settings.yml" ]; then
    CURRENT_VERSION=$(grep -E '^\s*soft_version' data/settings.yml 2>/dev/null | awk '{print $2}' || echo "unknown")
fi
info "Текущая версия: $CURRENT_VERSION"

# ── 4. Получение последнего релиза с GitHub ───────────────────────────────────
info "Получение информации о последнем релизе..."

RELEASE_INFO=$(curl -sf "https://api.github.com/repos/${GITHUB_REPO}/releases/latest") \
    || fail "Не удалось получить данные о релизе. Проверьте интернет-соединение."

LATEST_VERSION=$(echo "$RELEASE_INFO" | python3 -c "
import sys, json
print(json.load(sys.stdin)['tag_name'])
")

DOWNLOAD_URL=$(echo "$RELEASE_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for a in data.get('assets', []):
    if a['name'].endswith('.tar.gz'):
        print(a['browser_download_url'])
        break
")

if [ -z "$LATEST_VERSION" ]; then
    fail "Не удалось определить версию релиза."
fi

if [ -z "$DOWNLOAD_URL" ]; then
    fail "Архив .tar.gz не найден в релизе $LATEST_VERSION."
fi

info "Последняя версия: $LATEST_VERSION"

if [ "$CURRENT_VERSION" = "$LATEST_VERSION" ]; then
    ok "У вас уже установлена последняя версия ($LATEST_VERSION)"
    exit 0
fi

echo ""
echo -e "  Обновление: ${YELLOW}$CURRENT_VERSION${NC} → ${GREEN}$LATEST_VERSION${NC}"
read -r -p "  Продолжить? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    info "Обновление отменено"
    exit 0
fi

# ── 5. Бэкап текущей версии ───────────────────────────────────────────────────
info "Создание бэкапа..."
BACKUP_DIR="backups/backup_${CURRENT_VERSION}_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
for item in api.py config.py requirements.txt crawlers/ services/ models/ core/ utils/ admin/ licensing/; do
    [ -e "$item" ] && cp -r "$item" "$BACKUP_DIR/"
done
ok "Бэкап сохранён → $BACKUP_DIR"

# ── 6. Скачивание ─────────────────────────────────────────────────────────────
info "Скачивание $LATEST_VERSION..."
curl -L "$DOWNLOAD_URL" -o "$TMP_DIR/release.tar.gz" --progress-bar \
    || fail "Ошибка загрузки архива."
ok "Архив загружен"

# ── 7. Распаковка ─────────────────────────────────────────────────────────────
info "Распаковка..."
tar -xzf "$TMP_DIR/release.tar.gz" -C "$TMP_DIR" --strip-components=1 \
    || fail "Ошибка распаковки архива."
ok "Распаковано"

# ── 8. Применение ─────────────────────────────────────────────────────────────
info "Применение обновления..."

# Не трогаем: данные пользователя, .venv, лицензию, логи, бэкапы
if command -v rsync &>/dev/null; then
    rsync -a \
        --exclude="data/" \
        --exclude=".venv/" \
        --exclude="license.json" \
        --exclude="logs/" \
        --exclude="backups/" \
        "$TMP_DIR/" "$INSTALL_DIR/"
else
    # Fallback без rsync
    for item in api.py config.py requirements.txt crawlers/ services/ models/ core/ utils/ admin/ licensing/; do
        [ -e "$TMP_DIR/$item" ] && cp -r "$TMP_DIR/$item" "$INSTALL_DIR/"
    done
fi

ok "Файлы обновлены"

# ── 9. Обновление зависимостей ────────────────────────────────────────────────
info "Обновление зависимостей..."
.venv/bin/pip install -r requirements.txt -q
ok "Зависимости обновлены"

# ── Готово ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Обновление завершено: $LATEST_VERSION${NC}"
echo -e "${GREEN}  Перезапустите сервер: ./start.sh${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
