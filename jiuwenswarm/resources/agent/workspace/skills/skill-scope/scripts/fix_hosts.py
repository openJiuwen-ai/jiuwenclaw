"""
版权所有 (c) 华为技术有限公司 2026-2026
fix_hosts.py — 进程级 hosts 映射（不改系统 hosts）

功能：把 {域名: IP} 映射在当前进程内生效，无需修改系统 hosts 文件。
用法：
    import fix_hosts
"""

import socket

def apply(host_ips):
    original_getaddrinfo = socket.getaddrinfo

    def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host in host_ips:
            host = host_ips[host]
        return original_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = patched_getaddrinfo

apply({"lfhagmirror.hwcloudtest.cn": "10.33.233.153"})