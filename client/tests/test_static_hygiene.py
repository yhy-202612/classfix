"""静态卫生检查：让「未定义的名字」「测试期望过期」在测试阶段就暴露，而不是等真机。

这两条都是 Linux 上就能跑的——真机专属用例（@skipUnless(win32)）在本地永远看不到红，
上一轮正是因此把 7 项期望当成可用，真机跑出 8 项后才 FAIL。
"""

import importlib.util
import io
import re
import unittest
from pathlib import Path


class UndefinedNameTest(unittest.TestCase):
    def test_no_undefined_names_in_package(self):
        """app.py 里曾出现 preferences.monitor.setChecked()——preferences 是另一个函数的局部变量。

        Windows 上拨动托盘「监视」开关就会 NameError，而且不崩程序，只静默不同步。
        这里用 pyflakes 把这一类问题变成一条机器可检查的规则（未安装时跳过）。
        """
        if importlib.util.find_spec("pyflakes") is None:
            self.skipTest("pyflakes 未安装")
        from pyflakes.api import checkPath
        from pyflakes.reporter import Reporter

        out, err = io.StringIO(), io.StringIO()
        root = Path(__file__).resolve().parents[1] / "classfix"
        self.assertTrue(root.is_dir())
        reporter = Reporter(out, err)
        for file in sorted(root.rglob("*.py")):
            checkPath(str(file), reporter)

        lines = (out.getvalue() + err.getvalue()).splitlines()
        offending = [line for line in lines if "undefined name" in line]
        self.assertEqual(offending, [])


class PreflightContractTest(unittest.TestCase):
    """真机体检项必须与真机用例的期望集合同步，否则 Linux 绿、Windows 红。"""

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def _product_keys(self):
        source = (self.root / "classfix" / "preflight.py").read_text(encoding="utf-8")
        keys = set(re.findall(r'Check\(\s*"([a-z_]+)"', source))
        # 音频三项由 inspect_audio 产出，preflight 只按白名单挑选
        picked = re.search(r"check\.key in \((.*?)\)", source, re.S)
        if picked:
            keys |= set(re.findall(r'"([a-z_]+)"', picked.group(1)))
        return keys

    def _expected_keys(self):
        source = (self.root / "tests" / "test_preflight.py").read_text(encoding="utf-8")
        block = re.search(r"report\.checks\},\s*(\{.*?\})\)", source, re.S)
        self.assertIsNotNone(block, "没找到真机用例里的期望集合")
        return set(re.findall(r'"([a-z_]+)"', block.group(1)))

    def test_live_case_expects_every_check_the_product_emits(self):
        product, expected = self._product_keys(), self._expected_keys()
        self.assertEqual(expected - product, set(), "真机用例期望了产品不会产出的检查项")
        self.assertEqual(product - expected, set(), "产品新增检查项后，真机用例的期望集合没跟上")
        self.assertGreaterEqual(len(product), 8)


if __name__ == "__main__":
    unittest.main()
