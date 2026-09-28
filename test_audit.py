"""针对缺陷复现的回归检查，全部使用临时数据。"""
import copy
import json
import pathlib
import tempfile
import tkinter as tk
import unittest
import zipfile
from unittest.mock import patch
from test_football import appmod, fixture


class AuditRulesTests(unittest.TestCase):
    def test_historical_appearance_survives_roster_change(self):
        data = fixture()
        data["teams"]["主队"]["members"].clear()
        self.assertEqual(appmod.person_category_points(data, "001")[0], .15)

    def test_malformed_and_legacy_custom_data_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "data.json"
            for raw in ('{"persons":{"001":null}}', '{"events":{"新赛":{"regs":{}}}}', '{"matches":[{"g1":NaN}]}'):
                path.write_text(raw, encoding="utf-8")
                with patch.object(appmod.messagebox, "showwarning"):
                    store = appmod.DataStore(str(path))
                self.assertTrue(store.read_only)
                self.assertEqual(path.read_text(encoding="utf-8"), raw)
            path.write_text('{"events":{"旧自定义赛事":{"regs":[]}}}', encoding="utf-8")
            store = appmod.DataStore(str(path))
            self.assertFalse(store.read_only)
            self.assertEqual(store.data["events"]["旧自定义赛事"]["prelim"], {})

    def test_tied_teams_head_to_head_and_unplayed_awards(self):
        data = fixture()
        data["teams"]["第三队"] = {"leader": "", "members": {}}
        base = data["matches"][0]
        data["matches"] += [{**base, "id": 2, "team1": "第三队", "team2": "主队", "g1": 3, "g2": 0},
                            {**base, "id": 3, "team1": "客队", "team2": "第三队", "g1": 1, "g2": 0}]
        ranking = appmod.rank_teams(data)
        self.assertEqual(ranking[0]["team"], "第三队")
        data["matches"].append({**base, "id": 4, "team1": "第三队", "team2": "主队", "g1": 1, "g2": 0})
        ranking = appmod.rank_teams(data)
        self.assertEqual([r["team"] for r in ranking], ["第三队", "主队", "客队"])
        data["matches"] = []
        self.assertTrue(all(not row["award"] for row in appmod.rank_teams(data)))

    def test_qualification_requires_results(self):
        data = fixture()
        for name in ("传球赛", "技巧赛", "1对1单挑赛"):
            event = data["events"][name]
            event["regs"] = [str(i) for i in range(15)]
            self.assertEqual(appmod.qualification_ids(data, name), [])
            if name == "1对1单挑赛":
                event["matches"] = [{"p1": "1", "p2": "2", "s1": 2, "s2": 0}]
            else:
                event["prelim"] = {"1": 0, "2": None}
            self.assertEqual(appmod.qualification_ids(data, name), ["1"])

    def test_xlsx_absolute_relationship_quoted_name_and_controls(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "file.xlsx"
            name = '赛事"A&B'
            appmod.export_xlsx(path, {name: [["学号", "姓名"], ["001", "甲\x01"]]})
            self.assertEqual(appmod.read_xlsx(path), (name, [["学号", "姓名"], ["001", "甲"]]))
            with zipfile.ZipFile(path) as archive:
                files = {key: archive.read(key) for key in archive.namelist()}
            key = "xl/_rels/workbook.xml.rels"
            files[key] = files[key].replace(b'Target="worksheets/', b'Target="/xl/worksheets/')
            with zipfile.ZipFile(path, "w") as archive:
                for key, content in files.items():
                    archive.writestr(key, content)
            self.assertEqual(appmod.read_xlsx(path)[1][1], ["001", "甲"])

    def test_save_failure_external_changes_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "data.json"
            store = appmod.DataStore(str(path))
            store.data = fixture()
            store.save()
            original = path.read_bytes()
            store.data["persons"].clear()
            with patch.object(appmod.os, "replace", side_effect=OSError("模拟写入失败")), self.assertRaises(OSError):
                store.save()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(store.data, fixture())
            other = appmod.DataStore(str(path))
            other.data["persons"]["003"] = {"name": "新增"}
            other.save()
            with self.assertRaises(OSError):
                store.save()
            self.assertIn("003", json.loads(path.read_text(encoding="utf-8"))["persons"])
            path.write_text("bad json", encoding="utf-8")
            with patch.object(appmod.messagebox, "showwarning"):
                corrupt = appmod.DataStore(str(path))
            self.assertTrue(corrupt.read_only)
            with self.assertRaises(OSError):
                corrupt.save()
            self.assertEqual(path.read_text(encoding="utf-8"), "bad json")

    def test_import_rejects_empty_name_and_invalid_signin_date(self):
        class Store:
            data = fixture()
        store = Store()
        report = appmod.import_basic(store, [["学号", "姓名"], ["003", ""]])
        self.assertEqual(report["success"], 0)
        report = appmod.import_signin(store, "活动", "2026-02-30", [["学号"], ["001"]])
        self.assertEqual(report["success"], 0)
        self.assertFalse(store.data["signins"])


class AuditInterfaceTests(unittest.TestCase):
    def test_score_form_validation_event_switch_awards_and_duel(self):
        original_init = tk.Tk.__init__
        def hidden(root, *args, **kwargs):
            original_init(root, *args, **kwargs)
            root.withdraw()
        with tempfile.TemporaryDirectory() as folder:
            store = appmod.DataStore(str(pathlib.Path(folder) / "data.json"))
            store.data = fixture()
            store.data["events"]["传球赛"]["regs"] = ["001", "002"]
            store.save()
            with patch.object(appmod, "DataStore", return_value=store), patch.object(tk.Tk, "__init__", hidden):
                app = appmod.MainApp()
            errors = []
            app.report_callback_exception = lambda *args: errors.append(args)
            try:
                page = next(p for n, p in app.pages if n == "绿茵全能王单项赛")
                page.ev_cb.set("传球赛")
                page.on_event_change()
                page.tv_score.selection_set("001")
                page._on_score_select()
                self.assertEqual(page.f_final.get(), "")
                self.assertEqual(page.f_prelim.get(), "")
                before = copy.deepcopy(store.data)
                page.f_prelim.set("5")
                page.f_final.set("错误")
                with patch.object(appmod.messagebox, "showwarning"):
                    page.save_score()
                self.assertEqual(store.data, before)
                page.f_final.set("")
                page.save_score()
                self.assertEqual(store.data["events"]["传球赛"]["prelim"]["001"], 5)
                self.assertNotIn("001", store.data["events"]["传球赛"]["final"])
                page.tv_score.selection_set("001")
                page._on_score_select()
                page.ev_cb.set("技巧赛")
                page.on_event_change()
                self.assertIsNone(page.f_id)
                with patch.object(appmod.messagebox, "showwarning"):
                    page.save_score()
                self.assertEqual(store.data["events"]["技巧赛"]["prelim"], {})
                team = next(p for n, p in app.pages if n == "交运杯团体赛")
                store.data["teams"]["新增队"] = {"leader": "", "members": {}}
                store.data["persons"]["003"] = {"name": "新人"}
                team.refresh_awards()
                self.assertIn("新增队", team.aw_team_boxes["champion"].cget("values"))
                self.assertIn("003 新人", team.mvp_box.cget("values"))
                event = store.data["events"]["1对1单挑赛"]
                event["regs"] = ["001", "002"]
                dialog = appmod.OneVOneDialog(app, app, event)
                dialog.top.withdraw()
                dialog.p1_var.set("001 甲")
                dialog.p2_var.set("003 新人")
                with patch.object(appmod.messagebox, "showwarning"):
                    dialog.ok()
                self.assertIsNone(dialog.result)
                dialog.p2_var.set("002 乙")
                with patch.object(appmod.messagebox, "showwarning"):
                    dialog.ok()
                self.assertIsNone(dialog.result)
                dialog.s1_var.set("1")
                dialog.s2_var.set("0")
                dialog.ok()
                self.assertEqual(dialog.result["s1"], 1)
                app.update()
                self.assertEqual(errors, [])
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
