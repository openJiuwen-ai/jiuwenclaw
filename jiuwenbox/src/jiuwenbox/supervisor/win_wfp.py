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


class FWPM_ACTION0(ctypes.Structure):
    """Filter action: type + union{filterType GUID; calloutKey GUID}.

    BLOCK/PERMIT 不用 union, 但 SDK 结构体 union 占 GUID (16B) 尺寸,
    需保留以使后续字段对齐.
    """
    _fields_ = [
        ("type", ctypes.c_uint32),  # FWP_ACTION_*
        ("filterType", GUID),       # union{ GUID filterType; GUID calloutKey; }
    ]


# ---------------------------------------------------------------------------
# FWPM_FILTER0 (fwpmtypes.h). 布局严格对齐 SDK:
#   filterKey(GUID) displayData(FWPM_DISPLAY_DATA0 内嵌) flags(UINT32)
#   providerKey(GUID*) providerData(FWP_BYTE_BLOB 内嵌) layerKey(GUID)
#   subLayerKey(GUID) weight(FWP_VALUE0) numFilterConditions(UINT32)
#   filterCondition(FWPM_FILTER_CONDITION0*) action(FWPM_ACTION0)
#   union{ UINT64 rawContext; GUID providerContextKey; }(占 8B)
#   reserved(GUID*) filterId(UINT64) effectiveWeight(FWP_VALUE0)
# review 错误: 旧版多了不存在的 providerDataSize、缺 flags、reserved 误为
# c_uint64. 已修.
# ---------------------------------------------------------------------------
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
        ("rawContext", ctypes.c_uint64),           # union{rawContext; providerContextKey}
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
        raise ctypes.WinError(hr)
    return engine


def _add_sublayer(engine: wintypes.HANDLE, sublayer_key: str, weight: int) -> GUID:
    """创建/复用 sublayer (幂等: 已存在则忽略 ERROR_ALREADY_EXISTS)."""
    fwpu = _get_fwpuclnt()
    sublayer = FWPM_SUBLAYER0()
    sublayer.subLayerKey = _guid_from_str(sublayer_key)
    sublayer.weight = ctypes.c_uint16(weight)
    sublayer.flags = 0
    hr = fwpu.FwpmSubLayerAdd0(engine, ctypes.byref(sublayer), None)
    if hr != 0 and hr != 0x800700B7:  # FWP_E_ALREADY_EXISTS
        raise ctypes.WinError(hr)
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
    cond.conditionValue.type = const.FWP_V6_ADDR_MASK
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
    # pywin32 EXPLICIT_ACCESS dict 字段为大写键 (AccessPermissions/AccessMode/
    # Inheritance/Trustee, 见 pywin32 PyACL.cpp PyWinObject_AsEXPLICIT_ACCESS).
    # Trustee 接受 TRUSTEE 对象; 用 BuildTrusteeWithSid 从 SID 构造.
    trustee = win32security.TRUSTEE()
    win32security.BuildTrusteeWithSid(trustee, user_sid_obj)
    explicit = {
        "AccessPermissions": const.FWP_ACTRL_MATCH_FILTER,
        "AccessMode": win32security.GRANT_ACCESS,
        "Inheritance": 0,  # 不继承
        "Trustee": trustee,
    }
    # SetEntriesInAcl 是 PyACL 方法: 在空 ACL 上添加 entries.
    dacl = win32security.ACL()
    dacl.SetEntriesInAcl([explicit])
    sd = win32security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    # MakeSelfRelativeSD 返回自相关 SD 的 bytes (contiguous, FWP 要求此格式).
    sd_bytes = win32security.MakeSelfRelativeSD(sd)

    blob = FWP_BYTE_BLOB()
    # 持有 sd_bytes (bytes) 引用, blob.data 指向其缓冲.
    buf = (ctypes.c_uint8 * len(sd_bytes)).from_buffer_copy(sd_bytes)
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
                for port in range(permit_port_start, permit_port_end + 1):
                    user_cond, user_ka = _build_ale_user_condition(sandbox_user_sid)
                    keeps.append(user_ka)
                    if "V4" in base_key:
                        lb_cond, lb_ka = _build_loopback_v4_condition()
                    else:
                        lb_cond, lb_ka = _build_loopback_v6_condition()
                    keeps.append(lb_ka)
                    port_cond = _build_port_eq_condition(port)
                    port_key = f"{base_key}-{port}"
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
        # Permit filter (每端口一个 key).
        for base_key in (
            const.JBX_FILTER_PERMIT_KEY_V4,
            const.JBX_FILTER_PERMIT_KEY_V6,
        ):
            for port in range(permit_port_start, permit_port_end + 1):
                _delete_filter_by_key(fwpu, engine, f"{base_key}-{port}")
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


def _delete_filter_by_key(fwpu, engine, fkey: str) -> None:
    """按 key 删除单个 WFP filter (幂等, not-found 静默)."""
    try:
        hr = fwpu.FwpmFilterDeleteByKey0(
            engine, ctypes.byref(_guid_from_str(fkey)),
        )
        if hr not in (0, 0x800700B7):  # 0x800700B7 = not found
            logger.warning("删除 WFP filter %s 返回 0x%X", fkey, hr)
    except Exception:  # noqa: BLE001
        logger.warning("删除 WFP filter %s 异常", fkey, exc_info=True)


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
