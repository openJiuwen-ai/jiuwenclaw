#!/usr/bin/env python3
"""
validate_outline.py - 验证 outline.md 文件格式是否正确

验证流程：
    - 确保文件不为空
    - 验证文件是否是Markdown格式
    - 验证文件是否以所需的 <style>xxx</style> 格式开头

用法：
    python validate_outline.py $PPT_SESSION_DIR/outline.md
"""

import sys
import re
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("validate_outline")

def _setup_logger(log_path: Path) -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.handlers.clear()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)

def validate_outline_format(outline_path: str) -> bool:
    """验证大纲文件格式是否正确"""
    try:
        outline_text = Path(outline_path).read_text(encoding="utf-8")
        outline_text = outline_text.strip()
        
        # 检查是否为空
        if not outline_text:
            logger.error("大纲文件为空")
            return False
            
        # 检查是否为JSON格式（如["xxx"]）
        if outline_text.startswith('[') and outline_text.endswith(']'):
            try:
                parsed = json.loads(outline_text)
                logger.error("大纲文件格式错误：检测到JSON数组格式，应为Markdown格式")
                logger.error("请重新生成大纲，确保以 <style>xxx</style> 开头的Markdown格式")
                return False
            except json.JSONDecodeError:
                pass
                
        # 检查是否为纯JSON对象
        if outline_text.startswith('{') and outline_text.endswith('}'):
            try:
                parsed = json.loads(outline_text)
                logger.error("大纲文件格式错误：检测到JSON对象格式，应为Markdown格式")
                logger.error("请重新生成大纲，确保以 <style>xxx</style> 开头的Markdown格式")
                return False
            except json.JSONDecodeError:
                pass
                
        # 检查是否以 <style> 开头
        if not outline_text.startswith('<style>'):
            logger.error("大纲文件必须以 <style>xxx</style> 开头")
            logger.error("当前文件内容以 '%s' 开头", outline_text[:50])
            return False
            
        # 检查 <style> 标签是否完整
        style_match = re.search(r'<style>(.*?)</style>', outline_text)
        if not style_match:
            logger.error("大纲文件中的 <style> 标签格式不正确")
            return False
            
        style_value = style_match.group(1).strip()
        if not style_value:
            logger.error("大纲文件中的 <style> 标签不能为空")
            return False
            
        logger.info("✅ 大纲文件格式正确，风格为: %s", style_value)
        return True
        
    except Exception as e:
        logger.error("❌ 验证大纲文件时发生错误: %s", e)
        return False

def main():
    if len(sys.argv) != 2:
        logger.error('用法: $PYTHON_CMD ~/.openclaw/workspace/skills/xiaoyi-ppt/scripts/validate_outline.py "$PPT_SESSION_DIR/outline.md"')
        sys.exit(1)
        
    outline_path = sys.argv[1]
    
    # 设置日志输出到指定文件
    ppt_session_id = outline_path.split("/")[-2]
    output_dir = Path("/tmp/xiaoyi_ppt") / ppt_session_id
    output_dir.mkdir(parents=True, exist_ok=True)
    _setup_logger(output_dir / "validate_outline.log")
    
    if not validate_outline_format(outline_path):
        sys.exit(1)

if __name__ == "__main__":
    main()