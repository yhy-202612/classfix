"""离屏渲染 UI 截图，用于人工核对深色弹窗与右上角账号栏。"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/tmp/pqt")
sys.path.insert(0, "/sandbox/workspace/evetech-work/EveTech")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from evetech import cloud  # noqa: E402
from evetech.app import create_window  # noqa: E402
from evetech.ui.account import AccountDialog  # noqa: E402
from evetech.ui.theme import apply_theme  # noqa: E402

OUT = "/sandbox/workspace/evetech-work/ui-shots"
os.makedirs(OUT, exist_ok=True)

app = QApplication([])
directory = tempfile.mkdtemp(prefix="cfui-")

for mode, dark in (("dark", True), ("light", False)):
    window = create_window(directory)
    apply_theme(app, dark)   # create_window 内部会按设置重设主题，这里放在后面覆盖
    window.resize(1180, 805)
    window.show()
    app.processEvents()
    window.grab().save(f"{OUT}/main-{mode}.png")

    box = QMessageBox(QMessageBox.Icon.Warning, "实测放映接口",
                      "这一步会真实调用 PowerPoint 的放映接口。\n"
                      "若 PowerPoint 没有在运行，会短暂启动一个后台窗口。\n\n继续吗？",
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, window)
    box.show()
    app.processEvents()
    box.grab().save(f"{OUT}/msgbox-{mode}.png")
    box.close()

    account = cloud.Account()
    dialog = AccountDialog(window, account)
    dialog.show()
    app.processEvents()
    dialog.grab().save(f"{OUT}/account-signout-{mode}.png")
    dialog.close()

    signed = cloud.Account(token="t", phone="13800001234", points=27)
    dialog = AccountDialog(window, signed)
    dialog.show()
    app.processEvents()
    dialog.grab().save(f"{OUT}/account-signedin-{mode}.png")
    dialog.close()

    if mode == "dark":
        # 已登录状态下的主界面（右上角账号栏）
        signed = cloud.Account(token="t", phone="13800001234", points=27)
        window.account_bar.account = signed
        window.account_bar.refresh()
        app.processEvents()
        window.grab().save(f"{OUT}/main-dark-signedin.png")

    window.close()
    app.processEvents()

print("saved to", OUT, sorted(os.listdir(OUT)))
