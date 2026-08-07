"""Shared WeChat QR-login delivery helpers for IM channels."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WechatLoginQrDelivery:
    """A channel-friendly snapshot of the current WeChat login QR."""

    phase: str
    text: str
    qr_value: str
    image_path: str | None = None


def build_wechat_login_qr_delivery(state: dict[str, Any]) -> WechatLoginQrDelivery | None:
    """Build a transport-neutral delivery from one scoped login snapshot."""
    login = state if isinstance(state, dict) else {}
    phase = str(login.get("phase") or "").strip()
    qr = login.get("qr") if isinstance(login.get("qr"), dict) else None

    if phase == "success":
        return WechatLoginQrDelivery(
            phase=phase,
            text="微信已绑定成功，可以直接通过微信发送消息了。",
            qr_value="",
        )
    if phase == "error":
        error = str(login.get("error") or "微信二维码登录失败，请重新生成二维码后再试。").strip()
        return WechatLoginQrDelivery(phase=phase, text=f"微信绑定失败：{error}", qr_value="")
    if phase not in {"awaiting_scan", "scanned"} or not qr:
        return None

    qr_value = str(qr.get("value") or "").strip()
    if not qr_value:
        return None

    if phase == "scanned":
        text = "已检测到微信扫码，请在手机微信里确认登录。"
    else:
        text = (
            "请使用微信扫描当前二维码并在手机上确认登录。\n"
            f"如果当前频道看不到图片，请打开这个链接查看二维码：{qr_value}"
        )

    image_path = _write_qr_image(qr)
    return WechatLoginQrDelivery(
        phase=phase,
        text=text,
        qr_value=qr_value,
        image_path=image_path,
    )


def wechat_delivery_payload(delivery: WechatLoginQrDelivery) -> dict[str, Any]:
    artifacts: list[dict[str, str]] = []
    if delivery.image_path:
        artifacts.append({"kind": "image", "path": delivery.image_path})
    if delivery.qr_value:
        artifacts.append({"kind": "link", "url": delivery.qr_value, "label": "打开微信二维码"})
    return {
        "text": delivery.text,
        "artifacts": artifacts,
        "source": "wechat_login",
        "phase": delivery.phase,
    }


def _write_qr_image(qr: dict[str, Any]) -> str | None:
    kind = str(qr.get("kind") or "").strip()
    value = str(qr.get("value") or "").strip()
    if not value:
        return None

    out_dir = Path(
        os.getenv("JIUWENSWARM_WECHAT_QR_DIR")
        or os.path.join(tempfile.gettempdir(), "jiuwenswarm-wechat-qr")
    )
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Wechat QR image dir is not writable: %s error=%s", out_dir, exc)
        return None

    digest = hashlib.sha256(f"{kind}:{value}".encode("utf-8")).hexdigest()[:16]
    suffix = ".png"
    path = out_dir / f"wechat-login-{digest}{suffix}"

    if kind == "data_url":
        raw = _decode_data_url(value)
        if not raw:
            return None
        try:
            path.write_bytes(raw)
            return str(path)
        except OSError as exc:
            logger.warning("Wechat QR data_url write failed: %s error=%s", path, exc)
            return None

    if kind in {"encode", "text", "url"}:
        try:
            import qrcode

            image = qrcode.make(value)
            image.save(path)
            return str(path)
        except Exception as exc:
            logger.warning("Wechat QR image generation failed: kind=%s error=%s", kind, exc)
            return None

    return None


def _decode_data_url(value: str) -> bytes | None:
    if not value.startswith("data:image") or "," not in value:
        return None
    payload = value.split(",", 1)[1].strip()
    if not payload:
        return None
    try:
        return base64.b64decode(payload, validate=False)
    except ValueError:
        return None
