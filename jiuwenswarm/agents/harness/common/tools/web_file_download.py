# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Web File Download Token Manager

提供基于 HMAC 签名的文件下载令牌生成与验证，支持跨进程（AgentServer / Gateway / app_web.py）
无需共享内存即可安全校验。

协议：
- 令牌格式: Base64URL(payload_json) + "." + Hex(HMAC-SHA256)
- 普通下载 payload: path, exp, sid
- Skill 正文图片 payload: purpose=skill_content_image, name, version, relative_path, exp, sid（无 path）
- 密钥来源: 环境变量 JIUWENSWARM_FILE_DOWNLOAD_SECRET 或自动生成并写入共享文件
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_EXPIRES_SECONDS = 600
_SECRET_ENV_KEY = "JIUWENSWARM_FILE_DOWNLOAD_SECRET"
_SECRET_FILE_NAME = ".file_download_secret"

PURPOSE_SKILL_CONTENT_IMAGE = "skill_content_image"


def _get_secret_file_path() -> Path:
    workspace = os.getenv("JIUWENSWARM_WORKSPACE")
    if workspace:
        return Path(workspace) / "config" / _SECRET_FILE_NAME
    return Path.home() / ".jiuwenswarm" / "config" / _SECRET_FILE_NAME


def _load_or_create_secret() -> str:
    secret = os.getenv(_SECRET_ENV_KEY)
    if secret and len(secret) >= 32:
        return secret

    secret_file = _get_secret_file_path()
    try:
        if secret_file.exists():
            existing = secret_file.read_text(encoding="utf-8").strip()
            if existing and len(existing) >= 32:
                return existing
    except Exception:
        logger.debug("[WebFileDownload] 读取密钥文件失败，将重新生成")

    new_secret = secrets.token_hex(32)
    try:
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(new_secret, encoding="utf-8")
        os.chmod(secret_file, 0o600)
    except Exception:
        logger.warning("[WebFileDownload] 写入密钥文件失败，使用内存密钥（重启后失效）")

    return new_secret


class WebFileDownloadManager:
    """管理 Web 端文件下载令牌的生成与验证。

    使用 HMAC-SHA256 签名保证令牌不可伪造，
    密钥通过环境变量或共享文件在 AgentServer / Gateway / app_web.py 间共享。
    """

    _instance: WebFileDownloadManager | None = None

    def __init__(self, secret: str | None = None) -> None:
        self._secret = secret or _load_or_create_secret()

    @classmethod
    def get_instance(cls) -> WebFileDownloadManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def _sign_payload(self, payload: dict[str, Any]) -> str:
        payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        payload_b64 = base64.urlsafe_b64encode(
            payload_json.encode("utf-8")
        ).decode("ascii")
        signature = hmac.new(
            self._secret.encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return f"{payload_b64}.{signature}"

    def generate_token(
        self,
        file_path: str,
        session_id: str = "",
        expires_in: int = _DEFAULT_EXPIRES_SECONDS,
    ) -> str:
        payload = {
            "path": file_path,
            "exp": int(time.time()) + expires_in,
            "sid": session_id,
        }
        return self._sign_payload(payload)

    def generate_skill_content_image_token(
        self,
        *,
        name: str,
        version: str | None,
        relative_path: str,
        session_id: str,
        expires_in: int = _DEFAULT_EXPIRES_SECONDS,
    ) -> str:
        """签发正文图片预览 token（不携带服务端绝对路径）."""
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("skill_content_image token 需要非空 session_id")
        skill_name = str(name or "").strip()
        rel = str(relative_path or "").strip().replace("\\", "/")
        if not skill_name or not rel:
            raise ValueError("skill_content_image token 需要 name 与 relative_path")
        payload: dict[str, Any] = {
            "purpose": PURPOSE_SKILL_CONTENT_IMAGE,
            "name": skill_name,
            "version": version if version is None else str(version).strip() or None,
            "relative_path": rel,
            "exp": int(time.time()) + expires_in,
            "sid": sid,
        }
        return self._sign_payload(payload)

    def validate_token(
        self,
        token: str,
        *,
        session_id: str | None = None,
        check_expiry: bool = True,
    ) -> dict[str, Any] | None:
        """校验下载令牌。

        Args:
            token: HMAC 签名令牌。
            session_id: 若提供非空字符串，则要求 payload.sid 与之相等。
            check_expiry: 是否校验 ``exp`` 过期时间（默认开启）。
        """
        try:
            parts = token.split(".")
            if len(parts) != 2:
                return None
            payload_b64, signature = parts
            expected_sig = hmac.new(
                self._secret.encode("utf-8"),
                payload_b64.encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(signature, expected_sig):
                logger.warning("[WebFileDownload] 令牌签名校验失败")
                return None
            payload_json = base64.urlsafe_b64decode(payload_b64).decode("utf-8")
            payload = json.loads(payload_json)
            if not isinstance(payload, dict):
                return None
            if check_expiry:
                # 兼容历史令牌：无 exp 字段时不强制过期；有 exp 则严格校验
                exp = payload.get("exp")
                if exp is not None and (
                    not isinstance(exp, (int, float)) or int(exp) < int(time.time())
                ):
                    logger.warning("[WebFileDownload] 令牌已过期")
                    return None
            if session_id is not None and str(session_id).strip():
                token_sid = str(payload.get("sid") or "").strip()
                if token_sid != str(session_id).strip():
                    logger.warning("[WebFileDownload] 令牌会话不匹配")
                    return None
            return payload
        except Exception:
            logger.debug("[WebFileDownload] 令牌解析异常", exc_info=True)
            return None

    @staticmethod
    def generate_download_url(token: str) -> str:
        return f"/file-api/download?token={token}"


def generate_file_download_token(
    file_path: str,
    session_id: str = "",
    expires_in: int = _DEFAULT_EXPIRES_SECONDS,
) -> str:
    return WebFileDownloadManager.get_instance().generate_token(
        file_path, session_id, expires_in
    )


def generate_skill_content_image_token(
    *,
    name: str,
    version: str | None,
    relative_path: str,
    session_id: str,
    expires_in: int = _DEFAULT_EXPIRES_SECONDS,
) -> str:
    return WebFileDownloadManager.get_instance().generate_skill_content_image_token(
        name=name,
        version=version,
        relative_path=relative_path,
        session_id=session_id,
        expires_in=expires_in,
    )


def validate_file_download_token(
    token: str,
    *,
    session_id: str | None = None,
    check_expiry: bool = True,
) -> dict[str, Any] | None:
    return WebFileDownloadManager.get_instance().validate_token(
        token,
        session_id=session_id,
        check_expiry=check_expiry,
    )


def build_file_download_info(
    file_path: str,
    file_name: str,
    session_id: str = "",
    expires_in: int = _DEFAULT_EXPIRES_SECONDS,
) -> dict[str, Any]:
    token = generate_file_download_token(file_path, session_id, expires_in)
    download_url = WebFileDownloadManager.get_instance().generate_download_url(token)

    file_size = 0
    mime_type = "application/octet-stream"
    try:
        file_size = os.path.getsize(file_path)
    except OSError:
        pass

    import mimetypes

    guessed_type, _ = mimetypes.guess_type(file_name)
    if guessed_type:
        mime_type = guessed_type

    return {
        "name": file_name,
        "size": file_size,
        "mime_type": mime_type,
        "download_url": download_url,
        "download_token": token,
    }
