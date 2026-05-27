"""猴子看护 - 主程序入口 (v1.8)"""
import os
import sys
import logging
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("app.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def main():
    # 高 DPI 适配：确保在不同分辨率/缩放的机器上布局一致
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("猴子看护")

    from ui_login import LoginDialog
    from ui_main import MainWindow
    from api_client import BBTreeClient

    cfg = config.load_config()

    # 登录
    login_dialog = LoginDialog(cfg)
    if login_dialog.exec_() != LoginDialog.Accepted:
        return

    client = login_dialog.client
    if not client:
        QMessageBox.critical(None, "错误", "登录客户端未初始化")
        return

    children = client.children
    if not children:
        QMessageBox.warning(None, "提示", "未找到关联的孩子信息")
        return

    logger.info("登录成功，共 %d 个宝宝", len(children))

    # 立即显示主窗口，摄像头列表由主窗口在后台线程加载（避免主线程阻塞白屏）
    window = MainWindow(client, children, [], cfg)
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
