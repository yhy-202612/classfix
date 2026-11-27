import os
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from evetech import find
from evetech.pptx import read_deck

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"


def build(path, target=r"D:\教学素材\光合作用.mp4", external=True, with_rel=True, parts=None):
    """Minimal deck whose only video is a link to `target`."""
    with ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   f'<Types xmlns="{TYPES}"><Default Extension="mp4" ContentType="video/mp4"/></Types>')
        z.writestr("ppt/presentation.xml",
                   f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>'
                   f'<p:sldId id="256" r:id="s1"/></p:sldIdLst></p:presentation>')
        z.writestr("ppt/_rels/presentation.xml.rels",
                   f'<Relationships xmlns="{REL}">'
                   f'<Relationship Id="s1" Type="{R}/slide" Target="slides/slide1.xml"/></Relationships>')
        z.writestr("ppt/slides/slide1.xml",
                   f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree><p:pic>'
                   f'<p:nvPicPr><p:cNvPr id="2" name="视频"/><p:cNvPicPr/>'
                   f'<p:nvPr><a:videoFile r:link="rId3"/></p:nvPr></p:nvPicPr>'
                   f'<p:blipFill><a:blip/></p:blipFill><p:spPr/></p:pic></p:spTree></p:cSld></p:sld>')
        body = (f'<Relationship Id="rId3" Type="{R}/video" Target="{target}"'
                + (' TargetMode="External"' if external else "") + "/>") if with_rel else ""
        z.writestr("ppt/slides/_rels/slide1.xml.rels",
                   f'<Relationships xmlns="{REL}">{body}</Relationships>')
        for name, payload in (parts or {}).items():
            z.writestr(name, payload)


class ScoreTest(unittest.TestCase):
    def test_identical_and_copy_names_rank_above_similar_names(self):
        self.assertEqual(find.score("光合作用.mp4", "光合作用.mp4"), 1.0)
        self.assertEqual(find.score("光合作用 (1).mp4", "光合作用.mp4"), 0.97)
        self.assertEqual(find.score("光合作用-副本.mp4", "光合作用.mp4"), 0.97)
        self.assertEqual(find.score("光合作用.avi", "光合作用.mp4"), 0.94)
        similar = find.score("光合作用实验演示.mp4", "光合作用演示.mp4")
        self.assertTrue(0.3 < similar < 0.94, similar)
        self.assertEqual(find.score("开学第一课.mp4", "光合作用演示.mp4"), 0.0)


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "下载").mkdir()
        (self.root / "旧课件" / "深一层").mkdir(parents=True)
        (self.root / "Windows").mkdir()
        for relative, payload in (("下载/光合作用.mp4", b"a" * 32),
                                  ("旧课件/深一层/光合作用演示.mp4", b"b" * 16),
                                  ("旧课件/开学第一课.mp4", b"c" * 8),
                                  ("下载/光合作用.mp4.txt", b"not a video"),
                                  ("Windows/光合作用.mp4", b"skipped dir")):
            (self.root / relative).write_bytes(payload)

    def tearDown(self):
        self.directory.cleanup()

    def test_finds_name_matches_and_skips_system_folders(self):
        result = find.search(r"D:\教学素材\光合作用.mp4", roots=[self.root], seconds=10)
        names = [item.path.name for item in result.candidates]
        self.assertEqual(names[0], "光合作用.mp4")
        self.assertIn("光合作用演示.mp4", names)
        self.assertNotIn("光合作用.mp4.txt", names)
        self.assertFalse(any("Windows" in str(item.path) for item in result.candidates))
        self.assertGreater(result.candidates[0].score, result.candidates[1].score)
        self.assertEqual(result.candidates[0].reason, "文件名相同")

    def test_stop_flag_ends_scan_without_crashing(self):
        result = find.search(r"D:\a\光合作用.mp4", roots=[self.root], seconds=10, stop=lambda: True)
        self.assertEqual(result.stopped, "已取消")

    def test_empty_name_reports_error(self):
        self.assertTrue(find.search("D:\\", roots=[self.root]).error)


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.deck = self.root / "lesson.pptx"
        build(self.deck)
        self.video = self.root / "素材" / "光合作用.mp4"
        self.video.parent.mkdir()
        self.video.write_bytes(b"v" * 64)

    def tearDown(self):
        self.directory.cleanup()

    def test_rewrite_points_link_at_found_file_and_keeps_other_parts(self):
        media = read_deck(self.deck).media[0]
        self.assertEqual((media.location, media.availability), ("linked", "missing"))
        before = {i.filename: i.CRC for i in ZipFile(self.deck).infolist()}
        backup, target = find.repair(self.deck, media, self.video)
        self.assertTrue(backup.is_file())
        self.assertTrue(target.endswith("光合作用.mp4"))
        repaired = read_deck(self.deck)
        self.assertEqual(repaired.media[0].availability, "present")
        after = {i.filename: i.CRC for i in ZipFile(self.deck).infolist()}
        for name in before:
            if name != "ppt/slides/_rels/slide1.xml.rels":
                self.assertEqual(before[name], after[name], name)

    def test_target_uses_relative_path_when_possible(self):
        media = read_deck(self.deck).media[0]
        _, target = find.repair(self.deck, media, self.video)
        self.assertNotIn(str(self.root), target)
        self.assertIn("素材", target)

    def test_embedded_and_broken_references_are_refused(self):
        build(self.root / "embedded.pptx", target="../media/missing.mp4", external=False)
        media = read_deck(self.root / "embedded.pptx").media[0]
        self.assertEqual(media.location, "embedded")
        with self.assertRaises(ValueError):
            find.repair(self.root / "embedded.pptx", media, self.video)
        build(self.root / "broken.pptx", with_rel=False)
        media = read_deck(self.root / "broken.pptx").media[0]
        self.assertEqual(media.location, "broken")
        with self.assertRaises(ValueError):
            find.repair(self.root / "broken.pptx", media, self.video)

    def test_unknown_relationship_leaves_deck_untouched(self):
        media = read_deck(self.deck).media[0]
        ghost = type(media)(media.slide, media.location, r"D:\别的目录\完全不同的.mp4",
                            media.availability, media.content_type)
        original = self.deck.read_bytes()
        with self.assertRaises(ValueError):
            find.repair(self.deck, ghost, self.video)
        self.assertEqual(self.deck.read_bytes(), original)
        self.assertFalse(list(self.root.glob("*.tmp*")))
        self.assertFalse(list(self.root.glob("*.bak")))

    def test_unreadable_target_is_not_written(self):
        media = read_deck(self.deck).media[0]
        ghost = self.root / "素材" / "不存在.mp4"
        original = self.deck.read_bytes()
        with self.assertRaises(ValueError):
            find.repair(self.deck, media, ghost)
        self.assertEqual(self.deck.read_bytes(), original)
        self.assertFalse(list(self.root.glob("*.tmp*")))
        self.assertFalse(list(self.root.glob("*.bak")))


if __name__ == "__main__":
    unittest.main()


class LockedDeckTest(unittest.TestCase):
    def test_open_deck_is_refused_before_any_write(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        deck = root / "lesson.pptx"
        build(deck)
        video = root / "光合作用.mp4"
        video.write_bytes(b"v")
        (root / "~$lesson.pptx").write_bytes(b"lock")
        media = read_deck(deck).media[0]
        original = deck.read_bytes()
        with self.assertRaises(ValueError) as caught:
            find.repair(deck, media, video)
        self.assertIn("正在 PowerPoint 中打开", str(caught.exception))
        self.assertEqual(deck.read_bytes(), original)
        self.assertFalse(list(root.glob("*.bak")))
