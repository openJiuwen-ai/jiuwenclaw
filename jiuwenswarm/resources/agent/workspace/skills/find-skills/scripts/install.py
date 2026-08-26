#!/usr/bin/env python3
# coding=utf-8
# Copyright © Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

import os
import sys
import zipfile
import argparse
import requests
from urllib3.exceptions import InsecureRequestWarning
from requests.exceptions import ConnectTimeout, RequestException
from pathlib import Path

# 忽略不安全请求警告
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# 定义基础路径（移除skill_id层级）
BASE_DIR = os.path.expanduser("~/.openclaw/workspace/skills")
TIMEOUT = 30  # 请求超时时间


def http_exception_handler(func):
    """HTTP请求异常处理装饰器（增加资源清理逻辑）"""
    def wrapper(*args, **kwargs):
        # 用于存储需要清理的临时文件路径
        temp_files = []
        try:
            # 将临时文件列表传入被装饰函数，方便函数内部添加需要清理的文件
            kwargs['temp_files'] = temp_files
            return func(*args, **kwargs)
        except ConnectTimeout:
            print(f"错误：连接超时（{TIMEOUT}秒），请检查URL是否可达", file=sys.stderr)
            sys.exit(1)
        except RequestException as e:
            print(f"错误：请求失败 - {str(e)}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"错误：未知异常 - {str(e)}", file=sys.stderr)
            sys.exit(1)
        finally:
            # 无论是否发生异常，都清理临时文件
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                        print(f"已清理临时文件: {temp_file}", file=sys.stderr)
                    except Exception as remove_err:
                        print(f"清理临时文件失败: {temp_file}, 错误: {str(remove_err)}", file=sys.stderr)
    return wrapper


def safe_extract(zip_file: zipfile.ZipFile, target_dir: str):
    """
    安全解压ZIP文件，防止目录遍历漏洞
    :param zip_file: 已打开的ZipFile对象
    :param target_dir: 目标解压目录
    """
    # 转换为Path对象并获取绝对路径（统一为Path类型，避免字符串/对象混用）
    target_dir_path = Path(target_dir).resolve()

    for member in zip_file.infolist():
        # 拼接解压后的文件路径（Path对象拼接，避免路径分隔符问题）
        member_path = target_dir_path / member.filename

        # 关键修复：检查member_path的父级是否在目标目录内（均为Path对象对比）
        # resolve()确保获取真实路径，避免符号链接等绕过检查
        if not member_path.resolve().is_relative_to(target_dir_path):
            raise ValueError(f"检测到恶意路径/越界路径：{member.filename}，拒绝解压该文件")

        # 安全解压单个文件/目录
        zip_file.extract(member, target_dir)


@http_exception_handler
def download_and_extract_zip(file_url: str, temp_files=None):
    """
    根据URL下载ZIP文件并解压到指定目录（增加安全校验和资源清理）
    :param file_url: ZIP文件的下载链接
    :param temp_files: 临时文件列表（由装饰器传入，用于清理）
    """
    if temp_files is None:
        temp_files = []

    # 构建目标目录（移除skill_id）
    target_dir = BASE_DIR
    os.makedirs(target_dir, exist_ok=True)

    # 临时ZIP文件路径
    zip_temp_path = os.path.join(target_dir, "temp_download.zip")
    temp_files.append(zip_temp_path)  # 将临时文件加入清理列表

    # 1. 下载ZIP文件
    print(f"开始下载文件")
    response = requests.get(file_url, verify=True, timeout=TIMEOUT, stream=True)
    response.raise_for_status()

    with open(zip_temp_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"文件下载完成")

    # 2. 解压ZIP文件（安全解压）
    print(f"开始解压文件")
    with zipfile.ZipFile(zip_temp_path, "r") as zip_ref:
        safe_extract(zip_ref, target_dir)  # 使用安全解压方法
    print("文件解压完成")

    # 3. 删除临时ZIP文件（正常流程主动清理，异常流程由装饰器兜底）
    if os.path.exists(zip_temp_path):
        os.remove(zip_temp_path)
        temp_files.remove(zip_temp_path)  # 从清理列表移除
    print(f"临时文件已清理")
    print(f"操作完成！！！")


def parse_args():
    """解析命令行参数（移除skillId参数）"""
    parser = argparse.ArgumentParser(description="下载ZIP文件并解压到指定目录")
    parser.add_argument("--url", required=True, help="ZIP文件的下载URL（必填）")
    return parser.parse_args()


def main():
    """主函数"""
    args = parse_args()
    download_and_extract_zip(file_url=args.url)


if __name__ == '__main__':
    main()