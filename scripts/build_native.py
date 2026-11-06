#!/usr/bin/env python3
"""Build the optional AHPS native libraries using an installed C compiler."""

import argparse
import ctypes
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "simd": ("ahps_simd.c", "ahps_abi_version", 206),
    "rans": ("ahps_rans.c", "ahps_rans_abi_version", 1),
}
VERIFY_CODE = """
import ctypes
import sys
library = ctypes.CDLL(sys.argv[1])
version = getattr(library, sys.argv[2])
version.argtypes = []
version.restype = ctypes.c_uint32
actual = version()
expected = int(sys.argv[3])
if actual != expected:
    raise SystemExit(f"ABI mismatch: expected {expected}, received {actual}")
print(f"ABI {actual} verified")
"""


def simd_supported():
    """Mirror the runtime's conservative AVX2 host check without loading it."""
    if platform.machine().lower() not in ("amd64", "x86_64"):
        return False
    try:
        if sys.platform == "win32":
            query = ctypes.windll.kernel32.IsProcessorFeaturePresent
            query.argtypes = [ctypes.c_uint]
            query.restype = ctypes.c_int
            return bool(query(40))
        if sys.platform == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.optional.avx2_0"],
                capture_output=True, text=True, check=True, timeout=10,
            )
            return result.stdout.strip() == "1"
        with open("/proc/cpuinfo", encoding="utf-8") as stream:
            flags = next(line for line in stream if line.startswith("flags"))
        return {"avx2", "popcnt"}.issubset(flags.split())
    except (AttributeError, OSError, StopIteration, subprocess.SubprocessError, ValueError):
        return False


def find_compiler(requested, system):
    names = [requested] if requested else (["clang"] if system == "Darwin" else ["gcc", "clang"])
    if system == "Windows" and not requested:
        names = ["gcc"]
    for name in names:
        path = shutil.which(name)
        if path is None:
            continue
        compiler = Path(path).resolve()
        result = subprocess.run(
            [str(compiler), "--version"], capture_output=True, text=True, check=True, timeout=10,
        )
        description = result.stdout.strip()
        is_clang = "clang" in description.lower()
        is_gcc = "gcc" in description.lower() or "Free Software Foundation" in description
        if system == "Windows" and (not is_gcc or is_clang):
            raise ValueError("Windows builds require MinGW GCC. Pass its gcc.exe with --compiler.")
        if system == "Darwin" and not is_clang:
            raise ValueError("macOS builds require Clang. Pass its clang executable with --compiler.")
        if not (is_clang or is_gcc):
            raise ValueError("The compiler must be GCC or Clang.")
        print(description.splitlines()[0], flush=True)
        return compiler
    raise ValueError("C compiler not found. Put GCC/Clang on PATH or pass --compiler with its executable path.")


def library_name(target, system):
    if system == "Windows":
        return f"ahps_{target}.dll"
    extension = "dylib" if system == "Darwin" else "so"
    return f"libahps_{target}.{extension}"


def compile_command(compiler, target, destination, system):
    command = [str(compiler), "-O3", "-std=c11", "-Wall", "-Wextra", "-Werror"]
    if target == "simd":
        command += ["-mavx2", "-mpopcnt"]
    if system == "Windows":
        command += ["-shared", "-static-libgcc", "-Wl,--no-insert-timestamp", "-Wl,--disable-auto-image-base"]
    elif system == "Darwin":
        command += ["-dynamiclib", "-fPIC", f"-Wl,-install_name,@rpath/{destination.name}"]
    else:
        command += ["-shared", "-fPIC"]
    command += [str(ROOT / TARGETS[target][0]), "-o", str(destination)]
    if target == "simd" and system != "Windows":
        command.append("-lm")
    return command


def install_libraries(staged, output_directory, staging_directory):
    """Back up existing files before installing the fully verified build set."""
    backup_directory = staging_directory / "backups"
    backup_directory.mkdir()
    backups = {}
    for source in staged:
        destination = output_directory / source.name
        if destination.exists():
            backup = backup_directory / source.name
            shutil.copy2(destination, backup)
            backups[destination] = backup
    installed = []
    try:
        for source in staged:
            destination = output_directory / source.name
            os.replace(source, destination)
            installed.append(destination)
    except OSError:
        for destination in reversed(installed):
            if destination in backups:
                os.replace(backups[destination], destination)
            else:
                destination.unlink()
        raise
    for destination in installed:
        print(f"Built {destination}", flush=True)


def build(args):
    system = platform.system()
    if system not in ("Windows", "Linux", "Darwin"):
        raise ValueError(f"Unsupported build platform: {system}")
    targets = list(TARGETS) if args.target == "all" else [args.target]
    if "simd" in targets and not simd_supported():
        if args.target == "simd":
            raise ValueError("SIMD requires an x86-64 host with confirmed AVX2 support.")
        targets.remove("simd")
        print("Building portable rANS only; the host did not confirm x86-64 AVX2 support.", flush=True)
    compiler = find_compiler(args.compiler, system)
    output_directory = args.output_dir.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    staging_directory = Path(tempfile.mkdtemp(prefix=".ahps-native-", dir=output_directory)).resolve()
    if staging_directory.parent != output_directory:
        raise ValueError("Build staging directory must stay inside the output directory.")
    installing = False
    try:
        staged = []
        for target in targets:
            destination = staging_directory / library_name(target, system)
            command = compile_command(compiler, target, destination, system)
            print(subprocess.list2cmdline(command), flush=True)
            subprocess.run(command, cwd=ROOT, check=True, timeout=120)
            _, symbol, abi = TARGETS[target]
            # The verifying process exits before installation, releasing DLL handles on Windows.
            subprocess.run(
                [sys.executable, "-I", "-c", VERIFY_CODE, str(destination), symbol, str(abi)],
                check=True, timeout=30,
            )
            staged.append(destination)
        installing = True
        install_libraries(staged, output_directory, staging_directory)
        installing = False
    finally:
        if installing:
            print(f"Installation failed; staged files and backups retained at {staging_directory}", file=sys.stderr)
        else:
            shutil.rmtree(staging_directory)


def main():
    parser = argparse.ArgumentParser(
        description="Build optional AHPS libraries from local C sources using an installed compiler.",
        epilog="Default all builds rANS and adds SIMD when the host confirms x86-64 AVX2 support.",
    )
    parser.add_argument("--target", choices=("all", "simd", "rans"), default="all", help="Libraries to build (default: all)")
    parser.add_argument("--compiler", help="GCC executable, or Clang on Linux/macOS (default: search PATH)")
    parser.add_argument("--output-dir", type=Path, default=ROOT, help="Destination directory (default: repository root)")
    args = parser.parse_args()
    try:
        build(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Build failed: {exc}\n")


if __name__ == "__main__":
    main()
