"""
generate_ppt.py - 通过 OSMS {SERVICE_URL}/celia-claw/v1/sse-api/skill/execute SSE 接口生成 PPT

用法：
  python generate_ppt.py <query>
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from posixpath import basename as url_basename
from typing import Optional
from typing import Tuple

import requests
import urllib3

from config import Config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SKILL_EXECUTE_PATH = "/celia-claw/v1/sse-api/skill/execute"
SKILL_ID = "xiaoyi_office"

OFFICE_EXTENSIONS = {
    ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls",
    ".pdf", ".pptm", ".docm", ".xlsm",
}

logger = logging.getLogger("generate_ppt")
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
# Event Handlers
# ============================================================

def handle_step_info(data: dict):
    content = data.get("content", "").rstrip("\n")
    if content:
        logger.info("[进度] %s", content)


def handle_data(data: dict) -> str:
    reasoning = data.get("reasoning_content", "")
    if reasoning:
        logger.debug("[思考] %s", reasoning)
    content = data.get("content", "")
    if content:
        logger.debug("[data] %s", content)
    return content


OFFICE_EXTENSIONS = {
    ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls",
    ".pdf", ".pptm", ".docm", ".xlsm",
}


def handle_generate_doc(data: dict, output_dir: Path) -> Optional[str]:
    doc = data.get("doc", {})
    name = doc.get("name") or doc.get("path", "")
    url = doc.get("url", "")
    if not url:
        return None
    logger.info("[生成文件] %s: %s", name, url)

    # 仅下载 Office 格式的文档，跳过 zip 等中间文件
    suffix = Path(name).suffix.lower() if name else ""
    if suffix and suffix not in OFFICE_EXTENSIONS:
        logger.info("[跳过下载] %s (非Office格式: %s)", name, suffix)
        return None

    # 下载文件到 output_dir
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        local_path = output_dir / (name or "output")
        resp = requests.get(url, timeout=300, verify=False)
        resp.raise_for_status()
        local_path.write_bytes(resp.content)
        abs_path = str(local_path.resolve())
        logger.info("[文件已保存] %s -> %s", name, abs_path)
        return abs_path
    except Exception as e:
        logger.error("[下载失败] %s: %s", name, e)
        return None


def handle_tool(data: dict):
    tool_name = data.get("tool_name", "")
    if tool_name:
        logger.info("[调用工具] %s", tool_name)


def handle_tool_result(data: dict):
    tool_name = data.get("tool_name", "")
    result = str(data.get("tool_result", ""))
    if len(result) > 200:
        result = result[:200] + "..."
    if tool_name:
        logger.info("[%s 结果] %s", tool_name, result)


def handle_command(data: dict):
    content_str = data.get("content", "")
    try:
        content = json.loads(content_str) if isinstance(content_str, str) else content_str
    except json.JSONDecodeError:
        return
    steps = content.get("payload", {}).get("cardParams", {}).get("taskStepInfo", [])
    running = [s["stepName"] for s in steps if s.get("stepStatus") == "running"]
    if running:
        logger.info("[进行中] %s", ", ".join(running))


def handle_finish(data: dict):
    finish_type = data.get("type", "normal")
    # logger.info("[完成] type=%s", finish_type)


def _heartbeat_worker(stop_event: threading.Event, interval: int = 15) -> None:
    start = time.monotonic()
    tick = 0
    while not stop_event.wait(interval):
        tick += 1
        elapsed = int(time.monotonic() - start)
        logger.info("[等待中] 已耗时 %dm%02ds，云端仍在处理…", elapsed // 60, elapsed % 60)


# ============================================================
# SSE parsing
# ============================================================

def process_event(event_data: str, response_text: list[str], output_dir: Path) -> bool:
    """处理一个 SSE 事件的 data 内容。返回 True 表示收到 finish 应终止。"""
    try:
        data = json.loads(event_data)
    except json.JSONDecodeError:
        logger.warning("无法解析: %s", event_data[:200])
        return False

    event_type = data.get("event", "")

    if event_type == "stepInfo":
        handle_step_info(data)
    elif event_type == "data":
        text = handle_data(data)
        if text:
            response_text.append(text)
    elif event_type == "generateDoc":
        handle_generate_doc(data, output_dir)
    elif event_type == "tool":
        handle_tool(data)
    elif event_type == "tool_result":
        handle_tool_result(data)
    elif event_type == "command":
        handle_command(data)
    elif event_type == "heartbeat":
        pass
    elif event_type == "finish":
        handle_finish(data)
        return True

    return False


# ============================================================
# Core: build request + streaming SSE response
# ============================================================

def build_request(
    cfg: Config,
    session_id: str,
    interaction_id: str,
    query: str,
    attachments: list[dict] = None,
) -> dict:
    timestamp = str(int(time.time() * 1000)) # 获取毫秒级时间戳
    sn = f"{session_id}_{interaction_id}_{timestamp}"
    req = {
        "sn": sn,
        "sessionid": sn,
        "interactionid": interaction_id,
        "query": query,
        "from": cfg.request_from,
        "device": cfg.device.to_dict(trace_id=sn),
        "client_context": {"appExtraInfo": {"customAgent": {"name": "acp_ppt_with_outline"}}},
    }

    if attachments:
        req["attachment"] = attachments

    if cfg.personal_uid:
        req["user"] = {"uid": cfg.personal_uid}

    if cfg.request_from:
        req["agentInfo"] = {"agentId": cfg.request_from}

    return req


def get_session_info(file_path: str = None) -> Tuple[str, str]:
    """
    获取 Session ID 和 Interaction ID。
    优先从文件读取，如果读取失败或字段缺失，则使用默认值生成逻辑。

    参数:
        file_path: 配置文件路径，默认为 ~/.openclaw/.xiaoyiruntime

    返回:
        Tuple[str, str]: (session_id, interaction_id)
    """

    # 1. 确定文件路径
    if file_path is None:
        file_path = os.path.expanduser("~/.openclaw/.xiaoyiruntime")

    session_id = ""
    interaction_id = ""

    # 2. 尝试从文件读取
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TASK_ID="):
                        # 截取等号后面的内容
                        raw_task_id = line.split("=", 1)[1]

                        # 根据 & 符号切分
                        parts = raw_task_id.split("&")

                        # 获取第一部分 (Session ID)
                        if len(parts) > 0:
                            session_id = parts[0]

                        # 获取第二部分 (Interaction ID)
                        if len(parts) > 1:
                            interaction_id = parts[1]
                        break
        except Exception:
            # 读取出错则忽略，走下面的默认逻辑
            pass

    # 3. 处理默认值和回退逻辑

    # 处理 SESSION_ID: 如果为空，生成 UUID
    if not session_id:
        session_id = str(uuid.uuid4())

    # 处理 INTERACTION_ID: 如果为空，默认1
    if not interaction_id:
        interaction_id = "1"

    return session_id, interaction_id


def prompt(cfg: Config, query: str, ppt_session_id:str, attachments: list[dict] = None) -> str:
    """
    发送 prompt 到 OSMS skill/execute SSE 接口，返回完整的响应文本。

    Args:
        cfg: 配置
        query: 用户查询文本
        attachments: 可选的附件列表

    Returns:
        完整的响应文本
    """
    session_id, interaction_id = get_session_info()
    output_dir = Path("/tmp/xiaoyi_ppt") / ppt_session_id
    output_dir.mkdir(parents=True, exist_ok=True)
    _setup_logger(output_dir / "generate.log")
    req_body = build_request(cfg, session_id, interaction_id, query, attachments)

    headers = {
        "Content-Type": "application/json",
        "x-skill-id": SKILL_ID,
        "x-hag-trace-id": req_body["sn"],
        **cfg.auth_headers(),
    }

    url = cfg.service_url.rstrip("/") + SKILL_EXECUTE_PATH
    response_text = []

    logger.info("发送请求 url=%s sn=%s", url, req_body["sn"])

    _stop_heartbeat = threading.Event()
    _heartbeat = threading.Thread(
        target=_heartbeat_worker, args=(_stop_heartbeat, 15), daemon=True
    )
    _heartbeat.start()

    try:
        with requests.post(
            url,
            json=req_body,
            headers=headers,
            stream=True,
            timeout=cfg.request_timeout,
            verify=not cfg.tls_skip_verify,
        ) as resp:
            if resp.status_code != 200:
                error_body = resp.text
                logger.error("HTTP %d: %s", resp.status_code, error_body)
                raise RuntimeError(f"HTTP {resp.status_code}: {error_body}")

            pending_data = ""
            has_pending = False

            for raw_line in resp.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line = raw_line.strip()

                if not line:
                    if has_pending and pending_data:
                        if process_event(pending_data, response_text, output_dir):
                            break
                    pending_data = ""
                    has_pending = False
                    continue

                if line.startswith("data:"):
                    data_part = line[5:].strip()
                    if has_pending:
                        pending_data += "\n" + data_part
                    else:
                        pending_data = data_part
                        has_pending = True

            if has_pending and pending_data:
                process_event(pending_data, response_text, output_dir)

    except requests.exceptions.RequestException as e:
        logger.error("[ERROR] 请求失败: %s", e)
        raise
    finally:
        _stop_heartbeat.set()
        _heartbeat.join(timeout=1)

    logger.info("[DONE] output_dir=%s", output_dir)
    return "".join(response_text)


# ============================================================
# CLI 入口
# ============================================================

def parse_files_to_attachments(files_json: str) -> list[dict]:
    """将 --files JSON 数组转换为 attachment 列表"""
    urls = json.loads(files_json)
    attachments = []
    for url in urls:
        file_name = url_basename(url.split("?")[0]) or "file"
        attachments.append({
            "fileId": str(uuid.uuid4()),
            "fileName": file_name,
            "fileDownloadUrl": url,
        })
    return attachments


def parse_image_urls_to_attachments(image_urls_json_path: str) -> list[dict]:
    """从 --image-urls-file 指定的 JSON 文件中读取图片 URL 列表，转换为 attachment 列表"""
    path = Path(image_urls_json_path)
    if not path.is_file():
        return []
    urls = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(urls, list):
        return []
    attachments = []
    for i, info in enumerate(urls):
        if not info or 'url' not in info or 'file_name' not in info:
            continue
        file_name = info['file_name']
        attachments.append({
            "fileId": str(uuid.uuid4()),
            "fileName": file_name,
            "fileDownloadUrl": info['url'],
        })
    return attachments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="用户查询文本")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--files", default=None, help='附件 URL JSON 数组，如 \'["http://host/a.md"]\'')
    group.add_argument("--outline-file", default=None, metavar="PATH",
                       help="大纲本地文件路径，脚本内部自动上传并获取 URL")
    parser.add_argument("--image-urls-file", default=None, metavar="PATH",
                        help="图片 URL 列表 JSON 文件路径，图片将作为 attachment 传入")
    args = parser.parse_args()

    try:
        cfg = Config.load()
    except ValueError as e:
        logger.error("配置错误: %s", e)
        sys.exit(1)

    attachments = []
    ppt_session_id = os.getenv("PPT_SESSION_ID", "") or str(uuid.uuid4())
    if args.outline_file:
        ppt_session_id = args.outline_file.split("/")[-2]
        try:
            from upload_file import upload_file as do_upload
            outline_url = do_upload(cfg, args.outline_file)
            attachments.extend(parse_files_to_attachments(json.dumps([outline_url])))
        except Exception as e:
            logger.error("大纲文件上传失败: %s", e)
            sys.exit(1)
    elif args.files:
        try:
            attachments.extend(parse_files_to_attachments(args.files))
        except (json.JSONDecodeError, TypeError) as e:
            logger.error("--files 参数解析失败: %s", e)
            sys.exit(1)

    if args.image_urls_file:
        try:
            image_attachments = parse_image_urls_to_attachments(args.image_urls_file)
            attachments.extend(image_attachments)
            logger.info("已添加 %d 张图片到 attachment", len(image_attachments))
        except Exception as e:
            logger.error("--image-urls-file 解析失败: %s", e)

    try:
        prompt(cfg, args.query,ppt_session_id, attachments if attachments else None)
    except Exception as e:
        logger.error("[ERROR] %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
