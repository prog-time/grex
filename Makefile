.PHONY: run stop restart help

PORT ?= 8004

help:
	@echo ""
	@echo "  make run                  — запустить локально (через start.sh)"
	@echo "  make stop                 — остановить процесс на порту $(PORT)"
	@echo "  make restart              — перезапустить (stop + run)"
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
