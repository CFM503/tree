"""加载遮罩组件

半透明遮罩 + 旋转动画 + 文字提示，可叠加在任何 QWidget 上。
"""
import math
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QSizePolicy
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QRectF
from PyQt5.QtGui import QPainter, QPen, QColor, QFont


class LoadingOverlay(QWidget):
    """加载遮罩：显示旋转动画 + 提示文字 + 可选重试按钮"""

    retry_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAutoFillBackground(False)
        self.hide()

        self._message = "加载中..."
        self._show_spinner = True
        self._angle = 0

        # 旋转动画定时器
        self._spin_timer = QTimer(self)
        self._spin_timer.timeout.connect(self._rotate)
        self._spin_interval = 30  # ~33fps

        # 布局
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        # 文字标签
        self._label = QLabel(self._message)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color: white; font-size: 13px; background: transparent;")
        layout.addWidget(self._label)

        # 重试按钮（默认隐藏）
        self._retry_btn = QPushButton("🔄 重试")
        self._retry_btn.setFixedSize(80, 30)
        self._retry_btn.setCursor(Qt.PointingHandCursor)
        self._retry_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,40);
                color: white;
                border: 1px solid rgba(255,255,255,80);
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,70);
            }
        """)
        self._retry_btn.clicked.connect(self.retry_clicked.emit)
        self._retry_btn.hide()
        layout.addWidget(self._retry_btn, alignment=Qt.AlignCenter)

    def show_loading(self, message: str = "加载中..."):
        """显示加载状态（旋转动画 + 文字）"""
        self._message = message
        self._label.setText(message)
        self._show_spinner = True
        self._retry_btn.hide()
        self._angle = 0
        self._spin_timer.start(self._spin_interval)
        self._resize_to_parent()
        self.show()
        self.raise_()

    def hide_loading(self):
        """隐藏遮罩"""
        self._spin_timer.stop()
        self.hide()

    def set_error(self, message: str, show_retry: bool = True):
        """显示错误状态"""
        self._message = message
        self._label.setText(message)
        self._show_spinner = False
        self._spin_timer.stop()
        if show_retry:
            self._retry_btn.show()
        else:
            self._retry_btn.hide()
        self._resize_to_parent()
        self.show()
        self.raise_()
        self.update()

    def _rotate(self):
        self._angle = (self._angle + 8) % 360
        self.update()

    def _resize_to_parent(self):
        if self.parent():
            self.setGeometry(0, 0, self.parent().width(), self.parent().height())

    def resizeEvent(self, event):
        self._resize_to_parent()
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 半透明背景
        painter.fillRect(self.rect(), QColor(0, 0, 0, 160))

        # 旋转弧线
        if self._show_spinner:
            center_x = self.width() / 2
            center_y = self.height() / 2 - 20
            radius = 18
            rect = QRectF(center_x - radius, center_y - radius, radius * 2, radius * 2)

            pen = QPen(QColor(255, 255, 255, 60), 3)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawEllipse(rect)

            pen.setColor(QColor(0, 120, 212))
            painter.setPen(pen)
            painter.drawArc(rect, int(self._angle * 16), int(90 * 16))

        painter.end()
