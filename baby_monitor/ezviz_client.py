"""萤石云 (EZVIZ/HikVision) 工具函数

从掌通家园API获取的萤石云数据中提取流地址等信息。
直播流获取已移至 api_client.py 的 get_stream_url()。
"""
import logging

logger = logging.getLogger(__name__)

# 萤石云配置 (来自APK抓包)
EZVIZ_APP_KEY = "8e7c062da6ce4a1b880e65793f96d834"
EZVIZ_APP_ID = "com.hyww.wisdomtree"
EZVIZ_OPEN_API = "https://open.ys7.com"


def parse_camera_info(cam_dict):
    """从摄像头API返回的字典中提取关键信息

    Args:
        cam_dict: get_camera_list()返回的单个摄像头字典

    Returns:
        dict with keys: name, device_code, channel_no, status, sn, ys_token
    """
    return {
        "name": cam_dict.get("ChannelName", ""),
        "device_code": cam_dict.get("DeviceCode", ""),
        "channel_no": cam_dict.get("ChannelNo", 1),
        "status": cam_dict.get("Status", 0),
        "sn": cam_dict.get("ZhsCarameSn", ""),
        "ys_token": cam_dict.get("YsToken", ""),
        "ipc_serial": cam_dict.get("IpcSerial", ""),
        "is_encrypt": cam_dict.get("IsEncrypt", 0),
        "quality": cam_dict.get("Quality", 0),
    }


def is_camera_online(cam_dict):
    """检查摄像头是否在线"""
    return cam_dict.get("Status", 0) == 1


def format_hls_url(device_code, channel_no, quality=2, expire=None, token=""):
    """格式化萤石云HLS直播URL

    Args:
        device_code: NVR设备编码
        channel_no: 通道号
        quality: 画质 1=流畅 2=标清 3=高清
        expire: 过期时间戳
        token: 访问token

    Returns:
        HLS URL字符串
    """
    url = f"{EZVIZ_OPEN_API}/v3/openlive/{device_code}_{channel_no}_{quality}.m3u8"
    if expire and token:
        url += f"?expire={expire}&t={token}"
    return url
