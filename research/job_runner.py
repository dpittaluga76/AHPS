#!/usr/bin/env python3
"""Bound one Windows worker tree before its first instruction executes.

run_job receives an absolute time.monotonic deadline, never a fresh per-phase
timeout. A Job Object enforces aggregate COMMITTED memory, denies breakaway,
and kills descendants when its handle closes. Resident memory is a separate
sampled quantity; phase markers are JSON objects containing a string "phase".

Windows API contracts:
https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information
https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
https://learn.microsoft.com/windows/win32/procthread/job-objects
"""

import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


if os.name != "nt":
    raise RuntimeError("This experimental runner requires Windows Job Objects")

import msvcrt


K = C.WinDLL("kernel32", use_last_error=True)
SIZE_T = C.c_size_t
ULONG_PTR = C.c_size_t
INVALID_HANDLE = C.c_void_p(-1).value
JOB_MEMORY = 0x200
KILL_ON_CLOSE = 0x2000
DIE_ON_UNHANDLED_EXCEPTION = 0x400
BREAKAWAY_FLAGS = 0x800 | 0x1000
WAIT_TIMEOUT = 258
STILL_ACTIVE = 259
MEMORY_LIMIT_MESSAGE = 10
TIMEOUT_EXIT = 0xE0000001
MEMORY_EXIT = 0xE0000002
RUNNER_EXIT = 0xE0000003


class IO_COUNTERS(C.Structure):
    _fields_ = [(name, C.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class BASIC_LIMIT(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_longlong),
        ("PerJobUserTimeLimit", C.c_longlong), ("LimitFlags", W.DWORD),
        ("MinimumWorkingSetSize", SIZE_T), ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", W.DWORD), ("Affinity", ULONG_PTR),
        ("PriorityClass", W.DWORD), ("SchedulingClass", W.DWORD)]


class EXTENDED_LIMIT(C.Structure):
    _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", SIZE_T), ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]


class COMPLETION_PORT(C.Structure):
    _fields_ = [("CompletionKey", C.c_void_p), ("CompletionPort", W.HANDLE)]


class BASIC_ACCOUNTING(C.Structure):
    _fields_ = [(name, C.c_longlong) for name in ("TotalUserTime", "TotalKernelTime",
        "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [
        (name, W.DWORD) for name in ("TotalPageFaultCount", "TotalProcesses",
        "ActiveProcesses", "TotalTerminatedProcesses")]


class STARTUPINFO(C.Structure):
    _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR),
        ("lpDesktop", W.LPWSTR), ("lpTitle", W.LPWSTR),
        *[(name, W.DWORD) for name in ("dwX", "dwY", "dwXSize", "dwYSize",
            "dwXCountChars", "dwYCountChars", "dwFillAttribute", "dwFlags")],
        ("wShowWindow", W.WORD), ("cbReserved2", W.WORD),
        ("lpReserved2", C.POINTER(C.c_byte)), ("hStdInput", W.HANDLE),
        ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE)]


class STARTUPINFOEX(C.Structure):
    _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", C.c_void_p)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE),
        ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]


class PROCESS_MEMORY_COUNTERS_EX(C.Structure):
    _fields_ = [("cb", W.DWORD), ("PageFaultCount", W.DWORD)] + [
        (name, SIZE_T) for name in ("PeakWorkingSetSize", "WorkingSetSize",
        "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


def bind(name, restype, *argtypes):
    function = getattr(K, name)
    function.restype = restype
    function.argtypes = argtypes
    return function


CreateJobObject = bind("CreateJobObjectW", W.HANDLE, C.c_void_p, W.LPCWSTR)
SetJob = bind("SetInformationJobObject", W.BOOL, W.HANDLE, C.c_int, C.c_void_p, W.DWORD)
QueryJob = bind("QueryInformationJobObject", W.BOOL, W.HANDLE, C.c_int,
    C.c_void_p, W.DWORD, C.POINTER(W.DWORD))
AssignJob = bind("AssignProcessToJobObject", W.BOOL, W.HANDLE, W.HANDLE)
TerminateJob = bind("TerminateJobObject", W.BOOL, W.HANDLE, W.UINT)
CloseHandle = bind("CloseHandle", W.BOOL, W.HANDLE)
CreatePort = bind("CreateIoCompletionPort", W.HANDLE, W.HANDLE, W.HANDLE, ULONG_PTR, W.DWORD)
GetCompletion = bind("GetQueuedCompletionStatus", W.BOOL, W.HANDLE,
    C.POINTER(W.DWORD), C.POINTER(ULONG_PTR), C.POINTER(C.c_void_p), W.DWORD)
CreateProcess = bind("CreateProcessW", W.BOOL, W.LPCWSTR, W.LPWSTR,
    C.c_void_p, C.c_void_p, W.BOOL, W.DWORD, C.c_void_p, W.LPCWSTR,
    C.POINTER(STARTUPINFOEX), C.POINTER(PROCESS_INFORMATION))
ResumeThread = bind("ResumeThread", W.DWORD, W.HANDLE)
TerminateProcess = bind("TerminateProcess", W.BOOL, W.HANDLE, W.UINT)
WaitForSingleObject = bind("WaitForSingleObject", W.DWORD, W.HANDLE, W.DWORD)
GetExitCode = bind("GetExitCodeProcess", W.BOOL, W.HANDLE, C.POINTER(W.DWORD))
OpenProcess = bind("OpenProcess", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
GetMemory = bind("K32GetProcessMemoryInfo", W.BOOL, W.HANDLE,
    C.POINTER(PROCESS_MEMORY_COUNTERS_EX), W.DWORD)
GetImage = bind("QueryFullProcessImageNameW", W.BOOL, W.HANDLE, W.DWORD,
    W.LPWSTR, C.POINTER(W.DWORD))
GetTimes = bind("GetProcessTimes", W.BOOL, W.HANDLE, *([C.POINTER(W.FILETIME)] * 4))
InitAttributes = bind("InitializeProcThreadAttributeList", W.BOOL,
    C.c_void_p, W.DWORD, W.DWORD, C.POINTER(SIZE_T))
UpdateAttributes = bind("UpdateProcThreadAttribute", W.BOOL, C.c_void_p,
    W.DWORD, ULONG_PTR, C.c_void_p, SIZE_T, C.c_void_p, C.c_void_p)
DeleteAttributes = bind("DeleteProcThreadAttributeList", None, C.c_void_p)


def require(result, operation):
    if not result:
        raise C.WinError(C.get_last_error(), operation)
    return result


def query(job, info_class, cls):
    value = cls()
    require(QueryJob(job, info_class, C.byref(value), C.sizeof(value), None),
        "QueryInformationJobObject")
    return value


def job_pids(job):
    capacity = 64
    while capacity <= 65536:
        storage = C.create_string_buffer(8 + C.sizeof(ULONG_PTR) * capacity)
        if QueryJob(job, 3, storage, C.sizeof(storage), None):
            count = W.DWORD.from_buffer(storage, 4).value
            return list((ULONG_PTR * count).from_buffer(storage, 8))
        if C.get_last_error() != 234:  # ERROR_MORE_DATA: job changed while sampled.
            raise C.WinError(C.get_last_error(), "Query job PID list")
        capacity *= 2
    raise RuntimeError("Job PID list exceeded diagnostic capacity")


def phase_from_file(path, previous):
    if path is None:
        return previous
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        phase = value["phase"]
        return phase if isinstance(phase, str) and phase else previous
    except (OSError, ValueError, KeyError, TypeError):
        return previous  # An atomic writer is preferred; never erase a valid phase.


def read_capture(path, limit=2 * 1024 * 1024):
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    return data[:limit].decode("utf-8", errors="replace"), len(data) > limit


def run_job(argv, *, cwd, deadline, memory_limit_bytes=8 * 1024**3,
            phase_file=None, stdout_path=None, stderr_path=None,
            sample_interval=0.02, env=None):
    """Run one complete worker under a single deadline; return JSON-safe evidence.

    Output paths must be new. If omitted, captures use temporary files and their
    bounded contents are returned. Caller-owned phase-file changes do not alter
    the deadline. This function is intended for one serial worker at a time.
    """
    if not argv or not math.isfinite(deadline) or memory_limit_bytes <= 0:
        raise ValueError("argv, finite absolute deadline, and positive memory cap required")
    if not 0 < sample_interval <= 1:
        raise ValueError("sample_interval must be in (0, 1]")
    argv = [str(arg) for arg in argv]
    executable = Path(argv[0]).resolve()
    if not executable.is_file():
        raise ValueError("argv[0] must identify an existing executable")
    started = time.monotonic()
    result = {"argv": argv, "cwd": str(Path(cwd).resolve()),
        "deadline_monotonic": deadline, "started_monotonic": started,
        "memory_limit_bytes": int(memory_limit_bytes),
        "memory_limit_kind": "aggregate job committed memory",
        "sample_interval_seconds": sample_interval, "status": "runner_error",
        "processes": [], "phases": {}, "events": [],
        "observed_aggregate_working_set_peak_bytes": 0,
        "resident_metric": "sampled sum of current working sets of live job members; not an exact simultaneous or kernel lifetime peak",
        "sample_failures": [], "tree_cleaned": False,
        "assignment_before_resume": False, "breakaway_allowed": False}
    job = port = None
    pi = PROCESS_INFORMATION()
    assigned = resumed = False
    files = []
    attributes = None
    attributes_initialized = False
    handles = {}
    records = {}
    stopped = threading.Event()
    stop_lock = threading.Lock()
    termination = {}
    watchdog = None
    phase = "startup"
    sample_number = 0
    temporary = tempfile.TemporaryDirectory(prefix="ahps-job-")
    stdout_path = Path(stdout_path) if stdout_path else Path(temporary.name) / "stdout.log"
    stderr_path = Path(stderr_path) if stderr_path else Path(temporary.name) / "stderr.log"
    result.update(stdout_path=str(stdout_path), stderr_path=str(stderr_path))

    def terminate(reason, code):
        with stop_lock:
            if not termination and not stopped.is_set():
                termination.update(reason=reason, requested_monotonic=time.monotonic())
                if not TerminateJob(job, code):
                    termination["terminate_error"] = C.get_last_error()

    def track(pid):
        if pid in handles:
            return
        handle = OpenProcess(0x1000 | 0x10 | 0x100000, False, pid)
        if not handle:
            result["sample_failures"].append({"pid": pid, "operation": "OpenProcess",
                "winerror": C.get_last_error()})
            return
        handles[pid] = handle
        record = {"pid": pid, "first_observed_phase": phase,
            "observed_working_set_peak_bytes": 0, "phase_observed_working_set_peaks": {}}
        size = W.DWORD(32768)
        name = C.create_unicode_buffer(size.value)
        if GetImage(handle, 0, name, C.byref(size)):
            record["image"] = name.value
        times = [W.FILETIME() for _ in range(4)]
        if GetTimes(handle, *(C.byref(value) for value in times)):
            record["creation_filetime_100ns"] = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        records[pid] = record

    def completions():
        while True:
            message, key, value = W.DWORD(), ULONG_PTR(), C.c_void_p()
            if not GetCompletion(port, C.byref(message), C.byref(key), C.byref(value), 0):
                if C.get_last_error() == WAIT_TIMEOUT:
                    return
                raise C.WinError(C.get_last_error(), "GetQueuedCompletionStatus")
            event = {"message": message.value, "pid": value.value,
                "elapsed_seconds": time.monotonic() - started, "phase": phase}
            result["events"].append(event)
            if message.value == 6 and value.value:  # JOB_OBJECT_MSG_NEW_PROCESS
                track(value.value)
            if message.value == MEMORY_LIMIT_MESSAGE:
                terminate("memory_limit", MEMORY_EXIT)

    def sample():
        nonlocal phase, sample_number
        phase = phase_from_file(phase_file, phase)
        live = job_pids(job)
        aggregate = 0
        missing = 0
        for pid in live:
            track(pid)
            handle = handles.get(pid)
            if not handle or WaitForSingleObject(handle, 0) == 0:
                missing += 1
                continue
            counters = PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = C.sizeof(counters)
            if not GetMemory(handle, C.byref(counters), counters.cb):
                missing += 1
                result["sample_failures"].append({"pid": pid, "operation": "GetProcessMemoryInfo",
                    "winerror": C.get_last_error()})
                continue
            resident = int(counters.WorkingSetSize)
            aggregate += resident
            record = records[pid]
            record["observed_working_set_peak_bytes"] = max(record["observed_working_set_peak_bytes"], resident)
            record["phase_observed_working_set_peaks"][phase] = max(
                record["phase_observed_working_set_peaks"].get(phase, 0), resident)
            record["peak_working_set_bytes_kernel"] = int(counters.PeakWorkingSetSize)
            record["peak_pagefile_usage_bytes_kernel"] = int(counters.PeakPagefileUsage)
        elapsed = time.monotonic() - started
        summary = result["phases"].setdefault(phase, {
            "observed_aggregate_working_set_peak_bytes": 0, "samples": 0,
            "first_sample_elapsed_seconds": elapsed, "samples_with_missing_members": 0})
        summary["samples"] += 1
        summary["last_sample_elapsed_seconds"] = elapsed
        summary["samples_with_missing_members"] += bool(missing)
        summary["observed_aggregate_working_set_peak_bytes"] = max(
            summary["observed_aggregate_working_set_peak_bytes"], aggregate)
        result["observed_aggregate_working_set_peak_bytes"] = max(
            result["observed_aggregate_working_set_peak_bytes"], aggregate)
        sample_number += 1

    try:
        if time.monotonic() >= deadline:
            result["status"] = "deadline_before_launch"
            result["tree_cleaned"] = True
            return result
        job = require(CreateJobObject(None, None), "CreateJobObject")
        limits = EXTENDED_LIMIT()
        limits.BasicLimitInformation.LimitFlags = JOB_MEMORY | KILL_ON_CLOSE | DIE_ON_UNHANDLED_EXCEPTION
        limits.JobMemoryLimit = int(memory_limit_bytes)
        require(SetJob(job, 9, C.byref(limits), C.sizeof(limits)), "Set aggregate job memory limit")
        applied = query(job, 9, EXTENDED_LIMIT)
        if applied.JobMemoryLimit != memory_limit_bytes or applied.BasicLimitInformation.LimitFlags != limits.BasicLimitInformation.LimitFlags:
            raise RuntimeError("Kernel job limits failed exact readback")
        if applied.BasicLimitInformation.LimitFlags & BREAKAWAY_FLAGS:
            raise RuntimeError("Job unexpectedly permits breakaway")
        result["verified_limit_flags"] = int(applied.BasicLimitInformation.LimitFlags)
        port = require(CreatePort(INVALID_HANDLE, None, 0, 1), "CreateIoCompletionPort")
        association = COMPLETION_PORT(C.c_void_p(1), port)
        require(SetJob(job, 7, C.byref(association), C.sizeof(association)), "Associate job completion port")
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stdin = open(os.devnull, "rb")
        files.append(stdin)
        stdout = open(stdout_path, "xb")
        files.append(stdout)
        stderr = open(stderr_path, "xb")
        files.append(stderr)
        inherited = (W.HANDLE * 3)(*(msvcrt.get_osfhandle(file.fileno()) for file in files))
        for handle in inherited:
            os.set_handle_inheritable(handle, True)
        needed = SIZE_T()
        InitAttributes(None, 1, 0, C.byref(needed))
        if not needed.value:
            raise C.WinError(C.get_last_error(), "Initialize attribute-list size")
        attribute_buffer = C.create_string_buffer(needed.value)
        attributes = C.cast(attribute_buffer, C.c_void_p)
        require(InitAttributes(attributes, 1, 0, C.byref(needed)), "Initialize attribute list")
        attributes_initialized = True
        require(UpdateAttributes(attributes, 0, 0x20002, inherited, C.sizeof(inherited), None, None),
            "Restrict inherited handle list")
        startup = STARTUPINFOEX()
        startup.StartupInfo.cb = C.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x100 | 0x1  # USESTDHANDLES | USESHOWWINDOW
        startup.StartupInfo.wShowWindow = 0
        startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = inherited
        startup.lpAttributeList = attributes
        environment = None
        if env is not None:
            environment = C.create_unicode_buffer("\0".join(
                f"{key}={value}" for key, value in sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0")
        command = C.create_unicode_buffer(subprocess.list2cmdline(argv))
        flags = 0x4 | 0x08000000 | 0x00080000 | 0x400  # SUSPENDED, NO_WINDOW, EXTENDED_STARTUPINFO, UNICODE_ENV
        require(CreateProcess(str(executable), command, None, None, True, flags,
            environment, str(Path(cwd).resolve()), C.byref(startup), C.byref(pi)), "CreateProcess suspended")
        result["pid"] = int(pi.dwProcessId)
        require(AssignJob(job, pi.hProcess), "Assign suspended worker to limited job")
        assigned = True
        result["assignment_before_resume"] = True
        track(int(pi.dwProcessId))

        def watch_deadline():
            if not stopped.wait(max(0.0, deadline - time.monotonic())):
                terminate("timeout", TIMEOUT_EXIT)

        watchdog = threading.Thread(target=watch_deadline, name="ahps-job-deadline", daemon=True)
        watchdog.start()
        if time.monotonic() >= deadline:
            terminate("timeout", TIMEOUT_EXIT)
        else:
            previous_suspend_count = ResumeThread(pi.hThread)
            if previous_suspend_count != 1:
                raise C.WinError(C.get_last_error(), f"ResumeThread returned {previous_suspend_count}")
            resumed = True
        CloseHandle(pi.hThread)
        pi.hThread = None
        while True:
            completions()
            sample()
            accounting = query(job, 1, BASIC_ACCOUNTING)
            if accounting.ActiveProcesses == 0:
                break
            # ActiveProcesses includes a console helper that can outlive a
            # successful console Python worker briefly. Wait for the job tree,
            # under the same deadline, instead of declaring a worker failure.
            if termination:
                break
            stopped.wait(min(sample_interval, max(0.0, deadline - time.monotonic())))
        if termination:
            cleanup_deadline = time.monotonic() + 5.0
            while query(job, 1, BASIC_ACCOUNTING).ActiveProcesses and time.monotonic() < cleanup_deadline:
                time.sleep(0.01)
        completions()
        result["tree_cleaned"] = query(job, 1, BASIC_ACCOUNTING).ActiveProcesses == 0
        code = W.DWORD()
        require(GetExitCode(pi.hProcess, C.byref(code)), "Read worker exit code")
        result["returncode"] = int(code.value)
        result["status"] = termination.get("reason", "ok" if code.value == 0 else "worker_error")
        if not result["tree_cleaned"]:
            result["status"] = "cleanup_failed"
    except Exception as exc:
        result.update(status="runner_error", error=f"{type(exc).__name__}: {exc}")
    finally:
        stopped.set()
        if watchdog:
            watchdog.join()
        cleanup_deadline = time.monotonic() + 5.0
        if job:
            try:
                info = query(job, 9, EXTENDED_LIMIT)
                accounting = query(job, 1, BASIC_ACCOUNTING)
                result["peak_job_committed_bytes_kernel"] = int(info.PeakJobMemoryUsed)
                result["peak_single_process_committed_bytes_kernel"] = int(info.PeakProcessMemoryUsed)
                result["total_job_processes"] = int(accounting.TotalProcesses)
                if accounting.ActiveProcesses:
                    TerminateJob(job, RUNNER_EXIT)
                    while query(job, 1, BASIC_ACCOUNTING).ActiveProcesses and time.monotonic() < cleanup_deadline:
                        time.sleep(0.01)
                result["tree_cleaned"] = query(job, 1, BASIC_ACCOUNTING).ActiveProcesses == 0
            except Exception as exc:
                result["cleanup_error"] = str(exc)
                result["tree_cleaned"] = False
            CloseHandle(job)  # KILL_ON_JOB_CLOSE is the final safety net.
        if pi.hProcess and not assigned:
            TerminateProcess(pi.hProcess, RUNNER_EXIT)
        # Job ActiveProcesses can reach zero before the last process handle is
        # signaled. Keep every observed handle and confirm actual termination.
        root_terminated = not pi.hProcess
        if pi.hProcess:
            wait_ms = max(0, int((cleanup_deadline - time.monotonic()) * 1000))
            root_terminated = WaitForSingleObject(pi.hProcess, wait_ms) == 0
            code = W.DWORD()
            if root_terminated and GetExitCode(pi.hProcess, C.byref(code)):
                result["returncode"] = int(code.value)
        for pid, handle in handles.items():
            wait_ms = max(0, int((cleanup_deadline - time.monotonic()) * 1000))
            terminated = WaitForSingleObject(handle, wait_ms) == 0
            counters = PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = C.sizeof(counters)
            if GetMemory(handle, C.byref(counters), counters.cb):
                records[pid]["peak_working_set_bytes_kernel"] = int(counters.PeakWorkingSetSize)
                records[pid]["peak_pagefile_usage_bytes_kernel"] = int(counters.PeakPagefileUsage)
            code = W.DWORD()
            if GetExitCode(handle, C.byref(code)):
                records[pid]["exit_code"] = int(code.value)
            records[pid]["terminated"] = terminated
            CloseHandle(handle)
        result["processes"] = list(records.values())
        if not job and not pi.hProcess:
            result["tree_cleaned"] = True
        result["tree_cleaned"] = (result["tree_cleaned"] and root_terminated
            and all(record["terminated"] for record in records.values()))
        if not result["tree_cleaned"] and pi.hProcess:
            result["status"] = "cleanup_failed"
        for handle in (pi.hThread, pi.hProcess, port):
            if handle:
                CloseHandle(handle)
        if attributes_initialized:
            DeleteAttributes(attributes)
        for file in files:
            file.close()
        for label, path in (("stdout", stdout_path), ("stderr", stderr_path)):
            try:
                if path.exists():
                    result[label], result[label + "_truncated"] = read_capture(path)
            except OSError as exc:
                result[label + "_capture_error"] = str(exc)
        result.update(elapsed_seconds=time.monotonic() - started, resumed=resumed,
            samples=sample_number, termination=termination)
        try:
            temporary.cleanup()
        except OSError as exc:
            result["temporary_cleanup_error"] = str(exc)
    return result


def validation_worker(mode, directory):
    directory = Path(directory)
    interpreter = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    script = str(Path(__file__).resolve())
    phase_file = directory / "phase.json"
    if mode == "memory":
        phase_file.write_text('{"phase":"encode"}', encoding="utf-8")
        first = subprocess.Popen([interpreter, "-I", "-B", script, "_allocate", str(directory / "first.ready"), "40"])
        until = time.monotonic() + 5
        while not (directory / "first.ready").exists():
            if first.poll() is not None or time.monotonic() > until:
                raise RuntimeError("First allocating child did not become ready")
            time.sleep(0.01)
        second = subprocess.Popen([interpreter, "-I", "-B", script, "_allocate", str(directory / "second.ready"), "40"])
        first.wait()
        second.wait()
    elif mode == "timeout":
        phase_file.write_text('{"phase":"decode"}', encoding="utf-8")
        children = [subprocess.Popen([interpreter, "-I", "-B", "-c", "import time; time.sleep(30)"]) for _ in range(2)]
        (directory / "children.json").write_text(json.dumps([child.pid for child in children]), encoding="utf-8")
        for child in children:
            child.wait()
    elif mode == "success":
        for phase in ("encode", "decode"):
            phase_file.write_text(json.dumps({"phase": phase}), encoding="utf-8")
            subprocess.run([interpreter, "-I", "-B", "-c", "import time; x=bytearray(4*1024*1024); time.sleep(0.15)"], check=True)
    else:
        raise ValueError(mode)


def self_test(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    reports = {}
    for mode in ("success", "memory", "timeout"):
        destination = directory / mode
        destination.mkdir(exist_ok=False)
        reports[mode] = run_job([str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()), "-I", "-B",
            str(Path(__file__).resolve()), "_worker", mode, str(destination)],
            cwd=destination, deadline=time.monotonic() + (1.5 if mode == "timeout" else 10),
            memory_limit_bytes=96 * 1024**2, phase_file=destination / "phase.json",
            stdout_path=destination / "stdout.log", stderr_path=destination / "stderr.log")
    checks = {
        "success": reports["success"]["status"] == "ok",
        "encode_and_decode_resident_samples": all(name in reports["success"]["phases"] for name in ("encode", "decode")),
        "aggregate_memory_limit_not_per_process": reports["memory"]["status"] == "memory_limit"
            and reports["memory"].get("total_job_processes", 0) >= 3
            and reports["memory"].get("peak_single_process_committed_bytes_kernel", 96*1024**2) < 96*1024**2,
        "second_child_allocation_denied": "Validation child allocation denied" in reports["memory"].get("stderr", "")
            and (directory / "memory" / "first.ready").exists()
            and not (directory / "memory" / "second.ready").exists(),
        "timeout_detected": reports["timeout"]["status"] == "timeout",
        "timeout_included_children": reports["timeout"].get("total_job_processes", 0) >= 3,
        "all_trees_cleaned": all(record["tree_cleaned"] for record in reports.values()),
        "all_observed_processes_terminated": all(process["terminated"] for record in reports.values() for process in record["processes"]),
        "assigned_before_resume": all(record["assignment_before_resume"] for record in reports.values()),
    }
    document = {"checks": checks, "ok": all(checks.values()), "runs": reports,
        "kernel_peak_note": "PeakJobMemoryUsed is retained verbatim. In validation it can exceed JobMemoryLimit on a denied allocation; this field is not used as the enforcement test. Enforcement is exact limit readback plus a job memory-limit notification and a failed second child allocation, while each child is below the aggregate limit."}
    (directory / "validation.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checks": checks, "ok": document["ok"], "report": str(directory / "validation.json")}))
    return 0 if document["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_worker":
        validation_worker(sys.argv[2], sys.argv[3])
    elif len(sys.argv) > 1 and sys.argv[1] == "_allocate":
        allocate = bind("VirtualAlloc", C.c_void_p, C.c_void_p, SIZE_T, W.DWORD, W.DWORD)
        size = int(sys.argv[3]) * 1024**2
        address = allocate(None, size, 0x3000, 4)
        if not address:
            raise C.WinError(C.get_last_error(), "Validation child allocation denied")
        C.memset(address, 0x5A, size)
        Path(sys.argv[2]).write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(30)
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--self-test", type=Path, required=True)
        raise SystemExit(self_test(parser.parse_args().self_test))
