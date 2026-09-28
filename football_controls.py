"""成员批量选择与综测规则录入组件。"""
import tkinter as tk
from tkinter import ttk, messagebox
from football_scoring import RULE_FIELDS, DEFAULT_RULES, get_rules, validate_rules


class CtrlDragSelection:
    """Ctrl 单击切换选择，Ctrl 拖动追加连续区间，兼容表格和报名列表。"""
    def __init__(self, widget):
        self.widget = widget
        self.tree = isinstance(widget, ttk.Treeview)
        self.anchor = None
        self.before = set()
        widget.bind("<Control-Button-1>", self.start)
        widget.bind("<B1-Motion>", self.move)
        widget.bind("<ButtonRelease-1>", self.stop)
        widget.bind("<Control-Double-1>", lambda _: "break")

    def items(self):
        return list(self.widget.get_children()) if self.tree else list(range(self.widget.size()))

    def row(self, y):
        if self.tree:
            return self.widget.identify_row(y) or None
        if not self.widget.size():
            return None
        index = self.widget.nearest(y)
        bounds = self.widget.bbox(index)
        return index if bounds and bounds[1] <= y < bounds[1] + bounds[3] else None

    def selected(self):
        return set(self.widget.selection() if self.tree else self.widget.curselection())

    def select(self, values):
        if self.tree:
            self.widget.selection_set([item for item in self.items() if item in values])
        else:
            self.widget.selection_clear(0, "end")
            for item in sorted(values):
                self.widget.selection_set(item)
            self.widget.event_generate("<<ListboxSelect>>")

    def start(self, event):
        self.anchor = self.row(event.y)
        if self.anchor is None:
            return "break"
        self.widget.focus_set()
        self.before = self.selected()
        self.select(self.before ^ {self.anchor})
        return "break"

    def move(self, event):
        if self.anchor is None:
            return
        if event.y < 0:
            self.widget.yview_scroll(-1, "units")
        elif event.y >= self.widget.winfo_height():
            self.widget.yview_scroll(1, "units")
        current = self.row(max(0, min(event.y, self.widget.winfo_height() - 1)))
        items = self.items()
        if current in items and self.anchor in items:
            first, last = sorted((items.index(self.anchor), items.index(current)))
            self.select(self.before | set(items[first:last + 1]))
        return "break"

    def stop(self, _event):
        if self.anchor is not None:
            self.anchor = None
            return "break"


class MemberPicker:
    def __init__(self, master, persons, exclude=()):
        self.persons = {pid: person for pid, person in persons.items() if pid not in exclude}
        self.selected = set()
        self.result = None
        self.top = tk.Toplevel(master)
        self.top.title("批量选择成员")
        self.top.geometry("660x540")
        self.top.transient(master.winfo_toplevel())
        self.top.grab_set()
        body = ttk.Frame(self.top, padding=14)
        body.pack(fill="both", expand=True)
        self.selection_hint = ttk.Label(body, text="点击「多选」可连续点选成员；搜索切换后保留已选成员。", style="Muted.TLabel")
        self.selection_hint.pack(anchor="w")
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=8)
        ttk.Label(bar, text="学号 / 姓名 / 年级").pack(side="left")
        self.query = tk.StringVar()
        ttk.Entry(bar, textvariable=self.query).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(bar, text="全选当前结果", command=self.select_visible).pack(side="left")
        ttk.Button(bar, text="清空选择", command=self.clear).pack(side="left", padx=4)
        selection_bar = ttk.Frame(body)
        selection_bar.pack(fill="x", pady=(0, 8))
        self.multi_enabled = False
        self.multi_button = ttk.Button(selection_bar, text="多选", command=self.toggle_multi)
        self.multi_button.pack(side="left")
        box = ttk.Frame(body)
        box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(box, columns=("学号", "姓名", "年级"), show="headings", selectmode="extended")
        for key in ("学号", "姓名", "年级"):
            self.tree.heading(key, text=key)
            self.tree.column(key, width=140, anchor="center")
        scrollbar = ttk.Scrollbar(box, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.count = ttk.Label(body)
        self.count.pack(anchor="w", pady=8)
        footer = ttk.Frame(body)
        footer.pack(fill="x")
        ttk.Button(footer, text="添加所选成员", command=self.confirm).pack(side="right")
        ttk.Button(footer, text="取消", command=self.top.destroy).pack(side="right", padx=8)
        self.tree.bind("<<TreeviewSelect>>", self.sync)
        self.tree.bind("<Control-a>", self.select_visible)
        self.tree.bind("<Button-1>", self.click_member)
        self.tree.bind("<Double-1>", lambda event: "break" if self.multi_enabled else None)
        CtrlDragSelection(self.tree)
        self.top.bind("<Escape>", lambda _: self.top.destroy())
        self.query.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    def toggle_multi(self):
        self.multi_enabled = not self.multi_enabled
        self.multi_button.configure(text="多选：已开启" if self.multi_enabled else "多选")
        self.selection_hint.configure(text="直接点击成员即可选中，再次点击取消；无需按住 Ctrl。" if self.multi_enabled
                                      else "点击「多选」可连续点选成员；也支持 Ctrl 多选 / Shift 连选。")

    def click_member(self, event):
        if not self.multi_enabled:
            return
        pid = self.tree.identify_row(event.y)
        if not pid:
            return
        self.tree.focus_set()
        self.tree.focus(pid)
        if pid in self.tree.selection():
            self.tree.selection_remove(pid)
        else:
            self.tree.selection_add(pid)
        self.sync()
        return "break"

    def sync(self, _event=None):
        self.selected.difference_update(self.tree.get_children())
        self.selected.update(self.tree.selection())
        self.count.configure(text=f"已选 {len(self.selected)} 人 / 当前搜索结果 {len(self.tree.get_children())} 人")

    def refresh(self):
        self.sync()
        self.tree.delete(*self.tree.get_children())
        query = self.query.get().strip().casefold()
        for pid, person in self.persons.items():
            values = (pid, person.get("name", ""), person.get("grade", ""))
            if not query or query in " ".join(values).casefold():
                self.tree.insert("", "end", iid=pid, values=values)
        self.tree.selection_set([pid for pid in self.selected if self.tree.exists(pid)])
        self.sync()

    def select_visible(self, _event=None):
        self.tree.selection_set(self.tree.get_children())
        self.sync()
        return "break"

    def clear(self):
        self.selected.clear()
        self.tree.selection_remove(self.tree.selection())
        self.sync()

    def confirm(self):
        self.sync()
        if not self.selected:
            messagebox.showinfo("选择成员", "请至少选择一名成员。", parent=self.top)
            return
        self.result = [pid for pid in self.persons if pid in self.selected]
        self.top.destroy()


class RulesEditor(ttk.Frame):
    def __init__(self, master, store, on_saved):
        super().__init__(master, padding=12)
        self.store, self.on_saved = store, on_saved
        ttk.Label(self, text="综测规则设置", style="Section.TLabel").pack(anchor="w")
        ttk.Label(self, text="分值设为 0 即关闭该项奖励；最多 4 位小数。保存后应用于全体人员及历史记录。",
                  style="Muted.TLabel").pack(anchor="w", pady=6)
        form = ttk.Frame(self)
        form.pack(fill="x")
        self.variables = {}
        for i, (key, label, _) in enumerate(RULE_FIELDS):
            row, column = i % 6, (i // 6) * 2
            ttk.Label(form, text=label).grid(row=row, column=column, sticky="w", padx=(0, 12), pady=5)
            variable = tk.StringVar()
            self.variables[key] = variable
            ttk.Entry(form, textvariable=variable, width=12).grid(row=row, column=column + 1, sticky="w", padx=(0, 26), pady=5)
        ttk.Label(self, text="规则说明 / 制度原文（仅供记录；实际计算以上方数值为准）").pack(anchor="w", pady=(12, 4))
        self.description = tk.Text(self, height=4, wrap="word", undo=True)
        self.description.pack(fill="both", expand=True)
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="保存规则并计算", command=self.save).pack(side="left")
        ttk.Button(bar, text="填入默认规则", command=lambda: self.populate(DEFAULT_RULES)).pack(side="left", padx=8)
        ttk.Button(bar, text="撤销未保存修改", command=lambda: self.populate(get_rules(store.data))).pack(side="left")
        self.hint = ttk.Label(bar, text="修改后请保存，才会用于计算。", style="Muted.TLabel")
        self.hint.pack(side="left", padx=12)
        self.populate(get_rules(store.data))

    def populate(self, rules):
        for key, variable in self.variables.items():
            variable.set(str(rules[key]))
        self.description.delete("1.0", "end")
        self.description.insert("1.0", rules.get("description", ""))

    def values(self):
        return validate_rules({**{key: variable.get() for key, variable in self.variables.items()},
                               "description": self.description.get("1.0", "end-1c")})

    def dirty(self):
        try:
            return self.values() != get_rules(self.store.data)
        except ValueError:
            return True

    def save(self):
        try:
            rules = self.values()
        except ValueError as error:
            messagebox.showwarning("规则填写错误", str(error), parent=self)
            return
        previous = self.store.data.get("score_rules")
        self.store.data["score_rules"] = rules
        try:
            self.store.save()
        except Exception as error:
            if previous is None:
                self.store.data.pop("score_rules", None)
            else:
                self.store.data["score_rules"] = previous
            messagebox.showerror("规则保存失败", str(error), parent=self)
            return
        self.populate(rules)
        self.hint.configure(text="规则已保存，已重新计算。")
        self.on_saved()
