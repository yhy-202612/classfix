import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from evetech.flow import diagnose, questions
from evetech.pptx import external_path, probe_asset, probe_file, read_deck

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
M = "http://schemas.microsoft.com/office/2007/relationships/media"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"


def make_deck(path, embedded=b"sample", target="clip%20with%20space.mp4", missing_part=False):
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml",
                         f'<Types xmlns="{TYPES}"><Default Extension="mp4" ContentType="video/mp4"/><Default Extension="m4a" ContentType="audio/mp4"/></Types>')
        archive.writestr("ppt/presentation.xml", f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst><p:sldId id="256" r:id="s2"/><p:sldId id="257" r:id="s1"/></p:sldIdLst></p:presentation>')
        archive.writestr("ppt/_rels/presentation.xml.rels", f'<Relationships xmlns="{REL}"><Relationship Id="s1" Type="{R}/slide" Target="slides/slide1.xml"/><Relationship Id="s2" Type="{R}/slide" Target="slides/slide2.xml"/></Relationships>')
        if not missing_part:
            archive.writestr("ppt/slides/slide1.xml", f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:spTree><p:pic><a:videoFile r:link="link"/></p:pic></p:spTree></p:sld>')
        archive.writestr("ppt/slides/slide2.xml", f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}" xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main"><p:spTree><p:pic><a:videoFile r:link="legacy"/><p14:media r:embed="data"/></p:pic><p:pic><a:audioFile r:link="audio"/><p14:media r:embed="audio"/></p:pic></p:spTree></p:sld>')
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", f'<Relationships xmlns="{REL}"><Relationship Id="link" Type="{R}/video" Target="{target}" TargetMode="External"/></Relationships>')
        archive.writestr("ppt/slides/_rels/slide2.xml.rels", f'<Relationships xmlns="{REL}"><Relationship Id="legacy" Type="{R}/video" Target="../media/clip.mp4"/><Relationship Id="data" Type="{M}" Target="../media/clip.mp4"/><Relationship Id="audio" Type="{M}" Target="../media/voice.m4a"/></Relationships>')
        archive.writestr("ppt/media/clip.mp4", embedded)
        archive.writestr("ppt/media/voice.m4a", b"sample")


class PptxTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.path = self.base / "lesson.pptx"

    def test_embedded_and_external_follow_presentation_order(self):
        make_deck(self.path)
        (self.base / "clip with space.mp4").write_bytes(b"video")
        deck = read_deck(self.path)
        self.assertEqual(deck.check.status, "ok")
        self.assertEqual([(m.slide, m.location, m.availability) for m in deck.media],
                         [(1, "embedded", "present"), (2, "linked", "present")])

    def test_missing_link_has_first_priority_and_is_rechecked(self):
        make_deck(self.path)
        media = read_deck(self.path).media[1]
        report = diagnose("format", self.path, media, None, "unavailable", {})
        self.assertEqual(report.findings[0].title, "视频引用失效")
        (self.base / "clip with space.mp4").write_bytes(b"video")
        retest = diagnose("format", self.path, media, None, "unavailable", {})
        self.assertEqual(retest.facts["media_status"], "present")
        self.assertFalse(any(f.title == "视频引用失效" for f in retest.findings))

    def test_network_link_is_never_probed(self):
        make_deck(self.path, target="https://example.invalid/clip.mp4")
        media = read_deck(self.path).media[1]
        self.assertEqual(media.availability, "remote")
        self.assertIsNone(external_path(self.path, "file://server/share/file.mp4"))
        self.assertIsNone(external_path(self.path, r"\\server\share\file.mp4"))
        self.assertIsNone(external_path(self.path, "file:%2f%2fserver/share/file.mp4"))

    def test_every_reference_carries_a_readable_state(self):
        make_deck(self.path)
        deck = read_deck(self.path)
        self.assertEqual([m.state_text for m in deck.media],
                         ["媒体已在课件内", "本机找不到源文件"])
        self.assertEqual(len(deck.missing), 1)
        self.assertIn("第 2 页", deck.missing_summary())
        self.assertIn("1 条", deck.missing_summary())
        (self.base / "clip with space.mp4").write_bytes(b"video")
        self.assertEqual(read_deck(self.path).missing_summary(), "")

    def test_unselected_reference_still_reports_lost_media(self):
        make_deck(self.path)
        report = diagnose("click", self.path, None, None, "unavailable", {})
        self.assertTrue(any(f.title == "课件内有视频找不到文件" for f in report.findings))
        self.assertEqual(report.facts["missing_references"], 1)
        self.assertEqual(report.facts["video_references"], 2)

    def test_healthy_selection_does_not_hide_another_broken_link(self):
        make_deck(self.path)
        deck = read_deck(self.path)
        healthy = next(m for m in deck.media if m.availability == "present")
        report = diagnose("click", self.path, healthy, None, "unavailable", {})
        self.assertEqual(report.facts["media_status"], "present")
        self.assertTrue(any(c.title == "课件其余视频引用" and c.status == "problem"
                            for c in report.checks))
        self.assertTrue(any(f.title == "课件内有视频找不到文件" for f in report.findings))

    def test_invalid_pptx_and_partial_pptx_have_distinct_status(self):
        self.path.write_bytes(b"not a zip")
        self.assertEqual(read_deck(self.path).check.status, "problem")
        make_deck(self.path, missing_part=True)
        self.assertEqual(read_deck(self.path).incomplete, 1)
        self.assertEqual(read_deck(self.path).check.status, "warning")

    def test_questions_adapt_to_symptom_and_answer(self):
        self.assertIn("trigger", [q.key for q in questions("click", {}, True)])
        self.assertIn("scope", [q.key for q in questions("picture", {}, True)])
        self.assertIn("error", [q.key for q in questions("format", {}, True)])
        self.assertIn("original_sound", [q.key for q in questions("silent", {}, False)])
        self.assertNotIn("original_sound", [q.key for q in questions("silent", {"original": "no"}, False)])
        self.assertNotIn("source", [q.key for q in questions("silent", {}, True)])

    def test_real_ffprobe_reads_codecs_for_embedded_and_linked_video(self):
        ffmpeg = os.environ.get("EVETECH_TEST_FFMPEG") or shutil.which("ffmpeg")
        ffprobe = os.environ.get("EVETECH_TEST_FFPROBE") or shutil.which("ffprobe")
        if not (ffmpeg and ffprobe):
            self.skipTest("FFmpeg and FFprobe are required for codec integration.")
        clip = self.base / "clip with space.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=size=128x72:rate=25",
                        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "0.4",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)],
                       check=True, capture_output=True, timeout=15)
        make_deck(self.path, embedded=clip.read_bytes())
        for media in read_deck(self.path).media:
            with self.subTest(media=media.location):
                probe = probe_asset(self.path, media, ffprobe)
                self.assertEqual((probe.state, probe.video, probe.audio), ("ok", ("h264",), ("aac",)))
                self.assertTrue(probe.mp4)
        self.assertEqual(probe_file(clip, None).state, "unavailable")
        alternate = self.base / "different-codec.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-i", str(clip), "-c:v", "mpeg4", "-an", str(alternate)],
                       check=True, capture_output=True, timeout=15)
        report = diagnose("format", None, None, alternate, ffprobe, {})
        self.assertEqual(report.facts["video"], ["mpeg4"])
        self.assertEqual(report.findings[0].title, "视频编码或容器可能影响兼容性")
        broken = self.base / "broken.mp4"
        broken.write_bytes(b"not an mp4 container")
        self.assertEqual(probe_file(broken, ffprobe).state, "failed")
