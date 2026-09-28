# -*- coding: utf-8 -*-
"""
============================================================================
 交通运输学院足球嘉年华档案管理系统（离线桌面版）
============================================================================
 【板块一】交运杯混合抽签八人制足球赛 —— 团体赛，单循环积分制
 【板块二】绿茵全能王足球单项系列赛 —— 5 个独立单项：
          1对1单挑赛 / 传球赛 / 技巧赛 / 点球大赛 / 射门赛
----------------------------------------------------------------------------
 【启动方式】
   方式 1：命令行运行  python 交运杯足球嘉年华管理系统.py
   方式 2：双击该文件运行（需 Python 3.8+，自带 tkinter，无需联网）
 【数据存储】
   自动保存至本程序同目录下 football_carnival.json，重启不丢失；
   亦可在"综测加分与导出"页手动备份为带时间戳的 JSON 文件。
 【首次使用步骤】
   1. 人员管理页：先录入参赛人员基础档案（学号/姓名/性别/年级/联系方式）
   2. 交运杯页：新建队伍并添加队员（可标注 A/B 档）→ 录入比赛结果 → 看积分榜
   3. 单项赛页：切换单项 → 报名名单 → 录入成绩（或对阵记录）→ 晋级计算 → 看排名
   4. 综测页：补充裁判/管理/观众记录 → 查询个人加分明细与全体综测总表 → 导出
============================================================================
"""
import os
import copy
import hashlib
import posixpath
import re
import json
import csv
import math
import shutil
import datetime
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from football_workspace import (STATUSES, match_status, is_finished, score_text,
                                validate_schedule, DashboardPage, PlayerPage)
from football_scoring import DEFAULT_RULES, get_rules, role_rows, calculate_score, rules_sheet
from football_controls import MemberPicker, RulesEditor, CtrlDragSelection
from football_events import event_type, AddEventDialog

# ---------------------------------------------------------------------------
# 全局常量
# ---------------------------------------------------------------------------
APP_TITLE = "交通运输学院足球嘉年华档案管理系统"
EVENT_NAMES = ["1对1单挑赛", "传球赛", "技巧赛", "点球大赛", "射门赛"]
SCORE_EVENTS = ["传球赛", "点球大赛", "射门赛"]   # 得分类：成绩越高越好
TIME_EVENTS = ["技巧赛"]                          # 用时类：用时越短越好
DEFAULT_DATA_FILE = "football_carnival.json"

TEAM_WIN, TEAM_DRAW = 3, 1   # 胜 3 分、平 1 分、负 0 分
# 综测分值由 football_scoring.DEFAULT_RULES 提供默认值，可在「综测规则设置」保存修改。

GRADES = ["2023级", "2024级", "2025级", "2026级"]
GENDERS = ["男", "女"]
# 身份记录：角色 -> (显示名, 每单位加分)
ROLE_LABELS = {
    "ref_team":  ("八人制裁判", 0.10),   # 八人制单场 0.1 分
    "ref_event": ("单项赛裁判", 0.05),   # 单项赛单场 0.05 分
    "manager":   ("赛事管理人员", 0.15),  # 赛事管理人员 0.15 分
    "audience":  ("观众", 0.05),          # 观众单场 0.05 分
}


def now_str():
    """当前时间字符串，用于默认日期与备份文件名"""
    return datetime.datetime.now().strftime("%Y-%m-%d")


def parse_num(text, name="成绩"):
    """成绩数值校验：必须为非负数字；返回 float 或抛 ValueError"""
    s = str(text).strip()
    if s == "":
        raise ValueError(f"{name}不能为空")
    try:
        v = float(s)
    except ValueError:
        raise ValueError(f"{name}必须是数字")
    if not math.isfinite(v):
        raise ValueError(f"{name}必须是有限数字")
    if v < 0:
        raise ValueError(f"{name}不能为负数")
    return v


def fmt(v):
    """格式化成绩显示：去掉多余的 0"""
    if v is None:
        return "-"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return f"{v:g}"


# ===========================================================================
# 模块 1：数据层（JSON 本地持久化）
# ===========================================================================
def _new_event():
    """创建单个单项赛的默认数据结构"""
    return {
        "regs": [],       # 报名名单（学号列表）
        "prelim": {},     # 预赛成绩 {学号: float}
        "final": {},      # 决赛成绩 {学号: float}
        "qualified": {},  # 晋级标记 {学号: bool}
        "wins": {},       # 获胜场次 {学号: int}（单挑赛自动统计，其余手动维护）
        "matches": [],    # 对阵记录（仅 1对1 单挑赛使用）
        "notes": {},      # 备注（如技巧赛罚时记录）{学号: str}
    }


def _merge(d, u):
    """浅深合并，兼容旧版本数据缺失字段"""
    out = dict(d)
    for k, v in u.items():
        if k in d and isinstance(d[k], dict) and isinstance(v, dict):
            out[k] = _merge(d[k], v)
        else:
            out[k] = v
    return out


class DataStore:
    """负责数据的载入、保存与人员级联删除"""

    def __init__(self, path):
        self.path = path
        self.data = self._default()
        self.read_only = False
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError("数据根节点必须为对象")
                for key, default in self._default().items():
                    if key in loaded and not isinstance(loaded[key], type(default)):
                        raise ValueError(f"{key} 数据类型不正确")
                self.data = _merge(self._default(), loaded)
                # 拒绝 JSON 扩展值 NaN/Infinity，避免进入成绩和规则计算。
                json.dumps(self.data, allow_nan=False)
                for person in self.data["persons"].values():
                    if not isinstance(person, dict):
                        raise ValueError("人员档案格式不正确")
                for team in self.data["teams"].values():
                    if not isinstance(team, dict) or not isinstance(team.get("members", {}), dict):
                        raise ValueError("队伍成员格式不正确")
                    team.setdefault("members", {})
                for name, event in self.data["events"].items():
                    if not isinstance(event, dict):
                        raise ValueError("赛事格式不正确")
                    for key, default in _new_event().items():
                        if key in event and not isinstance(event[key], type(default)):
                            raise ValueError(f"赛事 {name} 的 {key} 格式不正确")
                    self.data["events"][name] = _merge(_new_event(), event)
                get_rules(self.data)
            except Exception:
                self.data = self._default()
                self.read_only = True
                # 数据损坏：先自动备份损坏文件，避免后续保存操作覆盖原始数据
                try:
                    bak = (path + ".corrupt_" +
                           datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
                    shutil.copyfile(path, bak)
                    hint = f"已自动备份损坏文件为：\n{bak}\n"
                except Exception:
                    hint = ""
                messagebox.showwarning("数据读取",
                                       "数据文件损坏，" + hint + "已只读启动，不会覆盖原文件。请恢复备份后重启。")
        self._persisted = copy.deepcopy(self.data)
        self._disk_signature = self._signature()

    def _signature(self):
        if not os.path.exists(self.path):
            return None
        with open(self.path, "rb") as file:
            return hashlib.sha256(file.read()).digest()

    def _default(self):
        return {
            "persons": {},   # 学号 -> {name, gender, grade, phone}
            "teams": {},     # 队名 -> {leader, members: {学号: "A"|"B"}}
            "matches": [],   # [{id, team1, team2, date, g1, g2, lineup1:[学号], lineup2:[学号]}]
            "events": {e: _new_event() for e in EVENT_NAMES},
            "roles": {"ref_team": [], "ref_event": [], "manager": [], "audience": []},
            "signins": [],   # 活动签到记录 [{activity, date, pid, name}]（批量导入写入）
            "score_rules": dict(DEFAULT_RULES),
            "awards": {
                "team": {"champion": "", "runner_up": "", "third": ""},
                "player": {"mvp": "", "golden_boot": ""},
            },
        }

    def save(self):
        """原子写入 JSON，避免写一半损坏"""
        tmp = self.path + ".tmp"
        try:
            if self.read_only:
                raise OSError("当前数据文件异常，禁止覆盖。请恢复备份后重启系统。")
            if self._signature() != self._disk_signature:
                raise OSError("数据文件已被其他窗口或程序修改。请重新打开系统后操作，避免覆盖其他修改。")
            payload = json.dumps(self.data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            snapshot = copy.deepcopy(self.data)
            with open(tmp, "wb") as f:
                f.write(payload)
            if os.path.exists(self.path):
                shutil.copyfile(self.path, self.path + ".previous.tmp")
                os.replace(self.path + ".previous.tmp", self.path + ".previous")
            os.replace(tmp, self.path)
        except Exception:
            self.data = copy.deepcopy(self._persisted)
            raise
        self._persisted = snapshot
        self._disk_signature = hashlib.sha256(payload).digest()

    def backup(self):
        """生成带时间戳的备份文件，返回文件路径"""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dst = os.path.join(os.path.dirname(self.path),
                           f"football_carnival_backup_{ts}.json")
        shutil.copyfile(self.path, dst)
        return dst

    # ---------- 人员相关 ----------
    def remove_person(self, pid, save=True):
        """删除人员并级联清理所有关联数据（队伍/报名/成绩/对阵/奖项/身份记录/签到）"""
        d = self.data
        p = d["persons"].get(pid)
        name = p.get("name", "") if p else ""
        d["persons"].pop(pid, None)
        for tinfo in d["teams"].values():
            tinfo["members"].pop(pid, None)
            if tinfo.get("leader") == pid:
                tinfo["leader"] = ""
        for ev in d["events"].values():
            ev["regs"] = [i for i in ev["regs"] if i != pid]
            for k in ("prelim", "final", "qualified", "wins"):
                ev[k].pop(pid, None)
            ev["notes"].pop(pid, None)
            ev["matches"] = [m for m in ev["matches"]
                             if m.get("p1") != pid and m.get("p2") != pid]
        for m in d["matches"]:
            m.get("player_stats", {}).pop(pid, None)
            m["lineup1"] = [i for i in m.get("lineup1", []) if i != pid]
            m["lineup2"] = [i for i in m.get("lineup2", []) if i != pid]
        a = d["awards"]["player"]
        if a.get("mvp") == pid:
            a["mvp"] = ""
        if a.get("golden_boot") == pid:
            a["golden_boot"] = ""
        d["signins"] = [r for r in d.get("signins", []) if r.get("pid") != pid]
        # 级联清理身份记录：优先按学号匹配，兼容旧数据按姓名匹配
        for role in d["roles"].values():
            role[:] = [rec for rec in role
                       if not (rec.get("pid") == pid
                               or (not rec.get("pid") and rec.get("name") == name))]
        if save:
            self.save()

    def remove_persons(self, pids):
        """批量清理后只保存一次，保留完整的删除前备份。"""
        selected = set(pids) & self.data["persons"].keys()
        if not selected:
            return
        previous = copy.deepcopy(self.data)
        try:
            for pid in selected:
                self.remove_person(pid, save=False)
            self.save()
        except Exception:
            self.data = previous
            raise

    # ---------- 比赛相关 ----------
    def next_match_id(self):
        ids = [m.get("id", 0) for m in self.data["matches"]]
        return (max(ids) + 1) if ids else 1


# ===========================================================================
# 模块 2：规则引擎（积分排名 / 晋级名额 / 综测加分）
# ===========================================================================
def compute_team_stats(data):
    """统计每支队伍：场次/胜/平/负/进球/失球/净胜球/积分"""
    stats = {}
    for t in data["teams"]:
        stats[t] = {"played": 0, "win": 0, "draw": 0, "loss": 0,
                    "gf": 0, "ga": 0, "gd": 0, "pts": 0}
    for m in data["matches"]:
        if not is_finished(m):
            continue
        t1, t2 = m.get("team1"), m.get("team2")
        if t1 not in stats or t2 not in stats:
            continue  # 队伍已删除时忽略
        g1, g2 = m.get("g1"), m.get("g2")
        if not isinstance(g1, (int, float)) or not isinstance(g2, (int, float)):
            continue  # 脏数据防御：缺少有效比分时忽略该场
        for t, g, ga in ((t1, g1, g2), (t2, g2, g1)):
            s = stats[t]
            s["played"] += 1
            s["gf"] += g
            s["ga"] += ga
            if g > ga:
                s["win"] += 1
                s["pts"] += TEAM_WIN
            elif g == ga:
                s["draw"] += 1
                s["pts"] += TEAM_DRAW
            else:
                s["loss"] += 1
    for s in stats.values():
        s["gd"] = s["gf"] - s["ga"]
    return stats


def h2h_points(a, b, matches):
    """两队相互交锋得分（胜负关系判定用）"""
    pts = 0
    for m in matches:
        if not is_finished(m):
            continue
        if {m.get("team1"), m.get("team2")} != {a, b}:
            continue
        if m.get("team1") == a:
            ga, gb = m.get("g1", 0), m.get("g2", 0)
        else:
            ga, gb = m.get("g2", 0), m.get("g1", 0)
        if ga > gb:
            pts += 3
        elif ga == gb:
            pts += 1
    return pts


def rank_teams(data):
    """
    交运杯自动排名：积分 → 胜负关系 → 净胜球 → 总进球
    返回 [{rank, team, stats, award}]
    """
    stats = compute_team_stats(data)
    matches = data["matches"]

    # 同分组内统计相互交锋积分，避免三队循环胜负导致比较器不满足传递性。
    head_to_head = {team: sum(h2h_points(team, other, matches) for other in stats
                              if other != team and stats[other]["pts"] == stats[team]["pts"])
                    for team in stats}
    ordered = sorted(stats, key=lambda team: (-stats[team]["pts"], -head_to_head[team],
                                              -stats[team]["gd"], -stats[team]["gf"], team))
    medals = ["冠军", "亚军", "季军"]
    out = []
    for idx, t in enumerate(ordered, 1):
        out.append({"rank": idx, "team": t, "stats": stats[t],
                    "award": medals[idx - 1] if idx <= 3 and stats[t]["played"] else ""})
    return out


def single_match_stats(ev):
    """
    统计 1对1 单挑赛每名选手的 (胜场, 净胜分, 总进球)
    规则：比分高者胜；对手为空视为轮空直接获胜
    """
    wins, diff, goals = {}, {}, {}
    for m in ev.get("matches", []):
        p1, p2 = m.get("p1"), m.get("p2")
        s1, s2 = m.get("s1", 0), m.get("s2", 0)
        if p2 == "" and p1:          # 轮空：p1 直接获胜
            wins[p1] = wins.get(p1, 0) + 1
            diff[p1] = diff.get(p1, 0) + s1
            goals[p1] = goals.get(p1, 0) + s1
            continue
        if p1 == "" and p2:          # 轮空：p2 直接获胜
            wins[p2] = wins.get(p2, 0) + 1
            diff[p2] = diff.get(p2, 0) + s2
            goals[p2] = goals.get(p2, 0) + s2
            continue
        if not p1 or not p2:
            continue
        goals[p1] = goals.get(p1, 0) + s1
        goals[p2] = goals.get(p2, 0) + s2
        diff[p1] = diff.get(p1, 0) + (s1 - s2)
        diff[p2] = diff.get(p2, 0) + (s2 - s1)
        if s1 > s2:
            wins[p1] = wins.get(p1, 0) + 1
        elif s2 > s1:
            wins[p2] = wins.get(p2, 0) + 1
    return wins, diff, goals


def event_quota(data, ename):
    """
    晋级规则自动执行：
      - 单项目报名 ≤ 12 人 → 取消预赛，直接进入决赛
      - 预赛按有效参赛人数前 1/3 确定晋级名额，向上取整，上限 12 人
    返回 (晋级名额, 是否直接决赛, 有效参赛人数)
    """
    ev = data["events"][ename]
    n_reg = len(ev.get("regs", []))
    if event_type(ename, ev) == "duel":
        effective = len({i for m in ev.get("matches", [])
                         for i in (m.get("p1"), m.get("p2")) if i})
    else:
        effective = sum(1 for i in ev.get("regs", [])
                        if ev["prelim"].get(i) is not None)
    if n_reg == 0:
        return 0, False, 0
    if n_reg <= 12:                       # 直接决赛：全员进入
        return n_reg, True, effective
    base = effective if effective > 0 else n_reg  # 尚无成绩时按报名人数估算
    quota = min(12, math.ceil(base / 3))          # 前 1/3 向上取整，上限 12
    return quota, False, effective


def _event_value(ev, ename, pid):
    """单项赛选手成绩值：决赛优先，无决赛取预赛（得分/用时统一返回数值）"""
    f = ev["final"].get(pid)
    p = ev["prelim"].get(pid)
    return f if f is not None else p


def qualification_ids(data, ename):
    """只从有效成绩中选取晋级人员；小规模赛事仍全员直接决赛。"""
    event = data["events"][ename]
    quota, direct, _ = event_quota(data, ename)
    if direct:
        return list(event["regs"])
    kind = event_type(ename, event)
    if kind == "duel":
        played = {pid for match in event["matches"] for pid in (match.get("p1"), match.get("p2")) if pid}
        wins, diff, goals = single_match_stats(event)
        ranked = sorted((pid for pid in event["regs"] if pid in played),
                        key=lambda pid: (-wins.get(pid, 0), -diff.get(pid, 0), -goals.get(pid, 0)))
    else:
        ranked = sorted((pid for pid in event["regs"] if event["prelim"].get(pid) is not None),
                        key=lambda pid: event["prelim"][pid], reverse=kind == "score")
    return ranked[:quota]


def rank_event(data, ename):
    """
    生成单项赛排名并标注冠亚季军。
    返回 [{rank, id, name, result, detail, award}]
    """
    ev = data["events"][ename]
    regs = list(ev.get("regs", []))
    persons = data["persons"]
    rows = []
    if event_type(ename, ev) == "duel":
        wins, diff, goals = single_match_stats(ev)
        matches = ev.get("matches", [])
        played_ids = {i for m in matches for i in (m.get("p1"), m.get("p2")) if i}
        regs.sort(key=lambda i: (i not in played_ids, -wins.get(i, 0), -diff.get(i, 0), -goals.get(i, 0)))
        for i in regs:
            # 未参与任何对阵且无胜场的报名者标记为「未完赛」，不参与冠亚季军评定
            played = i in played_ids or wins.get(i, 0) > 0
            rows.append({
                "id": i, "name": persons.get(i, {}).get("name", "?"),
                "result": f"{wins.get(i, 0)} 胜" if played else "未完赛",
                "detail": f"净胜 {diff.get(i, 0)} 球 · 进 {goals.get(i, 0)} 球",
            })
    elif event_type(ename, ev) == "score":          # 得分类：从高到低
        regs.sort(key=lambda i: -_event_value(ev, ename, i) if _event_value(ev, ename, i) is not None else float("inf"))
        for i in regs:
            v = _event_value(ev, ename, i)
            rows.append({
                "id": i, "name": persons.get(i, {}).get("name", "?"),
                "result": fmt(v) + " 分" if v is not None else "未完赛",
                "detail": f"预赛 {fmt(ev['prelim'].get(i))} · 决赛 {fmt(ev['final'].get(i))}",
            })
    elif event_type(ename, ev) == "time":           # 用时类：从短到长
        regs.sort(key=lambda i: _event_value(ev, ename, i) if _event_value(ev, ename, i) is not None else float("inf"))
        for i in regs:
            v = _event_value(ev, ename, i)
            note = ev["notes"].get(i, "")
            rows.append({
                "id": i, "name": persons.get(i, {}).get("name", "?"),
                "result": fmt(v) + " 秒" if v is not None else "未完赛",
                "detail": f"预赛 {fmt(ev['prelim'].get(i))} · 决赛 {fmt(ev['final'].get(i))}"
                          + (f" · 罚时:{note}" if note else ""),
            })
    medals = ["冠军", "亚军", "季军"]
    for idx, r in enumerate(rows, 1):
        r["rank"] = idx
        r["award"] = medals[idx - 1] if (idx <= 3 and r["result"] != "未完赛") else ""
    return rows


def _role_match(rec, pid, name):
    """身份记录匹配：优先按学号；兼容旧数据按姓名"""
    return rec.get("pid") == pid or (not rec.get("pid") and rec.get("name") == name)


def person_roles_rows(data, pid):
    return [(label, points) for _, label, points in role_rows(data, pid)]


def person_detail(data, pid):
    result = calculate_score(data, pid, single_match_stats)
    return result["raw"], result["capped"], result["rows"]


def person_category_points(data, pid):
    return calculate_score(data, pid, single_match_stats)["categories"]


# ===========================================================================
# 模块 3：导出工具（CSV / 纯标准库 XLSX / JSON 备份）
# ===========================================================================
def _xlsx_col(n):
    """列号转 Excel 列字母"""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet_xml(rows):
    """生成单个工作表的 XML（使用内联字符串，无需 sharedStrings）"""
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for r, row in enumerate(rows, 1):
        cells = []
        for c, val in enumerate(row, 1):
            ref = f"{_xlsx_col(c)}{r}"
            if isinstance(val, (int, float)):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                v = escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", "" if val is None else str(val)))
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{v}</t></is></c>')
        parts.append(f'<row r="{r}">{"".join(cells)}</row>')
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def export_xlsx(path, sheets):
    """最简 XLSX 写出（零第三方依赖）。sheets: {表名: [[单元格,...], ...]}"""
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(sheets) + 1))
            + "</Types>",
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
    }
    wb_sheets, wb_rels = [], []
    for idx, (name, rows) in enumerate(sheets.items(), 1):
        files[f"xl/worksheets/sheet{idx}.xml"] = _sheet_xml(rows)
        wb_sheets.append(f'<sheet name={quoteattr(str(name))} sheetId="{idx}" r:id="rId{idx}"/>')
        wb_rels.append(
            f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>')
    files["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        + "".join(wb_sheets) + "</sheets></workbook>")
    files["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(wb_rels) + "</Relationships>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)


def do_export(parent, default_name, sheets):
    """弹出保存对话框，按扩展名导出 CSV（首个工作表）或 XLSX"""
    path = filedialog.asksaveasfilename(
        parent=parent, defaultextension=".xlsx" if len(sheets) > 1 else ".csv", initialfile=default_name,
        filetypes=([("Excel 文件", "*.xlsx"), ("CSV（仅第一张表）", "*.csv")] if len(sheets) > 1
                   else [("CSV 文件", "*.csv"), ("Excel 文件", "*.xlsx")]))
    if not path:
        return False
    try:
        if path.lower().endswith(".xlsx"):
            export_xlsx(path, sheets)
        else:
            _, rows = next(iter(sheets.items()))
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerows(rows)
        messagebox.showinfo("导出成功", f"已导出到：\n{path}", parent=parent)
        return True
    except Exception as e:
        messagebox.showerror("导出失败", str(e), parent=parent)
        return False


def team_ranking_sheet(data):
    """交运杯积分榜导出数据"""
    rows = [["名次", "队伍", "场次", "胜", "平", "负", "进球", "失球", "净胜球", "积分", "奖项"]]
    for r in rank_teams(data):
        s = r["stats"]
        rows.append([r["rank"], r["team"], s["played"], s["win"], s["draw"],
                     s["loss"], s["gf"], s["ga"], s["gd"], s["pts"], r["award"]])
    return {"交运杯积分榜": rows}


def match_detail_sheet(data):
    """交运杯比赛明细导出数据：逐场对阵、比分与双方上场人数"""
    rows = [["ID", "日期", "主队", "比分", "客队", "主队名单人数", "客队名单人数", "开球时间", "场地", "状态", "赛况备注"]]
    for m in data["matches"]:
        rows.append([m.get("id"), m.get("date", ""), m.get("team1"),
                     score_text(m), m.get("team2"),
                     len(m.get("lineup1", [])), len(m.get("lineup2", [])),
                     m.get("time", ""), m.get("venue", ""), match_status(m), m.get("notes", "")])
    return {"交运杯比赛明细": rows}


def event_ranking_sheets(data):
    """全部单项赛排名导出数据（每项一个工作表）"""
    sheets = {}
    for ename in data["events"]:
        rows = [["名次", "学号", "姓名", "成绩", "备注", "奖项"]]
        for r in rank_event(data, ename):
            rows.append([r["rank"], r["id"], r["name"], r["result"], r["detail"], r["award"]])
        sheets[ename] = rows
    return sheets


def score_summary_sheet(data):
    """全体人员综测总表导出数据"""
    rows = [["学号", "姓名", "性别", "交运杯", "单项赛", "裁判", "管理", "观众",
             "活动签到", "原始合计", f"实得(封顶{get_rules(data)['cap']:g})"]]
    for pid, p in data["persons"].items():
        t, e, r, m, a, sg = person_category_points(data, pid)
        raw = round(t + e + r + m + a + sg, 4)
        rows.append([pid, p["name"], p.get("gender", ""), t, e, r, m, a, sg,
                     raw, round(min(raw, get_rules(data)["cap"]), 4)])
    return {"综测总表": rows, "综测计算规则": rules_sheet(data)}


def person_sheet(data):
    """学生档案表导出数据：学号、姓名、性别、年级、联系方式"""
    rows = [["学号", "姓名", "性别", "年级", "联系方式"]]
    for pid, p in data["persons"].items():
        rows.append([pid, p.get("name", ""), p.get("gender", ""),
                     p.get("grade", ""), p.get("phone", "")])
    return {"学生档案表": rows}


def signin_sheet(data):
    """活动签到明细导出数据：活动、日期、学号、姓名"""
    rows = [["活动", "日期", "学号", "姓名"]]
    for r in data.get("signins", []):
        rows.append([r.get("activity", ""), r.get("date", ""),
                     r.get("pid", ""), r.get("name", "")])
    return {"活动签到明细": rows}


# ===========================================================================
# 模块 3.5：批量导入引擎（纯标准库读取 CSV/XLSX + 校验 + 写入）
# ===========================================================================
SID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")   # 学号合法字符：字母/数字/下划线/连字符
SID_MAX_LEN = 30
IMPORT_HINTS = "支持 .xlsx / .csv · 首行为表头 · 自动跳过空行 · Excel 学号列请设为「文本」格式，避免前导零丢失"


def valid_student_id(sid):
    """学号格式校验：非空、仅含字母/数字/下划线/连字符、长度不超过 30"""
    return (bool(sid) and len(sid) <= SID_MAX_LEN and bool(SID_RE.match(sid)))


def _is_blank_row(row):
    """空行判定：所有单元格去除空白后均为空"""
    return all(str(c).strip() == "" for c in row)


def _find_col(row, names):
    """在表头行中按候选名称查找列索引，找不到返回 -1"""
    for i, c in enumerate(row):
        if str(c).strip() in names:
            return i
    return -1


def _xlsx_col_index(ref):
    """Excel 单元格引用（如 "B3"）-> 0 基列索引"""
    m = re.match(r"[A-Za-z]+", str(ref or ""))
    if not m:
        return 0
    n = 0
    for ch in m.group().upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _xlsx_cell_text(el, ns, tag):
    """收集 XML 元素下指定标签的文本（用于 sharedStrings / inlineStr）"""
    return "".join(t.text or "" for t in el.iter(f"{{{ns}}}{tag}"))


def read_xlsx(path):
    """
    读取 xlsx 第一个工作表，返回 (表名, 行列表)。
    纯标准库实现，兼容 sharedStrings 与 inlineStr 两种字符串存储方式。
    """
    NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        wb_el = ET.fromstring(z.read("xl/workbook.xml"))
        sheet_el = wb_el.find(f".//{{{NS}}}sheets/{{{NS}}}sheet")
        if sheet_el is None:
            raise ValueError("工作簿中没有工作表")
        rid = sheet_el.get(f"{{{R_NS}}}id")
        sheet_name = sheet_el.get("name", "Sheet1")
        # 通过 rels 定位工作表 XML 路径
        target = None
        if "xl/_rels/workbook.xml.rels" in names:
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels:
                if rel.get("Id") == rid:
                    target = rel.get("Target")
                    break
        if not target:
            sheets = sorted(n for n in names
                            if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
            target = sheets[0] if sheets else None
        if not target:
            raise ValueError("无法定位工作表内容")
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("xl/"):
            target = posixpath.normpath(posixpath.join("xl", target))
        # 共享字符串表
        sst = []
        if "xl/sharedStrings.xml" in names:
            ss_el = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in ss_el.findall(f"{{{NS}}}si"):
                sst.append(_xlsx_cell_text(si, NS, "t"))
        # 逐行解析单元格
        sheet_el2 = ET.fromstring(z.read(target))
        rows = []
        for row in sheet_el2.findall(f".//{{{NS}}}sheetData/{{{NS}}}row"):
            cells = {}
            for c in row.findall(f"{{{NS}}}c"):
                ref = c.get("r") or ""
                t = c.get("t") or ""
                v_el = c.find(f"{{{NS}}}v")
                is_el = c.find(f"{{{NS}}}is")
                if t == "s" and v_el is not None:          # 共享字符串索引
                    try:
                        val = sst[int(v_el.text)]
                    except (ValueError, IndexError):
                        val = ""
                elif t == "inlineStr" and is_el is not None:  # 内联字符串
                    val = _xlsx_cell_text(is_el, NS, "t")
                elif v_el is not None:                     # 数值 / 普通文本
                    val = v_el.text or ""
                else:
                    val = ""
                idx = _xlsx_col_index(ref) if ref else len(cells)
                cells[idx] = val
            if cells:
                maxc = max(cells) + 1
                rows.append([cells.get(i, "") for i in range(maxc)])
        return sheet_name, rows


def read_table_file(path):
    """按扩展名读取表格文件 -> (表名, 行列表)；CSV 自动尝试 utf-8-sig / gbk 编码"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        return read_xlsx(path)
    if ext in (".csv", ".txt"):
        last_err = None
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                with open(path, "r", encoding=enc, newline="") as f:
                    return os.path.basename(path), [r for r in csv.reader(f)]
            except UnicodeDecodeError as e:
                last_err = e
        raise ValueError(f"无法识别文件编码：{last_err}")
    raise ValueError("仅支持 .xlsx / .csv 文件")


def import_basic(store, rows):
    """
    基础档案导入（学号 + 姓名）：
    - 学号为唯一主键：已存在则更新姓名，不存在则新增（其余字段留空）
    - 自动跳过空行
    - 校验：学号不能为空 / 格式非法 / 文件内重复 -> 均标记错误行，不写入
    返回报告：{success, added, updated, errors:[(Excel行号, 原因)]}
    """
    report = {"success": 0, "added": 0, "updated": 0, "errors": []}
    if not rows:
        report["errors"].append((0, "文件为空，未读取到任何行"))
        return report
    sid_col = _find_col(rows[0], {"学号", "学号*", "student_id", "StudentID", "ID", "id"})
    name_col = _find_col(rows[0], {"姓名", "姓名*", "name", "Name"})
    if sid_col < 0:
        report["errors"].append((1, "未找到「学号」列，请使用系统模板或下载模板"))
        return report
    persons = store.data["persons"]
    # 第一遍：统计文件内学号出现次数，用于查重
    seen = {}
    for ln, row in enumerate(rows[1:], 2):
        if _is_blank_row(row):
            continue
        sid = str(row[sid_col]).strip() if sid_col < len(row) else ""
        seen[sid] = seen.get(sid, 0) + 1
    dup = {s for s, n in seen.items() if n > 1}
    # 第二遍：校验并写入
    for ln, row in enumerate(rows[1:], 2):
        if _is_blank_row(row):
            continue                                   # 自动跳过空行
        sid = str(row[sid_col]).strip() if sid_col < len(row) else ""
        name = str(row[name_col]).strip() if name_col >= 0 and name_col < len(row) else ""
        if not sid:
            report["errors"].append((ln, "学号不能为空"))
            continue
        if not valid_student_id(sid):
            report["errors"].append((ln, f"学号「{sid}」格式非法"
                                         "（仅允许字母/数字/下划线/连字符，长度≤30）"))
            continue
        if sid in dup:
            report["errors"].append((ln, f"学号「{sid}」在文件内重复"))
            continue
        if not name and sid not in persons:
            report["errors"].append((ln, "新增人员姓名不能为空"))
            continue
        if sid in persons:
            persons[sid]["name"] = name or persons[sid].get("name", "")
            report["updated"] += 1
        else:
            persons[sid] = {"name": name, "gender": "", "grade": "", "phone": ""}
            report["added"] += 1
        report["success"] += 1
    return report


def import_signin(store, activity, date, rows):
    """
    活动签到导入（序号 + 学号 + 姓名）：
    - 不修改基础档案，只新增活动参与记录（姓名以系统档案为准）
    - 校验：学号非空 / 格式非法 / 文件内重复 / 系统中不存在 / 重复签到 -> 标记错误
    返回报告：{success, added, updated, errors:[(Excel行号, 原因)]}
    """
    report = {"success": 0, "added": 0, "updated": 0, "errors": []}
    if not activity.strip():
        report["errors"].append((0, "活动名称不能为空"))
        return report
    if not rows:
        report["errors"].append((0, "文件为空，未读取到任何行"))
        return report
    sid_col = _find_col(rows[0], {"学号", "学号*", "student_id", "StudentID", "ID", "id"})
    if sid_col < 0:
        report["errors"].append((1, "未找到「学号」列，请使用系统模板或下载模板"))
        return report
    persons = store.data["persons"]
    signins = store.data.setdefault("signins", [])
    act = activity.strip()
    date = str(date or "").strip()
    if date:
        try:
            validate_schedule(date, "")
        except ValueError as error:
            report["errors"].append((0, str(error)))
            return report
    # 判重键：活动 + 学号 + 日期（同一活动分多天举办时允许分别签到）
    exist = {(r.get("activity"), r.get("pid"), r.get("date")) for r in signins}
    seen = {}
    for ln, row in enumerate(rows[1:], 2):
        if _is_blank_row(row):
            continue
        sid = str(row[sid_col]).strip() if sid_col < len(row) else ""
        if sid:
            seen[sid] = seen.get(sid, 0) + 1
    dup = {s for s, n in seen.items() if n > 1}
    for ln, row in enumerate(rows[1:], 2):
        if _is_blank_row(row):
            continue                                   # 自动跳过空行
        sid = str(row[sid_col]).strip() if sid_col < len(row) else ""
        if not sid:
            report["errors"].append((ln, "学号不能为空"))
            continue
        if not valid_student_id(sid):
            report["errors"].append((ln, f"学号「{sid}」格式非法"
                                         "（仅允许字母/数字/下划线/连字符，长度≤30）"))
            continue
        if sid in dup:
            report["errors"].append((ln, f"学号「{sid}」在文件内重复"))
            continue
        if sid not in persons:
            report["errors"].append((ln, f"学号「{sid}」系统中不存在，请先导入基础档案"))
            continue
        if (act, sid, date) in exist:
            report["errors"].append((ln, f"学号「{sid}」已在该活动「{act}」{date or ''}签到过"))
            continue
        signins.append({"activity": act, "date": date,
                        "pid": sid, "name": persons[sid].get("name", "")})
        exist.add((act, sid, date))
        report["success"] += 1
        report["added"] += 1
    return report


# ===========================================================================
# 模块 4：通用 UI 组件
# ===========================================================================
def make_tree(parent, columns, widths=None, heights=12, stretch_last=True):
    """创建带纵向滚动条的 Treeview，返回 (tv, scrollbar)"""
    tv = ttk.Treeview(parent, columns=columns, show="headings", height=heights)
    for i, c in enumerate(columns):
        tv.heading(c, text=c)
        w = (widths or {}).get(c, 90)
        tv.column(c, width=w, anchor="center", stretch=(i == len(columns) - 1 and stretch_last))
    vsb = ttk.Scrollbar(parent, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=vsb.set)
    return tv, vsb


def clear_tree(tv):
    for i in tv.get_children():
        tv.delete(i)


def person_choices(store):
    """返回 (学号列表, 学号->显示名) 供下拉框使用"""
    ids = list(store.data["persons"].keys())
    label = {i: f"{i} {store.data['persons'][i].get('name','')}" for i in ids}
    return ids, label


class BasePage(ttk.Frame):
    """所有页面基类"""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.store = app.store

    def refresh(self):
        pass


# ===========================================================================
# 模块 5：人员信息管理页
# ===========================================================================
class PersonDialog:
    """新增/修改人员档案对话框"""

    def __init__(self, master, store, pid=None):
        self.store = store
        self.pid = pid
        self.result = None
        self.top = tk.Toplevel(master)
        self.top.title("修改人员" if pid else "新增人员")
        self.top.transient(master)
        self.top.resizable(False, False)
        self.top.grab_set()

        self.vars = {
            "id": tk.StringVar(), "name": tk.StringVar(),
            "gender": tk.StringVar(value="男"), "grade": tk.StringVar(value="2024级"),
            "phone": tk.StringVar(),
        }
        if pid:
            p = store.data["persons"].get(pid, {})
            self.vars["id"].set(pid)
            self.vars["name"].set(p.get("name", ""))
            self.vars["gender"].set(p.get("gender", "男"))
            self.vars["grade"].set(p.get("grade", ""))
            self.vars["phone"].set(p.get("phone", ""))

        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill="both", expand=True)
        labels = ["学号 *", "姓名 *", "性别", "年级", "联系方式"]
        rows = [
            ttk.Entry(frm, textvariable=self.vars["id"], width=22),
            ttk.Entry(frm, textvariable=self.vars["name"], width=22),
            ttk.Combobox(frm, textvariable=self.vars["gender"], values=GENDERS,
                         state="readonly", width=20),
            ttk.Combobox(frm, textvariable=self.vars["grade"], values=GRADES, width=20),
            ttk.Entry(frm, textvariable=self.vars["phone"], width=22),
        ]
        if pid:  # 学号是主键，修改时不可更改
            rows[0].configure(state="disabled")
        for i, (lab, w) in enumerate(zip(labels, rows)):
            ttk.Label(frm, text=lab).grid(row=i, column=0, sticky="e", padx=4, pady=4)
            w.grid(row=i, column=1, sticky="w", padx=4, pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=len(labels), column=0, columnspan=2, pady=(10, 0))
        ttk.Button(btns, text="保存", command=self.ok).pack(side="left", padx=6)
        ttk.Button(btns, text="取消", command=self.top.destroy).pack(side="left", padx=6)
        self.top.bind("<Return>", lambda e: self.ok())

    def ok(self):
        vals = {k: v.get().strip() for k, v in self.vars.items()}
        if not vals["id"] or not vals["name"]:
            messagebox.showwarning("提示", "学号与姓名不能为空。", parent=self.top)
            return
        if not valid_student_id(vals["id"]):
            messagebox.showwarning("提示", "学号仅允许字母、数字、下划线和连字符，长度不超过30。", parent=self.top)
            return
        if self.pid is None and vals["id"] in self.store.data["persons"]:
            messagebox.showwarning("提示", "该学号已存在，禁止重复录入。", parent=self.top)
            return
        self.store.data["persons"][vals["id"]] = {
            "name": vals["name"], "gender": vals["gender"],
            "grade": vals["grade"], "phone": vals["phone"],
        }
        self.store.save()
        self.result = vals["id"]
        self.top.destroy()


class PersonPage(BasePage):
    """人员信息管理：新增/修改/删除/按学号或姓名查询"""

    def __init__(self, master, app):
        super().__init__(master, app)
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="查询(学号/姓名):").pack(side="left")
        self.q_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.q_var, width=20).pack(side="left", padx=4)
        ttk.Button(top, text="查询", command=self.refresh).pack(side="left", padx=2)
        ttk.Button(top, text="显示全部",
                   command=lambda: (self.q_var.set(""), self.refresh())).pack(side="left", padx=2)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(top, text="批量导入", command=self.bulk_import).pack(side="right", padx=2)
        ttk.Button(top, text="新增", command=self.add).pack(side="right", padx=2)
        ttk.Button(top, text="修改", command=self.edit).pack(side="right", padx=2)
        ttk.Button(top, text="删除", command=self.delete).pack(side="right", padx=2)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.tv, vsb = make_tree(body, ["学号", "姓名", "性别", "年级", "联系方式"],
                                 {"学号": 140, "姓名": 120, "性别": 70,
                                  "年级": 90, "联系方式": 200}, heights=18)
        self.tv.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tv.bind("<Double-1>", lambda e: self.edit())
        CtrlDragSelection(self.tv)
        self.q_var.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        clear_tree(self.tv)
        kw = self.q_var.get().strip()
        persons = self.store.data["persons"]
        for pid, p in persons.items():
            if kw and kw not in pid and kw not in p.get("name", ""):
                continue
            self.tv.insert("", "end", iid=pid, values=(
                pid, p.get("name", ""), p.get("gender", ""),
                p.get("grade", ""), p.get("phone", "")))

    def _selected(self):
        sel = self.tv.selection()
        return sel[0] if sel else None

    def bulk_import(self):
        """打开批量导入对话框，完成后刷新列表"""
        dlg = ImportDialog(self, self.app)
        self.wait_window(dlg.top)
        self.refresh()

    def add(self):
        dlg = PersonDialog(self, self.store)
        self.wait_window(dlg.top)
        if dlg.result:
            self.refresh()

    def edit(self):
        if len(self.tv.selection()) > 1:
            messagebox.showinfo("提示", "修改档案时请只选择一名人员。", parent=self)
            return
        pid = self._selected()
        if not pid:
            messagebox.showinfo("提示", "请先选择要修改的人员。", parent=self)
            return
        dlg = PersonDialog(self, self.store, pid=pid)
        self.wait_window(dlg.top)
        self.refresh()

    def delete(self):
        selected = self.tv.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择要删除的人员。", parent=self)
            return
        names = [f"{self.store.data['persons'].get(pid, {}).get('name', '')}（{pid}）" for pid in selected]
        preview = "\n".join(names[:10])
        if len(names) > 10:
            preview += f"\n……另有 {len(names) - 10} 人"
        if messagebox.askyesno("确认删除",
                               f"确定删除选中的 {len(selected)} 人吗？\n{preview}\n\n他们的队伍成员关系、报名、成绩等关联数据将一并清除。",
                               parent=self):
            try:
                self.store.remove_persons(selected)
            except Exception as error:
                messagebox.showerror("删除失败", f"未能保存删除结果，已恢复人员数据。\n{error}", parent=self)
                return
            self.refresh()


class ReportDialog:
    """批量导入结果报告对话框：成功/新增/更新/错误统计 + 错误行明细"""

    def __init__(self, master, report):
        self.top = tk.Toplevel(master)
        self.top.title("导入结果报告")
        self.top.transient(master)
        self.top.grab_set()
        self.top.geometry("540x430")
        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill="both", expand=True)
        err_n = len(report["errors"])
        ttk.Label(frm, text=(
            f"导入完成：成功 {report['success']} 条"
            f"（新增 {report['added']} 条 · 更新 {report['updated']} 条）"
            f"，错误 {err_n} 条"),
            font=("", 11, "bold"), foreground="#1a5276").pack(anchor="w")
        if err_n:
            ttk.Label(frm, text="错误明细（Excel 行号 + 原因）：",
                      foreground="#c0392b").pack(anchor="w", pady=(10, 2))
            txt = tk.Text(frm, height=15, wrap="none")
            vsb = ttk.Scrollbar(frm, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=vsb.set)
            txt.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")
            for ln, reason in report["errors"]:
                txt.insert("end", f"第 {ln} 行：{reason}\n")
            txt.configure(state="disabled")
        ttk.Button(frm, text="关闭", command=self.top.destroy).pack(pady=(12, 0))


class ImportDialog:
    """批量导入对话框：支持【基础档案导入】与【活动签到导入】两种模式"""

    def __init__(self, master, app):
        self.app = app
        self.store = app.store
        self.top = tk.Toplevel(master)
        self.top.title("批量导入信息")
        self.top.transient(master)
        self.top.grab_set()
        self.top.resizable(False, False)
        self.path = ""
        self.kind = tk.StringVar(value="基础档案导入（学号+姓名）")
        self.activity_var = tk.StringVar()
        self.date_var = tk.StringVar(value=datetime.date.today().strftime("%Y-%m-%d"))

        frm = ttk.Frame(self.top, padding=14)
        frm.pack()
        ttk.Label(frm, text="导入类型:").grid(row=0, column=0, sticky="e", pady=4)
        ttk.Combobox(frm, textvariable=self.kind, state="readonly", width=28,
                     values=["基础档案导入（学号+姓名）",
                             "活动签到导入（序号+学号+姓名）"],
                     ).grid(row=0, column=1, sticky="w", padx=6)
        self.kind.trace_add("write", lambda *a: self._kind_changed())

        self.act_lb = ttk.Label(frm, text="活动名称 *:")
        self.act_entry = ttk.Entry(frm, textvariable=self.activity_var, width=24)
        self.act_lb.grid(row=1, column=0, sticky="e", pady=4)
        self.act_entry.grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(frm, text="签到日期:").grid(row=2, column=0, sticky="e", pady=4)
        ttk.Entry(frm, textvariable=self.date_var, width=24).grid(row=2, column=1, sticky="w", padx=6)

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, pady=(8, 4))
        ttk.Button(btns, text="选择文件…", command=self._pick_file).pack(side="left", padx=4)
        ttk.Button(btns, text="下载模板", command=self._download_template).pack(side="left", padx=4)
        ttk.Button(btns, text="开始导入", command=self._do_import).pack(side="left", padx=4)

        self.file_lb = ttk.Label(frm, text="尚未选择文件", foreground="#999")
        self.file_lb.grid(row=4, column=0, columnspan=2, pady=2)
        ttk.Label(frm, text=IMPORT_HINTS + " · 学号为空/格式非法/重复均标记为错误行",
                  foreground="#888").grid(row=5, column=0, columnspan=2, pady=(6, 0))
        self._kind_changed()

    def _kind_changed(self):
        """切换导入类型：仅活动签到需要填写活动名称与日期"""
        is_signin = self.kind.get().startswith("活动签到")
        state = "normal" if is_signin else "disabled"
        self.act_lb.configure(state=state)
        self.act_entry.configure(state=state)

    def _pick_file(self):
        p = filedialog.askopenfilename(
            parent=self.top, title="选择导入文件",
            filetypes=[("表格文件", "*.xlsx *.csv"), ("Excel 文件", "*.xlsx"),
                       ("CSV 文件", "*.csv"), ("所有文件", "*.*")])
        if p:
            self.path = p
            self.file_lb.configure(text=f"已选择：{os.path.basename(p)}", foreground="#1a5276")

    def _download_template(self):
        """下载当前类型的导入模板（仅含表头，示例留空）"""
        if self.kind.get().startswith("基础"):
            sheets = {"基础档案导入模板": [["学号", "姓名"], []]}
        else:
            sheets = {"活动签到导入模板": [["序号", "学号", "姓名"], ["1", "", ""]]}
        do_export(self.top, "导入模板", sheets)

    def _do_import(self):
        if not self.path:
            messagebox.showwarning("提示", "请先选择导入文件。", parent=self.top)
            return
        is_signin = self.kind.get().startswith("活动签到")
        if is_signin and not self.activity_var.get().strip():
            messagebox.showwarning("提示", "请填写活动名称。", parent=self.top)
            return
        try:
            _, rows = read_table_file(self.path)
        except Exception as e:
            messagebox.showerror("读取失败", f"无法读取文件：\n{e}", parent=self.top)
            return
        if is_signin:
            report = import_signin(self.store, self.activity_var.get(),
                                   self.date_var.get().strip() or
                                   datetime.date.today().strftime("%Y-%m-%d"), rows)
        else:
            report = import_basic(self.store, rows)
        self.store.save()
        ReportDialog(self.top, report)
        # 刷新全部页面，避免硬编码索引遗漏
        for _, page in self.app.pages:
            page.refresh()


# ===========================================================================
# 模块 6：交运杯团体赛管理页
# ===========================================================================
class TeamDialog:
    """新建/编辑队伍：队名、领队、队员列表（A/B 档标注）"""

    def __init__(self, master, app, team_name=None):
        self.store = app.store
        self.team_name = team_name
        self.top = tk.Toplevel(master)
        self.top.title("编辑队伍" if team_name else "新建队伍")
        self.top.transient(master)
        self.top.grab_set()
        self.top.geometry("820x560")

        self.name_var = tk.StringVar(value=team_name or "")
        leader_ids, self.leader_label = person_choices(self.store)
        self.leader_var = tk.StringVar()
        if team_name:
            ldr = self.store.data["teams"][team_name].get("leader", "")
            if ldr in self.leader_label:
                self.leader_var.set(ldr)
        self.members = {}   # 学号 -> "A"/"B"

        frm = ttk.Frame(self.top, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="队伍名称 *").grid(row=0, column=0, sticky="e", pady=4)
        ttk.Entry(frm, textvariable=self.name_var, width=24).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="领队").grid(row=0, column=2, sticky="e", pady=4, padx=(16, 0))
        ttk.Combobox(frm, textvariable=self.leader_var, values=leader_ids,
                     state="readonly", width=18).grid(row=0, column=3, sticky="w", padx=4)

        # 队员录入区
        pick = ttk.Frame(frm)
        pick.grid(row=1, column=0, columnspan=4, sticky="we", pady=6)
        ttk.Label(pick, text="新增成员档位:").pack(side="left")
        self.lv_var = tk.StringVar(value="A档")
        ttk.Combobox(pick, textvariable=self.lv_var, values=["A档", "B档"],
                     state="readonly", width=6).pack(side="left", padx=4)
        ttk.Button(pick, text="多选添加成员", command=self.add_member).pack(side="left", padx=4)
        ttk.Button(pick, text="移除所选", command=self.remove_member).pack(side="left", padx=4)
        ttk.Button(pick, text="切换所选档位", command=self.toggle_level).pack(side="left", padx=4)
        ttk.Button(pick, text="全选队内成员", command=lambda: self.tv.selection_set(self.tv.get_children())).pack(side="left", padx=4)

        self.tv, vsb = make_tree(frm, ["学号", "姓名", "档位"],
                                 {"学号": 160, "姓名": 140, "档位": 90}, heights=10)
        self.tv.grid(row=2, column=0, columnspan=4, sticky="nsew", pady=4)
        vsb.grid(row=2, column=4, sticky="ns")
        frm.rowconfigure(2, weight=1)
        self.tv.bind("<Double-1>", lambda e: self.toggle_level())
        self.tv.configure(selectmode="extended")
        self.tv.bind("<Control-a>", lambda e: (self.tv.selection_set(self.tv.get_children()), "break")[-1])

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=5, pady=(8, 0))
        ttk.Button(btns, text="保存", command=self.ok).pack(side="left", padx=8)
        ttk.Button(btns, text="取消", command=self.top.destroy).pack(side="left", padx=8)

        if team_name:
            self.members = dict(self.store.data["teams"][team_name].get("members", {}))
        self._refresh_members()

    def add_member(self):
        picker = MemberPicker(self.top, self.store.data["persons"], exclude=self.members)
        self.top.wait_window(picker.top)
        self.top.grab_set()
        if picker.result:
            level = "A" if self.lv_var.get() == "A档" else "B"
            self.members.update({pid: level for pid in picker.result})
            self._refresh_members()

    def remove_member(self):
        sel = self.tv.selection()
        if not sel:
            return
        for pid in sel:
            self.members.pop(pid, None)
        self._refresh_members()

    def toggle_level(self):
        sel = self.tv.selection()
        if not sel:
            return
        for pid in sel:
            cur = self.members.get(pid, "A")
            self.members[pid] = "B" if cur == "A" else "A"
        self._refresh_members()

    def _refresh_members(self):
        clear_tree(self.tv)
        for pid, lv in self.members.items():
            p = self.store.data["persons"].get(pid, {})
            self.tv.insert("", "end", iid=pid,
                           values=(pid, p.get("name", "?"), lv + "档"))

    def ok(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("提示", "队伍名称不能为空。", parent=self.top)
            return
        if self.team_name is None and name in self.store.data["teams"]:
            messagebox.showwarning("提示", "队伍名称已存在。", parent=self.top)
            return
        # 八人制规则提示：队员数不足 8 人时提醒（允许确认后继续，便于筹备期使用）
        n_members = len(self.members)
        if n_members and n_members < 8:
            if not messagebox.askyesno(
                    "人数提示",
                    f"当前队伍仅 {n_members} 人，不足八人制首发 8 人。\n仍要保存吗？",
                    parent=self.top):
                return
        teams = self.store.data["teams"]
        if name != self.team_name and name in teams:
            messagebox.showwarning("提示", "该队名已存在，请使用其他名称。", parent=self.top)
            return
        if self.team_name and self.team_name != name:
            # 改名时同步比赛与奖项引用
            for m in self.store.data["matches"]:
                if m["team1"] == self.team_name:
                    m["team1"] = name
                if m["team2"] == self.team_name:
                    m["team2"] = name
            for k in ("champion", "runner_up", "third"):
                if self.store.data["awards"]["team"].get(k) == self.team_name:
                    self.store.data["awards"]["team"][k] = name
        leader = self.leader_var.get() or ""
        teams[name] = {"leader": leader, "members": self.members}
        if self.team_name and self.team_name != name:
            teams.pop(self.team_name, None)
        self.store.save()
        self.top.destroy()


class MatchDialog:
    """录入/修改比赛结果：对阵双方、日期、比分、双方出场名单"""

    def __init__(self, master, app, match=None):
        self.store = app.store
        self.match = match
        self.top = tk.Toplevel(master)
        self.top.title("修改比赛" if match else "录入比赛")
        self.top.transient(master)
        self.top.grab_set()
        self.top.geometry("960x620")
        self.top.minsize(900, 580)

        team_names = list(self.store.data["teams"].keys())
        self.t1_var = tk.StringVar()
        self.t2_var = tk.StringVar()
        self.date_var = tk.StringVar(value=now_str())
        self.g1_var = tk.StringVar()
        self.g2_var = tk.StringVar()
        self.status_var = tk.StringVar(value=match_status(match) if match else "未开始")
        self.time_var = tk.StringVar(value=(match or {}).get("time", ""))
        self.venue_var = tk.StringVar(value=(match or {}).get("venue", ""))
        self.notes_var = tk.StringVar(value=(match or {}).get("notes", ""))
        self.lineup1, self.lineup2 = [], []   # 上场名单（学号列表）
        self._lineup_ids = {}                  # {l1: 已勾选上场学号集合, l2: ...}
        self._lineup_sel = {}                  # {(key, 队名): 该队勾选集合}，切换队伍时保留各自勾选

        frm = ttk.Frame(self.top, padding=10)
        frm.pack(fill="both", expand=True)
        row0 = ttk.Frame(frm)
        row0.pack(fill="x")
        ttk.Label(row0, text="主队 *").pack(side="left")
        c1 = ttk.Combobox(row0, textvariable=self.t1_var, values=team_names,
                          state="readonly", width=16)
        c1.pack(side="left", padx=4)
        ttk.Label(row0, text="客队 *").pack(side="left", padx=(12, 0))
        c2 = ttk.Combobox(row0, textvariable=self.t2_var, values=team_names,
                          state="readonly", width=16)
        c2.pack(side="left", padx=4)
        ttk.Label(row0, text="日期").pack(side="left", padx=(12, 0))
        ttk.Entry(row0, textvariable=self.date_var, width=12).pack(side="left", padx=4)
        ttk.Label(row0, text="比分").pack(side="left", padx=(12, 0))
        ttk.Entry(row0, textvariable=self.g1_var, width=5).pack(side="left")
        ttk.Label(row0, text=":").pack(side="left")
        ttk.Entry(row0, textvariable=self.g2_var, width=5).pack(side="left")

        meta = ttk.Frame(frm)
        meta.pack(fill="x", pady=12)
        ttk.Label(meta, text="赛况状态").pack(side="left")
        ttk.Combobox(meta, textvariable=self.status_var, values=STATUSES, state="readonly", width=10).pack(side="left", padx=6)
        ttk.Label(meta, text="开球 HH:MM").pack(side="left", padx=(12, 0))
        ttk.Entry(meta, textvariable=self.time_var, width=8).pack(side="left", padx=6)
        ttk.Label(meta, text="场地").pack(side="left", padx=(12, 0))
        ttk.Entry(meta, textvariable=self.venue_var, width=28).pack(side="left", padx=6)
        ttk.Label(frm, text="日期格式 YYYY-MM-DD；未开始 / 延期 / 取消不记比分，进行中比分不计入正式积分。", style="Muted.TLabel").pack(anchor="w")
        note_row = ttk.Frame(frm)
        note_row.pack(fill="x", pady=8)
        ttk.Label(note_row, text="赛况备注").pack(side="left")
        ttk.Entry(note_row, textvariable=self.notes_var).pack(side="left", fill="x", expand=True, padx=6)

        ttk.Label(frm, text="出场名单：Ctrl / Shift 多选后批量标记；✓ 表示实际出场，蓝色选中仅用于操作。",
                  foreground="#666").pack(anchor="w", pady=(8, 2))
        lists = ttk.Frame(frm)
        lists.pack(fill="both", expand=True)
        lists.rowconfigure(0, weight=1)
        for col, (tv_name, var, lv) in enumerate(((c1, self.t1_var, "l1"), (c2, self.t2_var, "l2"))):
            box = ttk.LabelFrame(lists, text="")
            box.grid(row=0, column=col, sticky="nsew", padx=6)
            lists.columnconfigure(col, weight=1)
            toolbar = ttk.Frame(box)
            toolbar.pack(side="top", fill="x")
            for index, (text, action) in enumerate((("所选上场", "add"), ("所选取消", "remove"), ("全员上场", "all"), ("清空名单", "clear"))):
                ttk.Button(toolbar, text=text, command=lambda k=lv, a=action: self._batch_lineup(k, a)).grid(row=index // 2, column=index % 2, sticky="ew", padx=2, pady=2)
            toolbar.columnconfigure((0, 1), weight=1)
            lb = tk.Listbox(box, height=10, exportselection=False, selectmode="extended")
            lb.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
            sb = ttk.Scrollbar(box, orient="vertical", command=lb.yview)
            lb.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y", pady=4)
            lb.bind("<Double-1>", lambda e, _lb=lb, _v=var: self._toggle(_lb, _v))
            lb.bind("<space>", lambda e, _lb=lb, _v=var: self._toggle(_lb, _v))
            lb.bind("<Control-a>", lambda e, _lb=lb: (_lb.selection_set(0, "end"), "break")[-1])
            setattr(self, f"{lv}_listbox", lb)
            setattr(self, f"{lv}_box", box)

        if match:
            m = match
            self.t1_var.set(m["team1"]); self.t2_var.set(m["team2"])
            self.date_var.set(m.get("date", ""))
            self.g1_var.set("" if m.get("g1") is None else m["g1"])
            self.g2_var.set("" if m.get("g2") is None else m["g2"])
            self.lineup1 = list(m.get("lineup1", []))
            self.lineup2 = list(m.get("lineup2", []))
        self._reload_lineups()
        c1.bind("<<ComboboxSelected>>", lambda e: self._reload_lineups())
        c2.bind("<<ComboboxSelected>>", lambda e: self._reload_lineups())

        btns = ttk.Frame(frm)
        btns.pack(pady=(8, 0))
        ttk.Button(btns, text="保存", command=self.ok).pack(side="left", padx=8)
        ttk.Button(btns, text="取消", command=self.top.destroy).pack(side="left", padx=8)
        self.top.bind("<Control-Return>", lambda _: self.ok())
        self.top.bind("<Escape>", lambda _: self.top.destroy())

    def _reload_lineups(self):
        teams = self.store.data["teams"]
        for key, var, default in (("l1", self.t1_var, self.lineup1),
                                  ("l2", self.t2_var, self.lineup2)):
            lb = getattr(self, f"{key}_listbox")
            box = getattr(self, f"{key}_box")
            t = var.get()
            box.configure(text=f"{t} 出场名单")
            lb.delete(0, "end")
            if not t:
                self._lineup_ids[key] = set()
                continue
            mems = teams.get(t, {}).get("members", {})
            # 勾选状态按「队伍」分别保存：切换队伍不再串用其他队伍的名单（修复名单继承 bug）
            sel_key = (key, t)
            if sel_key in self._lineup_sel:
                selected = set(self._lineup_sel[sel_key])
            else:
                # 首次加载该队伍：仅保留旧名单中仍属于该队的成员
                selected = set(i for i in default if i in mems)
                self._lineup_sel[sel_key] = set(selected)
            self._lineup_ids[key] = selected
            box.configure(text=f"{t} · 已标记上场 {len(selected)} / {len(mems)} 人")
            for pid in mems:
                p = self.store.data["persons"].get(pid, {})
                mark = "✓ " if pid in selected else "  "
                lb.insert("end", f"{mark}{pid} {p.get('name','')}")

    def _batch_lineup(self, key, action):
        team = (self.t1_var if key == "l1" else self.t2_var).get()
        ids = list(self.store.data["teams"].get(team, {}).get("members", {}))
        lb = getattr(self, f"{key}_listbox")
        selected = set(self._lineup_ids.get(key, set()))
        picked = {ids[index] for index in lb.curselection()}
        if action == "all":
            selected = set(ids)
        elif action == "clear":
            selected.clear()
        elif action == "add":
            selected.update(picked)
        elif action == "remove":
            selected.difference_update(picked)
        elif action == "toggle":
            selected.symmetric_difference_update(picked)
        self._lineup_sel[(key, team)] = selected
        self._reload_lineups()

    def _toggle(self, lb, var):
        key = "l1" if var is self.t1_var else "l2"
        self._batch_lineup(key, "toggle")
        return "break"

    def ok(self):
        t1, t2 = self.t1_var.get(), self.t2_var.get()
        if not t1 or not t2:
            messagebox.showwarning("提示", "请选择对阵双方。", parent=self.top)
            return
        if t1 == t2:
            messagebox.showwarning("提示", "对阵双方不能是同一支队伍。", parent=self.top)
            return
        try:
            validate_schedule(self.date_var.get().strip(), self.time_var.get().strip())
            if self.status_var.get() in ("已结束", "进行中"):
                g1v = parse_num(self.g1_var.get(), "主队比分")
                g2v = parse_num(self.g2_var.get(), "客队比分")
                if not g1v.is_integer() or not g2v.is_integer():
                    raise ValueError("比分必须是整数（不允许小数）")
                g1, g2 = int(g1v), int(g2v)
            else:
                g1 = g2 = None
        except ValueError as e:
            messagebox.showwarning("提示", str(e), parent=self.top)
            return
        lineup1 = sorted(getattr(self, "_lineup_ids", {}).get("l1", []))
        lineup2 = sorted(getattr(self, "_lineup_ids", {}).get("l2", []))
        # 八人制规则提示：上场名单非空且不足 8 人时提醒（允许确认后继续，便于补录阶段使用）
        for t, lu, side in ((t1, lineup1, "主队"), (t2, lineup2, "客队")):
            if self.status_var.get() == "已结束" and lu and len(lu) != 8:
                if not messagebox.askyesno(
                        "人数提示",
                        f"{side}「{t}」当前上场名单 {len(lu)} 人（八人制建议 8 人）。\n仍要保存吗？",
                        parent=self.top):
                    return
        rec = {**(self.match or {}), "id": self.match["id"] if self.match else self.store.next_match_id(),
               "team1": t1, "team2": t2, "date": self.date_var.get().strip(),
               "g1": g1, "g2": g2,
               "lineup1": lineup1, "lineup2": lineup2,
               "status": self.status_var.get(), "time": self.time_var.get().strip(),
               "venue": self.venue_var.get().strip(), "notes": self.notes_var.get().strip()}
        previous_stats = (self.match or {}).get("player_stats", {})
        removed = set(previous_stats) - set(lineup1 + lineup2)
        if removed and not messagebox.askyesno("调整出场名单", "移除的球员已有表现记录，保存将同时移除这些记录。是否继续？", parent=self.top):
            return
        rec["player_stats"] = {pid: stat for pid, stat in previous_stats.items() if pid in lineup1 + lineup2}
        if g1 is not None:
            for lineup, goals in ((lineup1, g1), (lineup2, g2)):
                if sum(rec["player_stats"].get(pid, {}).get("goals", 0) for pid in lineup) > goals:
                    messagebox.showwarning("比分冲突", "球员进球总数超过新的球队比分，请先修正球员表现。", parent=self.top)
                    return
        matches = self.store.data["matches"]
        if self.match:
            for i, m in enumerate(matches):
                if m.get("id") == rec["id"]:
                    matches[i] = rec
                    break
        else:
            # 同两队重复比赛提示（单循环赛制下每两队应只交手一次，允许确认后继续）
            dup = any({m.get("team1"), m.get("team2")} == {t1, t2} for m in matches)
            if dup and not messagebox.askyesno(
                    "重复提示",
                    f"「{t1}」与「{t2}」已有比赛记录（单循环赛制下通常只交手一次）。\n仍要新增吗？",
                    parent=self.top):
                return
            matches.append(rec)
        self.store.save()
        self.top.destroy()


class TeamPage(BasePage):
    """交运杯：队伍管理 / 比赛管理 / 积分榜 / 奖项记录"""

    def __init__(self, master, app):
        super().__init__(master, app)
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=6)
        self.tab_teams = ttk.Frame(nb)
        self.tab_matches = ttk.Frame(nb)
        self.tab_stand = ttk.Frame(nb)
        self.tab_awards = ttk.Frame(nb)
        nb.add(self.tab_teams, text=" 队伍管理 ")
        nb.add(self.tab_matches, text=" 比赛管理 ")
        nb.add(self.tab_stand, text=" 积分榜 ")
        nb.add(self.tab_awards, text=" 奖项记录 ")
        self._build_teams()
        self._build_matches()
        self._build_standings()
        self._build_awards()

    # ---------- 队伍管理 ----------
    def _build_teams(self):
        bar = ttk.Frame(self.tab_teams)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="新建队伍", command=self.add_team).pack(side="left", padx=2)
        ttk.Button(bar, text="编辑队伍", command=self.edit_team).pack(side="left", padx=2)
        ttk.Button(bar, text="删除队伍", command=self.del_team).pack(side="left", padx=2)
        ttk.Button(bar, text="刷新", command=self.refresh_teams).pack(side="right", padx=2)
        body = ttk.Frame(self.tab_teams)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.tv_teams, vsb = make_tree(body, ["队伍", "领队", "A档人数", "B档人数", "总人数"],
                                       {"队伍": 180, "领队": 140, "A档人数": 90,
                                        "B档人数": 90, "总人数": 90}, heights=16)
        self.tv_teams.pack(side="left", fill="both", expand=True)
        self.tv_teams.configure(selectmode="browse")
        vsb.pack(side="right", fill="y")
        self.tv_teams.bind("<Double-1>", lambda e: self.edit_team())
        self.refresh_teams()

    def refresh_teams(self):
        clear_tree(self.tv_teams)
        for t, info in self.store.data["teams"].items():
            mems = info.get("members", {})
            na = sum(1 for lv in mems.values() if lv == "A")
            nb_ = sum(1 for lv in mems.values() if lv == "B")
            ldr = info.get("leader", "")
            ldr_name = self.store.data["persons"].get(ldr, {}).get("name", ldr)
            self.tv_teams.insert("", "end", iid=t,
                                 values=(t, ldr_name, na, nb_, len(mems)))

    def add_team(self):
        dlg = TeamDialog(self, self.app)
        self.wait_window(dlg.top)
        self.refresh_teams()

    def edit_team(self):
        sel = self.tv_teams.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择队伍。", parent=self)
            return
        dlg = TeamDialog(self, self.app, team_name=sel[0])
        self.wait_window(dlg.top)
        self.refresh_teams()

    def del_team(self):
        sel = self.tv_teams.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择队伍。", parent=self)
            return
        if not messagebox.askyesno("确认删除", f"删除队伍「{sel[0]}」？\n其相关比赛记录将一并删除。", parent=self):
            return
        self.store.data["matches"] = [m for m in self.store.data["matches"]
                                      if m.get("team1") != sel[0] and m.get("team2") != sel[0]]
        aw = self.store.data["awards"]["team"]
        for k in ("champion", "runner_up", "third"):
            if aw.get(k) == sel[0]:
                aw[k] = ""
        self.store.data["teams"].pop(sel[0], None)
        self.store.save()
        self.refresh_teams()
        self.refresh_matches()
        self.refresh_standings()

    # ---------- 比赛管理 ----------
    def _build_matches(self):
        bar = ttk.Frame(self.tab_matches)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="安排 / 录入比赛", command=self.add_match).pack(side="left", padx=2)
        ttk.Button(bar, text="修改比赛", command=self.edit_match).pack(side="left", padx=2)
        ttk.Button(bar, text="删除比赛", command=self.del_match).pack(side="left", padx=2)
        ttk.Button(bar, text="导出比赛明细",
                   command=lambda: do_export(self, "交运杯比赛明细",
                                             match_detail_sheet(self.store.data))).pack(side="left", padx=2)
        ttk.Button(bar, text="刷新", command=self.refresh_matches).pack(side="right", padx=2)
        ttk.Label(bar, text="胜 3 分 / 平 1 分 / 负 0 分",
                  foreground="#888").pack(side="right", padx=8)
        filters = ttk.Frame(self.tab_matches)
        filters.pack(fill="x", padx=10, pady=4)
        ttk.Label(filters, text="搜索队伍 / 日期 / 场地").pack(side="left")
        self.match_query = tk.StringVar()
        ttk.Entry(filters, textvariable=self.match_query, width=26).pack(side="left", padx=6)
        self.match_filter = tk.StringVar(value="全部状态")
        ttk.Combobox(filters, textvariable=self.match_filter, values=("全部状态",) + STATUSES,
                     state="readonly", width=12).pack(side="left", padx=6)
        self.match_count = ttk.Label(filters)
        self.match_count.pack(side="right")
        body = ttk.Frame(self.tab_matches)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.tv_matches, vsb = make_tree(body, ["ID", "日期", "开球", "主队", "比分", "客队", "状态", "场地"],
                                         {"ID": 45, "日期": 110, "开球": 65, "主队": 130,
                                          "比分": 80, "客队": 130, "状态": 80, "场地": 130}, heights=16)
        self.tv_matches.pack(side="left", fill="both", expand=True)
        self.tv_matches.configure(selectmode="browse")
        vsb.pack(side="right", fill="y")
        self.tv_matches.bind("<Double-1>", lambda e: self.edit_match())
        self.match_query.trace_add("write", lambda *_: self.refresh_matches())
        self.match_filter.trace_add("write", lambda *_: self.refresh_matches())
        self.tv_matches.tag_configure("进行中", foreground="#167453")
        self.tv_matches.tag_configure("已取消", foreground="#8b95a3")
        self.refresh_matches()

    def refresh_matches(self):
        clear_tree(self.tv_matches)
        for m in sorted(self.store.data["matches"], key=lambda item: (item.get("date", ""), item.get("time", ""), item["id"])):
            keyword = self.match_query.get().strip().casefold()
            if keyword and keyword not in " ".join(str(m.get(k, "")) for k in ("team1", "team2", "date", "venue")).casefold():
                continue
            if self.match_filter.get() != "全部状态" and match_status(m) != self.match_filter.get():
                continue
            self.tv_matches.insert("", "end", iid=str(m.get("id")),
                                   values=(m.get("id"), m.get("date", ""),
                                           m.get("time", ""), m.get("team1"), score_text(m),
                                           m.get("team2"), match_status(m), m.get("venue", "")), tags=(match_status(m),))
        self.match_count.configure(text=f"显示 {len(self.tv_matches.get_children())} / {len(self.store.data['matches'])} 场")

    def add_match(self):
        if len(self.store.data["teams"]) < 2:
            messagebox.showinfo("提示", "请先在「队伍管理」中新建至少两支队伍。", parent=self)
            return
        dlg = MatchDialog(self, self.app)
        self.wait_window(dlg.top)
        self.refresh_matches()
        self.refresh_standings()

    def edit_match(self):
        sel = self.tv_matches.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择比赛。", parent=self)
            return
        m = next((x for x in self.store.data["matches"]
                  if str(x.get("id")) == sel[0]), None)
        if not m:
            return
        dlg = MatchDialog(self, self.app, match=m)
        self.wait_window(dlg.top)
        self.refresh_matches()
        self.refresh_standings()

    def del_match(self):
        sel = self.tv_matches.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择比赛。", parent=self)
            return
        if not messagebox.askyesno("删除比赛", "将删除该场赛程、赛果与球员表现，并重新计算积分。确定删除？", parent=self):
            return
        self.store.backup()
        self.store.data["matches"] = [m for m in self.store.data["matches"]
                                      if str(m.get("id")) != sel[0]]
        self.store.save()
        self.refresh_matches()
        self.refresh_standings()

    # ---------- 积分榜 ----------
    def _build_standings(self):
        bar = ttk.Frame(self.tab_stand)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="刷新", command=self.refresh_standings).pack(side="left", padx=2)
        ttk.Button(bar, text="导出积分榜",
                   command=lambda: do_export(self, "交运杯积分榜",
                                             team_ranking_sheet(self.store.data))).pack(side="left", padx=2)
        ttk.Label(bar, text="排名规则：积分 → 胜负关系 → 净胜球 → 总进球",
                  foreground="#888").pack(side="right", padx=8)
        body = ttk.Frame(self.tab_stand)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.tv_stand, vsb = make_tree(body, ["名次", "队伍", "场次", "胜", "平", "负",
                                              "进球", "失球", "净胜球", "积分", "奖项"],
                                       {"名次": 60, "队伍": 170, "场次": 55, "胜": 50,
                                        "平": 50, "负": 50, "进球": 60, "失球": 60,
                                        "净胜球": 70, "积分": 60, "奖项": 80}, heights=16)
        self.tv_stand.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.refresh_standings()

    def refresh_standings(self):
        clear_tree(self.tv_stand)
        for r in rank_teams(self.store.data):
            s = r["stats"]
            self.tv_stand.insert("", "end", iid=r["team"], values=(
                r["rank"], r["team"], s["played"], s["win"], s["draw"],
                s["loss"], s["gf"], s["ga"], s["gd"], s["pts"], r["award"]))

    # ---------- 奖项记录 ----------
    def _build_awards(self):
        team_names = list(self.store.data["teams"].keys())
        ids, label = person_choices(self.store)
        frm = ttk.LabelFrame(self.tab_awards, text="奖项记录", padding=14)
        frm.pack(fill="x", padx=12, pady=12)
        self.aw_team_vars = {"champion": tk.StringVar(), "runner_up": tk.StringVar(),
                             "third": tk.StringVar()}
        self.aw_team_boxes = {}
        rows = [("冠军", "champion"), ("亚军", "runner_up"), ("季军", "third")]
        for i, (lab, key) in enumerate(rows):
            ttk.Label(frm, text=f"交运杯 {lab}").grid(row=i, column=0, sticky="e", pady=5)
            box = ttk.Combobox(frm, textvariable=self.aw_team_vars[key], values=team_names,
                              state="readonly", width=18)
            box.grid(row=i, column=1, sticky="w", padx=6)
            self.aw_team_boxes[key] = box
        ttk.Label(frm, text="最佳球员").grid(row=3, column=0, sticky="e", pady=5)
        self.mvp_var = tk.StringVar()
        self.mvp_box = ttk.Combobox(frm, textvariable=self.mvp_var, values=list(label.values()), state="readonly", width=18)
        self.mvp_box.grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(frm, text="金靴奖").grid(row=4, column=0, sticky="e", pady=5)
        self.boot_var = tk.StringVar()
        self.boot_box = ttk.Combobox(frm, textvariable=self.boot_var, values=list(label.values()), state="readonly", width=18)
        self.boot_box.grid(row=4, column=1, sticky="w", padx=6)
        ttk.Button(frm, text="保存奖项", command=self.save_awards).grid(
            row=5, column=0, columnspan=2, pady=(12, 0))
        self.refresh_awards()

    def refresh_awards(self):
        aw = self.store.data["awards"]
        for key, var in self.aw_team_vars.items():
            self.aw_team_boxes[key].configure(values=[""] + list(self.store.data["teams"]))
            var.set(aw["team"].get(key, ""))
        ids, label = person_choices(self.store)
        self.mvp_box.configure(values=[""] + list(label.values()))
        self.boot_box.configure(values=[""] + list(label.values()))
        rev = {v: k for k, v in label.items()}
        self.mvp_var.set(label.get(aw["player"].get("mvp", ""), ""))
        self.boot_var.set(label.get(aw["player"].get("golden_boot", ""), ""))

    def save_awards(self):
        aw = self.store.data["awards"]
        for key, var in self.aw_team_vars.items():
            aw["team"][key] = var.get()
        ids, label = person_choices(self.store)
        rev = {v: k for k, v in label.items()}
        aw["player"]["mvp"] = rev.get(self.mvp_var.get(), "")
        aw["player"]["golden_boot"] = rev.get(self.boot_var.get(), "")
        self.store.save()
        messagebox.showinfo("已保存", "奖项记录已保存。", parent=self)

    def refresh(self):
        self.refresh_teams()
        self.refresh_matches()
        self.refresh_standings()
        self.refresh_awards()


# ===========================================================================
# 模块 7：单项赛管理页（5 个项目统一逻辑）
# ===========================================================================
class OneVOneDialog:
    """1对1 单挑赛：添加对阵记录（选手、比分、轮次）"""

    def __init__(self, master, app, ev):
        self.store = app.store
        self.event = ev
        self.top = tk.Toplevel(master)
        self.top.title("添加对阵记录")
        self.top.transient(master)
        self.top.grab_set()
        self.result = None
        regs = [i for i in ev.get("regs", [])]
        ids, label = person_choices(self.store)
        self.p1_var = tk.StringVar()
        self.p2_var = tk.StringVar()
        self.s1_var = tk.StringVar()
        self.s2_var = tk.StringVar()
        self.round_var = tk.StringVar(value="第一轮")

        frm = ttk.Frame(self.top, padding=14)
        frm.pack()
        # 选手下拉：仅显示本单项已报名人员
        def values_of(except_pid=""):
            vals = []
            for i in regs:
                if i != except_pid and i in label:
                    vals.append(label[i])
            return vals
        self.p1_cb = ttk.Combobox(frm, textvariable=self.p1_var, values=values_of(), width=22)
        self.p2_cb = ttk.Combobox(frm, textvariable=self.p2_var, values=values_of(), width=22)
        ttk.Label(frm, text="选手A").grid(row=0, column=0, sticky="e", pady=4)
        self.p1_cb.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="选手B").grid(row=0, column=2, sticky="e", pady=4, padx=(12, 0))
        self.p2_cb.grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="比分A").grid(row=1, column=0, sticky="e", pady=4)
        ttk.Entry(frm, textvariable=self.s1_var, width=6).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="比分B").grid(row=1, column=2, sticky="e", pady=4, padx=(12, 0))
        ttk.Entry(frm, textvariable=self.s2_var, width=6).grid(row=1, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="轮次").grid(row=2, column=0, sticky="e", pady=4)
        ttk.Entry(frm, textvariable=self.round_var, width=20).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="(如：小组赛/四分之一决赛/决赛)", foreground="#999").grid(
            row=2, column=2, columnspan=2, sticky="w")
        ttk.Label(frm, text="提示：选手A或选手B留空即视为轮空，另一方直接获胜；轮空时比分可留空",
                  foreground="#c0392b").grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=4, pady=(10, 0))
        ttk.Button(btns, text="保存", command=self.ok).pack(side="left", padx=8)
        ttk.Button(btns, text="取消", command=self.top.destroy).pack(side="left", padx=8)
        self.top.bind("<Return>", lambda e: self.ok())

    def ok(self):
        ids, label = person_choices(self.store)
        rev = {v: k for k, v in label.items()}
        p1 = rev.get(self.p1_var.get(), "")
        p2 = rev.get(self.p2_var.get(), "")
        for text, pid in ((self.p1_var.get().strip(), p1), (self.p2_var.get().strip(), p2)):
            if text and (not pid or pid not in self.event["regs"]):
                messagebox.showwarning("提示", "请从本赛事的已报名选手中选择，只有轮空方可以留空。", parent=self.top)
                return
        if not p1 and not p2:
            messagebox.showwarning("提示", "选手A与选手B不能同时为空。", parent=self.top)
            return
        if p1 == p2:
            messagebox.showwarning("提示", "对阵双方不能是同一个人。", parent=self.top)
            return
        try:
            # 比分校验：允许留空（轮空时按 0 计）；非空时必须为非负整数
            if p1 and p2 and (not self.s1_var.get().strip() or not self.s2_var.get().strip()):
                raise ValueError("非轮空对阵必须填写双方比分")
            s1 = s2 = 0
            for vname, var, slot in (("比分A", self.s1_var, 1), ("比分B", self.s2_var, 2)):
                if var.get().strip():
                    v = parse_num(var.get(), vname)
                    if not v.is_integer():
                        raise ValueError(f"{vname}必须是整数（不允许小数）")
                    if slot == 1:
                        s1 = int(v)
                    else:
                        s2 = int(v)
        except ValueError as e:
            messagebox.showwarning("提示", str(e), parent=self.top)
            return
        self.result = {"p1": p1, "p2": p2, "s1": s1, "s2": s2,
                       "round": self.round_var.get().strip() or "第一轮"}
        self.top.destroy()


class EventPage(BasePage):
    """单项系列赛：报名名单 / 成绩录入与晋级 / 对阵管理 / 项目排名"""

    def __init__(self, master, app):
        super().__init__(master, app)
        self.event_name = EVENT_NAMES[0]

        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="当前项目:").pack(side="left")
        self.ev_cb = ttk.Combobox(top, values=list(self.store.data["events"]), state="readonly", width=22)
        self.ev_cb.set(self.event_name)
        self.ev_cb.pack(side="left", padx=6)
        self.ev_cb.bind("<<ComboboxSelected>>", lambda e: self.on_event_change())
        ttk.Button(top, text="添加赛事", command=self.add_event).pack(side="left", padx=6)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)
        self.tab_reg = ttk.Frame(self.nb)
        self.tab_score = ttk.Frame(self.nb)
        self.tab_matches = ttk.Frame(self.nb)
        self.tab_rank = ttk.Frame(self.nb)
        self.nb.add(self.tab_reg, text=" 报名名单 ")
        self.nb.add(self.tab_score, text=" 成绩录入与晋级 ")
        self.nb.add(self.tab_matches, text=" 对阵管理 ")
        self.nb.add(self.tab_rank, text=" 项目排名 ")

        self._build_reg()
        self._build_score()
        self._build_matches()
        self._build_rank()
        self.on_event_change()

    def add_event(self):
        dialog = AddEventDialog(self, self.store)
        self.wait_window(dialog.top)
        if dialog.result:
            self.ev_cb.configure(values=list(self.store.data["events"]))
            self.ev_cb.set(dialog.result)
            self.on_event_change()
            self.nb.select(self.tab_reg)

    # ---------- 报名名单 ----------
    def _build_reg(self):
        bar = ttk.Frame(self.tab_reg)
        bar.pack(fill="x", padx=8, pady=6)
        self.reg_count_lb = ttk.Label(bar, text="")
        self.reg_count_lb.pack(side="left")
        ttk.Label(bar, text="Ctrl 点击或拖动多选 / Shift 连选 / Ctrl+A 全选", style="Muted.TLabel").pack(side="right")
        body = ttk.Frame(self.tab_reg)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.reg_all_lb = tk.Listbox(body, height=14, exportselection=False, selectmode="extended")
        self.reg_sel_lb = tk.Listbox(body, height=14, exportselection=False, selectmode="extended")
        for lb in (self.reg_all_lb, self.reg_sel_lb):
            CtrlDragSelection(lb)
            lb.bind("<Control-a>", lambda e, target=lb: (target.selection_set(0, "end"), "break")[-1])
            sb = ttk.Scrollbar(body, orient="vertical", command=lb.yview)
            lb.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            lb.pack(side="left", fill="both", expand=True, padx=6)
        btns = ttk.Frame(self.tab_reg)
        btns.pack(pady=8)
        ttk.Button(btns, text="全选待报名", command=lambda: self.reg_all_lb.selection_set(0, "end")).pack(side="left", padx=8)
        ttk.Button(btns, text="选中报名 →",
                   command=lambda: self.move_reg(self.reg_all_lb, self.reg_sel_lb, True)).pack(side="left", padx=10)
        ttk.Button(btns, text="← 取消报名",
                   command=lambda: self.move_reg(self.reg_sel_lb, self.reg_all_lb, False)).pack(side="left", padx=10)
        ttk.Button(btns, text="全选已报名", command=lambda: self.reg_sel_lb.selection_set(0, "end")).pack(side="left", padx=8)

    def move_reg(self, src, dst, adding):
        ev = self.store.data["events"][self.event_name]
        ids, label = person_choices(self.store)
        sel = src.curselection()
        if not sel:
            return
        if not adding and not messagebox.askyesno("取消报名", f"取消所选 {len(sel)} 人的报名，并清除他们在该项目的成绩及关联对阵，是否继续？", parent=self):
            return
        for idx in reversed(sel):
            item = src.get(idx)
            pid = item.split(" ")[0]
            if adding:
                if pid in ev["regs"]:
                    continue
                ev["regs"].append(pid)
            else:
                if pid in ev["regs"]:
                    ev["regs"].remove(pid)
                ev["prelim"].pop(pid, None)
                ev["final"].pop(pid, None)
                ev["qualified"].pop(pid, None)
                ev["wins"].pop(pid, None)
                ev["notes"].pop(pid, None)
                ev["matches"] = [m for m in ev.get("matches", []) if pid not in (m.get("p1"), m.get("p2"))]
        self.store.save()
        self.load_regs()
        self.load_scores()
        self.load_ranking()

    def load_regs(self):
        ev = self.store.data["events"][self.event_name]
        persons = self.store.data["persons"]
        self.reg_all_lb.delete(0, "end")
        self.reg_sel_lb.delete(0, "end")
        for pid, p in persons.items():
            line = f"{pid} {p.get('name','')}"
            (self.reg_sel_lb if pid in ev["regs"] else self.reg_all_lb).insert("end", line)
        self.reg_count_lb.configure(
            text=f"当前报名人数：{len(ev['regs'])} / 全部人员 {len(persons)} 人")

    # ---------- 成绩录入与晋级 ----------
    def _build_score(self):
        bar = ttk.Frame(self.tab_score)
        bar.pack(fill="x", padx=8, pady=6)
        self.score_hint = ttk.Label(bar, text="")
        self.score_hint.pack(side="left")
        ttk.Button(bar, text="刷新", command=self.load_scores).pack(side="right", padx=2)

        body = ttk.Frame(self.tab_score)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.tv_score, vsb = make_tree(body, ["学号", "姓名", "预赛", "决赛", "晋级", "胜场", "备注"],
                                       {"学号": 110, "姓名": 100, "预赛": 70, "决赛": 70,
                                        "晋级": 55, "胜场": 55, "备注": 160}, heights=12)
        self.tv_score.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tv_score.bind("<<TreeviewSelect>>", self._on_score_select)

        # 录入表单
        form = ttk.LabelFrame(self.tab_score, text="成绩录入（先在上方表格选择人员）", padding=8)
        form.pack(fill="x", padx=8, pady=6)
        self.f_prelim = tk.StringVar()
        self.f_final = tk.StringVar()
        self.f_wins = tk.StringVar()
        self.f_note = tk.StringVar()
        self.f_id = None
        r1 = ttk.Frame(form)
        r1.pack(fill="x")
        ttk.Label(r1, text="预赛成绩:").pack(side="left")
        ttk.Entry(r1, textvariable=self.f_prelim, width=10).pack(side="left", padx=3)
        ttk.Label(r1, text="决赛成绩:").pack(side="left", padx=(12, 0))
        ttk.Entry(r1, textvariable=self.f_final, width=10).pack(side="left", padx=3)
        ttk.Label(r1, text="胜场(非单挑手动):").pack(side="left", padx=(12, 0))
        ttk.Entry(r1, textvariable=self.f_wins, width=6).pack(side="left", padx=3)
        ttk.Label(r1, text="备注(技巧赛罚时):").pack(side="left", padx=(12, 0))
        ttk.Entry(r1, textvariable=self.f_note, width=18).pack(side="left", padx=3)
        ttk.Button(r1, text="保存成绩", command=self.save_score).pack(side="left", padx=8)
        r2 = ttk.Frame(form)
        r2.pack(fill="x", pady=(6, 0))
        ttk.Button(r2, text="设为晋级", command=lambda: self.set_qualified(True)).pack(side="left", padx=3)
        ttk.Button(r2, text="取消晋级", command=lambda: self.set_qualified(False)).pack(side="left", padx=3)
        self.quota_lb = ttk.Label(r2, text="")
        self.quota_lb.pack(side="left", padx=14)
        ttk.Button(r2, text="一键晋级", command=self.auto_qualify).pack(side="right", padx=3)
        ttk.Button(r2, text="清空晋级", command=self.clear_qualify).pack(side="right", padx=3)

    def _on_score_select(self, _e=None):
        sel = self.tv_score.selection()
        if not sel:
            self._clear_score_form()
            return
        self.f_id = sel[0]
        ev = self.store.data["events"][self.event_name]
        self.f_prelim.set(fmt(ev["prelim"][self.f_id]) if ev["prelim"].get(self.f_id) is not None else "")
        self.f_final.set(fmt(ev["final"][self.f_id]) if ev["final"].get(self.f_id) is not None else "")
        self.f_wins.set(str(ev["wins"].get(self.f_id, 0)))
        self.f_note.set(ev["notes"].get(self.f_id, ""))

    def _clear_score_form(self):
        self.f_id = None
        for variable in (self.f_prelim, self.f_final, self.f_wins, self.f_note):
            variable.set("")

    def load_scores(self):
        self._clear_score_form()
        ev = self.store.data["events"][self.event_name]
        persons = self.store.data["persons"]
        is_single = event_type(self.event_name, self.store.data["events"][self.event_name]) == "duel"
        wins, diff, goals = single_match_stats(ev) if is_single else (None, None, None)
        clear_tree(self.tv_score)
        for pid in ev["regs"]:
            p = persons.get(pid, {})
            if is_single:
                q = "✓" if ev["qualified"].get(pid) else ""
                self.tv_score.insert("", "end", iid=pid, values=(
                    pid, p.get("name", ""), "-", "-", q,
                    wins.get(pid, 0), f"胜{wins.get(pid,0)}/净{diff.get(pid,0)}/进{goals.get(pid,0)}"))
            else:
                q = "✓" if ev["qualified"].get(pid) else ""
                self.tv_score.insert("", "end", iid=pid, values=(
                    pid, p.get("name", ""), fmt(ev["prelim"].get(pid)),
                    fmt(ev["final"].get(pid)), q, ev["wins"].get(pid, 0),
                    ev["notes"].get(pid, "")))
        # 晋级规则提示
        quota, direct, eff = event_quota(self.store.data, self.event_name)
        if event_type(self.event_name, self.store.data["events"][self.event_name]) == "duel":
            hint = "对抗赛：胜场/净胜/进球由「对阵管理」自动统计"
            extra = "单挑赛以胜场排序自动晋级"
        else:
            hint = ("得分赛：成绩越高越好，单位为分" if event_type(self.event_name, self.store.data["events"][self.event_name]) == "score"
                    else "计时赛：用时越短越好，单位为秒")
            extra = "按预赛成绩自动晋级"
        self.score_hint.configure(text=hint)
        if direct:
            qtxt = f"报名 {len(ev['regs'])} 人 ≤ 12 → 直接决赛，取消预赛"
        else:
            qtxt = f"有效参赛 {eff} 人 → 晋级名额 {quota} 人（前1/3 向上取整，上限12）"
        self.quota_lb.configure(text=f"{qtxt}｜{extra}")

    def save_score(self):
        ev = self.store.data["events"][self.event_name]
        if event_type(self.event_name, self.store.data["events"][self.event_name]) == "duel":
            messagebox.showinfo("提示", "对抗赛成绩请在「对阵管理」中录入。", parent=self)
            return
        if not self.f_id or self.f_id not in ev["regs"] or self.f_id not in self.store.data["persons"]:
            messagebox.showwarning("提示", "请先在成绩表格中选择一名选手。", parent=self)
            return
        pid = self.f_id
        try:
            prelim = parse_num(self.f_prelim.get(), "预赛成绩") if self.f_prelim.get().strip() else None
            final = parse_num(self.f_final.get(), "决赛成绩") if self.f_final.get().strip() else None
            w = self.f_wins.get().strip()
            if w:
                if not w.isdecimal():
                    raise ValueError("胜场必须是整数")
            wins = int(w) if w else None
        except ValueError as e:
            messagebox.showwarning("提示", str(e), parent=self)
            return
        for key, value in (("prelim", prelim), ("final", final), ("wins", wins)):
            if value is None:
                ev[key].pop(pid, None)
            else:
                ev[key][pid] = value
        ev["notes"][pid] = self.f_note.get().strip()
        self.store.save()
        self.load_scores()
        self.load_ranking()

    def set_qualified(self, val):
        if not self.f_id or self.f_id not in self.store.data["events"][self.event_name]["regs"]:
            messagebox.showwarning("提示", "请先在成绩表格中选择一名选手。", parent=self)
            return
        ev = self.store.data["events"][self.event_name]
        ev["qualified"][self.f_id] = val
        self.store.save()
        self.load_scores()

    def auto_qualify(self):
        """自动晋级：按预赛成绩（得分类从高到低 / 技巧用时从短到长 / 单挑按胜场）取前 N"""
        ev = self.store.data["events"][self.event_name]
        quota, direct, _ = event_quota(self.store.data, self.event_name)
        if len(ev["regs"]) == 0:
            messagebox.showinfo("提示", "当前项目还没有报名人员。", parent=self)
            return
        if direct:
            for pid in ev["regs"]:
                ev["qualified"][pid] = True
            messagebox.showinfo("自动晋级", "报名人数 ≤ 12，取消预赛，全部直接进入决赛。", parent=self)
        else:
            ranked = qualification_ids(self.store.data, self.event_name)
            if not ranked:
                messagebox.showinfo("暂无有效成绩", "请先录入预赛成绩或对阵结果，再计算晋级。", parent=self)
                return
            for pid in ev["regs"]:
                ev["qualified"][pid] = False
            for pid in ranked[:quota]:
                ev["qualified"][pid] = True
            messagebox.showinfo("自动晋级",
                                f"已按有效成绩晋级 {len(ranked)} 人。\n可通过下方表格「设为晋级/取消晋级」手动微调。",
                                parent=self)
        self.store.save()
        self.load_scores()

    def clear_qualify(self):
        ev = self.store.data["events"][self.event_name]
        for pid in ev["regs"]:
            ev["qualified"][pid] = False
        self.store.save()
        self.load_scores()

    # ---------- 对阵管理（仅单挑赛） ----------
    def _build_matches(self):
        bar = ttk.Frame(self.tab_matches)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="添加对阵", command=self.add_onevone).pack(side="left", padx=2)
        ttk.Button(bar, text="删除对阵", command=self.del_onevone).pack(side="left", padx=2)
        ttk.Button(bar, text="刷新", command=self.load_matches).pack(side="right", padx=2)
        ttk.Label(bar, text="比分高者获胜，胜场自动统计；对手留空视为轮空直接获胜",
                  foreground="#888").pack(side="right", padx=8)
        body = ttk.Frame(self.tab_matches)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.tv_matches, vsb = make_tree(body, ["选手A", "比分", "选手B", "轮次", "胜者"],
                                         {"选手A": 170, "比分": 80, "选手B": 170,
                                          "轮次": 130, "胜者": 120}, heights=14)
        self.tv_matches.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def load_matches(self):
        ev = self.store.data["events"][self.event_name]
        persons = self.store.data["persons"]
        clear_tree(self.tv_matches)
        for i, m in enumerate(ev.get("matches", [])):
            winner = ""
            if m["s1"] > m["s2"]:
                winner = persons.get(m["p1"], {}).get("name", m["p1"])
            elif m["s2"] > m["s1"]:
                winner = persons.get(m["p2"], {}).get("name", m["p2"])
            elif not m["p2"]:
                winner = persons.get(m["p1"], {}).get("name", m["p1"])
            elif not m["p1"]:
                winner = persons.get(m["p2"], {}).get("name", m["p2"])
            else:
                winner = "平局"
            self.tv_matches.insert("", "end", iid=str(i), values=(
                persons.get(m["p1"], {}).get("name", m["p1"]),
                f"{m['s1']} : {m['s2']}",
                persons.get(m["p2"], {}).get("name", m["p2"]),
                m.get("round", ""), winner))

    def add_onevone(self):
        ev = self.store.data["events"][self.event_name]
        if len(ev["regs"]) < 2:
            messagebox.showinfo("提示", "至少需要 2 名已报名选手才能添加对阵。", parent=self)
            return
        dlg = OneVOneDialog(self, self.app, ev)
        self.wait_window(dlg.top)
        if getattr(dlg, "result", None):
            ev["matches"].append(dlg.result)
            self.store.save()
            self.load_matches()
            self.load_scores()
            self.load_ranking()

    def del_onevone(self):
        ev = self.store.data["events"][self.event_name]
        sel = self.tv_matches.selection()
        if not sel:
            return
        if messagebox.askyesno("删除", f"确定删除所选 {len(sel)} 条对阵记录？", parent=self):
            for index in sorted((int(item) for item in sel), reverse=True):
                ev["matches"].pop(index)
            self.store.save()
            self.load_matches()
            self.load_scores()
            self.load_ranking()

    # ---------- 项目排名 ----------
    def _build_rank(self):
        bar = ttk.Frame(self.tab_rank)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="刷新", command=self.load_ranking).pack(side="left", padx=2)
        ttk.Button(bar, text="导出排名",
                   command=lambda: do_export(self, f"排名-{self.event_name}",
                                             event_ranking_sheets(self.store.data))).pack(side="left", padx=2)
        ttk.Label(bar, text="自动标注冠亚季军",
                  foreground="#888").pack(side="right", padx=8)
        body = ttk.Frame(self.tab_rank)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.tv_rank, vsb = make_tree(body, ["名次", "学号", "姓名", "成绩", "备注", "奖项"],
                                      {"名次": 55, "学号": 120, "姓名": 110,
                                       "成绩": 110, "备注": 230, "奖项": 70}, heights=14)
        self.tv_rank.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def load_ranking(self):
        clear_tree(self.tv_rank)
        for r in rank_event(self.store.data, self.event_name):
            self.tv_rank.insert("", "end", iid=f"{r['rank']}-{r['id']}", values=(
                r["rank"], r["id"], r["name"], r["result"], r["detail"], r["award"]))

    # ---------- 事件切换 ----------
    def on_event_change(self):
        self.event_name = self.ev_cb.get()
        is_single = event_type(self.event_name, self.store.data["events"][self.event_name]) == "duel"
        # 单挑赛：成绩页改为只读统计，对阵页可用；其他项目相反
        self.nb.tab(self.tab_matches, state="normal" if is_single else "disabled")
        self.load_regs()
        self.load_scores()
        self.load_matches()
        self.load_ranking()

    def refresh(self):
        self.ev_cb.configure(values=list(self.store.data["events"]))
        self.load_regs()
        self.load_scores()
        self.load_matches()
        self.load_ranking()


# ===========================================================================
# 模块 8：综测加分统计与导出页
# ===========================================================================
class RoleDialog:
    """添加身份记录（裁判/管理/观众），以学号关联到人员（兼容旧数据按姓名）"""

    def __init__(self, master, app):
        self.store = app.store
        self.top = tk.Toplevel(master)
        self.top.title("添加身份记录")
        self.top.transient(master)
        self.top.grab_set()
        self.result = None

        ids, label = person_choices(self.store)
        frm = ttk.Frame(self.top, padding=14)
        frm.pack()
        ttk.Label(frm, text="人员 *").grid(row=0, column=0, sticky="e", pady=4)
        self.name_var = tk.StringVar()
        ttk.Combobox(frm, textvariable=self.name_var, values=list(label.values()),
                     width=22).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(frm, text="(下拉选择：学号+姓名，避免同名串档)",
                  foreground="#888").grid(row=0, column=2, columnspan=2, sticky="w", padx=(2, 0))
        ttk.Label(frm, text="身份").grid(row=1, column=0, sticky="e", pady=4)
        self.role_var = tk.StringVar(value="ref_team")
        role_box = ttk.Combobox(frm, textvariable=self.role_var, state="readonly", width=22,
                                values=[r for r in ROLE_LABELS])
        role_box.grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(frm, text="场次/数量").grid(row=2, column=0, sticky="e", pady=4)
        self.count_var = tk.StringVar(value="1")
        ttk.Entry(frm, textvariable=self.count_var, width=8).grid(row=2, column=1, sticky="w", padx=6)
        self.hint_lb = ttk.Label(frm, text="", foreground="#888")
        self.hint_lb.grid(row=3, column=0, columnspan=2, pady=4)
        role_box.bind("<<ComboboxSelected>>", lambda e: self._hint())
        self._hint()
        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="保存", command=self.ok).pack(side="left", padx=8)
        ttk.Button(btns, text="取消", command=self.top.destroy).pack(side="left", padx=8)

    def _hint(self):
        label, per = ROLE_LABELS.get(self.role_var.get(), ("", 0))
        per = get_rules(self.store.data).get(self.role_var.get(), 0)
        if self.role_var.get() == "manager":
            self.hint_lb.configure(text=f"{label}：固定 {per} 分/人")
        else:
            self.hint_lb.configure(text=f"{label}：{per} 分/场 × 场次")

    def ok(self):
        # 以「学号 姓名」下拉反查学号：记录同时保存学号与姓名，学号为主键，避免同名人员串档
        ids, label = person_choices(self.store)
        rev = {v: k for k, v in label.items()}
        chosen = self.name_var.get().strip()
        pid = rev.get(chosen, "")
        if not chosen or not pid:
            messagebox.showwarning("提示", "请从下拉列表中选择人员（学号+姓名）。", parent=self.top)
            return
        role = self.role_var.get()
        count = 1
        if role != "manager":
            c = self.count_var.get().strip()
            if not c.isdigit() or int(c) < 1:
                messagebox.showwarning("提示", "场次必须是 ≥1 的整数。", parent=self.top)
                return
            count = int(c)
        name = self.store.data["persons"].get(pid, {}).get("name", "")
        self.result = {"name": name, "pid": pid, "role": role, "matches": count}
        self.top.destroy()


class ScorePage(BasePage):
    """综测加分：个人明细 / 全体总表 / 身份记录 / 导出与备份"""

    def __init__(self, master, app):
        super().__init__(master, app)
        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=12, pady=(10, 0))
        ttk.Button(actions, text="一键计算全体综测", command=self.calculate_all).pack(side="left")
        ttk.Button(actions, text="录入 / 修改综测规则", command=lambda: self.nb.select(self.rules_editor)).pack(side="left", padx=8)
        self.calculation_hint = ttk.Label(actions, text="按已保存规则计算，可随时重新计算。", style="Muted.TLabel")
        self.calculation_hint.pack(side="left", padx=8)
        nb = ttk.Notebook(self)
        self.nb = nb
        nb.pack(fill="both", expand=True, padx=8, pady=6)
        self.tab_detail = ttk.Frame(nb)
        self.tab_summary = ttk.Frame(nb)
        self.tab_roles = ttk.Frame(nb)
        self.tab_signin = ttk.Frame(nb)
        nb.add(self.tab_detail, text=" 个人加分明细 ")
        nb.add(self.tab_summary, text=" 全体综测总表 ")
        nb.add(self.tab_roles, text=" 身份记录(裁判/管理/观众) ")
        nb.add(self.tab_signin, text=" 活动签到明细 ")
        self._build_detail()
        self._build_summary()
        self._build_roles()
        self._build_signin()
        self.rules_editor = RulesEditor(nb, self.store, self.calculate_all)
        nb.add(self.rules_editor, text=" 综测规则设置 ")
        self._build_export_bar()

    def calculate_all(self):
        if self.rules_editor.dirty():
            self.nb.select(self.rules_editor)
            messagebox.showinfo("规则尚未保存", "请点击「保存规则并计算」，应用刚填写的规则后再计算。", parent=self)
            return
        self.refresh()
        self.nb.select(self.tab_summary)
        count = len(self.store.data["persons"])
        cap = get_rules(self.store.data)["cap"]
        capped = sum(person_detail(self.store.data, pid)[0] > cap for pid in self.store.data["persons"])
        self.calculation_hint.configure(text=f"已计算 {count} 人 · {capped} 人超额封顶 · {datetime.datetime.now():%H:%M:%S}")

    # ---------- 个人加分明细 ----------
    def _build_detail(self):
        bar = ttk.Frame(self.tab_detail)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Label(bar, text="选择人员:").pack(side="left")
        self.detail_cb = ttk.Combobox(bar, width=24, state="readonly")
        self.detail_cb.pack(side="left", padx=4)
        self.detail_cb.bind("<<ComboboxSelected>>", lambda _: self.load_detail())
        ttk.Button(bar, text="查询明细", command=self.load_detail).pack(side="left", padx=2)
        self.detail_total_lb = ttk.Label(bar, text="")
        self.detail_total_lb.pack(side="right", padx=8)
        body = ttk.Frame(self.tab_detail)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.tv_detail, vsb = make_tree(body, ["加分项", "分值"],
                                        {"加分项": 420, "分值": 120}, heights=14)
        self.tv_detail.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._reload_detail_cb()

    def _reload_detail_cb(self):
        ids, label = person_choices(self.store)
        previous = self.detail_cb.get()
        self.detail_cb.configure(values=list(label.values()))
        if previous in label.values():
            self.detail_cb.set(previous)
        elif ids:
            self.detail_cb.set(label[ids[0]])
        else:
            self.detail_cb.set("")

    def load_detail(self):
        ids, label = person_choices(self.store)
        rev = {v: k for k, v in label.items()}
        pid = rev.get(self.detail_cb.get(), "")
        clear_tree(self.tv_detail)
        if not pid:
            self.detail_total_lb.configure(text="")
            return
        raw, capped, rows = person_detail(self.store.data, pid)
        clear_tree(self.tv_detail)
        for item, pts in rows:
            self.tv_detail.insert("", "end", values=(item, pts))
        self.detail_total_lb.configure(
            text=f"原始合计 {raw} 分 → 实得 {capped} 分（封顶 {get_rules(self.store.data)['cap']:g} 分）")

    # ---------- 全体综测总表 ----------
    def _build_summary(self):
        bar = ttk.Frame(self.tab_summary)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="重新计算", command=self.calculate_all).pack(side="left", padx=2)
        self.cap_hint = ttk.Label(bar, foreground="#c00")
        self.cap_hint.pack(side="right", padx=8)
        body = ttk.Frame(self.tab_summary)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.tv_summary, vsb = make_tree(body, ["学号", "姓名", "性别", "交运杯", "单项赛",
                                                "裁判", "管理", "观众", "活动签到",
                                                "原始合计", "实得(封顶)"],
                                         {"学号": 92, "姓名": 82, "性别": 50, "交运杯": 60,
                                          "单项赛": 60, "裁判": 55, "管理": 55, "观众": 55,
                                          "活动签到": 65, "原始合计": 75, "实得(封顶)": 85},
                                         heights=16)
        self.tv_summary.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def load_summary(self):
        clear_tree(self.tv_summary)
        cap = get_rules(self.store.data)["cap"]
        self.cap_hint.configure(text=f"当前规则：个人累计封顶 {cap:g} 分，超出自动截断")
        for pid, p in self.store.data["persons"].items():
            t, e, r, m, a, sg = person_category_points(self.store.data, pid)
            raw = round(t + e + r + m + a + sg, 4)
            self.tv_summary.insert("", "end", iid=pid, values=(
                pid, p.get("name", ""), p.get("gender", ""),
                t, e, r, m, a, sg, raw, round(min(raw, cap), 4)))

    # ---------- 身份记录 ----------
    def _build_roles(self):
        bar = ttk.Frame(self.tab_roles)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="添加记录", command=self.add_role).pack(side="left", padx=2)
        ttk.Button(bar, text="删除所选", command=self.del_role).pack(side="left", padx=2)
        ttk.Button(bar, text="刷新", command=self.load_roles).pack(side="right", padx=2)
        self.roles_hint = ttk.Label(bar, foreground="#888")
        self.roles_hint.pack(side="right", padx=8)
        body = ttk.Frame(self.tab_roles)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.tv_roles, vsb = make_tree(body, ["姓名", "身份", "场次/数量", "加分"],
                                       {"姓名": 180, "身份": 180, "场次/数量": 100,
                                        "加分": 100}, heights=14)
        self.tv_roles.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def load_roles(self):
        clear_tree(self.tv_roles)
        rules = get_rules(self.store.data)
        self.roles_hint.configure(text=f"八人制裁判 {rules['ref_team']:g}/场 · 单项裁判 {rules['ref_event']:g}/场 · 管理 {rules['manager']:g}/人 · 观众 {rules['audience']:g}/场")
        i = 0
        for role in ("ref_team", "ref_event", "manager", "audience"):
            label, per = ROLE_LABELS[role]
            per = rules[role]
            for rec in self.store.data["roles"].get(role, []):
                n = rec.get("matches", 1)
                pts = per if role == "manager" else round(per * n, 4)
                self.tv_roles.insert("", "end", iid=str(i), values=(
                    rec.get("name", ""), label, n if role != "manager" else "-", pts))
                i += 1

    def add_role(self):
        dlg = RoleDialog(self, self.app)
        self.wait_window(dlg.top)
        if dlg.result:
            r = dlg.result
            self.store.data["roles"][r["role"]].append(r)
            self.store.save()
            self.load_roles()

    def del_role(self):
        sel = self.tv_roles.selection()
        if not sel:
            return
        if not messagebox.askyesno("删除身份记录", f"确定删除所选 {len(sel)} 条身份记录？", parent=self):
            return
        flat = []
        for role in ("ref_team", "ref_event", "manager", "audience"):
            for rec in self.store.data["roles"][role]:
                flat.append((role, rec))
        for index in sorted((int(item) for item in sel), reverse=True):
            role, rec = flat[index]
            self.store.data["roles"][role].remove(rec)
        self.store.save()
        self.load_roles()

    # ---------- 活动签到明细（批量导入产生） ----------
    def _build_signin(self):
        bar = ttk.Frame(self.tab_signin)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="删除所选", command=self.del_signin).pack(side="left", padx=2)
        ttk.Button(bar, text="刷新", command=self.load_signins).pack(side="right", padx=2)
        self.signin_hint = ttk.Label(bar, foreground="#888")
        self.signin_hint.pack(side="right", padx=8)
        body = ttk.Frame(self.tab_signin)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        self.tv_signin, vsb = make_tree(body, ["活动", "日期", "学号", "姓名"],
                                        {"活动": 260, "日期": 110, "学号": 140,
                                         "姓名": 120}, heights=14)
        self.tv_signin.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def load_signins(self):
        clear_tree(self.tv_signin)
        rules = get_rules(self.store.data)
        self.signin_hint.configure(text=f"每次签到 +{rules['signin']:g} 分；个人总分封顶 {rules['cap']:g} 分")
        for i, r in enumerate(self.store.data.get("signins", [])):
            self.tv_signin.insert("", "end", iid=str(i), values=(
                r.get("activity", ""), r.get("date", ""),
                r.get("pid", ""), r.get("name", "")))

    def del_signin(self):
        sel = self.tv_signin.selection()
        if not sel:
            return
        signins = self.store.data.get("signins", [])
        idxs = sorted((int(s) for s in sel), reverse=True)
        if messagebox.askyesno("删除", f"确定删除所选 {len(idxs)} 条签到记录？",
                               parent=self):
            for i in idxs:
                if 0 <= i < len(signins):
                    signins.pop(i)
            self.store.save()
            self.load_signins()
            self.load_summary()

    # ---------- 导出与备份 ----------
    def _build_export_bar(self):
        bar = ttk.LabelFrame(self, text="导出与备份", padding=10)
        bar.pack(fill="x", padx=8, pady=(4, 8))
        ttk.Button(bar, text="导出交运杯积分榜",
                   command=lambda: do_export(self, "交运杯积分榜",
                                             team_ranking_sheet(self.store.data))).pack(side="left", padx=3)
        ttk.Button(bar, text="导出单项赛排名",
                   command=lambda: do_export(self, "单项赛排名",
                                             event_ranking_sheets(self.store.data))).pack(side="left", padx=3)
        ttk.Button(bar, text="导出综测总表",
                   command=lambda: do_export(self, "综测总表",
                                             score_summary_sheet(self.store.data))).pack(side="left", padx=3)
        ttk.Button(bar, text="导出学生档案",
                   command=lambda: do_export(self, "学生档案表",
                                             person_sheet(self.store.data))).pack(side="left", padx=3)
        ttk.Button(bar, text="导出签到明细",
                   command=lambda: do_export(self, "活动签到明细",
                                             signin_sheet(self.store.data))).pack(side="left", padx=3)
        ttk.Button(bar, text="备份数据(JSON)", command=self.backup).pack(side="left", padx=3)
        ttk.Label(bar, text=f"数据文件：{self.store.path}",
                  foreground="#888").pack(side="right", padx=8)

    def backup(self):
        try:
            dst = self.store.backup()
            messagebox.showinfo("备份成功", f"已备份到：\n{dst}", parent=self)
        except Exception as e:
            messagebox.showerror("备份失败", str(e), parent=self)

    def refresh(self):
        self._reload_detail_cb()
        self.load_detail()
        self.load_summary()
        self.load_roles()
        self.load_signins()


# ===========================================================================
# 模块 9：主程序
# ===========================================================================
class MainApp(tk.Tk):
    def report_callback_exception(self, exc_type, value, traceback):
        messagebox.showerror("操作未完成", str(value), parent=self)

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x780")
        self.minsize(1024, 680)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background="#f3f6fa", foreground="#203047")
        style.configure("TFrame", background="#f3f6fa")
        style.configure("TLabel", background="#f3f6fa")
        style.configure("TButton", padding=(12, 7))
        style.configure("TNotebook", background="#f3f6fa", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 10))
        style.map("TNotebook.Tab", background=[("selected", "#e0efe9")], foreground=[("selected", "#126346")])
        style.configure("Treeview", rowheight=32, fieldbackground="#ffffff", background="#ffffff", borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), padding=8, background="#e8edf4")
        style.map("Treeview", background=[("selected", "#d6eee3")], foreground=[("selected", "#124e38")])
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 21, "bold"))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Metric.TLabel", font=("Microsoft YaHei UI", 28, "bold"), foreground="#137452")
        style.configure("Muted.TLabel", foreground="#627187")

        data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DEFAULT_DATA_FILE)
        self.store = DataStore(data_path)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=6)
        self.pages = [
            ("赛事总览", DashboardPage(self.nb, self)),
            ("人员信息管理", PersonPage(self.nb, self)),
            ("交运杯团体赛", TeamPage(self.nb, self)),
            ("球员表现档案", PlayerPage(self.nb, self)),
            ("绿茵全能王单项赛", EventPage(self.nb, self)),
            ("综测加分与导出", ScorePage(self.nb, self)),
        ]
        for title, page in self.pages:
            self.nb.add(page, text=f"  {title}  ")
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_change)
        self.bind("<F5>", lambda _: self.refresh_current())

        status = ttk.Frame(self)
        status.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(status, text="本地 JSON 持久化 · 每次操作自动保存 · 无需联网",
                  foreground="#666").pack(side="left")
        ttk.Label(status, text=f"数据文件: {data_path}",
                  foreground="#666").pack(side="right")

    def _on_tab_change(self, event):
        if event.widget is not self.nb or not self.nb.select():
            return
        w = self.nb.nametowidget(self.nb.select())
        for _, page in self.pages:
            if page is w:
                page.refresh()

    def goto(self, title):
        for name, page in self.pages:
            if name == title:
                self.nb.select(page)
                page.refresh()
                break

    def refresh_current(self):
        self.nb.nametowidget(self.nb.select()).refresh()

    def edit_scheduled_match(self, match_id):
        match = next((m for m in self.store.data["matches"] if m["id"] == match_id), None)
        if match:
            dialog = MatchDialog(self, self, match)
            self.wait_window(dialog.top)

    def export_records(self, parent, name, sheets):
        do_export(parent, name, sheets)

    def save(self):
        self.store.save()


def main():
    app = MainApp()
    app.mainloop()


if __name__ == "__main__":
    main()




