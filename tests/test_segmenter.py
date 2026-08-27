import unittest

from segmenter import words_to_segments


class SegmenterTests(unittest.TestCase):
    def test_splits_on_sentence_punctuation(self):
        words = [
            {"word": "Hello", "start": 0, "end": .5, "probability": .9},
            {"word": "everyone.", "start": 2.0, "end": 2.4, "probability": .9},
            {"word": "Welcome", "start": 3, "end": 3.4, "probability": .9},
            {"word": "back!", "start": 5.2, "end": 5.5, "probability": .9},
        ]
        segments = words_to_segments(words, 6000)
        self.assertEqual(2, len(segments))
        self.assertEqual("Hello everyone.", segments[0].text)
        self.assertFalse(segments[0].needsReview)

    def test_marks_low_confidence_for_review(self):
        words = [
            {"word": "This", "start": 0, "end": .4, "probability": .4},
            {"word": "is", "start": 1, "end": 1.3, "probability": .4},
            {"word": "unclear.", "start": 2.1, "end": 2.7, "probability": .4},
        ]
        segment = words_to_segments(words, 3000)[0]
        self.assertTrue(segment.needsReview)

    def test_merges_short_fragments(self):
        words = [
            {"word": "A", "start": 0, "end": .2, "probability": .9},
            {"word": "sentence.", "start": 2, "end": 2.2, "probability": .9},
            {"word": "Yes.", "start": 2.4, "end": 3.0, "probability": .9},
        ]
        segments = words_to_segments(words, 4000)
        self.assertEqual(1, len(segments))
        self.assertIn("Yes.", segments[0].text)


if __name__ == "__main__":
    unittest.main()
