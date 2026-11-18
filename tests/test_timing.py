import json
import unittest

from bacchus.timing import TimingError, import_cues


HASH = "a" * 64


def evidence(*words, duration=2.0):
    return json.dumps({
        "schema": 1, "audio": {"sha256": HASH, "path": "external.wav"}, "duration": duration,
        "words": [{"text": text, "start": start, "end": end} for text, start, end in words],
        "provenance": {"method": "delivered-words"}, "lines": [],
    }).encode()


class TimingTests(unittest.TestCase):
    def test_import_preserves_actual_cue_span(self):
        result = import_cues(evidence(("Hello", 0.61, 0.94), ("there.", 0.93, 2.04)), HASH, 2.0)
        self.assertEqual(result["timing"], "source-cues")
        self.assertEqual(result["script"], "Hello there.")
        self.assertEqual(result["lines"], [
            {"id": 1, "text": "Hello there.", "start": 0.61, "end": 2.0, "firstWord": 1, "lastWord": 2},
        ])
        self.assertEqual(result["words"], [
            {"id": 1, "text": "Hello", "start": 0.61, "end": 0.94},
            {"id": 2, "text": "there.", "start": 0.93, "end": 2.0},
        ])

    def test_rejects_mismatched_recording_and_invalid_ranges(self):
        with self.assertRaisesRegex(TimingError, "different recording"):
            import_cues(evidence(("Hello", 0.0, 1.0)), "b" * 64, 2.0)
        with self.assertRaisesRegex(TimingError, "duration"):
            import_cues(evidence(("Hello", 0.0, 1.0), duration=5.0), HASH, 2.0)
        for invalid in (
            (("Late", 0.5, 1.0), ("early", 0.1, 0.2)),
            (("Hello", 0.0, 2.1),),
            (("Hello", 0.0, float("nan")),),
            (("Go", 0.1, 0.3), ("go", 0.1, 0.4)),
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(TimingError, "unordered times"):
                import_cues(evidence(*invalid), HASH, 2.0)


if __name__ == "__main__":
    unittest.main()
