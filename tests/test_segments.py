"""Offline narration segmentation tests; no synthesis or third-party packages."""

import importlib.util
from pathlib import Path
import re
import unittest


SPEC = importlib.util.spec_from_file_location(
    "anatomy_text_segments", Path(__file__).resolve().parents[1] / "scripts" / "text_segments.py")
segments = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(segments)
split_phrases = segments.split_phrases


def content(text):
    return re.sub(r"\s+", "", text)


class PhraseTests(unittest.TestCase):
    def assert_preserved(self, text, phrases):
        self.assertEqual(content("".join(phrases)), content(text))
        self.assertTrue(all(phrase and phrase == phrase.strip() for phrase in phrases))

    def test_empty_text(self):
        for text in ("", " ", "\n\t\r\u3000"):
            with self.subTest(text=text):
                self.assertEqual(split_phrases(text), [])

    def test_short_sentences_remain_natural_separate_cues(self):
        self.assertEqual(split_phrases("声音来自振动。振动推动空气。"),
                         ["声音来自振动。", "振动推动空气。"])
        self.assertEqual(split_phrases("先观察；再提问。真的？是的！"),
                         ["先观察；", "再提问。", "真的？", "是的！"])

    def test_short_comma_fragments_merge_within_the_same_sentence(self):
        text = "先观察，再提问，然后验证。"
        self.assertEqual(split_phrases(text), [text])

    def test_long_sentence_becomes_multiple_phrases(self):
        text = "声音会带动周围的空气振动，空气再把振动传给更远的地方，我们最终听见声音。"
        phrases = split_phrases(text)
        self.assertGreater(len(phrases), 1)
        self.assertTrue(all(12 <= len(phrase) <= 26 for phrase in phrases))
        self.assert_preserved(text, phrases)

    def test_long_unpunctuated_chinese_is_balanced_and_bounded(self):
        for count in (27, 32, 33, 53, 95, 200):
            text = "振" * count
            phrases = split_phrases(text)
            with self.subTest(count=count):
                self.assertGreater(len(phrases), 1)
                self.assertTrue(all(12 <= len(phrase) <= 26 for phrase in phrases))
                self.assert_preserved(text, phrases)

    def test_punctuation_and_closing_quotes_are_retained(self):
        text = "他说：“声音来自振动。”她问：“空气会移动很远吗？”"
        phrases = split_phrases(text)
        self.assertEqual(phrases, ["他说：“声音来自振动。”", "她问：“空气会移动很远吗？”"])
        self.assert_preserved(text, phrases)

    def test_english_words_are_never_cut(self):
        text = "Sound starts when an object vibrates and the moving air carries that vibration toward your ears."
        phrases = split_phrases(text, "en-US")
        self.assertGreater(len(phrases), 1)
        self.assertTrue(all(len(phrase) <= 32 for phrase in phrases))
        self.assertEqual(" ".join(phrases), text)
        self.assert_preserved(text, phrases)

    def test_a_word_longer_than_the_limit_stays_intact(self):
        word = "pneumonoultramicroscopicsilicovolcanoconiosis"
        text = "We pronounce " + word + " carefully."
        phrases = split_phrases(text, "en")
        self.assertIn(word, phrases)
        self.assertEqual(" ".join(phrases), text)
        self.assertTrue(all(len(phrase) <= 32 or phrase == word for phrase in phrases))

    def test_mixed_language_keeps_embedded_english_word_whole(self):
        word = "electromagnetism"
        text = "扬声器通过" + word + "驱动振膜振动并带动周围的空气将声音传向远处"
        phrases = split_phrases(text)
        self.assertTrue(any(word in phrase for phrase in phrases))
        self.assertTrue(all(len(phrase) <= 32 for phrase in phrases))
        self.assert_preserved(text, phrases)

    def test_decimals_and_numeric_separators_are_not_sentence_boundaries(self):
        text = "频率是440.5赫兹，计数是1,000次，比例是3:2。"
        phrases = split_phrases(text)
        for number in ("440.5", "1,000", "3:2"):
            self.assertTrue(any(number in phrase for phrase in phrases))
        self.assert_preserved(text, phrases)

    def test_common_english_abbreviations_do_not_end_a_sentence(self):
        text = "Dr. Li checks e.g. a small speaker."
        phrases = split_phrases(text, "en-US")
        self.assertNotIn("Dr.", phrases)
        self.assertTrue(any("e.g." in phrase for phrase in phrases))
        self.assertEqual(" ".join(phrases), text)

    def test_whitespace_is_normalized(self):
        text = "  Sound\t travels\n through   air.  "
        phrases = split_phrases(text, "en")
        self.assertEqual(" ".join(phrases), "Sound travels through air.")
        self.assertTrue(all("  " not in phrase and "\n" not in phrase and "\t" not in phrase for phrase in phrases))


if __name__ == "__main__":
    unittest.main()
