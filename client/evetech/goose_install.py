"""Install the exact Windows v0.31 archive from the author's itch.io page."""

import hashlib
import http.cookiejar
import io
import json
import tempfile
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile

from .storage import data_directory
from .update import UpdateError, unpack


GOOSE_PAGE = "https://samperson.itch.io/desktop-goose"
_GOOSE_SIZE = 4_338_637
_GOOSE_SHA256 = "e66264065923676807fd6d7b36f7c9dc52db9ef1c5399b2811738eb5e22a30f6"
_STOCK_FILES = {
    "GooseDesktop.exe": "943fd1ea44266c5d7fa02f2b292db095a4e6ba8027a1f6c73fd60d1165e63aff",
    "GooseModdingAPI.dll": "efa31df7ab1394092395365805f913dd023cdcd21796603f133641524fb9ad79",
}
_ARCHIVE_ROOT = Path("Desktop Goose v0.31", "DesktopGoose v0.31")


class GooseInstallError(Exception):
    pass


def prepare_classroom_goose(original, mod):
    """Install just the verified runtime and EveTech's Mod into a private copy."""
    if not original_goose_available(original):
        raise GooseInstallError("Choose the extracted official Desktop Goose v0.31 folder.")
    original = Path(original)
    base = data_directory() / "desktop-goose"
    runtime = None
    try:
        base.mkdir(parents=True, exist_ok=True)
        runtime = tempfile.TemporaryDirectory(prefix="classroom-", dir=base)
        root = Path(runtime.name)
        mod_folder = root / "Assets" / "Mods" / "EveTech"
        mod_folder.mkdir(parents=True, exist_ok=True)
        (root / "Assets" / "Images" / "Memes").mkdir(parents=True, exist_ok=True)
        audio = Path("Assets", "Sound", "NotEmbedded", "Honk1.mp3")
        (root / audio.parent).mkdir(parents=True, exist_ok=True)
        pairs = [(original / name, root / name) for name in (*_STOCK_FILES, audio)]
        pairs.append((mod, mod_folder / "EveTechGooseMod.dll"))
        for source, target in pairs:
            target.write_bytes(source.read_bytes())
        if not original_goose_available(root):
            raise OSError("The original binaries changed during setup.")
        # The unapproved stock host can only wander while EveTech waits for the Mod.
        (root / "config.ini").write_text(
            "Version_DoNotEdit=1\nEnableMods=True\nSilenceSounds=True\n"
            "Task_CanAttackMouse=False\nAttackRandomly=False\n"
            "FirstWanderTimeSeconds=86400\nMinWanderingTimeSeconds=86400\nMaxWanderingTimeSeconds=86400\n",
            encoding="ascii",
        )
        return runtime
    except OSError as error:
        if runtime is not None:
            runtime.cleanup()
        raise GooseInstallError("EveTech could not prepare its Desktop Goose Mod folder.") from error


def original_goose_available(folder):
    if not folder:
        return False
    root = Path(folder)
    try:
        for name, expected in _STOCK_FILES.items():
            with (root / name).open("rb") as stream:
                content = stream.read(256 * 1024 + 1)
            if hashlib.sha256(content).hexdigest() != expected:
                return False
        return (root / "Assets" / "Sound" / "NotEmbedded" / "Honk1.mp3").is_file()
    except (OSError, ValueError):
        return False


class _DownloadPage(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.csrf = ""
        self.feed(content.decode("utf-8"))

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            fields = dict(attrs)
            if fields.get("name") == "csrf_token":
                self.csrf = fields.get("value", "")


def _official_url(url, *, archive=False):
    target = urllib.parse.urlsplit(url)
    host = target.hostname or ""
    if (target.scheme != "https" or target.port not in (None, 443)
            or target.username is not None or target.password is not None):
        raise GooseInstallError("The download link is invalid.")
    if not archive and host == "samperson.itch.io" and target.path.startswith("/desktop-goose/"):
        return target.geturl()
    if archive and (host.endswith(".itch.zone") or (
            host.startswith("itchio-mirror.") and host.endswith(".r2.cloudflarestorage.com"))):
        return target.geturl()
    raise GooseInstallError("itch.io returned an unexpected download link.")


class _ItchRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, archive=False):
        self.archive = archive

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, _official_url(newurl, archive=self.archive))


def download_archive():
    session = urllib.request.build_opener(
        _ItchRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def read_page(url, *, referer=None, csrf=None):
        headers = {"Cache-Control": "no-cache"}
        payload = None
        if csrf is not None:
            if not csrf:
                raise GooseInstallError("itch.io did not open its download form.")
            payload = urllib.parse.urlencode({"csrf_token": csrf}).encode("ascii")
            headers.update(Referer=referer, Origin="https://samperson.itch.io")
            headers["X-Requested-With"] = "XMLHttpRequest"
        request = urllib.request.Request(_official_url(url), payload, headers)
        with session.open(request, timeout=15) as response:
            return response.read(512 * 1024)

    try:
        purchase = GOOSE_PAGE + "/purchase"
        summary = read_page(GOOSE_PAGE + "/download_url", referer=purchase,
                            csrf=_DownloadPage(read_page(purchase)).csrf)
        page = _official_url(json.loads(summary)["url"])
        file = read_page(GOOSE_PAGE + "/file/6628222?source=game_download", referer=page,
                         csrf=_DownloadPage(read_page(page)).csrf)
        url = _official_url(json.loads(file)["url"], archive=True)
        downloads = urllib.request.build_opener(_ItchRedirect(archive=True))
        with downloads.open(url, timeout=25) as response:
            return response.read(_GOOSE_SIZE + 1)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise GooseInstallError("Could not download from itch.io. Try again or import the official Windows ZIP.") from error


def install_goose(archive=None):
    base = data_directory() / "desktop-goose"
    target = base / "v0.31"
    try:
        if archive is not None:
            with Path(archive).open("rb") as stream:
                content = stream.read(_GOOSE_SIZE + 1)
        elif original_goose_available(target):
            return target
        else:
            content = download_archive()
        if len(content) != _GOOSE_SIZE or hashlib.sha256(content).hexdigest() != _GOOSE_SHA256:
            raise GooseInstallError("This is not the official Desktop Goose v0.31 Windows ZIP. Download it from itch.io again.")
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="goose-", dir=base) as staging:
            unpack(io.BytesIO(content), staging)
            original = Path(staging) / _ARCHIVE_ROOT
            if not original_goose_available(original):
                raise GooseInstallError("The extracted archive is missing the Desktop Goose files.")
            if target.exists():
                if original_goose_available(target):
                    return target
                target = base / ("v0.31-" + uuid4().hex[:8])
            try:
                return original.rename(target)
            except OSError:
                if original_goose_available(target):
                    return target
                raise
    except (OSError, BadZipFile, UpdateError) as error:
        raise GooseInstallError("Could not install Desktop Goose in the EveTech data folder.") from error
