"""右侧账号栏与登录对话框。

未登录时右上角显示「登录」，登录后显示手机号（打码）与积分。登录态与设置页共用同一个
cloud.Account 对象，任一入口登录/退出后两处都会同步刷新。
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from .. import cloud
from .jobs import run_job
from .widgets import button, horizontal, label


def valid_phone(text):
    text = str(text or "").strip()
    return len(text) == 11 and text.isdigit() and text[0] == "1" and text[1] in "3456789"


def masked_phone(phone):
    phone = str(phone or "").strip()
    return phone[:3] + "****" + phone[-4:] if len(phone) == 11 else (phone or "本机账号")


def guard(operation):
    """把云端异常折叠成 {error: 中文原因}，避免线程里只剩异常类名。"""
    try:
        return operation()
    except cloud.CloudError as error:
        return {"error": str(error)}


class AccountBar(QWidget):
    """主界面右上角的常驻账号区。"""

    def __init__(self, account, on_open, parent=None):
        super().__init__(parent)
        self.account = account
        self.on_open = on_open
        row = horizontal(9)
        row.setContentsMargins(0, 0, 0, 0)
        self.setLayout(row)
        self.avatar = label("", "avatar")
        self.avatar.setFixedSize(30, 30)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.avatar)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.primary = label("", "strong", wrap=False)
        self.secondary = label("", "small", wrap=False)
        text.addWidget(self.primary)
        text.addWidget(self.secondary)
        row.addLayout(text)
        self.action = button("登录", self.on_open, "primary")
        row.addWidget(self.action)
        self.refresh()

    def refresh(self):
        account = self.account
        if account.signed_in:
            self.avatar.setText("✓")
            self.primary.setText(masked_phone(account.phone))
            self.secondary.setText("剩余 %d 积分" % account.points)
            self.action.setText("账号")
            self.action.setProperty("role", "")
            self.action.setToolTip("已登录 · 点开可刷新积分或退出登录")
        else:
            self.avatar.setText("·")
            self.primary.setText("未登录")
            self.secondary.setText("云端诊断不可用")
            self.action.setText("登录")
            self.action.setProperty("role", "primary")
            self.action.setToolTip("手机号验证码登录，注册送 10 积分、每天赠 5 积分")
        # 属性变化要重新走一遍样式表，否则 role 变了颜色不跟着变
        self.action.style().unpolish(self.action)
        self.action.style().polish(self.action)


class AccountDialog(QDialog):
    """独立的账号弹窗：登录 / 刷新积分 / 退出登录。"""

    def __init__(self, parent, account, on_change=None):
        super().__init__(parent)
        self.setWindowTitle("ClassFix 账号")
        self.setModal(True)
        self.setMinimumWidth(430)
        self.account = account
        self.on_change = on_change or (lambda: None)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(9)
        layout.addWidget(label("账号与积分", "heading"))
        self.summary = label("", "strong")
        layout.addWidget(self.summary)
        layout.addWidget(label("登录后用积分调用云端 AI 诊断（每次 1~2 积分，视频 2 分）；"
                               "不登录也可以填自己的 API 密钥直连，不消耗积分。", "muted"))
        self.sign_in = QWidget()
        form = QVBoxLayout(self.sign_in)
        form.setContentsMargins(0, 6, 0, 0)
        form.setSpacing(8)
        row = horizontal(8)
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("11 位手机号")
        self.phone.setMaximumWidth(180)
        row.addWidget(self.phone)
        self.code_button = button("发送验证码", self.send_code)
        row.addWidget(self.code_button)
        self.code = QLineEdit()
        self.code.setPlaceholderText("验证码")
        self.code.setMaximumWidth(120)
        row.addWidget(self.code)
        row.addWidget(button("登录", self.do_login, "primary"))
        row.addStretch()
        form.addLayout(row)
        layout.addWidget(self.sign_in)
        self.signed_in_actions = QWidget()
        actions = QHBoxLayout(self.signed_in_actions)
        actions.setContentsMargins(0, 4, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(button("刷新积分", self.refresh_points))
        actions.addWidget(button("退出登录", self.sign_out))
        actions.addStretch()
        layout.addWidget(self.signed_in_actions)
        self.message = label("", "muted")
        layout.addWidget(self.message)
        closing = horizontal(8)
        closing.addStretch()
        closing.addWidget(button("关闭", self.accept))
        layout.addLayout(closing)
        self.cooldown = QTimer(self)
        self.cooldown.setInterval(1000)
        self.cooldown.timeout.connect(self.tick_cooldown)
        self.cooldown_left = 0
        self.paint()

    # ------------------------------------------------------------------ 状态
    def paint(self):
        account = self.account
        if account.signed_in:
            self.summary.setText("已登录 %s · 剩余 %d 积分" % (masked_phone(account.phone), account.points))
            if account.phone:
                self.phone.setText(account.phone)
        else:
            self.summary.setText("当前未登录 · 云端 AI 诊断不可用（仍可用自己的 API 密钥）")
        self.sign_in.setVisible(not account.signed_in)
        self.signed_in_actions.setVisible(account.signed_in)

    def tick_cooldown(self):
        self.cooldown_left -= 1
        if self.cooldown_left <= 0:
            self.cooldown.stop()
            self.code_button.setEnabled(True)
            self.code_button.setText("发送验证码")
        else:
            self.code_button.setText("%d 秒后可重发" % self.cooldown_left)

    # ------------------------------------------------------------------ 动作
    def send_code(self):
        phone = self.phone.text().strip()
        if not valid_phone(phone):
            self.message.setText("请输入 11 位手机号。")
            return
        self.message.setText("正在发送验证码…")
        self.code_button.setEnabled(False)

        def done(result):
            if not isinstance(result, dict) or "error" in result:
                self.code_button.setEnabled(True)
                self.code_button.setText("发送验证码")
                reason = result.get("error") if isinstance(result, dict) else "未知原因"
                self.message.setText("发送失败（%s）。请检查网络后重试。" % reason)
                return
            self.message.setText("验证码已发送至 %s，5 分钟内有效。" % result.get("masked", phone))
            self.cooldown_left = int(result.get("retryAfter") or 60)
            self.code_button.setText("%d 秒后可重发" % self.cooldown_left)
            self.cooldown.start()

        run_job(lambda: guard(lambda: cloud.request_code(self.account, phone)), done,
                lambda name: (self.code_button.setEnabled(True), self.code_button.setText("发送验证码"),
                              self.message.setText("发送失败（%s）。请检查网络后重试。" % name)))

    def do_login(self):
        phone = self.phone.text().strip()
        code = self.code.text().strip()
        if not valid_phone(phone):
            self.message.setText("请输入 11 位手机号。")
            return
        if not code:
            self.message.setText("请输入收到的验证码。")
            return
        self.message.setText("正在登录…")
        account = self.account

        def operation():
            try:
                result = cloud.login(account, phone, code)
            except cloud.CloudError as error:
                return {"error": str(error)}
            cloud.profile(account)
            return result

        def done(result):
            if "error" in result:
                self.message.setText(result["error"])
                return
            self.paint()
            self.on_change()
            self.message.setText(("已创建账号，赠送 %d 积分。" % account.points)
                                 if result.get("created") else "登录成功，剩余 %d 积分。" % account.points)

        run_job(operation, done, lambda name: self.message.setText("登录失败（%s）。" % name))

    def refresh_points(self):
        if not self.account.signed_in:
            self.message.setText("请先登录。")
            return
        self.message.setText("正在刷新积分…")

        def done(result):
            if isinstance(result, dict) and "error" in result:
                self.message.setText(result["error"])
                return
            self.paint()
            self.on_change()
            self.message.setText("当前 %d 积分。" % self.account.points)

        run_job(lambda: guard(lambda: cloud.profile(self.account)), done,
                lambda name: self.message.setText("刷新失败（%s）。" % name))

    def sign_out(self):
        self.account.token = ""
        self.account.points = 0
        self.paint()
        self.on_change()
        self.message.setText("已退出登录，云端诊断不再可用（仍可用自己的 API 密钥）。")
