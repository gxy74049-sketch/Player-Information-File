"""可持久化的综测规则，以及统一的明细、分类和封顶计算。"""
import math
from football_workspace import is_finished
from football_events import event_type

RULE_FIELDS = (
    ("cap", "个人累计封顶", 0.3),
    ("team_played", "团体赛上场（每人）", 0.15),
    ("team_registered", "团体赛报名未上场（每人）", 0.05),
    ("event_completed", "单项赛完赛（每项目）", 0.1),
    ("female_bonus", "女生完赛奖励（每项目）", 0.05),
    ("wins_step", "每获胜多少场奖励一次", 2),
    ("win_bonus", "每次获胜奖励", 0.05),
    ("ref_team", "八人制裁判（每场）", 0.1),
    ("ref_event", "单项赛裁判（每场）", 0.05),
    ("manager", "管理人员（每人一次）", 0.15),
    ("audience", "观众（每场）", 0.05),
    ("signin", "活动签到（每次）", 0.05),
)
DEFAULT_RULES = {key: default for key, _, default in RULE_FIELDS}
DEFAULT_RULES["description"] = "团体赛上场与报名未上场不重复计分；单项赛奖励按项目累加；管理身份只计一次；所有类别合计后执行个人封顶。"
ROLE_NAMES = {"ref_team": "八人制裁判", "ref_event": "单项赛裁判", "manager": "赛事管理人员", "audience": "观众"}
CATEGORIES = ("交运杯", "单项赛", "裁判", "管理", "观众", "活动签到")


def validate_rules(values):
    result = {}
    for key, label, default in RULE_FIELDS:
        try:
            number = float(values.get(key, default))
        except (ValueError, TypeError):
            raise ValueError(f"{label}必须为数字")
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{label}必须为非负有限数字")
        if key == "wins_step":
            if not number.is_integer() or number < 1:
                raise ValueError("获胜奖励间隔必须是大于等于 1 的整数")
            number = int(number)
        elif number > 10000 or abs(number - round(number, 4)) > 1e-10:
            raise ValueError(f"{label}最多保留 4 位小数，且不超过 10000 分")
        result[key] = number
    result["description"] = str(values.get("description", DEFAULT_RULES["description"])).strip()
    return result


def get_rules(data):
    return validate_rules(data.get("score_rules", {}))


def role_matches(record, pid, name):
    return record.get("pid") == pid or (not record.get("pid") and record.get("name") == name)


def role_rows(data, pid):
    rules = get_rules(data)
    person = data["persons"].get(pid)
    if not person:
        return []
    rows = []
    for role in ("ref_team", "ref_event", "audience"):
        for rec in data["roles"].get(role, []):
            if role_matches(rec, pid, person.get("name", "")):
                count = rec.get("matches", 1)
                rows.append(("观众" if role == "audience" else "裁判",
                             f"{ROLE_NAMES[role]} · {count} 场", round(rules[role] * count, 4)))
    if any(role_matches(rec, pid, person.get("name", "")) for rec in data["roles"].get("manager", [])):
        rows.append(("管理", ROLE_NAMES["manager"], rules["manager"]))
    return rows


def score_entries(data, pid, single_stats):
    person = data["persons"].get(pid)
    if not person:
        return []
    rules = get_rules(data)
    rows = []
    registered = any(pid in team.get("members", {}) for team in data["teams"].values())
    played = any(is_finished(match) and pid in match.get("lineup1", []) + match.get("lineup2", [])
                 for match in data["matches"])
    if played:
        rows.append(("交运杯", "交运杯 · 上场队员", rules["team_played"]))
    elif registered:
        rows.append(("交运杯", "交运杯 · 报名未上场", rules["team_registered"]))
    for name, event in data["events"].items():
        if pid not in event.get("regs", []):
            continue
        if event_type(name, event) == "duel":
            wins = single_stats(event)[0].get(pid, 0)
            done = wins > 0 or any(pid in (m.get("p1"), m.get("p2")) for m in event.get("matches", []))
        else:
            wins = int(event.get("wins", {}).get(pid, 0) or 0)
            done = event.get("prelim", {}).get(pid) is not None or event.get("final", {}).get(pid) is not None
        if not done:
            continue
        rows.append(("单项赛", f"{name} · 完赛", rules["event_completed"]))
        if person.get("gender") == "女":
            rows.append(("单项赛", f"{name} · 女生奖励", rules["female_bonus"]))
        bonus = round((wins // rules["wins_step"]) * rules["win_bonus"], 4)
        if bonus:
            rows.append(("单项赛", f"{name} · 获胜奖励(胜 {wins} 场)", bonus))
    rows.extend(role_rows(data, pid))
    for rec in data.get("signins", []):
        if rec.get("pid") == pid:
            rows.append(("活动签到", f"活动签到 · {rec.get('activity', '')}（{rec.get('date', '')}）", rules["signin"]))
    return rows


def calculate_score(data, pid, single_stats):
    entries = score_entries(data, pid, single_stats)
    categories = tuple(round(sum(value for category, _, value in entries if category == name), 4) for name in CATEGORIES)
    raw = round(sum(categories), 4)
    return {"categories": categories, "raw": raw, "capped": round(min(raw, get_rules(data)["cap"]), 4),
            "rows": [(label, value) for _, label, value in entries]}


def rules_sheet(data):
    rules = get_rules(data)
    return [["规则项", "当前设置"]] + [[label, rules[key]] for key, label, _ in RULE_FIELDS] + [["规则说明（不参与公式计算）", rules["description"]]]
