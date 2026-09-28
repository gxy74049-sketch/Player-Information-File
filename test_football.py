"""业务回归和 Tk 组件集成检查；使用临时目录，不写入正式数据。"""
import copy
import importlib.util
import json
import pathlib
import tempfile
import tkinter as tk
import unittest
import zipfile
from xml.etree import ElementTree
from unittest.mock import patch

from football_workspace import (match_status, player_summary, parse_performance,
                                validate_schedule, score_text)
from football_scoring import DEFAULT_RULES, get_rules, validate_rules
from football_controls import MemberPicker
from football_events import make_event, event_type, AddEventDialog, EVENT_TYPES

ROOT = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("carnival", ROOT / "交运杯足球嘉年华管理系统.py")
appmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appmod)


def fixture():
    data = appmod.DataStore._default(None)
    data["persons"] = {"001": {"name": "甲", "gender": "男"}, "002": {"name": "乙", "gender": "女"}}
    data["teams"] = {"主队": {"leader": "", "members": {"001": "A"}},
                     "客队": {"leader": "", "members": {"002": "B"}}}
    data["matches"] = [{"id": 1, "team1": "主队", "team2": "客队", "date": "2026-09-20",
                         "g1": 2, "g2": 0, "lineup1": ["001"], "lineup2": ["002"]}]
    return data


class RulesTests(unittest.TestCase):
    def test_bulk_person_delete_confirmation_backup_and_rollback(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            store = appmod.DataStore(str(pathlib.Path(folder) / "data.json"))
            store.data = fixture()
            store.data["persons"]["003"] = {"name": "保留"}
            store.data["teams"]["主队"]["leader"] = "001"
            store.data["events"]["传球赛"]["regs"] = ["001", "002", "003"]
            store.save()
            before = copy.deepcopy(store.data)
            page = SimpleNamespace(store=store, tv=Mock(), refresh=Mock())
            page.tv.selection.return_value = ("001", "002")
            with patch.object(appmod.messagebox, "askyesno", return_value=False):
                appmod.PersonPage.delete(page)
            self.assertEqual(store.data, before)
            with patch.object(appmod.messagebox, "askyesno", return_value=True) as confirm, patch.object(store, "save", wraps=store.save) as save:
                appmod.PersonPage.delete(page)
                self.assertIn("2 人", confirm.call_args.args[1])
                save.assert_called_once()
            self.assertEqual(set(store.data["persons"]), {"003"})
            self.assertEqual(store.data["events"]["传球赛"]["regs"], ["003"])
            self.assertEqual(store.data["matches"][0]["lineup1"], [])
            self.assertEqual(store.data["matches"][0]["lineup2"], [])
            self.assertEqual(store.data["teams"]["主队"]["leader"], "")
            self.assertEqual(json.loads(pathlib.Path(store.path + ".previous").read_text(encoding="utf-8")), before)
            page.refresh.assert_called_once()
            after = copy.deepcopy(store.data)
            with patch.object(store, "save", side_effect=OSError("test")), self.assertRaises(OSError):
                store.remove_persons(["003"])
            self.assertEqual(store.data, after)

    def test_custom_event_types_ranking_and_scoring(self):
        data = fixture()
        data["persons"]["003"] = {"name": "未完赛", "gender": "男"}
        for kind in ("score", "time", "duel"):
            name, event = make_event("测试" + kind, kind, data["events"])
            data["events"][name] = event
            event["regs"] = ["003", "001", "002"]
            if kind == "duel":
                event["matches"] = [{"p1": "001", "p2": "002", "s1": 2, "s2": 0}]
            else:
                event["prelim"] = {"001": 10, "002": 20}
            ranked = appmod.rank_event(data, name)
            self.assertEqual(ranked[0]["id"], "002" if kind == "score" else "001")
            self.assertEqual(ranked[-1]["result"], "未完赛")
            self.assertIn(name, appmod.event_ranking_sheets(data))
            self.assertEqual(appmod.event_quota(data, name)[0], 3)
        self.assertEqual(appmod.person_category_points(data, "001")[1], .3)
        for name, kind in (("", "score"), ("测试score", "score"), ("不合法/名称", "score"), ("新赛", "bad")):
            with self.assertRaises(ValueError):
                make_event(name, kind, data["events"])
        self.assertEqual(event_type("技巧赛", {}), "time")

    def test_configured_scores_match_detail_categories_and_export(self):
        data = fixture()
        event = data["events"]["传球赛"]
        event["regs"] = ["002"]
        event["prelim"]["002"] = 0
        event["wins"]["002"] = 4
        for role, count in (("ref_team", 2), ("ref_event", 1), ("audience", 3), ("manager", 1)):
            data["roles"][role].append({"pid": "002", "name": "乙", "matches": count})
        data["roles"]["manager"].append({"pid": "002", "name": "乙", "matches": 8})
        data["signins"] = [{"pid": "002", "activity": "签到"}, {"pid": "002", "activity": "签到2"}]
        self.assertEqual(appmod.person_category_points(data, "002"), (.15, .25, .25, .15, .15, .1))
        self.assertEqual(appmod.person_detail(data, "002")[:2], (1.05, .3))
        data["score_rules"] = {**DEFAULT_RULES, "cap": 2, "team_played": .2, "event_completed": .3,
                               "female_bonus": .04, "wins_step": 3, "win_bonus": .07,
                               "ref_team": .12, "ref_event": .06, "manager": .25, "audience": .02, "signin": .01}
        self.assertEqual(appmod.person_category_points(data, "002"), (.2, .41, .3, .25, .06, .02))
        raw, capped, rows = appmod.person_detail(data, "002")
        self.assertEqual((raw, capped), (1.24, 1.24))
        self.assertEqual(round(sum(value for _, value in rows), 4), raw)
        exported = appmod.score_summary_sheet(data)
        self.assertEqual(exported["综测总表"][2][-2:], [raw, capped])
        self.assertIn("封顶2", exported["综测总表"][0][-1])
        self.assertIn("综测计算规则", exported)
        data["score_rules"]["cap"] = 0
        self.assertEqual(appmod.person_detail(data, "002")[1], 0)

    def test_rule_validation_partial_defaults_and_single_wins(self):
        self.assertEqual(get_rules({}), DEFAULT_RULES)
        self.assertEqual(get_rules({"score_rules": {"cap": .8}})["signin"], .05)
        for key, value in (("cap", "nan"), ("signin", "-1"), ("manager", "inf"),
                           ("wins_step", "0"), ("wins_step", "2.5"), ("cap", ""), ("cap", "0.12345")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_rules({key: value})
        data = fixture()
        event = data["events"]["1对1单挑赛"]
        event["regs"] = ["001"]
        event["matches"] = [{"p1": "001", "p2": "002", "s1": 2, "s2": 0}]
        data["score_rules"] = {**DEFAULT_RULES, "wins_step": 1, "win_bonus": .12}
        self.assertEqual(appmod.person_category_points(data, "001")[1], .22)

    def test_legacy_and_pending(self):
        data = fixture()
        self.assertEqual(match_status(data["matches"][0]), "已结束")
        self.assertEqual(appmod.compute_team_stats(data)["主队"]["pts"], 3)
        self.assertAlmostEqual(appmod.person_detail(data, "001")[0], 0.15)
        for status in ("未开始", "进行中", "已延期", "已取消"):
            data["matches"][0]["status"] = status
            self.assertEqual(appmod.compute_team_stats(data)["主队"]["played"], 0)
            self.assertEqual(appmod.h2h_points("主队", "客队", data["matches"]), 0)
            self.assertAlmostEqual(appmod.person_detail(data, "001")[0], 0.05)
            self.assertAlmostEqual(appmod.person_category_points(data, "001")[0], 0.05)
            self.assertEqual(player_summary(data)[0][2], 0)
        data["matches"][0].update(g1=None, g2=None, status="未开始")
        self.assertEqual(score_text(data["matches"][0]), "待定")
        appmod.rank_teams(data)
        self.assertEqual(appmod.match_detail_sheet(data)["交运杯比赛明细"][1][3], "待定")

    def test_summary_missing_scores_and_zero(self):
        data = fixture()
        m = data["matches"][0]
        m["player_stats"] = {"001": {"goals": 2, "assists": 0, "rating": 0}}
        other = copy.deepcopy(m)
        other.update(id=2, player_stats={})
        data["matches"].append(other)
        self.assertEqual(player_summary(data)[0][2:], (2, 1, 2, 0, 0))
        self.assertEqual(player_summary(data)[1][-1], "—")

    def test_validation(self):
        for number in ("nan", "inf", "-inf", "-1"):
            with self.assertRaises(ValueError):
                appmod.parse_num(number)
        for rating in ("nan", "inf", "11", "-1", "abc"):
            with self.assertRaises(ValueError):
                parse_performance("0", "0", rating, "")
        self.assertIsNone(parse_performance("0", "1", "", "") ["rating"])
        for date, time in (("2026-02-30", ""), ("2026-1-1", ""), ("2026-09-20", "25:00")):
            with self.assertRaises(ValueError):
                validate_schedule(date, time)
        validate_schedule("2028-02-29", "14:30")

    def test_roundtrip_backup_and_cascade(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(pathlib.Path(folder) / "data.json")
            store = appmod.DataStore(path)
            store.data = fixture()
            store.data["matches"][0]["player_stats"] = {"001": {"goals": 2}}
            store.save()
            before = pathlib.Path(path).read_bytes()
            store.remove_person("001")
            self.assertEqual(pathlib.Path(path + ".previous").read_bytes(), before)
            restored = appmod.DataStore(path)
            self.assertNotIn("001", restored.data["matches"][0]["player_stats"])
            self.assertNotIn("001", restored.data["matches"][0]["lineup1"])

    def test_schedule_export(self):
        data = fixture()
        data["matches"][0].update(status="未开始", g1=None, g2=None, time="15:00", venue="球场")
        sheets = appmod.match_detail_sheet(data)
        row = sheets["交运杯比赛明细"][1]
        self.assertEqual(row[7:10], ["15:00", "球场", "未开始"])
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "export.xlsx"
            appmod.export_xlsx(str(path), sheets)
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.endswith(".xml"):
                        ElementTree.fromstring(archive.read(name))
                self.assertIn("待定", archive.read("xl/worksheets/sheet1.xml").decode())


class InterfaceTests(unittest.TestCase):
    def test_multiselect_rules_save_and_calculate(self):
        original = tk.Tk.__init__
        def hidden(root, *args, **kwargs):
            original(root, *args, **kwargs)
            root.withdraw()
        with tempfile.TemporaryDirectory() as folder:
            store = appmod.DataStore(str(pathlib.Path(folder) / "test.json"))
            store.data = fixture()
            store.data["persons"]["003"] = {"name": "丙", "gender": "男"}
            store.save()
            with patch.object(appmod, "DataStore", return_value=store), patch.object(tk.Tk, "__init__", hidden):
                app = appmod.MainApp()
            errors = []
            app.report_callback_exception = lambda *args: errors.append(args)
            try:
                picker = MemberPicker(app, store.data["persons"], exclude=["003"])
                picker.top.withdraw()
                picker.tree.selection_set("001")
                picker.query.set("乙")
                app.update()
                self.assertEqual(picker.selected, {"001"})
                picker.select_visible()
                picker.query.set("")
                app.update()
                self.assertEqual(picker.selected, {"001", "002"})
                self.assertEqual(set(picker.tree.selection()), {"001", "002"})
                picker.clear()
                self.assertFalse(picker.selected)
                picker.select_visible()
                picker.confirm()
                self.assertEqual(picker.result, ["001", "002"])
                dialog = appmod.TeamDialog(app, app, "主队")
                dialog.top.withdraw()
                def choose():
                    window = next(c for c in dialog.top.winfo_children() if isinstance(c, tk.Toplevel))
                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child
                            yield from descendants(child)
                    tree = next(w for w in descendants(window) if isinstance(w, appmod.ttk.Treeview))
                    tree.selection_set(tree.get_children())
                    button = next(w for w in descendants(window) if isinstance(w, appmod.ttk.Button) and w.cget("text") == "添加所选成员")
                    button.invoke()
                app.after(20, choose)
                dialog.add_member()
                self.assertEqual(set(dialog.members), {"001", "002", "003"})
                dialog.tv.selection_set(("002", "003"))
                dialog.toggle_level()
                self.assertEqual(dialog.members["002"], "B")
                self.assertEqual(dialog.members["003"], "B")
                dialog.tv.selection_set(("002", "003"))
                dialog.remove_member()
                self.assertEqual(set(dialog.members), {"001"})
                dialog.top.destroy()
                store.data["teams"]["主队"]["members"]["003"] = "A"
                match = appmod.MatchDialog(app, app, store.data["matches"][0])
                match.top.withdraw()
                match.l1_listbox.selection_set(0, "end")
                match._batch_lineup("l1", "add")
                self.assertEqual(match._lineup_ids["l1"], {"001", "003"})
                match._batch_lineup("l1", "clear")
                self.assertEqual(match._lineup_ids["l1"], set())
                match._batch_lineup("l1", "all")
                self.assertEqual(match._lineup_ids["l1"], {"001", "003"})
                match.top.destroy()
                event = next(p for n, p in app.pages if n == "绿茵全能王单项赛")
                for label, kind in EVENT_TYPES.items():
                    def create_dialog(master, target_store):
                        dialog = AddEventDialog(master, target_store)
                        dialog.top.withdraw()
                        dialog.name.set("新增赛事" + kind)
                        dialog.kind.set(label)
                        app.after(20, dialog.save)
                        return dialog
                    with patch.object(appmod, "AddEventDialog", side_effect=create_dialog):
                        event.add_event()
                    self.assertEqual(event.event_name, "新增赛事" + kind)
                    self.assertEqual(event.nb.tab(event.tab_matches, "state"), "normal" if kind == "duel" else "disabled")
                    restored = appmod.DataStore(store.path)
                    self.assertEqual(restored.data["events"][event.event_name]["type"], kind)
                event.reg_all_lb.selection_set(0, "end")
                event.move_reg(event.reg_all_lb, event.reg_sel_lb, True)
                self.assertEqual(len(store.data["events"][event.event_name]["regs"]), 3)
                event.reg_sel_lb.selection_set(0, "end")
                with patch.object(appmod.messagebox, "askyesno", return_value=True):
                    event.move_reg(event.reg_sel_lb, event.reg_all_lb, False)
                self.assertEqual(store.data["events"][event.event_name]["regs"], [])
                score = next(p for n, p in app.pages if n == "综测加分与导出")
                score.rules_editor.variables["cap"].set("0.8")
                score.rules_editor.variables["team_played"].set("0.42")
                score.rules_editor.description.insert("end", " 测试规则原文")
                with patch.object(appmod.messagebox, "showinfo") as notice:
                    score.calculate_all()
                    notice.assert_called_once()
                score.rules_editor.save()
                self.assertEqual(get_rules(appmod.DataStore(store.path).data)["cap"], .8)
                self.assertEqual(score.tv_summary.item("001", "values")[-1], "0.42")
                self.assertIn("0.42", score.detail_total_lb.cget("text"))
                self.assertIn("已计算 3 人", score.calculation_hint.cget("text"))
                self.assertEqual(score.nb.select(), str(score.tab_summary))
                score.rules_editor.variables["wins_step"].set("0")
                with patch("football_controls.messagebox.showwarning") as warning:
                    score.rules_editor.save()
                    warning.assert_called_once()
                self.assertEqual(get_rules(store.data)["wins_step"], 2)
                app.update()
                self.assertEqual(errors, [])
            finally:
                app.destroy()

    def test_pages_dialog_and_schedule_lifecycle(self):
        original = tk.Tk.__init__
        def hidden(root, *args, **kwargs):
            original(root, *args, **kwargs)
            root.withdraw()
        with tempfile.TemporaryDirectory() as folder:
            store = appmod.DataStore(str(pathlib.Path(folder) / "test.json"))
            store.data = fixture()
            store.save()
            with patch.object(appmod, "DataStore", return_value=store), patch.object(tk.Tk, "__init__", hidden):
                app = appmod.MainApp()
            errors = []
            app.report_callback_exception = lambda *args: errors.append(args)
            try:
                for name, page in app.pages:
                    app.goto(name)
                    app.update()
                    page.refresh()
                team = next(page for name, page in app.pages if name == "交运杯团体赛")
                team.match_filter.set("未开始")
                self.assertFalse(team.tv_matches.get_children())
                team.match_filter.set("全部状态")
                self.assertEqual(len(team.tv_matches.get_children()), 1)
                dialog = appmod.MatchDialog(app, app)
                dialog.top.withdraw()
                app.update_idletasks()
                self.assertLessEqual(dialog.top.winfo_reqwidth(), 960)
                dialog.t1_var.set("主队")
                dialog.t2_var.set("客队")
                dialog.date_var.set("2026-10-01")
                dialog.time_var.set("14:30")
                dialog.venue_var.set("一号球场")
                with patch.object(appmod.messagebox, "askyesno", return_value=True):
                    dialog.ok()
                self.assertEqual(len(store.data["matches"]), 2)
                self.assertIsNone(store.data["matches"][-1]["g1"])
                dialog = appmod.MatchDialog(app, app, store.data["matches"][-1])
                dialog.top.withdraw()
                dialog.status_var.set("已结束")
                dialog.g1_var.set("1")
                dialog.g2_var.set("1")
                dialog.ok()
                self.assertEqual(appmod.compute_team_stats(store.data)["主队"]["pts"], 4)
                player = next(page for name, page in app.pages if name == "球员表现档案")
                player.refresh()
                player.tree.selection_set("001")
                player.load_detail()
                self.assertEqual(len(player.detail.get_children()), 1)
                player.detail.selection_set("1")
                player.edit()
                performance = next(child for child in player.winfo_children() if isinstance(child, tk.Toplevel))
                performance.withdraw()
                form = performance.winfo_children()[0]
                entries = [child for child in form.winfo_children() if isinstance(child, appmod.ttk.Entry)]
                for entry, value in zip(entries, ("2", "0", "8.5", "测试备注")):
                    entry.delete(0, "end")
                    entry.insert(0, value)
                next(child for child in form.winfo_children() if isinstance(child, appmod.ttk.Button)).invoke()
                self.assertEqual(store.data["matches"][0]["player_stats"]["001"]["rating"], 8.5)
                with patch.object(app, "export_records") as export:
                    player.export()
                    sheets = export.call_args.args[2]
                    self.assertEqual(len(sheets), 2)
                    self.assertEqual(sheets["逐场表现"][1][8], 8.5)
                for _, page in app.pages:
                    page.refresh()
                app.update()
                self.assertEqual(errors, [])
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
