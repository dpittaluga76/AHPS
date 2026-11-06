# Building optional native libraries

AHPS includes Python implementations of its codecs. The optional C libraries
accelerate compression and decompression. Build them from the checked-out
sources with Python and an installed C compiler.

| Platform | Compiler | rANS library | Core SIMD library |
| --- | --- | --- | --- |
| Windows | MinGW GCC | `ahps_rans.dll` | `ahps_simd.dll` on x86-64 with AVX2 |
| Linux | GCC or Clang | `libahps_rans.so` | `libahps_simd.so` on x86-64 with AVX2 and POPCNT |
| macOS | Clang | `libahps_rans.dylib` | `libahps_simd.dylib` on an Intel host that confirms AVX2 |

Apple Silicon uses portable native rANS and the Python implementation of the
core transforms. The build script checks the current host before compiling the
AVX2 kernel. These commands build for the machine and Python architecture in
use. Cross-compilation and MSVC are outside this script's supported interface.

## Commands

From the repository root, build all compatible libraries.

```sh
python scripts/build_native.py
```

On Windows, pass the installed MinGW GCC path if it is absent from `PATH`.

```powershell
python scripts/build_native.py --compiler C:\msys64\ucrt64\bin\gcc.exe
```

Build only rANS, or choose a compiler explicitly on Linux or macOS.

```sh
python scripts/build_native.py --target rans
python scripts/build_native.py --compiler clang
```

An explicit `--target simd` requires a compatible host and reports an error
when the CPU check fails. The default `--target all` builds rANS and reports a
skip for SIMD on other hosts. `--help` lists the available options.

To validate a build in a separate directory, specify `--output-dir`.

```sh
python scripts/build_native.py --output-dir experiments/native-build
```

The runtime loads libraries alongside `ahps.py` and `ahps_rans.py`. The default
output directory puts the libraries there. A separate output directory is
intended for build checks. Restart Python processes that already imported the
codec modules before using newly built libraries. On Windows, close processes
holding the destination DLLs before rebuilding them.

## Build and ABI checks

The script prints the compiler version and exact command line. Common flags
are `-O3 -std=c11 -Wall -Wextra -Werror`. The SIMD target also uses `-mavx2
-mpopcnt`. Linux uses `-shared -fPIC`, macOS uses `-dynamiclib -fPIC`, and Windows
uses `-shared -static-libgcc`. Windows PE timestamps and automatic image-base
selection are disabled so temporary directory names do not affect the build. macOS library
install names use the final library basename rather than a temporary build path.
The SIMD target links the system math library on Linux and macOS.

All requested libraries are compiled in a temporary directory and checked in
isolated Python subprocesses. The SIMD library must report ABI 206 and rANS
must report ABI 1. An ABI check confirms interface version and loadability.
Run the synthetic test suite to verify codec behavior.

```text
python scripts/run_tests.py --require-native -v
```

This check requires both native libraries. On hosts with only portable rANS,
run `python scripts/run_tests.py` instead. The [test guide](../tests/README.md)
also covers source-only verification and focused regression tests.

Compilation or ABI-check failure leaves existing libraries untouched. After
all checks pass, each library is installed with an atomic file replacement.
If an installation fails partway, the script attempts to restore already
replaced files and retains its staging directory and backups for recovery.
The build process uses local sources and the selected installed compiler.

The historical independent AHRC backend has its own
`research/build_ahrc_backend.py` workflow and belongs to the separate research
experiments. The default builder produces the two public codec accelerators.
