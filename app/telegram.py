"""Проверка initData и вызовы Bot API."""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qsl

API_BASE = os.environ.get("TG_API_BASE", "https://api.telegram.org")
INIT_DATA_MAX_AGE = 24 * 3600


def bot_token() -> str:
    return os.environ["BOT_TOKEN"]


def verify_init_data(init_data: str, token: str | None = None):
    """Возвращает пользователя, если подпись Telegram верна, иначе None."""
    if not init_data:
        return None
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = fields.pop("hash", "")
    if not received:
        return None
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", (token or bot_token()).encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        if time.time() - int(fields.get("auth_date", 0)) > INIT_DATA_MAX_AGE:
            return None
        user = json.loads(fields["user"])
        user["id"] = int(user["id"])
    except (KeyError, ValueError, TypeError):
        return None
    user["start_param"] = fields.get("start_param", "")
    return user


def call(method: str, **params):
    url = f"{API_BASE}/bot{bot_token()}/{method}"
    request = urllib.request.Request(
        url,
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read()).get("result")
    except urllib.error.HTTPError as error:
        # В логах только метод и ответ Telegram: токен есть в URL, его не печатаем.
        detail = error.read().decode(errors="replace")[:300]
        raise RuntimeError(f"Telegram {method} failed: {error.code} {detail}") from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"Telegram {method} unreachable: {error.reason}") from None
