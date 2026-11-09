"""Сквозные тесты: настоящий сервер + подставной Telegram API.

Запуск: pytest -q
"""
import hashlib
import hmac
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode

import pytest
import requests

TOKEN = "123456:TEST"
WEBHOOK = "hook-secret"
ADS = "ads-secret"
ROOT = pathlib.Path(__file__).parent.parent
calls = []


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeTelegram(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = self.path.rsplit("/", 1)[1]
        calls.append((method, body))
        result = "https://t.me/$invoice" if method == "createInvoiceLink" else True
        data = json.dumps({"ok": True, "result": result}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def signed(uid, name="Аня", token=TOKEN, start_param=None, auth_date=None):
    fields = {"auth_date": str(auth_date or int(time.time())),
              "user": json.dumps({"id": uid, "first_name": name}, ensure_ascii=False)}
    if start_param:
        fields["start_param"] = start_param
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    tg_port = free_port()
    fake = HTTPServer(("127.0.0.1", tg_port), FakeTelegram)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    port = free_port()
    env = {**os.environ, "BOT_TOKEN": TOKEN, "WEBHOOK_SECRET": WEBHOOK, "ADSGRAM_SECRET": ADS,
           "TG_API_BASE": f"http://127.0.0.1:{tg_port}", "PUBLIC_URL": f"http://127.0.0.1:{port}",
           "APP_LINK": "https://t.me/bot/app", "ADMIN_ID": "777",
           "SQLITE_PATH": str(tmp_path_factory.mktemp("db") / "t.db"), "DATABASE_URL": ""}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port),
                             "--log-level", "warning"], cwd=ROOT, env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if requests.get(url + "/health", timeout=1).ok:
                break
        except requests.RequestException:
            time.sleep(.2)
    yield url
    proc.terminate()
    fake.shutdown()


def post(base, path, uid, body=None, **kw):
    return requests.post(base + "/api" + path, json=body or {},
                         headers={"X-Init-Data": signed(uid, **kw)}, timeout=5)


def hook(base, update, secret=WEBHOOK):
    return requests.post(base + "/tg/webhook", json=update,
                         headers={"X-Telegram-Bot-Api-Secret-Token": secret}, timeout=5)


def pay(base, uid, pack, charge, stars):
    return hook(base, {"message": {"from": {"id": uid}, "chat": {"id": uid}, "successful_payment": {
        "invoice_payload": pack, "telegram_payment_charge_id": charge, "total_amount": stars,
        "currency": "XTR"}}})


def coins(base, uid):
    return post(base, "/me", uid).json()["profile"]["coins"]


def test_setup_registers_webhook(base):
    assert any(m == "setWebhook" and b["secret_token"] == WEBHOOK for m, b in calls)


def test_rejects_forged_expired_and_missing_auth(base):
    h = lambda d: requests.post(base + "/api/me", json={}, headers={"X-Init-Data": d}, timeout=5).status_code
    assert h(signed(1, token="999:OTHER")) == 401
    assert h(signed(1, auth_date=int(time.time()) - 90000)) == 401
    assert h("") == 401
    assert h(signed(1).replace("%D0%90", "%D0%91")) == 401
    assert post(base, "/me", 1).status_code == 200


def test_run_is_clamped_by_server_clock_and_single_use(base):
    run = post(base, "/run/start", 10).json()["runId"]
    r = post(base, "/run/finish", 10, {"runId": run, "kills": 99999, "seconds": 300, "dawn": True}).json()
    assert r["dawn"] is False
    assert r["coins"] <= 2 * 7 * 2 + 2
    assert post(base, "/me", 10).json()["profile"]["night"] == 1
    assert post(base, "/run/finish", 10, {"runId": run, "kills": 1, "seconds": 1}).status_code == 400


def test_cannot_finish_other_players_run(base):
    run = post(base, "/run/start", 11).json()["runId"]
    assert post(base, "/run/finish", 12, {"runId": run, "kills": 1, "seconds": 1}).status_code == 400


def test_upgrade_needs_coins_and_charges_once(base):
    assert post(base, "/upgrade", 20, {"id": "damage"}).status_code == 400
    pay(base, 20, "coins_s", "c-up", 25)
    r = post(base, "/upgrade", 20, {"id": "damage"}).json()["profile"]
    assert r["coins"] == 1400 and r["levels"]["damage"] == 1
    assert post(base, "/upgrade", 20, {"id": "nope"}).status_code == 400


def test_parallel_upgrades_never_overspend(base):
    pay(base, 21, "coins_s", "c-par", 25)  # хватает на несколько, но не на бесконечность
    threads = [threading.Thread(target=post, args=(base, "/upgrade", 21, {"id": "damage"})) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    p = post(base, "/me", 21).json()["profile"]
    spent = sum(int(100 * 1.55 ** i) for i in range(p["levels"]["damage"]))
    assert p["coins"] == 1500 - spent and p["coins"] >= 0


def test_payment_is_idempotent_and_refundable(base):
    assert pay(base, 30, "coins_m", "c-1", 100).ok
    pay(base, 30, "coins_m", "c-1", 100)
    assert coins(base, 30) == 8000
    hook(base, {"message": {"from": {"id": 30}, "chat": {"id": 30}, "refunded_payment": {
        "telegram_payment_charge_id": "c-1", "invoice_payload": "coins_m", "total_amount": 100, "currency": "XTR"}}})
    assert coins(base, 30) == 0


def test_vip_doubles_and_cannot_be_rebought(base):
    pay(base, 31, "vip", "c-vip", 199)
    assert post(base, "/me", 31).json()["profile"]["vip"] is True
    assert post(base, "/invoice", 31, {"pack": "vip"}).status_code == 400


def test_precheckout_validates_price(base):
    calls.clear()
    hook(base, {"pre_checkout_query": {"id": "1", "invoice_payload": "coins_s", "currency": "XTR", "total_amount": 25}})
    hook(base, {"pre_checkout_query": {"id": "2", "invoice_payload": "coins_s", "currency": "XTR", "total_amount": 1}})
    answers = {b["pre_checkout_query_id"]: b["ok"] for m, b in calls if m == "answerPreCheckoutQuery"}
    assert answers == {"1": True, "2": False}


def test_invoice_uses_stars(base):
    calls.clear()
    assert post(base, "/invoice", 32, {"pack": "coins_l"}).json()["link"]
    _, b = next(c for c in calls if c[0] == "createInvoiceLink")
    assert b["currency"] == "XTR" and b["provider_token"] == "" and b["prices"][0]["amount"] == 350


def test_webhook_rejects_wrong_secret(base):
    assert hook(base, {}, secret="bad").status_code == 403


def test_ad_flow(base):
    run = post(base, "/run/start", 40).json()["runId"]
    time.sleep(1.1)
    got = post(base, "/run/finish", 40, {"runId": run, "kills": 3, "seconds": 2}).json()["coins"]
    assert got > 0
    assert post(base, "/ad/claim", 40, {"runId": run}).status_code == 400  # просмотра не было
    assert requests.get(base + "/adsgram/reward", params={"userid": 40, "secret": "x"}, timeout=5).status_code == 403
    assert requests.get(base + "/adsgram/reward", params={"userid": 40, "secret": ADS}, timeout=5).ok
    assert post(base, "/ad/claim", 40, {"runId": run}).status_code == 200
    assert coins(base, 40) == got * 2
    requests.get(base + "/adsgram/reward", params={"userid": 40, "secret": ADS}, timeout=5)
    assert post(base, "/ad/claim", 40, {"runId": run}).status_code == 400  # второй раз нельзя


def test_daily_once(base):
    assert post(base, "/daily", 50).json()["reward"] == 100
    assert post(base, "/daily", 50).status_code == 400


def test_self_referral_ignored(base):
    post(base, "/me", 60, start_param="r60")
    run = post(base, "/run/start", 60).json()["runId"]
    post(base, "/run/finish", 60, {"runId": run, "kills": 0, "seconds": 0})
    assert post(base, "/me", 60).json()["profile"]["friends"] == 0


def test_stats_only_for_admin(base):
    calls.clear()
    hook(base, {"message": {"from": {"id": 1}, "chat": {"id": 1}, "text": "/stats"}})
    assert not [c for c in calls if c[0] == "sendMessage"]
    hook(base, {"message": {"from": {"id": 777}, "chat": {"id": 777}, "text": "/stats"}})
    assert "Звёзд всего" in next(b for m, b in calls if m == "sendMessage")["text"]


def test_page_served(base):
    assert "Дожить" in requests.get(base + "/", timeout=5).text
