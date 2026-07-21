# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Windows Job Object 资源限制 (等价 Linux cgroup).

对齐 docs/window沙箱.md 6.8:
  - 内存上限   -> JobObjectExtendedLimitInformation.ProcessMemoryLimit
  - CPU 速率   -> JobObjectCpuRateControlInformation.CpuRate
  - 进程数上限 -> JobObjectBasicLimitInformation.ActiveProcessLimit
  - 全部清理   -> JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

通过 ``ctypes`` 调用 kernel32.dll 实现. 所有 win32 调用延迟到函数体内,
模块顶层只定义结构体 (ctypes.Structure 无副作用), Linux 下可 import.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.supervisor import win_constants as const

configure_logging()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job Object 相关结构体 (JOBOBJECT_*_INFORMATION 的 C 布局).
# 详见 winnt.h / jobapi2.h.
# ---------------------------------------------------------------------------
class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", wintypes.ULARGE_INTEGER),
        ("WriteOperationCount", wintypes.ULARGE_INTEGER),
        ("OtherOperationCount", wintypes.ULARGE_INTEGER),
        ("ReadTransferCount", wintypes.ULARGE_INTEGER),
        ("WriteTransferCount", wintypes.ULARGE_INTEGER),
        ("OtherTransferCount", wintypes.ULARGE_INTEGER),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.POINTER(wintypes.ULONG)),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("ControlFlags", wintypes.DWORD),
        ("CpuRate", wintypes.DWORD),
    ]


_kernel32: ctypes.WinDLL | None = None


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError(
            f"win_job 仅在 Windows 平台可用; 当前平台 {sys.platform!r}"
        )


def _get_kernel32() -> ctypes.WinDLL:
    global _kernel32
    if _kernel32 is None:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # 函数原型.
        _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        _kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,  # JOBOBJECTINFOCLASS
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        _kernel32.SetInformationJobObject.restype = wintypes.BOOL
        _kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE,
        ]
        _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _kernel32.CloseHandle.restype = wintypes.BOOL
        _kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ]
        _kernel32.OpenProcess.restype = wintypes.HANDLE
    return _kernel32


def create_job(
    memory_max: int | None = None,
    cpu_rate: int | None = None,
    max_processes: int | None = None,
) -> int:
    """创建一个 Job Object 并施加资源限制, 返回 Job handle.

    所有参数为 None 表示无限制 (但仍创建 Job 以便 KILL_ON_JOB_CLOSE 生效).
    """
    _require_windows()
    kernel32 = _get_kernel32()

    job_handle = kernel32.CreateJobObjectW(None, None)
    if not job_handle:
        raise ctypes.WinError(ctypes.get_last_error())

    # --- 1. 扩展限制: 内存上限 + KILL_ON_JOB_CLOSE ---
    ext = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    ext.BasicLimitInformation.LimitFlags = const.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if memory_max is not None and memory_max > 0:
        ext.BasicLimitInformation.LimitFlags |= const.JOB_OBJECT_LIMIT_PROCESS_MEMORY
        ext.ProcessMemoryLimit = int(memory_max)
    if max_processes is not None and max_processes > 0:
        ext.BasicLimitInformation.LimitFlags |= const.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        ext.BasicLimitInformation.ActiveProcessLimit = int(max_processes)

    ok = kernel32.SetInformationJobObject(
        job_handle,
        const.JobObjectExtendedLimitInformation,
        ctypes.byref(ext),
        ctypes.sizeof(ext),
    )
    if not ok:
        kernel32.CloseHandle(job_handle)
        raise ctypes.WinError(ctypes.get_last_error())

    # --- 2. CPU 速率限制 ---
    if cpu_rate is not None and 0 < cpu_rate <= 100:
        rate = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION()
        rate.ControlFlags = (
            const.JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
            | const.JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
        )
        # CpuRate 以 0.01% 为单位, 百分比 * 100.
        rate.CpuRate = int(cpu_rate * 100)
        ok = kernel32.SetInformationJobObject(
            job_handle,
            const.JobObjectCpuRateControlInformation,
            ctypes.byref(rate),
            ctypes.sizeof(rate),
        )
        if not ok:
            logger.warning(
                "设置 Job CPU 速率限制失败 (cpu_rate=%s), 继续运行无 CPU 限制",
                cpu_rate,
                exc_info=True,
            )

    logger.info(
        "创建 Job Object: handle=%s memory=%s cpu_rate=%s max_procs=%s",
        job_handle, memory_max, cpu_rate, max_processes,
    )
    return int(job_handle)


def assign_process(job_handle: int, process_handle: int) -> None:
    """把进程加入 Job Object. 子进程自动继承 (除非 CREATE_BREAKAWAY_FROM_JOB)."""
    _require_windows()
    kernel32 = _get_kernel32()
    ok = kernel32.AssignProcessToJobObject(
        wintypes.HANDLE(job_handle), wintypes.HANDLE(process_handle),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def assign_process_by_pid(job_handle: int, pid: int) -> None:
    """按 pid 打开进程并加入 Job (用于无法直接拿到 handle 的子进程)."""
    _require_windows()
    kernel32 = _get_kernel32()
    # PROCESS_SET_QUOTA (0x0100) | PROCESS_TERMINATE (0x0001) | PROCESS_QUERY_LIMITED_INFORMATION (0x1000)
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001
    proc_handle = kernel32.OpenProcess(
        PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid,
    )
    if not proc_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        assign_process(job_handle, proc_handle)
    finally:
        kernel32.CloseHandle(proc_handle)


def close_job(job_handle: int) -> None:
    """关闭 Job handle -> 内核强制终止所有成员进程 (KILL_ON_JOB_CLOSE)."""
    if job_handle is None or job_handle == 0:
        return
    _require_windows()
    kernel32 = _get_kernel32()
    kernel32.CloseHandle(wintypes.HANDLE(job_handle))
    logger.info("关闭 Job Object handle=%s, 成员进程被强制终止", job_handle)


def teardown(job_handle: int | None) -> None:
    """teardown: 关闭 Job (best-effort, 不抛错)."""
    if job_handle is None:
        return
    try:
        close_job(job_handle)
    except Exception:  # noqa: BLE001
        logger.warning("Job teardown 失败 handle=%s", job_handle, exc_info=True)
