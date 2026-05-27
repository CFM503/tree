"""录像管理模块

基于FFmpeg的录像功能。
"""
import os
import sys
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RecordingInfo:
    """录像文件信息"""
    file_path: str
    camera_name: str
    start_time: str
    file_size: int = 0
    duration: float = 0

    @property
    def filename(self) -> str:
        return os.path.basename(self.file_path)

    @property
    def exists(self) -> bool:
        return os.path.isfile(self.file_path)


class FFmpegRecorder:
    """基于FFmpeg的录像器（备用方案）"""

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._output_path: str = ""
        self._camera_name: str = ""
        self.is_recording: bool = False
        self._ffmpeg_path: str = self._find_ffmpeg()

    def _find_ffmpeg(self) -> str:
        """查找FFmpeg可执行文件"""
        # 常见安装路径
        candidates = [
            "ffmpeg",
            "ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            os.path.expanduser(r"~\ffmpeg\bin\ffmpeg.exe"),
        ]
        for c in candidates:
            try:
                result = subprocess.run(
                    [c, "-version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                if result.returncode == 0:
                    return c
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return ""

    @property
    def available(self) -> bool:
        return bool(self._ffmpeg_path)

    def start_recording(self, stream_url: str, output_dir: str, camera_name: str) -> Optional[str]:
        """
        开始录像

        Args:
            stream_url: 视频流URL
            output_dir: 输出目录
            camera_name: 摄像头名称

        Returns:
            输出文件路径，失败返回None
        """
        if not self._ffmpeg_path:
            logger.error("FFmpeg未安装，无法录像")
            return None

        if self.is_recording:
            logger.warning("已在录像中")
            return None

        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = camera_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
        filename = f"{safe_name}_{timestamp}.ts"
        output_path = str(Path(output_dir) / filename)

        try:
            cmd = [
                self._ffmpeg_path,
                "-y",                    # 覆盖输出
                "-rtsp_transport", "tcp", # 使用TCP传输RTSP
                "-i", stream_url,         # 输入流
                "-c:v", "copy",           # 视频直接复制（不重编码）
                "-c:a", "copy",           # 音频直接复制
                "-f", "mpegts",           # 输出格式
                output_path,
            ]

            # Windows下隐藏窗口
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )

            self._output_path = output_path
            self._camera_name = camera_name
            self.is_recording = True
            logger.info(f"FFmpeg开始录像: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"FFmpeg录像启动失败: {e}")
            return None

    def stop_recording(self) -> Optional[str]:
        """
        停止录像

        Returns:
            录像文件路径
        """
        if not self.is_recording or not self._process:
            return None

        try:
            # 发送 'q' 让FFmpeg优雅退出
            self._process.communicate(input=b"q", timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass

        self.is_recording = False
        saved_path = self._output_path
        self._process = None
        logger.info(f"FFmpeg停止录像: {saved_path}")
        return saved_path

    def get_current_size(self) -> int:
        """获取当前录像文件大小（字节）"""
        if self._output_path and os.path.isfile(self._output_path):
            return os.path.getsize(self._output_path)
        return 0


class RecordingManager:
    """录像管理器"""

    def __init__(self, recordings_dir: str):
        self.recordings_dir = Path(recordings_dir)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self._active_recordings: Dict[str, FFmpegRecorder] = {}

    def scan_recordings(self) -> List[RecordingInfo]:
        """扫描录像目录，返回录像文件列表"""
        recordings = []
        files = list(self.recordings_dir.glob("*.mp4")) + list(self.recordings_dir.glob("*.ts"))
        for f in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
            info = RecordingInfo(
                file_path=str(f),
                camera_name=self._extract_camera_name(f.name),
                start_time=datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                file_size=f.stat().st_size,
            )
            recordings.append(info)
        return recordings

    def _extract_camera_name(self, filename: str) -> str:
        """从文件名提取摄像头名称"""
        # 文件名格式: 摄像头名_20240101_120000.ts/.mp4
        parts = filename.rsplit("_", 3)
        if len(parts) >= 3:
            return parts[0].replace("_", " ")
        return filename

    def delete_recording(self, file_path: str) -> bool:
        """删除录像文件"""
        try:
            p = Path(file_path)
            if p.exists() and p.parent == self.recordings_dir:
                p.unlink()
                logger.info(f"删除录像: {file_path}")
                return True
        except Exception as e:
            logger.error(f"删除录像失败: {e}")
        return False

    def get_total_size(self) -> int:
        """获取录像目录总大小"""
        total = 0
        for f in self.recordings_dir.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return total

    def format_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"
