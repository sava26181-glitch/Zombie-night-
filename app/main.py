import hmac
import json
import logging
import os
import pathlib
import secrets
import time
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import db, game, telegram

log = logging.getLogger("night")
STATIC = pathlib.Path(__file__).parent.parent / "static"

PUBLIC_URL = os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
ADSGRAM_SECRET = os.environ.get("ADSGRAM_SECRET", "")
ADSGRAM_BLOCK_ID = os.environ.get("ADSGRAM_BLOCK_ID", "")
APP_LINK = os.environ.get("APP_LINK", "")  # https://t.me/имя_бота/имя_приложения
ADMIN_ID = int(os.environ.get("ADMIN_ID") or 0)
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "администратору бота")


class GameError(Exception):
    pass


def now() -> int:
    return int(time.time())


def today() -> int:
    return now() // 86400


# ---------- Игрок ----------

def ensure_user(t: db.Tx, user: dict) -> dict:
    uid, name = user["id"], str(user.get("first_name", ""))[:24]
    referrer = None
    param = user.get("start_param", "")
    if param.startswith("r") and param[1:].isdigit() and len(param) <= 16 and int(param[1:]) != uid:
        candidate = int(param[1:])
        if t.one("SELECT id FROM users WHERE id=?", (candidate,)):
            referrer = candidate
    t.run(
        "INSERT INTO users (id, name, referrer, created, seen) VALUES (?,?,?,?,?) "
        "ON CONFLICT (id) DO UPDATE SET seen=excluded.seen, "
        "name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE users.name END",
        (uid, name, referrer, now(), now()),
    )
    return t.one("SELECT * FROM users WHERE id=?", (uid,))


def profile(row: dict) -> dict:
    levels = json.loads(row["upgrades"])
    upgrades = []
    for key, info in game.UPGRADES.items():
        level = levels.get(key, 0)
        upgrades.append({
            "id": key, "name": info["name"], "desc": info["desc"], "level": level,
            "max": game.MAX_LEVEL,
            "cost": game.upgrade_cost(key, level) if level < game.MAX_LEVEL else None,
        })
    return {
        "id": row["id"], "name": row["name"], "coins": row["coins"], "best": row["best"],
        "night": row["night"], "vip": bool(row["vip"]), "friends": row["friends"],
        "streak": row["streak"], "dailyReady": row["daily_day"] < today(),
        "levels": levels, "upgrades": upgrades,
    }


def public_config(row: dict) -> dict:
    invite = f"{APP_LINK}?startapp=r{row['id']}" if APP_LINK else ""
    packs = [{"id": k, **v} for k, v in game.PACKS.items()]
    return {"packs": packs, "adsBlock": ADSGRAM_BLOCK_ID, "invite": invite,
            "dawn": game.DAWN_SECONDS, "refBonus": game.REFERRAL_BONUS}


def me(user, _body):
    with db.tx() as t:
        row = ensure_user(t, user)
    return {"profile": profile(row), "config": public_config(row)}


def daily(user, _body):
    with db.tx() as t:
        row = ensure_user(t, user)
        day = today()
        streak = row["streak"] + 1 if row["daily_day"] == day - 1 else 1
        reward = 100 * min(streak, 7)
        done = t.run(
            "UPDATE users SET daily_day=?, streak=?, coins=coins+? WHERE id=? AND daily_day<?",
            (day, streak, reward, user["id"], day),
        ).rowcount
        if not done:
            raise GameError("Награда уже получена сегодня")
        row = t.one("SELECT * FROM users WHERE id=?", (user["id"],))
    return {"reward": reward, "profile": profile(row)}


def upgrade(user, body):
    key = str(body.get("id", ""))
    if key not in game.UPGRADES:
        raise GameError("Неизвестное улучшение")
    with db.tx() as t:
        row = ensure_user(t, user)
        levels = json.loads(row["upgrades"])
        level = levels.get(key, 0)
        if level >= game.MAX_LEVEL:
            raise GameError("Максимальный уровень")
        cost = game.upgrade_cost(key, level)
        levels[key] = level + 1
        # Условие upgrades=? не даёт двум одновременным запросам списать монеты дважды.
        done = t.run(
            "UPDATE users SET coins=coins-?, upgrades=? WHERE id=? AND coins>=? AND upgrades=?",
            (cost, json.dumps(levels), user["id"], cost, row["upgrades"]),
        ).rowcount
        if not done:
            raise GameError("Не хватает монет")
        row = t.one("SELECT * FROM users WHERE id=?", (user["id"],))
    return {"profile": profile(row)}


# ---------- Забег ----------

def run_start(user, _body):
    run_id = secrets.token_urlsafe(16)
    with db.tx() as t:
        ensure_user(t, user)
        t.run("INSERT INTO runs (id, user_id, started) VALUES (?,?,?)",
              (run_id, user["id"], now()))
    return {"runId": run_id}


def run_finish(user, body):
    run_id = str(body.get("runId", ""))
    claimed_kills = max(0, int(body.get("kills", 0)))
    claimed_seconds = max(0, int(body.get("seconds", 0)))
    with db.tx() as t:
        row = ensure_user(t, user)
        run = t.one("SELECT * FROM runs WHERE id=? AND user_id=?", (run_id, user["id"]))
        if not run:
            raise GameError("Забег не найден")
        if not t.run("UPDATE runs SET finished=1 WHERE id=? AND finished=0", (run_id,)).rowcount:
            raise GameError("Забег уже завершён")
        # Время берём по часам сервера: клиент не может заявить больше, чем прошло.
        seconds = min(claimed_seconds, now() - run["started"] + 2, game.DAWN_SECONDS)
        kills = min(claimed_kills, seconds * game.MAX_KILLS_PER_SECOND)
        dawn = seconds >= game.DAWN_SECONDS - 1 and bool(body.get("dawn"))
        reward = game.run_reward(kills, seconds, row["night"], bool(row["vip"]), dawn)
        t.run("UPDATE runs SET coins=? WHERE id=?", (reward["coins"], run_id))
        t.run(
            "UPDATE users SET coins=coins+?, runs=runs+1, night=night+?, "
            "best=CASE WHEN best<? THEN ? ELSE best END WHERE id=?",
            (reward["coins"], 1 if dawn else 0, reward["score"], reward["score"], user["id"]),
        )
        if seconds >= 30 and row["referrer"] and t.run(
            "UPDATE users SET ref_paid=1 WHERE id=? AND ref_paid=0", (user["id"],)
        ).rowcount:
            bonus = game.REFERRAL_BONUS
            t.run("UPDATE users SET coins=coins+? WHERE id=?", (bonus, user["id"]))
            t.run("UPDATE users SET coins=coins+?, friends=friends+1 WHERE id=?",
                  (bonus, row["referrer"]))
        row = t.one("SELECT * FROM users WHERE id=?", (user["id"],))
    return {"coins": reward["coins"], "score": reward["score"], "dawn": dawn,
            "profile": profile(row)}


def leaderboard(user, _body):
    with db.tx() as t:
        row = ensure_user(t, user)
        top = t.all("SELECT id, name, best, night FROM users WHERE best>0 "
                    "ORDER BY best DESC LIMIT 50")
        rank = t.one("SELECT COUNT(*) AS n FROM users WHERE best>?", (row["best"],))["n"] + 1
    return {"top": top, "rank": rank, "me": user["id"]}


# ---------- Реклама ----------

def ad_claim(user, body):
    """Удваивает монеты забега после просмотра, подтверждённого сервером Adsgram."""
    run_id = str(body.get("runId", ""))
    with db.tx() as t:
        run = t.one("SELECT * FROM runs WHERE id=? AND user_id=? AND finished=1",
                    (run_id, user["id"]))
        if not run or run["doubled"]:
            raise GameError("Награда недоступна")
        if not t.run("UPDATE users SET ad_credits=ad_credits-1 WHERE id=? AND ad_credits>0",
                     (user["id"],)).rowcount:
            raise GameError("Просмотр ещё не подтверждён")
        if not t.run("UPDATE runs SET doubled=1 WHERE id=? AND doubled=0", (run_id,)).rowcount:
            raise GameError("Награда уже получена")
        t.run("UPDATE users SET coins=coins+? WHERE id=?", (run["coins"], user["id"]))
        row = t.one("SELECT * FROM users WHERE id=?", (user["id"],))
    return {"profile": profile(row)}


async def adsgram_reward(request):
    secret = request.query_params.get("secret", "")
    if not ADSGRAM_SECRET or not hmac.compare_digest(secret, ADSGRAM_SECRET):
        return PlainTextResponse("forbidden", status_code=403)
    try:
        uid = int(request.query_params.get("userid", ""))
    except ValueError:
        return PlainTextResponse("bad user", status_code=400)

    def credit():
        day = today()
        with db.tx() as t:
            t.run("UPDATE users SET ad_day=?, ad_today=0 WHERE id=? AND ad_day<?", (day, uid, day))
            t.run(
                "UPDATE users SET ad_today=ad_today+1, ad_credits=ad_credits+1 "
                "WHERE id=? AND ad_today<? AND ad_credits<?",
                (uid, game.AD_DAILY_CAP, game.AD_CREDITS_CAP),
            )

    await run_in_threadpool(credit)
    return PlainTextResponse("ok")


# ---------- Покупки ----------

def invoice(user, body):
    pack_id = str(body.get("pack", ""))
    pack = game.PACKS.get(pack_id)
    if not pack:
        raise GameError("Нет такого набора")
    if pack.get("vip"):
        with db.tx() as t:
            if ensure_user(t, user)["vip"]:
                raise GameError("VIP уже куплен")
    link = telegram.call(
        "createInvoiceLink",
        title=pack["title"],
        description=f"Игровой набор: {pack['coins']} монет" + (", двойные монеты навсегда" if pack.get("vip") else ""),
        payload=pack_id,
        provider_token="",
        currency="XTR",
        prices=[{"label": pack["title"], "amount": pack["stars"]}],
    )
    return {"link": link}


def handle_update(update: dict):
    if query := update.get("pre_checkout_query"):
        pack = game.PACKS.get(query.get("invoice_payload", ""))
        valid = bool(pack) and query.get("currency") == "XTR" and query.get("total_amount") == pack["stars"]
        args = {"pre_checkout_query_id": query["id"], "ok": valid}
        if not valid:
            args["error_message"] = "Набор устарел. Откройте магазин ещё раз."
        telegram.call("answerPreCheckoutQuery", **args)
        return
    message = update.get("message") or {}
    if payment := message.get("successful_payment"):
        apply_payment(message["from"]["id"], payment)
    elif refund := message.get("refunded_payment"):
        apply_refund(refund)
    elif (text := message.get("text", "")).startswith("/"):
        handle_command(message["chat"]["id"], message.get("from", {}).get("id"), text)


def apply_payment(uid: int, payment: dict):
    pack_id = payment.get("invoice_payload", "")
    pack = game.PACKS.get(pack_id)
    if not pack:
        log.error("payment with unknown pack: %s", pack_id)
        return
    with db.tx() as t:
        ensure_user(t, {"id": uid})
        # Telegram может повторить update: по charge_id начисляем ровно один раз.
        fresh = t.run(
            "INSERT INTO payments (charge_id, user_id, pack, stars, created) VALUES (?,?,?,?,?) "
            "ON CONFLICT (charge_id) DO NOTHING",
            (payment["telegram_payment_charge_id"], uid, pack_id,
             payment.get("total_amount", 0), now()),
        ).rowcount
        if fresh:
            t.run("UPDATE users SET coins=coins+?, vip=CASE WHEN ?=1 THEN 1 ELSE vip END WHERE id=?",
                  (pack["coins"], 1 if pack.get("vip") else 0, uid))


def apply_refund(refund: dict):
    charge = refund.get("telegram_payment_charge_id", "")
    with db.tx() as t:
        payment = t.one("SELECT * FROM payments WHERE charge_id=?", (charge,))
        if not payment or not t.run(
            "UPDATE payments SET refunded=1 WHERE charge_id=? AND refunded=0", (charge,)
        ).rowcount:
            return
        pack = game.PACKS.get(payment["pack"], {"coins": 0})
        coins = pack["coins"]
        t.run("UPDATE users SET coins=CASE WHEN coins>=? THEN coins-? ELSE 0 END, "
              "vip=CASE WHEN ?=1 THEN 0 ELSE vip END WHERE id=?",
              (coins, coins, 1 if pack.get("vip") else 0, payment["user_id"]))


def stats_text() -> str:
    day_ago = now() - 86400
    with db.tx() as t:
        users = t.one("SELECT COUNT(*) AS n FROM users")["n"]
        active = t.one("SELECT COUNT(*) AS n FROM users WHERE seen>?", (day_ago,))["n"]
        new = t.one("SELECT COUNT(*) AS n FROM users WHERE created>?", (day_ago,))["n"]
        runs = t.one("SELECT COUNT(*) AS n FROM runs WHERE started>?", (day_ago,))["n"]
        total = t.one("SELECT COALESCE(SUM(stars),0) AS s FROM payments WHERE refunded=0")["s"]
        recent = t.one("SELECT COALESCE(SUM(stars),0) AS s FROM payments "
                       "WHERE refunded=0 AND created>?", (day_ago,))["s"]
    return (f"Игроков: {users}\nАктивных за сутки: {active}\nНовых за сутки: {new}\n"
            f"Забегов за сутки: {runs}\nЗвёзд за сутки: {recent}\nЗвёзд всего: {total}")


def handle_command(chat_id: int, uid, text: str):
    command = text.split()[0].split("@")[0].lower()
    if command == "/start":
        reply = {"chat_id": chat_id, "text":
                 "Ночь. Зомби. Ты, пёс и пять минут до рассвета.\nВыживешь?"}
        if PUBLIC_URL:
            reply["reply_markup"] = {"inline_keyboard": [[
                {"text": "🌙 Играть", "web_app": {"url": PUBLIC_URL + "/"}}]]}
        telegram.call("sendMessage", **reply)
    elif command == "/paysupport":
        telegram.call("sendMessage", chat_id=chat_id, text=(
            f"Вопрос по оплате? Напишите {SUPPORT_CONTACT} и приложите дату покупки. "
            "Если набор не начислился, вернём звёзды."))
    elif command == "/terms":
        telegram.call("sendMessage", chat_id=chat_id, text=(
            "Монеты и VIP — внутриигровые цифровые товары. Они не выводятся и не обмениваются "
            "на деньги. Оплата в Telegram Stars. Поддержка: /paysupport"))
    elif command == "/stats" and ADMIN_ID and uid == ADMIN_ID:
        telegram.call("sendMessage", chat_id=chat_id, text=stats_text())


async def webhook(request):
    token = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not WEBHOOK_SECRET or not hmac.compare_digest(token, WEBHOOK_SECRET):
        return PlainTextResponse("forbidden", status_code=403)
    update = await request.json()
    try:
        await run_in_threadpool(handle_update, update)
    except RuntimeError as error:
        log.error("update failed: %s", error)
    return PlainTextResponse("ok")


# ---------- Обвязка ----------

def api(handler):
    async def endpoint(request):
        user = telegram.verify_init_data(request.headers.get("x-init-data", ""))
        if not user:
            return JSONResponse({"error": "Откройте игру через Telegram"}, status_code=401)
        try:
            raw = await request.body()
            body = json.loads(raw) if raw else {}
            if not isinstance(body, dict):
                raise ValueError
            return JSONResponse(await run_in_threadpool(handler, user, body))
        except GameError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        except (ValueError, TypeError):
            return JSONResponse({"error": "Неверный запрос"}, status_code=400)
        except RuntimeError as error:
            log.error("api failed: %s", error)
            return JSONResponse({"error": "Сервис занят, попробуйте ещё раз"}, status_code=502)
    return endpoint


async def index(_request):
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


async def health(_request):
    return PlainTextResponse("ok")


def setup_bot():
    if not PUBLIC_URL or not WEBHOOK_SECRET:
        log.warning("PUBLIC_URL или WEBHOOK_SECRET не заданы, вебхук не настроен")
        return
    telegram.call("setWebhook", url=f"{PUBLIC_URL}/tg/webhook", secret_token=WEBHOOK_SECRET,
                  allowed_updates=["message", "pre_checkout_query"])
    telegram.call("setChatMenuButton", menu_button={
        "type": "web_app", "text": "Играть", "web_app": {"url": PUBLIC_URL + "/"}})


@asynccontextmanager
async def lifespan(_app):
    if not os.environ.get("BOT_TOKEN"):
        raise SystemExit("Не задан BOT_TOKEN")
    await run_in_threadpool(db.init)
    try:
        await run_in_threadpool(setup_bot)
    except RuntimeError as error:
        log.error("bot setup failed: %s", error)
    yield


app = Starlette(lifespan=lifespan, routes=[
    Route("/", index),
    Route("/health", health),
    Route("/tg/webhook", webhook, methods=["POST"]),
    Route("/adsgram/reward", adsgram_reward),
    Route("/api/me", api(me), methods=["POST"]),
    Route("/api/daily", api(daily), methods=["POST"]),
    Route("/api/upgrade", api(upgrade), methods=["POST"]),
    Route("/api/run/start", api(run_start), methods=["POST"]),
    Route("/api/run/finish", api(run_finish), methods=["POST"]),
    Route("/api/leaderboard", api(leaderboard), methods=["POST"]),
    Route("/api/ad/claim", api(ad_claim), methods=["POST"]),
    Route("/api/invoice", api(invoice), methods=["POST"]),
    Mount("/static", StaticFiles(directory=STATIC)),
])
