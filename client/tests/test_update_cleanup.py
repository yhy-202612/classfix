"""更新下载的异常清理：任何异常都不应留下半截 .part 文件。

回归 1.6.3 的一个缺陷：download() 只在 HTTPError / URLError / OSError 三个分支里
删临时文件，而包体超限（UpdateError）和进度回调抛错走的是别的路径，`.part` 会残留
到下一次更新才开始清理。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from classfix import update
from classfix.update import UpdateError, download


class _FakeResponse:
    """只吐固定字节的响应，不需要起本地 HTTP 服务。"""

    headers = {}

    def __init__(self, payload):
        self._left = payload

    def read(self, size=-1):
        chunk, self._left = self._left[:size], self._left[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(payload):
    def opener(request, timeout=None):
        return _FakeResponse(payload)

    return opener


class DownloadCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # 不走真实网络，也不校验 URL 白名单：这里只测清理逻辑
        patcher = mock.patch.object(update, "check_url", side_effect=lambda url: url)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_size_cap_cleans_part(self):
        target = self.root / "big.zip"
        with mock.patch.object(update, "MAX_PACKAGE_BYTES", 1024):
            with self.assertRaises(UpdateError):
                download("https://example.invalid/big.zip", target, "0" * 64,
                         opener=_opener(b"y" * 4096))
        self.assertFalse(target.with_name(target.name + ".part").exists())

    def test_progress_callback_error_cleans_part(self):
        target = self.root / "cb.zip"

        def boom(done, total):
            raise RuntimeError("进度回调炸了")

        with self.assertRaises(RuntimeError):
            download("https://example.invalid/cb.zip", target, "0" * 64,
                     on_progress=boom, opener=_opener(b"z" * 512))
        self.assertFalse(target.with_name(target.name + ".part").exists())


if __name__ == "__main__":
    unittest.main()
