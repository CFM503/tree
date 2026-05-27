"""掌通家园监控查看器 - 主程序入口 (v1.5)"""
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
    app.setApplicationName("掌通家园监控")

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

    # 预获取第一个宝宝的摄像头列表和流地址
    child = children[0]
    logger.info("预获取摄像头: %s (school_id=%s)", child.get("name"), child.get("school_id"))
    client.select_child(child["child_id"], child.get("class_id", 0), child.get("school_id", 0))

    cameras = []
    try:
        cameras = client.get_camera_list()
        logger.info("获取到 %d 个摄像头", len(cameras))
        # 保持权限
        for cam in cameras:
            cam["authority"] = 0
            try:
                auth = client.get_camera_authority(camera_sn=cam.get("ZhsCarameSn", ""))
                cam["authority"] = auth.get("data", {}).get("authority", 0)
            except Exception:
                pass
        # 获取在线摄像头的流地址
        authorized = [c for c in cameras if c.get("authority") == 1]
        if authorized:
            urls = client.get_all_stream_urls(authorized)
            for cam in authorized:
                name = cam.get("ChannelName", "")
                if name in urls:
                    cam["stream_url"] = urls[name]
    except Exception as e:
        logger.warning("预获取摄像头失败: %s", e)

    # 显示主窗口（只传有权限的摄像头）
    authorized_cameras = [c for c in cameras if c.get("authority") == 1]
    window = MainWindow(client, children, authorized_cameras, cfg)
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
