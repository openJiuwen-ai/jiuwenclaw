#!/usr/bin/env python3
"""
版权所有 (c) 华为技术有限公司 2026-2026

skill-scope 安全扫描脚本

用法：
  python3 check.py <skill_path> <type> <source>

参数：
  skill_path    要扫描的 skill/plugin 目录的绝对路径
  type          类型: upload | download | create
  source        来源 URL 或命令
"""

import json
import logging
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))

# 进程级 hosts 映射：在 urlopen 等网络调用之前 import，导入即生效
import fix_hosts  # noqa: F401  pylint: disable=unused-import

from calculate_hash import calculate_project_hash
from upload_zip import upload_skill_with_size
from config import load_env_config, load_file_config
from constants import (
    API_URL_SUFFIX,
    SCAN_STATUS_ACCEPT, SCAN_STATUS_REJECT, SCAN_STATUS_ERROR
)

# ============ 日志配置 ============
LOG_DIR = '/tmp/logs/skill-scope'
LOG_MAX_FILES = 7


def _setup_logger() -> logging.Logger:
    """配置按日期存储的日志，最多保留7个日志文件"""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    today = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    log_file = os.path.join(LOG_DIR, f'{today}.log')

    logger = logging.getLogger('skill-scope')
    logger.setLevel(logging.DEBUG)

    # 文件 handler
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(fh)

    # stdout handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter('[skill-scope] %(message)s'))
    logger.addHandler(sh)

    # 清理旧日志
    _cleanup_old_logs()

    return logger


def _cleanup_old_logs():
    """删除超过 LOG_MAX_FILES 个的旧日志文件"""
    try:
        log_files = sorted(
            [f for f in os.listdir(LOG_DIR) if f.endswith('.log')],
            reverse=True
        )
        for old_file in log_files[LOG_MAX_FILES:]:
            os.remove(os.path.join(LOG_DIR, old_file))
    except Exception:
        pass


logger = _setup_logger()


# ============ 功能函数 ============

def load_skill_content(skill_md_path: str) -> str:
    """读取 SKILL.md 内容"""
    try:
        with open(skill_md_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ""


def format_req_time() -> str:
    """格式化请求时间：2026-07-09 12:34:56.789+0800"""
    now = datetime.now(timezone(timedelta(hours=8)))
    return now.strftime('%Y-%m-%d %H:%M:%S.') + f'{now.microsecond // 1000:03d}+0800'


def build_skill_scan_payload(
    target_hash: str,
    download_url: str,
    skill_type: str,
    skill_content: str,
    uid: str,
    session_id: str
) -> dict:
    """构建扫描请求体（与原 TS 版本 buildSkillScanPayload 一致）"""
    task_id = str(uuid.uuid4())
    inter_action_id = 1
    business_id = "voiceassistant"
    scene_id = "SKILL_SCAN"
    req_time = format_req_time()
    check_point = 7
    action = "XIAOYI_CLAW"

    available_skill_body_len = 10240
    skill_body = skill_content[:available_skill_body_len] if len(skill_content) > available_skill_body_len else skill_content

    call_obj = {
        "type": "function",
        "name": "install_skill",
        "arguments": "{}",
        "index": 0,
        "id": "0",
        "file": [
            {
                "type": "doc",
                "url": download_url,
                "hash": target_hash,
                "size": len(skill_content),
                "body": skill_body,
                "fromType": skill_type
            }
        ]
    }

    return {
        "taskID": task_id,
        "sessionID": session_id,
        "interActionID": inter_action_id,
        "uid": uid,
        "businessID": business_id,
        "sceneID": scene_id,
        "reqTime": req_time,
        "checkPoint": check_point,
        "action": action,
        "packageName": "com.huawei.hmos.vassistant",
        "ansDone": False,
        "userId": uid,
        "message": {
            "input": {
                "toolIn": [
                    {
                        "toolCalls": [call_obj]
                    }
                ]
            }
        }
    }


def call_skill_scan_api(
    target_hash: str,
    download_url: str,
    skill_type: str,
    skill_content: str,
    session_id: str
) -> str:
    """调用 Celia 安全扫描 API"""
    env_config = load_env_config()
    file_config = load_file_config()

    if not env_config.serviceUrl:
        raise Exception("SERVICE_URL is not configured")

    api_url = env_config.serviceUrl + API_URL_SUFFIX

    headers = {
        'x-hag-trace-id': session_id,
        'sessionId': session_id,
        'x-sandbox-id': env_config.sandbox_id,
        'x-api-key': env_config.apiKey,
        'x-request-from': file_config.requestFrom,
        'x-skill-id': file_config.skillId,
        'x-uid': env_config.uid,
        'Content-Type': 'application/json',
    }

    payload = build_skill_scan_payload(
        target_hash, download_url, skill_type, skill_content, env_config.uid, session_id
    )
    body = json.dumps(payload)

    logger.debug(f'Request headers: {json.dumps(headers)}')
    logger.debug(f'Request body: {body}')

    try:
        req = urllib.request.Request(
            api_url,
            data=body.encode('utf-8'),
            headers=headers,
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=file_config.api_timeout) as response:
            return response.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        err_body = ''
        try:
            err_body = e.read().decode('utf-8')
        except Exception:
            pass
        raise Exception(f"API request failed with status {e.code}, body: {err_body}")
    except Exception as e:
        raise Exception(f"API request failed: {e}")


def parse_security_result(response_text: str) -> dict:
    """解析安全扫描结果"""
    try:
        result = json.loads(response_text)
    except Exception as e:
        raise Exception(f"Failed to parse response: {e}")

    data = result.get('data', {})
    if not isinstance(data, dict):
        raise Exception('Response.data is not an object')

    security_result = data.get('securityResult', '')
    if not security_result:
        raise Exception('Response.data.securityResult is missing')

    security_result = security_result.strip()
    if security_result not in ('ACCEPT', 'REJECT'):
        raise Exception(f'securityResult must be "ACCEPT" or "REJECT". Actual: "{security_result}"')

    return {'status': security_result}


def scan_target(skill_path: str, skill_type: str, source: str) -> dict:
    """对目标目录执行安全扫描"""
    # 校验路径
    if not os.path.exists(skill_path):
        return {
            'status': SCAN_STATUS_ERROR,
            'message': f'Target path does not exist: {skill_path}',
            'target': skill_path
        }

    if not os.path.isdir(skill_path):
        return {
            'status': SCAN_STATUS_ERROR,
            'message': f'Target path is not a directory: {skill_path}',
            'target': skill_path
        }

    try:
        logger.info(f'Starting security scan for: {skill_path}')

        # 1. 计算项目哈希
        target_hash = calculate_project_hash(skill_path)
        logger.info(f'Hash calculated: {target_hash}')

        # 2. 打包上传 zip
        download_url, file_size = upload_skill_with_size(skill_path)
        logger.info(f'Skill uploaded, URL: {download_url}, Size: {file_size} bytes')

        # 3. 读取 SKILL.md 内容
        skill_md_path = os.path.join(skill_path, 'SKILL.md')
        skill_content = load_skill_content(skill_md_path)

        # 4. 调用安全扫描 API
        session_id = secrets.token_hex(16)
        response_text = call_skill_scan_api(
            target_hash, download_url, skill_type, skill_content, session_id
        )

        # 5. 打印 API 原始返回并解析结果
        logger.debug(f'API raw response: {response_text}')
        result = parse_security_result(response_text)

        if result['status'] == SCAN_STATUS_ACCEPT:
            logger.info('Benign: Scan completed, verification passed.')
            return {
                'status': SCAN_STATUS_ACCEPT,
                'message': 'Scan completed, verification passed.',
                'target': skill_path,
                'hash': target_hash
            }
        else:
            logger.warning('Malicious: Malicious Skill detected! This skill poses a serious security threat.')
            return {
                'status': SCAN_STATUS_REJECT,
                'message': 'Malicious Skill detected! This skill poses a serious security threat.',
                'target': skill_path,
                'hash': target_hash
            }

    except Exception as e:
        logger.error(f'Failed to call API: {e}')
        return {
            'status': SCAN_STATUS_ERROR,
            'message': f'Security scan error: {e}',
            'target': skill_path
        }


def main():
    args = sys.argv[1:]

    if len(args) < 3 or '--help' in args or '-h' in args:
        print("""
skill-scope - Security Scanner for OpenClaw Skills/Plugins

Usage:
  python3 check.py <skill_path> <type> <source>

Arguments:
  skill_path    Absolute path to the skill/plugin directory to scan
  type          Type: upload | download | create
  source        Source URL or command

Examples:
  python3 check.py /path/to/my-skill download https://example.com/skill
  python3 check.py /path/to/my-skill create "clawhub install my-skill"
""")
        sys.exit(0)

    skill_path = args[0]
    skill_type = args[1]
    source = args[2]

    result = scan_target(skill_path, skill_type, source)

    # 输出 JSON 结果到 stdout
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # 退出码：0=ACCEPT, 1=REJECT, 3=ERROR
    if result['status'] == SCAN_STATUS_REJECT:
        sys.exit(1)
    elif result['status'] == SCAN_STATUS_ERROR:
        sys.exit(3)


if __name__ == "__main__":
    main()
