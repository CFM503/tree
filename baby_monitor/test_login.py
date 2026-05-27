"""掌通家园 PC客户端 - 完整测试（含视频流）"""
import sys
import json
sys.stdout.reconfigure(encoding='utf-8')

from api_client import BBTreeClient

# ====== 测试完整流程 ======
import getpass
phone = input("手机号: ")
password = getpass.getpass("密码: ")
client = BBTreeClient(phone, password)

# Step 1: 获取AES密钥
print("=== Step 1: 获取AES密钥 ===")
key = client.get_appkey()
print(f"  密钥: {key[:16]}...")

# Step 2: 登录
print("\n=== Step 2: 登录 ===")
login_data = client.login()
print(f"  user_id: {client.user_id}")
print(f"  宝宝数量: {len(client.children)}")
for c in client.children:
    print(f"  - {c['name']}: {c.get('school_name','?')} (child_id={c['child_id']})")

# Step 3: 选择张书瑜
print("\n=== Step 3: 选择宝宝 ===")
child = [c for c in client.children if c.get('name') == '张书瑜'][0]
client.select_child(child['child_id'], child['class_id'], child['school_id'])
print(f"  已选择: {child['name']} @ {child.get('school_name','?')}")

# Step 4: 获取摄像头列表
print("\n=== Step 4: 摄像头列表 ===")
cameras = client.get_camera_list()
print(f"  共 {len(cameras)} 个摄像头:")
for cam in cameras[:10]:
    status = "在线" if cam.get('Status') == 1 else "离线"
    print(f"  - {cam['ChannelName']}: {status}")
print(f"  ... (共{len(cameras)}个)")

# Step 5: 获取萤石云Token
print(f"\n=== Step 5: 萤石云Token ===")
print(f"  Token: {client.ys_token[:40]}...")

# Step 6: 保持摄像头权限
print(f"\n=== Step 6: 摄像头权限 ===")
if cameras:
    sn = cameras[0].get('ZhsCarameSn', '')
    print(f"  测试摄像头: {cameras[0]['ChannelName']} (SN={sn})")
    try:
        auth = client.get_camera_authority(camera_sn=sn)
        print(f"  权限状态: {json.dumps(auth, ensure_ascii=False)}")
    except Exception as e:
        print(f"  权限请求失败: {e}")

# Step 7: 获取视频流URL
print(f"\n=== Step 7: 获取视频流URL ===")
online_cameras = [c for c in cameras if c.get('Status') == 1]
print(f"  在线摄像头: {len(online_cameras)} 个")

# 取前2个在线摄像头测试
test_cameras = online_cameras[:2]
stream_urls = []
for cam in test_cameras:
    name = cam['ChannelName']
    device_code = cam.get('DeviceCode', '')
    channel_no = cam.get('ChannelNo', 1)
    print(f"  获取: {name} (Device={device_code}, Ch={channel_no})")
    try:
        url = client.get_stream_url(device_code, channel_no)
        if url:
            print(f"    URL: {url[:80]}...")
            stream_urls.append({"name": name, "channel": channel_no, "sn": cam.get('ZhsCarameSn', ''), "url": url})
        else:
            print(f"    未获取到URL")
    except Exception as e:
        print(f"    失败: {e}")

# Step 8: 批量获取流地址
print(f"\n=== Step 8: 批量获取流地址 ===")
try:
    all_urls = client.get_all_stream_urls(online_cameras)
    print(f"  成功获取 {len(all_urls)} 个流地址")
except Exception as e:
    print(f"  批量获取失败: {e}")

# Step 9: 验证流URL格式
print(f"\n=== Step 9: 验证流URL ===")
for item in stream_urls:
    url = item["url"]
    name = item["name"]
    if ".m3u8" in url:
        print(f"  {name}: HLS OK ({len(url)} chars)")
    elif "rtmp://" in url:
        print(f"  {name}: RTMP OK")
    else:
        print(f"  {name}: 未知协议 - {url[:60]}...")

print("\n=== 全部测试完成! ===")
print(f"  摄像头数据已保存到 cameras.json")
print(f"  流地址已保存到 stream_urls.json")

# 保存数据
with open('cameras.json', 'w', encoding='utf-8') as f:
    json.dump(cameras, f, ensure_ascii=False, indent=2)

with open('stream_urls.json', 'w', encoding='utf-8') as f:
    json.dump(stream_urls, f, ensure_ascii=False, indent=2)
