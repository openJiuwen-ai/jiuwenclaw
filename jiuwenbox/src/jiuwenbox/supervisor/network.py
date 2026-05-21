# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Network isolation via iptables rules inside an unshared network namespace.

This module configures iptables rules within a sandbox network namespace.
"""

from __future__ import annotations

import ipaddress
import logging
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.models.policy import NetworkPolicy, NetworkMode, NetworkRulePolicy

configure_logging()
logger = logging.getLogger(__name__)

IP_BINARY = "ip"
IPTABLES_BINARY = "iptables"
IP6TABLES_BINARY = "ip6tables"
IPTABLES_LEGACY_BINARY = "iptables-legacy"
IP6TABLES_LEGACY_BINARY = "ip6tables-legacy"
IPTABLES_NFT_BINARY = "iptables-nft"
IP6TABLES_NFT_BINARY = "ip6tables-nft"


class NetworkSetupError(RuntimeError):
    """Raised when required network isolation setup fails."""


def _format_command_error(cmd: list[str], result: subprocess.CompletedProcess) -> str:
    details = [
        f"Command '{' '.join(cmd)}' failed with exit code {result.returncode}.",
    ]
    if result.stderr:
        details.append(f"stderr: {result.stderr.strip()}")
    if result.stdout:
        details.append(f"stdout: {result.stdout.strip()}")
    return " ".join(details)


@dataclass
class ResolvedNetworkRules:
    """Pre-resolved iptables rules ready to apply."""

    allowed_ips: list[str] = field(default_factory=list)
    blocked_ips: list[str] = field(default_factory=list)
    allowed_ports: list[int] = field(default_factory=list)
    blocked_ports: list[int] = field(default_factory=list)
    default_deny: bool = True


def resolve_domains(domains: list[str]) -> list[str]:
    """Resolve domain names to IP addresses.

    Supports wildcard domains like '*.example.com' by stripping the
    wildcard prefix and resolving the base domain.
    """
    ips: list[str] = []
    for domain in domains:
        # Strip wildcard prefix for resolution
        clean = domain.lstrip("*.")
        try:
            results = socket.getaddrinfo(clean, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for family, _, _, _, sockaddr in results:
                ip = sockaddr[0]
                if ip not in ips:
                    ips.append(ip)
            logger.debug("Resolved %s -> %s", domain, ips[-len(results):])
        except socket.gaierror:
            logger.warning("Failed to resolve domain: %s", domain)
    return ips


def normalize_ips(values: list[str]) -> list[str]:
    """Normalize IP/CIDR entries into iptables-ready values."""
    resolved: list[str] = []
    for value in values:
        try:
            normalized = str(ipaddress.ip_network(value, strict=False))
            if normalized not in resolved:
                resolved.append(normalized)
        except ValueError:
            logger.warning("Ignoring invalid IP/CIDR rule: %s", value)
    return resolved


def build_network_rules(policy: NetworkRulePolicy) -> ResolvedNetworkRules:
    """Resolve domains/IPs and build a direction-agnostic network rule set."""
    rules = ResolvedNetworkRules(
        default_deny=(policy.default == "deny"),
        allowed_ports=list(policy.allowed_ports),
        blocked_ports=list(policy.blocked_ports),
    )

    rules.blocked_ips = [
        *normalize_ips(policy.blocked_ips),
        *resolve_domains(policy.blocked_domains),
    ]
    rules.allowed_ips = [
        *normalize_ips(policy.allowed_ips),
        *resolve_domains(policy.allowed_domains),
    ]

    # Remove entries that appear in both allowed and blocked (blocked wins).
    blocked_set = set(rules.blocked_ips)
    rules.allowed_ips = [ip for ip in rules.allowed_ips if ip not in blocked_set]

    return rules


def _existing_binaries(paths: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for path in paths:
        if path in result:
            continue
        if path.startswith("/") and not shutil.which(path):
            continue
        result.append(path)
    return result


def _iptables_candidates(ip_version: int) -> list[str]:
    if ip_version == 6:
        return _existing_binaries((
            IP6TABLES_BINARY,
            IP6TABLES_NFT_BINARY,
            IP6TABLES_LEGACY_BINARY,
        ))
    return _existing_binaries((
        IPTABLES_BINARY,
        IPTABLES_NFT_BINARY,
        IPTABLES_LEGACY_BINARY,
    ))


def _run_iptables_binary(
    binary: str,
    args: list[str],
    *,
    check: bool = True,
    namespace: str | None = None,
) -> subprocess.CompletedProcess:
    cmd = [binary] + args
    if namespace:
        cmd = [IP_BINARY, "netns", "exec", namespace, *cmd]
    logger.debug("%s: %s", binary, " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise NetworkSetupError(_format_command_error(cmd, result))
    return result


@lru_cache(maxsize=128)
def _select_iptables_binary(ip_version: int, namespace: str | None = None) -> str:
    """Return a working iptables backend for the target namespace.

    Some distributions default iptables to the nf_tables backend,
    while older or constrained kernels only support legacy xtables. Probe both
    and keep the sandbox creation failure explicit if neither backend works.
    """
    failures: list[str] = []
    for binary in _iptables_candidates(ip_version):
        result = _run_iptables_binary(
            binary,
            ["-L", "OUTPUT", "-n"],
            check=False,
            namespace=namespace,
        )
        if result.returncode == 0:
            return binary
        failures.append(_format_command_error(
            [IP_BINARY, "netns", "exec", namespace, binary, "-L", "OUTPUT", "-n"]
            if namespace
            else [binary, "-L", "OUTPUT", "-n"],
            result,
        ))

    family = "IPv6" if ip_version == 6 else "IPv4"
    target = f" in netns {namespace}" if namespace else ""
    detail = " ".join(failures) if failures else "No iptables binary found."
    raise NetworkSetupError(f"No working {family} iptables backend{target}. {detail}")


def _run_iptables(
    args: list[str],
    check: bool = True,
    namespace: str | None = None,
    ip_version: int = 4,
) -> subprocess.CompletedProcess:
    """Run an iptables/ip6tables command."""
    binary = _select_iptables_binary(ip_version, namespace)
    return _run_iptables_binary(binary, args, check=check, namespace=namespace)


def run_iptables(
    args: list[str],
    check: bool = True,
    namespace: str | None = None,
    ip_version: int = 4,
) -> subprocess.CompletedProcess:
    """Run an iptables/ip6tables command for callers outside this module."""
    return _run_iptables(
        args,
        check=check,
        namespace=namespace,
        ip_version=ip_version,
    )


def _ip_version(value: str) -> int:
    """Return 4 or 6 for an IP/CIDR rule value."""
    return ipaddress.ip_network(value, strict=False).version


def _run_iptables_for_ip(
    args: list[str],
    ip_value: str,
    check: bool = True,
    namespace: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a firewall rule in the table matching the IP/CIDR version."""
    ip_version = _ip_version(ip_value)
    if ip_version == 6 and not _ip6tables_available(namespace):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    return _run_iptables(
        args,
        check=check,
        namespace=namespace,
        ip_version=ip_version,
    )


def _run_iptables_both(
    args: list[str],
    check: bool = True,
    namespace: str | None = None,
) -> None:
    """Run a protocol-agnostic firewall rule for both IPv4 and IPv6."""
    _run_iptables(args, check=check, namespace=namespace, ip_version=4)
    if _ip6tables_available(namespace):
        _run_iptables(args, check=check, namespace=namespace, ip_version=6)


@lru_cache(maxsize=128)
def _ip6tables_available(namespace: str | None = None) -> bool:
    """Return whether ip6tables can manage rules in the target namespace."""
    try:
        result = _run_iptables(
            ["-L", "OUTPUT", "-n"],
            check=False,
            namespace=namespace,
            ip_version=6,
        )
    except (OSError, NetworkSetupError) as exc:
        logger.warning("Skipping IPv6 firewall rules because ip6tables is unavailable: %s", exc)
        return False

    if result.returncode == 0:
        return True

    target = f" in netns {namespace}" if namespace else ""
    stderr = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    logger.warning("Skipping IPv6 firewall rules%s because ip6tables is unavailable: %s", target, stderr)
    return False


def _run_ip(
    args: list[str],
    check: bool = True,
    namespace: str | None = None,
) -> subprocess.CompletedProcess:
    """Run an ip command."""
    cmd = [IP_BINARY] + args
    if namespace:
        cmd = [IP_BINARY, "netns", "exec", namespace, *cmd]
    logger.debug("ip: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def netns_name_for_sandbox(sandbox_id: str) -> str:
    """Return the deterministic network namespace name for a sandbox."""
    return f"jiuwenbox-{sandbox_id}"


def namespace_exists(namespace: str) -> bool:
    """Check whether a named network namespace already exists."""
    result = subprocess.run(
        [IP_BINARY, "netns", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False

    return any(line.split(maxsplit=1)[0] == namespace for line in result.stdout.splitlines())


def create_named_namespace(namespace: str) -> None:
    """Create a persistent named network namespace."""
    subprocess.run([IP_BINARY, "netns", "add", namespace], check=True, capture_output=True, text=True)


def delete_named_namespace(namespace: str) -> None:
    """Delete a persistent named network namespace."""
    subprocess.run(
        [IP_BINARY, "netns", "delete", namespace],
        check=True,
        capture_output=True,
        text=True,
    )


def setup_loopback(namespace: str | None = None) -> None:
    """Bring up the loopback interface inside the network namespace."""
    _run_ip(["link", "set", "lo", "up"], namespace=namespace)


def build_egress_rules(egress: NetworkRulePolicy) -> ResolvedNetworkRules:
    """Resolve egress policy into iptables-ready rules."""
    return build_network_rules(egress)


def build_ingress_rules(ingress: NetworkRulePolicy) -> ResolvedNetworkRules:
    """Resolve ingress policy into iptables-ready rules."""
    return build_network_rules(ingress)


def _apply_egress_rules(rules: ResolvedNetworkRules, namespace: str | None = None) -> None:
    """Apply outbound network rules inside the current namespace."""
    _run_iptables_both(["-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT"], namespace=namespace)
    # Allow established/related connections
    _run_iptables_both(["-A", "OUTPUT", "-m", "state", "--state",
                        "ESTABLISHED,RELATED", "-j", "ACCEPT"], namespace=namespace)

    for port in rules.blocked_ports:
        _run_iptables_both(
            ["-A", "OUTPUT", "-p", "tcp", "--dport", str(port), "-j", "DROP"],
            namespace=namespace,
        )

    if not rules.default_deny:
        for ip in rules.blocked_ips:
            _run_iptables_for_ip(["-A", "OUTPUT", "-d", ip, "-j", "DROP"], ip, namespace=namespace)
        return

    # Allow DNS (needed for domain resolution within the sandbox)
    _run_iptables_both(["-A", "OUTPUT", "-p", "udp", "--dport", "53", "-j", "ACCEPT"], namespace=namespace)
    _run_iptables_both(["-A", "OUTPUT", "-p", "tcp", "--dport", "53", "-j", "ACCEPT"], namespace=namespace)

    # Block explicitly blocked IPs first (higher priority)
    for ip in rules.blocked_ips:
        _run_iptables_for_ip(["-A", "OUTPUT", "-d", ip, "-j", "DROP"], ip, namespace=namespace)

    # Allow traffic to resolved IPs on allowed ports
    for ip in rules.allowed_ips:
        if rules.allowed_ports:
            for port in rules.allowed_ports:
                _run_iptables_for_ip([
                    "-A", "OUTPUT", "-d", ip, "-p", "tcp",
                    "--dport", str(port), "-j", "ACCEPT",
                ], ip, namespace=namespace)
        else:
            _run_iptables_for_ip(["-A", "OUTPUT", "-d", ip, "-j", "ACCEPT"], ip, namespace=namespace)

    # Default drop for everything else
    _run_iptables_both(["-A", "OUTPUT", "-j", "DROP"], namespace=namespace)

    logger.info(
        "Network egress rules applied: %d allowed IPs, %d blocked IPs, allowed ports %s, blocked ports %s",
        len(rules.allowed_ips), len(rules.blocked_ips), rules.allowed_ports, rules.blocked_ports,
    )


def _apply_ingress_rules(rules: ResolvedNetworkRules, namespace: str | None = None) -> None:
    """Apply inbound network rules inside the current namespace."""
    _run_iptables_both(["-A", "INPUT", "-m", "state", "--state",
                        "ESTABLISHED,RELATED", "-j", "ACCEPT"], namespace=namespace)

    for ip in rules.blocked_ips:
        _run_iptables_for_ip(["-A", "INPUT", "-s", ip, "-j", "DROP"], ip, namespace=namespace)
    for port in rules.blocked_ports:
        _run_iptables_both(
            ["-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "DROP"],
            namespace=namespace,
        )

    if not rules.default_deny:
        _run_iptables_both(["-A", "INPUT", "-j", "ACCEPT"], namespace=namespace)
        return

    if rules.allowed_ips and rules.allowed_ports:
        for ip in rules.allowed_ips:
            for port in rules.allowed_ports:
                _run_iptables_for_ip([
                    "-A", "INPUT", "-s", ip, "-p", "tcp",
                    "--dport", str(port), "-j", "ACCEPT",
                ], ip, namespace=namespace)
    elif rules.allowed_ips:
        for ip in rules.allowed_ips:
            _run_iptables_for_ip(["-A", "INPUT", "-s", ip, "-j", "ACCEPT"], ip, namespace=namespace)
    elif rules.allowed_ports:
        for port in rules.allowed_ports:
            _run_iptables_both(
                ["-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"],
                namespace=namespace,
            )

    _run_iptables_both(["-A", "INPUT", "-j", "DROP"], namespace=namespace)

    logger.info(
        "Network ingress rules applied: %d allowed IPs, %d blocked IPs, allowed ports %s, blocked ports %s",
        len(rules.allowed_ips),
        len(rules.blocked_ips),
        rules.allowed_ports,
        rules.blocked_ports,
    )


def apply_iptables_rules(
    egress_rules: ResolvedNetworkRules,
    ingress_rules: ResolvedNetworkRules,
    namespace: str | None = None,
) -> None:
    """Apply iptables rules inside the current (unshared) network namespace."""
    setup_loopback(namespace=namespace)
    _apply_egress_rules(egress_rules, namespace=namespace)
    _apply_ingress_rules(ingress_rules, namespace=namespace)


def setup_network_isolation(policy: NetworkPolicy, namespace: str | None = None) -> None:
    """Top-level entry point for network isolation setup.

    Called from within the sandbox namespace after bwrap --unshare-net or
    from the host against a pre-created named namespace.
    """
    if policy.mode == NetworkMode.HOST:
        logger.info("Network mode is 'host', skipping isolation")
        return

    egress_rules = build_egress_rules(policy.egress)
    ingress_rules = build_ingress_rules(policy.ingress)
    apply_iptables_rules(egress_rules, ingress_rules, namespace=namespace)
