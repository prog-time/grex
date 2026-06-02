"""License checker — validates client API key against grex.iliya-code.ru at startup.

Flow:
  1. POST /activate {mac_address}  → 200 OK (idempotent — safe on every run)
  2. POST /config {mac_address}    → 200 OK → done
  3. Any 401/403 at activate       → abort
  4. 403 at config (blocked/expired) → abort
  5. Network error + cache         → warn and continue
  6. Network error, no cache       → abort
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from config import data_path, settings


def _licensing_cfg() -> dict:
    return settings.get("licensing", {})


def _cache_file_path() -> Path:
    raw: str = _licensing_cfg().get("cache_file", "data/license_cache.json")
    p = Path(raw)
    return p if p.is_absolute() else Path(data_path) / raw


def _read_cache() -> dict:
    path = _cache_file_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_license_file() -> dict:
    """Return cached license data (used by admin panel)."""
    return _read_cache()


def _save_cache(response_data: dict) -> None:
    from datetime import datetime, timezone
    path = _cache_file_path()
    payload = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "client_id": response_data.get("client_id", ""),
        "tokens": response_data.get("tokens", []),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"  Предупреждение: не удалось сохранить кеш лицензии: {exc}")


def _get_mac_address() -> str:
    """Return this machine's MAC address in AA:BB:CC:DD:EE:FF format."""
    mac_int = uuid.getnode()
    return ":".join(f"{(mac_int >> i) & 0xff:02x}" for i in range(40, -1, -8)).upper()


def _post_json(url: str, api_key: str, body: dict, timeout: int) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _read_error_body(exc: urllib.error.HTTPError) -> dict:
    try:
        return json.loads(exc.read().decode())
    except Exception:
        return {}


def _decode_token_source(token: str) -> str | None:
    """Extract the 'source' claim from a JWT payload without signature verification."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("source")
    except Exception:
        return None


def _activate_device(service_url: str, api_key: str, mac: str, timeout: int) -> None:
    """Call POST /activate. Idempotent — safe to call on every startup. Aborts on failure."""
    print("  Активация устройства...")
    try:
        result = _post_json(f"{service_url}/activate", api_key, {"mac_address": mac}, timeout)
        if result.get("activated"):
            print(f"  {result.get('message', 'Устройство активировано.')}")
        else:
            reason = result.get("reason", "неизвестная причина")
            _abort(f"Активация не удалась: {reason}")
    except urllib.error.HTTPError as exc:
        body = _read_error_body(exc)
        if exc.code == 401:
            _abort("Неверный API ключ при активации. Проверьте licensing.api_key в settings.yml")
        elif exc.code == 403:
            msg = body.get("message") or body.get("error", "")
            _abort(f"Активация запрещена: {msg or 'ключ заблокирован или недействителен'}")
        else:
            _abort(f"Ошибка активации: HTTP {exc.code}: {exc.reason}")
    except Exception:
        # Network error (service unreachable, DNS, timeout). Do NOT abort here —
        # re-raise so check_license() can fall back to the local cache per the
        # documented contract (step 5). Aborting here would crash startup even
        # when a valid cached license exists.
        raise


def _request_config(service_url: str, api_key: str, mac: str, timeout: int) -> dict:
    """Call POST /config and return response. Raises urllib errors as-is."""
    return _post_json(f"{service_url}/config", api_key, {"mac_address": mac}, timeout)


def check_license() -> dict:
    """Validate API key and activate device at startup. Calls SystemExit(1) on failure."""
    cfg = _licensing_cfg()
    service_url: str = cfg.get("service_url", "").rstrip("/")
    api_key: str = cfg.get("api_key", "")
    timeout: int = cfg.get("timeout_seconds", 10)

    if not service_url or not api_key or api_key == "your-client-api-key-here":
        _abort("API ключ не настроен. Укажите licensing.api_key в data/settings.yml")

    mac = _get_mac_address()

    # Step 1: activate (idempotent — safe on every run)
    try:
        _activate_device(service_url, api_key, mac, timeout)
    except SystemExit:
        raise
    except Exception as exc:
        _handle_network_error(str(exc))
        return {}

    # Step 2: fetch config
    try:
        data = _request_config(service_url, api_key, mac, timeout)
        _save_cache(data)
        sources = [s for s in (_decode_token_source(t) for t in data.get("tokens", [])) if s is not None]
        print(f"  Лицензия активна. Доступные источники: {', '.join(sources) if sources else '—'}")
        return data

    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            _abort("Неверный API ключ. Проверьте licensing.api_key в settings.yml")

        elif exc.code == 403:
            body = _read_error_body(exc)
            error_text = (body.get("error") or "").lower()

            if "blocked" in error_text:
                _abort("Устройство заблокировано (обнаружена смена устройства). Обратитесь к поставщику.")
            elif "expired" in error_text:
                _abort("Истёк срок подписки на обновления. Обратитесь к поставщику.")
            else:
                msg = body.get("error") or body.get("message", "")
                _abort(f"Доступ запрещён: {msg or 'недействительный или неактивный ключ'}")

        else:
            _handle_network_error(f"HTTP {exc.code}: {exc.reason}")

    except Exception as exc:
        _handle_network_error(str(exc))

    return {}


def _handle_network_error(reason: str) -> None:
    cache = _read_cache()
    if cache.get("tokens"):
        print(
            f"  Предупреждение: сервис лицензий недоступен ({reason}). "
            "Используется локальный кеш."
        )
    else:
        _abort(
            f"Не удалось проверить лицензию: {reason}. "
            "Локальный кеш отсутствует."
        )


def _abort(message: str) -> None:
    print(f"\n  ОШИБКА ЛИЦЕНЗИИ: {message}\n")
    raise SystemExit(1)
