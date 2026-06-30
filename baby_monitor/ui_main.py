"""主界面

掌通家园 PC 客户端主窗口。
流程：宝宝列表 → 点击宝宝 → 加载摄像头 → 双击播放
"""
import sys
import logging
from pathlib import Path
from datetime import datetime

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QStackedWidget,
    QStatusBar, QFrame, QMessageBox, QSizePolicy, QApplication,
    QDialog, QFileDialog, QSpinBox, QLineEdit
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread, QSize
from PyQt5.QtGui import QFont

from video_widget import VideoWidget

logger = logging.getLogger(__name__)


class ChildListWidget(QListWidget):
    """宝宝列表控件"""
    child_clicked = pyqtSignal(int)  # 孩子索引

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QListWidget {
                background-color: #1e1e2e;
                color: white;
                border: none;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 10px 8px;
                border-bottom: 1px solid #333;
            }
            QListWidget::item:selected {
                background-color: #0078d4;
            }
            QListWidget::item:hover {
                background-color: #333;
            }
        """)
        self.currentRowChanged.connect(self._on_row_changed)

    def load_children(self, children: list):
        """加载宝宝列表"""
        self.clear()
        for i, child in enumerate(children):
            name = child.get("name", f"宝宝{i+1}")
            school = child.get("school_name", "")
            item = QListWidgetItem()
            item.setText(f"  {name}\n  {school}")
            item.setData(Qt.UserRole, i)
            item.setSizeHint(QSize(200, 44))
            self.addItem(item)

    def _on_row_changed(self, row):
        if row >= 0:
            self.child_clicked.emit(row)


class CameraListWidget(QListWidget):
    """摄像头列表控件"""
    camera_selected = pyqtSignal(int)
    play_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QListWidget {
                background-color: #1e1e2e;
                color: white;
                border: none;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #333;
            }
            QListWidget::item:selected {
                background-color: #0078d4;
            }
            QListWidget::item:hover {
                background-color: #333;
            }
        """)
        self.itemDoubleClicked.connect(self._on_double_click)

    def update_cameras(self, cameras: list):
        """更新摄像头列表（只显示有权限的）"""
        self.clear()
        for i, cam in enumerate(cameras):
            name = cam.get("ChannelName", f"摄像头 {i+1}")
            online = cam.get("Status", 0) == 1
            icon = "🟢" if online else "🔴"
            item = QListWidgetItem()
            item.setText(f"{icon} {name}")
            item.setData(Qt.UserRole, i)
            self.addItem(item)

    def _on_double_click(self, item):
        idx = item.data(Qt.UserRole)
        if idx is not None:
            self.play_requested.emit(idx)


class RecordingListWidget(QListWidget):
    """录像列表控件"""
    play_recording = pyqtSignal(str)  # 文件路径

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QListWidget {
                background-color: #1e1e2e;
                color: white;
                border: none;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 6px 8px;
                border-bottom: 1px solid #333;
            }
            QListWidget::item:selected {
                background-color: #0078d4;
            }
            QListWidget::item:hover {
                background-color: #333;
            }
        """)
        self.itemDoubleClicked.connect(self._on_double_click)

    def refresh(self, recordings_dir: str = None):
        """刷新录像列表"""
        from config import load_config, RECORDINGS_DIR
        if recordings_dir is None:
            cfg = load_config()
            recordings_dir = cfg.get("recording_path", str(RECORDINGS_DIR))

        self.clear()
        # 支持同时扫描旧的 .mp4 和新的 .ts 录像文件
        files = []
        if Path(recordings_dir).exists():
            files = list(Path(recordings_dir).glob("REC_*.mp4")) + list(Path(recordings_dir).glob("REC_*.ts"))
        recordings = sorted(
            files,
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for f in recordings:
            size_mb = f.stat().st_size / 1024 / 1024
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            time_str = mtime.strftime("%m-%d %H:%M:%S")
            item = QListWidgetItem()
            item.setText(f"  {time_str}  ({size_mb:.1f}MB)")
            item.setData(Qt.UserRole, str(f))
            item.setToolTip(f.name)
            self.addItem(item)

    def _on_double_click(self, item):
        path = item.data(Qt.UserRole)
        if path and Path(path).exists():
            self.play_recording.emit(path)


class LoadCamerasThread(QThread):
    """后台加载摄像头线程"""
    finished = pyqtSignal(list)  # 摄像头列表
    error = pyqtSignal(str)

    def __init__(self, client, child):
        super().__init__()
        self.client = client
        self.child = child

    def run(self):
        try:
            child = self.child
            self.client.select_child(
                child["child_id"],
                child.get("class_id", 0),
                child.get("school_id", 0)
            )
            cameras = self.client.get_camera_list()
            if not cameras:
                cameras = []

            from concurrent.futures import ThreadPoolExecutor, as_completed

            # 1. 并发获取每个摄像头的权限
            def fetch_authority(cam):
                cam["authority"] = 0
                try:
                    auth = self.client.get_camera_authority(
                        camera_sn=cam.get("ZhsCarameSn", ""))
                    cam["authority"] = auth.get("data", {}).get("authority", 0)
                except Exception as e:
                    logger.warning("并发获取权限失败 [%s]: %s", cam.get("ChannelName", ""), e)
                return cam

            if cameras:
                max_workers = min(len(cameras), 8)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(fetch_authority, cam) for cam in cameras]
                    for future in as_completed(futures):
                        pass

            # 2. 并发获取有权限摄像头的流URL
            authorized = [c for c in cameras if c.get("authority") == 1]
            if authorized:
                def fetch_stream_url(cam):
                    if cam.get("Status") != 1:
                        return cam
                    device_code = cam.get("DeviceCode", "")
                    channel_no = cam.get("ChannelNo", 1)
                    name = cam.get("ChannelName", "")
                    try:
                        url = self.client.get_stream_url(device_code, channel_no, protocol=2, quality=1)
                        if url:
                            cam["stream_url"] = url
                            logger.info("并发获取流地址: %s -> %s...", name, url[:60])
                    except Exception as e:
                        logger.error("并发获取流地址失败 [%s]: %s", name, e)
                    return cam

                max_workers_url = min(len(authorized), 8)
                with ThreadPoolExecutor(max_workers=max_workers_url) as executor:
                    futures = [executor.submit(fetch_stream_url, cam) for cam in authorized]
                    for future in as_completed(futures):
                        pass

            self.finished.emit(cameras)
        except Exception as e:
            self.error.emit(str(e))


class CollapsibleSection(QWidget):
    """可折叠面板：折叠时只显示标题按钮，展开时内容自适应填充"""

    toggled = pyqtSignal(bool)  # 展开/折叠信号

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._is_expanded = True
        self._title = title

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题按钮
        self._toggle_btn = QPushButton(f"  ▼  {title}")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(True)
        self._toggle_btn.setFixedHeight(32)
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e1e2e;
                color: #aaa;
                font-size: 13px;
                font-weight: bold;
                padding: 0 10px;
                border: none;
                border-bottom: 1px solid #2a2a40;
                text-align: left;
            }
            QPushButton:checked {
                color: white;
                background-color: #252540;
            }
            QPushButton:hover {
                background-color: #2a2a4a;
                color: #ddd;
            }
        """)
        self._toggle_btn.clicked.connect(self._on_toggle)
        layout.addWidget(self._toggle_btn)

        # 内容容器
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        layout.addWidget(self._content, 1)  # stretch=1 让内容区域填充

        # 默认展开状态的尺寸策略
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_content_widget(self, widget: QWidget):
        """设置折叠面板的内容控件"""
        self._content_layout.addWidget(widget)

    def _on_toggle(self, checked: bool):
        self._is_expanded = checked
        self._content.setVisible(checked)
        if checked:
            self._toggle_btn.setText(f"  ▼  {self._title}")
            # 恢复弹性尺寸：移除 setFixedHeight 设置的约束
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        else:
            self._toggle_btn.setText(f"  ▶  {self._title}")
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setFixedHeight(self._toggle_btn.height())
        self.toggled.emit(checked)

    def expand(self):
        self._toggle_btn.setChecked(True)
        self._on_toggle(True)

    def collapse(self):
        self._toggle_btn.setChecked(False)
        self._on_toggle(False)

    def is_expanded(self) -> bool:
        return self._is_expanded


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self, client, children: list, cameras: list, config: dict, parent=None):
        super().__init__(parent)
        self.client = client
        self.children = children
        self.config = config
        self.cameras = cameras or []
        self._current_child = children[0] if children else None

        self._grid_mode = True
        self._current_single_index = 0
        self._video_widgets: list = []
        self._max_cameras = 6
        self._load_thread = None

        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle("猴子看护")
        self.setMinimumSize(1024, 700)
        self.resize(1280, 800)

        if self.config.get("window_geometry"):
            try:
                from PyQt5.QtCore import QByteArray
                self.restoreGeometry(QByteArray.fromBase64(
                    self.config["window_geometry"].encode()))
            except Exception:
                pass

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 左侧面板
        left_panel = QWidget()
        left_panel.setFixedWidth(240)
        left_panel.setStyleSheet("""
            QWidget#leftPanel {
                background-color: #16162a;
                border-right: 1px solid #2a2a40;
            }
        """)
        left_panel.setObjectName("leftPanel")
        self._left_layout = QVBoxLayout(left_panel)
        self._left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_layout.setSpacing(0)

        # 宝宝列表（折叠面板）- 展开时占主要空间
        self.child_list = ChildListWidget()
        self.child_list.load_children(self.children)
        self.child_list.child_clicked.connect(self._on_child_clicked)
        self._child_section = CollapsibleSection("👶 宝宝列表")
        self._child_section.set_content_widget(self.child_list)
        self._left_layout.addWidget(self._child_section, 0)

        # 摄像头列表（折叠面板）
        self.camera_list = CameraListWidget()
        self.camera_list.play_requested.connect(self._on_camera_play)
        self._camera_section = CollapsibleSection("📹 摄像头列表")
        self._camera_section.set_content_widget(self.camera_list)
        self._camera_section.collapse()  # 默认折叠
        self._left_layout.addWidget(self._camera_section, 0)

        # 录像列表（折叠面板）
        self.recording_list = RecordingListWidget()
        self.recording_list.play_recording.connect(self._play_recording)
        self._recording_section = CollapsibleSection("🎬 录像列表")
        self._recording_section.set_content_widget(self.recording_list)
        self._recording_section.collapse()  # 默认折叠
        self._left_layout.addWidget(self._recording_section, 0)

        # 底部弹簧：当所有面板折叠时把它们推到顶部
        self._left_layout.addStretch(0)

        # 所有面板创建完成后再连接信号（避免 collapse 触发时其他面板尚未创建）
        self._child_section.toggled.connect(lambda _: self._update_left_panel_stretch())
        self._camera_section.toggled.connect(lambda _: self._update_left_panel_stretch())
        self._recording_section.toggled.connect(lambda _: self._update_left_panel_stretch())

        # 初始化 stretch 分配
        self._update_left_panel_stretch()

        # 初始加载录像列表
        self.recording_list.refresh()

        main_layout.addWidget(left_panel)

        # 右侧视频区域
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # 工具栏
        toolbar = QWidget()
        toolbar.setFixedHeight(44)
        toolbar.setStyleSheet("background-color: #1a1a2e; border-bottom: 1px solid #333;")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 4, 8, 4)

        self.btn_grid_mode = QPushButton("6画面")
        self.btn_grid_mode.setFixedWidth(70)
        self.btn_grid_mode.setStyleSheet(self._toolbar_btn_style(True))
        self.btn_grid_mode.clicked.connect(self._switch_to_grid)
        tb_layout.addWidget(self.btn_grid_mode)

        self.btn_single_mode = QPushButton("单画面")
        self.btn_single_mode.setFixedWidth(70)
        self.btn_single_mode.setStyleSheet(self._toolbar_btn_style(False))
        self.btn_single_mode.clicked.connect(lambda: self._switch_to_single(0))
        tb_layout.addWidget(self.btn_single_mode)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #444; margin: 4px 6px;")
        sep.setFixedHeight(24)
        tb_layout.addWidget(sep)

        btn_open_dir = QPushButton("📁 录像目录")
        btn_open_dir.setFixedWidth(80)
        btn_open_dir.setStyleSheet(self._toolbar_btn_style())
        btn_open_dir.clicked.connect(self._open_recordings_dir)
        tb_layout.addWidget(btn_open_dir)

        btn_settings = QPushButton("⚙️ 设置")
        btn_settings.setFixedWidth(68)
        btn_settings.setStyleSheet(self._toolbar_btn_style())
        btn_settings.clicked.connect(self._open_settings)
        tb_layout.addWidget(btn_settings)

        tb_layout.addStretch()

        # 当前宝宝标签
        self._child_label = QLabel("请选择宝宝")
        self._child_label.setStyleSheet("color: #0078d4; font-size: 13px; font-weight: bold;")
        tb_layout.addWidget(self._child_label)

        right_layout.addWidget(toolbar)

        # 视频网格区域
        self._video_stack = QStackedWidget()

        self._grid_widget = QWidget()
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setSpacing(4)
        self._grid_layout.setContentsMargins(4, 4, 4, 4)
        self._video_stack.addWidget(self._grid_widget)

        self._single_widget = QWidget()
        self._single_layout = QVBoxLayout(self._single_widget)
        self._single_layout.setContentsMargins(0, 0, 0, 0)
        self._video_stack.addWidget(self._single_widget)

        # 占位提示
        self._placeholder = QLabel("← 请在左侧选择宝宝\n\n选择后将自动加载摄像头列表\n双击摄像头即可播放")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet("color: #666; font-size: 16px;")
        self._video_stack.addWidget(self._placeholder)
        self._video_stack.setCurrentWidget(self._placeholder)

        right_layout.addWidget(self._video_stack)

        main_layout.addWidget(right_panel)

        # 状态栏
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("请选择宝宝")

        # 软件版本号
        self._status_version = QLabel("v2.7.3-stable")
        self._status_version.setStyleSheet("color: #777; margin-right: 15px; font-weight: bold;")
        self._status_bar.addPermanentWidget(self._status_version)

        self._status_cam_count = QLabel("")
        self._status_cam_count.setStyleSheet("color: #aaa; margin-right: 15px;")
        self._status_bar.addPermanentWidget(self._status_cam_count)

        self._status_disk = QLabel("")

        self._status_disk.setStyleSheet("color: #aaa; margin-right: 10px;")
        self._status_bar.addPermanentWidget(self._status_disk)

        # 权限保活心跳定时器（每 3 分钟）
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.timeout.connect(self._heartbeat_authority)
        self._heartbeat_timer.start(3 * 60 * 1000)

        # 异步预加载第一个宝宝的摄像头
        if self.children:
            QTimer.singleShot(100, self._preload_first_child)

    def _update_left_panel_stretch(self):
        """动态调整左侧面板 stretch：展开的面板填充空间，折叠的紧贴"""
        sections = [self._child_section, self._camera_section, self._recording_section]
        any_expanded = any(s.is_expanded() for s in sections)

        for i, s in enumerate(sections):
            self._left_layout.setStretch(i, 1 if s.is_expanded() else 0)

        # 底部弹簧：所有面板折叠时 stretch=1 把它们推到顶部，否则 stretch=0
        spacer_index = len(sections)  # addStretch 是第4个 item (index 3)
        self._left_layout.setStretch(spacer_index, 0 if any_expanded else 1)

    def _toolbar_btn_style(self, active=False) -> str:
        bg = "#0078d4" if active else "rgba(255,255,255,30)"
        return f"""
            QPushButton {{
                background-color: {bg};
                color: white;
                border: 1px solid rgba(255,255,255,50);
                border-radius: 3px;
                font-size: 11px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{ background-color: rgba(255,255,255,60); }}
        """

    def _preload_first_child(self):
        """异步预加载第一个宝宝的摄像头（避免启动阻塞）"""
        if not self.children:
            return
        child = self.children[0]
        name = child.get("name", "")
        self._current_child = child
        self._child_label.setText(f"当前: {name}")
        self._status_bar.showMessage(f"⏳ 正在加载 {name} 的摄像头...")

        # 选中列表项但不触发重复加载
        self.child_list.blockSignals(True)
        self.child_list.setCurrentRow(0)
        self.child_list.blockSignals(False)

        # 后台线程加载
        self._load_thread = LoadCamerasThread(self.client, child)
        self._load_thread.finished.connect(self._on_cameras_loaded)
        self._load_thread.error.connect(self._on_cameras_error)
        self._load_thread.start()

    def _show_preloaded_cameras(self):
        """显示预加载的摄像头（仅显示有权限的）"""
        if self._current_child:
            name = self._current_child.get("name", "")
            school = self._current_child.get("school_name", "")
            self._child_label.setText(f"当前: {name} @ {school}")
            # 用 blockSignals 避免触发 _on_child_clicked 的重复网络请求
            self.child_list.blockSignals(True)
            self.child_list.setCurrentRow(0)
            self.child_list.blockSignals(False)

        authorized = [c for c in self.cameras if c.get("authority") == 1]
        online = [c for c in authorized if c.get("Status") == 1]
        with_stream = [c for c in authorized if c.get("stream_url")]

        self._status_bar.showMessage(
            f"共 {len(self.cameras)} 个摄像头，{len(authorized)} 个有权限，{len(online)} 个在线，"
            f"{len(with_stream)} 个有流地址")

        if not self._video_widgets:
            self._init_video_widgets()

        # 只显示有权限的摄像头
        self.cameras = authorized
        for i, cam in enumerate(self.cameras[:self._max_cameras]):
            name = cam.get("ChannelName", f"摄像头 {i+1}")
            stream_url = cam.get("stream_url", "")
            is_online = cam.get("Status", 0) == 1
            self._video_widgets[i].set_camera(name, stream_url, is_online)

        self.camera_list.update_cameras(self.cameras)
        shown = min(len(self.cameras), self._max_cameras)
        self._status_cam_count.setText(f"摄像头: {shown}/{len(self.cameras)}")

        self._video_stack.setCurrentWidget(self._grid_widget)
        self._play_all()

    def _on_child_clicked(self, index: int):
        """点击宝宝 → 加载摄像头"""
        if index >= len(self.children):
            return

        child = self.children[index]
        name = child.get("name", "")
        school = child.get("school_name", "")
        self._current_child = child
        self._child_label.setText(f"当前: {name} @ {school}")
        self._status_bar.showMessage(f"正在加载 {name} 的摄像头...")

        # 禁用列表防止重复点击
        self.child_list.setEnabled(False)

        # 显示加载提示
        self._status_bar.showMessage(f"⏳ 正在加载 {name} 的摄像头列表...")
        for vw in self._video_widgets:
            if vw.is_playing:
                vw.stop()
            vw._name_label.setText("加载中...")
            vw._placeholder.setText("⏳ 正在加载摄像头...")
            vw._placeholder.setVisible(True)

        # 后台加载
        self._load_thread = LoadCamerasThread(self.client, child)
        self._load_thread.finished.connect(self._on_cameras_loaded)
        self._load_thread.error.connect(self._on_cameras_error)
        self._load_thread.start()

    def _on_cameras_loaded(self, cameras: list):
        """摄像头加载完成（仅显示有权限的）"""
        self.child_list.setEnabled(True)

        authorized = [c for c in cameras if c.get("authority") == 1]
        online = [c for c in authorized if c.get("Status") == 1]
        with_stream = [c for c in authorized if c.get("stream_url")]

        self._status_bar.showMessage(
            f"共 {len(cameras)} 个摄像头，{len(authorized)} 个有权限，{len(online)} 个在线，"
            f"{len(with_stream)} 个有流地址")

        # 只保留有权限的摄像头
        self.cameras = authorized

        # 初始化视频控件（如果还没创建）
        if not self._video_widgets:
            self._init_video_widgets()

        # 更新摄像头信息
        for i, cam in enumerate(self.cameras):
            if i >= self._max_cameras:
                break
            name = cam.get("ChannelName", f"摄像头 {i+1}")
            stream_url = cam.get("stream_url", "")
            is_online = cam.get("Status", 0) == 1
            self._video_widgets[i].set_camera(name, stream_url, is_online)
            if not is_online and self._video_widgets[i].is_playing:
                self._video_widgets[i].stop()

        # 清空多余的视频控件
        for i in range(len(self.cameras), self._max_cameras):
            if i < len(self._video_widgets):
                self._video_widgets[i].set_camera(f"摄像头 {i+1}", "", False)
                if self._video_widgets[i].is_playing:
                    self._video_widgets[i].stop()

        # 更新摄像头列表
        self.camera_list.update_cameras(self.cameras)
        shown = min(len(self.cameras), self._max_cameras)
        self._status_cam_count.setText(f"摄像头: {shown}/{len(self.cameras)}")

        # 切换到视频网格并自动播放
        self._video_stack.setCurrentWidget(self._grid_widget)
        self._play_all()

    def _on_cameras_error(self, error_msg: str):
        """摄像头加载失败"""
        self.child_list.setEnabled(True)
        self._status_bar.showMessage(f"加载失败: {error_msg}")
        # 不弹错误对话框，只在状态栏提示
        logger.warning("摄像头加载失败: %s", error_msg)

    def _init_video_widgets(self):
        """初始化视频控件"""
        for i in range(self._max_cameras):
            vw = VideoWidget(i)
            vw.double_clicked.connect(self._on_video_double_click)
            vw.recording_stopped.connect(self._on_recording_stopped)
            vw.stream_expired.connect(self._on_stream_expired)
            self._video_widgets.append(vw)
            row = i // 3
            col = i % 3
            self._grid_layout.addWidget(vw, row, col)

    def _play_all(self):
        """播放所有摄像头"""
        for i, cam in enumerate(self.cameras):
            if i >= self._max_cameras:
                break
            url = cam.get("stream_url", "")
            if url and cam.get("Status", 0) == 1 and cam.get("authority", 0) == 1:
                self._video_widgets[i].play(url)

    def _stop_all(self):
        """停止所有播放"""
        for vw in self._video_widgets:
            vw.stop()

    def _switch_to_grid(self):
        """切换到多画面模式"""
        if not self._grid_mode:
            single_vw = self._single_layout.itemAt(0)
            if single_vw:
                self._single_layout.removeItem(single_vw)

            for i, vw in enumerate(self._video_widgets):
                row = i // 3
                col = i % 3
                self._grid_layout.addWidget(vw, row, col)
                vw.show()
                vw.setParent(self._grid_widget)
                vw.set_zoom_enabled(False)  # 网格多画面下禁用缩放并重置状态

            self._grid_mode = True
            self._video_stack.setCurrentWidget(self._grid_widget)
            self.btn_grid_mode.setStyleSheet(self._toolbar_btn_style(True))
            self.btn_single_mode.setStyleSheet(self._toolbar_btn_style(False))

            # 重新恢复原来需要播放的在线摄像头（如果当前未在播放）
            for i, cam in enumerate(self.cameras):
                if i >= self._max_cameras:
                    break
                vw = self._video_widgets[i]
                if not vw.is_playing:
                    url = cam.get("stream_url", "")
                    if url and cam.get("Status", 0) == 1 and cam.get("authority", 0) == 1:
                        vw.play(url)

    def _switch_to_single(self, index: int = 0):
        """切换到单画面模式"""
        if self._grid_mode and index < len(self._video_widgets):
            self._current_single_index = index
            vw = self._video_widgets[index]
            self._grid_layout.removeWidget(vw)
            for w in self._video_widgets:
                if w != vw:
                    w.hide()
                    w.stop()  # 停止隐藏画面的播放进程以释放CPU/GPU/网络资源
            self._single_layout.addWidget(vw)
            vw.show()
            vw.set_zoom_enabled(True)  # 单画面下允许画面缩放与平移
            self._grid_mode = False
            self._video_stack.setCurrentWidget(self._single_widget)
            self.btn_grid_mode.setStyleSheet(self._toolbar_btn_style(False))
            self.btn_single_mode.setStyleSheet(self._toolbar_btn_style(True))

    def _on_video_double_click(self, index: int):
        """双击视频切换全屏/多画面"""
        if self._grid_mode:
            self._switch_to_single(index)
        else:
            self._switch_to_grid()

    def _on_camera_play(self, index: int):
        """从摄像头列表请求播放：单画面模式推送到当前单画面，多画面模式推送到第一格"""
        if index >= len(self.cameras):
            return
        cam = self.cameras[index]
        name = cam.get("ChannelName", f"摄像头 {index+1}")
        url = cam.get("stream_url", "")
        if not url or cam.get("Status", 0) != 1 or cam.get("authority", 0) != 1:
            QMessageBox.information(self, "提示", f"摄像头 {name} 离线或无权限")
            return

        # 判断当前模式，决定推送到哪个播放窗口
        if not self._grid_mode:
            # 单画面模式：推送到当前展示的这一格
            target_index = self._current_single_index
        else:
            # 多画面或6格模式：推送到第一格
            target_index = 0

        if target_index < len(self._video_widgets):
            target_vw = self._video_widgets[target_index]
            target_vw.stop()
            target_vw.set_camera(name, url, True)
            target_vw.play(url)

    def _on_recording_stopped(self, camera_name: str, file_path: str):
        """录像停止后刷新录像列表"""
        logger.info("录像保存: %s -> %s", camera_name, file_path)
        self.recording_list.refresh()

    def _play_recording(self, file_path: str):
        """用 mpv 打开录像文件"""
        from video_widget import MPV_PATH, MPV_AVAILABLE
        if not MPV_AVAILABLE:
            QMessageBox.warning(self, "错误", "mpv.exe 未找到，无法播放录像")
            return
        try:
            import subprocess
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            subprocess.Popen([MPV_PATH, file_path], startupinfo=startupinfo)
            logger.info("播放录像: %s", file_path)
        except Exception as e:
            logger.error("播放录像失败: %s", e)

    def _open_recordings_dir(self):
        """打开录像目录"""
        path = self.config.get("recording_path")
        if not path:
            from config import RECORDINGS_DIR
            path = str(RECORDINGS_DIR)
        import subprocess
        subprocess.Popen(f'explorer "{path}"')

    def _open_settings(self):
        """打开设置对话框"""
        dialog = SettingsDialog(self.config, self)
        if dialog.exec_() == QDialog.Accepted:
            # 刷新录像列表以匹配新路径
            self.recording_list.refresh()
            logger.info("保存设置并刷新录像列表")

    def _on_stream_expired(self, index: int):
        """处理流过期，重新获取 URL"""
        if index >= len(self.cameras):
            return
        cam = self.cameras[index]
        camera_sn = cam.get("ZhsCarameSn", "")
        device_code = cam.get("DeviceCode", "")
        channel_no = cam.get("ChannelNo", 1)
        if not camera_sn or not device_code:
            return

        import threading
        def _refresh():
            try:
                logger.info("流地址过期，重新申请权限和流地址: %s", cam.get("ChannelName", ""))
                self.client.get_camera_authority(camera_sn)
                new_url = self.client.get_stream_url(device_code, channel_no, protocol=2, quality=1)
                
                # 如果萤石云的 accessToken 本身也过期了，则全量刷新设备列表来获取新 token
                if not new_url:
                    logger.info("尝试刷新萤石云 Token...")
                    try:
                        self.client.login() # 刷新主账号状态
                        self.client.get_camera_list() # 获取最新摄像头列表并更新 ys_token
                        new_url = self.client.get_stream_url(device_code, channel_no, protocol=2, quality=1)
                    except Exception as inner_e:
                        logger.error("刷新 Token 失败: %s", inner_e)

                if new_url:
                    cam["stream_url"] = new_url
                    logger.info("流地址刷新成功: %s -> %s...", cam.get("ChannelName", ""), new_url[:60])
                    # 在主线程调用播放
                    QTimer.singleShot(0, lambda: self._video_widgets[index].play(new_url))
                else:
                    logger.warning("刷新流地址最终失败: %s", cam.get("ChannelName", ""))
            except Exception as e:
                logger.error("刷新流地址出错: %s", e)

        threading.Thread(target=_refresh, daemon=True).start()

    def _heartbeat_authority(self):
        """权限保活心跳：后台线程续期摄像头权限"""
        if not self.cameras or not self._video_widgets:
            return

        # 只对正在播放的摄像头续期
        playing_cameras = []
        for i, vw in enumerate(self._video_widgets):
            if vw.is_playing and i < len(self.cameras):
                playing_cameras.append(self.cameras[i])

        if not playing_cameras:
            return

        import threading

        def _renew():
            for cam in playing_cameras:
                try:
                    self.client.get_camera_authority(
                        camera_sn=cam.get("ZhsCarameSn", ""))
                except Exception as e:
                    logger.warning("权限续期失败 [%s]: %s",
                                   cam.get("ChannelName", ""), e)

        threading.Thread(target=_renew, daemon=True).start()
        logger.info("权限保活心跳: %d 个摄像头", len(playing_cameras))

    def closeEvent(self, event):
        """关闭窗口"""
        self._heartbeat_timer.stop()
        for vw in self._video_widgets:
            vw.stop()
        from config import save_config
        self.config["window_geometry"] = self.saveGeometry().toBase64().data().decode()
        save_config(self.config)
        event.accept()


class SettingsDialog(QDialog):
    """系统设置对话框"""
    def __init__(self, config_dict: dict, parent=None):
        super().__init__(parent)
        self.config_dict = config_dict
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle("系统设置")
        self.setFixedSize(450, 290)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e2e;
                color: white;
            }
            QLabel {
                color: #ddd;
                font-size: 13px;
            }
            QLineEdit {
                background-color: #2e2e3e;
                color: white;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:read-only {
                color: #aaa;
                background-color: #1a1a2a;
            }
            QPushButton {
                background-color: #0078d4;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
            QPushButton:pressed {
                background-color: #005a9e;
            }
            QSpinBox {
                background-color: #2e2e3e;
                color: white;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 6px;
                font-size: 13px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # 1. 录像保存路径
        path_layout = QVBoxLayout()
        path_label_layout = QHBoxLayout()
        path_label = QLabel("录像保存路径:")
        path_label_layout.addWidget(path_label)
        path_label_layout.addStretch()
        path_layout.addLayout(path_label_layout)

        path_input_layout = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setReadOnly(True)
        # 获取当前录像路径，若不存在则使用默认配置
        from config import RECORDINGS_DIR
        current_path = self.config_dict.get("recording_path", str(RECORDINGS_DIR))
        self.path_input.setText(current_path)
        path_input_layout.addWidget(self.path_input)

        btn_browse = QPushButton("浏览...")
        btn_browse.setFixedWidth(80)
        btn_browse.clicked.connect(self._on_browse)
        path_input_layout.addWidget(btn_browse)
        path_layout.addLayout(path_input_layout)
        layout.addLayout(path_layout)

        # 2. 录像分割时长
        segment_layout = QHBoxLayout()
        segment_label = QLabel("录像单段分割时长:")
        segment_layout.addWidget(segment_label)

        self.segment_spin = QSpinBox()
        self.segment_spin.setRange(1, 120)  # 支持 1 分钟到 2 小时
        self.segment_spin.setSuffix(" 分钟")
        current_minutes = self.config_dict.get("recording_segment_minutes", 5)
        self.segment_spin.setValue(current_minutes)
        self.segment_spin.setFixedWidth(100)
        segment_layout.addWidget(self.segment_spin)
        segment_layout.addStretch()
        layout.addLayout(segment_layout)

        # 3. 网络连接超时时长
        timeout_layout = QHBoxLayout()
        timeout_label = QLabel("网络连接超时时长:")
        timeout_layout.addWidget(timeout_label)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(3, 120)  # 支持 3 秒到 120 秒
        self.timeout_spin.setSuffix(" 秒")
        current_timeout = self.config_dict.get("network_timeout_seconds", 15)
        self.timeout_spin.setValue(current_timeout)
        self.timeout_spin.setFixedWidth(100)
        timeout_layout.addWidget(self.timeout_spin)
        timeout_layout.addStretch()
        layout.addLayout(timeout_layout)

        # 分割线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #333; margin: 4px 0;")
        layout.addWidget(line)

        # 3. 底部确定取消按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,20);
                color: #ddd;
                border: 1px solid #444;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,35);
            }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_save = QPushButton("确定")
        btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(btn_save)
        layout.addLayout(btn_layout)

    def _on_browse(self):
        # 打开文件夹选择框
        dir_path = QFileDialog.getExistingDirectory(
            self, "选择录像保存目录", self.path_input.text()
        )
        if dir_path:
            # 转换为绝对路径并更新输入框
            self.path_input.setText(str(Path(dir_path).resolve()))

    def _on_save(self):
        new_path = self.path_input.text().strip()
        new_minutes = self.segment_spin.value()
        new_timeout = self.timeout_spin.value()

        if not new_path:
            QMessageBox.warning(self, "警告", "录像保存路径不能为空")
            return

        # 确保路径文件夹存在
        try:
            Path(new_path).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法创建该录像目录:\n{e}")
            return

        # 保存到配置
        self.config_dict["recording_path"] = new_path
        self.config_dict["recording_segment_minutes"] = new_minutes
        self.config_dict["network_timeout_seconds"] = new_timeout

        from config import save_config
        save_config(self.config_dict)
        self.accept()
