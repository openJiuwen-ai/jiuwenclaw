#!/usr/bin/env python3
"""
Celia find skills search - Helper Script

This is a helper script for batch generation or CLI usage.
The main skill workflow is defined in SKILL.md and executed by Claude.

Usage:
    python search.py "query"
"""
import os
import uuid
import json
import argparse
import requests


def read_xiaoyienv():
    """
    读取 ~/.openclaw/.xiaoyienv 文件并返回键值对字典。
    """
    file_path = os.path.expanduser("~/.openclaw/.xiaoyienv")
    env_dict = {}

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            for line in file:
                # 去除行首尾的空白字符和换行符
                line = line.strip()
                # 跳过空行和以 # 开头的注释行
                if not line or line.startswith('#'):
                    continue
                # 确保行中包含等号
                if '=' in line:
                    # 只在第一个等号处分割，防止 value 中包含等号
                    key, value = line.split('=', 1)
                    env_dict[key.strip()] = value.strip()

    except FileNotFoundError:
        print(f"提示: 未找到env文件 {file_path}")
    except Exception as e:
        print(f"读取文件时发生错误: {e}")

    return env_dict


def get_installed_skills():
    """
    扫描 ~/.openclaw/workspace/skills 目录下的文件夹，返回文件夹名称列表
    """
    skills_dir = os.path.expanduser("~/.openclaw/workspace/skills")
    installed_skills = []

    try:
        # 检查目录是否存在
        if os.path.exists(skills_dir) and os.path.isdir(skills_dir):
            # 遍历目录下的所有条目
            for entry in os.listdir(skills_dir):
                entry_path = os.path.join(skills_dir, entry)
                # 只添加文件夹
                if os.path.isdir(entry_path):
                    installed_skills.append(entry)
        else:
            print(f"提示: 技能目录 {skills_dir} 不存在或不是目录")
    except Exception as e:
        print(f"读取技能目录时发生错误: {e}")

    return installed_skills


def format_skill_data(raw_skills, installed_skills):
    """
    格式化技能数据，添加安装状态标识，调整字段名称
    """
    formatted_skills = []

    for skill in raw_skills:
        # 检查是否已安装
        status = "已安装" if skill.get("skillId") in installed_skills else "未安装"

        # 构建格式化后的技能字典
        formatted_skill = {
            "skillId": skill.get("skillId", ""),
            "skillName": skill.get("skillName", ""),
            "skillDesc": skill.get("skillDesc", ""),
            "downloadPath": skill.get("packUrl", ""),  # 将packUrl重命名为downloadPath
            "status": status
        }

        formatted_skills.append(formatted_skill)

    return formatted_skills


def search_skills(query):
    # Check environment variables
    required_env = ['PERSONAL-API-KEY', 'PERSONAL-UID', 'SERVICE_URL']

    env_dict = read_xiaoyienv()
    UID = env_dict.get('PERSONAL-UID', '')
    SERVICE_URL = env_dict.get('SERVICE_URL', '')
    API_KEY = env_dict.get('PERSONAL-API-KEY', '')

    missing = [k for k in required_env if not env_dict.get(k)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}")
        return None

    # Configuration
    trace_id = str(uuid.uuid4())
    api_url = f'{SERVICE_URL}/celia-claw/v1/rest-api/skill/execute'

    # Build request
    headers = {
        'Content-Type': 'application/json',
        'x-skill-id': 'celia_find_skills',
        'x-hag-trace-id': trace_id,
        'x-uid': UID,
        'x-api-key': API_KEY,
        'x-request-from': 'openclaw',
    }

    payload = {
        "query": query
    }

    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=30, verify=True)
        response.raise_for_status()
        # 尝试解析JSON，增加异常捕获
        try:
            response_data = response.json()
        except json.JSONDecodeError as e:
            print(f"\nJSON解析失败: {e}")
            print(f"无法解析的原始内容: {response.text}")
            return None

        # 获取已安装的技能列表
        installed_skills = get_installed_skills()

        # 格式化技能数据
        if response_data.get("errorCode") == "0" and "content" in response_data and "skills" in response_data["content"]:
            formatted_data = format_skill_data(response_data["content"]["skills"], installed_skills)

            # 打印格式化后的JSON数据
            print("skill列表:")
            print(json.dumps(formatted_data, indent=2, ensure_ascii=False))
            return formatted_data
        else:
            print("\n响应数据格式异常，未找到skill列表")
            print(f"原始响应数据: {json.dumps(response_data, indent=2, ensure_ascii=False)}")
            return None

    except requests.exceptions.Timeout:
        print("Error: Request timed out (30s)")
        return None
    except requests.exceptions.RequestException as e:
        print(f"HTTP请求异常: {e}")
        return None
    except Exception as e:
        print(f"未知错误: {e}")
        return None


def parse_args():
    """解析命令行参数（移除skillId参数）"""
    parser = argparse.ArgumentParser(description="查找技能")
    parser.add_argument("--query", required=True, help="查找技能的关键词")
    return parser.parse_args()


def main():
    """主函数"""
    args = parse_args()
    search_skills(query=args.query)


if __name__ == '__main__':
    main()