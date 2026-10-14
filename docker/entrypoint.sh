#!/bin/sh
# Container entrypoint: prepares data/settings.yml on first start, then runs the app.
set -e

cd /app
mkdir -p data

if [ ! -f data/settings.yml ]; then
    cp docker/settings.yml.example data/settings.yml
    # Inside a container the app must listen on all interfaces, Redis is the
    # compose service, and the example's secret_key and admin/admin are public.
    ADMIN_PASSWORD=$(REDIS_HOST="${REDIS_HOST:-redis}" python - <<'PY'
import os
import re
import secrets

path = "data/settings.yml"
text = open(path, encoding="utf-8").read()
password = secrets.token_urlsafe(12)
text = re.sub(r'^(app:\n\s+host:\s*).*$', r'\1"0.0.0.0"', text, count=1, flags=re.M)
text = re.sub(r'^(redis:\n\s+host:\s*).*$', lambda m: f'{m.group(1)}"{os.environ["REDIS_HOST"]}"', text, count=1, flags=re.M)
text = re.sub(r'^(\s+secret_key:\s*).*$', lambda m: f'{m.group(1)}"{secrets.token_urlsafe(32)}"', text, count=1, flags=re.M)
text = re.sub(r'^(\s+password:\s*).*$', lambda m: f'{m.group(1)}"{password}"', text, count=1, flags=re.M)
open(path, "w", encoding="utf-8").write(text)
print(password)
PY
)
    echo "data/settings.yml created. Admin panel login: admin, password: ${ADMIN_PASSWORD}"
    echo "The password is stored in data/settings.yml (admin.password)."
fi

exec xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24" python api.py
