from types import SimpleNamespace
import unittest

from bacchus.local_timing import _window_words
from bacchus.timing import TimingError


class LocalTimingTests(unittest.TestCase):
    def test_uses_token_bounds_and_drops_overlapping_context(self):
        result = SimpleNamespace(
            tokens=[" predecessor", " Next", " cue", "!"],
            timestamps=[1, 1.9, 2.3, 2.6],
            durations=[0, 0.3, 0.2, 0],
        )
        words = _window_words(result, 28.1, 30, 60, 62)
        self.assertEqual([word["text"] for word in words], ["Next", "cue!"])
        self.assertAlmostEqual(words[0]["start"], 30)
        self.assertAlmostEqual(words[0]["end"], 30.3)

    def test_refuses_to_invent_duration_for_current_spoken_word(self):
        result = SimpleNamespace(tokens=[" word"], timestamps=[0.5], durations=[0])
        with self.assertRaisesRegex(TimingError, "valid measured span"):
            _window_words(result, 0, 0, 30, 32)
