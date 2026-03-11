**Установка**:

- Активируем виртуальное окружение
- pip install -r requirements.txt
- patchright install
- python api.py

Настройки в data/settings.yml

Прокси указать в файле data/proxy.txt в формате:

`username:password@server:port`

Тип прокси указывать в настройках, а НЕ в файле proxy.txt.

Документация API будет доступна по эндпоинту /docs после старта (доступна только в дебаг-режиме).