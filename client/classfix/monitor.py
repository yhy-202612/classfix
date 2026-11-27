"""Silent PowerPoint monitoring and evidence-based media checks."""

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import Path
from time import time

import psutil

from PySide6.QtCore import QObject, QTimer, Signal

from .audio import read_system_volume
from .flow import standard_video
from .layered import PlaybackFailure
from .media_trace import powerpoint_target
from .model import Check
from .powerpoint_errors import read_powerpoint_media_errors
from .pptx import find_ffprobe, probe_asset, read_deck
from .ui.jobs import run_job


@dataclass(frozen=True)
class MediaProblem:
    slide: int
    category_title: str
    detail: str
    powerpoint_error: str = ""
    presentation: str = ""
    api_error: str = ""


def _scan_presentation(path, ffprobe_setting=""):
    """Read one saved Open XML presentation without changing it."""
    deck_path = Path(path)
    deck = read_deck(deck_path)
    if deck.check.status == "problem":
        return [], set(), {}
    video_slides = {media.slide for media in deck.media}
    probe = find_ffprobe(ffprobe_setting)
    problems = set()
    evidence = {}
    probed = {}
    for media in deck.media:
        checks = evidence.setdefault(media.slide, [])
        refs = {
            ("linked", "present"): ("ok", "外部链接存在。"),
            ("embedded", "present"): ("ok", "在课件内找到嵌入视频。"),
        }
        status, description = refs.get((media.location, media.availability),
                                       ("problem" if media.availability == "missing" else "unknown",
                                        media.state_text))
        reference = Check("media_reference", "PPTX 视频引用", status, description)
        if reference not in checks:
            checks.append(reference)
        if media.availability == "missing":
            title = {"linked": "外部视频链接失效", "embedded": "嵌入视频缺失",
                     "broken": "课件视频引用损坏"}[media.location]
            problems.add(MediaProblem(media.slide, title, f"{media.state_text}：{media.name}"))
            continue
        if media.availability == "unreadable":
            problems.add(MediaProblem(media.slide, "视频路径无法读取", f"{media.state_text}：{media.name}"))
            continue
        if media.availability != "present" or not probe:
            if media.availability == "present" and not probe:
                check = Check("media_encoding", "FFprobe 编码检测", "unknown", "未找到 FFprobe。")
                if check not in checks:
                    checks.append(check)
            continue
        asset = (media.location, media.target)
        if asset not in probed:
            probed[asset] = probe_asset(deck_path, media, probe, background=True)
        result = probed[asset]
        if result.state in ("ok", "partial"):
            video = " / ".join(result.video) or "无视频轨"
            audio = " / ".join(result.audio) or "无音轨"
            encoding = Check("media_encoding", "FFprobe 编码检测",
                             "ok" if result.state == "ok" and standard_video(result) else "warning",
                             f"视频 {video} · 音频 {audio} · 容器 {result.container or '未知'}。")
        else:
            encoding = Check("media_encoding", "FFprobe 编码检测", "unknown", result.detail)
        if encoding not in checks:
            checks.append(encoding)
        if result.state in ("failed", "unreadable"):
            problems.add(MediaProblem(media.slide, "媒体无法读取", f"{media.name} · {result.detail}"))
        elif result.state in ("ok", "partial") and not result.video:
            problems.add(MediaProblem(media.slide, "未检测到视频轨", f"{media.name} · {result.detail}"))
        elif result.state in ("ok", "partial") and not standard_video(result):
            video = " / ".join(result.video)
            audio = " / ".join(result.audio) or "无音轨"
            detail = (f"{media.name} · 容器：{result.container or '未知'}，视频：{video}，音频：{audio}。"
                      "与推荐组合有差异，需在本机试播。")
            problems.add(MediaProblem(media.slide, "视频兼容性风险", detail))
        elif result.state == "partial":
            problems.add(MediaProblem(media.slide, "媒体解析异常", f"{media.name} · {result.detail}"))
    return sorted(problems, key=lambda item: (item.slide, item.category_title, item.detail)), video_slides, evidence


def inspect_presentation_details(path, ffprobe_setting=""):
    problems, slides, _evidence = _scan_presentation(path, ffprobe_setting)
    return problems, slides


def inspect_presentation(path, ffprobe_setting=""):
    problems, _video_slides = inspect_presentation_details(path, ffprobe_setting)
    return problems


def _collection_item(collection, index):
    item = getattr(collection, "Item", None)
    return item(index) if callable(item) else collection(index)


def _presentation_key(path):
    return str(path).replace("\\", "/").casefold()


def _window_location(window):
    for name in ("HWND", "Hwnd"):
        try:
            return int(getattr(window, name))
        except Exception:
            pass
    try:
        return (float(window.Left), float(window.Top), float(window.Width), float(window.Height))
    except Exception:
        return 0


def _movie_shapes(slide):
    try:
        shapes = slide.Shapes
        count = int(shapes.Count)
    except Exception:
        return ()
    movies = []
    for index in range(1, count + 1):
        try:
            shape = _collection_item(shapes, index)
            if int(shape.Type) == 16 and int(shape.MediaType) == 3:
                movies.append(int(shape.Id))
        except Exception:
            continue
    return movies


def _process_for_show(location):
    if sys.platform != "win32":
        return None
    if isinstance(location, int) and location:
        pid = wintypes.DWORD()
        get_pid = ctypes.windll.user32.GetWindowThreadProcessId
        get_pid.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        get_pid.restype = wintypes.DWORD
        if get_pid(location, ctypes.byref(pid)):
            return powerpoint_target(pid.value)
        return None
    # Older Office builds expose window geometry but no HWND. Require one
    # unique PowerPoint process in our interactive session in that case.
    import os
    session_of = ctypes.windll.kernel32.ProcessIdToSessionId
    own = wintypes.DWORD()
    if not session_of(os.getpid(), ctypes.byref(own)):
        return None
    try:
        candidates = []
        for process in psutil.process_iter(["name"]):
            if (process.info["name"] or "").casefold() != "powerpnt.exe":
                continue
            other = wintypes.DWORD()
            if session_of(process.pid, ctypes.byref(other)) and other.value == own.value:
                candidates.append(process.pid)
        return powerpoint_target(candidates[0]) if len(candidates) == 1 else None
    except (OSError, psutil.Error):
        return None


class PowerPointWatcher(QObject):
    media_problem = Signal(object, object)
    low_volume = Signal(int, int, bool, object)
    playback_ok = Signal()
    status = Signal(str)
    monitoring_changed = Signal(bool)
    playback_failure = Signal(object)
    media_record = Signal(object)
    slideshow_changed = Signal(bool)

    def __init__(self, ffprobe_setting, threshold=25, parent=None, interval=900):
        super().__init__(parent)
        self.ffprobe_setting = ffprobe_setting
        self.threshold = threshold
        self.enabled = True
        self._seen_files = {}
        self._video_slides = {}
        self._media_evidence = {}
        self._pending = set()
        self._announced = set()
        self._playback_key = None
        self._low_volume_active = False
        self._audio_snapshot = (None, None)
        self._volume_announced = set()
        self._visible_powerpoint_errors = set()
        self._player_page = None
        self._player_states = {}
        self.show_location = 0
        self.slideshow_active = False
        self._com_initialized = False
        self.timer = QTimer(self)
        self.timer.setInterval(interval)
        self.timer.timeout.connect(self.poll)
        if sys.platform == "win32":
            try:
                import comtypes
                comtypes.CoInitialize()
                self._com_initialized = True
            except (ImportError, OSError):
                pass

    def start(self):
        self.timer.start()
        self.monitoring_changed.emit(True)
        self.poll()

    def stop(self):
        self.timer.stop()
        self._playback_key = None
        self._low_volume_active = False
        self._volume_announced.clear()
        self._visible_powerpoint_errors.clear()
        self._player_states.clear()
        self._player_page = None
        self.show_location = 0
        self._set_slideshow(False)
        self.playback_ok.emit()
        self.monitoring_changed.emit(False)

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if self.enabled:
            self.start()
        else:
            self.stop()

    def set_threshold(self, value):
        self.threshold = max(1, min(80, int(value)))

    def suppress_current_playback(self):
        self._low_volume_active = False

    def _set_slideshow(self, active):
        if self.slideshow_active != active:
            self.slideshow_active = active
            self.slideshow_changed.emit(active)

    def poll(self):
        if not self.enabled or sys.platform != "win32":
            return
        try:
            from comtypes.client import GetActiveObject
            powerpoint = GetActiveObject("PowerPoint.Application", dynamic=True)
        except Exception:
            self.status.emit("等待 PowerPoint")
            self.show_location = 0
            self._set_slideshow(False)
            self._update_volume(None, 0)
            self._volume_announced.clear()
            self._player_states.clear()
            self._player_page = None
            self._report_powerpoint_errors(0)
            return
        location = 0
        status = "正在监视 PowerPoint"
        try:
            location = self._inspect_slide_shows(powerpoint)
        except Exception:
            status = "PowerPoint 放映状态暂不可读"
        try:
            self._queue_open_presentations(powerpoint)
        except Exception:
            if status == "正在监视 PowerPoint":
                status = "PowerPoint 课件列表暂不可读"
        self.show_location = location
        self.status.emit(status)
        self._report_powerpoint_errors(location)

    def _report_powerpoint_errors(self, location):
        messages = read_powerpoint_media_errors(location)
        for message in messages:
            if message not in self._visible_powerpoint_errors:
                name = Path(self._playback_key[0]).name if self._playback_key else ""
                problem = MediaProblem(0, "PowerPoint 媒体报错", "检测到 PowerPoint 的媒体错误对话框。",
                                       message, presentation=name)
                self.media_problem.emit(problem, location)
                if location and self._playback_key:
                    # The text is from the slideshow process. Native dialogs
                    # do not identify the slide that raised them.
                    target = _process_for_show(location) if location else None
                    self.playback_failure.emit(PlaybackFailure(
                        time(), 0, problem.detail, target, powerpoint_error=message, presentation=name))
                else:
                    self.media_record.emit(problem)
        self._visible_powerpoint_errors = set(messages)

    def _queue_open_presentations(self, powerpoint):
        presentations = powerpoint.Presentations
        open_paths = set()
        for index in range(1, int(presentations.Count) + 1):
            presentation = _collection_item(presentations, index)
            try:
                path = Path(str(presentation.FullName))
                if not path.is_file():
                    continue
                signature = (path.stat().st_mtime_ns, path.stat().st_size)
            except (OSError, ValueError, TypeError):
                continue
            key = _presentation_key(path)
            open_paths.add(key)
            # FFprobe and extraction are the costly part. Finish one deck
            # before starting another on a classroom computer.
            if self._seen_files.get(key) == signature or self._pending:
                continue
            self._pending.add(key)
            setting = self.ffprobe_setting()
            run_job(lambda source=path, configured=setting: _scan_presentation(source, configured),
                    lambda result, source=key, stamp=signature: self._scan_finished(source, stamp, result),
                    lambda _error, source=key: self._scan_failed(source))
        for key in tuple(self._seen_files):
            if key not in open_paths:
                self._seen_files.pop(key, None)
                self._video_slides.pop(key, None)
                self._media_evidence.pop(key, None)
                self._announced = {item for item in self._announced if item[0] != key}

    def _scan_finished(self, source, signature, result):
        self._pending.discard(source)
        if not self.enabled:
            return
        self._seen_files[source] = signature
        problems, video_slides, evidence = result
        self._video_slides[source] = set(video_slides)
        self._media_evidence[source] = evidence
        for problem in problems:
            key = (source, problem)
            if key not in self._announced:
                self._announced.add(key)
                observed = replace(problem, presentation=Path(source).name)
                self.media_record.emit(observed)
                self.media_problem.emit(observed, self.show_location)

    def _scan_failed(self, source):
        self._pending.discard(source)

    def _inspect_slide_shows(self, powerpoint):
        windows = powerpoint.SlideShowWindows
        window_count = int(windows.Count)
        self._set_slideshow(window_count > 0)
        if not window_count:
            self._volume_announced.clear()
            self._player_states.clear()
            self._player_page = None
            self._update_volume(None, 0)
            return 0
        for window_index in range(1, window_count + 1):
            try:
                window = _collection_item(windows, window_index)
                view = window.View
                slide = view.Slide
                slide_number = int(slide.SlideIndex)
                try:
                    presentation = _presentation_key(slide.Parent.FullName)
                except Exception:
                    presentation = _presentation_key(window.Presentation.FullName)
            except Exception:
                continue
            location = _window_location(window)
            page = (presentation, slide_number)
            if self._player_page != page:
                self._player_states.clear()
                self._player_page = page
            movies = _movie_shapes(slide)
            cached_video = slide_number in self._video_slides.get(presentation, ())
            self._update_volume(page if movies or cached_video else None, location)
            for shape_id in movies:
                self._check_player(view, shape_id, page, location)
            return location
        return 0

    def _check_player(self, view, shape_id, page, location):
        previous_state = self._player_states.get(shape_id)
        try:
            state = int(view.Player(shape_id).State)
        except Exception as error:
            hresult = getattr(error, "hresult", None)
            if previous_state in (0, 1) and isinstance(hresult, int):
                code = hresult & 0xFFFFFFFF
                # Busy/disconnected COM and missing optional members are not
                # evidence of a playback failure.
                if code not in (0x80010001, 0x8001010A, 0x80010108, 0x800706BA, 0x800706BE,
                                0x80020003, 0x80020006):
                    self._player_states[shape_id] = None
                    self._report_player_problem(page, location,
                                                "播放或暂停后读取 PowerPoint 播放器状态失败。",
                                                f"0x{code:08X}")
            return
        self._player_states[shape_id] = state
        if state == 3 and previous_state in (0, 1):
            self._report_player_problem(page, location,
                                        "PowerPoint 播放器在播放或暂停后转为未就绪，具体原因待确认。")

    def _report_player_problem(self, page, location, detail, api_error=""):
        name = Path(page[0]).name
        problem = MediaProblem(page[1], "PowerPoint 播放状态异常", detail,
                               presentation=name, api_error=api_error)
        self.media_problem.emit(problem, location)
        checks = tuple(self._media_evidence.get(page[0], {}).get(page[1], ()))
        self.playback_failure.emit(PlaybackFailure(time(), page[1], detail, _process_for_show(location),
                                                   api_error=api_error, file_checks=checks,
                                                   volume=self._audio_snapshot[0], muted=self._audio_snapshot[1],
                                                   presentation=name))

    def _update_volume(self, page, location):
        if page != self._playback_key:
            if self._low_volume_active:
                self._low_volume_active = False
                self.playback_ok.emit()
            self._playback_key = page
        if page is None:
            self._audio_snapshot = (None, None)
            return
        volume, muted = read_system_volume()
        self._audio_snapshot = (volume, muted)
        if volume is None or muted is None:
            return
        if muted or volume < self.threshold:
            if page not in self._volume_announced:
                self._volume_announced.add(page)
                self._low_volume_active = True
                self.low_volume.emit(page[1], volume, muted, location)
        elif self._low_volume_active:
            self._low_volume_active = False
            self.playback_ok.emit()

    def shutdown(self):
        self.stop()
        if self._com_initialized:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except (ImportError, OSError):
                pass
            self._com_initialized = False


def screen_for_window(app, location):
    if isinstance(location, tuple) and len(location) == 4:
        from PySide6.QtCore import QPoint
        left, top, width, height = location
        screen = app.screenAt(QPoint(round(left + width / 2), round(top + height / 2)))
        if screen:
            return screen
    elif location:
        rectangle = wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(location, ctypes.byref(rectangle)):
            from PySide6.QtCore import QPoint
            point = QPoint((rectangle.left + rectangle.right) // 2,
                           (rectangle.top + rectangle.bottom) // 2)
            screen = app.screenAt(point)
            if screen:
                return screen
    return app.primaryScreen()
