#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF 合并工具 - 基于 pypdf
从 src/pdfmerge/pdfmerge.py 抽取，供 skill 独立使用
"""

import sys
from pathlib import Path
from typing import List, Union

try:
    from pypdf import PdfWriter
except ImportError:
    print("[pdfmerge] 错误: 需要安装 pypdf: pip install pypdf")
    raise


class PDFMerger:
    """基于 pypdf 的 PDF 合并工具"""

    def __init__(self):
        self.writer = PdfWriter()
        self.processed_files = []

    def merge_pdfs(self, pdf_paths: List[Union[str, Path]],
                   output_path: Union[str, Path]) -> bool:
        """
        合并多个 PDF 文件

        Args:
            pdf_paths: PDF 文件路径列表
            output_path: 输出文件路径

        Returns:
            bool: 是否成功
        """
        try:
            # 清空之前的处理记录
            self.processed_files = []

            # 逐个处理 PDF 文件
            for pdf_path in pdf_paths:
                pdf_path = Path(pdf_path)
                if not pdf_path.exists():
                    print(f"[pdfmerge] 警告: 文件不存在 - {pdf_path}")
                    continue

                print(f"[pdfmerge] 正在处理: {pdf_path.name}")

                self.writer.append(str(pdf_path))
                self.processed_files.append(pdf_path.name)

            if not self.processed_files:
                print("[pdfmerge] 错误: 没有成功处理任何 PDF 文件")
                return False

            # 处理大文件时的递归限制
            if sys.getrecursionlimit() < 10000:
                sys.setrecursionlimit(10000)

            # 写入输出文件
            self.writer.write(str(output_path))
            print(f"[pdfmerge] 合并成功！输出文件: {output_path}")
            print(f"[pdfmerge] 处理文件数: {len(self.processed_files)}")

            return True

        except Exception as e:
            print(f"[pdfmerge] 合并失败: {e}")
            return False

        finally:
            # 关闭 writer 释放资源
            self.writer.close()

    def merge_from_folder(self, folder_path: Union[str, Path],
                          pattern: str = "*.pdf",
                          output_filename: str = "merged.pdf",
                          recursive: bool = False) -> bool:
        """
        合并文件夹中的所有 PDF 文件

        Args:
            folder_path: 文件夹路径
            pattern: 文件匹配模式，默认 "*.pdf"
            output_filename: 输出文件名
            recursive: 是否递归子文件夹

        Returns:
            bool: 是否成功
        """
        folder_path = Path(folder_path)
        if not folder_path.exists():
            print(f"[pdfmerge] 错误: 文件夹不存在: {folder_path}")
            return False

        # 查找 PDF 文件
        if recursive:
            pdf_files = list(folder_path.rglob(pattern))
        else:
            pdf_files = list(folder_path.glob(pattern))

        # 按文件名排序
        pdf_files.sort()

        if not pdf_files:
            print(f"[pdfmerge] 错误: 在 {folder_path} 中没有找到 PDF 文件")
            return False

        print(f"[pdfmerge] 找到 {len(pdf_files)} 个 PDF 文件:")
        for pdf in pdf_files:
            print(f"[pdfmerge]   - {pdf.name}")

        output_path = folder_path / output_filename
        return self.merge_pdfs(pdf_files, output_path)
