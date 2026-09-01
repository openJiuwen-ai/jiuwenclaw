"""
upload_file.py - 通过 OSMS 三步上传文件（prepare → upload → completeAndQuery）

上传流程：
  1. POST {SERVICE_URL}/osms/v1/file/manager/prepare        获取上传路径
  2. PUT/POST 临时 URL                                       上传文件内容
  3. POST {SERVICE_URL}/osms/v1/file/manager/completeAndQuery 确认并获取下载 URL

用法：
  python upload_file.py <file_path>
  输出上传后的下载 URL 到 stdout。
"""

import hashlib
import logging
import os
import sys
import urllib3
import uuid
from pathlib import Path

import requests

from config import Config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PREPARE_PATH = "/osms/v1/file/manager/prepare"
COMPLETE_AND_QUERY_PATH = "/osms/v1/file/manager/completeAndQuery"

logger = logging.getLogger("upload_file")
logger.addHandler(logging.NullHandler())

def _setup_logger(log_path: Path) -> None:
    """初始化 logger，输出到指定文件。"""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.handlers.clear()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)
# ============================================================
# Upload
# ============================================================

def upload_file(cfg: Config, file_path: str) -> str:
    """
    通过 OSMS 上传文件，返回下载 URL。

    Args:
        cfg: 配置
        file_path: 本地文件路径

    Returns:
        文件下载 URL
    """
    base_url = cfg.service_url.rstrip("/")
    verify = not cfg.osms_tls_skip_verify
    timeout = cfg.request_timeout
    auth = cfg.auth_headers()

    # 读取文件
    file_data = Path(file_path).read_bytes()
    file_name = Path(file_path).name
    file_size = len(file_data)
    file_sha256 = hashlib.sha256(file_data).hexdigest()

    ppt_session_id = os.getenv("PPT_SESSION_ID", "") or str(uuid.uuid4())
    output_dir = Path("/tmp/xiaoyi_ppt") / ppt_session_id
    output_dir.mkdir(parents=True, exist_ok=True)
    _setup_logger(output_dir / "upload_file.log")

    logger.info("上传文件 name=%s size=%d sha256=%s", file_name, file_size, file_sha256)

    # 1. prepare
    prepare_url = base_url + PREPARE_PATH
    prepare_body = {
        "objectType": cfg.osms_object_type,
        "fileName": file_name,
        "fileSha256": file_sha256,
        "fileSize": file_size,
    }

    logger.info("prepare url=%s", prepare_url)
    resp = requests.post(
        prepare_url,
        json=prepare_body,
        headers={"Content-Type": "application/json", **auth},
        timeout=timeout,
        verify=verify,
    )
    resp.raise_for_status()
    prepare_resp = resp.json()

    object_id = prepare_resp.get("objectId", "")
    draft_id = prepare_resp.get("draftId", "")
    upload_infos = prepare_resp.get("uploadInfos", [])

    if not object_id or not draft_id or not upload_infos:
        raise RuntimeError(
            f"prepare 响应数据不完整: objectId={object_id}, draftId={draft_id}, "
            f"uploadInfos={len(upload_infos)}"
        )

    logger.info("prepare 成功 objectId=%s draftId=%s uploadInfoCount=%d",
                object_id, draft_id, len(upload_infos))

    # 2. upload
    upload_info = upload_infos[0]
    upload_url = upload_info["url"]
    upload_method = upload_info.get("method", "PUT").upper()
    upload_headers = {k: v for k, v in upload_info.get("headers", {}).items()
                      if k.lower() != "content-length"}

    logger.info("upload method=%s url=%s", upload_method, upload_url)
    resp = requests.request(
        upload_method,
        upload_url,
        data=file_data,
        headers=upload_headers,
        timeout=timeout,
        verify=verify,
    )
    resp.raise_for_status()

    logger.info("文件上传成功 name=%s", file_name)

    # 3. completeAndQuery
    complete_url = base_url + COMPLETE_AND_QUERY_PATH
    complete_body = {
        "useEdge": cfg.osms_use_edge,
        "objectId": object_id,
        "draftId": draft_id,
    }

    logger.info("completeAndQuery url=%s objectId=%s", complete_url, object_id)
    resp = requests.post(
        complete_url,
        json=complete_body,
        headers={"Content-Type": "application/json", **auth},
        timeout=timeout,
        verify=verify,
    )
    resp.raise_for_status()
    complete_resp = resp.json()

    download_url = complete_resp.get("fileDetailInfo", {}).get("url", "")
    if not download_url:
        raise RuntimeError(
            f"completeAndQuery 响应中缺少下载 URL, objectId={object_id}"
        )

    logger.info("completeAndQuery 成功 objectId=%s url=%s", object_id, download_url)
    return download_url


# ============================================================
# CLI 入口
# ============================================================

def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    file_path = sys.argv[1]
    if not Path(file_path).is_file():
        logger.error("文件不存在: %s", file_path)
        sys.exit(1)

    try:
        cfg = Config.load()
    except ValueError as e:
        logger.error("配置错误: %s", e)
        sys.exit(1)

    try:
        url = upload_file(cfg, file_path)
        print(url)
    except Exception as e:
        logger.error("上传失败: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
