"""Баланс игры. Всё, что влияет на деньги, считается только здесь, на сервере."""

MAX_LEVEL = 15
DAWN_SECONDS = 300          # одна ночь = 5 минут
MAX_KILLS_PER_SECOND = 7    # больше физически нельзя: защита от накрутки
MAX_COINS_PER_RUN = 20_000
REFERRAL_BONUS = 500
AD_CREDITS_CAP = 5          # сколько просмотров можно держать «про запас»
AD_DAILY_CAP = 30           # лимит наград за рекламу в сутки на игрока

UPGRADES = {
    "damage": {"name": "Патроны", "desc": "+15% к урону", "base": 100},
    "rate":   {"name": "Затвор",  "desc": "+6% к скорострельности", "base": 120},
    "hp":     {"name": "Бронежилет", "desc": "+15 к здоровью", "base": 90},
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
