# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import ipaddress
import os
import re
import socket
from struct import unpack
from socket import inet_aton
from typing import Optional, List
from urllib.parse import urlparse

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


class UrlUtils:
    @staticmethod
    def check_url_is_valid(url):
        """check url is valid"""
        if not url:
            raise build_error(StatusCode.COMMON_URL_INPUT_INVALID, error_msg='url is empty')
        parsed_url = urlparse(url)
        hostname = parsed_url.hostname
        if not re.match(r"^https?://.*$", url):
            raise build_error(StatusCode.COMMON_URL_INPUT_INVALID, error_msg='illegal url protocol')
        try:
            ip_address = socket.gethostbyname(hostname)
        except socket.error as e:
            raise build_error(StatusCode.COMMON_URL_INPUT_INVALID, error_msg="resolving IP address failed") from e
        if UrlUtils._is_inner_ipaddress(ip_address):
            raise build_error(StatusCode.COMMON_URL_INPUT_INVALID, error_msg="illegal ip address")

    @staticmethod
    def get_global_proxy_url(url: str) -> Optional[str]:
        """get global proxy url"""
        if url and UrlUtils.should_bypass_proxy(url):
            return None

        global_proxy_url = os.getenv("http_proxy") or os.getenv("https_proxy") \
                           or os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
        if global_proxy_url:
            return global_proxy_url.strip()
        return global_proxy_url

    @staticmethod
    def get_global_proxies(url: str) -> Optional[dict]:
        """get global proxies"""
        global_proxy_url = UrlUtils.get_global_proxy_url(url)
        if global_proxy_url:
            return {
                "http": global_proxy_url,
                "https": global_proxy_url,
            }
        return None

    @staticmethod
    def _is_inner_ipaddress(ip):
        """judge inner ip"""
        if os.getenv("SSRF_PROTECT_ENABLED", "true").lower() == "false":
            # only if set SSRF_PROTECT_ENABLED to false, then allow inner ip
            return False

        ip_long = UrlUtils._ip_to_long(ip)
        is_inner_ip = UrlUtils._ip_to_long("10.0.0.0") <= ip_long <= UrlUtils._ip_to_long("10.255.255.255") or \
                      UrlUtils._ip_to_long("172.16.0.0") <= ip_long <= UrlUtils._ip_to_long("172.31.255.255") or \
                      UrlUtils._ip_to_long("192.168.0.0") <= ip_long <= UrlUtils._ip_to_long("192.168.255.255") or \
                      UrlUtils._ip_to_long("127.0.0.0") <= ip_long <= UrlUtils._ip_to_long("127.255.255.255") or \
                      ip_long == UrlUtils._ip_to_long("0.0.0.0")
        return is_inner_ip

    @staticmethod
    def _ip_to_long(ip_addr):
        """ trans ip to long"""
        return unpack("!L", inet_aton(ip_addr))[0]

    @staticmethod
    def should_bypass_proxy(url: str) -> bool:
        """check if URL should bypass proxy based on NO_PROXY environment variable"""
        parsed_url = urlparse(url)
        hostname = parsed_url.hostname
        if not hostname:
            return False

        no_proxy_list = UrlUtils._get_no_proxy_list()
        if not no_proxy_list:
            return False
        return UrlUtils._hostname_matches_no_proxy(hostname, no_proxy_list)

    @staticmethod
    def _get_no_proxy_list() -> List[str]:
        """parse NO_PROXY environment variable and get NO_PROXY list"""
        no_proxy_upper = os.getenv("NO_PROXY", "")
        no_proxy_lower = os.getenv("no_proxy", "")

        result = []
        seen = set()

        def process_proxy_str(proxy_str: str) -> None:
            if not proxy_str:
                return
            proxy_str = proxy_str.replace(" ", ",").replace(";", ",")
            items = [item.strip().lower() for item in proxy_str.split(',') if item.strip()]
            for item in items:
                if item not in seen:
                    seen.add(item)
                    result.append(item)

        process_proxy_str(no_proxy_upper)
        process_proxy_str(no_proxy_lower)

        return result

    @staticmethod
    def _hostname_matches_no_proxy(hostname: str, no_proxy_list: List[str]) -> bool:
        """check if hostname matches any entry in NO_PROXY list"""

        hostname_lower = hostname.lower()
        for entry in no_proxy_list:
            # 1. Wildcard "*" matches everything
            if entry == "*":
                return True
            # 2. Exact domain match: "example.com" matches "example.com"
            if entry == hostname_lower:
                return True
            # 3. Suffix match: ".example.com" matches "*.example.com"
            if entry.startswith("."):
                if hostname_lower.endswith(entry):
                    return True
            # 4. IP address exact match
            if UrlUtils._is_ip_match(hostname_lower, entry):
                return True

        return False

    @staticmethod
    def _is_ip_match(hostname: str, entry: str) -> bool:
        """check if IP address or CIDR matches"""
        try:
            ip_addr = ipaddress.ip_address(hostname)

            if "/" in entry:
                network = ipaddress.ip_network(entry, strict=False)
                return ip_addr in network
            else:
                entry_ip = ipaddress.ip_address(entry)
                return ip_addr == entry_ip
        except ValueError:
            return False
