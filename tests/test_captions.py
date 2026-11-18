import copy
import unittest

from bacchus import captions


def words(*intervals):
    return [{"id": index, "text": text, "start": start, "end": end}
            for index, (text, start, end) in enumerate(intervals, 1)]


def parse_ms(stamp):
    hours, minutes, seconds = stamp.split(":")
    seconds, milliseconds = seconds.split(",")
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(milliseconds)


def parse(srt):
    entries = []
    for index, block in enumerate(srt.strip().split("\n\n"), 1):
        number, time, *lines = block.split("\n")
        if number != str(index):
            raise ValueError("Caption entries must be sequential.")
        start, end = time.split(" --> ")
        entries.append((parse_ms(start), parse_ms(end), lines))
    return entries


class CaptionsTests(unittest.TestCase):
    def check_caption_coverage(self, source):
        srt, report = captions.generate(source)
        entries = parse(srt)
        self.assertEqual(report["method"], captions.METHOD)
        self.assertEqual(report["cueCount"], len(entries))
        self.assertEqual(report["flaggedCount"], len(report["issues"]))
        spoken = [token for word in source["words"] for token in word["text"].split()]
        shown = [token for _, _, lines in entries for line in lines for token in line.split()]
        self.assertEqual(shown, spoken)
        last_end = 0
        for start, end, lines in entries:
            self.assertLessEqual(last_end, start)
            self.assertGreater(end, start)
            self.assertLessEqual(end - start, 7000)
            self.assertLessEqual(end, source["duration"] * 1000)
            self.assertLessEqual(len(lines), 2)
            last_end = end
        return entries, report

    def test_rejects_empty_narrated_caption_source(self):
        with self.assertRaisesRegex(captions.CaptionError, "approved words"):
            captions.generate({"duration": 1, "words": []})

    def test_uses_measured_onset_and_limits_silent_extension(self):
        source = {"duration": 12, "words": words(
            ("This", 0.8, 1.03), ("short", 1.15, 1.38), ("line.", 1.56, 1.65),
            ("The next utterance.", 5.37, 7.20),
        )}
        approved = copy.deepcopy(source)
        entries, report = self.check_caption_coverage(source)
        self.assertEqual(entries[0], (800, 1800, ["This short line."]))
        self.assertEqual(entries[1], (5370, 7200, ["The next utterance."]))
        self.assertEqual(report["issues"], [])
        self.assertEqual(source, approved)
        self.assertEqual(captions.generate(source), captions.generate(approved))

    def test_wraps_multiword_record_without_splitting_name_or_unit(self):
        text = "The cast carried 20 kilograms past Dr. Alice Ng this morning."
        source = {"duration": 6, "words": words((text, 0.4, 4.8))}
        [entry], report = self.check_caption_coverage(source)
        self.assertEqual(entry[:2], (400, 4800))
        self.assertEqual(len(entry[2]), 2)
        self.assertTrue(all(len(line) <= 42 for line in entry[2]))
        self.assertTrue(any("20 kilograms" in line for line in entry[2]))
        self.assertTrue(any("Dr. Alice Ng" in line for line in entry[2]))
        self.assertEqual(report["issues"], [])

    def test_uses_sentence_boundaries_when_words_need_multiple_captions(self):
        sentences = [
            "A clear path brings everyone to the front of the unusually dark and quiet theatre.",
            "They wait in the shadows until the first streak of sunlight falls across the stage.",
        ]
        tokens = " ".join(sentences).split()
        source = {"duration": 15, "words": words(*(
            (token, index * 0.42, index * 0.42 + 0.30) for index, token in enumerate(tokens)
        ))}
        entries, report = self.check_caption_coverage(source)
        self.assertEqual([" ".join(lines) for _, _, lines in entries], sentences)
        self.assertEqual(report["issues"], [])

    def test_flags_rapid_speech_when_no_legal_hold_can_make_it_readable(self):
        source = {"duration": 0.25, "words": words(*(
            ("performance", index * 0.02, index * 0.02 + 0.019) for index in range(12)
        ))}
        entries, report = self.check_caption_coverage(source)
        self.assertEqual(len(entries), report["flaggedCount"])
        self.assertTrue(all(any("less than one second" in message for message in issue["messages"])
                            and any("Reading speed" in message for message in issue["messages"])
                            for issue in report["issues"]))

    def test_preserves_fractional_and_overlapping_words_at_srt_resolution(self):
        first = "initial group of text occupies most of two reasonable caption lines"
        second = "next collection of words also occupies two reasonable caption lines"
        source = {"duration": 4, "words": words(
            (first, 0.1, 1.8), (second, 1.0, 2.9), ("one", 3.9981, 3.9983),
            ("one", 3.9982, 3.9984), ("last", 3.9998, 4.0),
        )}
        entries, report = self.check_caption_coverage(source)
        self.assertEqual(entries[0][:2], (100, 1000))
        self.assertEqual(entries[1][0], 1000)
        self.assertEqual(entries[-1][1], 4000)
        messages = [message for issue in report["issues"] for message in issue["messages"]]
        self.assertTrue(any("Overlapping" in message for message in messages))
        self.assertTrue(any("Millisecond" in message for message in messages))

    def test_keeps_a_long_indivisible_word_and_caps_its_display_time(self):
        long_word = "incompressibility" * 4
        source = {"duration": 14, "words": words((long_word, 1.55, 12.21))}
        [entry], report = self.check_caption_coverage(source)
        self.assertEqual(entry, (1550, 8550, [long_word]))
        messages = report["issues"][0]["messages"]
        self.assertTrue(any("42 characters" in message for message in messages))
        self.assertTrue(any("seven seconds" in message for message in messages))

    def test_production_length_captions_are_independent_of_the_240_cue_limit(self):
        phrase = "the original audio stays in the project while a reviewed plan assigns visual cues to specific spoken words"
        tokens = phrase.split()
        intervals = [
            (tokens[index % len(tokens)] + ("." if (index + 1) % 12 == 0 else ""),
             index * 0.4, index * 0.4 + 0.3)
            for index in range(4500)
        ]
        source = {"duration": 1800, "words": words(*intervals)}
        entries, report = self.check_caption_coverage(source)
        self.assertGreater(report["cueCount"], 240)
        self.assertEqual(entries[0][0], 0)
        self.assertGreaterEqual(entries[-1][1], 1_799_900)
        self.assertTrue(all(len(line) <= 42 for _, _, lines in entries for line in lines))
        self.assertEqual(report["issues"], [])


if __name__ == "__main__":
    unittest.main()
