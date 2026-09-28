"""赛事工作台：赛程状态、球员表现与概览（兼容既有 JSON）。"""
import datetime
import math
import tkinter as tk
from tkinter import ttk, messagebox

STATUSES = ("未开始", "进行中", "已结束", "已延期", "已取消")


def match_status(match):
    if match.get("status") in STATUSES:
        return match["status"]
    return "已结束" if all(isinstance(match.get(k), (int, float)) for k in ("g1", "g2")) else "未开始"


def is_finished(match):
    return match_status(match) == "已结束"


def score_text(match):
    return "待定" if match.get("g1") is None or match.get("g2") is None else f"{match['g1']} : {match['g2']}"


def validate_schedule(date, time):
    try:
        parsed = datetime.datetime.strptime(date, "%Y-%m-%d")
        if parsed.strftime("%Y-%m-%d") != date:
            raise ValueError()
    except ValueError:
        raise ValueError("日期请填写 YYYY-MM-DD，例如 2026-09-20")
    if time:
        try:
            parsed = datetime.datetime.strptime(time, "%H:%M")
            if parsed.strftime("%H:%M") != time:
                raise ValueError()
        except ValueError:
            raise ValueError("开球时间请填写 HH:MM，例如 14:30；也可留空")


def parse_performance(goals, assists, rating, note):
    result = {"note": note.strip()}
    for key, value in (("goals", goals), ("assists", assists)):
        value = value.strip()
        if not value.isdecimal():
            raise ValueError("进球和助攻必须为非负整数")
        result[key] = int(value)
    if rating.strip():
        try:
            number = float(rating)
        except ValueError:
            raise ValueError("评分请输入 0 至 10 的数字，或留空")
        if not math.isfinite(number) or not 0 <= number <= 10:
            raise ValueError("评分范围为 0 至 10")
        result["rating"] = number
    else:
        result["rating"] = None
    return result


def player_summary(data):
    rows = []
    for pid, person in data["persons"].items():
        games = goals = assists = recorded = 0
        ratings = []
        for match in data["matches"]:
            if not is_finished(match) or pid not in match.get("lineup1", []) + match.get("lineup2", []):
                continue
            games += 1
            stat = match.get("player_stats", {}).get(pid)
            if stat is not None:
                recorded += 1
                goals += stat.get("goals", 0)
                assists += stat.get("assists", 0)
                if stat.get("rating") is not None:
                    ratings.append(stat["rating"])
        rows.append((pid, person.get("name", ""), games, recorded, goals, assists,
                     round(sum(ratings) / len(ratings), 2) if ratings else "—"))
    return sorted(rows, key=lambda row: (-row[4], -row[5], row[0]))


def table(parent, columns):
    wrap = ttk.Frame(parent)
    wrap.pack(fill="both", expand=True, padx=12, pady=8)
    tree = ttk.Treeview(wrap, columns=columns, show="headings", selectmode="browse")
    for name in columns:
        tree.heading(name, text=name)
        tree.column(name, width=110, minwidth=65, anchor="center")
    sy = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
    sx = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
    tree.grid(row=0, column=0, sticky="nsew")
    sy.grid(row=0, column=1, sticky="ns")
    sx.grid(row=1, column=0, sticky="ew")
    wrap.rowconfigure(0, weight=1)
    wrap.columnconfigure(0, weight=1)
    return tree


class DashboardPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        ttk.Label(self, text="赛事工作台", style="Title.TLabel").pack(anchor="w", padx=20, pady=(18, 4))
        ttk.Label(self, text="先安排赛程，再录入赛果；完赛后补充球员表现，实时查看积分与档案。",
                  style="Muted.TLabel").pack(anchor="w", padx=20)
        cards = ttk.Frame(self)
        cards.pack(fill="x", padx=16, pady=18)
        self.metrics = []
        for i, name in enumerate(("参赛人员", "参赛队伍", "待赛 / 进行中", "已结束")):
            box = ttk.LabelFrame(cards, text=name, padding=16)
            box.grid(row=0, column=i, sticky="ew", padx=4)
            cards.columnconfigure(i, weight=1)
            label = ttk.Label(box, style="Metric.TLabel")
            label.pack(anchor="w")
            self.metrics.append(label)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=20)
        for title in ("人员信息管理", "交运杯团体赛", "球员表现档案", "综测加分与导出"):
            ttk.Button(bar, text=title, command=lambda t=title: app.goto(t)).pack(side="left", padx=(0, 8))
        ttk.Label(self, text="待办赛程 · 按日期排序 · 双击更新赛况", style="Section.TLabel").pack(anchor="w", padx=20, pady=(20, 0))
        self.tree = table(self, ("日期", "开球", "主队", "比分", "客队", "场地", "状态"))
        self.tree.bind("<Double-1>", self.edit_match)
        self.hint = ttk.Label(self, style="Muted.TLabel")
        self.hint.pack(anchor="w", padx=20, pady=(0, 16))
        self.refresh()

    def refresh(self):
        data = self.app.store.data
        pending = [m for m in data["matches"] if match_status(m) in ("未开始", "进行中", "已延期")]
        for label, value in zip(self.metrics, (len(data["persons"]), len(data["teams"]),
                                             len(pending), sum(is_finished(m) for m in data["matches"]))):
            label.configure(text=str(value))
        self.tree.delete(*self.tree.get_children())
        for m in sorted(pending, key=lambda m: (m.get("date", ""), m.get("time", ""), m["id"])):
            self.tree.insert("", "end", iid=str(m["id"]), values=(m.get("date", ""), m.get("time", ""),
                             m["team1"], score_text(m), m["team2"], m.get("venue", ""), match_status(m)))
        missing = sum(1 for m in data["matches"] if is_finished(m)
                      for pid in set(m.get("lineup1", []) + m.get("lineup2", []))
                      if pid not in m.get("player_stats", {}))
        self.hint.configure(text=f"待补录球员表现：{missing} 人次。评分独立记录，不参与综测加分。" if pending
                            else f"暂无待办赛程，可进入团体赛安排比赛。待补录球员表现：{missing} 人次。")

    def edit_match(self, _event=None):
        selected = self.tree.selection()
        if selected:
            self.app.edit_scheduled_match(int(selected[0]))
            self.refresh()


class PlayerPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        ttk.Label(self, text="球员表现档案", style="Title.TLabel").pack(anchor="w", padx=16, pady=(16, 4))
        ttk.Label(self, text="仅汇总已结束比赛的出场球员；平均评分仅计算已评分场次，未录入不视为零分。",
                  style="Muted.TLabel").pack(anchor="w", padx=16)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=16, pady=10)
        ttk.Label(bar, text="学号 / 姓名").pack(side="left")
        self.query = tk.StringVar()
        ttk.Entry(bar, textvariable=self.query, width=24).pack(side="left", padx=8)
        ttk.Button(bar, text="导出球员统计", command=self.export).pack(side="right")
        ttk.Button(bar, text="录入 / 修改所选场次表现", command=self.edit).pack(side="right", padx=8)
        self.tree = table(self, ("学号", "姓名", "出场", "已录表现", "进球", "助攻", "平均评分"))
        ttk.Label(self, text="选中球员查看逐场记录 · 双击比赛补录表现", style="Section.TLabel").pack(anchor="w", padx=16)
        self.detail = table(self, ("日期", "对阵", "比分", "进球", "助攻", "评分", "备注"))
        self.tree.bind("<<TreeviewSelect>>", self.load_detail)
        self.detail.bind("<Double-1>", self.edit)
        self.query.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        keyword = self.query.get().strip().casefold()
        for row in player_summary(self.app.store.data):
            if not keyword or keyword in (row[0] + " " + row[1]).casefold():
                self.tree.insert("", "end", iid=row[0], values=row)
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self.load_detail()

    def load_detail(self, _event=None):
        self.detail.delete(*self.detail.get_children())
        selected = self.tree.selection()
        if not selected:
            return
        pid = selected[0]
        for m in sorted(self.app.store.data["matches"], key=lambda x: x.get("date", ""), reverse=True):
            if not is_finished(m) or pid not in m.get("lineup1", []) + m.get("lineup2", []):
                continue
            stat = m.get("player_stats", {}).get(pid, {})
            self.detail.insert("", "end", iid=str(m["id"]), values=(m.get("date", ""),
                               f"{m['team1']} / {m['team2']}", score_text(m), stat.get("goals", "未录入"),
                               stat.get("assists", "未录入"), stat.get("rating") if stat.get("rating") is not None else "—",
                               stat.get("note", "")))

    def edit(self, _event=None):
        if not self.detail.selection() or not self.tree.selection():
            return
        mid, pid = int(self.detail.selection()[0]), self.tree.selection()[0]
        match = next(m for m in self.app.store.data["matches"] if m["id"] == mid)
        stat = match.get("player_stats", {}).get(pid, {})
        top = tk.Toplevel(self)
        top.title(f"球员表现 · {self.app.store.data['persons'][pid]['name']}")
        top.transient(self.winfo_toplevel())
        top.grab_set()
        form = ttk.Frame(top, padding=20)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text=f"{match['team1']}  {score_text(match)}  {match['team2']}", style="Section.TLabel").grid(row=0, columnspan=2, pady=(0, 12))
        fields = []
        for i, (name, key, default) in enumerate((("进球", "goals", 0), ("助攻", "assists", 0),
                                                  ("评分（0–10，可留空）", "rating", ""), ("表现备注", "note", "")), 1):
            value = stat.get(key, default)
            var = tk.StringVar(value="" if value is None else str(value))
            fields.append(var)
            ttk.Label(form, text=name).grid(row=i, column=0, sticky="w", pady=7)
            ttk.Entry(form, textvariable=var, width=35).grid(row=i, column=1, padx=10)
        def save():
            try:
                record = parse_performance(*(v.get() for v in fields))
                side = "1" if pid in match.get("lineup1", []) else "2"
                total = record["goals"] + sum(match.get("player_stats", {}).get(other, {}).get("goals", 0)
                                              for other in match.get("lineup" + side, []) if other != pid)
                if total > match["g" + side]:
                    raise ValueError("本队球员进球总数超过球队比分，请核对赛果或个人进球")
            except ValueError as error:
                messagebox.showwarning("请检查记录", str(error), parent=top)
                return
            match.setdefault("player_stats", {})[pid] = record
            self.app.store.save()
            top.destroy()
            self.refresh()
        ttk.Button(form, text="保存表现", command=save).grid(row=5, column=1, sticky="e", pady=14)
        top.bind("<Escape>", lambda _: top.destroy())

    def export(self):
        rows = [["学号", "姓名", "出场", "已录表现", "进球", "助攻", "平均评分"]]
        rows.extend(player_summary(self.app.store.data))
        detail = [["比赛ID", "日期", "主队", "客队", "学号", "姓名", "进球", "助攻", "评分", "备注"]]
        for m in self.app.store.data["matches"]:
            if not is_finished(m):
                continue
            for pid in dict.fromkeys(m.get("lineup1", []) + m.get("lineup2", [])):
                stat = m.get("player_stats", {}).get(pid, {})
                detail.append([m["id"], m.get("date", ""), m["team1"], m["team2"], pid,
                               self.app.store.data["persons"].get(pid, {}).get("name", ""),
                               stat.get("goals", ""), stat.get("assists", ""),
                               stat["rating"] if stat.get("rating") is not None else "", stat.get("note", "")])
        self.app.export_records(self, "球员表现档案", {"球员汇总": rows, "逐场表现": detail})
