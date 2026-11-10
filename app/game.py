"""Баланс игры. Всё, что влияет на деньги, считается только здесь, на сервере."""

MAX_LEVEL = 15
DAWN_SECONDS = 300          # одна ночь = 5 минут
MAX_KILLS_PER_SECOND = 10   # больше физически нельзя: защита от накрутки
MAX_COINS_PER_RUN = 20_000
REFERRAL_BONUS = 500
AD_CREDITS_CAP = 5          # сколько просмотров можно держать «про запас»
AD_DAILY_CAP = 30           # лимит наград за рекламу в сутки на игрока

UPGRADES = {
    "damage": {"name": "Патроны", "desc": "+15% к урону", "base": 100},
    "rate":   {"name": "Затвор",  "desc": "+6% к скорострельности", "base": 120},
    "hp":     {"name": "Закалка", "desc": "+15 к здоровью", "base": 90},
    "armor":  {"name": "Бронежилет", "desc": "+10 к броне", "base": 130},
    "speed":  {"name": "Кроссовки", "desc": "+4% к скорости", "base": 110},
    "magnet": {"name": "Магнит", "desc": "+20% к радиусу сбора", "base": 80},
    "dog":    {"name": "Пёс Бублик", "desc": "+25% к укусу пса", "base": 150},
}

PACKS = {
    "coins_s": {"stars": 25,  "coins": 1_500,  "title": "Горсть монет"},
    "coins_m": {"stars": 100, "coins": 8_000,  "title": "Мешок монет"},
    "coins_l": {"stars": 350, "coins": 35_000, "title": "Сундук монет"},
    "vip":     {"stars": 199, "coins": 2_000,  "title": "VIP навсегда",
                "vip": True},
}


# Косметика не влияет на силу персонажа.
DEFAULT_SKINS = {"hat": "cap", "outfit": "plain", "dog": "bublik", "tracer": "yellow"}

SKINS = {
    "cap":     {"slot": "hat", "name": "Кепка", "coins": 0},
    "helmet":  {"slot": "hat", "name": "Каска", "coins": 800},
    "ushanka": {"slot": "hat", "name": "Ушанка", "coins": 1_200},
    "cowboy":  {"slot": "hat", "name": "Шляпа", "coins": 1_800},
    "mohawk":  {"slot": "hat", "name": "Ирокез", "coins": 2_500},
    "crown":   {"slot": "hat", "name": "Корона", "stars": 150},
    "plain":   {"slot": "outfit", "name": "Обычный", "coins": 0},
    "camo":    {"slot": "outfit", "name": "Камуфляж", "coins": 600},
    "hazmat":  {"slot": "outfit", "name": "Химзащита", "coins": 1_200},
    "ninja":   {"slot": "outfit", "name": "Алый", "coins": 2_000},
    "gold":    {"slot": "outfit", "name": "Золотой", "stars": 99},
    "bublik":  {"slot": "dog", "name": "Бублик", "coins": 0},
    "ryzhik":  {"slot": "dog", "name": "Рыжик", "coins": 500},
    "ugolek":  {"slot": "dog", "name": "Уголёк", "coins": 900},
    "robot":   {"slot": "dog", "name": "Робопёс", "stars": 75},
    "yellow":  {"slot": "tracer", "name": "Жёлтые", "coins": 0},
    "toxic":   {"slot": "tracer", "name": "Ядовитые", "coins": 700},
    "plasma":  {"slot": "tracer", "name": "Плазма", "coins": 1_500},
    "fire":    {"slot": "tracer", "name": "Огненные", "stars": 50},
}

# Характеристики стволов живут в клиенте, сервер отвечает за покупку и выбор.
DEFAULT_WEAPON = "pistol"
WEAPONS = {
    "pistol":  {"name": "Пистолет", "desc": "Надёжный и простой", "coins": 0},
    "shotgun": {"name": "Дробовик", "desc": "Сносит толпу вблизи", "coins": 1_500},
    "smg":     {"name": "Автомат", "desc": "Очень быстрая стрельба", "coins": 2_500},
    "sniper":  {"name": "Снайперка", "desc": "Двойной урон по боссам, пробивает ряд", "coins": 4_000},
    "flame":   {"name": "Огнемёт", "desc": "Жжёт всех рядом, бьёт близко", "coins": 5_000},
    "rocket":  {"name": "Базука", "desc": "Взрыв задевает всех вокруг", "coins": 6_500},
    "rail":    {"name": "Рельсотрон", "desc": "Луч пробивает всех на линии, сильнее по боссам", "stars": 249},
}

for _weapon_id, _weapon in WEAPONS.items():
    if "stars" in _weapon:
        PACKS["weapon_" + _weapon_id] = {"stars": _weapon["stars"], "coins": 0,
                                         "title": _weapon["name"], "weapon": _weapon_id}

for _skin_id, _skin in SKINS.items():
    if "stars" in _skin:
        PACKS["skin_" + _skin_id] = {"stars": _skin["stars"], "coins": 0,
                                     "title": _skin["name"], "skin": _skin_id}


STORY_FREE_ACTS = 2
STORY_PASS_STARS = 149
STORY_PASS_COINS = 15_000
STORY_REWARD = 100
STORY_SAVE_LIMIT = 120_000
PACKS["story_pass"] = {"stars": STORY_PASS_STARS, "coins": 0, "title": "Полный сюжет", "story": True}


def upgrade_cost(upgrade_id: str, level: int) -> int:
    return int(UPGRADES[upgrade_id]["base"] * 1.55 ** level)


def run_reward(kills: int, seconds: int, night: int, vip: bool, dawn: bool) -> dict:
    coins = kills * 2 + seconds // 3
    coins = int(coins * (1 + 0.25 * (night - 1)))
    if dawn:
        coins += 300 * night
    if vip:
        coins *= 2
    score = kills * 10 + seconds * 2 + (night * 1000 if dawn else 0)
    return {"coins": min(coins, MAX_COINS_PER_RUN), "score": score}
