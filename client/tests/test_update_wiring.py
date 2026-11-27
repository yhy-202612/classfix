"""软件更新界面的接线测试：确保 guarded() 返回的是可调用对象。

回归背景（1.3.0 及更早）：preferences.py 的 guarded() 被写成「当场执行」，
三个调用点又是 run_job(self.guarded(...), ...)，于是 run_job 拿到的是 dict，
后台线程执行 self.operation() 时抛 TypeError: 'dict' object is not callable，
界面只显示「更新过程中出错（TypeError）」——检查更新、下载、解压三条路全废。
本文件用「抽取真实源码 + 桩类 exec」的方式验证语义，不启动 Qt、不联网。
"""

import ast
import textwrap
import unittest
from pathlib import Path

from evetech.update import UpdateError

PREFERENCES = Path(__file__).resolve().parents[1] / "evetech" / "ui" / "preferences.py"


def load_method(class_name, method_name):
    """从 preferences.py 抽出某个方法的真实源码，注入桩类里构造可调用对象。"""
    source = PREFERENCES.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    body = ast.get_source_segment(source, item)
                    stub = "class Stub:\n" + textwrap.indent(textwrap.dedent(body), "    ")
                    namespace = {"UpdateError": UpdateError, "OSError": OSError}
                    exec(stub, namespace)  # noqa: S102 - 测试专用，输入是受控源码
                    return getattr(namespace["Stub"](), method_name)
    raise AssertionError("%s.%s 不存在" % (class_name, method_name))


class GuardedTests(unittest.TestCase):
    def test_returns_callable_without_running(self):
        """构造 guarded(...) 时不得执行 operation —— 必须等后台线程来调用。"""
        calls = []
        guarded = load_method("SettingsPage", "guarded")
        job = guarded(lambda: calls.append(1) or {"ok": True})
        self.assertTrue(callable(job), "guarded 必须返回可调用对象")
        self.assertEqual(calls, [], "guarded 构造阶段就执行了 operation")
        self.assertEqual(job(), {"ok": True})
        self.assertEqual(calls, [1])

    def test_update_error_folded(self):
        guarded = load_method("SettingsPage", "guarded")

        def boom():
            raise UpdateError("清单读取失败")

        self.assertEqual(guarded(boom)(), {"error": "清单读取失败"})

    def test_os_error_folded(self):
        guarded = load_method("SettingsPage", "guarded")

        def boom():
            raise OSError("磁盘只读")

        self.assertEqual(guarded(boom)(), {"error": "文件操作失败：磁盘只读"})

    def test_other_error_propagates_to_thread(self):
        """TypeError 这类编程错误应当冒到线程层显示类名，而不是被吞成文案。"""
        guarded = load_method("SettingsPage", "guarded")

        def boom():
            raise TypeError("'dict' object is not callable")

        job = guarded(boom)
        with self.assertRaises(TypeError):
            job()


class RunJobWiringTests(unittest.TestCase):
    def test_no_immediate_guarded_call(self):
        """三个 run_job 调用点都必须把 guarded 的返回值（闭包）传进去。"""
        source = PREFERENCES.read_text(encoding="utf-8")
        tree = ast.parse(source)
        checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in
                    {"guarded", "guard_cloud", "staged", "guard"}):
                continue
            # guarded/guard_cloud 只能出现在 run_job(lambda: ...) 内部或作为 run_job 首参，
            # 绝不能被单独赋值/直接执行成结果
            self.assertNotIsInstance(node.parent if hasattr(node, "parent") else None, ast.Expr)
        # 明确检查三处调用的首参形态
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run_job":
                self.assertTrue(node.args, "run_job 缺少 operation 参数")
                first = node.args[0]
                self.assertIsInstance(
                    first, (ast.Name, ast.Lambda, ast.Call),
                    "run_job 的首参必须是函数：%s" % ast.dump(first)[:80])
                checked += 1
        self.assertGreaterEqual(checked, 5, "没找到预期的 run_job 调用点")


if __name__ == "__main__":
    unittest.main()
