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


# WFP 专用 HRESULT 段 0x80320xxx (FWP_E_*) 的常见码, 帮助诊断。完整列表见
# fwperr.h。S9: ctypes.WinError(hr) 对高位为 1 的 HRESULT 会 OverflowError
# (FormatError 内部转 signed long 溢出), 掩盖真实错误码。统一用 _wfp_error 抛出。
_WFP_ERROR_NAMES: dict[int, str] = {
    0x80320001: "FWP_E_CALLOUT_NOT_FOUND",
    0x80320002: "FWP_E_CONDITION_NOT_FOUND",
    0x80320003: "FWP_E_FILTER_NOT_FOUND",
    0x80320004: "FWP_E_LAYER_NOT_FOUND",
    0x80320005: "FWP_E_PROVIDER_NOT_FOUND",
    0x80320006: "FWP_E_PROVIDER_CONTEXT_NOT_FOUND",
    0x80320007: "FWP_E_SUBLAYER_NOT_FOUND",
    0x80320008: "FWP_E_NOT_FOUND",
    0x80320009: "FWP_E_ALREADY_EXISTS",
    0x8032000A: "FWP_E_IN_USE",
    0x8032000B: "FWP_E_DUPLICATE_CONDITION",
    0x8032000C: "FWP_E_DUPLICATE_KEYMOD",
    0x8032000D: "FWP_E_IN_USE_LOCKED",
    0x8032000E: "FWP_E_INVALID_PLUGIN",
    0x8032000F: "FWP_E_INVALID_PLUGIN_TRUSTED",
    0x80320010: "FWP_E_DROP_NO_EXEMPT",  # 通用无效参数/类型类
    0x80320011: "FWP_E_NULL_KEY",
    0x80320012: "FWP_E_INVALID_ENUMERATOR",
    0x80320013: "FWP_E_INVALID_FLAGS",
    0x80320014: "FWP_E_INVALID_NET_MASK",
    0x80320015: "FWP_E_INVALID_STATUS",
    0x80320016: "FWP_E_INVALID_RANGE",
    0x80320017: "FWP_E_INVALID_INTERVAL",
    0x80320018: "FWP_E_TOO_MANY_REFERENCES",
    0x80320019: "FWP_E_NAME_NOT_FOUND",
    0x80320020: "FWP_E_INVALID_WEIGHT",
    0x80320021: "FWP_E_MATCH_TYPE_MISMATCH",
    0x80320023: "FWP_E_INVALID_AUTH_VALUE",
    0x80320024: "FWP_E_INVALID_KEY",
    0x80320031: "FWP_E_KEY_NOT_FOUND",
    0x80320032: "FWP_E_FILTER_NOT_FOUND (delete)",
    0xC0360017: "FWP_E_TRUSTED_PACKAGE_MISMATCH",
}


def _wfp_error(hr: int, where: str) -> "OSError":
    """把 WFP HRESULT 转成带明文的 OSError (避免 WinError OverflowError).

    hr 是 Fwpm* API 返回的 DWORD (unsigned), 高位为 1 的 HRESULT (0x80320xxx)
    经 ctypes.WinError 会溢出。这里显式格式化为 0xXXXXXXXX + 已知名, 便于
    实跑定位。system error (位 0x80070000 段) 也兼容 (如 0x80070005 = access denied)。

    落在 0x80320xxx 段但 _WFP_ERROR_NAMES 未登记的码 (如 0x80320027, 多为
    condition value 类型与 layer/field 不匹配类校验错误), 不再裸显示 UNKNOWN,
    而是标注段名 + 提示对照本地 SDK fwperr.h, 便于实跑定位。
    """
    name = _WFP_ERROR_NAMES.get(hr)
    # 位 0x80070000 段 = HRESULT_FROM_WIN32, 取低 16 位是 Win32 错误码。
    if name is None and (hr & 0xFFFF0000) == 0x80070000:
        win32 = hr & 0xFFFF
        _SYS = {5: "ERROR_ACCESS_DENIED", 87: "ERROR_INVALID_PARAMETER",
                1377: "ERROR_MEMBER_IN_ALIAS", 2224: "NERR_UserExists"}
        name = _SYS.get(win32, f"WIN32_{win32}")
    if name is None and 0x80320000 <= hr <= 0x8032FFFF:
        # FWP_E_* 段未登记码: 多为 condition value / filter 结构校验类错误,
        # 精确名需对照本地 SDK fwperr.h (无法在线核实时不臆测写死).
        name = f"FWP_E_UNLISTED_{hr & 0xFFFF:04X} (check fwperr.h)"
    label = name or "UNKNOWN"
    return OSError(f"[{where}] WFP/HRESULT hr=0x{hr:08X} ({label})")


# ---------------------------------------------------------------------------
# GUID 结构体 (WFP 大量使用 GUID 作为 layer/condition/filter key).
# 布局对齐 Windows SDK GUID: { DWORD; WORD; WORD; BYTE[8] }.
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


# 固定 namespace GUID (任意合法 UUID), 用于 uuid5 派生 per-port filter key。
# uuid5 基于 (namespace, name) 哈希, 确定性、不依赖时间/随机, install/uninstall
# 用相同 (base_key, port) 派生出相同 GUID, 保证幂等删。
_PERMIT_FILTER_NAMESPACE = "6f9b2a3c-1d4e-4b5f-8a90-7c2e1b3a4d5f"


def _permit_filter_guid_str(base_key: str, port: int) -> str:
    """为端口 Permit filter 派生确定性 GUID 字符串.

    S9 实跑: 旧版 port_key = f"{base_key}-{port}" 不是合法 UUID (含端口后缀),
    _guid_from_str 里 uuid.UUID(s) 报 ValueError, install/uninstall 全崩。
    改用 uuid5(namespace, f"{base_key}:{port}") 生成合法 GUID, 同 (base,port)
    派生同 GUID, 幂等安装/卸载。
    """
    import uuid
    ns = uuid.UUID(_PERMIT_FILTER_NAMESPACE)
    return str(uuid.uuid5(ns, f"{base_key}:{port}"))


# ---------------------------------------------------------------------------
# FWP_BYTE_BLOB / FWP_V4_ADDR_AND_MASK / FWP_V6_ADDR_AND_MASK.
# 布局对齐 fwptypes.h.
# ---------------------------------------------------------------------------
class FWP_BYTE_ARRAY16(ctypes.Structure):
    _fields_ = [("byteArray16", ctypes.c_uint8 * 16)]


class FWP_BYTE_BLOB(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
    ]


class FWP_V4_ADDR_MASK(ctypes.Structure):
    """IPv4 地址 + 掩码 (FWP_V4_ADDR_AND_MASK, fwptypes.h).

    addr 为 host byte order (NOT network order). 127.0.0.1 = 0x7F000001.
    """
    _fields_ = [
        ("addr", ctypes.c_uint32),
        ("mask", ctypes.c_uint32),
    ]


class FWP_V6_ADDR_AND_MASK(ctypes.Structure):
    _fields_ = [
        ("addr", ctypes.c_uint8 * 16),
        ("prefixLength", ctypes.c_uint8),
    ]


# ---------------------------------------------------------------------------
# FWP_VALUE0 / FWP_CONDITION_VALUE0 (带类型标签的联合体).
# 联合体成员对照 fwptypes.h FWP_VALUE0_/FWP_CONDITION_VALUE0_ union:
# 数值标量 + 指针成员 (byteArray16*/byteBlob*/sid*/sd*/v4AddrMask*/...).
# 指针成员用 c_void_p (x64 8B), 标量成员按 SDK 类型. 联合体尺寸 = max(成员).
# ---------------------------------------------------------------------------
class FWP_VALUE0(ctypes.Structure):
    """FWP_VALUE0: 一个带类型标签的值 (Type + 联合体)."""

    class _VALUE(ctypes.Union):
        _fields_ = [
            ("uint8", ctypes.c_uint8),
            ("uint16", ctypes.c_uint16),
            ("uint32", ctypes.c_uint32),
            ("int8", ctypes.c_int8),
            ("int16", ctypes.c_int16),
            ("int32", ctypes.c_int32),
            ("float32", wintypes.FLOAT),
            ("double64", ctypes.c_double),
            # 指针成员 (x64 8B): uint64/int64 在 SDK 是 UINT64*/INT64* (指向值).
            ("uint64", ctypes.POINTER(ctypes.c_uint64)),
            ("int64", ctypes.POINTER(ctypes.c_int64)),
            ("byteArray16", ctypes.c_void_p),   # FWP_BYTE_ARRAY16*
            ("byteBlob", ctypes.c_void_p),      # FWP_BYTE_BLOB*
            ("sid", ctypes.c_void_p),           # SID*
            ("sd", ctypes.c_void_p),            # FWP_BYTE_BLOB* (SECURITY_DESCRIPTOR)
            ("unicodeString", ctypes.c_void_p),  # LPWSTR
            ("byteArray6", ctypes.c_void_p),    # FWP_BYTE_ARRAY6*
        ]

    _fields_ = [
        ("type", ctypes.c_uint32),
        ("value", _VALUE),
    ]


class FWP_CONDITION_VALUE0(ctypes.Structure):
    """FWP_CONDITION_VALUE0: condition 值 (比 FWP_VALUE0 多 v4/v6 addr mask + range)."""

    class _VALUE(ctypes.Union):
        _fields_ = [
            ("uint8", ctypes.c_uint8),
            ("uint16", ctypes.c_uint16),
            ("uint32", ctypes.c_uint32),
            ("int8", ctypes.c_int8),
            ("int16", ctypes.c_int16),
            ("int32", ctypes.c_int32),
            ("float32", wintypes.FLOAT),
            ("double64", ctypes.c_double),
            ("uint64", ctypes.POINTER(ctypes.c_uint64)),
            ("int64", ctypes.POINTER(ctypes.c_int64)),
            ("byteArray16", ctypes.c_void_p),
            ("byteBlob", ctypes.c_void_p),
            ("sid", ctypes.c_void_p),
            ("sd", ctypes.c_void_p),
            ("unicodeString", ctypes.c_void_p),
            ("byteArray6", ctypes.c_void_p),
            # v4/v6/range 在 SDK 是 *指针* (FWP_V4_ADDR_AND_MASK* 等),
            # 用 c_void_p 保持 8B 对齐; 实际构造时用 ctypes.cast 指向局部实例.
            ("v4AddrMask", ctypes.c_void_p),
            ("v6AddrMask", ctypes.c_void_p),
            ("rangeValue", ctypes.c_void_p),
        ]

    _fields_ = [
        ("type", ctypes.c_uint32),
        ("value", _VALUE),
    ]


class FWP_RANGE0(ctypes.Structure):
    """FWP_RANGE0: 范围匹配的低/高值 (FWP_MATCH_RANGE 用)."""
    _fields_ = [
        ("valueLow", FWP_VALUE0),
        ("valueHigh", FWP_VALUE0),
    ]


# ---------------------------------------------------------------------------
# FWPM_DISPLAY_DATA0 (内嵌结构体, 两 wchar_t* 指针, x64 16B).
# FWPM_FILTER0/SUBLAYER0/SESSION0 都内嵌它, 不能用 c_void_p (8B) 否则后续
# 字段全部错位 (review MAJOR #6).
# ---------------------------------------------------------------------------
class FWPM_DISPLAY_DATA0(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_wchar_p),
        ("description", ctypes.c_wchar_p),
    ]


class FWPM_FILTER_CONDITION0(ctypes.Structure):
    """单个 filter condition: fieldKey + matchType + conditionValue."""
    _fields_ = [
        ("fieldKey", GUID),
        ("matchType", ctypes.c_uint32),  # FWP_MATCH_*
        ("conditionValue", FWP_CONDITION_VALUE0),
    ]


class _FWPM_ACTION0_UNION(ctypes.Union):
    """FWPM_ACTION0 内嵌 union: {GUID filterType; GUID calloutKey}.

    两个成员都是 GUID (16B), union = 16B.
    """
    _fields_ = [
        ("filterType", GUID),   # FWP_ACTION_FLAG_CALLOUT 时是 filterType
        ("calloutKey", GUID),   # 否则是 calloutKey
    ]


class FWPM_ACTION0(ctypes.Structure):
    """Filter action: type(4B + 4B pad) + union(16B) = 24B (x64).

    对齐 windows-sys FWPM_ACTION0 repr(C): type + Anonymous union.
    BLOCK/PERMIT 只用 type, union 留空 (ctypes 零初始化).
    """
    _fields_ = [
        ("type", ctypes.c_uint32),          # FWP_ACTION_*
        ("Anonymous", _FWPM_ACTION0_UNION),
    ]


# ---------------------------------------------------------------------------
# FWPM_FILTER0 (fwpmtypes.h). 布局严格对齐 windows-sys SDK (repr(C), x64):
#   filterKey(GUID,16@0) displayData(FWPM_DISPLAY_DATA0,16@16) flags(UINT32,4@32)
#   pad(4@36) providerKey(GUID*,8@40) providerData(FWP_BYTE_BLOB,16@48)
#   layerKey(GUID,16@64) subLayerKey(GUID,16@80) weight(FWP_VALUE0,16@96)
#   numFilterConditions(UINT32,4@112) pad(4@116)
#   filterCondition(FWPM_FILTER_CONDITION0*,8@120) action(FWPM_ACTION0,24@128)
#   Anonymous(FWPM_FILTER0_0,16@152)  union{UINT64 rawContext; GUID providerContextKey}
#   reserved(GUID*,8@168) filterId(UINT64,8@176) effectiveWeight(FWP_VALUE0,16@184)
#   sizeof = 200B (x64).
#
# S9 旧 bug: rawContext 写成 c_uint64 (8B), 实际 SDK 是 16B union
# (UINT64 与 GUID 取 max=16B). 旧版 sizeof(flt)=192 少 8B, BFE 收到错误
# 大小的结构体经 RPC 返回 RPC_X_BAD_STUB_DATA (hr=0x6F7). 改为 16B union.
# ---------------------------------------------------------------------------
class _FWPM_FILTER0_UNION(ctypes.Union):
    """FWPM_FILTER0 内嵌 union: {UINT64 rawContext; GUID providerContextKey}.

    UINT64 (8B) 与 GUID (16B) 取 max=16B, 8B 对齐.
    """
    _fields_ = [
        ("rawContext", ctypes.c_uint64),
        ("providerContextKey", GUID),
    ]


class FWPM_FILTER0(ctypes.Structure):
    _fields_ = [
        ("filterKey", GUID),
        ("displayData", FWPM_DISPLAY_DATA0),       # 内嵌, 非 c_void_p
        ("flags", ctypes.c_uint32),
        ("providerKey", ctypes.c_void_p),          # GUID*
        ("providerData", FWP_BYTE_BLOB),          # 内嵌, 非 providerDataSize+ptr
        ("layerKey", GUID),
        ("subLayerKey", GUID),
        ("weight", FWP_VALUE0),
        ("numFilterConditions", ctypes.c_uint32),
        ("filterCondition", ctypes.POINTER(FWPM_FILTER_CONDITION0)),
        ("action", FWPM_ACTION0),
        ("Anonymous", _FWPM_FILTER0_UNION),       # union{rawContext; providerContextKey}
        ("reserved", ctypes.c_void_p),            # GUID*
        ("filterId", ctypes.c_uint64),            # BFE 填, 调用方不设
        ("effectiveWeight", FWP_VALUE0),          # BFE 填
    ]


class FWPM_SUBLAYER0(ctypes.Structure):
    """FWPM_SUBLAYER0 (fwpmtypes.h): subLayerKey + displayData(内嵌) +
    flags + providerKey(GUID*) + providerData(内嵌) + weight(UINT16).
    weight 是 UINT16, 旧版误为 c_uint32; 且无 providerDataSize 字段."""
    _fields_ = [
        ("subLayerKey", GUID),
        ("displayData", FWPM_DISPLAY_DATA0),
        ("flags", ctypes.c_uint32),
        ("providerKey", ctypes.c_void_p),   # GUID*
        ("providerData", FWP_BYTE_BLOB),
        ("weight", ctypes.c_uint16),
    ]


class FWPM_SESSION0(ctypes.Structure):
    """FWPM_SESSION0 (fwpmtypes.h): sessionKey + displayData(内嵌) +
    flags + txnWaitTimeoutInMSec + processId/sid/username/kernelMode
    (BFE 填, 但需占位保结构体尺寸). 旧版 displayData 误为 c_void_p、重复
    txnWait 字段、缺输出字段."""
    _fields_ = [
        ("sessionKey", GUID),
        ("displayData", FWPM_DISPLAY_DATA0),
        ("flags", ctypes.c_uint32),
        ("txnWaitTimeoutInMSec", ctypes.c_uint32),
        ("processId", wintypes.DWORD),       # BFE 填
        ("sid", ctypes.c_void_p),            # SID*, BFE 填
        ("username", ctypes.c_wchar_p),     # wchar_t*, BFE 填
        ("kernelMode", wintypes.BOOL),       # BFE 填
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
        raise _wfp_error(hr, "FwpmEngineOpen0")
    return engine


def _add_sublayer(engine: wintypes.HANDLE, sublayer_key: str, weight: int) -> GUID:
    """创建/复用 sublayer (幂等: 已存在则忽略 ERROR_ALREADY_EXISTS).

    S9 实跑: displayData.name 不能为 NULL (BFE 报 FWP_E_INVALID_AUTH_VALUE=
    0x80320023, 文档 0x80320023 = "displayData.name field cannot be null")。
    旧版未设 displayData → 报错。显式设 name/description。
    """
    fwpu = _get_fwpuclnt()
    sublayer = FWPM_SUBLAYER0()
    sublayer.subLayerKey = _guid_from_str(sublayer_key)
    # displayData.name 是 c_wchar_p, 必须赋 str (非 None); 否则 BFE 拒绝。
    sublayer.displayData.name = "JiuwenBoxSandboxSublayer"
    sublayer.displayData.description = "JiuwenBox sandbox egress filter sublayer"
    sublayer.flags = 0
    sublayer.weight = weight  # 字段已是 c_uint16, 直接赋 int
    hr = fwpu.FwpmSubLayerAdd0(engine, ctypes.byref(sublayer), None)
    if hr != 0 and hr != 0x800700B7:  # FWP_E_ALREADY_EXISTS
        raise _wfp_error(hr, "FwpmSubLayerAdd0")
    return sublayer.subLayerKey


def _build_loopback_v4_condition() -> "tuple[FWPM_FILTER_CONDITION0, FWP_V4_ADDR_MASK]":
    """构造 IP_REMOTE_ADDRESS == 127.0.0.1 条件 (IPv4).

    SDK 的 FWP_CONDITION_VALUE0 里 v4AddrMask 是 *指针* (FWP_V4_ADDR_AND_MASK*),
    不能直接内嵌赋值; 需返回一个存活的局部 FWP_V4_ADDR_MASK 实例, 由调用方
    保持其生命周期到 FwpmFilterAdd0 返回 (否则指针悬垂). addr 用 host byte
    order 0x7F000001 (review CRITICAL #4: 旧值 0x0100007F 是网络序, 匹配
    1.0.0.127).
    """
    cond = FWPM_FILTER_CONDITION0()
    cond.fieldKey = _guid_from_str(const.FWPM_CONDITION_IP_REMOTE_ADDRESS)
    cond.matchType = const.FWP_MATCH_EQUAL
    cond.conditionValue.type = const.FWP_V4_ADDR_MASK
    addr_mask = FWP_V4_ADDR_MASK()
    addr_mask.addr = const.LOOPBACK_IPV4_INT  # 0x7F000001 = 127.0.0.1 host order
    addr_mask.mask = 0xFFFFFFFF
    cond.conditionValue.value.v4AddrMask = ctypes.cast(
        ctypes.pointer(addr_mask), ctypes.c_void_p,
    ).value
    return cond, addr_mask


def _build_loopback_v6_condition() -> "tuple[FWPM_FILTER_CONDITION0, FWP_V6_ADDR_AND_MASK]":
    """构造 IPv6 ::1 条件. 返回 (cond, 存活的局部实例)."""
    cond = FWPM_FILTER_CONDITION0()
    cond.fieldKey = _guid_from_str(const.FWPM_CONDITION_IP_REMOTE_ADDRESS)
    cond.matchType = const.FWP_MATCH_EQUAL
    cond.conditionValue.type = const.FWP_V6_ADDR_AND_MASK
    addr_mask = FWP_V6_ADDR_AND_MASK()
    addr_arr = (ctypes.c_uint8 * 16)(
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
    )
    ctypes.memmove(
        ctypes.addressof(addr_mask.addr), addr_arr, 16,
    )
    addr_mask.prefixLength = 128
    cond.conditionValue.value.v6AddrMask = ctypes.cast(
        ctypes.pointer(addr_mask), ctypes.c_void_p,
    ).value
    return cond, addr_mask


def _build_ale_user_condition(sandbox_user_sid: str) -> "tuple[FWPM_FILTER_CONDITION0, object]":
    """构造 ALE_USER_ID 条件 (基于 SECURITY_DESCRIPTOR, 非 裸 SID).

    SDK: FWPM_CONDITION_ALE_USER_ID 的 condition 值类型是
    FWP_SECURITY_DESCRIPTOR_TYPE (FWP_BYTE_BLOB* 指向自相关 SD 字节),
    而非 FWP_SID. BFE 评估时检查 SD 的 DACL 是否对发起连接的用户授予
    FWP_ACTRL_MATCH_FILTER 访问权 (SDK 示例: Permitting and Blocking
    Applications and Users, BuildSecurityDescriptorW + FWP_ACTRL_MATCH_FILTER).

    用 win32security 构造自相关 SD: DACL 授 jbx-sandbox 用户
    FWP_ACTRL_MATCH_FILTER. 返回 (cond, _keeps), _keeps 持有 SD 字节与
    FWP_BYTE_BLOB 实例引用, 调用方需保持其生命周期到 FwpmFilterAdd0 返回.
    """
    cond = FWPM_FILTER_CONDITION0()
    cond.fieldKey = _guid_from_str(const.FWPM_CONDITION_ALE_USER_ID)
    cond.matchType = const.FWP_MATCH_EQUAL
    cond.conditionValue.type = const.FWP_SECURITY_DESCRIPTOR_TYPE

    # 用 win32security 构造自相关 SD. 延迟 import (Linux 无 pywin32).
    try:
        import win32security  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "构造 ALE_USER_ID 条件需要 pywin32 (win32security); "
            "未安装, WFP user-keyed 过滤不可用 (降级 PowerShell 路径)"
        ) from exc

    # jbx-sandbox 用户的 SID (字符串 -> SID 对象).
    user_sid_obj = win32security.ConvertStringSidToSid(sandbox_user_sid)
    # S9 实跑: win32security 无 TRUSTEE 属性/BuildTrusteeWithSid 未暴露。
    # pywin32 SetEntriesInAcl 接受 EXPLICIT_ACCESS dict, 其 Trustee 字段本身
    # 也是 dict (见 PyACL.cpp PyWinObject_AsTRUSTEE): {TrusteeType, TrusteeForm,
    # Identifier}. TrusteeForm=TRUSTEE_IS_SID 时 Identifier 放 PySID 对象。
    explicit = {
        "AccessPermissions": const.FWP_ACTRL_MATCH_FILTER,
        "AccessMode": win32security.GRANT_ACCESS,
        "Inheritance": 0,  # 不继承
        "Trustee": {
            "TrusteeType": win32security.TRUSTEE_IS_USER,
            "TrusteeForm": win32security.TRUSTEE_IS_SID,
            "Identifier": user_sid_obj,
        },
    }
    # SetEntriesInAcl 是 PyACL 方法: 在空 ACL 上添加 entries.
    dacl = win32security.ACL()
    dacl.SetEntriesInAcl([explicit])
    sd = win32security.SECURITY_DESCRIPTOR()
    # S9 实跑: FwpmFilterAdd0 报 RPC_X_BAD_STUB_DATA(0x6F7)。SDK 示例用
    # BuildSecurityDescriptorW 构造带 owner/group/DACL 的完整 SD。pywin32 的
    # SetSecurityDescriptorDacl 内部 _MakeAbsoluteSD 需 owner/group, 缺失时
    # 可能产生不一致的 self-relative SD → BFE marshal 校验失败。
    # 显式 Initialize + 设 owner/group(用 jbx-sandbox 自己的 SID) 再设 DACL,
    # 构造完整 SD。Owner 用沙箱用户自己, 保证 SD 自洽。
    sd.Initialize()
    sd.SetSecurityDescriptorOwner(user_sid_obj, 0)
    sd.SetSecurityDescriptorGroup(user_sid_obj, 0)
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    # S9 实跑: win32security 无 MakeSelfRelativeSD。PySECURITY_DESCRIPTOR 对象内部
    # 始终以 self-relative 格式存储 (见 pywin32 PySECURITY_DESCRIPTOR.cpp SetSD),
    # 且支持 buffer 接口, bytes(sd) 直接拿到 self-relative 原始字节 (FWP 要求此格式)。
    sd_bytes = bytes(sd)
    # 诊断: SD 长度 + control flags (确认 self-relative 位)。
    try:
        ctrl, _rev = sd.GetSecurityDescriptorControl()
        sr_bit = bool(ctrl & 0x8000)  # SE_SELF_RELATIVE
    except Exception:  # noqa: BLE001
        sr_bit = "?"
    logger.info(
        "ALE_USER_ID SD: len=%d valid=%s self_relative=%s sid=%s",
        len(sd_bytes), bool(sd_bytes), sr_bit, sandbox_user_sid,
    )

    blob = FWP_BYTE_BLOB()
    # S9 实跑: FwpmFilterAdd0 报 RPC_X_BAD_STUB_DATA(0x6F7), SD 本身有效(self_relative
    # =True, 176B), 根因在 blob.data 指针的内存来源。SDK 示例用 BuildSecurityDescriptorW
    # 返回的 LocalAlloc 内存, blob.data 直接指它。旧版用 from_buffer_copy 创建 ctypes
    # 数组, 该 buffer 的内存来源/对齐可能让 BFE RPC marshal 校验失败。改用
    # create_string_buffer (malloc 分配, 标准 8 字节对齐, RPC 友好), 与 SDK 对齐。
    buf = ctypes.create_string_buffer(sd_bytes, len(sd_bytes))
    blob.size = len(sd_bytes)
    blob.data = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8))
    cond.conditionValue.value.sd = ctypes.cast(
        ctypes.pointer(blob), ctypes.c_void_p,
    ).value
    return cond, _KeepAlive(blob=blob, buf=buf, sd_bytes=sd_bytes)


class _KeepAlive:
    """持有 ctypes 对象引用, 防止条件构造返回后 GC 释放指针指向的内存."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _build_port_eq_condition(port: int) -> FWPM_FILTER_CONDITION0:
    """构造 IP_REMOTE_PORT == port 条件 (FWP_MATCH_EQUAL).

    放行整个端口范围采用"每个端口一个 Permit filter"的方案: WFP 的
    FWP_MATCH_RANGE 需 FWP_RANGE0 结构, ctypes 布局复杂且易错; 每端口一
    个 EQUAL filter 更可靠, 端口范围通常 <=10 个, 开销可忽略.
    """
    cond = FWPM_FILTER_CONDITION0()
    cond.fieldKey = _guid_from_str(const.FWPM_CONDITION_IP_REMOTE_PORT)
    cond.matchType = const.FWP_MATCH_EQUAL
    cond.conditionValue.type = const.FWP_UINT16
    cond.conditionValue.value.uint16 = port
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
    keeps_alive: list[object] | None = None,
) -> None:
    """安装一个 filter.

    幂等: 已存在 (FWP_E_ALREADY_EXISTS=0x800700B7) 则忽略, 不删后加
    (旧 docstring 误导为"先删后加", 已修正).
    keeps_alive: 条件构造返回的 keep-alive 引用 (FWP_V4_ADDR_MASK/
    FWPM_DISPLAY_DATA/SD blob 等), 必须存活到本函数 FwpmFilterAdd0 返回.
    """
    fwpu = _get_fwpuclnt()
    fkey = _guid_from_str(filter_key)

    flt = FWPM_FILTER0()
    flt.filterKey = fkey
    flt.layerKey = _guid_from_str(layer_key)
    flt.subLayerKey = sublayer_key
    # S9: displayData.name 不能为 NULL (同 sublayer, BFE 拒绝)。用传入的 display_name。
    flt.displayData.name = display_name
    flt.displayData.description = display_name
    flt.weight.type = const.FWP_UINT8
    flt.weight.value.uint8 = weight
    flt.action.type = action_type

    cond_array = (FWPM_FILTER_CONDITION0 * len(conditions))(*conditions)
    flt.numFilterConditions = ctypes.c_uint32(len(conditions))
    # S12 旧 bug: 字段名拼错成 filterConditions (带 s), ctypes 当成新实例属性,
    # 结构体内的 filterCondition 字段保持 NULL, BFE 收到 numFilterConditions=1
    # 但 filterCondition=NULL, 返回 RPC_X_BAD_STUB_DATA (hr=0x6F7).
    flt.filterCondition = ctypes.cast(
        cond_array, ctypes.POINTER(FWPM_FILTER_CONDITION0),
    )

    fid = ctypes.c_uint64(0)
    hr = fwpu.FwpmFilterAdd0(engine, ctypes.byref(flt), None, ctypes.byref(fid))
    if hr != 0 and hr != 0x800700B7:  # FWP_E_ALREADY_EXISTS
        # 诊断: 0x6F7=RPC_X_BAD_STUB_DATA 时打结构体布局 + 指针字段值助定位。
        # 关键: sizeof(flt) 应与 SDK sizeof(FWPM_FILTER0)=168 一致 (Windows x64);
        # 各指针字段(filterConditions/providerKey/reserved) 指向有效内存或 NULL;
        # cond 的 conditionValue.value.sd 应指向 blob (FWP_BYTE_BLOB*)。
        # 整块 try 防诊断自身异常掩盖真实 hr (旧版 hex(POINTER) 抛 TypeError)。
        try:
            def _ptr_val(p):
                v = ctypes.cast(p, ctypes.c_void_p).value
                return hex(v) if v else "NULL"
            cond_types = [getattr(c.conditionValue, "type", -1) for c in conditions]
            cond_sd_ptrs = []
            for c in conditions:
                try:
                    cond_sd_ptrs.append(_ptr_val(c.conditionValue.value.sd))
                except Exception:  # noqa: BLE001
                    cond_sd_ptrs.append("?")
            flt_bytes = bytes(flt)
            logger.error(
                "FwpmFilterAdd0 失败 display=%s hr=0x%08X sizeof_flt=%d "
                "num_conds=%d cond_types=%s cond_sd_ptrs=%s weight_type=%d "
                "action=%d filterConditions_ptr=%s providerKey=%s reserved=%s "
                "flt_hex_first32=%s",
                display_name, hr, ctypes.sizeof(flt), len(conditions),
                cond_types, cond_sd_ptrs, flt.weight.type, action_type,
                _ptr_val(flt.filterCondition),
                _ptr_val(flt.providerKey),
                _ptr_val(flt.reserved),
                flt_bytes[:32].hex(),
            )
        except Exception:  # noqa: BLE001
            logger.error("FwpmFilterAdd0 诊断自身异常 hr=0x%08X", hr, exc_info=True)
        raise _wfp_error(hr, f"FwpmFilterAdd0({display_name})")
    logger.info("WFP filter 安装: %s (layer=%s, action=%d)", display_name, layer_key, action_type)


def install_wfp_filters(
    sandbox_user_sid: str,
    permit_port_start: int,
    permit_port_end: int,
) -> None:
    """安装 Block + Permit WFP filter set.

    幂等: sublayer/filter 用固定合法 GUID key, 重复安装会命中
    FWP_E_ALREADY_EXISTS 并被忽略.

    Args:
        sandbox_user_sid: jbx-sandbox 用户 SID 字符串.
        permit_port_start/end: Permit filter 放行的 loopback 端口范围.
    """
    _require_windows()
    fwpu = _get_fwpuclnt()
    # keeps_alive 列表: 持有所有条件构造返回的 ctypes 局部对象引用,
    # 直到全部 FwpmFilterAdd0 完成才允许 GC (否则 v4AddrMask/SD blob 指针悬垂).
    keeps: list[object] = []

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
                block_cond, ka = _build_ale_user_condition(sandbox_user_sid)
                keeps.append(ka)
                _add_filter(
                    engine, fkey, layer, sublayer_key,
                    [block_cond],
                    const.FWP_ACTION_BLOCK,
                    const.FWP_WEIGHT_BLOCK,
                    f"JiuwenBox-Block-{fkey}",
                    keeps_alive=keeps,
                )

            # --- Permit filters (V4 + V6) for loopback + port range ---
            # 为端口范围内每个端口装一个独立 Permit filter (放行整个范围).
            # filter key 形如 <base_key>-<port>, 幂等安装/卸载.
            for layer, base_key in (
                (const.FWPM_LAYER_ALE_AUTH_CONNECT_V4, const.JBX_FILTER_PERMIT_KEY_V4),
                (const.FWPM_LAYER_ALE_AUTH_CONNECT_V6, const.JBX_FILTER_PERMIT_KEY_V6),
            ):
                # 按 layer GUID 显式判断 V4/V6, 不能用 "V4" in base_key:
                # base_key 是纯 hex GUID 字符串 (如 "BC5D4E3F-...-DEF0"),
                # 不含 "V4"/"V6" 子串, 旧代码两路都走 else → V4 层装了
                # IPv6 ::1 条件 (FWP_V6_ADDR_AND_MASK=257), BFE 因 condition
                # 类型与 layer 不匹配返回 0x80320027, 主路径失败降级防火墙.
                is_v4 = (layer == const.FWPM_LAYER_ALE_AUTH_CONNECT_V4)
                for port in range(permit_port_start, permit_port_end + 1):
                    user_cond, user_ka = _build_ale_user_condition(sandbox_user_sid)
                    keeps.append(user_ka)
                    if is_v4:
                        lb_cond, lb_ka = _build_loopback_v4_condition()
                    else:
                        lb_cond, lb_ka = _build_loopback_v6_condition()
                    keeps.append(lb_ka)
                    port_cond = _build_port_eq_condition(port)
                    # S9: port_key 必须是合法 UUID (旧版 f"{base}-{port}" 非法,
                    # _guid_from_str 报 ValueError)。用 uuid5 派生确定性 GUID。
                    port_key = _permit_filter_guid_str(base_key, port)
                    _add_filter(
                        engine, port_key, layer, sublayer_key,
                        [user_cond, lb_cond, port_cond],
                        const.FWP_ACTION_PERMIT,
                        const.FWP_WEIGHT_PERMIT,
                        f"JiuwenBox-Permit-Loopback-{base_key}-{port}",
                        keeps_alive=keeps,
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


def uninstall_wfp_filters(
    permit_port_start: int = const.DEFAULT_PROXY_PORT_RANGE_START,
    permit_port_end: int = const.DEFAULT_PROXY_PORT_RANGE_END,
) -> None:
    """卸载所有 JiuwenBox WFP filter + sublayer (幂等).

    Permit filter 是每端口一个 (key 带 -port 后缀), 按端口范围遍历删除.
    """
    _require_windows()
    fwpu = _get_fwpuclnt()
    engine = _open_engine()
    try:
        # Block filter (固定 key).
        for fkey in (
            const.JBX_FILTER_BLOCK_KEY_V4,
            const.JBX_FILTER_BLOCK_KEY_V6,
        ):
            _delete_filter_by_key(fwpu, engine, fkey)
        # Permit filter (每端口一个 key, S9: 用 uuid5 派生, 与 install 一致)。
        for base_key in (
            const.JBX_FILTER_PERMIT_KEY_V4,
            const.JBX_FILTER_PERMIT_KEY_V6,
        ):
            for port in range(permit_port_start, permit_port_end + 1):
                _delete_filter_by_key(
                    fwpu, engine, _permit_filter_guid_str(base_key, port),
                )
        try:
            hr = fwpu.FwpmSubLayerDeleteByKey0(
                engine, ctypes.byref(_guid_from_str(const.JBX_SUBLAYER_KEY)),
            )
            # W3: sublayer not-found 是 0x80320031 (KEY_NOT_FOUND), 幂等静默。
            if hr not in (0, 0x80320031):
                logger.warning(
                    "删除 WFP sublayer: %s",
                    _wfp_error(hr, "FwpmSubLayerDeleteByKey0"),
                )
        except Exception:  # noqa: BLE001
            logger.warning("删除 WFP sublayer 异常", exc_info=True)
    finally:
        fwpu.FwpmEngineClose0(engine)
    logger.info("WFP filter set 卸载完成")


def _delete_filter_by_key(fwpu, engine, fkey: str) -> None:
    """按 key 删除单个 WFP filter (幂等, not-found 静默).

    W3: 旧版把 0x800700B7 (ERROR_ALREADY_EXISTS, add 路径才出现) 当 not-found 忽略,
    但 delete 的 not-found 是 FWP_E_KEY_NOT_FOUND=0x80320031 / FWP_E_FILTER_NOT_FOUND=
    0x80320003 (0x80320xxx 段)。改为忽略正确的 not-found 码, 其余用 _wfp_error。
    """
    # delete 路径的 "不存在" 码: 视为幂等成功, 静默。
    _DELETE_NOT_FOUND = {0x80320031, 0x80320003}
    try:
        hr = fwpu.FwpmFilterDeleteByKey0(
            engine, ctypes.byref(_guid_from_str(fkey)),
        )
        if hr == 0:
            return
        if hr in _DELETE_NOT_FOUND:
            return
        logger.warning("删除 WFP filter %s: %s", fkey, _wfp_error(hr, "FwpmFilterDeleteByKey0"))
    except Exception:  # noqa: BLE001
        logger.warning("删除 WFP filter %s 异常", fkey, exc_info=True)


def install_firewall_rule_fallback(
    sandbox_user_name: str,
    permit_port_start: int,
    permit_port_end: int,
    sandbox_user_sid: str | None = None,
) -> bool:
    """降级方案: 用 PowerShell New-NetFirewallRule 实现用户级出站拦截.

    对齐 docs/window沙箱.md 6.4.2 降级路径. 牺牲内核态优先级控制与绕过保护,
    功能等价 (按用户拦截出站).

    S10: 旧版加 `-ErrorAction SilentlyContinue` 吞掉 stderr, 失败只 warning 且循环
    外无条件打"安装完成" → 假成功。改为不打 SilentlyContinue, 捕获 stderr 明文
    打印; 返回是否全部成功, 调用方据此决定是否致命 raise (S12)。

    S12 实跑修复 (failed.txt):
      1) -LocalUser 裸传 'S-1-5-21-...' 被拒 (stderr: 本地用户权限列表无效,
         只能含字母和 :/._ 不含连字符 '-'). 微软文档要求 -LocalUser 传 SDDL
         字符串 'D:(A;;CC;;;<SID>)', 不是裸 SID. 构造 SDDL 传入.
      2) -RemotePort 配 -RemoteAddress 但缺 -Protocol, stderr: 协议绑定对象
         选择与所选协议不匹配 (HRESULT 0x80070057). 显式加 -Protocol TCP.
    """
    _require_windows()
    rule_block = "JiuwenBox-Block-Sandbox-Egress"
    rule_permit = "JiuwenBox-Permit-Loopback"
    port_range = f"{permit_port_start}-{permit_port_end}"

    # -LocalUser 要的是 SDDL 字符串 (微软文档): D:(A;;CC;;;<SID>).
    # 无 SID 时退回裸用户名 (含 '-' 会被拒, 但至少留下诊断).
    if sandbox_user_sid:
        local_user = f"D:(A;;CC;;;{sandbox_user_sid})"
    else:
        local_user = sandbox_user_name
    # Block 规则: 拦截 sandbox 用户的所有出站。
    ps_block = (
        f"New-NetFirewallRule -DisplayName '{rule_block}' "
        f"-Direction Outbound -Action Block "
        f"-LocalUser '{local_user}'"
    )
    # Permit 规则: 放行 sandbox 用户到 127.0.0.1:port_range (放行需在 Block 之前)。
    # 注: Windows Firewall 对 loopback 目标过滤支持有限, -RemoteAddress 127.0.0.1
    # 在部分版本不生效; 真要精确 loopback 仍走 WFP 主路径。降级路径主要靠 Block 规则。
    # S12: 显式 -Protocol TCP (缺省协议对 -RemotePort 报 "协议不匹配").
    ps_permit = (
        f"New-NetFirewallRule -DisplayName '{rule_permit}' "
        f"-Direction Outbound -Action Allow "
        f"-Protocol TCP "
        f"-LocalUser '{local_user}' "
        f"-RemoteAddress 127.0.0.1 "
        f"-RemotePort {port_range}"
    )
    all_ok = True
    for ps in (ps_permit, ps_block):
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                check=True, capture_output=True, timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            all_ok = False
            stderr = b""
            if isinstance(exc, subprocess.CalledProcessError):
                stderr = exc.stderr or b""
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(
                "PowerShell 防火墙规则安装失败 (%s): %s | stderr: %s",
                ps[:60], exc, stderr_text or "<空>",
            )
    if all_ok:
        logger.info(
            "降级防火墙规则安装完成: user=%s permit_port=%s",
            sandbox_user_name, port_range,
        )
    else:
        logger.error(
            "降级防火墙规则安装存在失败: user=%s permit_port=%s "
            "(网络隔离可能不完整)", sandbox_user_name, port_range,
        )
    return all_ok


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
