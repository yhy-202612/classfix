"""Read slide relationships and probe local media without changing the deck."""

import json
import ntpath
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from .model import Check

VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".avi", ".wmv", ".mkv", ".webm", ".mpg",
                    ".mpeg", ".m2v", ".m2ts", ".ts", ".mts", ".3gp", ".flv", ".asf", ".vob",
                    ".ogv", ".mxf", ".dv", ".hevc", ".h264"}
OPEN_XML_PRESENTATIONS = {".pptx", ".pptm", ".ppsx", ".ppsm", ".potx", ".potm"}
REL_NAMESPACES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "http://purl.oclc.org/ooxml/officeDocument/relationships",
}
SLIDE_NS = {
    "http://schemas.openxmlformats.org/presentationml/2006/main",
    "http://purl.oclc.org/ooxml/presentationml/main",
}
MAX_XML = 8 * 1024 * 1024
MAX_EXTRACT = 512 * 1024 * 1024
DRIVE = re.compile(r"^[a-zA-Z]:[\\/]")


@dataclass
class Media:
    slide: int
    location: str
    target: str
    availability: str
    content_type: str = ""
    rel_id: str = ""
    rels_part: str = ""

    @property
    def name(self):
        return ntpath.basename(unquote(self.target).rstrip("/")) or "未找到目标文件"

    @property
    def description(self):
        origin = {"embedded": "嵌入", "linked": "外部链接", "broken": "关系缺失"}[self.location]
        return f"第 {self.slide} 页 · {origin} · {self.name}"

    @property
    def state_text(self):
        """One short phrase a teacher can act on, shown next to every reference."""
        if self.availability == "remote":
            return "网络或共享路径，未检测"
        if self.availability == "unreadable":
            return "路径无法读取"
        return {
            ("embedded", "present"): "媒体已在课件内",
            ("embedded", "missing"): "课件内媒体已丢失",
            ("linked", "present"): "本机可找到源文件",
            ("linked", "missing"): "本机找不到源文件",
            ("broken", "missing"): "引用关系已损坏",
        }.get((self.location, self.availability), self.availability)


@dataclass
class Deck:
    check: Check
    media: list[Media] = field(default_factory=list)
    incomplete: int = 0

    @property
    def missing(self):
        return [media for media in self.media if media.availability == "missing"]

    def missing_summary(self):
        """Deck-wide warning, independent of which reference the teacher selected."""
        lost = self.missing
        if not lost:
            return ""
        listed = "、".join(f"第 {media.slide} 页 {media.name}" for media in lost[:3])
        if len(lost) > 3:
            listed += f" 等 {len(lost)} 条"
        return f"共 {len(self.media)} 条视频引用，其中 {len(lost)} 条在本机找不到文件：{listed}。"


def local_name(name):
    return name.rsplit("}", 1)[-1]


def relation_id(node, names):
    return next((node.get(f"{{{ns}}}{name}") for name in names for ns in REL_NAMESPACES
                 if node.get(f"{{{ns}}}{name}")), "")


def xml(archive, name):
    with archive.open(name) as stream:
        data = stream.read(MAX_XML + 1)
    if len(data) > MAX_XML:
        raise ValueError("XML 部件超过读取上限")
    # OOXML parts are UTF-8 or UTF-16. Strip NULs only for detection of declarations.
    if b"<!DOCTYPE" in data.replace(b"\x00", b"").upper():
        raise ValueError("XML 部件包含 DTD")
    return ET.fromstring(data)


def relationships(archive, part):
    base, file = posixpath.split(part)
    rels = posixpath.join(base, "_rels", file + ".rels")
    if rels not in archive.NameToInfo:
        return {}
    return {
        rel.get("Id"): (rel.get("Type", "").rsplit("/", 1)[-1],
                        rel.get("Target", ""), rel.get("TargetMode", "").lower() == "external")
        for rel in xml(archive, rels) if local_name(rel.tag) == "Relationship"
    }


def member_path(part, target):
    target = unquote(target).replace("\\", "/")
    if not target:
        return ""
    path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else
                             posixpath.join(posixpath.dirname(part), target))
    return "" if path in ("", ".", "..") or path.startswith("../") else path


def external_path(deck, target):
    if not target or target.startswith(("\\\\", "//", "\\?")):
        return None
    if DRIVE.match(target):
        raw = unquote(target)
    else:
        url = urlsplit(target)
        if url.scheme and url.scheme.lower() != "file" or url.netloc not in ("", "localhost"):
            return None
        raw = unquote(url.path)
        if url.scheme and raw.startswith("/") and DRIVE.match(raw[1:]):
            raw = raw[1:]
    if raw.startswith(("\\\\", "//")) or not raw or "\x00" in raw:
        return None
    path = (deck.parent / raw).absolute()
    if os.name == "nt" and path.drive:
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(path.anchor) == 4:
            return None
    return path


def file_state(path):
    try:
        return "present" if path.is_file() else "missing"
    except (OSError, ValueError):
        return "unreadable"


def make_media(archive, deck, slide, part, relation, mime, identifier=""):
    rels_part = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if not relation:
        return Media(slide, "broken", "", "missing", rel_id=identifier, rels_part=rels_part)
    _, target, external = relation
    if external:
        path = external_path(deck, target)
        return Media(slide, "linked", target, file_state(path) if path is not None else "remote",
                     rel_id=identifier, rels_part=rels_part)
    member = member_path(part, target)
    return Media(slide, "embedded", member, "present" if member in archive.NameToInfo else "missing",
                 mime(member), identifier, rels_part)


def slide_media(archive, deck, index, part, mime):
    rels = relationships(archive, part)
    root = xml(archive, part)
    found = {}
    groups = {"pic", "graphicFrame", "sp", "cxnSp"}
    for shape in (element for element in root.iter() if local_name(element.tag) in groups):
        elements = list(shape.iter())
        explicit = [node for node in elements if local_name(node.tag) in ("videoFile", "quickTimeFile", "video")]
        legacy_ids = [relation_id(node, ("link", "embed")) for node in explicit]
        modern = [node for node in elements if local_name(node.tag) == "media"]
        modern_ids = [relation_id(node, ("embed", "link")) for node in modern]
        for identifier in modern_ids + legacy_ids:
            relation = rels.get(identifier)
            known_video = identifier in legacy_ids
            if relation:
                kind, target, external = relation
                if kind not in ("video", "media"):
                    relation = None
                else:
                    path = target if external else member_path(part, target)
                    if not (known_video or explicit or kind == "video" or mime(path).startswith("video/")
                            or Path(unquote(target)).suffix.lower() in VIDEO_EXTENSIONS):
                        continue
            elif not known_video:
                continue
            asset = make_media(archive, deck, index, part, relation, mime, identifier)
            found[(asset.location, asset.target)] = asset
    # Relationships also cover template variants that store the video outside a standard shape.
    for identifier, relation in rels.items():
        kind, target, external = relation
        path = target if external else member_path(part, target)
        if kind == "video" or kind == "media" and (
            mime(path).startswith("video/") or Path(unquote(target)).suffix.lower() in VIDEO_EXTENSIONS
        ):
            asset = make_media(archive, deck, index, part, relation, mime, identifier)
            found[(asset.location, asset.target)] = asset
    return list(found.values())


def read_deck(deck):
    title = "PPT 文件结构"
    if deck is None:
        return Deck(Check("deck", title, "unknown", "尚未检测 · 未选择 PPTX，可单独检查原视频。"))
    if deck.suffix.lower() not in OPEN_XML_PRESENTATIONS:
        return Deck(Check("deck", title, "unknown", "尚未检测 · 请在 PowerPoint 中另存为 PPTX、PPTM、PPSX 或 PPSM。"))
    try:
        with ZipFile(deck) as archive:
            types = xml(archive, "[Content_Types].xml")
            presentation = xml(archive, "ppt/presentation.xml")
            if local_name(presentation.tag) != "presentation":
                raise ValueError("入口部件格式不正确")
            override = {n.get("PartName", "").lstrip("/"): n.get("ContentType", "")
                        for n in types if local_name(n.tag) == "Override"}
            defaults = {n.get("Extension", "").lower(): n.get("ContentType", "")
                        for n in types if local_name(n.tag) == "Default"}

            def mime(part):
                return override.get(part, defaults.get(Path(part).suffix.lower().lstrip("."), ""))

            rels = relationships(archive, "ppt/presentation.xml")
            slides = [node for node in presentation.iter()
                      if local_name(node.tag) == "sldId" and node.tag.split("}")[0].lstrip("{") in SLIDE_NS]
            found = []
            skipped = 0
            for index, slide in enumerate(slides, 1):
                try:
                    kind, target, external = rels[relation_id(slide, ("id",))]
                    if kind != "slide" or external:
                        raise ValueError("幻灯片关系无效")
                    found.extend(slide_media(archive, deck, index,
                                            member_path("ppt/presentation.xml", target), mime))
                except (KeyError, ValueError, OSError, ET.ParseError, BadZipFile, RuntimeError):
                    skipped += 1
            detail = f"读取了 {len(slides) - skipped}/{len(slides)} 张幻灯片，识别 {len(found)} 条视频引用。"
            if skipped:
                detail += f" 有 {skipped} 张幻灯片无法读取。"
            return Deck(Check("deck", title, "warning" if skipped else "ok", detail), found, skipped)
    except (KeyError, ValueError, OSError, ET.ParseError, BadZipFile, RuntimeError) as error:
        return Deck(Check("deck", title, "problem",
                          f"无法读取 PPTX 结构（{type(error).__name__}）。请在 PowerPoint 中试开文件或找原始备份。"))


@dataclass
class Probe:
    state: str
    detail: str
    container: str = ""
    brand: str = ""
    video: tuple[str, ...] = ()
    audio: tuple[str, ...] = ()
    pixels: tuple[str, ...] = ()
    width: int = 0
    height: int = 0

    @property
    def mp4(self):
        brand = self.brand.lower()
        return ("mp4" in self.container.split(",") and
                brand.startswith(("iso", "mp4", "avc", "m4v", "dash", "msnv")))


def find_ffprobe(configured=""):
    if configured:
        path = Path(configured).expanduser().absolute()
        return str(path) if path.is_file() and path.name.casefold() == "ffprobe.exe" else None
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    for candidate in (base / "ffprobe.exe", base / "tools" / "ffprobe.exe"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffprobe")


def probe_file(path, ffprobe, *, background=False):
    state = file_state(path)
    if state != "present":
        return Probe(state, "所选视频文件缺失或无法读取。")
    if not ffprobe:
        return Probe("unavailable", "尚未检测 · 找不到 FFprobe，可在设置中指定 ffprobe.exe。")
    # Restrict demuxers and protocols to local media. Passing an absolute argument avoids option parsing.
    command = [
        ffprobe, "-v", "error", "-protocol_whitelist", "file",
        "-format_whitelist", "mov,matroska,webm,avi,asf,mpeg,mpegts,mpegvideo,ogg,flv,mxf,h264,hevc,dv,rm",
        "-max_streams", "64", "-show_entries",
        "format=format_name:format_tags=major_brand:stream=codec_type,codec_name,width,height,pix_fmt:stream_disposition=attached_pic",
        "-of", "json", str(path.absolute()),
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if background:
        flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=12,
                                creationflags=flags)
        if result.returncode:
            return Probe("failed", f"FFprobe 未能识别该媒体（退出码 {result.returncode}）。可能不受探测器支持，或文件不完整。")
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        videos = [item for item in streams if item.get("codec_type") == "video"
                  and not item.get("disposition", {}).get("attached_pic")]
        audio = tuple(item.get("codec_name") or "未知" for item in streams if item.get("codec_type") == "audio")
        fmt = data.get("format", {})
        primary = videos[0] if videos else {}
        warnings = bool(result.stderr.strip())
        detail = "FFprobe 已读取流信息。" if not warnings else "FFprobe 读取了流信息，同时报告了解析错误。"
        return Probe("partial" if warnings else "ok", detail, fmt.get("format_name", ""),
                     fmt.get("tags", {}).get("major_brand", "").strip(),
                     tuple(item.get("codec_name") or "未知" for item in videos), audio,
                     tuple(item.get("pix_fmt") or "未知" for item in videos),
                     primary.get("width", 0), primary.get("height", 0))
    except subprocess.TimeoutExpired:
        return Probe("unavailable", "尚未检测 · FFprobe 超时。可先用本地播放器测试原视频。")
    except (OSError, ValueError):
        return Probe("unavailable", "尚未检测 · FFprobe 启动失败或返回格式无法识别。")


def probe_asset(deck, media, ffprobe, *, background=False):
    if media.availability != "present":
        return Probe("unavailable", "尚未检测 · 当前视频目标无法作为本地文件读取。")
    if not ffprobe:
        return Probe("unavailable", "尚未检测 · 找不到 FFprobe，可在设置中指定 ffprobe.exe。")
    if media.location == "linked":
        path = external_path(deck, media.target)
        return (probe_file(path, ffprobe, background=background) if path else
                Probe("unavailable", "尚未检测 · 网络链接。"))
    try:
        with ZipFile(deck) as archive:
            info = archive.getinfo(media.target)
            if info.file_size > MAX_EXTRACT:
                return Probe("unavailable", "尚未检测 · 嵌入视频大于 512 MB，可导出为独立文件后探测。")
            with tempfile.TemporaryDirectory(prefix="classfix-media-") as temporary:
                suffix = Path(media.target).suffix.lower()
                target = Path(temporary) / ("video" + (suffix if suffix in VIDEO_EXTENSIONS else ".bin"))
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination, 256 * 1024)
                return probe_file(target, ffprobe, background=background)
    except (OSError, BadZipFile, KeyError, RuntimeError, NotImplementedError):
        return Probe("unreadable", "嵌入媒体无法完整读取。可在 PowerPoint 中尝试重新插入原始视频。")
