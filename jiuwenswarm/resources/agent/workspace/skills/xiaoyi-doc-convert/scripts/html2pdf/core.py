#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTML 转 PDF 工具 - 基于 Playwright
支持单文件和批量转换
"""

import os
import sys
import urllib
from pathlib import Path
from typing import Optional
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# 导入本地的 PDFMerger
from .pdfmerge import PDFMerger

# 硬编码配置
PAGE_LOAD_TIMEOUT = 10  # 秒
EXTRA_WAIT_TIME = 0     # 毫秒
DEFAULT_PAGE_WIDTH = '21cm'     # A4 宽度
DEFAULT_PAGE_HEIGHT = '29.7cm'  # A4 高度


def get_chrome_executable_path() -> Optional[str]:
    """获取 Chrome 浏览器可执行文件路径

    优先从 CHROME_HOME 环境变量获取，如果未设置或路径不存在则返回 None
    （使用 Playwright 内置浏览器）

    Returns:
        Chrome 浏览器可执行文件路径，或 None
    """
    CHROME_HOME = os.getenv("CHROME_HOME", "/home/sandbox/chrome-linux")
    if not CHROME_HOME:
        return None

    # 根据操作系统判断可执行文件名称
    is_windows = sys.platform == 'win32'
    chrome_exe = 'chrome.exe' if is_windows else 'chrome'

    chrome_path = os.path.join(CHROME_HOME, chrome_exe)

    # 检查文件是否存在
    if os.path.exists(chrome_path):
        return chrome_path

    # 尝试 google-chrome (Linux 常见路径)
    if not is_windows:
        google_chrome = os.path.join(CHROME_HOME, 'google-chrome')
        if os.path.exists(google_chrome):
            return google_chrome

    print(f"[html2pdf] 警告: CHROME_HOME 设置的路径不存在: {chrome_path}")
    return None


def build_pdf_options(page_width: str = None, page_height: str = None) -> dict:
    """
    构建PDF打印选项

    Args:
        page_width: 页面宽度，带单位 (默认 '21cm'，即 A4 宽度)
        page_height: 页面高度，带单位 (默认 '29.7cm'，即 A4 高度)

    Returns:
        dict: PDF打印选项字典
    """
    return {
        'width': page_width or DEFAULT_PAGE_WIDTH,
        'height': page_height or DEFAULT_PAGE_HEIGHT,
        'print_background': True,
        'prefer_css_page_size': True,
        'margin': {
            'top': '0cm',
            'bottom': '0cm',
            'left': '0cm',
            'right': '0cm'
        },
        'scale': 1.0  # 不缩放
    }


def html_to_pdf(html_file_path: str, output_path: str = None,
                page_width: str = None, page_height: str = None) -> dict:
    """
    将HTML文件转换为PDF (基于 Playwright)

    Args:
        html_file_path: HTML文件路径
        output_path: PDF输出路径，默认为同名.pdf文件
        page_width: 页面宽度 (默认 21cm，即 A4 宽度)
        page_height: 页面高度 (默认 29.7cm，即 A4 高度)

    Returns:
        dict: 包含 pdf_path 的字典
    """
    # 检查HTML文件是否存在
    if not os.path.exists(html_file_path):
        raise FileNotFoundError(f"HTML文件不存在: {html_file_path}")

    # 计算默认输出路径
    if not output_path:
        base_path = os.path.splitext(html_file_path)[0]
        output_path = f"{base_path}.pdf"

    print(f"[html2pdf] 开始转换: {os.path.basename(html_file_path)}")

    try:
        # 转换为绝对路径并构建 file:// URL
        abs_path = os.path.abspath(html_file_path)
        # Windows 路径需要特殊处理：C:\path -> /C:/path
        if sys.platform == 'win32':
            abs_path = abs_path.replace('\\', '/')
            if not abs_path.startswith('/'):
                abs_path = '/' + abs_path
        encoded_path = urllib.parse.quote(abs_path, safe='/')
        file_url = f"file://{encoded_path}"

        # 使用 sync_playwright 创建临时浏览器上下文
        with sync_playwright() as p:
            # 获取 Chrome 浏览器路径（如果配置了 CHROME_HOME）
            executable_path = get_chrome_executable_path()

            launch_options = {
                'headless': True,
                'args': [
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process'
                ]
            }

            # 如果指定了浏览器路径，使用它
            if executable_path:
                print(f"[html2pdf] 使用指定 Chrome 路径: {executable_path}")
                launch_options['executable_path'] = executable_path

            # 使用 chromium 浏览器
            browser = p.chromium.launch(**launch_options)
            context = browser.new_context()

            try:
                # 创建新页面
                page = context.new_page()

                # 加载HTML文件
                page.goto(file_url, wait_until='networkidle', timeout=PAGE_LOAD_TIMEOUT * 1000)

                # 等待 body 元素加载完成
                page.wait_for_selector('body', state='attached', timeout=PAGE_LOAD_TIMEOUT * 1000)

                # 额外等待以确保动态内容（如图表）渲染完成
                if EXTRA_WAIT_TIME > 0:
                    page.wait_for_timeout(EXTRA_WAIT_TIME)

                # 生成PDF
                pdf_options = build_pdf_options(page_width, page_height)
                page.pdf(path=output_path, **pdf_options)

                print(f"[html2pdf] 转换完成: {os.path.basename(output_path)}")

                # 关闭页面
                page.close()

            finally:
                # 关闭上下文和浏览器
                context.close()
                browser.close()

        return {"pdf_path": output_path}

    except PlaywrightTimeout as e:
        print(f"[html2pdf] 转换超时: {e}")
        raise TimeoutError(f"HTML转PDF超时: {html_file_path}") from e
    except Exception as e:
        print(f"[html2pdf] 转换失败: {e}")
        raise RuntimeError(f"HTML转PDF失败: {html_file_path}") from e


def batch_convert(html_dir: str, output_dir: str = None,
                  page_width: str = None, page_height: str = None) -> dict:
    """
    批量转换目录下的所有HTML文件，并合并为一个PDF

    Args:
        html_dir: HTML文件所在目录
        output_dir: PDF输出目录，默认为原目录
        page_width: 页面宽度
        page_height: 页面高度

    Returns:
        dict: 包含合并结果的字典
    """
    if not os.path.isdir(html_dir):
        raise NotADirectoryError(f"目录不存在: {html_dir}")

    # 获取所有HTML文件
    html_files = list(Path(html_dir).glob("*.html")) + list(Path(html_dir).glob("*.htm"))

    if not html_files:
        print(f"[html2pdf] 目录中没有HTML文件: {html_dir}")
        return {"error": "没有HTML文件"}

    # 按文件名排序
    html_files.sort()

    print(f"[html2pdf] 发现 {len(html_files)} 个HTML文件，开始批量转换...")

    # 确定输出目录
    out_dir = Path(output_dir) if output_dir else Path(html_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 使用目录名作为输出文件名
    dir_name = Path(html_dir).name
    merged_pdf_path = out_dir / f"{dir_name}.pdf"

    results = []
    pdf_files = []
    success_count = 0
    fail_count = 0

    for html_file in html_files:
        # 临时PDF文件路径（在临时目录中）
        temp_pdf_path = out_dir / f"_{html_file.stem}.pdf"

        try:
            result = html_to_pdf(
                html_file_path=str(html_file),
                output_path=str(temp_pdf_path),
                page_width=page_width,
                page_height=page_height
            )
            results.append(result)
            pdf_files.append(temp_pdf_path)
            success_count += 1
        except Exception as e:
            print(f"[html2pdf] 转换失败 [{html_file.name}]: {e}")
            results.append({"error": str(e), "html_file": str(html_file)})
            fail_count += 1

    print(f"[html2pdf] 批量转换完成: 成功 {success_count} 个, 失败 {fail_count} 个")

    if not pdf_files:
        return {"error": "没有成功转换的PDF文件", "results": results}

    # 合并PDF文件
    print(f"[html2pdf] 开始合并 {len(pdf_files)} 个PDF文件...")
    merger = PDFMerger()
    merge_success = merger.merge_pdfs(pdf_files, merged_pdf_path)

    if merge_success:
        # 删除单个PDF文件
        for pdf_file in pdf_files:
            try:
                os.remove(pdf_file)
            except Exception as e:
                print(f"[html2pdf] 警告: 删除临时文件失败 {pdf_file}: {e}")

        print(f"[html2pdf] 合并完成: {merged_pdf_path}")
        return {
            "merged_pdf": str(merged_pdf_path),
            "count": len(pdf_files),
            "total_html": len(html_files),
            "success": success_count,
            "failed": fail_count
        }
    else:
        print(f"[html2pdf] 合并失败，保留单个PDF文件")
        return {
            "error": "PDF合并失败",
            "individual_pdfs": [str(p) for p in pdf_files],
            "results": results
        }
