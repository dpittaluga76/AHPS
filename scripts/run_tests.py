#!/usr/bin/env python3
"""Run synthetic repository tests without discovering private experiment folders."""

import argparse
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tests", nargs="*", help="Optional unittest module, class, or method names")
    engines = parser.add_mutually_exclusive_group()
    engines.add_argument("--require-native", action="store_true",
                         help="Fail unless both optional native libraries load")
    engines.add_argument("--require-pure-python", action="store_true",
                         help="Fail if either optional native library loads")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))

    if args.require_native or args.require_pure_python:
        import ahps
        import ahps_rans
        loaded = (ahps.SIMD_BRIDGE.lib is not None, ahps_rans._NATIVE is not None)
        if args.require_native and not all(loaded):
            parser.error("Both native libraries must load; build them with scripts/build_native.py first")
        if args.require_pure_python and any(loaded):
            parser.error("Pure-Python verification requires a source-only checkout without native libraries")

    names = args.tests or [path.stem for path in sorted(ROOT.glob("test_*.py"))]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
