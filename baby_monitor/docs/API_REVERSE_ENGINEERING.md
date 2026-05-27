# 掌通家园 API 逆向工程文档

> 基于 APK (P.apk v8.1.7) 逆向分析 + 真实流量抓包，完整记录掌通家园的 API 架构。
> 最后更新: 2026-05-26

---

## 1. 服务器架构

掌通家园使用 4 个独立域名，各司其职:

| 域名 | 用途 | 协议 |
|------|------|------|
| `javaport.hyzhihuixing.com` | 用户登录 (relativeLogin) | AES 加密 |
| `pro.zhihuishu.hyzhihuixing.com` | 获取 AES 密钥 (appkey) | 明文 JSON |
| `videoapiv4.hyzhihuixing.com` | 摄像头权限/设备查询 | AES 加密 |
| `hxg.api.myenglish.com.cn` | 摄像头列表 (明文) | 明文 JSON |

视频流通过萤石云 (EZVIZ) 分发:

| 域名 | 用途 |
|------|------|
| `open.ys7.com` | 萤石云 OpenAPI (获取直播地址) |
| `open.ezvizlife.com` | 萤石云备用域名 |

---

## 2. 登录流程

登录分两步: 先获取 AES 密钥，再用 AES 加密发送登录请求。

### Step 1: 获取 AES 密钥 (appkey)

```
POST https://pro.zhihuishu.hyzhihuixing.com/service/v2/appkey
Content-Type: application/json

{
    "app_version": "P_Final_8.1.7",
    "channel_id": "5",
    "model": "PC",
    "system_version": "Windows",
    "uuid": "<16位随机hex>",
    "child_id": 0,
    "curr": {"child_id": 0, "class_id": 0, "school_id": 0, "user_id": 0},
    "data_ver": 817,
    "isMergeVersion": false,
    "platform": 2,
    "school_app_type": 0,
    "version_code": 817,
    "version_no": "P_Final_8.1.7"
}
```

响应 (明文 JSON):
```json
{
    "key_code": "<16字节AES密钥>"
}
```

### Step 2: 登录 (relativeLogin)

```
POST https://javaport.hyzhihuixing.com/service/v2/user/relativeLogin
Content-Type: application/json

{
    "data": "<AES加密后的base64>",
    "uuid": "<设备UUID>"
}
```

加密前的明文数据:
```json
{
    "account_type": 0,
    "classId": 0,
    "client_type": 1,
    "loginType": 0,
    "password": "<明文密码>",
    "userId": 0,
    "username": "<手机号>",
    "versionCode": 817,
    "child_id": 0,
    "curr": {"child_id": 0, "class_id": 0, "school_id": 0, "user_id": 0},
    "data_ver": 817,
    "isMergeVersion": false,
    "platform": 2,
    "school_app_type": 0,
    "version_code": 817,
    "version_no": "P_Final_8.1.7"
}
```

响应 (AES 加密): 解密后得到:
```json
{
    "code": "000",
    "data": {
        "user_id": 250887591,
        "token_id": "QNcr1l1nxsN-PNRIT1mWPjM5HRuhsF2E",
        "children": [
            {
                "child_id": 241954492,
                "class_id": 213875324,
                "school_id": 203152785,
                "name": "张书瑜",
                "school_name": "凯乐奇贝森之家幼儿园",
                "class_name": "K1b",
                "call": "爸爸",
                ...
            }
        ],
        ...
    },
    "msg": "请求成功!"
}
```

---

## 3. AES 加密方案

掌通家园使用 **AES/ECB/ZeroBytePadding**:

- **模式**: ECB (无 IV)
- **填充**: ZeroBytePadding (不足 16 字节时补 `\x00`，刚好 16 倍数时不补)
- **密钥**: 动态获取，每次调用 appkey 接口返回不同的 16 字节密钥
- **编码**: 明文 JSON 序列化为 `{"key":value}` 紧凑格式 → AES 加密 → Base64 编码

Python 实现:
```python
from Crypto.Cipher import AES
import json, base64

def encrypt(data_dict, aes_key):
    data_bytes = json.dumps(data_dict, separators=(",", ":")).encode("utf-8")
    pad_len = 16 - (len(data_bytes) % 16)
    if pad_len == 16:
        pad_len = 0
    padded = data_bytes + b"\x00" * pad_len
    cipher = AES.new(aes_key, AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(padded)).decode("utf-8")

def decrypt(b64_data, aes_key):
    encrypted = base64.b64decode(b64_data)
    cipher = AES.new(aes_key, AES.MODE_ECB)
    return json.loads(cipher.decrypt(encrypted).rstrip(b"\x00").decode("utf-8"))
```

---

## 4. 摄像头列表接口

**明文接口**，无需加密:

```
GET https://hxg.api.myenglish.com.cn/api/v4/Video/GetVideoCameraListByZhsSchoolId?zhsSchoolId=<school_id>
```

响应 (明文 JSON):
```json
[
    {
        "ZhsCarameSn": "nod20200919T14282095001",
        "ZhsSchoolId": 203152785,
        "ChannelName": "视频1@DS-7832N-R2(B)(K38932817)",
        "DeviceCode": "K38932817",
        "ChannelNo": 1,
        "Status": 1,
        "YsToken": "at.0bhahawv55gbq0tzbxa1t5iq89iryybh-...",
        "IsEncrypt": 0,
        "Quality": 0,
        "PicUrl": "https://s0.hybbtree.com/...",
        "VideoLevel": 2,
        "Permission": 0,
        "RelatedIpc": true,
        "IpcSerial": "K98667855"
    },
    ...
]
```

关键字段:
- `ZhsCarameSn`: 摄像头唯一标识 (掌通系统内)
- `DeviceCode`: NVR 设备编号 (海康威视)
- `ChannelNo`: 通道号 (1-32)
- `Status`: 1=在线, 0=离线
- `YsToken`: 萤石云 AccessToken (有效期约 2 小时)
- `IpcSerial`: IPC 摄像头序列号

---

## 5. 摄像头权限接口

```
GET https://videoapiv4.hyzhihuixing.com/video/v4/parent/camera/authority
    ?uuid=<设备UUID>
    &data=<AES加密的Base64>
```

加密前的参数:
```json
{
    "cameraSn": "<ZhsCarameSn>",
    "childId": 241954492,
    "classId": 213875324,
    "programId": 0,
    "role": 3,
    "schoolId": 203152785,
    "userId": 250887591,
    "watchTime": 5,
    "appinfo": {
        "app_type": 3,
        "child_id": 241954492,
        "class_id": 213875324,
        "client_type": 1,
        "is_main_app": 1,
        "school_id": 203152785,
        "user_id": 250887591,
        "version": "8.1.7"
    },
    ...公共参数
}
```

---

## 6. 视频流获取 (萤石云 EZVIZ)

### 6.1 萤石云配置

掌通家园嵌入的萤石云 SDK 配置:

| 常量 | 值 |
|------|------|
| APP_KEY | `8e7c062da6ce4a1b880e65793f96d834` |
| APP_ID | `com.hyww.wisdomtree` |
| OpenAPI | `https://open.ys7.com` |

### 6.2 获取直播地址

```
POST https://open.ys7.com/api/lapp/v2/live/address/get
Content-Type: application/x-www-form-urlencoded

accessToken=<YsToken>&
deviceSerial=<DeviceCode>&
channelNo=<ChannelNo>&
protocol=2&
quality=2&
type=1&
expireTime=3600
```

参数说明:
- `accessToken`: 从摄像头列表的 `YsToken` 字段获取
- `deviceSerial`: NVR 设备编码 (`DeviceCode`)
- `channelNo`: 通道号 (`ChannelNo`)
- `protocol`: 1=ezopen(私有协议), 2=HLS(m3u8), 3=RTMP
- `quality`: 1=流畅, 2=标清, 3=高清, 4=超清
- `supportH265`: **关键参数**，设为 `1` 可获取 H.265 编码的原始流；不带此参数时 H.265 设备会返回错误提示视频
- `type`: 1=预览, 2=回放

响应:
```json
{
    "code": "200",
    "msg": "操作成功!",
    "data": {
        "url": "https://open.ys7.com/v3/openlive/K38932817_27_2.m3u8?expire=...",
        "expireTime": 1779852926
    }
}
```

### 6.3 URL 格式

HLS 直播 URL 格式:
```
https://open.ys7.com/v3/openlive/{DeviceCode}_{ChannelNo}_{Quality}.m3u8?expire={timestamp}&id={id}&t={token}&ev=101
```

---

## 7. 公共请求参数

所有加密接口都包含以下公共字段:

```json
{
    "child_id": 241954492,
    "curr": {
        "child_id": 241954492,
        "class_id": 213875324,
        "school_id": 203152785,
        "user_id": 250887591
    },
    "data_ver": 817,
    "isMergeVersion": false,
    "platform": 2,
    "school_app_type": 0,
    "version_code": 817,
    "version_no": "P_Final_8.1.7"
}
```

---

## 8. 请求头规范

所有 API 请求:
```
User-Agent: android
Content-Type: application/json; charset=utf-8
Accept-Encoding: gzip
```

萤石云 API:
```
Content-Type: application/x-www-form-urlencoded
```

---

## 9. 关键常量

| 常量 | 值 | 说明 |
|------|------|------|
| APP_VERSION | `P_Final_8.1.7` | 应用版本 |
| VERSION_CODE | `817` | 版本号 |
| platform | `2` | PC 端 |
| client_type | `1` | 客户端类型 |
| role | `3` | 家长角色 |
| EZVIZ_APP_KEY | `8e7c062da6ce4a1b880e65793f96d834` | 萤石云 AppKey |
| EZVIZ_APP_ID | `com.hyww.wisdomtree` | 萤石云 AppId |

---

## 10. 错误码

| code | 含义 |
|------|------|
| `000` | 请求成功 (掌通 API) |
| `200` | 操作成功 (萤石云 API) |
| `1000` | 参数错误 |
| `2000` | Token 过期/无效 |
| `4000` | 无权限 |

---

## 11. 完整数据流

```
┌─────────┐    appkey     ┌──────────────────┐
│ PC客户端 │ ────────────→ │ pro.zhihuishu... │  获取 AES 密钥
└────┬────┘               └──────────────────┘
     │
     │  relativeLogin (AES加密)
     │──────────────────────→ javaport.hyzhihuixing.com  登录
     │                        返回 user_id, children, token_id
     │
     │  GetVideoCameraList (明文)
     │──────────────────────→ hxg.api.myenglish.com.cn  摄像头列表
     │                        返回 cameras + YsToken
     │
     │  camera/authority (AES加密)
     │──────────────────────→ videoapiv4.hyzhihuixing.com  激活观看权限
     │
     │  live/address/get (萤石云)
     │──────────────────────→ open.ys7.com  获取 HLS 流 URL
     │
     │  HLS (.m3u8) 播放
     │──────────────────────→ open.ys7.com  VLC 拉流播放
     ▼
  视频画面
```

---

## 12. 注意事项

1. **AES 密钥动态变化**: 每次调用 appkey 接口会返回不同的密钥，不能缓存复用
2. **YsToken 有效期**: 萤石云 AccessToken 约 2 小时过期，过期后需重新获取摄像头列表
3. **权限维持**: 每 5 分钟需调用 camera/authority 接口保持观看权限 (watchTime=5)
4. **密码明文传输**: 登录接口的密码字段是明文，通过 AES 加密通道传输
5. **学校-班级-孩子层级**: API 使用 school_id → class_id → child_id 三级关联
