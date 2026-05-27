"""登录界面 - 掌通家园 PC 客户端"""
import logging
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread
from PyQt5.QtGui import QFont

from api_client import BBTreeClient

logger = logging.getLogger(__name__)


class LoginThread(QThread):
    """后台登录线程"""
    success = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, phone, password):
        super().__init__()
        self.phone = phone
        self.password = password
        self.client = None

    def run(self):
        try:
            client = BBTreeClient(phone=self.phone, password=self.password)
            client.get_appkey()
            result = client.login()
            self.client = client
            self.success.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class LoginDialog(QDialog):
    """登录对话框"""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.client = None
        self.remember_password = False
        self._login_thread = None
        self._init_ui()
        self._load_saved()

    def get_credentials(self):
        return self.phone_input.text().strip(), self.pwd_input.text()

    def _init_ui(self):
        self.setWindowTitle("掌通家园 - 登录")
        self.setFixedSize(400, 320)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(30, 20, 30, 20)

        title = QLabel("掌通家园 宝宝监控")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        title.setStyleSheet("color: #0078d4; margin-bottom: 5px;")
        layout.addWidget(title)

        subtitle = QLabel("PC 客户端")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #666; font-size: 12px; margin-bottom: 10px;")
        layout.addWidget(subtitle)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #ddd;")
        layout.addWidget(line)

        # 手机号
        phone_layout = QHBoxLayout()
        phone_label = QLabel("手机号:")
        phone_label.setFixedWidth(60)
        phone_label.setStyleSheet("font-size: 13px;")
        phone_layout.addWidget(phone_label)
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("请输入手机号码")
        self.phone_input.setMaxLength(11)
        self.phone_input.setStyleSheet(
            "QLineEdit { padding: 8px 12px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; }"
            "QLineEdit:focus { border-color: #0078d4; }"
        )
        phone_layout.addWidget(self.phone_input)
        layout.addLayout(phone_layout)

        # 密码
        pwd_layout = QHBoxLayout()
        pwd_label = QLabel("密  码:")
        pwd_label.setFixedWidth(60)
        pwd_label.setStyleSheet("font-size: 13px;")
        pwd_layout.addWidget(pwd_label)
        self.pwd_input = QLineEdit()
        self.pwd_input.setPlaceholderText("请输入密码")
        self.pwd_input.setEchoMode(QLineEdit.Password)
        self.pwd_input.setStyleSheet(
            "QLineEdit { padding: 8px 12px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; }"
            "QLineEdit:focus { border-color: #0078d4; }"
        )
        self.pwd_input.returnPressed.connect(self._on_login)
        pwd_layout.addWidget(self.pwd_input)
        layout.addLayout(pwd_layout)

        self.remember_cb = QCheckBox("记住密码")
        self.remember_cb.setStyleSheet("font-size: 12px; color: #555;")
        layout.addWidget(self.remember_cb)

        self.login_btn = QPushButton("登 录")
        self.login_btn.setFixedHeight(40)
        self.login_btn.setStyleSheet(
            "QPushButton { background-color: #0078d4; color: white; border: none; border-radius: 4px; font-size: 15px; font-weight: bold; }"
            "QPushButton:hover { background-color: #106ebe; }"
            "QPushButton:pressed { background-color: #005a9e; }"
            "QPushButton:disabled { background-color: #ccc; }"
        )
        self.login_btn.clicked.connect(self._on_login)
        layout.addWidget(self.login_btn)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #cc0000; font-size: 12px;")
        layout.addWidget(self.status_label)

        layout.addStretch()

    def _load_saved(self):
        if self.cfg.get("phone"):
            self.phone_input.setText(self.cfg["phone"])
            from config import get_password
            pwd = get_password(self.cfg)
            if pwd:
                self.pwd_input.setText(pwd)
            self.remember_cb.setChecked(self.cfg.get("remember_password", False))

    def _on_login(self):
        phone = self.phone_input.text().strip()
        password = self.pwd_input.text()

        if not phone or len(phone) != 11:
            self.status_label.setText("请输入正确的11位手机号码")
            return
        if not password:
            self.status_label.setText("请输入密码")
            return

        self.login_btn.setEnabled(False)
        self.login_btn.setText("登录中...")
        self.status_label.setText("正在连接服务器...")
        self.status_label.setStyleSheet("color: #0078d4; font-size: 12px;")

        self._login_thread = LoginThread(phone, password)
        self._login_thread.success.connect(self._on_success)
        self._login_thread.error.connect(self._on_error)
        self._login_thread.start()

    def _on_success(self, user_info: dict):
        self.client = self._login_thread.client
        self.remember_password = self.remember_cb.isChecked()

        self.cfg["phone"] = self.phone_input.text().strip()
        self.cfg["remember_password"] = self.remember_cb.isChecked()
        if self.remember_cb.isChecked():
            from config import set_password
            set_password(self.cfg, self.pwd_input.text())
        else:
            self.cfg["password_encrypted"] = ""
        from config import save_config
        save_config(self.cfg)

        self.status_label.setText("登录成功!")
        self.status_label.setStyleSheet("color: #00aa00; font-size: 12px;")
        self.login_btn.setEnabled(True)
        self.login_btn.setText("登 录")
        self.accept()

    def _on_error(self, error_msg: str):
        self.login_btn.setEnabled(True)
        self.login_btn.setText("登 录")
        self.status_label.setText(f"登录失败: {error_msg}")
        self.status_label.setStyleSheet("color: #cc0000; font-size: 12px;")
