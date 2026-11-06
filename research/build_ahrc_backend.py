"""Build the independent AHRC DLL and record compiler/dependency identities."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


HERE = Path(__file__).resolve().parent
COMPILER = Path("C:/msys64/ucrt64/bin/gcc.exe")
SOURCE = HERE / "ahrc_backend.c"
DLL = HERE / "ahrc_backend.dll"
MANIFEST = HERE / "ahrc_backend_build.json"
BELOW_NORMAL = 0x4000


def below_normal():
    if os.name != "nt":
        raise RuntimeError("This frozen build requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.SetPriorityClass.restype = wintypes.BOOL
    kernel.GetPriorityClass.argtypes = [wintypes.HANDLE]
    kernel.GetPriorityClass.restype = wintypes.DWORD
    handle = kernel.GetCurrentProcess()
    if not kernel.SetPriorityClass(handle, BELOW_NORMAL):
        raise ctypes.WinError(ctypes.get_last_error())
    actual = kernel.GetPriorityClass(handle)
    if actual != BELOW_NORMAL:
        raise RuntimeError("Below-normal build priority readback failed")
    return actual


def identity(path):
    path = Path(path).resolve()
    data = path.read_bytes()
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def loaded_dependency(name):
    """Resolve API-set imports to the actual DLL selected by Windows."""
    library = ctypes.WinDLL(name, use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetModuleFileNameW.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetModuleFileNameW(library._handle, buffer, len(buffer))
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value)


def build():
    priority = below_normal()
    source_before = identity(SOURCE)
    compiler_before = identity(COMPILER)
    command = [str(COMPILER), "-O3", "-std=c11", "-Wall", "-Wextra", "-Werror",
               "-shared", "-static-libgcc", "-Wl,--no-insert-timestamp", str(SOURCE), "-o", str(DLL)]
    compiler_version = subprocess.check_output([str(COMPILER), "--version"], text=True)
    result = subprocess.run(command, cwd=HERE, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Compilation failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    if identity(SOURCE) != source_before or identity(COMPILER) != compiler_before:
        raise RuntimeError("Source/compiler changed during compilation")
    objdump = COMPILER.with_name("objdump.exe")
    dependencies = []
    inspection = {"available": objdump.is_file()}
    if objdump.is_file():
        inspect_command = [str(objdump), "-p", str(DLL)]
        output = subprocess.check_output(inspect_command, text=True)
        names = sorted(set(re.findall(r"DLL Name:\s*(\S+)", output)), key=str.lower)
        if any(any(word in name.lower() for word in ("lzma", "zstd", "zlib", "deflate", "brotli", "lz4")) for name in names):
            raise RuntimeError("Unexpected compression-library import")
        for name in names:
            dependencies.append({"name": name, "identity": identity(loaded_dependency(name)),
                                 "resolution": "actual Windows loader module (API-set names may share a host)"})
        inspection.update(tool=identity(objdump), command=inspect_command, imported_dll_names=names, output=output)
    library = ctypes.CDLL(str(DLL))
    library.ahrc_version.argtypes = []
    library.ahrc_version.restype = ctypes.c_uint32
    if library.ahrc_version() != 1:
        raise RuntimeError("Unexpected backend ABI")
    manifest = {"schema": 1, "backend_abi": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
                "priority_class": priority, "source": source_before, "dll": identity(DLL),
                "build_script": identity(__file__), "compiler": compiler_before,
                "compiler_version": compiler_version, "command": command,
                "dependency_inspection": inspection, "dependencies": dependencies,
                "priority_after": below_normal()}
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(MANIFEST), "dll": manifest["dll"],
                      "dependencies": dependencies, "priority_class": priority}))


if __name__ == "__main__":
    build()
