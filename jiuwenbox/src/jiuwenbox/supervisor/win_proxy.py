# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Windows 出站代理 (HTTP CONNECT + SOCKS5, asyncio).

对齐 docs/window沙箱.md 6.6:
  - 监听 127.0.0.1:<port_range> 范围, WFP Permit filter 仅放行指向这些端口
    的流量, 沙箱所有出网流量必须经过代理.
  - HTTP CONNECT: 用于 HTTPS 流量 (透传 TLS, 不做 MITM).
  - SOCKS5: 用于非 HTTP 协议 (Git SSH / DB 等).
  - 过滤逻辑: 解析目标域名/IP -> 先查 deny (命中拒绝) -> 再查 allow (非空
    且未命中拒绝则放行) -> 否则放行/拒绝 (按 default).

过滤语义与 Linux supervisor/network.py 的 egress/ingress 完全对齐:
deny 优先于 allow; 域名解析后比对 IP CIDR; 端口单独匹配.

纯标准库 asyncio 实现, 无 win32 依赖, 因此 Linux 下可完整单元测试.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.models.policy import NetworkRulePolicy
from jiuwenbox.supervisor import win_constants as const

configure_logging()
logger = logging.getLogger(__name__)

# 单个隧道读缓冲上限, 防止恶意对端用大量数据撑爆内存.
_TUNNEL_BUF = 65536
# 代理握手阶段超时 (秒).
_HANDSHAKE_TIMEOUT = 15.0
# 目标连接超时 (秒).
_CONNECT_TIMEOUT = 15.0


class EgressFilter:
    """域名/IP/端口 过滤器 (对齐 network.py 语义).

    规则:
      1. 先查 deny: blocked_domains/blocked_ips/blocked_ports 命中 -> 拒绝.
      2. 再查 allow: allowed_domains(解析后为 IP CIDR)/allowed_ips/allowed_ports.
         若 allow 列表非空且未命中 -> 拒绝; 若 allow 列表为空 -> 放行.
      3. default="deny" 时, allow 列表为空 -> 拒绝; default="allow" 时放行.
    """

    def __init__(
        self,
        egress: NetworkRulePolicy,
        ingress: NetworkRulePolicy | None = None,
    ) -> None:
        self.egress = egress
        self.ingress = ingress
        # 预解析: 域名不在这里解析 (运行时按目标域名动态解析), 只预建 IP/端口集合.
        self._blocked_ips = self._parse_networks(egress.blocked_ips)
        self._allowed_ips = self._parse_networks(egress.allowed_ips)
        self._blocked_ports = set(egress.blocked_ports)
        self._allowed_ports = set(egress.allowed_ports)
        # 域名规则保留原始字符串, 握手时匹配 (支持通配符).
        self._blocked_domains = list(egress.blocked_domains)
        self._allowed_domains = list(egress.allowed_domains)

    @staticmethod
    def _parse_networks(values: list[str]) -> list["ipaddress.IPv4Network | ipaddress.IPv6Network"]:
        nets: list["ipaddress.IPv4Network | ipaddress.IPv6Network"] = []
        for v in values:
            try:
                nets.append(ipaddress.ip_network(v, strict=False))
            except ValueError:
                logger.warning("忽略无效 IP/CIDR 规则: %s", v)
        return nets

    @staticmethod
    def _domain_matches(pattern: str, host: str) -> bool:
        """通配符域名匹配: '*.example.com' 匹配 'a.example.com' / 'example.com'?.

        约定 (与 network.py resolve_domains 一致): 通配符前缀 '*' 匹配任意
        子域; 无通配符则精确匹配.
        """
        if not pattern or not host:
            return False
        pat = pattern.lower().strip()
        host = host.lower().strip()
        if pat.startswith("*."):
            base = pat[2:]
            # *.example.com 匹配 sub.example.com, 也匹配 example.com (兼容写法).
            return host == base or host.endswith("." + base)
        return pat == host

    def _ip_in_networks(self, ip_str: str, nets: list) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        return any(ip in net for net in nets)

    def allow(self, host: str, port: int) -> tuple[bool, str]:
        """判定是否放行 (host, port). 返回 (allowed, reason).

        语义对齐 Linux supervisor/network.py 的 iptables 规则:
          1. blocked_domains / blocked_ips / blocked_ports 命中 -> 拒绝.
          2. 若同时有 allowed_ips 和 allowed_ports: 必须两者都命中才放行 (AND).
          3. 若只有 allowed_ports (无 allowed_ips/domains): 放行该端口所有 IP.
          4. 若只有 allowed_ips/domains (无 allowed_ports): 放行这些 IP/域名的
             所有端口.
          5. 无任何 allow 规则: 按 default.
        """
        if not host:
            return False, "empty host"

        # 1. 域名 deny 优先.
        for pat in self._blocked_domains:
            if self._domain_matches(pat, host):
                return False, f"domain blocked by {pat}"

        # 2. 端口 deny.
        if port in self._blocked_ports:
            return False, f"port {port} blocked"

        # 3. IP deny (域名先解析).
        resolved_ips: list[str] = []
        ip_host: str | None = None
        try:
            ipaddress.ip_address(host)
            ip_host = host
        except ValueError:
            try:
                infos = socket.getaddrinfo(
                    host, None, socket.AF_UNSPEC, socket.SOCK_STREAM,
                )
                for info in infos:
                    resolved_ips.append(info[4][0])
            except socket.gaierror:
                if self._has_allow_rules():
                    return False, f"unresolvable domain {host} with allow-rules present"
                return self.egress.default != "deny", "unresolvable domain, default"

        ips_to_check: list[str] = [ip_host] if ip_host else resolved_ips

        # 3a. blocked_ips 命中 -> 拒绝.
        for ip in ips_to_check:
            if self._ip_in_networks(ip, self._blocked_ips):
                return False, f"ip {ip} blocked"

        # 4. allow 判定.
        has_ip_rules = bool(self._allowed_ips or self._allowed_domains)
        has_port_rules = bool(self._allowed_ports)

        domain_allowed = any(
            self._domain_matches(pat, host) for pat in self._allowed_domains
        )
        ip_allowed = any(
            self._ip_in_networks(ip, self._allowed_ips) for ip in ips_to_check
        )
        port_in_allow = port in self._allowed_ports if self._allowed_ports else False

        # 4a. allow 规则按维度独立判定 (OR), 对齐 Linux iptables 的独立 ACCEPT 链:
        #     - allowed_ips 是一条 ACCEPT-by-IP 规则;
        #     - allowed_ports 是另一条 ACCEPT-by-port 规则.
        #     任一命中即放行, 不做 AND (review MAJOR #10: 旧版在 IP+port 同时存在时
        #     做 AND, 比 Linux 严, {allowed_ips:[10/8], allowed_ports:[443]} 会
        #     错杀 10.1.2.3:8443).
        if has_ip_rules or has_port_rules:
            reasons: list[str] = []
            if domain_allowed:
                reasons.append("domain")
            if ip_allowed:
                reasons.append("ip")
            if port_in_allow:
                reasons.append("port")
            if reasons:
                return True, f"explicitly allowed ({'+'.join(reasons)})"
            return False, f"{host}:{port} not in any allow rule"

        # 5. 无任何 allow 规则: 按 default.
        if self.egress.default == "deny":
            return False, "default deny (no allow rules)"
        return True, "default allow"

    def _has_allow_rules(self) -> bool:
        return bool(
            self._allowed_domains
            or self._allowed_ips
            or self._allowed_ports
        )


async def _pipe_streams(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """双向桥接两个流, 任意一端关闭则结束."""
    try:
        while True:
            data = await reader.read(_TUNNEL_BUF)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_reader: asyncio.StreamReader,
    target_writer: asyncio.StreamWriter,
) -> None:
    """双向隧道: client <-> target. 两个方向都跑完才结束."""
    t1 = asyncio.create_task(
        _pipe_streams(client_reader, target_writer), name="c2t",
    )
    t2 = asyncio.create_task(
        _pipe_streams(target_reader, client_writer), name="t2c",
    )
    try:
        await asyncio.gather(t1, t2, return_exceptions=True)
    finally:
        for t in (t1, t2):
            if not t.done():
                t.cancel()
        for w in (client_writer, target_writer):
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass


async def _connect_target(host: str, port: int) -> tuple:
    """连接目标, 返回 (reader, writer)."""
    return await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=_CONNECT_TIMEOUT,
    )


async def handle_http_connect(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    first_line: bytes,
    egress_filter: EgressFilter,
) -> None:
    """处理 HTTP CONNECT 隧道请求.

    first_line 形如 ``b'CONNECT host:port HTTP/1.1'``.
    """
    try:
        # 解析 CONNECT 目标.
        try:
            line = first_line.decode("latin-1").strip()
            parts = line.split()
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await client_writer.drain()
                return
            host_port = parts[1]
            if ":" in host_port:
                host, port_str = host_port.rsplit(":", 1)
                port = int(port_str)
            else:
                host, port = host_port, 443
        except (ValueError, UnicodeDecodeError):
            client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await client_writer.drain()
            return

        allowed, reason = egress_filter.allow(host, port)
        if not allowed:
            logger.info("HTTP CONNECT 拒绝 %s:%d (%s)", host, port, reason)
            client_writer.write(
                b"HTTP/1.1 403 Forbidden\r\n"
                b"Content-Length: 0\r\n"
                b"\r\n",
            )
            await client_writer.drain()
            return

        # 读掉 CONNECT 之后到空行的剩余 HTTP 头.
        while True:
            header = await asyncio.wait_for(
                client_reader.readline(), timeout=_HANDSHAKE_TIMEOUT,
            )
            if header in (b"\r\n", b"\n", b""):
                break

        try:
            target_reader, target_writer = await _connect_target(host, port)
        except (asyncio.TimeoutError, OSError) as exc:
            logger.info("CONNECT 目标连接失败 %s:%d (%s)", host, port, exc)
            client_writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\n"
                b"Content-Length: 0\r\n"
                b"\r\n",
            )
            await client_writer.drain()
            return

        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()
        await _relay(client_reader, client_writer, target_reader, target_writer)
    except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
        logger.debug("HTTP CONNECT handler 异常: %s", exc)
    finally:
        try:
            client_writer.close()
        except Exception:  # noqa: BLE001
            pass


async def handle_socks5(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    egress_filter: EgressFilter,
) -> None:
    """处理 SOCKS5 请求 (RFC 1928)."""
    try:
        # 握手: 版本 + 方法数 + 方法列表.
        header = await asyncio.wait_for(
            client_reader.readexactly(2), timeout=_HANDSHAKE_TIMEOUT,
        )
        if header[0] != 0x05:  # SOCKS 版本
            return
        nmethods = header[1]
        await client_reader.readexactly(nmethods)
        # 无需认证.
        client_writer.write(b"\x05\x00")
        await client_writer.drain()

        # 请求: VER CMD RSV ATYP DST.ADDR DST.PORT
        req = await asyncio.wait_for(
            client_reader.readexactly(4), timeout=_HANDSHAKE_TIMEOUT,
        )
        if req[0] != 0x05 or req[1] != 0x01:  # 仅支持 CONNECT
            client_writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            await client_writer.drain()
            return
        atyp = req[3]
        if atyp == 0x01:  # IPv4
            addr_bytes = await client_reader.readexactly(4)
            host = str(ipaddress.IPv4Address(addr_bytes))
        elif atyp == 0x03:  # 域名
            length = (await client_reader.readexactly(1))[0]
            host = (await client_reader.readexactly(length)).decode("ascii", "replace")
        elif atyp == 0x04:  # IPv6
            addr_bytes = await client_reader.readexactly(16)
            host = str(ipaddress.IPv6Address(addr_bytes))
        else:
            client_writer.write(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
            await client_writer.drain()
            return
        port_bytes = await client_reader.readexactly(2)
        port = int.from_bytes(port_bytes, "big")

        allowed, reason = egress_filter.allow(host, port)
        if not allowed:
            logger.info("SOCKS5 拒绝 %s:%d (%s)", host, port, reason)
            client_writer.write(b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00")
            await client_writer.drain()
            return

        try:
            target_reader, target_writer = await _connect_target(host, port)
        except (asyncio.TimeoutError, OSError) as exc:
            logger.info("SOCKS5 目标连接失败 %s:%d (%s)", host, port, exc)
            client_writer.write(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            await client_writer.drain()
            return

        # 成功应答.
        client_writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await client_writer.drain()
        await _relay(client_reader, client_writer, target_reader, target_writer)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
        logger.debug("SOCKS5 handler 异常: %s", exc)
    finally:
        try:
            client_writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    egress_filter: EgressFilter,
) -> None:
    """根据首字节判断是 HTTP CONNECT 还是 SOCKS5, 分派到对应 handler."""
    try:
        first = await asyncio.wait_for(
            client_reader.readexactly(1), timeout=_HANDSHAKE_TIMEOUT,
        )
        if first == b"C":  # "CONNECT..." 起头.
            # 读回第一行剩余部分 (用 readline 但已读了首字节, 拼回).
            rest = await asyncio.wait_for(
                client_reader.readline(), timeout=_HANDSHAKE_TIMEOUT,
            )
            await handle_http_connect(
                client_reader, client_writer, first + rest, egress_filter,
            )
        elif first == b"\x05":  # SOCKS5 版本字节.
            await handle_socks5(client_reader, client_writer, egress_filter)
        else:
            # 非 HTTP CONNECT / 非 SOCKS5: 透传当作纯 TCP? 文档要求仅放行代理
            # 协议, 这里直接关闭未知协议连接.
            client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await client_writer.drain()
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
        logger.debug("client handler 异常: %s", exc)
    finally:
        try:
            client_writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _serve_port(
    port: int,
    egress_filter: EgressFilter,
    stop_event: asyncio.Event,
) -> None:
    """在单个端口上监听, 直到 stop_event 触发."""
    server: asyncio.base_events.Server | None = None
    try:
        async def _cb(r, w):
            await _handle_client(r, w, egress_filter)

        server = await asyncio.start_server(_cb, host="127.0.0.1", port=port)
        logger.info("win_proxy 监听 127.0.0.1:%d", port)
        # 阻塞直到 stop_event.
        await stop_event.wait()
    except OSError as exc:
        logger.warning("win_proxy 端口 %d 启动失败: %s", port, exc)
    finally:
        if server is not None:
            server.close()
            try:
                await server.wait_closed()
            except Exception:  # noqa: BLE001
                pass


async def serve_windows_proxy(
    egress: NetworkRulePolicy,
    ingress: NetworkRulePolicy | None = None,
    port_range_start: int = const.DEFAULT_PROXY_PORT_RANGE_START,
    port_range_end: int = const.DEFAULT_PROXY_PORT_RANGE_END,
    stop_event: asyncio.Event | None = None,
) -> tuple[asyncio.Task, asyncio.Event]:
    """启动 Windows 出站代理, 监听端口范围.

    返回 (proxy_task, stop_event). 调用方 ``stop_event.set()`` 即可让所有
    监听任务优雅退出. proxy_task 是汇总所有端口 server 的总任务.
    """
    if stop_event is None:
        stop_event = asyncio.Event()
    egress_filter = EgressFilter(egress, ingress)
    tasks = [
        asyncio.create_task(
            _serve_port(port, egress_filter, stop_event),
            name=f"win-proxy-port-{port}",
        )
        for port in range(port_range_start, port_range_end + 1)
    ]

    async def _supervisor():
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:  # noqa: BLE001
            logger.exception("win_proxy supervisor 异常")

    proxy_task = asyncio.create_task(_supervisor(), name="win-proxy-supervisor")
    logger.info(
        "win_proxy 启动: 端口范围 %d-%d, egress default=%s",
        port_range_start, port_range_end, egress.default,
    )
    return proxy_task, stop_event
