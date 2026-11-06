"""Native rANS result pointers must not corrupt aliased caller buffers."""

import ctypes
import unittest

import ahps_rans


@unittest.skipIf(ahps_rans._NATIVE is None, "Optional native rANS library is unavailable")
class NativeResultPointerTests(unittest.TestCase):
    def setUp(self):
        self.source = ctypes.create_string_buffer(bytes(range(16)), 16)
        self.frequencies = (ctypes.c_uint16 * 128)(*([32] * 128))
        self.output = ctypes.create_string_buffer(b"x" * 64, 64)

    def encode(self, written):
        return ahps_rans._NATIVE.ahps_rans_encode(
            self.source, 16, self.frequencies, 128, self.output, 64, written
        )

    def buffers(self):
        return self.source.raw, bytes(self.frequencies), self.output.raw

    def test_aliased_result_pointer_is_rejected_without_writes(self):
        for name in ("source", "frequencies", "output"):
            buffer = getattr(self, name)
            # Exercise an exact base alias and an interior alias.
            for offset in (0, ctypes.sizeof(ctypes.c_size_t)):
                with self.subTest(buffer=name, offset=offset):
                    written = ctypes.cast(ctypes.byref(buffer, offset), ctypes.POINTER(ctypes.c_size_t))
                    before = self.buffers()
                    self.assertEqual(self.encode(written), 1)
                    self.assertEqual(self.buffers(), before)

    def test_null_and_wrapping_result_pointers_are_rejected(self):
        addresses = (0, ctypes.c_size_t(-1).value)
        for address in addresses:
            with self.subTest(address=address):
                written = ctypes.cast(ctypes.c_void_p(address), ctypes.POINTER(ctypes.c_size_t))
                before = self.buffers()
                self.assertEqual(self.encode(written), 1)
                self.assertEqual(self.buffers(), before)

    def test_separate_result_pointer_preserves_sources_and_returns_payload(self):
        written = ctypes.c_size_t(999)
        source_before = self.source.raw
        table_before = bytes(self.frequencies)
        self.assertEqual(self.encode(ctypes.byref(written)), 0)
        self.assertEqual(self.source.raw, source_before)
        self.assertEqual(bytes(self.frequencies), table_before)
        payload = self.output.raw[:written.value]
        restored = ahps_rans.decode_fp8(payload, 16, [32] * 128, force_python=True)
        self.assertEqual(restored, source_before)


if __name__ == "__main__":
    unittest.main()
