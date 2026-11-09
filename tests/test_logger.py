"""Structured JSON logs, redaction and stdlib interception (ADR 0010)."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from loguru import logger

import utils.logger as log_mod

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_FIELDS = {"ts", "level", "service", "version", "env", "msg", "logger"}


@pytest.fixture
def captured():
    """Configure the logger once and capture the JSON and the plain-text output."""
    log_mod.setup_logger()
    json_raw: list[str] = []
    text: list[str] = []
    ids = [
        logger.add(json_raw.append, format=log_mod._json_format, level="DEBUG"),
        logger.add(text.append, format=log_mod._plain_format, level="DEBUG"),
    ]
    yield (lambda: [json.loads(line) for line in json_raw]), text
    for sink_id in ids:
        logger.remove(sink_id)


@pytest.fixture
def json_lines(captured):
    return captured[0]


@pytest.fixture
def text_lines(captured):
    return captured[1]


def test_tests_never_write_to_real_logs_dir():
    assert Path(log_mod.log_dir()).resolve() != (PROJECT_ROOT / "logs").resolve()


def test_exception_record_is_valid_json_with_required_fields(json_lines):
    try:
        raise ValueError("bad price")
    except ValueError:
        logger.exception("Parsing error for https://ozon.ru/product/1")

    (doc,) = json_lines()
    assert REQUIRED_FIELDS <= doc.keys()
    assert doc["service"] == "grex"
    assert doc["level"] == "error"
    assert doc["ts"].endswith("Z")
    assert doc["logger"] == __name__
    assert doc["error"]["type"] == "ValueError"
    assert doc["error"]["message"] == "bad price"
    assert "Traceback" in doc["error"]["stack"]


def test_success_maps_to_info_and_levels_are_lowercase(json_lines):
    logger.success("parsed")
    logger.warning("slow")
    assert [d["level"] for d in json_lines()] == ["info", "warning"]


def test_context_fields_are_top_level(json_lines):
    with logger.contextualize(request_id="abc", marketplace="ozon", url="https://ozon.ru/p/1"):
        logger.info("Starting parse")

    (doc,) = json_lines()
    assert doc["request_id"] == "abc"
    assert doc["marketplace"] == "ozon"
    assert doc["url"] == "https://ozon.ru/p/1"
    assert "error" not in doc


def test_context_cannot_overwrite_required_fields(json_lines):
    with logger.contextualize(level="fake", service="other"):
        logger.info("x")
    (doc,) = json_lines()
    assert doc["level"] == "info"
    assert doc["service"] == "grex"


@pytest.mark.parametrize(
    "message, secrets",
    [
        ("Using proxy: http://api27a9e86b918c_c_RU:s3cretPass@gate.example.com:10945", ["api27a9e86b918c_c_RU", "s3cretPass"]),
        ("Using proxy: socks5h://login:***@gate.example.com:10945", ["login"]),
        ("bad proxy line user42:pw42xyz@10.0.0.1:8000", ["user42", "pw42xyz"]),
        ("POST https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage",
         ["AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"]),
        ("GET https://card.wb.ru/x?x_wbaas_token=eyJhbGciOiJIUzI1NiJ9abc&nm=1", ["eyJhbGciOiJIUzI1NiJ9abc"]),
        ("headers: {'X-API-Key': 'grex_live_0123456789abcdef'}", ["grex_live_0123456789abcdef"]),
        ("Authorization: Bearer abcdefghijklmnop1234", ["abcdefghijklmnop1234"]),
        ("Cookie: session=deadbeef42; _wbauid=777888999", ["deadbeef42", "777888999"]),
        ('{"password": "hunter2hunter2"}', ["hunter2hunter2"]),
    ],
)
def test_secrets_are_redacted_in_every_format(json_lines, text_lines, message, secrets):
    logger.info(message)
    (doc,) = json_lines()
    for secret in secrets:
        assert secret not in doc["msg"]
        assert secret not in text_lines[0]
    assert "***" in doc["msg"]


def test_secrets_are_redacted_in_exception_and_context(json_lines, text_lines):
    with logger.contextualize(url="http://user:pa55word@proxy.local:8080"):
        try:
            raise RuntimeError("connect failed via http://user:pa55word@proxy.local:8080")
        except RuntimeError:
            logger.exception("proxy error")

    (doc,) = json_lines()
    blob = json.dumps(doc) + text_lines[0]
    assert "pa55word" not in blob
    assert "user:" not in blob


def test_known_secret_values_from_settings_are_redacted(json_lines, monkeypatch):
    monkeypatch.setattr(log_mod, "_known_secrets", ("Sup3r-Secret-Value",))
    logger.info("loaded key Sup3r-Secret-Value from config")
    (doc,) = json_lines()
    assert "Sup3r-Secret-Value" not in doc["msg"]


def test_plain_words_are_not_redacted(json_lines):
    msg = "WB internal API HTTP 498: token expired, refreshing; Cookies saved successfully"
    logger.info(msg)
    (doc,) = json_lines()
    assert doc["msg"] == msg


def test_stdlib_logging_is_intercepted(json_lines):
    logging.getLogger("uvicorn.error").info("Uvicorn running on http://localhost:8004")
    logging.getLogger("httpx").warning("HTTP Request: GET https://u:p4ss@x.ru:1/ 503")

    docs = json_lines()
    assert [(d["logger"], d["level"]) for d in docs] == [("uvicorn.error", "info"), ("httpx", "warning")]
    assert "p4ss" not in docs[1]["msg"]


def test_json_file_sink_writes_one_json_per_line(tmp_path, monkeypatch):
    monkeypatch.setenv("GREX_LOG_DIR", str(tmp_path))
    monkeypatch.setitem(log_mod.settings, "debug", {"active": True, "logging_in_file": True})
    log_mod.setup_logger()
    try:
        logger.info("first")
        try:
            raise KeyError("sku")
        except KeyError:
            logger.exception("second")
        logger.complete()
    finally:
        log_mod.setup_logger()  # back to the session configuration

    lines = (tmp_path / "app.jsonl").read_text().splitlines()
    docs = [json.loads(line) for line in lines]
    assert [d["msg"] for d in docs] == ["first", "second"]
    assert all(REQUIRED_FIELDS <= d.keys() for d in docs)
    assert docs[1]["error"]["type"] == "KeyError"
    assert list(tmp_path.glob("*.log")), "text log file is still written"


def test_child_setup_has_no_sse_sink(monkeypatch):
    calls: list[object] = []
    real_add = logger.add

    def spy_add(sink, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(sink)
        return real_add(sink, *args, **kwargs)

    monkeypatch.setattr(logger, "add", spy_add)
    log_mod.setup_logger(child=True)
    monkeypatch.undo()
    log_mod.setup_logger()
    assert log_mod.sse_sink not in calls


def test_env_comes_from_environment(json_lines, monkeypatch):
    monkeypatch.setenv("GREX_ENV", "staging")
    logger.info("x")
    assert json_lines()[0]["env"] == "staging"
    assert os.environ["GREX_ENV"] == "staging"


# ---------------------------------------------------------------------------
# Browser subprocess (ADR 0009): logger setup and context hand-over
# ---------------------------------------------------------------------------

def test_current_log_context_is_captured_for_subprocess():
    from crawlers.base import _current_log_context

    with logger.contextualize(url="https://ozon.ru/p/1", marketplace="ozon", request_id="r1"):
        ctx = _current_log_context()
    assert ctx == {"url": "https://ozon.ru/p/1", "marketplace": "ozon", "request_id": "r1"}
    assert _current_log_context() == {}


@pytest.mark.parametrize("configure", [True, False])
def test_child_main_configures_logger_only_for_spawn(json_lines, monkeypatch, configure):
    import crawlers.base as base

    calls: list[bool] = []
    monkeypatch.setattr(log_mod, "setup_logger", lambda child=False: calls.append(child))

    def target(value):  # type: ignore[no-untyped-def]
        logger.info(f"child got {value}")

    base._child_main(target, configure, {"url": "https://wb.ru/1", "marketplace": "wildberries"}, 42)

    assert calls == ([True] if configure else [])
    (doc,) = json_lines()
    assert doc["msg"] == "child got 42"
    assert doc["url"] == "https://wb.ru/1"
    assert doc["marketplace"] == "wildberries"
