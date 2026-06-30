"""配置管理模块"""
import sys
import json
import base64
from pathlib import Path

# 判断是否是打包后的单文件环境，确保保存的配置文件和录像文件位于 exe 所在的同一目录下，而非临时目录
if getattr(sys, 'frozen', False):
    EXE_DIR = Path(sys.executable).parent
else:
    EXE_DIR = Path(__file__).parent

CONFIG_FILE = EXE_DIR / "config.json"
RECORDINGS_DIR = EXE_DIR / "rec"
RECORDINGS_DIR.mkdir(exist_ok=True)

DEFAULT_CONFIG = {
    "api_base_url": "https://videoapiv4.hyzhihuixing.com",
    "recording_path": str(RECORDINGS_DIR),
    "recording_segment_minutes": 5,  # 默认录像分割时长：5分钟
    "network_timeout_seconds": 15,    # 默认网络超时时间：15秒
    "remember_password": False,
    "phone": "",
    "password_encrypted": "",
    "last_cameras": [],
    "window_geometry": None,
}


def _obfuscate_password(text: str) -> str:
    """Base64 obfuscation for local password storage. NOT encryption."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _deobfuscate_password(encoded: str) -> str:
    try:
        return base64.b64decode(encoded.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception:
            pass
    return cfg


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_password(cfg: dict) -> str:
    return _deobfuscate_password(cfg.get("password_encrypted", ""))


def set_password(cfg: dict, password: str):
    cfg["password_encrypted"] = _obfuscate_password(password)
