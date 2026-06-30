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
