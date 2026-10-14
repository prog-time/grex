.PHONY: run stop restart help lint-yaml lock hooks

PORT ?= 8004

help:
	@echo ""
	@echo "  make run                  — запустить локально (через start.sh)"
	@echo "  make stop                 — остановить процесс на порту $(PORT)"
	@echo "  make restart              — перезапустить (stop + run)"
	@echo "  make lint-yaml            — проверить YAML (yamllint)"
	@echo "  make lock                 — пересобрать requirements-ci.txt"
	@echo "  make hooks                — подключить git-хуки из linting/hooks"
	@echo ""

# ── Запуск ────────────────────────────────────────────────────────────────────
run:
	@bash start.sh

# ── Остановка ─────────────────────────────────────────────────────────────────
stop:
	@PID=$$(lsof -ti tcp:$(PORT) -sTCP:LISTEN 2>/dev/null); \
	if [ -z "$$PID" ]; then \
		echo "  ✓ На порту $(PORT) ничего не запущено"; \
	else \
		echo "  → Остановка процесса на порту $(PORT) (PID $$PID)"; \
		kill $$PID 2>/dev/null || true; \
		for i in $$(seq 1 10); do \
			lsof -ti tcp:$(PORT) -sTCP:LISTEN >/dev/null 2>&1 || break; \
			sleep 0.5; \
		done; \
		if lsof -ti tcp:$(PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
			echo "  → Не завершился — принудительно (SIGKILL)"; \
			kill -9 $$PID 2>/dev/null || true; \
		fi; \
		echo "  ✓ Остановлено"; \
	fi

# ── Перезапуск ────────────────────────────────────────────────────────────────
restart: stop run

# ── CI ────────────────────────────────────────────────────────────────────────
lint-yaml:
	@yamllint -c .yamllint.yml .github/ .yamllint.yml

lock:
	@.venv/bin/python scripts/lock_requirements.py

hooks:
	@git config core.hooksPath linting/hooks
	@echo "  ✓ git-хуки подключены: linting/hooks (pre-commit, pre-push, prepare-commit-msg)"
