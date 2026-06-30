"""掌通家园 API客户端 - 基于真实抓包数据重构"""
import uuid
import json
import base64
import logging
import requests
from Crypto.Cipher import AES

logger = logging.getLogger(__name__)

# ============ 常量 (来自真实抓包) ============
URL_BASE = "https://javaport.hyzhihuixing.com"
URL_PRO = "https://pro.zhihuishu.hyzhihuixing.com"
URL_VIDEO = "https://videoapiv4.hyzhihuixing.com"
URL_HXG = "https://hxg.api.myenglish.com.cn"

URL_APPKEY = f"{URL_PRO}/service/v2/appkey"
URL_LOGIN = f"{URL_BASE}/service/v2/user/relativeLogin"
URL_CAMERA_LIST = f"{URL_HXG}/api/v4/Video/GetVideoCameraListByZhsSchoolId"
URL_CAMERA_AUTH = f"{URL_VIDEO}/video/v4/parent/camera/authority"
URL_QUERY_STATUS = f"{URL_VIDEO}/video/v4/teacher/queryStatus"
URL_DEVICE = f"{URL_VIDEO}/video/v4/user/device"

APP_VERSION = "P_Final_8.1.7"
VERSION_CODE = 817

# 萤石云配置 (来自抓包)
EZVIZ_APP_KEY = "8e7c062da6ce4a1b880e65793f96d834"
EZVIZ_APP_ID = "com.hyww.wisdomtree"


class BBTreeClient:
    """掌通家园 API客户端"""

    def __init__(self, phone="", password=""):
        self.phone = phone
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "android",
            "Content-Type": "application/json; charset=utf-8",
            "Accept-Encoding": "gzip",
        })
        self.aes_key = None
        self.device_uuid = uuid.uuid4().hex[:16]
        self.token = ""
        self.user_id = 0
        self.child_id = 0
        self.class_id = 0
        self.school_id = 0
        self.children = []
        self.ys_token = ""

    # ============ AES加密/解密 ============

    def _encrypt(self, data_dict):
        """AES/ECB/ZeroBytePadding 加密"""
        if not self.aes_key:
            raise RuntimeError("AES密钥未获取，请先调用 get_appkey()")
        data_bytes = json.dumps(data_dict, separators=(",", ":")).encode("utf-8")
        pad_len = 16 - (len(data_bytes) % 16)
        if pad_len == 16:
            pad_len = 0
        padded = data_bytes + b"\x00" * pad_len
        cipher = AES.new(self.aes_key, AES.MODE_ECB)
        encrypted = cipher.encrypt(padded)
        return base64.b64encode(encrypted).decode("utf-8")

    def _decrypt(self, b64_data):
        """AES/ECB/ZeroBytePadding 解密"""
        if not self.aes_key:
            raise RuntimeError("AES密钥未获取")
        encrypted = base64.b64decode(b64_data)
        cipher = AES.new(self.aes_key, AES.MODE_ECB)
        decrypted = cipher.decrypt(encrypted).rstrip(b"\x00")
        return json.loads(decrypted.decode("utf-8"))

    def _post_encrypted(self, url, data_dict):
        """发送加密POST请求"""
        body = {"data": self._encrypt(data_dict), "uuid": self.device_uuid}
        resp = self.session.post(url, json=body, timeout=15)
        resp.raise_for_status()
        text = resp.text.strip()
        if not text:
            return {}
        if text.startswith("{"):
            return resp.json()
        try:
            return self._decrypt(text)
        except Exception:
            logger.warning("无法解密响应: %s", text[:100])
            return {"raw": text}

    def _common_params(self):
        """公共请求参数"""
        return {
            "child_id": self.child_id,
            "curr": {
                "child_id": self.child_id,
                "class_id": self.class_id,
                "school_id": self.school_id,
                "user_id": self.user_id,
            },
            "data_ver": VERSION_CODE,
            "isMergeVersion": False,
            "platform": 2,
            "school_app_type": 0,
            "version_code": VERSION_CODE,
            "version_no": APP_VERSION,
        }

    # ============ API方法 ============

    def get_appkey(self):
        """获取AES加密密钥"""
        body = {
            "app_version": APP_VERSION,
            "channel_id": "5",
            "model": "PC",
            "system_version": "Windows",
            "uuid": self.device_uuid,
            "child_id": 0,
            "curr": {"child_id": 0, "class_id": 0, "school_id": 0, "user_id": 0},
            "data_ver": VERSION_CODE,
            "isMergeVersion": False,
            "platform": 2,
            "school_app_type": 0,
            "version_code": VERSION_CODE,
            "version_no": APP_VERSION,
        }
        resp = self.session.post(URL_APPKEY, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        key_code = data.get("key_code", "")
        if not key_code:
            raise RuntimeError(f"获取appkey失败: {data}")
        self.aes_key = key_code.encode("utf-8")
        logger.info("获取appkey成功")
        return key_code

    def login(self):
        """登录并获取用户信息"""
        if not self.aes_key:
            self.get_appkey()

        login_data = {
            "account_type": 0,
            "classId": 0,
            "client_type": 1,
            "loginType": 0,
            "password": self.password,
            "userId": 0,
            "username": self.phone,
            "versionCode": VERSION_CODE,
            "child_id": 0,
            "curr": {"child_id": 0, "class_id": 0, "school_id": 0, "user_id": 0},
            "data_ver": VERSION_CODE,
            "isMergeVersion": False,
            "platform": 2,
            "school_app_type": 0,
            "version_code": VERSION_CODE,
            "version_no": APP_VERSION,
        }
        data = self._post_encrypted(URL_LOGIN, login_data)

        login_inner = data.get("data", data)
        self.user_id = login_inner.get("user_id", 0)
        self.children = login_inner.get("children", [])
        logger.info("登录成功: user_id=%s, children=%d", self.user_id, len(self.children))
        return login_inner

    def select_child(self, child_id, class_id=0, school_id=0):
        """选择宝宝"""
        self.child_id = child_id
        self.class_id = class_id
        self.school_id = school_id

    def get_camera_list(self):
        """获取摄像头列表 (明文接口)"""
        logger.info("请求摄像头列表: school_id=%s, url=%s", self.school_id, URL_CAMERA_LIST)
        resp = requests.get(URL_CAMERA_LIST, params={"zhsSchoolId": self.school_id}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        data_list = (data.get("Data") or []) if isinstance(data, dict) else []
        logger.info("摄像头API响应: Code=%s, Data长度=%d", data.get("Code", "?"), len(data_list))
        cameras = data_list if isinstance(data, dict) else data
        if not cameras:
            logger.warning("摄像头列表为空，响应: %s", str(data)[:200])
            return []
        # 提取萤石云token
        if cameras[0].get("YsToken"):
            self.ys_token = cameras[0]["YsToken"]
        return cameras

    def get_camera_authority(self, camera_sn=""):
        """获取/保持摄像头观看权限"""
        params = {
            "cameraSn": camera_sn,
            "childId": self.child_id,
            "classId": self.class_id,
            "programId": 0,
            "role": 3,
            "schoolId": self.school_id,
            "userId": self.user_id,
            "watchTime": 5,
            "appinfo": {
                "app_type": 3,
                "child_id": self.child_id,
                "class_id": self.class_id,
                "client_type": 1,
                "is_main_app": 1,
                "school_id": self.school_id,
                "user_id": self.user_id,
                "version": "8.1.7",
            },
            **self._common_params(),
        }
        enc_data = self._encrypt(params)
        url = f"{URL_CAMERA_AUTH}?uuid={self.device_uuid}&data={requests.utils.quote(enc_data, safe='')}"
        resp = self.session.get(url, timeout=15, headers={"Content-Type": ""})
        resp.raise_for_status()
        text = resp.text.strip()
        if text.startswith("{"):
            return resp.json()
        try:
            return self._decrypt(text)
        except Exception:
            return {"raw": text}

    def get_stream_url(self, device_code, channel_no, protocol=2, quality=1):
        """获取摄像头直播流URL (萤石云EZVIZ)

        Args:
            device_code: NVR设备编码 (DeviceCode)
            channel_no: 通道号 (ChannelNo)
            protocol: 协议类型 1=ezopen(私有), 2=HLS(m3u8), 3=RTMP
            quality: 1=流畅, 2=标清, 3=高清, 4=超清

        Returns:
            直播流URL字符串，失败返回空字符串
        """
        if not self.ys_token:
            logger.warning("萤石云Token未设置，请先调用get_camera_list()")
            return ""

        url = "https://open.ys7.com/api/lapp/v2/live/address/get"
        resp = requests.post(url, data={
            "accessToken": self.ys_token,
            "deviceSerial": device_code,
            "channelNo": channel_no,
            "protocol": protocol,
            "quality": quality,
            "type": "1",
            "expireTime": 86400 * 7,
            "supportH265": "1",
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        code = data.get("code", "")
        if str(code) not in ("200", "0"):
            logger.warning("获取直播地址失败: %s", data.get("msg", ""))
            return ""
        return data.get("data", {}).get("url", "")

    def get_all_stream_urls(self, cameras, protocol=2, quality=1):
        """批量获取摄像头直播流URL

        Args:
            cameras: get_camera_list()返回的摄像头列表
            protocol: 协议类型 1=HLS
            quality: 画质 2=标清

        Returns:
            字典 {ChannelName: stream_url}
        """
        urls = {}
        for cam in cameras:
            if cam.get("Status") != 1:
                continue
            device_code = cam.get("DeviceCode", "")
            channel_no = cam.get("ChannelNo", 1)
            name = cam.get("ChannelName", "")
            try:
                url = self.get_stream_url(device_code, channel_no, protocol, quality)
                if url:
                    urls[name] = url
                    logger.info("获取流地址: %s -> %s...", name, url[:60])
            except Exception as e:
                logger.error("获取流地址失败 [%s]: %s", name, e)
        return urls

    def get_device_info(self, device_serial, camera_no=1):
        """获取设备详情 (萤石云)"""
        params = {
            "classId": self.class_id,
            "role": 3,
            "schoolId": self.school_id,
            "isMainApp": 1,
            "platformType": 1,
            "source": 0,
            "childId": self.child_id,
            "userId": self.user_id,
            "currentVersion": "8.1.7",
        }
        resp = self.session.get(URL_QUERY_STATUS, params=params, timeout=15, headers={"Content-Type": ""})
        resp.raise_for_status()
        return resp.json()
