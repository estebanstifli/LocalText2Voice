from __future__ import annotations

import re
import unittest

from app.ui.widgets import LogView


class LogViewTests(unittest.TestCase):
    def test_timestamp_is_added_to_log_lines(self) -> None:
        stamped = LogView._timestamp_message("Starting generation...")

        self.assertRegex(stamped, r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\] ")
        self.assertTrue(stamped.endswith("Starting generation..."))

    def test_existing_timestamp_is_not_duplicated(self) -> None:
        stamped = LogView._timestamp_message("[12:34:56.789] Existing message")

        self.assertEqual(
            len(re.findall(r"\[\d{2}:\d{2}:\d{2}\.\d{3}\]", stamped)),
            1,
        )


if __name__ == "__main__":
    unittest.main()
