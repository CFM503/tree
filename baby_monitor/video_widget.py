"""mpv 视频播放控件

基于 mpv.exe 的 PyQt5 视频播放控件，通过 --wid 嵌入到 Qt 窗口。
支持 H.265/H.264、RTMP/HLS/ezopen 等所有格式。
"""
import os
import sys
import time
import signal
import logging
import threading
import subprocess
from pathlib import Path
from datetime import datetime

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QMenu, QAction, QApplication
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QMouseEvent, QPalette, QColor

from loading_overlay import LoadingOverlay

logger = logging.getLogger(__name__)

# 查找 mpv.exe
_MPV_DIR = Path(__file__).parent / "mpg"
MPV_PATH = str(_MPV_DIR / "mpv.exe")
MPV_AVAILABLE = os.path.isfile(MPV_PATH)

if not MPV_AVAILABLE:
    logger.warning("mpv.exe 未找到: %s，视频播放不可用", MPV_PATH)


class VideoWidget(QWidget):
    """单个摄像头视频播放控件"""

    double_clicked = pyqtSignal(int)
    recording_started = pyqtSignal(str)
    recording_stopped = pyqtSignal(str, str)

    def __init__(self, index: int = 0, parent=None):
        super().__init__(parent)
        self.index = index
        self.camera_name = f"摄像头 {index + 1}"
        self.stream_url = ""
        self.is_playing = False
        self.is_recording = False
        self.is_fullscreen = False

        self._mpv_proc = None
        self._recording_path = ""
        self._save_dir = ""
        self._segment_timer = QTimer(self)
        self._segment_timer.setSingleShot(True)
        self._segment_timer.timeout.connect(self._rotate_recording)

        # 健康监测 & 自动重连
        self._retry_count = 0
        self._max_retries = 3
        self._play_start_time = 0.0
        self._loading_hidden = False
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._check_health)

        self._init_ui()

    def _init_ui(self):
        """初始化UI"""
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("""
            VideoWidget {
                background-color: #1a1a2e;
                border: 2px solid #333;
                border-radius: 4px;
            }
            VideoWidget:hover {
                border: 2px solid #0078d4;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 视频画面区域
        self._video_frame = QWidget()
        self._video_frame.setStyleSheet("background-color: #0a0a1a;")
        self._video_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._video_frame)

        # 加载遮罩（叠加在视频画面上）
        self._loading_overlay = LoadingOverlay(self._video_frame)
        self._loading_overlay.retry_clicked.connect(self._on_retry)

        # 信息栏
        info_bar = QWidget()
        info_bar.setFixedHeight(30)
        info_bar.setStyleSheet("background-color: rgba(0,0,0,180);")
        info_layout = QHBoxLayout(info_bar)
        info_layout.setContentsMargins(8, 2, 8, 2)

        self._name_label = QLabel(self.camera_name)
        self._name_label.setStyleSheet("color: white; font-size: 12px;")
        info_layout.addWidget(self._name_label)

        info_layout.addStretch()

        self._status_label = QLabel("● 离线")
        self._status_label.setStyleSheet("color: #ff4444; font-size: 11px;")
        info_layout.addWidget(self._status_label)

        self._rec_indicator = QLabel("● REC")
        self._rec_indicator.setStyleSheet("color: #ff0000; font-size: 11px; font-weight: bold;")
        self._rec_indicator.setVisible(False)
        info_layout.addWidget(self._rec_indicator)

        layout.addWidget(info_bar)

        # 控制栏
        ctrl_bar = QWidget()
        ctrl_bar.setFixedHeight(36)
        ctrl_bar.setStyleSheet("background-color: rgba(0,0,0,150);")
        ctrl_layout = QHBoxLayout(ctrl_bar)
        ctrl_layout.setContentsMargins(4, 2, 4, 2)

        self._btn_play = QPushButton("▶ 播放")
        self._btn_play.setFixedWidth(60)
        self._btn_play.setStyleSheet(self._button_style())
        self._btn_play.clicked.connect(self.toggle_play)
        ctrl_layout.addWidget(self._btn_play)

        self._btn_record = QPushButton("⏺ 录像")
        self._btn_record.setFixedWidth(60)
        self._btn_record.setStyleSheet(self._button_style())
        self._btn_record.clicked.connect(self.toggle_record)
        ctrl_layout.addWidget(self._btn_record)

        self._btn_snapshot = QPushButton("📸 截图")
        self._btn_snapshot.setFixedWidth(60)
        self._btn_snapshot.setStyleSheet(self._button_style())
        self._btn_snapshot.clicked.connect(self.take_snapshot)
        ctrl_layout.addWidget(self._btn_snapshot)

        ctrl_layout.addStretch()

        self._btn_fullscreen = QPushButton("⛶ 全屏")
        self._btn_fullscreen.setFixedWidth(60)
        self._btn_fullscreen.setStyleSheet(self._button_style())
        self._btn_fullscreen.clicked.connect(self.toggle_fullscreen)
        ctrl_layout.addWidget(self._btn_fullscreen)

        layout.addWidget(ctrl_bar)

        # 未播放时的占位图
        self._placeholder = QLabel("双击播放")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet("color: #666; font-size: 16px; background: transparent;")
        layout.addWidget(self._placeholder)

        # 录像闪烁定时器
        self._blink_timer = QTimer()
        self._blink_timer.timeout.connect(self._blink_rec)
        self._blink_visible = False

    def _button_style(self) -> str:
        return """
            QPushButton {
                background-color: rgba(255,255,255,30);
                color: white;
                border: 1px solid rgba(255,255,255,50);
                border-radius: 3px;
                font-size: 11px;
                padding: 2px 4px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,60);
            }
            QPushButton:pressed {
                background-color: rgba(255,255,255,80);
            }
        """

    def set_camera(self, name: str, stream_url: str, is_online: bool = True):
        """设置摄像头信息"""
        self.camera_name = name
        self.stream_url = stream_url
        self._retry_count = 0  # 重置重连计数
        self._name_label.setText(name)
        if is_online:
            self._status_label.setText("● 在线")
            self._status_label.setStyleSheet("color: #44ff44; font-size: 11px;")
        else:
            self._status_label.setText("● 离线")
            self._status_label.setStyleSheet("color: #ff4444; font-size: 11px;")

    def play(self, url: str = None):
        """播放视频流"""
        if not MPV_AVAILABLE:
            logger.warning("mpv 不可用")
            self._loading_overlay.set_error("mpv.exe 未找到", show_retry=False)
            return

        if url:
            self.stream_url = url

        if not self.stream_url:
            logger.warning("无视频流地址")
            return

        # 停止当前播放
        self.stop()

        # 显示加载动画
        self._loading_overlay.show_loading("正在连接...")
        self._loading_hidden = False
        self._play_start_time = time.time()

        try:
            # 确保窗口已渲染
            self._video_frame.show()
            QApplication.processEvents()

            from config import load_config
            cfg = load_config()
            timeout = cfg.get("network_timeout_seconds", 15)

            cmd = [
                MPV_PATH,
                f"--wid={wid}",
                "--terminal=no",
                "--really-quiet",
                "--keep-open=no",
                "--hwdec=auto",
                "--vo=gpu",
                "--ao=null",
                "--cache=no",                         # 禁用缓存以实现最低延迟直播播放
                "--demuxer-max-bytes=10M",             # 降低缓冲区大小（默认50M）
                "--demuxer-readahead-secs=0.5",        # 降低预读秒数
                "--stream-buffer-size=32KiB",          # 降低流输入缓存区大小加快出图
                f"--network-timeout={timeout}",         # 自定义网络连接超时
                self.stream_url,
            ]

            # Windows: 不显示控制台窗口
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            self._mpv_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
            )

            self.is_playing = True
            self._btn_play.setText("⏸ 停止")
            self._placeholder.setVisible(False)

            # 启动健康监测
            self._health_timer.start(3000)

            logger.info("开始播放: %s (%s)", self.camera_name, self.stream_url[:60])

        except Exception as e:
            logger.error("播放失败: %s", e)
            self._loading_overlay.set_error(f"播放失败: {e}")

    def stop(self):
        """停止播放（非阻塞：后台清理进程）"""
        self._health_timer.stop()
        self._loading_overlay.hide_loading()

        if self._mpv_proc:
            proc = self._mpv_proc
            self._mpv_proc = None
            try:
                proc.terminate()
            except Exception:
                pass
            # 后台线程等待进程退出，不阻塞UI
            threading.Thread(target=self._reap_process, args=(proc,), daemon=True).start()

        self.is_playing = False
        self._btn_play.setText("▶ 播放")

        if self.is_recording:
            self.stop_recording()

    @staticmethod
    def _reap_process(proc):
        """后台清理已终止的 mpv 进程"""
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        except Exception:
            pass

    def _check_health(self):
        """定时检查 mpv 进程健康状态"""
        if not self.is_playing or not self._mpv_proc:
            return

        elapsed = time.time() - self._play_start_time

        # 检查进程是否已退出（崩溃/断流）
        ret = self._mpv_proc.poll()
        if ret is not None:
            logger.warning("mpv 进程退出: %s (code=%s)", self.camera_name, ret)
            self._mpv_proc = None
            self.is_playing = False
            self._btn_play.setText("▶ 播放")

            if self._retry_count < self._max_retries:
                self._retry_count += 1
                logger.info("自动重连 %d/%d: %s", self._retry_count, self._max_retries, self.camera_name)
                self._loading_overlay.show_loading(f"重新连接中 ({self._retry_count}/{self._max_retries})...")
                QTimer.singleShot(1000, lambda: self.play())
            else:
                self._health_timer.stop()
                self._loading_overlay.set_error("连接中断，点击重试")
            return

        # 超时检测：15秒仍未出画面
        if not self._loading_hidden and elapsed > 15:
            self._loading_overlay.show_loading("连接较慢，请稍候...")

        # 成功检测：进程存活超过 4 秒，认为已出画面
        if not self._loading_hidden and elapsed > 4:
            self._loading_hidden = True
            self._loading_overlay.hide_loading()
            self._retry_count = 0  # 重置重连计数

    def _on_retry(self):
        """用户点击重试"""
        self._retry_count = 0
        self.play()

    def toggle_play(self):
        """切换播放/停止"""
        if self.is_playing:
            self.stop()
        else:
            self.play()

    def _generate_recording_path(self, save_dir: str) -> str:
        """生成录像文件路径"""
        import random, string
        rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"REC_{timestamp}_{rand}.ts"
        return str(Path(save_dir) / filename)

    def _start_ffmpeg_recording(self) -> bool:
        """启动 ffmpeg 录制进程"""
        try:
            import imageio_ffmpeg
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            self._recorder_proc = subprocess.Popen([
                ffmpeg_path, '-y',
                '-i', self.stream_url,
                '-c', 'copy',
                self._recording_path,
            ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startupinfo)
            return True
        except Exception as e:
            logger.error("启动 ffmpeg 失败: %s", e)
            return False

    def start_recording(self, save_dir: str = None):
        """开始录像（通过 ffmpeg 录制，根据设置自动分段）"""
        if not self.stream_url:
            logger.warning("无视频流，无法录像")
            return

        if self.is_recording:
            return

        from config import load_config
        cfg = load_config()

        if save_dir is None:
            save_dir = cfg.get("recording_path")
        if not save_dir:
            from config import RECORDINGS_DIR
            save_dir = str(RECORDINGS_DIR)
        
        self._save_dir = save_dir
        self._recording_path = self._generate_recording_path(save_dir)

        if not self._start_ffmpeg_recording():
            return

        self.is_recording = True
        self._rec_indicator.setVisible(True)
        self._blink_timer.start(500)
        self._btn_record.setText("⏹ 停止")
        self._btn_record.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,0,0,80);
                color: white;
                border: 1px solid #ff0000;
                border-radius: 3px;
                font-size: 11px;
                padding: 2px 4px;
            }
        """)
        self.recording_started.emit(self.camera_name)
        
        # 动态获取分段时长，默认5分钟
        segment_minutes = cfg.get("recording_segment_minutes", 5)
        self._segment_timer.start(segment_minutes * 60 * 1000)
        logger.info("开始录像: %s, 分段时长: %d 分钟", self._recording_path, segment_minutes)

    def _stop_ffmpeg(self):
        """停止当前 ffmpeg 进程"""
        if hasattr(self, '_recorder_proc') and self._recorder_proc:
            try:
                self._recorder_proc.communicate(input=b"q", timeout=5)
            except subprocess.TimeoutExpired:
                self._recorder_proc.kill()
                self._recorder_proc.wait(timeout=3)
            except Exception:
                pass
            self._recorder_proc = None

    def _rotate_recording(self):
        """自动分段：保存当前文件，开始新文件"""
        if not self.is_recording:
            return

        saved_path = self._recording_path
        self._stop_ffmpeg()
        self.recording_stopped.emit(self.camera_name, saved_path)
        logger.info("自动分段保存: %s", saved_path)

        # 开始新分段
        self._recording_path = self._generate_recording_path(self._save_dir)
        if self._start_ffmpeg_recording():
            from config import load_config
            cfg = load_config()
            segment_minutes = cfg.get("recording_segment_minutes", 5)
            self._segment_timer.start(segment_minutes * 60 * 1000)
            logger.info("自动分段开始新录像: %s, 分段时长: %d 分钟", self._recording_path, segment_minutes)
        else:
            self.is_recording = False
            self._rec_indicator.setVisible(False)
            self._blink_timer.stop()
            self._btn_record.setText("⏺ 录像")
            self._btn_record.setStyleSheet(self._button_style())

    def stop_recording(self):
        """停止录像"""
        if not self.is_recording:
            return

        self._segment_timer.stop()
        self._stop_ffmpeg()

        self.is_recording = False
        self._rec_indicator.setVisible(False)
        self._blink_timer.stop()
        self._btn_record.setText("⏺ 录像")
        self._btn_record.setStyleSheet(self._button_style())

        saved_path = self._recording_path
        self.recording_stopped.emit(self.camera_name, saved_path)
        logger.info("停止录像: %s", saved_path)

    def toggle_record(self):
        """切换录像"""
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def take_snapshot(self):
        """截图（使用 ffmpeg 截取当前帧）"""
        if not self.is_playing or not self.stream_url:
            return

        from config import RECORDINGS_DIR
        import random, string
        rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"SNAP_{timestamp}_{rand}.jpg"
        snapshot_path = str(RECORDINGS_DIR / filename)

        try:
            import imageio_ffmpeg
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            proc = subprocess.run([
                ffmpeg_path, '-y',
                '-i', self.stream_url,
                '-frames:v', '1',
                '-q:v', '2',
                snapshot_path,
            ], capture_output=True, timeout=10, startupinfo=startupinfo)

            if os.path.isfile(snapshot_path):
                logger.info("截图保存: %s", snapshot_path)
            else:
                # 备用方案：Qt截图
                pixmap = self._video_frame.grab()
                pixmap.save(snapshot_path, "JPEG", 90)
                logger.info("截图保存(Qt): %s", snapshot_path)
        except Exception as e:
            logger.error("截图失败: %s", e)

    def toggle_fullscreen(self):
        """切换全屏"""
        self.double_clicked.emit(self.index)

    def _blink_rec(self):
        """录像指示灯闪烁"""
        self._blink_visible = not self._blink_visible
        if self._blink_visible:
            self._rec_indicator.setStyleSheet("color: #ff0000; font-size: 11px; font-weight: bold;")
        else:
            self._rec_indicator.setStyleSheet("color: #660000; font-size: 11px; font-weight: bold;")

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """双击切换全屏"""
        self.double_clicked.emit(self.index)

    def contextMenuEvent(self, event):
        """右键上下文菜单：支持一键重连刷新、截图和录像开关"""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e1e2e;
                color: white;
                border: 1px solid #444;
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 18px;
            }
            QMenu::item:selected {
                background-color: #0078d4;
            }
        """)

        action_reconnect = QAction("🔄 重新连接 (刷新画面)", self)
        action_reconnect.triggered.connect(self.play)
        menu.addAction(action_reconnect)

        menu.addSeparator()

        action_snapshot = QAction("📸 画面截图", self)
        action_snapshot.triggered.connect(self.take_snapshot)
        menu.addAction(action_snapshot)

        rec_text = "⏹ 停止录像" if self.is_recording else "🎥 开始录像"
        action_record = QAction(rec_text, self)
        action_record.triggered.connect(self.toggle_record)
        menu.addAction(action_record)

        menu.exec_(event.globalPos())

    def closeEvent(self, event):
        """关闭时清理资源"""
        self.stop()
        super().closeEvent(event)
