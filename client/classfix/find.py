"""Locate a missing slide video on this machine and point the slide link back at it."""

import ctypes
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from zipfile import ZipFile

from .pptx import VIDEO_EXTENSIONS, read_deck

# Directories that never hold lesson media and make a full scan crawl.
SKIP_DIRECTORIES = frozenset({
    "windows", "windows.old", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "recovery", "perflogs", "msocache",
    "appdata", "node_modules", ".git", ".svn", "__pycache__",
    "driverstore", "winsxs", "installer", "assembly", "servicing",
})
# Where footage is actually kept; matches here rank first.
PREFERRED = ("desktop", "downloads", "documents", "videos", "桌面", "下载", "文档", "视频")
MAX_DEPTH = 14
MIN_RATIO = 0.70
COPY_SUFFIX = re.compile(r"\s*(?:\(\d+\)|（\d+）|-\s*副本|_副本|\s+copy|-copy)\s*$", re.IGNORECASE)


@dataclass
class Candidate:
    path: Path
    score: float
    reason: str
    size: int = 0
    mtime: float = 0.0

    @property
    def size_text(self):
        size = float(self.size)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return "未知"

    @property
    def time_text(self):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.mtime)) if self.mtime else "未知"


@dataclass
class Result:
    target: str
    candidates: list = field(default_factory=list)
    scanned: int = 0
    seconds: float = 0.0
    stopped: str = ""
    error: str = ""

    @property
    def empty(self):
        return not self.candidates


def volumes():
    """Fixed and removable drives; [] off Windows so tests can drive search(roots=...)."""
    if os.name != "nt":
        return []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except (AttributeError, OSError):
        return []
    found = []
    for index in range(26):
        if not mask >> index & 1:
            continue
        root = f"{chr(65 + index)}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) in (2, 3):  # removable, fixed
            found.append(Path(root))
    return found


def home_roots():
    home = Path(os.path.expanduser("~"))
    names = ("Desktop", "Documents", "Downloads", "Videos", "桌面", "文档", "下载", "视频")
    return [home / name for name in names if (home / name).is_dir()]


def _normalised(name):
    stem, extension = os.path.splitext(name.strip())
    return (COPY_SUFFIX.sub("", stem).strip() + extension).casefold()


def score(name, wanted):
    """1.0 = same file name, lower = plausible, 0 = not worth listing."""
    base, target = name.casefold(), wanted.casefold()
    if base == target:
        return 1.0
    if _normalised(name) and _normalised(name) == _normalised(wanted):
        return 0.97
    if os.path.splitext(base)[0] == os.path.splitext(target)[0]:
        return 0.94
    ratio = SequenceMatcher(None, os.path.splitext(base)[0], os.path.splitext(target)[0]).ratio()
    return 0.3 + 0.6 * ratio if ratio >= MIN_RATIO else 0.0


def _preferred(path):
    text = str(path.parent).casefold()
    return 0.04 if any(word in text for word in PREFERRED) else 0.0


def _walk(root, wanted, deadline, stop, hits, stats):
    stack = [(Path(root), 0)]
    while stack:
        if stop and stop():
            stats["stopped"] = "已取消"
            return
        if time.monotonic() > deadline:
            stats["stopped"] = "时间到"
            return
        folder, depth = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except (OSError, ValueError):
            continue
        stats["scanned"] += 1
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth >= MAX_DEPTH or entry.name.casefold() in SKIP_DIRECTORIES:
                        continue
                    if getattr(entry, "is_junction", lambda: False)():
                        continue
                    stack.append((Path(entry.path), depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if os.path.splitext(entry.name)[1].lower() not in VIDEO_EXTENSIONS:
                    continue
                value = score(entry.name, wanted)
                if not value:
                    continue
                info = entry.stat(follow_symlinks=False)
                hits.append(Candidate(Path(entry.path), value + _preferred(Path(entry.path)),
                                      "文件名相同" if value >= 0.94 else "文件名相近",
                                      info.st_size, info.st_mtime))
            except (OSError, ValueError):
                continue


def search(target, roots=None, seconds=60.0, stop=None):
    """Scan the likely folders first, then drives, inside a time budget."""
    started = time.monotonic()
    wanted = os.path.basename(str(target).replace("\\", "/").rstrip("/"))
    result = Result(str(target))
    if not wanted or ":" in wanted or wanted in (".", ".."):
        result.error = "这条引用没有可用的文件名。"
        return result
    stats = {"scanned": 0, "stopped": ""}
    hits = []
    deadline = started + max(5.0, float(seconds))
    for root in (roots if roots is not None else home_roots() + volumes()):
        _walk(root, wanted, deadline, stop, hits, stats)
        if stats["stopped"]:
            break
    seen, kept = set(), []
    for hit in sorted(hits, key=lambda item: (-item.score, str(item.path))):
        key = str(hit.path).casefold()
        if key not in seen:
            seen.add(key)
            kept.append(hit)
    result.candidates = kept[:12]
    result.scanned = stats["scanned"]
    result.stopped = stats["stopped"]
    result.seconds = round(time.monotonic() - started, 1)
    return result


def relative_target(deck, found):
    """Prefer a path relative to the deck so the folder stays portable."""
    try:
        return os.path.relpath(Path(found), Path(deck).parent)
    except ValueError:  # different drives on Windows cannot be expressed relatively
        return str(found)


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _rewrite_rels(data, old_target, new_target):
    """Repoint every video/media relationship that points at `old_target`."""
    encoding = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    text = data.decode(encoding, "surrogateescape")
    wanted = _escape(old_target)
    hits = [0]

    def replace(match):
        tag = match.group(0)
        kind = re.search(r'\bType="([^"]*)"', tag)
        target = re.search(r'\bTarget="([^"]*)"', tag)
        if not kind or not target or kind.group(1).rsplit("/", 1)[-1] not in ("video", "media"):
            return tag
        if target.group(1) != wanted:
            return tag
        hits[0] += 1
        return re.sub(r'\bTarget="[^"]*"', 'Target="%s"' % _escape(new_target), tag, count=1)

    updated = re.sub(r"<Relationship\b[^>]*>", replace, text)
    if not hits[0]:
        raise ValueError("关系部件里没有指向该视频的链接")
    return updated.encode(encoding, "surrogateescape")


def _backup_name(deck):
    candidate = deck.with_name(deck.name + ".bak")
    if candidate.exists():
        candidate = deck.with_name(f"{deck.name}.{time.strftime('%Y%m%d%H%M%S')}.bak")
    return candidate


def repair(deck, media, found):
    """Back up the deck, point one external link at `found`, and verify before replacing."""
    deck = Path(deck)
    if media.location != "linked" or not media.rels_part:
        raise ValueError("只有外部链接的视频才能改指向；嵌入或关系已损坏的引用需要用原视频重新插入。")
    lock = deck.parent / f"~${deck.name}"
    if lock.exists():
        raise ValueError("课件正在 PowerPoint 中打开。请先保存并关闭，再回来点「指回课件」。")
    target = relative_target(deck, found)
    temporary = deck.with_name(f"{deck.name}.tmp{deck.suffix or '.pptx'}")
    with ZipFile(deck) as source, ZipFile(temporary, "w") as dest:
        hit = False
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == media.rels_part:
                data = _rewrite_rels(data, media.target, target)
                hit = True
            dest.writestr(info, data)
    if not hit:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"课件里没有 {media.rels_part}")
    rebuilt = read_deck(temporary)
    repaired = [item for item in rebuilt.media
                if item.slide == media.slide and item.location == "linked" and item.availability == "present"]
    if not repaired:
        temporary.unlink(missing_ok=True)
        raise ValueError("改写后仍然找不到目标文件，已放弃，课件未被修改。")
    backup = _backup_name(deck)
    try:
        shutil.copy2(deck, backup)
        os.replace(temporary, deck)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"课件可能正在 PowerPoint 中打开，无法写入（{type(error).__name__}）。") from error
    return backup, target


def reveal(path):
    """Open Explorer with the file selected."""
    if os.name != "nt":
        return False
    try:
        subprocess.Popen(["explorer", "/select,", str(path)])
        return True
    except OSError:
        return False


def open_folder(path):
    if os.name != "nt":
        return False
    try:
        subprocess.Popen(["explorer", str(Path(path))])
        return True
    except OSError:
        return False
