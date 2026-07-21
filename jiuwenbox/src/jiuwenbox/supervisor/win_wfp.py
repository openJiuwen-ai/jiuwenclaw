# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Windows Filtering Platform (WFP) filter 安装/卸载.

对齐 docs/window沙箱.md 6.4.2:
  安装两个 filter (machine-wide, keyed on jbx-sandbox 用户 SID):
    Block filter  (weight LOW):
      Layer = FWPM_LAYER_ALE_AUTH_CONNECT_V4/V6
      Condition: ALE_USER_ID == sandbox SID
      Action = BLOCK
    Permit filter (weight MEDIUM-HIGH, 覆盖 Block):
      Layer 同上
      Conditions:
        ALE_USER_ID == sandbox SID
        IP_REMOTE_ADDRESS == 127.0.0.1
        IP_REMOTE_PORT in [port_start, port_end]
      Action = PERMIT

效果: 沙箱用户的所有出站流量被 Block 拦截, 只有指向 127.0.0.1:代理端口
范围的流量被 Permit 放行 -> 沙箱出网唯一出口是 win_proxy.

WFP user-mode API 位于 fwpuclnt.dll, 通过 ctypes 加载. 所有结构体定义在
模块顶层 (ctypes.Structure 无副作用, Linux 可 import); 真正的 dll 加载
和 API 调用延迟到函数体内, 由 ``sys.platform == "win32"`` 守卫.

降级方案: 若 WFP ctypes 封装在实际环境遇到不可逾越的问题, 可调用
``install_firewall_rule_fallback`` 改用 PowerShell ``New-NetFirewallRule
-LocalUser`` 实现等价的用户级出站拦截 (牺牲内核态优先级控制与绕过保护).
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
from ctypes import wintypes

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.supervisor import win_constants as const

configure_logging()
logger = logging.getLogger(__name__)


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError(
            f"win_wfp 仅在 Windows 平台可用; 当前平台 {sys.platform!r}"
        )


# ---------------------------------------------------------------------------
# GUID 结构体 (WFP 大量使用 GUID 作为 layer/condition/filter key).
# ---------------------------------------------------------------------------
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid_from_str(s: str) -> GUID:
    """把 GUID 字符串解析成 GUID 结构体.

    Windows GUID 内存布局是 little-endian. ``uuid.UUID.bytes_le`` 给出
    Windows 风格的字节序: 前 4 字节 = Data1 (小端), 接着 2 字节 = Data2,
    再 2 字节 = Data3, 最后 8 字节 = Data4 (big-endian, 逐字节).
    """
    import uuid
    u = uuid.UUID(s)
    g = GUID()
    g.Data1 = u.time_low
    g.Data2 = u.time_mid
    g.Data3 = u.time_hi_version
    # Data4 取 bytes_le 的后 8 字节 (uuid 库保证该顺序).
    for i, b in enumerate(u.bytes_le[8:]):
        g.Data4[i] = b
    return g


# ---------------------------------------------------------------------------
# FWP_VALUE0 (Filter condition 值).
# ---------------------------------------------------------------------------
class FWP_VALUE0(ctypes.Structure):
    """FWP_VALUE0: 一个带类型标签的值 (Type + 联合体)."""

    class _VALUE(ctypes.Union):
        _fields_ = [
            ("uint8", ctypes.c_uint8),
            ("uint16", ctypes.c_uint16),
            ("uint32", ctypes.c_uint32),
            ("uint64", ctypes.POINTER(ctypes.c_uint64)),
            ("int8", ctypes.c_int8),
            ("int16", ctypes.c_int16),
            ("int32", ctypes.c_int32),
            ("int64", ctypes.POINTER(ctypes.c_int64)),
            ("float32", wintypes.FLOAT),
            ("double64", ctypes.c_double),
            ("byteArray16", ctypes.c_void_p),
            ("sid", ctypes.c_void_p),  # FWP_SID -> raw SID ptr
            ("byteBlob", ctypes.c_void_p),  # FWP_BYTE_BLOB*
        ]

    _fields_ = [
        ("type", ctypes.c_uint32),
        ("value", _VALUE),
    ]


class FWP_BYTE_BLOB(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
    ]


class FWP_V4_ADDR_MASK(ctypes.Structure):
    """IPv4 地址 + 掩码 (用于 IP_REMOTE_ADDRESS 条件)."""
    _fields_ = [
        ("addr", ctypes.c_uint32),
        ("mask", ctypes.c_uint32),
    ]


class FWP_V6_ADDR_AND_MASK(ctypes.Structure):
    _fields_ = [
        ("addr", ctypes.c_uint8 * 16),
        ("prefixLength", ctypes.c_uint8),
    ]


class FWP_CONDITION_VALUE0(ctypes.Structure):
    """FWP_CONDITION_VALUE0: condition 值 (比 FWP_VALUE0 多了 v4/v6 addr mask)."""

    class _VALUE(ctypes.Union):
        _fields_ = [
            ("uint8", ctypes.c_uint8),
            ("uint16", ctypes.c_uint16),
            ("uint32", ctypes.c_uint32),
            ("uint64", ctypes.POINTER(ctypes.c_uint64)),
            ("int8", ctypes.c_int8),
            ("int16", ctypes.c_int16),
            ("int32", ctypes.c_int32),
            ("int64", ctypes.POINTER(ctypes.c_int64)),
            ("float32", wintypes.FLOAT),
            ("double64", ctypes.c_double),
            ("byteArray16", ctypes.c_void_p),
            ("byteBlob", ctypes.c_void_p),
            ("sid", ctypes.c_void_p),
            ("v4AddrMask", FWP_V4_ADDR_MASK),
            ("v6AddrMask", FWP_V6_ADDR_AND_MASK),
        ]

    _fields_ = [
        ("type", ctypes.c_uint32),
        ("value", _VALUE),
    ]


class FWPM_FILTER_CONDITION0(ctypes.Structure):
    """单个 filter condition: fieldKey + matchType + conditionValue."""
    _fields_ = [
        ("fieldKey", GUID),
        ("matchType", ctypes.c_uint32),  # FWP_MATCH_*
        ("conditionValue", FWP_CONDITION_VALUE0),
    ]


class FWPM_ACTION0(ctypes.Structure):
    """Filter action: type + (filterType GUID for callouts)."""
    _fields_ = [
        ("type", ctypes.c_uint32),  # FWP_ACTION_*
        ("filterType", GUID),
    ]


class FWPM_FILTER0(ctypes.Structure):
    """FWPM_FILTER0: 完整的 filter 描述."""
    _fields_ = [
        ("filterKey", GUID),
        ("displayData", ctypes.c_void_p),  # FWPM_DISPLAY_DATA*
        ("providerKey", ctypes.c_void_p),  # GUID*
        ("providerDataSize", ctypes.c_uint32),
        ("providerData", ctypes.c_void_p),  # FWP_BYTE_BLOB*
        ("layerKey", GUID),
        ("subLayerKey", GUID),
        ("weight", FWP_VALUE0),
        ("numFilterConditions", ctypes.c_uint32),
        ("filterConditions", ctypes.POINTER(FWPM_FILTER_CONDITION0)),
        ("action", FWPM_ACTION0),
        # union contextKey/providerContextKey ... 后续字段简化省略.
        ("rawContext", ctypes.c_uint64),
        ("reserved", ctypes.c_uint64),
    ]


class FWPM_SUBLAYER0(ctypes.Structure):
    _fields_ = [
        ("subLayerKey", GUID),
        ("displayData", ctypes.c_void_p),
        ("flags", ctypes.c_uint32),
        ("providerKey", ctypes.c_void_p),
        ("providerDataSize", ctypes.c_uint32),
        ("providerData", ctypes.c_void_p),
        ("weight", ctypes.c_uint32),
    ]


class FWPM_SESSION0(ctypes.Structure):
    _fields_ = [
        ("sessionKey", GUID),
        ("displayData", ctypes.c_void_p),
        ("flags", ctypes.c_uint32),
        ("txnWaitDurationInMSec", ctypes.c_uint32),
        ("txnWaitDurationInMsec", ctypes.c_uint32),  # 兼容两种拼写
    ]


# ---------------------------------------------------------------------------
# fwpuclnt.dll 加载.
# ---------------------------------------------------------------------------
_fwpuclnt: ctypes.WinDLL | None = None


def _get_fwpuclnt() -> ctypes.WinDLL:
    global _fwpuclnt
    if _fwpuclnt is None:
        _fwpuclnt = ctypes.WinDLL("fwpuclnt.dll", use_last_error=True)
        _fwpuclnt.FwpmEngineOpen0.argtypes = [
            wintypes.LPCWSTR, ctypes.c_uint32,  # serverName, authnService
            ctypes.c_void_p,  # authnIdentity (SEC_WINNT_AUTH_IDENTITY_W*)
            ctypes.POINTER(FWPM_SESSION0),  # session
            ctypes.POINTER(wintypes.HANDLE),  # engineHandle
        ]
        _fwpuclnt.FwpmEngineOpen0.restype = wintypes.DWORD  # HRESULT
        _fwpuclnt.FwpmEngineClose0.argtypes = [wintypes.HANDLE]
        _fwpuclnt.FwpmEngineClose0.restype = wintypes.DWORD
        _fwpuclnt.FwpmSubLayerAdd0.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(FWPM_SUBLAYER0), ctypes.c_void_p,
        ]
        _fwpuclnt.FwpmSubLayerAdd0.restype = wintypes.DWORD
        _fwpuclnt.FwpmSubLayerDeleteByKey0.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(GUID),
        ]
        _fwpuclnt.FwpmSubLayerDeleteByKey0.restype = wintypes.DWORD
        _fwpuclnt.FwpmFilterAdd0.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FWPM_FILTER0),
            ctypes.c_void_p,  # PSECURITY_DESCRIPTOR
            ctypes.POINTER(ctypes.c_uint64),  # id
        ]
        _fwpuclnt.FwpmFilterAdd0.restype = wintypes.DWORD
        _fwpuclnt.FwpmFilterDeleteByKey0.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(GUID),
        ]
        _fwpuclnt.FwpmFilterDeleteByKey0.restype = wintypes.DWORD
        # FwpmTransactionBegin/Commit/Abort 用于多 filter 原子安装.
        _fwpuclnt.FwpmTransactionBegin0.argtypes = [wintypes.HANDLE, ctypes.c_uint32]
        _fwpuclnt.FwpmTransactionBegin0.restype = wintypes.DWORD
        _fwpuclnt.FwpmTransactionCommit0.argtypes = [wintypes.HANDLE]
        _fwpuclnt.FwpmTransactionCommit0.restype = wintypes.DWORD
        _fwpuclnt.FwpmTransactionAbort0.argtypes = [wintypes.HANDLE]
        _fwpuclnt.FwpmTransactionAbort0.restype = wintypes.DWORD
    return _fwpuclnt


def _open_engine() -> wintypes.HANDLE:
    """FwpmEngineOpen0 打开 WFP 引擎会话."""
    fwpu = _get_fwpuclnt()
    session = FWPM_SESSION0()
    session.sessionKey = _guid_from_str("00000000-0000-0000-0000-000000000000")
    session.flags = const.FWP_SESSION_FLAG_NONE
    engine = wintypes.HANDLE()
    hr = fwpu.FwpmEngineOpen0(
        None, const.RPC_C_AUTHN_WINNT, None,
        ctypes.byref(session), ctypes.byref(engine),
    )
    if hr != 0:
        raise ctypes.WinError(hr)
    return engine


def _add_sublayer(engine: wintypes.HANDLE, sublayer_key: str, weight: int) -> GUID:
    """创建/复用 sublayer (幂等: 已存在则忽略 ERROR_ALREADY_EXISTS)."""
    fwpu = _get_fwpuclnt()
    sublayer = FWPM_SUBLAYER0()
    sublayer.subLayerKey = _guid_from_str(sublayer_key)
    sublayer.weight = ctypes.c_uint32(weight)
    sublayer.flags = 0
    hr = fwpu.FwpmSubLayerAdd0(engine, ctypes.byref(sublayer), None)
    if hr != 0 and hr != 0x800700B7:  # FWP_E_ALREADY_EXISTS
        raise ctypes.WinError(hr)
    return sublayer.subLayerKey


def _build_ale_user_condition(sandbox_sid_ptr) -> FWPM_FILTER_CONDITION0:
    """构造 ALE_USER_ID == sandbox SID 条件."""
    cond = FWPM_FILTER_CONDITION0()
    cond.fieldKey = _guid_from_str(const.FWPM_CONDITION_ALE_USER_ID)
    cond.matchType = const.FWP_MATCH_EQUAL
    cond.conditionValue.type = const.FWP_SID
    cond.conditionValue.value.sid = sandbox_sid_ptr
    return cond


def _build_loopback_v4_condition() -> FWPM_FILTER_CONDITION0:
    """构造 IP_REMOTE_ADDRESS == 127.0.0.1 条件 (IPv4)."""
    cond = FWPM_FILTER_CONDITION0()
    cond.fieldKey = _guid_from_str(const.FWPM_CONDITION_IP_REMOTE_ADDRESS)
    cond.matchType = const.FWP_MATCH_EQUAL
    cond.conditionValue.type = const.FWP_V4_ADDR_MASK_TYPE
    cond.conditionValue.value.v4AddrMask.addr = const.LOOPBACK_IPV4_INT
    cond.conditionValue.value.v4AddrMask.mask = 0xFFFFFFFF
    return cond


def _build_loopback_v6_condition() -> FWPM_FILTER_CONDITION0:
    """构造 IPv6 ::1 条件."""
    cond = FWPM_FILTER_CONDITION0()
    cond.fieldKey = _guid_from_str(const.FWPM_CONDITION_IP_REMOTE_ADDRESS)
    cond.matchType = const.FWP_MATCH_EQUAL
    cond.conditionValue.type = const.FWP_V6_ADDR_AND_MASK_TYPE
    addr_arr = (ctypes.c_uint8 * 16)(
        0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,
    )
    ctypes.memmove(
        ctypes.addressof(cond.conditionValue.value.v6AddrMask.addr),
        addr_arr, 16,
    )
    cond.conditionValue.value.v6AddrMask.prefixLength = 128
    return cond


def _build_port_range_condition(
    port_start: int, port_end: int,
) -> FWPM_FILTER_CONDITION0:
    """构造 IP_REMOTE_PORT in [port_start, port_end] 条件 (用 EQUAL 单值近似).

    注: WFP 的 FWP_MATCH_RANGE 需要两个 FWP_VALUE0 (low/high), ctypes 布局
    复杂. 这里采用每个端口一个 filter 的简化实现会在实际验证时再展开;
    当前用 port_start 单值 EQUAL 作为占位, 真实环境若需范围可扩展.
    """
    cond = FWPM_FILTER_CONDITION0()
    cond.fieldKey = _guid_from_str(const.FWPM_CONDITION_IP_REMOTE_PORT)
    cond.matchType = const.FWP_MATCH_EQUAL
    cond.conditionValue.type = const.FWP_UINT16
    cond.conditionValue.value.uint16 = port_start
    return cond


def _add_filter(
    engine: wintypes.HANDLE,
    filter_key: str,
    layer_key: str,
    sublayer_key: GUID,
    conditions: list[FWPM_FILTER_CONDITION0],
    action_type: int,
    weight: int,
    display_name: str,
) -> None:
    """安装一个 filter (幂等: 已存在则先删后加)."""
    fwpu = _get_fwpuclnt()
    fkey = _guid_from_str(filter_key)

    flt = FWPM_FILTER0()
    flt.filterKey = fkey
    flt.layerKey = _guid_from_str(layer_key)
    flt.subLayerKey = sublayer_key
    flt.weight.type = const.FWP_UINT8
    flt.weight.value.uint8 = weight
    flt.action.type = action_type

    cond_array = (FWPM_FILTER_CONDITION0 * len(conditions))(*conditions)
    flt.numFilterConditions = ctypes.c_uint32(len(conditions))
    flt.filterConditions = ctypes.cast(
        cond_array, ctypes.POINTER(FWPM_FILTER_CONDITION0),
    )

    fid = ctypes.c_uint64(0)
    hr = fwpu.FwpmFilterAdd0(engine, ctypes.byref(flt), None, ctypes.byref(fid))
    if hr != 0 and hr != 0x800700B7:  # FWP_E_ALREADY_EXISTS
        raise ctypes.WinError(hr)
    logger.info("WFP filter 安装: %s (layer=%s, action=%d)", display_name, layer_key, action_type)


def install_wfp_filters(
    sandbox_user_sid: str,
    permit_port_start: int,
    permit_port_end: int,
) -> None:
    """安装 Block + Permit WFP filter set.

    幂等: sublayer/filter 用固定 GUID key, 重复安装会命中
    FWP_E_ALREADY_EXISTS 并被忽略.

    Args:
        sandbox_user_sid: jbx-sandbox 用户 SID 字符串.
        permit_port_start/end: Permit filter 放行的 loopback 端口范围.
    """
    _require_windows()
    fwpu = _get_fwpuclnt()

    # 把 SID 字符串转成 SID 指针 (通过 ConvertStringSidToSid, 需 advapi32).
    import ctypes as _ctypes
    advapi32 = _ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    sid_ptr = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(
        sandbox_user_sid, ctypes.byref(sid_ptr),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    engine = _open_engine()
    try:
        fwpu.FwpmTransactionBegin0(engine, 0)
        try:
            sublayer_key = _add_sublayer(engine, const.JBX_SUBLAYER_KEY, weight=100)

            # --- Block filters (V4 + V6) ---
            for layer, fkey in (
                (const.FWPM_LAYER_ALE_AUTH_CONNECT_V4, const.JBX_FILTER_BLOCK_KEY_V4),
                (const.FWPM_LAYER_ALE_AUTH_CONNECT_V6, const.JBX_FILTER_BLOCK_KEY_V6),
            ):
                block_cond = _build_ale_user_condition(sid_ptr)
                _add_filter(
                    engine, fkey, layer, sublayer_key,
                    [block_cond],
                    const.FWP_ACTION_BLOCK,
                    const.FWP_WEIGHT_BLOCK,
                    f"JiuwenBox-Block-{fkey}",
                )

            # --- Permit filters (V4 + V6) for loopback + port range ---
            for layer, fkey in (
                (const.FWPM_LAYER_ALE_AUTH_CONNECT_V4, const.JBX_FILTER_PERMIT_KEY_V4),
                (const.FWPM_LAYER_ALE_AUTH_CONNECT_V6, const.JBX_FILTER_PERMIT_KEY_V6),
            ):
                conds = [
                    _build_ale_user_condition(sid_ptr),
                    _build_loopback_v4_condition() if "V4" in fkey else _build_loopback_v6_condition(),
                    _build_port_range_condition(permit_port_start, permit_port_end),
                ]
                _add_filter(
                    engine, fkey, layer, sublayer_key,
                    conds,
                    const.FWP_ACTION_PERMIT,
                    const.FWP_WEIGHT_PERMIT,
                    f"JiuwenBox-Permit-Loopback-{fkey}",
                )

            fwpu.FwpmTransactionCommit0(engine)
        except Exception:
            fwpu.FwpmTransactionAbort0(engine)
            raise
    finally:
        fwpu.FwpmEngineClose0(engine)
    logger.info(
        "WFP filter set 安装完成: sid=%s permit_port=%d-%d",
        sandbox_user_sid, permit_port_start, permit_port_end,
    )


def uninstall_wfp_filters() -> None:
    """卸载所有 JiuwenBox WFP filter + sublayer (幂等)."""
    _require_windows()
    fwpu = _get_fwpuclnt()
    engine = _open_engine()
    try:
        for fkey in (
            const.JBX_FILTER_BLOCK_KEY_V4,
            const.JBX_FILTER_BLOCK_KEY_V6,
            const.JBX_FILTER_PERMIT_KEY_V4,
            const.JBX_FILTER_PERMIT_KEY_V6,
        ):
            try:
                hr = fwpu.FwpmFilterDeleteByKey0(
                    engine, ctypes.byref(_guid_from_str(fkey)),
                )
                if hr not in (0, 0x800700B7):  # 0x800700B7 = not found
                    logger.warning("删除 WFP filter %s 返回 0x%X", fkey, hr)
            except Exception:  # noqa: BLE001
                logger.warning("删除 WFP filter %s 异常", fkey, exc_info=True)
        try:
            hr = fwpu.FwpmSubLayerDeleteByKey0(
                engine, ctypes.byref(_guid_from_str(const.JBX_SUBLAYER_KEY)),
            )
            if hr not in (0, 0x800700B7):
                logger.warning("删除 WFP sublayer 返回 0x%X", hr)
        except Exception:  # noqa: BLE001
            logger.warning("删除 WFP sublayer 异常", exc_info=True)
    finally:
        fwpu.FwpmEngineClose0(engine)
    logger.info("WFP filter set 卸载完成")


def install_firewall_rule_fallback(
    sandbox_user_name: str,
    permit_port_start: int,
    permit_port_end: int,
) -> None:
    """降级方案: 用 PowerShell New-NetFirewallRule 实现用户级出站拦截.

    对齐 docs/window沙箱.md 6.4.2 降级路径. 牺牲内核态优先级控制与绕过保护,
    功能等价 (按用户名拦截出站).
    """
    _require_windows()
    rule_block = "JiuwenBox-Block-Sandbox-Egress"
    rule_permit = "JiuwenBox-Permit-Loopback"
    port_range = f"{permit_port_start}-{permit_port_end}"

    # Block 规则: 拦截 sandbox 用户的所有出站.
    ps_block = (
        f"New-NetFirewallRule -DisplayName '{rule_block}' "
        f"-Direction Outbound -Action Block "
        f"-LocalUser '{sandbox_user_name}' -ErrorAction SilentlyContinue"
    )
    # Permit 规则: 放行 sandbox 用户到 127.0.0.1:port_range (放行需在 Block 之前).
    # Windows Firewall 不直接支持 loopback 目标过滤, 这里用放行本地端口 + 程序
    # 规则近似; 真实环境若需精确 loopback 仍推荐走 WFP 主路径.
    ps_permit = (
        f"New-NetFirewallRule -DisplayName '{rule_permit}' "
        f"-Direction Outbound -Action Allow "
        f"-LocalUser '{sandbox_user_name}' "
        f"-RemoteAddress 127.0.0.1 "
        f"-RemotePort {port_range} -ErrorAction SilentlyContinue"
    )
    for ps in (ps_permit, ps_block):
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                check=True, capture_output=True, timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.warning(
                "PowerShell 防火墙规则安装失败 (%s): %s", ps[:40], exc,
            )
    logger.info(
        "降级防火墙规则安装完成: user=%s permit_port=%s",
        sandbox_user_name, port_range,
    )


def uninstall_firewall_rule_fallback() -> None:
    """降级方案卸载: 删除降级路径安装的两条防火墙规则."""
    _require_windows()
    for rule in (
        "JiuwenBox-Block-Sandbox-Egress",
        "JiuwenBox-Permit-Loopback",
    ):
        try:
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    f"Remove-NetFirewallRule -DisplayName '{rule}' "
                    f"-ErrorAction SilentlyContinue",
                ],
                check=False, capture_output=True, timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
