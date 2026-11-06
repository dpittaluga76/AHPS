"""Capability checks shared by synthetic tests."""

import errno
import os
import unittest
from unittest import mock


def hardlink_or_skip(source, destination):
    """Create a test alias, skipping only when the host denies hard links."""
    try:
        os.link(source, destination)
    except OSError as exc:
        unsupported = {errno.EACCES, errno.EPERM, errno.ENOSYS, errno.ENOTSUP}
        if exc.errno in unsupported or getattr(exc, "winerror", None) in (1, 5, 50, 1314):
            raise unittest.SkipTest(f"Hard links unavailable in the test directory: {exc}") from exc
        raise


class HardlinkCapabilityTests(unittest.TestCase):
    def test_permission_and_unsupported_errors_skip_with_reason(self):
        for code in (errno.EACCES, errno.EPERM, errno.ENOSYS, errno.ENOTSUP):
            with self.subTest(errno=code), mock.patch.object(os, "link", side_effect=OSError(code, "host denied")):
                with self.assertRaisesRegex(unittest.SkipTest, "Hard links unavailable"):
                    hardlink_or_skip("source", "destination")

    def test_other_link_errors_remain_failures(self):
        for code in (errno.ENOENT, errno.EEXIST, errno.EIO):
            failure = OSError(code, "test failure")
            with self.subTest(errno=code), mock.patch.object(os, "link", side_effect=failure):
                with self.assertRaises(OSError) as raised:
                    hardlink_or_skip("source", "destination")
                self.assertIs(raised.exception, failure)
