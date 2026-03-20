.PHONY: run build release clean help

PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
DIST   := dist

# Версия берётся из data/settings.yml если не передана явно
VERSION ?= $(shell grep -E '^\s*soft_version' data/settings.yml 2>/dev/null | awk '{print $$2}')

help:
	@echo ""
	@echo "  make run                  — запустить локально (через start.sh)"
	@echo "  make build                — обфусцировать проект в $(DIST)/"
	@echo "  make release VERSION=1.2  — собрать и упаковать архив для GitHub Release"
	@echo "  make clean                — удалить $(DIST)/"
	@echo ""

# ── Запуск ────────────────────────────────────────────────────────────────────
run:
	@bash start.sh

# ── Обфускация в dist/ ────────────────────────────────────────────────────────
build:
	@echo ""
	@echo "  → Очистка $(DIST)/"
	@rm -rf $(DIST)

	@echo "  → Обфускация: crawlers/ services/ licensing/checker.py"
	@$(PYTHON) -m pyarmor gen -O $(DIST) \
		crawlers/ \
		services/ \
		licensing/checker.py

	@echo "  → Копирование необфусцированных файлов"
	@cp api.py config.py requirements.txt start.sh update.sh $(DIST)/
	@cp -r models/ core/ utils/ admin/ $(DIST)/

	@# licensing/__init__.py и generate.py — отдельно
	@cp licensing/__init__.py $(DIST)/licensing/__init__.py
	@# generate.py НЕ копируется — он только для разработчика

	@echo "  → Копирование шаблона конфига"
	@mkdir -p $(DIST)/data
	@[ -f data/settings.yml ] && cp data/settings.yml $(DIST)/data/ || true

	@echo ""
	@echo "  ✓ Сборка готова → $(DIST)/"
	@echo ""

# ── Сборка архива для GitHub Release ─────────────────────────────────────────
release: build
	@if [ -z "$(VERSION)" ]; then \
		echo "  ✗ Укажите версию: make release VERSION=1.2.0"; exit 1; \
	fi
	@echo "  → Упаковка release-v$(VERSION).tar.gz"
	@tar -czf release-v$(VERSION).tar.gz -C $(DIST) .
	@echo ""
	@echo "  ✓ Готово: release-v$(VERSION).tar.gz"
	@echo "  Загрузите на GitHub: gh release create v$(VERSION) release-v$(VERSION).tar.gz"
	@echo ""

# ── Очистка ───────────────────────────────────────────────────────────────────
clean:
	@rm -rf $(DIST) release-*.tar.gz
	@echo "  ✓ $(DIST)/ удалён"
