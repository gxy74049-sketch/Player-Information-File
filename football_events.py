"""自定义单项赛事与类型兼容。"""
import re
import tkinter as tk
from tkinter import ttk, messagebox

EVENT_TYPES = {"得分赛（分数越高越好）": "score", "计时赛（用时越短越好）": "time", "对抗赛（按胜场排名）": "duel"}


def event_type(name, event):
    return event.get("type") or ("duel" if name == "1对1单挑赛" else "time" if name == "技巧赛" else "score")


def make_event(name, kind, existing):
    name = name.strip()
    if not name or len(name) > 31 or re.search(r"[\\/?:*\[\]\x00-\x1f]", name) or name.startswith("'") or name.endswith("'"):
        raise ValueError("赛事名称需为 1–31 个字符，不能含 \\ / ? : * [ ]、控制字符或首尾单引号。")
    if name.casefold() in {key.casefold() for key in existing}:
        raise ValueError("该赛事名称已存在，请使用其他名称。")
    if kind not in EVENT_TYPES.values():
        raise ValueError("请选择赛事类型。")
    return name, {"type": kind, "regs": [], "prelim": {}, "final": {}, "qualified": {},
                  "wins": {}, "matches": [], "notes": {}}


class AddEventDialog:
    def __init__(self, master, store):
        self.store = store
        self.result = None
        self.top = tk.Toplevel(master)
        self.top.title("添加赛事")
        self.top.transient(master.winfo_toplevel())
        self.top.grab_set()
        form = ttk.Frame(self.top, padding=20)
        form.pack(fill="both", expand=True)
        self.name = tk.StringVar()
        self.kind = tk.StringVar(value=next(iter(EVENT_TYPES)))
        ttk.Label(form, text="赛事名称").grid(row=0, column=0, sticky="w", pady=8)
        entry = ttk.Entry(form, textvariable=self.name, width=32)
        entry.grid(row=0, column=1, padx=10)
        ttk.Label(form, text="赛事类型").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Combobox(form, textvariable=self.kind, values=list(EVENT_TYPES), state="readonly", width=30).grid(row=1, column=1, padx=10)
        ttk.Label(form, text="创建后即可报名、录入成绩和查看排名。\n综测使用当前单项赛规则；晋级沿用现有名额规则。",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=14)
        ttk.Button(form, text="添加赛事", command=self.save).grid(row=3, column=1, sticky="e", padx=10)
        ttk.Button(form, text="取消", command=self.top.destroy).grid(row=3, column=0)
        self.top.bind("<Return>", lambda _: self.save())
        self.top.bind("<Escape>", lambda _: self.top.destroy())
        entry.focus_set()

    def save(self):
        try:
            name, event = make_event(self.name.get(), EVENT_TYPES.get(self.kind.get()), self.store.data["events"])
        except ValueError as error:
            messagebox.showwarning("请检查赛事信息", str(error), parent=self.top)
            return
        self.store.data["events"][name] = event
        try:
            self.store.save()
        except Exception as error:
            self.store.data["events"].pop(name, None)
            messagebox.showerror("赛事保存失败", str(error), parent=self.top)
            return
        self.result = name
        self.top.destroy()
