"""Terminal progress is optional, bounded, and honest about completion."""

import io
import unittest
from unittest import mock

from ahps_progress import TerminalProgress


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ProgressTests(unittest.TestCase):
    def test_redirected_and_explicitly_disabled_output_has_no_thread_or_writes(self):
        for stream, enabled in ((io.StringIO(), None), (Terminal(), False)):
            with self.subTest(enabled=enabled), mock.patch("ahps_progress.threading.Thread") as thread:
                with TerminalProgress("Compress", enabled=enabled, stream=stream) as progress:
                    for index in range(100):
                        progress.update(index / 99, "Encoding")
                self.assertFalse(progress.enabled)
                thread.assert_not_called()
                self.assertEqual(stream.getvalue(), "")

    def test_success_has_initial_zero_and_final_hundred_only(self):
        stream = Terminal()
        with mock.patch("ahps_progress.threading.Thread"):
            with TerminalProgress("Compress", stream=stream) as progress:
                progress.update(0.7, "Encoding")
                progress._render()
                progress.update(0.2, "Writing")
                progress._render()
                progress.update(2.0, "Verifying")
                progress._render()
            progress.update(0.1, "Too late")
        output = stream.getvalue()
        frames = output.split("\r")[1:]
        self.assertIn("  0%", frames[0])
        self.assertIn(" 70%", frames[1])
        self.assertIn(" 70%", frames[2])
        self.assertIn(" 99%", frames[3])
        self.assertIn("100%", frames[-1])
        self.assertIn("Done", frames[-1])
        self.assertEqual(output.count("100%"), 1)
        self.assertTrue(output.endswith("\n"))
        self.assertNotIn("Too late", output)
        self.assertLessEqual(max(map(len, frames)), 100)

    def test_exception_is_propagated_and_never_displays_hundred(self):
        stream = Terminal()
        with mock.patch("ahps_progress.threading.Thread"):
            with self.assertRaisesRegex(ValueError, "codec failed"):
                with TerminalProgress("Decompress", stream=stream) as progress:
                    progress.update(1.0, "Checking CRC")
                    raise ValueError("codec failed")
        self.assertNotIn("100%", stream.getvalue())
        self.assertIn("Failed: Checking CRC", stream.getvalue())
        self.assertTrue(stream.getvalue().endswith("\n"))

    def test_handled_failures_do_not_display_success_on_normal_exit(self):
        for stage in ("Completed with errors", "Two corpus jobs failed"):
            stream = Terminal()
            with self.subTest(stage=stage), mock.patch("ahps_progress.threading.Thread"):
                with TerminalProgress("Bench", stream=stream) as progress:
                    progress.mark_failed(stage)
                    progress.update(1.0, "Saved results")
            final = stream.getvalue().split("\r")[-1]
            self.assertIn(" 99%", final)
            self.assertIn(stage, final)
            self.assertIn("!", final)
            self.assertNotIn("100%", stream.getvalue())
            self.assertNotIn("Done", stream.getvalue())

    def test_updates_do_not_render_and_periodic_wait_is_throttled(self):
        stream = Terminal()
        with mock.patch("ahps_progress.threading.Thread"):
            with TerminalProgress("Bench", stream=stream, interval=0.001) as progress:
                initial = stream.getvalue()
                for index in range(1000):
                    progress.update(index / 1000, "Measuring")
                self.assertEqual(stream.getvalue(), initial)
                with mock.patch.object(progress._stop, "wait", side_effect=[False, False, True]) as wait:
                    with mock.patch.object(progress, "_render") as render:
                        progress._run()
                self.assertEqual(wait.call_args_list, [mock.call(0.25)] * 3)
                self.assertEqual(render.call_count, 2)

    def test_forced_progress_works_on_redirected_stream_and_stops_thread(self):
        stream = io.StringIO()
        with TerminalProgress("Compress", enabled=True, stream=stream) as progress:
            progress.update(0.5, "Encoding")
            self.assertTrue(progress._thread.is_alive())
        self.assertFalse(progress._thread.is_alive())
        self.assertIn("100%", stream.getvalue())

    def test_failed_terminal_write_does_not_change_codec_outcome(self):
        stream = mock.Mock()
        stream.isatty.return_value = True
        stream.write.side_effect = OSError("detached terminal")
        with mock.patch("ahps_progress.threading.Thread") as thread:
            with TerminalProgress("Compress", stream=stream) as progress:
                progress.update(0.5, "Encoding")
            thread.assert_not_called()
        self.assertEqual(stream.write.call_count, 1)

    def test_control_characters_cannot_inject_terminal_commands(self):
        stream = Terminal()
        with mock.patch("ahps_progress.threading.Thread"):
            with TerminalProgress("bad\x1blabel", stream=stream) as progress:
                progress.update(0.5, "bad\nstage\r\x1b")
                progress._render()
        self.assertNotIn("\x1b", stream.getvalue())
        self.assertEqual(stream.getvalue().count("\n"), 1)

    def test_status_line_clears_bar_without_triggering_extra_progress_frame(self):
        stream = Terminal()
        output = io.StringIO()
        with mock.patch("ahps_progress.threading.Thread"):
            with TerminalProgress("Bench", stream=stream) as progress:
                before = stream.getvalue()
                progress.write_line("row complete", file=output)
                added = stream.getvalue()[len(before):]
                self.assertEqual(added, "\r" + " " * progress._width + "\r")
                self.assertEqual(output.getvalue(), "row complete\n")
                self.assertEqual(progress._frame, 1)


if __name__ == "__main__":
    unittest.main()
