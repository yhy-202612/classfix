import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

import classfix.ui.video as video_module
from classfix import flow, opencheck
from classfix.model import Check, Report
from classfix.ui.theme import apply_theme


def report():
    return Report("open", "课件能否打开",
                  [Check("open_com", "PowerPoint 实际打开测试", "problem", "打开失败")])


class OpenDispatchTest(unittest.TestCase):
    def test_questions_are_skipped_for_open(self):
        self.assertEqual(flow.questions("open", {}, False), [])

    def test_diagnose_routes_open_to_opencheck(self):
        with patch.object(opencheck, "inspect_open", return_value=report()) as spy:
            result = flow.diagnose("open", Path("/tmp/a.pptx"), None, None, "", {})
        spy.assert_called_once()
        self.assertEqual(result.kind, "open")

    def test_other_cases_still_read_the_deck(self):
        with patch.object(flow, "read_deck") as deck:
            deck.return_value = type("Deck", (), {"check": Check("deck", "PPT 文件结构", "unknown", "尚未检测"),
                                                  "media": [], "missing": []})()
            flow.diagnose("click", Path("/tmp/a.pptx"), None, None, "", {})
        deck.assert_called_once()


class OpenPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def page(self):
        page = video_module.VideoPage(lambda: "")
        self.addCleanup(self.delete_page, page)
        return page

    def delete_page(self, page):
        page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def texts(self, page):
        return [label.text() for label in page.findChildren(QLabel)]

    def test_symptom_list_includes_open_case(self):
        page = self.page()
        texts = self.texts(page)
        self.assertIn("课件打不开或打开就报错", texts)
        for letter in "ABCDE":
            self.assertIn(letter, texts)

    def test_open_case_asks_only_for_the_deck(self):
        page = self.page()
        page.begin("open")
        texts = self.texts(page)
        self.assertIn("PowerPoint 课件", texts)
        self.assertNotIn("原始视频（可选）", texts)

    def test_other_cases_keep_the_video_slot(self):
        page = self.page()
        page.begin("click")
        self.assertIn("原始视频（可选）", self.texts(page))

    def test_scan_short_circuits_to_diagnosis_for_open(self):
        page = self.page()
        page.begin("open")
        page.deck_path = Path("/tmp/a.pptx")
        with patch.object(page, "run_diagnosis") as run_diagnosis:
            page.scan()
        run_diagnosis.assert_called_once_with()

    def test_result_page_returns_to_file_picker(self):
        page = self.page()
        page.begin("open")
        page.latest = page.initial = report()
        page.stage = 4
        page.render()
        buttons = [button.text() for button in page.findChildren(QPushButton)]
        self.assertIn("返回选择课件", buttons)
        self.assertNotIn("返回问题", buttons)

    def test_video_case_keeps_question_back(self):
        page = self.page()
        page.begin("click")
        page.latest = page.initial = report()
        page.stage = 4
        page.render()
        buttons = [button.text() for button in page.findChildren(QPushButton)]
        self.assertIn("返回问题", buttons)


if __name__ == "__main__":
    unittest.main()
