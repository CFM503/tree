"""mpv 视频播放控件

基于 mpv.exe 的 PyQt5 视频播放控件，通过 --wid 嵌入到 Qt 窗口。
支持 H.265/H.264、RTMP/HLS/ezopen 等所有格式。
"""
import ctypes
import json
import logging
import msvcrt
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QMenu, QAction
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QMouseEvent, QWheelEvent, QPalette, QColor

from loading_overlay import LoadingOverlay

logger = logging.getLogger(__name__)

# 查找 mpv.exe
_MPV_DIR = Path(__file__).parent / "mpg"
MPV_PATH = str(_MPV_DIR / "mpv.exe")
MPV_AVAILABLE = os.path.isfile(MPV_PATH)

if not MPV_AVAILABLE:
    logger.warning("mpv.exe 未找到: %s，视频播放不可用", MPV_PATH)

# 播放进度停滞多久判定为画面卡死（秒）
STALL_SECONDS = 12.0

# Windows 命名管道 API（显式声明参数/返回值类型，避免 64 位句柄被截断）
_k32 = ctypes.windll.kernel32
_k32.GetLastError.argtypes = ()
_k32.GetLastError.restype = ctypes.c_uint
_k32.CreateFileW.argtypes = (
    ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
    ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
)
_k32.CreateFileW.restype = ctypes.c_void_p
_k32.WaitNamedPipeW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
_k32.WaitNamedPipeW.restype = ctypes.c_int
_k32.PeekNamedPipe.argtypes = (
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
    ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
    ctypes.POINTER(ctypes.c_uint),
)
_k32.PeekNamedPipe.restype = ctypes.c_int


class VideoInteractionOverlay(QWidget):
    """用于捕获视频上方鼠标事件的透明遮罩控件"""

    double_clicked = pyqtSignal(QMouseEvent)
    mouse_pressed = pyqtSignal(QMouseEvent)
    mouse_moved = pyqtSignal(QMouseEvent)
    mouse_released = pyqtSignal(QMouseEvent)
    wheel_scrolled = pyqtSignal(QWheelEvent)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self.hide()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.double_clicked.emit(event)

    def mousePressEvent(self, event: QMouseEvent):
        self.mouse_pressed.emit(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        self.mouse_moved.emit(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.mouse_released.emit(event)

    def wheelEvent(self, event: QWheelEvent):
        self.wheel_scrolled.emit(event)

    def paintEvent(self, event):
        # 保持透明，只接收事件
        pass


class VideoWidget(QWidget):
    """单个摄像头视频播放控件"""

    double_clicked = pyqtSignal(int)
    recording_started = pyqtSignal(str)
    recording_stopped = pyqtSignal(str, str)
    stream_expired = pyqtSignal(int)  # 通知主窗口流可能已过期，需要重新获取 URL
    _async_done = pyqtSignal(object)  # 后台任务完成回调（信号会排队回主线程执行）

    def __init__(self, index: int = 0, parent=None):
        super().__init__(parent)
        # 跨线程回调统一走信号：后台线程没有事件循环，QTimer.singleShot 不会触发
        self._async_done.connect(self._invoke_callback)
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

        # 播放进度监控（JSON-IPC observe playback-time，用于检测“进程活着但画面卡死”）
        self._ipc_stop = None
        self._ipc_thread = None
        self._ipc_connected = False
        self._ipc_failed = False
        self._progress_seen = False
        self._last_time_pos = None
        self._last_progress_ts = 0.0

        # 截图防重入
        self._snapshot_busy = False
        self._recorder_proc = None

        # 画面放大和平移状态 (仅在单画面下激活)
        self.zoom_enabled = False
        self.zoom_level = 0.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.ipc_pipe = ""
        self._is_dragging = False
        self._drag_start = None
        self._start_pan_x = 0.0
        self._start_pan_y = 0.0

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

        # 交互遮罩（放置在最顶层用于捕获鼠标拖拽/滚轮缩放事件）
        self._interaction_overlay = VideoInteractionOverlay(self._video_frame)
        self._interaction_overlay.double_clicked.connect(self._on_overlay_double_click)
        self._interaction_overlay.mouse_pressed.connect(self._on_overlay_pressed)
        self._interaction_overlay.mouse_moved.connect(self._on_overlay_moved)
        self._interaction_overlay.mouse_released.connect(self._on_overlay_released)
        self._interaction_overlay.wheel_scrolled.connect(self._on_overlay_wheel)

        # 画面放大倍率提示标签
        self._zoom_label = QLabel(self._video_frame)
        self._zoom_label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 180);
                color: #0078d4;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: bold;
            }
        """)
        self._zoom_label.setVisible(False)

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

        # 生成用于 mpv JSON-IPC 控制的唯一命名管道
        self.ipc_pipe = rf"\\.\pipe\baby-monitor-mpv-{self.index}-{int(time.time() * 1000)}"

        # 显示加载动画
        self._loading_overlay.show_loading("正在连接...")
        self._loading_hidden = False
        self._play_start_time = time.monotonic()

        # 重置进度监控状态（每次播放都开一个独立的停止事件，避免旧线程串扰）
        self._ipc_stop = threading.Event()
        self._ipc_connected = False
        self._ipc_failed = False
        self._progress_seen = False
        self._last_time_pos = None
        self._last_progress_ts = time.monotonic()

        try:
            # 确保窗口已渲染
            self._video_frame.show()

            wid = int(self._video_frame.winId())
            logger.info("mpv WID: %s, URL: %s", hex(wid), self.stream_url[:60])

            from config import load_config
            cfg = load_config()
            timeout = cfg.get("network_timeout_seconds", 15)

            cmd = [
                MPV_PATH,
                f"--wid={wid}",
                f"--input-ipc-server={self.ipc_pipe}",  # 启用 JSON-IPC 服务
                "--terminal=no",
                "--really-quiet",
                "--keep-open=no",
                "--hwdec=auto",
                "--vo=gpu",
                "--ao=null",
                # 直播缓冲：HLS 按分段突发下发，缓冲过小会被网络抖动打断导致画面卡顿
                "--cache=yes",                          # 启用网络缓存（同时启用 cache-pause 补播）
                "--cache-secs=3",                       # 预缓冲约 3 秒，平滑网络抖动（延迟增加约 3 秒）
                "--cache-pause=yes",                    # 缓冲耗尽时暂停补播，而不是持续掉帧
                "--demuxer-max-bytes=50M",              # 解码前向缓冲上限
                "--demuxer-readahead-secs=3",           # 解码线程预读秒数
                "--stream-buffer-size=1M",              # 底层流缓冲（默认 128K，网络流适当加大）
                f"--network-timeout={timeout}",         # 自定义网络连接超时
                f"--demuxer-lavf-o=timeout={timeout * 1000000}", # FFmpeg底层读取超时 (微秒)
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
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
            )

            self.is_playing = True
            self._btn_play.setText("⏸ 停止")
            self._placeholder.setVisible(False)

            # 启动 IPC 进度监控线程（检测画面卡死）
            self._ipc_thread = threading.Thread(
                target=self._ipc_reader,
                args=(self.ipc_pipe, self._ipc_stop),
                daemon=True,
            )
            self._ipc_thread.start()

            # 如果缩放交互已启用，展示透明交互遮罩层
            if self.zoom_enabled:
                self._interaction_overlay.setGeometry(0, 0, self._video_frame.width(), self._video_frame.height())
                self._interaction_overlay.show()
                self._interaction_overlay.raise_()

            # 启动健康监测
            self._health_timer.start(3000)

            logger.info("开始播放: %s (%s)", self.camera_name, self.stream_url[:60])

        except Exception as e:
            logger.error("播放失败: %s", e)
            self._loading_overlay.set_error(f"播放失败: {e}")

    def stop(self):
        """停止播放（非阻塞：后台清理进程），录像也会一并停止"""
        self._stop_mpv()
        self._loading_overlay.hide_loading()

        # 隐藏并重置缩放交互界面
        if hasattr(self, '_interaction_overlay'):
            self._interaction_overlay.hide()
        if hasattr(self, '_zoom_label'):
            self._zoom_label.hide()
        self.zoom_level = 0.0
        self.pan_x = 0.0
        self.pan_y = 0.0

        if self.is_recording:
            self.stop_recording()

    def _stop_mpv(self):
        """仅停止 mpv 播放进程并复位播放状态（录像由 ffmpeg 独立维持，不受影响）"""
        self._health_timer.stop()
        if self._ipc_stop is not None:
            self._ipc_stop.set()
            self._ipc_stop = None
        self.ipc_pipe = ""
        self._is_dragging = False

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
        """定时检查 mpv 健康状态：进程是否退出、播放进度是否停滞（画面卡死）"""
        if not self.is_playing or not self._mpv_proc:
            return

        now = time.monotonic()
        elapsed = now - self._play_start_time

        # 检查进程是否已退出（崩溃/断流）
        ret = self._mpv_proc.poll()
        if ret is not None:
            self._on_player_lost("进程退出 code=%s" % ret)
            return

        # 画面卡死检测：进程还活着，但播放进度长时间不再推进
        stall = now - self._last_progress_ts
        if self._progress_seen and elapsed > STALL_SECONDS + 5 and stall > STALL_SECONDS:
            self._on_player_lost("画面停滞 %.0f 秒" % stall)
            return

        # 加载遮罩：优先以“播放进度已推进”作为出画面的证据
        if not self._loading_hidden:
            if self._progress_seen:
                self._loading_hidden = True
                self._loading_overlay.hide_loading()
            elif self._ipc_failed or self._ipc_thread_dead():
                # IPC 进度监控不可用，退回“进程存活 4 秒”判定
                if elapsed > 4:
                    self._loading_hidden = True
                    self._loading_overlay.hide_loading()
            elif elapsed > 15:
                self._loading_overlay.show_loading("连接较慢，请稍候...")

    def _ipc_thread_dead(self) -> bool:
        return self._ipc_thread is None or not self._ipc_thread.is_alive()

    def _on_player_lost(self, reason: str):
        """mpv 退出或画面卡死：复位播放状态并按需自动重连"""
        logger.warning("播放中断: %s (%s)", self.camera_name, reason)
        self._stop_mpv()
        self._loading_overlay.hide_loading()

        if self._retry_count < self._max_retries:
            self._retry_count += 1
            logger.info("自动重连 %d/%d: %s", self._retry_count, self._max_retries, self.camera_name)
            self._loading_overlay.show_loading(f"重新连接中 ({self._retry_count}/{self._max_retries})...")
            # 触发重新获取 URL 的信号，而不是直接用旧 URL 重连
            self.stream_expired.emit(self.index)
        else:
            self._loading_overlay.set_error("连接中断，点击重试")

    # ------------------------------------------------------------------
    # mpv JSON-IPC
    # ------------------------------------------------------------------
    def _ipc_reader(self, pipe_name: str, stop_event: threading.Event):
        """后台线程：连接 mpv 的 JSON-IPC 管道并观察 playback-time。

        用于发现“进程还活着但画面已经卡死”的情况（仅靠 poll() 检测不到）。
        """
        # 等待 mpv 创建命名管道。mpv 是启动后才异步创建的，此时调用
        # WaitNamedPipeW 会立刻返回 ERROR_FILE_NOT_FOUND，因此必须轮询
        deadline = time.monotonic() + 10.0
        ready = False
        last_err = 0
        while not stop_event.is_set():
            if _k32.WaitNamedPipeW(pipe_name, 250):
                ready = True
                break
            last_err = _k32.GetLastError()
            # 2=管道尚不存在 121=等待超时（有实例正在连接中），继续等
            if last_err not in (0, 2, 121):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        if not ready:
            if not stop_event.is_set():
                logger.warning("IPC 管道未就绪(err=%d): %s",
                               last_err, self.camera_name)
                self._ipc_failed = True
            return

        handle = _k32.CreateFileW(
            pipe_name, 0x80000000 | 0x40000000, 0, None, 3, 0, None)
        if not handle:
            logger.warning("打开 IPC 管道失败(err=%d): %s",
                           _k32.GetLastError(), self.camera_name)
            self._ipc_failed = True
            return

        fd = -1
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR)
            if fd < 0:
                logger.warning("IPC 句柄转 fd 失败: %s", self.camera_name)
                self._ipc_failed = True
                return
            self._ipc_connected = True

            # 观察播放进度，mpv 每次变化都会推送 property-change 事件
            os.write(fd, (json.dumps(
                {"command": ["observe_property", 1, "playback-time"]}) + "\n").encode("utf-8"))

            pending = b""
            while not stop_event.is_set():
                avail = ctypes.c_uint(0)
                if not _k32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None):
                    break  # 管道断开：mpv 已退出
                if avail.value:
                    chunk = os.read(fd, min(avail.value, 65536))
                    if not chunk:
                        break
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        self._handle_ipc_message(line, stop_event)
                else:
                    if self._mpv_proc is None or self._mpv_proc.poll() is not None:
                        break
                    time.sleep(0.2)
        except Exception as e:
            logger.debug("IPC 进度监控线程退出: %s", e)
        finally:
            if fd >= 0:
                try:
                    os.close(fd)  # 关闭 fd 同时关闭其持有的管道句柄
                except Exception:
                    pass
            self._ipc_connected = False

    def _handle_ipc_message(self, line: bytes, stop_event: threading.Event):
        """解析 mpv 推送的 property-change 事件，记录播放进度的最后推进时间"""
        if stop_event.is_set():
            return
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            return
        if not isinstance(msg, dict) or msg.get("event") != "property-change":
            return
        if msg.get("id") != 1:
            return
        pos = msg.get("data")
        if not isinstance(pos, (int, float)):
            return

        if self._last_time_pos is None:
            # 首个事件只是基线，之后的数值变化才算真正“画面在推进”
            self._last_time_pos = float(pos)
            return
        if abs(pos - self._last_time_pos) <= 1e-6:
            return

        self._last_time_pos = float(pos)
        self._last_progress_ts = time.monotonic()
        self._progress_seen = True
        self._retry_count = 0  # 画面正常推进，清零重连计数

    def reconnect_with_refresh(self):
        """重新连接并刷新流地址与权限 (带 Token)"""
        self.stop()
        self._retry_count = 0
        self._loading_overlay.show_loading("重新获取流地址...")
        self.stream_expired.emit(self.index)

    def _on_retry(self):
        """用户点击重试"""
        self.reconnect_with_refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_interaction_overlay'):
            self._interaction_overlay.setGeometry(0, 0, self._video_frame.width(), self._video_frame.height())
        if hasattr(self, '_zoom_label') and self._zoom_label.isVisible():
            self._position_zoom_label()

    def _position_zoom_label(self):
        margin = 10
        x = self._video_frame.width() - self._zoom_label.width() - margin
        y = margin
        self._zoom_label.move(max(margin, x), y)

    @staticmethod
    def _send_commands_to_pipe(pipe_name: str, cmds: list) -> bool:
        """向指定的 mpv JSON-IPC 管道写入若干条指令 (无需 pywin32)"""
        if not pipe_name:
            return False

        handle = _k32.CreateFileW(pipe_name, 0x40000000, 0, None, 3, 0, None)
        if not handle:
            return False

        try:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
            with os.fdopen(fd, 'wb', buffering=0) as f:
                for cmd in cmds:
                    payload = (json.dumps(cmd) + "\n").encode('utf-8')
                    f.write(payload)
            return True
        except Exception as e:
            logger.error("发送 IPC 命令到 mpv 失败: %s", e)
            return False

    def _send_mpv_commands(self, cmds: list) -> bool:
        """通过 Windows 命名管道给 mpv.exe 发送 JSON IPC 指令"""
        if not self.is_playing or not self.ipc_pipe:
            return False
        return self._send_commands_to_pipe(self.ipc_pipe, cmds)

    def _apply_zoom_and_pan(self):
        """应用缩放和平移参数"""
        cmds = [
            {"command": ["set_property", "video-zoom", self.zoom_level]},
            {"command": ["set_property", "video-pan-x", self.pan_x]},
            {"command": ["set_property", "video-pan-y", self.pan_y]}
        ]
        self._send_mpv_commands(cmds)

    def set_zoom_enabled(self, enabled: bool):
        """设置该视频控件是否允许使用缩放/平移功能"""
        self.zoom_enabled = enabled
        if enabled:
            if self.is_playing:
                self._interaction_overlay.setGeometry(0, 0, self._video_frame.width(), self._video_frame.height())
                self._interaction_overlay.show()
                self._interaction_overlay.raise_()
                self._update_cursor()
        else:
            self._interaction_overlay.hide()
            self._zoom_label.hide()
            self.reset_zoom()

    def reset_zoom(self):
        """重置缩放和平移"""
        self.zoom_level = 0.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        if self.is_playing and self.zoom_enabled:
            self._apply_zoom_and_pan()
            self._show_zoom_tooltip()
            self._update_cursor()

    def _update_cursor(self):
        if self.zoom_level > 0.0:
            self._interaction_overlay.setCursor(Qt.OpenHandCursor)
        else:
            self._interaction_overlay.setCursor(Qt.ArrowCursor)

    def _show_zoom_tooltip(self):
        if self.zoom_level > 0.0:
            percentage = int((1.0 + self.zoom_level) * 100)
            self._zoom_label.setText(f"🔍 放大: {percentage}%")
            self._zoom_label.adjustSize()
            self._position_zoom_label()
            self._zoom_label.show()
            self._zoom_label.raise_()

            if not hasattr(self, '_zoom_timer'):
                self._zoom_timer = QTimer(self)
                self._zoom_timer.setSingleShot(True)
                self._zoom_timer.timeout.connect(self._zoom_label.hide)
            self._zoom_timer.start(1500)
        else:
            self._zoom_label.hide()

    def _on_overlay_wheel(self, event):
        if not self.is_playing or not self.zoom_enabled:
            return

        delta = event.angleDelta().y() / 120.0
        old_zoom = self.zoom_level
        self.zoom_level = max(0.0, min(5.0, self.zoom_level + delta * 0.2))

        if self.zoom_level != old_zoom:
            if self.zoom_level == 0.0:
                self.pan_x = 0.0
                self.pan_y = 0.0

            self._apply_zoom_and_pan()
            self._show_zoom_tooltip()
            self._update_cursor()

    def _on_overlay_pressed(self, event):
        if not self.is_playing or not self.zoom_enabled or self.zoom_level == 0.0:
            return

        if event.button() == Qt.LeftButton:
            self._is_dragging = True
            self._drag_start = event.pos()
            self._start_pan_x = self.pan_x
            self._start_pan_y = self.pan_y
            self._interaction_overlay.setCursor(Qt.ClosedHandCursor)

    def _on_overlay_moved(self, event):
        if not self.is_playing or not self.zoom_enabled or not getattr(self, '_is_dragging', False):
            return

        dx = event.pos().x() - self._drag_start.x()
        dy = event.pos().y() - self._drag_start.y()

        width = self._video_frame.width()
        height = self._video_frame.height()

        if width > 0 and height > 0:
            # 移动比例需要随着放大倍数进行敏感度折算
            scale = 1.0 + self.zoom_level
            self.pan_x = self._start_pan_x + (dx / width) / scale
            self.pan_y = self._start_pan_y + (dy / height) / scale

            # 限制范围
            self.pan_x = max(-2.0, min(2.0, self.pan_x))
            self.pan_y = max(-2.0, min(2.0, self.pan_y))

            self._apply_zoom_and_pan()

    def _on_overlay_released(self, event):
        self._is_dragging = False
        if self.is_playing and self.zoom_enabled:
            self._update_cursor()

    def _on_overlay_double_click(self, event):
        if not self.is_playing or not self.zoom_enabled:
            return

        if self.zoom_level > 0.0:
            self.reset_zoom()
        else:
            self.double_clicked.emit(self.index)

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

    def _invoke_callback(self, callback):
        """在主线程执行后台任务传回的回调"""
        if callable(callback):
            callback()

    def _post_to_main(self, callback):
        """从任意线程把回调投递到主线程执行（等价于跨线程的 QTimer.singleShot）"""
        self._async_done.emit(callback)

    def _stop_ffmpeg(self, on_finished=None):
        """停止当前 ffmpeg 录制进程。

        在后台线程里等待退出（最多 5 秒），避免 ffmpeg 收尾时阻塞 UI 线程；
        文件真正关闭后回到主线程执行 on_finished 回调。
        """
        proc = self._recorder_proc
        self._recorder_proc = None
        if not proc:
            if on_finished:
                on_finished()
            return

        def _worker():
            try:
                proc.communicate(input=b"q", timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception:
                    pass
            except Exception:
                pass
            if on_finished:
                self._post_to_main(on_finished)

        threading.Thread(target=_worker, daemon=True).start()

    def _rotate_recording(self):
        """自动分段：后台保存当前文件，文件关闭后再开始新文件"""
        if not self.is_recording:
            return

        saved_path = self._recording_path
        self._stop_ffmpeg(on_finished=lambda: self._start_next_segment(saved_path))

    def _start_next_segment(self, saved_path: str):
        """上一段录像文件已关闭（主线程）：刷新录像列表并开始新分段"""
        logger.info("自动分段保存: %s", saved_path)
        self.recording_stopped.emit(self.camera_name, saved_path)

        if not self.is_recording:
            return

        self._recording_path = self._generate_recording_path(self._save_dir)
        if self._start_ffmpeg_recording():
            from config import load_config
            cfg = load_config()
            segment_minutes = cfg.get("recording_segment_minutes", 5)
            self._segment_timer.start(segment_minutes * 60 * 1000)
            logger.info("自动分段开始新录像: %s, 分段时长: %d 分钟", self._recording_path, segment_minutes)
        else:
            self._rec_indicator.setVisible(False)
            self._blink_timer.stop()
            self.is_recording = False
            self._btn_record.setText("⏺ 录像")
            self._btn_record.setStyleSheet(self._button_style())

    def stop_recording(self):
        """停止录像（ffmpeg 在后台收尾，不阻塞 UI）"""
        if not self.is_recording:
            return

        self.is_recording = False
        self._segment_timer.stop()
        self._rec_indicator.setVisible(False)
        self._blink_timer.stop()
        self._btn_record.setText("⏺ 录像")
        self._btn_record.setStyleSheet(self._button_style())

        saved_path = self._recording_path
        self._stop_ffmpeg(
            on_finished=lambda: self.recording_stopped.emit(self.camera_name, saved_path))
        logger.info("停止录像: %s", saved_path)

    def toggle_record(self):
        """切换录像"""
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------
    def take_snapshot(self):
        """截图：优先截取 mpv 正在播放的画面（不额外拉流），全程在后台执行"""
        if self._snapshot_busy or not self.is_playing or not self.stream_url:
            return

        from config import load_config, RECORDINGS_DIR
        cfg = load_config()
        save_dir = Path(cfg.get("recording_path", str(RECORDINGS_DIR)))
        import random, string
        rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_path = str(save_dir / f"SNAP_{timestamp}_{rand}.jpg")

        self._snapshot_busy = True
        self._btn_snapshot.setEnabled(False)
        ipc_pipe = self.ipc_pipe
        stream_url = self.stream_url
        threading.Thread(
            target=self._do_snapshot,
            args=(snapshot_path, ipc_pipe, stream_url),
            daemon=True,
        ).start()

    def _do_snapshot(self, snapshot_path: str, ipc_pipe: str, stream_url: str):
        saved = False

        # 1) 首选：让 mpv 把当前画面存盘（复用已有连接，不重复拉流）
        if ipc_pipe:
            try:
                if self._send_commands_to_pipe(ipc_pipe, [
                    {"command": ["screenshot-to-file", snapshot_path, "video"]}
                ]):
                    saved = self._wait_for_file(snapshot_path, 5.0)
            except Exception as e:
                logger.warning("mpv 截图失败，改用 ffmpeg: %s", e)

        # 2) 兜底：ffmpeg 重新拉流截取一帧
        if not saved:
            saved = self._ffmpeg_snapshot(snapshot_path, stream_url)

        # 3) 最终兜底：Qt 抓取控件画面（必须回主线程）
        self._post_to_main(lambda: self._on_snapshot_done(snapshot_path, saved))

    @staticmethod
    def _wait_for_file(path: str, timeout: float) -> bool:
        """等待 mpv 落盘截图文件"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if os.path.isfile(path) and os.path.getsize(path) > 0:
                    return True
            except OSError:
                pass
            time.sleep(0.2)
        return False

    @staticmethod
    def _ffmpeg_snapshot(snapshot_path: str, stream_url: str) -> bool:
        """用 ffmpeg 拉流截取一帧（在后台线程中调用）"""
        try:
            import imageio_ffmpeg
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            subprocess.run([
                ffmpeg_path, '-y',
                '-i', stream_url,
                '-frames:v', '1',
                '-q:v', '2',
                snapshot_path,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, startupinfo=startupinfo)
            return os.path.isfile(snapshot_path) and os.path.getsize(snapshot_path) > 0
        except Exception as e:
            logger.error("ffmpeg 截图失败: %s", e)
            return False

    def _on_snapshot_done(self, snapshot_path: str, saved: bool):
        """截图结束（主线程）：恢复按钮并兜底"""
        self._snapshot_busy = False
        self._btn_snapshot.setEnabled(True)

        if saved:
            logger.info("截图保存: %s", snapshot_path)
            return

        try:
            pixmap = self._video_frame.grab()
            if pixmap.save(snapshot_path, "JPEG", 90):
                logger.info("截图保存(Qt): %s", snapshot_path)
                return
        except Exception as e:
            logger.error("Qt 截图失败: %s", e)
        logger.error("截图失败: %s", snapshot_path)

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

        if self.zoom_enabled and self.zoom_level > 0.0:
            action_reset_zoom = QAction("🔍 重置画面缩放", self)
            action_reset_zoom.triggered.connect(self.reset_zoom)
            menu.addAction(action_reset_zoom)
            menu.addSeparator()

        action_reconnect = QAction("🔄 重新连接 (刷新画面)", self)
        action_reconnect.triggered.connect(self.reconnect_with_refresh)
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
