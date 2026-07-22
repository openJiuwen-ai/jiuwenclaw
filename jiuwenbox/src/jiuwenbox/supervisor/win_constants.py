# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Windows 沙箱常量定义 (纯 Python, 无 win32 依赖).

集中存放 Token / Job Object / WFP / 文件 ACL 相关的 magic number 与
结构体字段常量. 模块顶层不加载任何 win32 库 (``ctypes``/``pywin32``),
因此在 Linux 下可正常 import, 也可被单元测试直接断言常量值.

运行时的 win32 API 调用统一延迟到 ``win_*.py`` 各功能模块内部, 并以
``sys.platform == "win32"`` 守卫, 详见 ``docs/window沙箱.md`` 第6章.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 合成 SID (Synthetic SID) - Windows 沙箱文件写入控制的核心标记.
#
# 该 SID 不关联任何真实账户, 仅作为 "允许写入" 的权限标记出现在 NTFS
# DACL 中. 沙箱进程的 Write-Restricted Token 必须携带此 SID 才能写入
# 白名单路径. 详见 ``docs/window沙箱.md`` 2.2 / 6.7.
#
# 格式: S-1-5-21-<machine>-<sub-authority>-<RID>. 这里用一个固定的
# sub-authority 序列 + 一个明显高于真实用户 RID 的值, 避免与真实账户
# 碰撞 (真实账户 RID 通常 < 1000).
# ---------------------------------------------------------------------------
SANDBOX_USER_NAME = "jbx-sandbox"
SANDBOX_USER_GROUP = "jbx-sandbox-users"
SYNTHETIC_WRITE_SID_PREFIX = "S-1-5-21"
# 固定的 sub-authority 区段, 与任何真实域/机器账户错开.
SYNTHETIC_WRITE_SID_SUBAUTHS: tuple[int, ...] = (
    0xBABE0013,  # 机器标识占位 (实际取自安装机器)
    0x00002000,  # 子权限
)
SYNTHETIC_WRITE_SID_RID = 0x0000C0DE  # 合成 SID 的 RID

# 沙箱用户密码长度 (安装时随机生成).
SANDBOX_USER_PASSWORD_LENGTH = 64

# ---------------------------------------------------------------------------
# Token 信息类 (TOKEN_INFORMATION_CLASS) - GetTokenInformation 参数.
# 仅列出沙箱用到的几个, 完整列表见 winnt.h.
# ---------------------------------------------------------------------------
TOKEN_USER = 1
TOKEN_GROUPS = 2
TOKEN_PRIVILEGES = 3
TOKEN_OWNER = 4
TOKEN_PRIMARY_GROUP = 5
TOKEN_DEFAULT_DACL = 6
TOKEN_SOURCE = 7
TOKEN_TYPE = 8
TOKEN_IMPERSONATION_LEVEL = 9
TOKEN_STATISTICS = 11
TOKEN_RESTRICTIONS = 13
TOKEN_SESSION_ID = 14
TOKEN_GROUPS_AND_PRIVILEGES = 15
TOKEN_SESSION_REFERENCE = 16
TOKEN_SANDBOX_INERT = 29

# ---------------------------------------------------------------------------
# CreateRestrictedToken 标志.
#   DISABLE_MAX_PRIVILEGE  : 清除 token 中所有特权.
#   SANDBOX_INERT          : 标记 token 为沙箱 inert (某些路径豁免检查).
#   WRITE_RESTRICTED      : 只对写操作做 Restricted SID 双重 ACL 检查.
# ---------------------------------------------------------------------------
DISABLE_MAX_PRIVILEGE = 0x1
SANDBOX_INERT = 0x4
WRITE_RESTRICTED = 0x8

# CreateRestrictedToken 组合: 文档 6.5 要求的受限 SID 列表 =
# [Everyone, 当前 LogonSession, JHXSandboxWrite].
RESTRICTED_TOKEN_FLAGS = DISABLE_MAX_PRIVILEGE | SANDBOX_INERT | WRITE_RESTRICTED

# ---------------------------------------------------------------------------
# WellKnownSid 类型 (CreateWellKnownSid 的枚举值).
#   WinWorldSid   -> Everyone (S-1-1-0)
#   WinNullSid    -> S-1-0-0
#   WinLocalSystemSid -> S-1-5-18
# ---------------------------------------------------------------------------
WIN_WORLD_SID = 1  # WinWorldSid -> Everyone

# ---------------------------------------------------------------------------
# LogonUser / CreateProcessWithLogonW / CreateProcessAsUser 标志.
# ---------------------------------------------------------------------------
LOGON32_LOGON_INTERACTIVE = 2
LOGON32_PROVIDER_DEFAULT = 0

# CreateProcessW / CreateProcessAsUserW dwCreationFlags.
CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
EXTENDED_STARTUPINFO_PRESENT = 0x00080000

# 进程/线程优先级类.
NORMAL_PRIORITY_CLASS = 0x20

# DUPLICATE_... 权限 (DuplicateTokenEx).
DUPLICATE_SAME_ACCESS = 0x2

# TOKEN 类型 (DuplicateTokenEx).
TOKEN_PRIMARY = 1
TOKEN_IMPERSONATION = 2

# 安全模拟级别 (SECURITY_IMPERSONATION_LEVEL).
SecurityImpersonation = 2

# ---------------------------------------------------------------------------
# 用户账户标志 (USER_INFO_1.usri1_flags / NetUserAdd).
#
# 注意: netapi.h 中 UF_* 与 USER_PRIV_* / USER_UNK_* 是不同的位域,
# 但头文件里 DONT_EXPIRE_PASSWD 与 NORMAL_ACCOUNT 实际各自位宽不冲突:
#   UF_SCRIPT             = 0x0001  (NetUserAdd 强制要求)
#   UF_ACCOUNTDISABLE     = 0x0002  (禁用账户; 沙箱用户不能设, 否则 LogonUser 失败)
#   UF_HOMEDIR_REQUIRED   = 0x0008
#   UF_PASSWD_CANT_CHANGE = 0x0040  (用户不能改密码)
#   UF_DONT_EXPIRE_PASSWD = 0x0200  (密码不过期)
#   UF_NORMAL_ACCOUNT     = 0x0200  (普通账户; 见 netapi.h UF_NORMAL_ACCOUNT)
#
# 头文件中 UF_DONT_EXPIRE_PASSWD 与 UF_NORMAL_ACCOUNT 的数值在历史上确实同
# 占 0x0200, 但二者属不同语义域 (一个属 UF_* 标志位, 一个属账户类型分类),
# 在 USER_INFO_1.usri1_flags 里高位区段(0x0200 及以上)按账户类型解析,
# 低位区段(0x0001..0x0080)按能力标志解析, 因此同时设置语义不冲突.
# ---------------------------------------------------------------------------
UF_SCRIPT = 0x0001
UF_ACCOUNTDISABLE = 0x0002
UF_HOMEDIR_REQUIRED = 0x0008
UF_PASSWD_CANT_CHANGE = 0x0040
UF_DONT_EXPIRE_PASSWD = 0x0200
UF_NORMAL_ACCOUNT = 0x0200

# 沙箱用户最终 flag: 脚本位 + 不改密码 + 不过期 + 普通账户. 不设 DISABLE.
SANDBOX_USER_FLAGS = UF_SCRIPT | UF_PASSWD_CANT_CHANGE | UF_DONT_EXPIRE_PASSWD

# NetLocalGroupAddMembers 预定义级别.
LOCALGROUP_MEMBERS_INFO_0 = 0

# NetUserAdd 信息级别.
USER_INFO_1_LEVEL = 1

# ---------------------------------------------------------------------------
# Job Object 信息类 (JOBOBJECTINFOCLASS).
#   JobObjectBasicLimitInformation        = 2  (进程数上限等)
#   JobObject ExtendedLimitInformation   = 9  (内存上限 / KILL_ON_CLOSE 等)
#   JobObject CpuRateControlInformation  = 15 (CPU 速率)
#   JobObject AssociateCompletionPortInformation = 7
#   JobObject GroupInformation            = 11
# ---------------------------------------------------------------------------
JobObjectBasicLimitInformation = 2
JobObjectExtendedLimitInformation = 9
JobObjectCpuRateControlInformation = 15

# Job Object 基本限制标志 (JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags).
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000400
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000

# Job Object 扩展限制标志 (JOBOBJECT_EXTENDED_LIMIT_INFORMATION.BasicLimit.LimitFlags).
# 内存限制位.
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400

# CPU 速率控制标志 (JOBOBJECT_CPU_RATE_CONTROL_INFORMATION.ControlFlags).
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x00000001
JOB_OBJECT_CPU_RATE_CONTROL_WEIGHT_BASED = 0x00000002
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x00000004
JOB_OBJECT_CPU_RATE_CONTROL_NOTIFY = 0x00000008

# CPU 速率以 0.01% 为单位, 范围 [1, 10000] (即 0.01% ~ 100%).
CPU_RATE_MIN = 1
CPU_RATE_MAX = 10000

# ---------------------------------------------------------------------------
# 文件 ACL 访问掩码 (Access Mask) - 用于 ACE 的权限位.
# 对齐 docs/window沙箱.md 6.7: allow_write 施加 Allow Write+Execute+Delete.
# ---------------------------------------------------------------------------
FILE_GENERIC_READ = 0x00120089
FILE_GENERIC_WRITE = 0x00120116
FILE_GENERIC_EXECUTE = 0x001200A0
FILE_DELETE_ACCESS = 0x00010000  # DELETE 位
FILE_READ_ATTRIBUTES = 0x00000080

# allow_write 路径授予的写权限组合.
ALLOW_WRITE_RIGHTS = FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE | FILE_DELETE_ACCESS
# read 控制中 deny 施加的读权限.
DENY_READ_RIGHTS = FILE_GENERIC_READ

# ACL/ACE 类型.
ACCESS_ALLOWED_ACE_TYPE = 0
ACCESS_DENIED_ACE_TYPE = 1
INHERITED_ACE = 0x10

# ACE 继承标志 (AceFlags), 用于容器/子对象继承.
CONTAINER_INHERIT_ACE = 0x2
OBJECT_INHERIT_ACE = 0x1
SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x7
INHERIT_ONLY_ACE = 0x8
NO_PROPAGATE_INHERIT_ACE = 0x4

# 递归施加 ACE 时使用的继承标志 (目录 + 所有子对象).
RECURSIVE_ACE_FLAGS = (
    CONTAINER_INHERIT_ACE
    | OBJECT_INHERIT_ACE
    | SUB_CONTAINERS_AND_OBJECTS_INHERIT
)

# SECURITY_INFORMATION 标志 (Get/SetNamedSecurityInfo).
DACL_SECURITY_INFORMATION = 0x4
OWNER_SECURITY_INFORMATION = 0x1
GROUP_SECURITY_INFORMATION = 0x2
PROTECTED_DACL_SECURITY_INFORMATION = 0x40000000  # 阻止继承的 DACL

# SE_OBJECT_TYPE (Get/SetNamedSecurityInfo 第一个参数类型).
SE_FILE_OBJECT = 1

# ---------------------------------------------------------------------------
# WFP (Windows Filtering Platform) 常量.
# 详见 fwpmtypes.h / fwpsu.h. 文档 6.4.2 要求安装 Block + Permit filter.
# ---------------------------------------------------------------------------

# RPC_C_AUTHN_WINNT 鉴别级别 (FwpmEngineOpen).
RPC_C_AUTHN_WINNT = 10
RPC_C_AUTHN_LEVEL_DEFAULT = 0

# Filter Action 类型 (FWP_ACTION_TYPE). 取值对齐 fwptypes.h.
FWP_ACTION_FLAG_TERMINATING = 0x00001000
FWP_ACTION_BLOCK = 0x00000001 | FWP_ACTION_FLAG_TERMINATING
FWP_ACTION_PERMIT = 0x00000002 | FWP_ACTION_FLAG_TERMINATING
FWP_ACTION_CALLOUT_TERMINATING = 0x00004000 | FWP_ACTION_FLAG_TERMINATING

# FWP_ACTRL_* (条件匹配权限位). ALE_USER_ID 条件评估 SD 时, 检查
# FWP_ACTRL_MATCH_FILTER 访问权是否被 DACL 授予 (SDK: Permitting and
# Blocking Applications and Users).
FWP_ACTRL_MATCH_FILTER = 0x00000001

# Filter Weight (FWP_VALUE0.uint8 范围 0..15, 文档要求 Permit > Block).
FWP_WEIGHT_BLOCK = 0x0  # Block filter 权重最低
FWP_WEIGHT_PERMIT = 0xF  # Permit filter 权重最高, 覆盖 Block

# ALE (Application Layer Enforcement) Layer GUIDs (网络出站拦截层).
# 取值对齐 fwpmu.h DEFINE_GUID (真实 SDK 值, 非 DCE UUID 字面量需注意
# Windows GUID 内存布局 little-endian, 见 win_wfp._guid_from_str).
# FWPM_LAYER_ALE_AUTH_CONNECT_V4 / V6 - 出站连接授权层.
FWPM_LAYER_ALE_AUTH_CONNECT_V4 = "C38D57D1-05A7-4C33-900F-7FBCEEE60E82"
FWPM_LAYER_ALE_AUTH_CONNECT_V6 = "4A72393B-319F-44BC-84C3-BA54DCB3B6B4"

# Filter Condition 字段 (FWPM_CONDITION_...). 取值对齐 fwpmu.h DEFINE_GUID.
FWPM_CONDITION_ALE_USER_ID = "AF043A0A-B34D-4F86-979C-C90371AF6E66"
FWPM_CONDITION_IP_REMOTE_ADDRESS = "B235AE9A-1D64-49B8-A44C-5FF3D9095045"
FWPM_CONDITION_IP_REMOTE_PORT = "C35A604D-D22B-4E1A-91B4-68F674EE674B"

# Condition 匹配类型 (FWP_MATCH_TYPE, fwptypes.h).
FWP_MATCH_EQUAL = 0
FWP_MATCH_GREATER = 1
FWP_MATCH_LESS = 2
FWP_MATCH_GREATER_OR_EQUAL = 3
FWP_MATCH_LESS_OR_EQUAL = 4
FWP_MATCH_RANGE = 5
FWP_MATCH_FLAGS_ALL_SET = 6
FWP_MATCH_FLAGS_ANY_SET = 7
FWP_MATCH_FLAGS_NONE_SET = 8
FWP_MATCH_EQUAL_CASE_INSENSITIVE = 9
FWP_MATCH_NOT_EQUAL = 10

# FWP_DATA_TYPE (FWP_VALUE0.Type / FWP_CONDITION_VALUE0.Type).
# 取值严格对齐 fwptypes.h FWP_DATA_TYPE 枚举 (注意: 旧版常量值与 SDK 错位,
# FWP_SID 应为 13 而非 12; FWP_BYTE_BLOB_TYPE 应为 12 而非 16).
FWP_EMPTY = 0
FWP_UINT8 = 1
FWP_UINT16 = 2
FWP_UINT32 = 3
FWP_UINT64 = 4
FWP_INT8 = 5
FWP_INT16 = 6
FWP_INT32 = 7
FWP_INT64 = 8
FWP_FLOAT = 9
FWP_DOUBLE = 10
FWP_BYTE_ARRAY16_TYPE = 11
FWP_BYTE_BLOB_TYPE = 12
FWP_SID = 13
FWP_SECURITY_DESCRIPTOR_TYPE = 14
FWP_TOKEN_INFORMATION_TYPE = 15
FWP_TOKEN_ACCESS_INFORMATION_TYPE = 16
FWP_UNICODE_STRING_TYPE = 17
FWP_BYTE_ARRAY6_TYPE = 18
FWP_SINGLE_DATA_TYPE_MAX = 0xFF
# FWP_CONDITION_VALUE0 扩展类型 (FWP_DATA_TYPE 续, 值 > 0xFF).
FWP_V4_ADDR_MASK = 0x100   # 256
FWP_V6_ADDR_AND_MASK = 0x101  # 257
FWP_RANGE_TYPE = 0x102     # 258
# 别名 (win_wfp.py 用 _TYPE 后缀形式引用, 保持向后兼容).
FWP_V4_ADDR_MASK_TYPE = FWP_V4_ADDR_MASK
FWP_V6_ADDR_AND_MASK_TYPE = FWP_V6_ADDR_AND_MASK

# FWPM_SESSION0 flags (FWPM_SESSION_FLAG_*).
FWPM_SESSION_FLAG_NONE = 0x0
FWPM_SESSION_FLAG_DYNAMIC = 0x00000001  # session 内添加的对象随 session 结束自动删除
# 别名: 旧代码用 FWP_SESSION_FLAG_NONE 命名 (SDK 实际为 FWPM_SESSION_FLAG_*).
FWP_SESSION_FLAG_NONE = FWPM_SESSION_FLAG_NONE

# FWPM_SUBLAYER0 flags.
FWPM_SUBLAYER_FLAG_PERSISTENT = 0x00000001

# FWPM_FILTER0 flags.
FWPM_FILTER_FLAG_NONE = 0x00000000
FWPM_FILTER_FLAG_PERSISTENT = 0x00000001

# Sublayer key (固定合法 GUID, 幂等安装/卸载时用同一 key). 必须是合法
# UUID 字符串, win_wfp._guid_from_str 用 uuid.UUID() 解析, 非法字符串会
# 直接 ValueError 导致 WFP 安装从未执行 (review CRITICAL #2).
JBX_SUBLAYER_KEY = "8F2A1B3C-4D5E-6F70-8190-123456789ABC"

# Filter key (固定合法 GUID, 幂等安装/卸载时按 key 删除).
JBX_FILTER_BLOCK_KEY_V4 = "9A3B2C1D-5E6F-7081-9012-3456789ABCDE"
JBX_FILTER_BLOCK_KEY_V6 = "AB4C3D2E-6F70-8192-0123-456789ABCDEF"
JBX_FILTER_PERMIT_KEY_V4 = "BC5D4E3F-7081-9203-1234-56789ABCDEF0"
JBX_FILTER_PERMIT_KEY_V6 = "CD6E5F40-8192-0314-2345-6789ABCDEF01"

# 出站代理端口范围 (默认, 与 docs 6.6 对齐).
DEFAULT_PROXY_PORT_RANGE_START = 60080
DEFAULT_PROXY_PORT_RANGE_END = 60089

# Permit filter 放行的 loopback 地址 (IPv4 整数表示, 127.0.0.1).
# WFP FWP_V4_ADDR_AND_MASK.addr 要求 host byte order (NOT network order).
# host order: 127.0.0.1 -> 0x7F000001; 旧值 0x0100007F 是网络序, 会让
# Permit filter 匹配 1.0.0.127 而非 127.0.0.1 (review CRITICAL #4).
LOOPBACK_IPV4_INT = 0x7F000001  # 127.0.0.1 in host byte order

# ---------------------------------------------------------------------------
# 安装状态注册表路径 (幂等标记 + SID 缓存 + 代理端口等).
# HKLM\Software\JiuwenBox\WindowsSandbox
# ---------------------------------------------------------------------------
REG_BASE_KEY = r"Software\JiuwenBox\WindowsSandbox"
REG_VALUE_INSTALLED = "installed"
REG_VALUE_SANDBOX_USER_SID = "sandbox_user_sid"
REG_VALUE_SYNTHETIC_WRITE_SID = "synthetic_write_sid"
REG_VALUE_SANDBOX_USER_PW = "sandbox_user_pw_encrypted"
REG_VALUE_READ_ACL_PROGRESS = "read_acl_progress"

# UAC 提权子进程的命令行标记.
INSTALL_SUBCOMMAND = "--install"
UNINSTALL_SUBCOMMAND = "--uninstall"
