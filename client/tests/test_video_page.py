import tempfile
import unittest
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

import classfix.ui.video as video_module
from classfix import find
from classfix.pptx import read_deck
from classfix.ui.theme import apply_theme
from test_find import build



class VideoRecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.original_search = video_module.search
        self.addCleanup(setattr, video_module, "search", self.original_search)
        video_module.search = lambda target, stop=None: find.search(target, roots=[self.root], stop=stop)

    def page(self, deck, case="click"):
        page = video_module.VideoPage(lambda: "")
        page.case = case
        page.deck_path = deck
        page.deck = read_deck(deck)
        page.stage = 2
        page.render()
        return page

    def texts(self, page):
        return [label.text() for label in page.findChildren(QLabel)]

    def buttons(self, page):
        return {item.text(): item for item in page.findChildren(QPushButton)}

    def choices(self, page):
        return [combo.itemText(i) for combo in page.findChildren(QComboBox) for i in range(combo.count())]

    def wait(self, predicate, seconds=8):
        for _ in range(int(seconds * 100)):
            if predicate():
                return True
            QTest.qWait(10)
        return False

    def test_broken_link_offers_local_search_and_repairs_deck(self):
        deck = self.root / "lesson.pptx"
        build(deck, target=r"D:\教学素材\光合作用.mp4")
        (self.root / "下载").mkdir()
        video = self.root / "下载" / "光合作用.mp4"
        video.write_bytes(b"v" * 128)
        page = self.page(deck)
        self.assertIn("在本机查找这个视频", self.buttons(page))
        self.assertTrue(any(text.startswith("⚠") for text in self.texts(page)))

        self.buttons(page)["在本机查找这个视频"].click()
        self.assertTrue(self.wait(lambda: page.finder and page.finder.get("state") == "done"))
        self.assertEqual(len(page.finder["result"].candidates), 1)

        self.buttons(page)["指回课件"].click()
        self.assertTrue(self.wait(lambda: page.finder.get("note")))
        self.assertTrue((self.root / "lesson.pptx.bak").is_file())
        self.assertEqual(read_deck(deck).media[0].availability, "present")
        self.assertEqual(page.media.availability, "present")

    def test_healthy_or_embedded_references_hide_the_search(self):
        healthy = self.root / "healthy.pptx"
        build(healthy, target="../media/clip.mp4", external=False,
              parts={"ppt/media/clip.mp4": b"v" * 8})
        page = self.page(healthy)
        self.assertNotIn("在本机查找这个视频", self.buttons(page))
        self.assertTrue(any("媒体已在课件内" in text for text in self.choices(page)))

        embedded = self.root / "embedded.pptx"
        build(embedded, target="../media/missing.mp4", external=False)
        page = self.page(embedded)
        self.assertNotIn("在本机查找这个视频", self.buttons(page))
        self.assertTrue(any("课件内媒体已丢失" in text for text in self.choices(page)))

    def test_no_candidate_reports_plainly_without_writing(self):
        deck = self.root / "lesson.pptx"
        build(deck, target=r"D:\教学素材\光合作用.mp4")
        page = self.page(deck)
        self.buttons(page)["在本机查找这个视频"].click()
        self.assertTrue(self.wait(lambda: page.finder and page.finder.get("state") == "done"))
        self.assertTrue(page.finder["result"].empty)
        self.assertTrue(any("没有找到同名" in text for text in self.texts(page)))
        self.assertEqual(list(self.root.glob("*.bak")), [])


if __name__ == "__main__":
    unittest.main()
