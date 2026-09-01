"""
replace_gen_images.py - 解析大纲中的 <image_user_provided> 和 <image_gen_queries> 标签，处理图片并替换为实际图片引用

流程：
  1. 读取 outline_pre.md
  2. 正则提取所有 <image_user_provided> 标签：预处理用户图片，上传到 OSMS，替换为图片引用
  3. 正则提取所有 <image_gen_queries> 标签：调用 Seedream 生成图片，上传到 OSMS，替换为图片引用
  4. 输出替换后的大纲文件和图片 URL JSON

用法：
  python replace_gen_images.py $PPT_SESSION_DIR/outline_pre.md
"""

import argparse
import json
import logging
import os
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

from config import Config
from generate_seedream import call_seedream, download_image
from upload_file import upload_file

logger = logging.getLogger("replace_gen_images")
logger.addHandler(logging.NullHandler())


def _setup_logger(log_path: Path) -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.handlers.clear()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)


GEN_IMAGE_RE = re.compile(r"<image_gen_queries>(.*?)</image_gen_queries>", re.DOTALL)
USER_IMAGE_RE = re.compile(r"<image_user_provided>(.*?)</image_user_provided>", re.DOTALL)
STYLE_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)


def extract_style(outline_text: str) -> str | None:
    m = STYLE_RE.search(outline_text)
    return m.group(1).strip() if m else None


def extract_gen_images(outline_text: str) -> list[tuple[int, str]]:
    matches = []
    for i, m in enumerate(GEN_IMAGE_RE.finditer(outline_text)):
        caption = m.group(1).strip()
        matches.append((i, caption))
    return matches


def extract_user_images(outline_text: str) -> list[tuple[int, str, str]]:
    matches = []
    for i, m in enumerate(USER_IMAGE_RE.finditer(outline_text)):
        content = m.group(1).strip()
        lines = [line.strip().lstrip("- ") for line in content.split("\n") if line.strip()]
        entries = []
        for line in lines:
            if "|" in line:
                path_part, desc_part = line.split("|", 1)
                entries.append((path_part.strip(), desc_part.strip()))
            else:
                entries.append((line.strip(), ""))
        matches.append((i, entries))
    return matches


def get_image_dimensions(image_path: Path) -> tuple[int, int]:
    try:
        with Image.open(image_path) as img:
            return img.size
    except Exception:
        return (1134, 750)


def normalize_user_image(src_path: str, output_dir: Path) -> Path | None:
    try:
        img = Image.open(src_path)
        if img.mode in ('RGBA', 'P', 'LA'):
            img = img.convert('RGB')
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=90)
        basename = Path(src_path).stem + ".jpg"
        dest = output_dir / basename
        # 避免重名
        counter = 1
        while dest.exists():
            basename = f"{Path(src_path).stem}_{counter}.jpg"
            dest = output_dir / basename
            counter += 1
        dest.write_bytes(buf.getvalue())
        logger.info("用户图片已预处理: %s -> %s", src_path, dest.name)
        return dest
    except Exception as e:
        logger.error("用户图片预处理失败: %s, 错误: %s", src_path, e)
        return None


def replace_gen_images(
    outline_path: str,
    output_dir: Path | None = None,
) -> tuple[str, list[str]]:
    if output_dir is None:
        ppt_session_id = outline_path.split("/")[-2]
        output_dir = Path("/tmp/xiaoyi_ppt") / ppt_session_id
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    _setup_logger(output_dir / "replace_gen_images.log")

    outline_text = Path(outline_path).read_text(encoding="utf-8")
    
    style_value = extract_style(outline_text)

    try:
        cfg = Config.load()
    except ValueError as e:
        logger.error("配置错误: %s", e)
        sys.exit(1)

    image_urls_for_attachment = []

    # --- 阶段 1：处理 <image_user_provided> 标签 ---
    user_images = extract_user_images(outline_text)

    if user_images:
        logger.info("发现 %d 个 <image_user_provided> 标签", len(user_images))
        user_caption_map: dict[int, dict | None] = {}

        for idx, entries in user_images:
            processed = False
            for img_path_str, img_desc in entries:
                if not Path(img_path_str).is_file():
                    logger.warning("[%d] 用户图片不存在: %s", idx, img_path_str)
                    continue

                normalized = normalize_user_image(img_path_str, images_dir)
                if normalized is None:
                    continue

                width, height = get_image_dimensions(normalized)
                filename = normalized.name

                try:
                    download_url = upload_file(cfg, str(normalized))
                    image_urls_for_attachment.append({'url': download_url, 'file_name': filename})
                    logger.info("[%d] 用户图片已上传: %s", idx, download_url[:80])
                except Exception as e:
                    logger.error("[%d] 用户图片上传失败: %s", idx, e)
                    image_urls_for_attachment.append({})

                user_caption_map[idx] = {
                    "caption": img_desc or filename,
                    "content": "",
                    "width": str(width),
                    "height": str(height),
                    "filename": filename,
                }
                processed = True
                break

            if not processed:
                user_caption_map[idx] = None
                image_urls_for_attachment.append({})

        user_replace_counter = [0]

        def _user_replacer(m: re.Match) -> str:
            idx_pos = user_replace_counter[0]
            user_replace_counter[0] += 1
            info = user_caption_map.get(idx_pos)
            if info is None:
                return m.group(0)
            alt_json = json.dumps(info, ensure_ascii=False)
            return f"![{alt_json}]({info['filename']})"

        outline_text = USER_IMAGE_RE.sub(_user_replacer, outline_text)
        logger.info("<image_user_provided> 替换完成")

    # --- 阶段 2：处理 <image_gen_queries> 标签 ---
    gen_images = extract_gen_images(outline_text)

    if not gen_images:
        logger.info("大纲中无 <image_gen_queries> 标签，无需处理")
    else:
        caption_map: dict[int, dict] = {}

        def _generate_one(idx: int, caption: str) -> tuple[int, str, list[str] | None]:
            logger.info("[%d] 生成图片 caption=%s", idx, caption[:80])
            try:
                image_urls = call_seedream(caption, style=style_value, output_dir=images_dir)
                if image_urls:
                    return (idx, caption, image_urls)
            except Exception as e:
                logger.error("[%d] Seedream 调用失败: %s", idx, e)
            return (idx, caption, None)

        with ThreadPoolExecutor(max_workers=min(len(gen_images), 25)) as executor:
            futures = {
                executor.submit(_generate_one, idx, caption): idx
                for idx, caption in gen_images
            }
            results: dict[int, tuple[str, list[str] | None]] = {}
            for future in as_completed(futures):
                idx, caption, image_urls = future.result()
                results[idx] = (caption, image_urls)

        failed_indices: set[int] = set()

        def _upload_one(idx: int, caption: str, image_path: Path) -> tuple[int, dict, dict | None]:
            width, height = get_image_dimensions(image_path)
            filename = image_path.name
            attachment = {}
            caption_info = None
            try:
                download_url = upload_file(cfg, str(image_path))
                attachment = {'url': download_url, 'file_name': filename}
                logger.info("[%d] 图片已上传: %s", idx, download_url[:80])
                caption_info = {
                    "caption": caption,
                    "content": "",
                    "width": str(width),
                    "height": str(height),
                    "filename": filename,
                }
            except Exception as e:
                logger.error("[%d] 图片上传失败: %s", idx, e)
            return (idx, attachment, caption_info)

        upload_futures = []
        with ThreadPoolExecutor(max_workers=min(len(gen_images), 25)) as upload_executor:
            for idx in range(len(gen_images)):
                caption, image_urls = results.get(idx, (None, None))

                if image_urls is None or caption is None:
                    logger.warning("[%d] 图片生成失败，保留原始标签", idx)
                    caption_map[idx] = None
                    failed_indices.add(idx)
                    continue

                image_path = image_urls[0]
                future = upload_executor.submit(_upload_one, idx, caption, image_path)
                upload_futures.append(future)

            upload_results: dict[int, tuple[dict | None, dict | None]] = {}
            for future in as_completed(upload_futures):
                idx, attachment, caption_info = future.result()
                upload_results[idx] = (attachment, caption_info)

        for idx in range(len(gen_images)):
            if idx in failed_indices:
                image_urls_for_attachment.append({})
                continue
            attachment, caption_info = upload_results[idx]
            image_urls_for_attachment.append(attachment)
            if caption_info is None:
                caption_map[idx] = None
            else:
                caption_map[idx] = caption_info

        replace_counter = [0]

        def _replacer(m: re.Match) -> str:
            idx_pos = replace_counter[0]
            replace_counter[0] += 1
            info = caption_map.get(idx_pos)
            if info is None:
                return m.group(0)
            alt_json = json.dumps(info, ensure_ascii=False)
            return f"![{alt_json}]({info['filename']})"

        outline_text = GEN_IMAGE_RE.sub(_replacer, outline_text)

    # --- 保存结果 ---
    new_outline = outline_text

    replaced_path = Path(outline_path).with_name("outline.md")
    replaced_path.write_text(new_outline, encoding="utf-8")
    logger.info("替换后大纲已保存: %s", replaced_path)

    urls_path = output_dir / "image_urls.json"
    urls_path.write_text(json.dumps(image_urls_for_attachment, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("图片 URL 列表已保存: %s (%d 张)", urls_path, len(image_urls_for_attachment))

    return new_outline, image_urls_for_attachment


def main():
    parser = argparse.ArgumentParser(description="替换大纲中的 <image_user_provided> 和 <image_gen_queries> 标签为实际图片引用")
    parser.add_argument("outline_file", help="大纲文件路径")
    parser.add_argument("--output-dir", default=None, help="输出目录，默认 $PPT_SESSION_DIR")
    args = parser.parse_args()

    ppt_session_id = args.outline_file.split("/")[-2]
    # todo 规避模型风险
    if ppt_session_id == ".":
        output_dir = os.getcwd()
    else:
        output_dir = Path(args.output_dir) if args.output_dir else Path("/tmp/xiaoyi_ppt") / ppt_session_id

    _, image_urls = replace_gen_images(args.outline_file, output_dir)

    if image_urls:
        print(json.dumps(image_urls, ensure_ascii=False))
    else:
        print("[]")


if __name__ == "__main__":
    main()
