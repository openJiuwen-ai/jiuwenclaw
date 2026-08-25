"""
版权所有 (c) 华为技术有限公司 2026-2026

打包 skill 目录为 zip 并通过 OSMS 三步上传到 OBS
"""

import hashlib
import json
import os
import shutil
import ssl
import tempfile
import urllib.request
import urllib.error
import zipfile

from config import load_env_config
from constants import (
    MAX_TIMES, CONNECT_TIMEOUT, READ_TIMEOUT, EXPIRE_TIME,
    OSMS_PREPARE_URL_SUFFIX, OSMS_COMPLETE_URL_SUFFIX,
    TEMPORARY_MATERIAL_PACKAGE, FILE_OWNER_UID, FILE_OWNER_TEAM_ID
)


def _build_osms_headers(env_config, trace_id: str) -> dict:
    return {
        'Content-Type': 'application/json',
        'x-request-from': 'openclaw',
        'x-uid': env_config.uid,
        'x-api-key': env_config.apiKey,
        'x-uid': env_config.uid,
        'x-hag-trace-id': trace_id
    }


def _http_post_json(url: str, headers: dict, body: dict, timeout: int) -> dict:
    """发送 JSON POST 请求并返回解析后的响应"""
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            resp_data = response.read().decode('utf-8')
            return json.loads(resp_data)
    except urllib.error.HTTPError as e:
        body_text = ''
        try:
            body_text = e.read().decode('utf-8')
        except Exception:
            pass
        raise Exception(f'HTTP error! status: {e.code}, body: {body_text}')
    except Exception as e:
        raise Exception(f'Request failed: {e}')


def _invoking_osms_prepare(file_path: str, env_config, file_sha256: str, file_size: int, session_id: str) -> dict:
    """调用 OSMS Prepare 接口"""
    headers = _build_osms_headers(env_config, session_id)
    file_name = os.path.basename(file_path)

    body = {
        'useEdge': False,
        'objectType': TEMPORARY_MATERIAL_PACKAGE,
        'fileName': file_name,
        'fileSha256': file_sha256,
        'fileSize': file_size,
        'fileOwnerInfo': {
            'uid': FILE_OWNER_UID,
            'teamId': FILE_OWNER_TEAM_ID
        }
    }

    prepare_url = env_config.serviceUrl + OSMS_PREPARE_URL_SUFFIX

    for times in range(MAX_TIMES):
        try:
            print(f'[skill-scope] Calling OSMS prepare API (attempt {times + 1}/{MAX_TIMES})')
            resp = _http_post_json(prepare_url, headers, body, CONNECT_TIMEOUT)

            if not resp.get('objectId') or not resp.get('draftId') or not resp.get('uploadInfos'):
                raise Exception('The hag osms prepare interface returns an exception')

            upload_infos = resp['uploadInfos']
            if not upload_infos or len(upload_infos) == 0:
                raise Exception('The hag osms prepare interface uploadInfos returns is empty')

            upload_info = upload_infos[0]
            if not upload_info.get('url') or not upload_info.get('headers'):
                raise Exception('The hag osms prepare interface url and headers for uploadInfos map returns is empty')

            return resp
        except Exception as e:
            print(f'[skill-scope] OSMS prepare attempt {times + 1} failed: {e}')
            if times == MAX_TIMES - 1:
                raise

    raise Exception('Failed to invoke OSMS prepare interface after max retries')


def _upload_to_obs(upload_url: str, upload_headers: dict, file_bytes: bytes) -> None:
    """上传文件到 OBS"""
    retry_time = 1

    while True:
        try:
            print(f'[skill-scope] Uploading file to OBS (attempt {retry_time}/{MAX_TIMES})')
            req = urllib.request.Request(upload_url, data=file_bytes, method='PUT')
            for k, v in upload_headers.items():
                req.add_header(k, v)
            req.add_header('content-length', str(len(file_bytes)))

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            with urllib.request.urlopen(req, timeout=READ_TIMEOUT, context=ctx) as response:
                pass

            print('[skill-scope] File uploaded to OBS successfully')
            return
        except Exception as e:
            retry_time += 1
            if retry_time > MAX_TIMES:
                raise Exception(f'Upload file to obs failed: {e}')
            import time
            delay = (2 ** retry_time)
            print(f'[skill-scope] OBS upload attempt {retry_time - 1} failed, retrying in {delay}s...')


def _invoking_osms_complete(object_id: str, draft_id: str, env_config, session_id: str) -> dict:
    """调用 OSMS Complete 接口"""
    headers = _build_osms_headers(env_config, session_id)

    body = {
        'objectId': object_id,
        'draftId': draft_id,
        'expireTime': EXPIRE_TIME
    }

    complete_url = env_config.serviceUrl + OSMS_COMPLETE_URL_SUFFIX

    for times in range(MAX_TIMES):
        try:
            print(f'[skill-scope] Calling OSMS complete API (attempt {times + 1}/{MAX_TIMES})')
            return _http_post_json(complete_url, headers, body, CONNECT_TIMEOUT)
        except Exception as e:
            print(f'[skill-scope] OSMS complete attempt {times + 1} failed: {e}')
            if times == MAX_TIMES - 1:
                raise

    raise Exception('Failed to invoke OSMS complete interface after max retries')


def _create_zip(source_dir: str, zip_path: str) -> None:
    """将目录打包为 zip"""
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, source_dir)
                zf.write(file_path, arcname)


def _calculate_file_sha256_bytes(file_path: str) -> str:
    """计算文件的 SHA256（二进制模式，用于 zip 文件）"""
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def upload_skill_with_size(skill_path: str) -> tuple:
    """
    打包 skill 目录为 zip，上传到 OBS，返回 (download_url, file_size)
    """
    env_config = load_env_config()

    # 创建临时 zip
    temp_dir = tempfile.mkdtemp(prefix='skill-scope-')
    skill_name = os.path.basename(os.path.normpath(skill_path))
    zip_path = os.path.join(temp_dir, f'{skill_name}.zip')

    try:
        print(f'[skill-scope] Creating zip archive for {skill_path}')
        _create_zip(skill_path, zip_path)
        print(f'[skill-scope] Zip created: {zip_path}')

        file_size = os.path.getsize(zip_path)
        file_hash = _calculate_file_sha256_bytes(zip_path)

        import secrets
        session_id = secrets.token_hex(16)

        # OSMS 三步上传
        prepare_response = _invoking_osms_prepare(zip_path, env_config, file_hash, file_size, session_id)

        with open(zip_path, 'rb') as f:
            file_bytes = f.read()

        upload_info = prepare_response['uploadInfos'][0]
        _upload_to_obs(upload_info['url'], upload_info['headers'], file_bytes)

        complete_response = _invoking_osms_complete(
            prepare_response['objectId'],
            prepare_response['draftId'],
            env_config,
            session_id
        )

        file_detail_info = complete_response.get('fileDetailInfo', {})
        download_url = file_detail_info.get('url', '') if file_detail_info else ''

        if not download_url:
            raise Exception('Failed to get download URL from complete response')

        print(f'[skill-scope] Zip uploaded, URL: {download_url}')
        return (download_url, file_size)

    finally:
        # 清理临时文件
        try:
            if os.path.exists(zip_path):
                os.unlink(zip_path)
            os.rmdir(temp_dir)
        except Exception:
            pass
