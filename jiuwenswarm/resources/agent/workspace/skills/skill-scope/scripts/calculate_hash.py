"""
版权所有 (c) 华为技术有限公司 2026-2026

计算项目目录的 SHA256 哈希值
"""

import hashlib
import os

from constants import IGNORE_DIRS, IGNORE_FILES


def _calculate_file_sha256(file_path: str) -> str:
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _calculate_string_sha256(input_string: str) -> str:
    return hashlib.sha256(input_string.encode('utf-8')).hexdigest()


def calculate_project_hash(project_path: str) -> str:
    """
    递归计算项目目录中所有文件的 SHA256 哈希，
    将各文件哈希排序后拼接再取整体哈希
    """
    file_hashes = []

    def walk_dir(dir_path: str):
        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            return

        dirs = []
        files = []

        for entry in entries:
            full_path = os.path.join(dir_path, entry)
            if os.path.isdir(full_path):
                if entry not in IGNORE_DIRS:
                    dirs.append(entry)
            elif os.path.isfile(full_path):
                if entry not in IGNORE_FILES:
                    files.append(entry)

        for filename in files:
            fpath = os.path.join(dir_path, filename)
            try:
                file_hashes.append(_calculate_file_sha256(fpath))
            except Exception:
                pass

        for d in dirs:
            walk_dir(os.path.join(dir_path, d))

    walk_dir(project_path)
    file_hashes.sort()
    return _calculate_string_sha256('\n'.join(file_hashes))
