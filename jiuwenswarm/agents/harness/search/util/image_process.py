import base64
import io
from pathlib import Path

from PIL import Image


def image_url_to_base64(image_url: str) -> str:
    path = Path(image_url)
    if not path.exists():
        raise FileNotFoundError(f"图片文件不存在: {image_url}")
    if not path.is_file():
        raise ValueError(f"路径不是文件: {image_url}")
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:image/{path.suffix.lstrip('.')};base64,{encoded}"


def resize_base64_image(img_base64: str, max_long_side: int = 1024) -> str:
    """将base64编码的图片解码为原图，按最长边缩放后再重新编码为base64。

    Args:
        img_base64: 原始图片的base64编码字符串，支持带data URI前缀。
        max_long_side: 缩放后最长边的目标像素大小，默认1024。

    Returns:
        缩放后图片的base64编码字符串，保留原始的data URI前缀格式。
    """
    raw = img_base64
    prefix = ""
    if raw.startswith("data:"):
        prefix_end = raw.index(",") + 1
        prefix = raw[:prefix_end]
        raw = raw[prefix_end:]

    img_bytes = base64.b64decode(raw)
    img = Image.open(io.BytesIO(img_bytes))

    width, height = img.size
    long_side = max(width, height)
    if long_side <= max_long_side:
        return img_base64

    scale = max_long_side / long_side
    new_width = int(width * scale)
    new_height = int(height * scale)
    img = img.resize((new_width, new_height), Image.LANCZOS)

    buffer = io.BytesIO()
    fmt = img.format or "PNG"
    if fmt.upper() == "JPEG":
        img.save(buffer, format="JPEG", quality=95)
    else:
        img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")

    if prefix:
        return f"{prefix}{encoded}"
    return f"data:image/{fmt.lower()};base64,{encoded}"
