#!/usr/bin/env python3
"""
Xiaoyi Image Generator - Helper Script

This is a helper script for batch generation or CLI usage.
The main skill workflow is defined in SKILL.md and executed by Claude.

Usage:
    python generate_seedream.py "prompt" --image https://example.com/photo.png --max-images 3
"""

import requests
import json
import os
import sys
import uuid
import hashlib
import random
import string
import base64
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
import argparse
from config import Config

# MIME类型映射
_MIME_TYPE_MAP = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.bmp': 'image/bmp',
}

def read_xiaoyienv():
    """
    读取 ~/.openclaw/.xiaoyienv 文件并返回键值对字典。
    """
    file_path = os.path.expanduser("~/.openclaw/.xiaoyienv")
    env_dict = {}

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            for line in file:
                # 去除行首尾的空白字符和换行符
                line = line.strip()
                # 跳过空行和以 # 开头的注释行
                if not line or line.startswith('#'):
                    continue
                # 确保行中包含等号
                if '=' in line:
                    # 只在第一个等号处分割，防止 value 中包含等号
                    key, value = line.split('=', 1)
                    env_dict[key.strip()] = value.strip()

    except FileNotFoundError:
        print(f"提示: 未找到文件 {file_path}")
    except Exception as e:
        print(f"读取文件时发生错误: {e}")

    return env_dict


def _is_remote_url(value):
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_local_file(value):
    """Check if value is a local file path."""
    try:
        return Path(value).is_file()
    except (OSError, TypeError):
        return False
    

def validate_image_input(value):
    """Validate that the image parameter is a remote URL or local file path."""
    if not (_is_remote_url(value) or _is_local_file(value)):
        raise argparse.ArgumentTypeError(
            f"Image must be a remote HTTP/HTTPS URL or a local file path, not: {value}"
        )
    return value


def _image_to_base64(image_path):
    """将本地图片文件转为base64字符串（带data URI前缀）。"""
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
            b64_string = base64.b64encode(image_data).decode('utf-8')
            
            # 根据文件扩展名确定MIME类型
            ext = Path(image_path).suffix.lower()
            mime_type = _MIME_TYPE_MAP.get(ext, 'image/png')
            return f"data:{mime_type};base64,{b64_string}"
    except Exception as e:
        raise ValueError(f"Failed to convert image to base64: {e}")


def calculate_sha256(file_path):
    """计算文件的 SHA256 哈希值"""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def upload_file(file_path, object_type="TEMPORARY_MATERIAL_DOC"):
    """
    将本地文件上传到小艺文件存储服务（三阶段上传：prepare → upload → complete）

    Args:
        file_path: 本地文件路径
        object_type: 文件类型（默认 TEMPORARY_MATERIAL_DOC）

    Returns:
        fileUrl
    """
    try:
        # 校验文件存在
        if not os.path.isfile(file_path):
            print(f'❌ 文件不存在：{file_path}')
            return None

        # 读取并校验配置
        config = read_xiaoyienv()

        required_keys = ['PERSONAL-API-KEY', 'PERSONAL-UID']
        check_result = True

        for key in required_keys:
            if key not in config:
                print(f'❌ key "{key}" 不存在：失败...')
                check_result = False

        if not check_result:
            return None

        # 准备文件信息
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        file_sha256 = calculate_sha256(file_path)
        uid = config['PERSONAL-UID']
        base_url = config.get('SERVICE_URL', '')

        # 公共请求头
        common_headers = {
            'Content-Type': 'application/json',
            'x-uid': uid,
            'x-api-key': config['PERSONAL-API-KEY'],
            'x-request-from': 'openclaw',
        }

        # ── 阶段 1: Prepare ──────────────────────────────────────────────────────
        prepare_url = f'{base_url}/osms/v1/file/manager/prepare'

        prepare_payload = {
            'objectType': object_type,
            'fileName': file_name,
            'fileSha256': file_sha256,
            'fileSize': file_size,
            'fileOwnerInfo': {
                'uid': uid,
                'teamId': uid,
            },
            'useEdge': False,
        }

        prepare_resp = requests.post(
            prepare_url,
            headers=common_headers,
            json=prepare_payload,
            timeout=30
        )

        if prepare_resp.status_code != 200:
            print(f'❌ Prepare 请求失败: HTTP {prepare_resp.status_code}')
            print(f'❌ 响应内容: {prepare_resp.text}')
            return None

        prepare_data = prepare_resp.json()

        # 部分服务器返回 code 字段，"0" 为成功
        if 'code' in prepare_data and prepare_data['code'] != '0':
            print(f'❌ Prepare 失败: {prepare_data.get("desc", "未知错误")}')
            return None

        object_id = prepare_data.get('objectId')
        draft_id = prepare_data.get('draftId')
        upload_infos = prepare_data.get('uploadInfos', [])

        if not object_id or not draft_id or not upload_infos:
            print(f'❌ Prepare 响应缺少必要字段: objectId={object_id}, draftId={draft_id}')
            return None

        upload_info = upload_infos[0]
        upload_url = upload_info['url']
        upload_method = upload_info.get('method', 'PUT').upper()
        upload_headers = upload_info.get('headers', {'Content-Type': 'application/octet-stream'})

        with open(file_path, 'rb') as f:
            file_data = f.read()

        upload_resp = requests.request(
            method=upload_method,
            url=upload_url,
            headers=upload_headers,
            data=file_data,
            timeout=120
        )

        if upload_resp.status_code not in (200, 204):
            print(f'❌ 文件上传失败: HTTP {upload_resp.status_code}')
            return None

        # ── 阶段 3: Complete ─────────────────────────────────────────────────────
        complete_url = f'{base_url}/osms/v1/file/manager/completeAndQuery'

        complete_payload = {
            'objectId': object_id,
            'draftId': draft_id,
        }

        complete_resp = requests.post(
            complete_url,
            headers=common_headers,
            json=complete_payload,
            timeout=30
        )

        if complete_resp.status_code != 200:
            print(f'❌ Complete 请求失败: HTTP {complete_resp.status_code}')
            return None

        complete_data = complete_resp.json()

        # 从 completeAndQuery 响应中直接获取文件下载 URL
        file_url = complete_data.get('fileDetailInfo', {}).get('url', '')

        return file_url

    except requests.exceptions.Timeout:
        print('❌ 请求超时')
        return None
    except requests.exceptions.ConnectionError as e:
        print(f'❌ 连接失败: {e}')
        return None
    except Exception as e:
        print(f'❌ 上传异常: {e}')
        import traceback
        traceback.print_exc()
        return None


def _process_image_input(image):
    """处理图片输入：验证并转换本地文件为base64，远程URL保持不变。"""
    def _validate_and_convert(item):
        """验证单个图片项并返回处理后的结果。"""
        if _is_remote_url(item):
            return item
        elif _is_local_file(item):
            return upload_file(item)
        raise ValueError(f"Invalid image input: {item}")

    if isinstance(image, str):
        return _validate_and_convert(image)
    elif isinstance(image, list):
        if not image:
            raise ValueError("Image list cannot be empty.")
        return [_validate_and_convert(item) for item in image]
    raise ValueError("Image input must be a remote URL, local file path, or list of URLs/paths.")


def generate_image(
    prompt, 
    input_image=None,
    size="2K",
    watermark=True,
    max_images=None):
    """Call the Xiaoyi image generation API."""
    
    # Check environment variables
    required_env = ['PERSONAL-API-KEY', 'PERSONAL-UID', 'SERVICE_URL']

    env_dict = read_xiaoyienv()
    SERVICE_URL = env_dict.get('SERVICE_URL', '')
    UID = env_dict.get('PERSONAL-UID', '')
    API_KEY = env_dict.get('PERSONAL-API-KEY', '')

    missing = [k for k in required_env if not env_dict.get(k)]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        sys.exit(1)
    
    # Configuration
    trace_id = str(uuid.uuid4())
    api_url = f'{SERVICE_URL}/celia-claw/v1/sse-api/skill/execute'
    
    # Build request
    headers = {
        'Content-Type': 'application/json',
        'x-skill-id': 'seedream',
        'x-hag-trace-id': trace_id,
        'x-uid': UID,
        'x-api-key': API_KEY,
        'x-request-from': 'openclaw',
    }
    
    # 定义 content 字段，便于后续扩展
    content = {
        "prompt": prompt,
        "size": size,
        "watermark": watermark,
        "response_format": "url"
    }
    
    # 处理图片输入并添加到 content 中
    if input_image is not None:
        input_image = _process_image_input(input_image)
        content["reference_images"] = input_image

    if max_images is not None:
        content["max_images"] = max_images
    
    payload = {
        "actions": [
            {
                "actionExecutorTask": {
                    "actionName": "seedreamBatch5",
                    "content": content,
                    "pluginId": "abf9388fed6b4df89daac71be85fc62c",
                    "replyCard": False
                },
                "actionSn": "81ef5ac1b5e74e85b90832503ea34a07"
            }
        ],
        "endpoint": {
            "countryCode": "",
            "device": {
                "deviceId": "5682d99dbb90973b775b7e9bf774ff9f",
                "phoneType": "2in1",
                "prdVer": "11.6.2.202"
            }
        },
        "session": {
            "interactionId": "0",
            "isNew": True,
            "sessionId": str(uuid.uuid4())
        },
        "utterance": {
            "original": "",
            "type": "text"
        },
        "version": "1.0"
    }
    
    # Call API
    print(f"📡 Generating image...")
    print(f"   Prompt: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
    
    try:
        # Use stream to handle multiple responses (heartbeat packets)
        response = requests.post(api_url, headers=headers, json=payload, timeout=120, verify=False, stream=True)
        response.raise_for_status()
        
        # Read streaming responses until we get the final result
        image_urls = None
        
        print(f"⏳ Waiting for image generation to complete...")
        
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                try:
                    # Parse SSE (Server-Sent Events) format
                    # Skip id: lines and other non-data lines
                    if line_str.startswith('data:'):
                        # Extract JSON data from "data: {...}" line
                        json_str = line_str[5:].strip()  # Remove "data:" prefix
                        result = json.loads(json_str)
                    elif line_str.startswith('id:'):
                        # Skip id lines
                        continue
                    else:
                        # Skip empty or other non-data lines
                        continue
                    
                    # Extract image URL from new JSON structure
                    ability_infos = result.get('abilityInfos', [])
                    if not ability_infos:
                        continue
                    
                    action_executor_result = ability_infos[0].get('actionExecutorResult', {})
                    if action_executor_result.get('code') != '0':
                        error_msg = action_executor_result.get('desc', 'Unknown error')
                        print(f"❌ API Error: {error_msg}")
                        continue
                    
                    reply = action_executor_result.get('reply', {})
                    stream_info = reply.get('streamInfo', {})
                    stream_type = stream_info.get('streamType', '')
                    
                    # Check if this is a heartbeat packet or final response
                    if stream_type == 'final':
                        # This is the final response with image URLs
                        items = reply.get('items', [])
                        if items:
                            image_urls = items
                            print(f"✅ Final response received, {len(image_urls)} image(s) generated")
                            break
                
                except json.JSONDecodeError:
                    # Skip lines that are not valid JSON
                    continue
        
        # Return the image URLs or None if not found
        return image_urls
        
    except requests.exceptions.Timeout:
        print("❌ Error: Request timed out (120s)")
        return None
    except requests.exceptions.RequestException as e:
        print(f"❌ Error: {e}")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def download_image(url, output_dir):
    """Download image and save to file."""
    
    print(f"⬇️  Downloading...")
    
    try:
        response = requests.get(url, timeout=60, verify=False)
        response.raise_for_status()
        
        # Determine extension
        path_lower = url.lower().split('?')[0]
        if path_lower.endswith('.jpg') or path_lower.endswith('.jpeg'):
            ext = '.jpg'
        elif path_lower.endswith('.webp'):
            ext = '.webp'
        elif path_lower.endswith('.gif'):
            ext = '.gif'
        else:
            ext = '.png'
        
        # Generate filename
        now_time = datetime.now()
        ms = now_time.strftime('%f')[:3]
        base_time = now_time.strftime('%Y%m%d_%H%M%S')

        # 2 位随机字符
        random_chars = ''.join(random.choices(string.ascii_letters + string.digits, k=2))

        timestamp = f"{base_time}_{ms}_{random_chars}"
        filename = f"{timestamp}_generated{ext}"
        output_path = Path(output_dir) / filename
        
        # Save
        with open(output_path, 'wb') as f:
            f.write(response.content)
        
        print(f"💾 Saved to: {output_path}")
        return output_path
        
    except Exception as e:
        print(f"❌ Download failed: {e}")
        return None

STYLE_PROMPT_MAP = {
    "CreamInk": "页面背景色 #faf9f7",
    "PlatinumExecutive": "页面背景色 #FFFFFF",
    "WarmPaper": "页面背景色 #f5f3ee"
}


def call_seedream(prompt: str, style: str | None = None, output_dir: Path | None = None) -> list[str]:
    style_prefix = ""
    if style and style in STYLE_PROMPT_MAP:
        style_prefix = STYLE_PROMPT_MAP[style] + "，"
    # Generate images
    image_urls = generate_image(
        prompt=f"生成一张 16:9 的图片，照片会通过蒙版显露，请生成适合蒙版信息的图片，图片里无需包含蒙版背景，蒙版信息：{style_prefix}，图片描述：{prompt}，必须遵守：生成图片不要包含任何文字！",
        size="2K",
        watermark=False
    )
    
    if not image_urls:
        raise RuntimeError("Seedream 未返回图片")
    
    # Determine output directory
    if output_dir is None:
        output_dir = Path('~/.openclaw/workspace/generated-images').expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Download all images
    saved_paths = []
    for i, url in enumerate(image_urls, 1):
        print(f"\n[{i}/{len(image_urls)}] Processing image...")
        saved_path = download_image(url, output_dir)
        if saved_path:
            saved_paths.append(saved_path)
    
    # Check if all downloads succeeded
    if len(saved_paths) < len(image_urls):
        print(f"\n⚠️  Warning: Only {len(saved_paths)} out of {len(image_urls)} images were successfully downloaded")
        return saved_paths
    return saved_paths