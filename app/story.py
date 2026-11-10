"""Разбор и проверка сюжета из story.txt.

Формат файла описан примерами в самом story.txt:
# N | Акт, === id | Название | Герой | Сцена | Условие, [узел], текст,
> Выбор -> узел | эффекты | условие | заметка, -> узел | эффекты, ? условие -> узел,
t секунды номер_выбора, q тип количество секунды успех провал, e Название_концовки.
"""
import pathlib
import re

SOURCE = pathlib.Path(__file__).with_name("story.txt")
STAGING = pathlib.Path(__file__).with_name("staging.txt")
END = "$"


class StoryError(Exception):
    pass


def _fields(text: str) -> list:
    return [part.strip() for part in text.split("|")]


def parse(text: str) -> list:
    acts, act, mission, node = [], None, None, None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            if line.startswith("# "):
                index, title = _fields(line[2:])
                act = {"n": int(index), "title": title, "missions": []}
                acts.append(act)
            elif line.startswith("=== "):
                parts = _fields(line[4:]) + [""]
                mission = {"id": parts[0], "title": parts[1], "who": parts[2], "bg": parts[3],
                           "need": parts[4], "start": None, "nodes": {}}
                act["missions"].append(mission)
            elif re.fullmatch(r"\[[^\]]+\]", line):
                key = line[1:-1]
                if key in mission["nodes"]:
                    raise StoryError("повтор узла " + key)
                node = {"text": "", "choices": [], "go": []}
                mission["nodes"][key] = node
                mission["start"] = mission["start"] or key
            elif line.startswith("> "):
                head, *rest = _fields(line[2:])
                label, target = [p.strip() for p in head.split("->")]
                rest += ["", "", ""]
                node["choices"].append({"l": label, "to": target, "fx": rest[0], "if": rest[1], "note": rest[2]})
            elif line.startswith("-> "):
                target, *rest = _fields(line[3:])
                node["next"] = {"to": target, "fx": rest[0] if rest else ""}
            elif line.startswith("? "):
                condition, target = [p.strip() for p in line[2:].split("->")]
                node["go"].append({"if": condition, "to": target})
            elif re.match(r"t \d", line):
                _, seconds, default = line.split()
                node["timer"] = {"s": int(seconds), "def": int(default) - 1}
            elif re.match(r"q (tap|dir) ", line):
                _, kind, count, seconds, ok, fail = line.split()
                node["qte"] = {"kind": kind, "n": int(count), "s": float(seconds), "ok": ok, "fail": fail}
            elif line.startswith("e "):
                node["ending"] = line[2:].strip()
            else:
                node["text"] = (node["text"] + " " + line).strip()
        except (ValueError, TypeError, AttributeError) as error:
            raise StoryError(f"строка {number}: {raw}: {error}") from error
    return acts


def edges(node: dict) -> list:
    found = [go["to"] for go in node["go"]] + [c["to"] for c in node["choices"]]
    if "next" in node:
        found.append(node["next"]["to"])
    if "qte" in node:
        found += [node["qte"]["ok"], node["qte"]["fail"]]
    return found


def validate(acts: list) -> None:
    problems, seen = [], set()
    for act in acts:
        for mission in act["missions"]:
            mid = mission["id"]
            if mid in seen:
                problems.append("повтор миссии " + mid)
            seen.add(mid)
            nodes = mission["nodes"]
            reachable, stack = set(), [mission["start"]]
            while stack:
                key = stack.pop()
                if key == END or key in reachable:
                    continue
                reachable.add(key)
                if key not in nodes:
                    problems.append(f"{mid}: нет узла {key}")
                    continue
                stack += edges(nodes[key])
            for key, node in nodes.items():
                if key not in reachable:
                    problems.append(f"{mid}: недостижим узел {key}")
                if not node["text"]:
                    if not node["go"]:
                        problems.append(f"{mid}: пустой узел {key}")
                if not (node["choices"] or "next" in node or "qte" in node or "ending" in node or node["go"]):
                    problems.append(f"{mid}: тупик в узле {key}")
                timer = node.get("timer")
                if timer and timer["def"] >= len(node["choices"]):
                    problems.append(f"{mid}: таймер {key}")
    if problems:
        raise StoryError("; ".join(problems))


def load() -> list:
    acts = parse(SOURCE.read_text(encoding="utf8"))
    validate(acts)
    apply_staging(acts, STAGING.read_text(encoding="utf8"))
    return acts


def apply_staging(acts: list, text: str) -> None:
    """Подставляет в каждое событие действие на экране из staging.txt."""
    nodes = {(m["id"], key): node for a in acts for m in a["missions"] for key, node in m["nodes"].items()}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("! "):
            mission, key, index, action, *cast = line[2:].split()
            if (mission, key) not in nodes or int(index) >= len(nodes[(mission, key)]["choices"]):
                raise StoryError(f"постановка для неизвестного выбора {line}")
            nodes[(mission, key)]["choices"][int(index)]["stage"] = {"a": action, "c": cast}
            continue
        mission, key, action, *cast = line.split()
        if (mission, key) not in nodes:
            raise StoryError(f"постановка для неизвестного узла {mission} {key}")
        nodes[(mission, key)]["stage"] = {"a": action, "c": cast}
    missing = [f"{m} {k}" for (m, k), n in nodes.items() if n["text"] and "stage" not in n]
    missing += [f"{m} {k} выбор {i}" for (m, k), n in nodes.items() for i, c in enumerate(n["choices"]) if "stage" not in c]
    if missing:
        raise StoryError("нет постановки: " + ", ".join(missing))


def check(condition: str, state: dict) -> bool:
    """Та же логика условий, что и в браузере: через запятую, все должны выполняться."""
    for part in filter(None, (p.strip() for p in condition.split(","))):
        match = re.fullmatch(r"(\w+)(>=|<=|<|>)(-?\d+)", part)
        if match:
            value, limit = state.get(match[1], 0), int(match[3])
            op = match[2]
            if not ((op == ">=" and value >= limit) or (op == "<=" and value <= limit)
                    or (op == ">" and value > limit) or (op == "<" and value < limit)):
                return False
        elif part.startswith("!"):
            if state.get(part[1:]):
                return False
        elif not state.get(part):
            return False
    return True


def apply_effects(effects: str, state: dict) -> None:
    for part in filter(None, (p.strip() for p in effects.split(","))):
        if part[0] == "+":
            state[part[1:]] = state.get(part[1:], 0) + 1
        elif part[0] == "-":
            state[part[1:]] = state.get(part[1:], 0) - 1
        elif part[0] == "!":
            state[part[1:]] = 0
        else:
            state[part] = 1
