"""深色主题弹窗配色 + 右侧账号栏（平台无关，可在 Linux offscreen 跑）。"""

import unittest

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from classfix import cloud
from classfix.ui.account import AccountBar, AccountDialog, masked_phone, valid_phone
from classfix.ui.theme import apply_theme


def luminance(color):
    return (color.red() + color.green() + color.blue()) / 3


class ThemeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dark_palette_is_actually_dark(self):
        apply_theme(self.app, dark=True)
        window = self.app.palette().color(QPalette.ColorRole.Window)
        text = self.app.palette().color(QPalette.ColorRole.WindowText)
        self.assertLess(luminance(window), 90, "深色主题的窗口底色仍然是浅色：%s" % window.name())
        self.assertGreater(luminance(text), 150, "深色主题的文字颜色太暗：%s" % text.name())

    def test_light_palette_is_actually_light(self):
        apply_theme(self.app, dark=False)
        window = self.app.palette().color(QPalette.ColorRole.Window)
        self.assertGreater(luminance(window), 200)

    def test_dialogs_and_menus_get_explicit_background(self):
        """样式表必须显式覆盖弹窗/菜单底色，否则深色下会出现白底浅字。"""
        apply_theme(self.app, dark=True)
        sheet = self.app.styleSheet()
        flat = sheet.replace("\n", " ")
        for selector in ("QDialog", "QMessageBox", "QMenu", "QToolTip"):
            self.assertIn(selector, sheet, "样式表缺少 %s 规则" % selector)
        # 弹窗那一组规则必须带 background
        popup_rule = flat.split("QDialog")[1].split("}")[0]
        self.assertIn("background", popup_rule, "弹窗规则里没有设底色")

    def test_message_box_uses_theme_background_not_system_white(self):
        apply_theme(self.app, dark=True)
        box = QMessageBox()
        self.addCleanup(box.deleteLater)
        dialog = QDialog()
        self.addCleanup(dialog.deleteLater)
        for widget in (box, dialog):
            # 样式表生效后，Qt 会把这些部件绘制成主题底色；这里核对外层调色板不再是纯白
            background = widget.palette().color(QPalette.ColorRole.Window)
            self.assertLess(luminance(background), 120,
                            "%s 的背景仍是浅色（%s），深色主题下会白底浅字"
                            % (type(widget).__name__, background.name()))
        apply_theme(self.app, dark=False)


class AccountBarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def make_bar(self, signed_in=False, phone="13800001234", points=12):
        account = cloud.Account()
        if signed_in:
            account.token = "token"
            account.phone = phone
            account.points = points
        bar = AccountBar(account, lambda: None)
        self.addCleanup(bar.deleteLater)
        return bar, account

    def test_signed_out_shows_login_entry(self):
        bar, _account = self.make_bar()
        self.assertEqual(bar.primary.text(), "未登录")
        self.assertEqual(bar.action.text(), "登录")
        self.assertIn("云端诊断不可用", bar.secondary.text())

    def test_signed_in_shows_masked_phone_and_points(self):
        bar, _account = self.make_bar(signed_in=True, points=27)
        self.assertEqual(bar.primary.text(), "138****1234")
        self.assertIn("27 积分", bar.secondary.text())
        self.assertEqual(bar.action.text(), "账号")

    def test_refresh_follows_account_changes(self):
        bar, account = self.make_bar()
        account.token = "t"
        account.phone = "13912345678"
        account.points = 5
        bar.refresh()
        self.assertEqual(bar.primary.text(), "139****5678")
        account.token = ""
        bar.refresh()
        self.assertEqual(bar.action.text(), "登录")

    def test_masked_phone_and_valid_phone(self):
        self.assertEqual(masked_phone("13800001234"), "138****1234")
        self.assertEqual(masked_phone(""), "本机账号")
        self.assertTrue(valid_phone("13800001234"))
        self.assertFalse(valid_phone("12800001234"))
        self.assertFalse(valid_phone("1380000123"))


class AccountDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def test_signed_out_shows_login_form(self):
        account = cloud.Account()
        dialog = AccountDialog(None, account)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.sign_in.isVisibleTo(dialog))
        self.assertFalse(dialog.signed_in_actions.isVisibleTo(dialog))
        self.assertIn("未登录", dialog.summary.text())

    def test_signed_in_shows_actions_and_summary(self):
        account = cloud.Account(token="t", phone="13800001234", points=9)
        dialog = AccountDialog(None, account)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.sign_in.isVisibleTo(dialog))
        self.assertTrue(dialog.signed_in_actions.isVisibleTo(dialog))
        self.assertIn("138****1234", dialog.summary.text())
        self.assertIn("9 积分", dialog.summary.text())

    def test_sign_out_clears_account_and_notifies(self):
        account = cloud.Account(token="t", phone="13800001234", points=9)
        calls = []
        dialog = AccountDialog(None, account, on_change=lambda: calls.append(1))
        self.addCleanup(dialog.deleteLater)
        dialog.sign_out()
        self.assertEqual(account.token, "")
        self.assertEqual(calls, [1])
        self.assertTrue(dialog.sign_in.isVisibleTo(dialog))
