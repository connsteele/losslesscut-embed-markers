from pathlib import Path
import unittest
from unittest.mock import patch

from llc_markers.media import MediaTools, InsufficientPacketMatches
from llc_markers.model import MarkerError


def packets(offset=0, inconsistent=False):
    return [{'data_hash': str(i), 'pts_time': str(offset + i / 10 + (1 if inconsistent and i == 5 else 0))}
            for i in range(6)]


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.tools = MediaTools('ffmpeg', 'ffprobe', Path('.'))
        self.source = Path('source.mp4')
        self.clip = Path('clip.mp4')

    def test_repeated_front_packets_expand_and_tail_still_checked(self):
        repeated = [{'data_hash': 'repeat', 'pts_time': str(i / 10)} for i in range(20)]
        with patch.object(self.tools, 'packets', side_effect=[repeated, packets(), packets(36)]) as clip, \
                patch.object(self.tools, 'probe', side_effect=[
                    {'packets': repeated}, {'packets': packets(100)}, {'packets': packets(136)}]):
            offset, count = self.tools.alignment(self.source, self.clip, 100, 140,
                                                {'format': {'duration': 40}}, {'format': {}})
        self.assertEqual((offset, count), (100, 12))
        self.assertEqual([c.args[1:] for c in clip.call_args_list], [(0, 4), (0, 8), (36, 5)])

    def test_tail_can_expand_without_ignoring_offset_disagreement(self):
        repeated = [{'data_hash': 'repeat', 'pts_time': str(36 + i / 10)} for i in range(20)]
        with patch.object(self.tools, 'packets', side_effect=[packets(), repeated, packets(32)]), \
                patch.object(self.tools, 'probe', side_effect=[
                    {'packets': packets(100)}, {'packets': []}, {'packets': packets(133)}]):
            with self.assertRaisesRegex(MarkerError, 'different offsets'):
                self.tools.alignment(self.source, self.clip, 100, 140,
                                     {'format': {'duration': 40}}, {'format': {}})

    def test_inconsistent_timestamps_fail_without_retry(self):
        with patch.object(self.tools, 'packets', return_value=packets()) as sample, \
                patch.object(self.tools, 'probe', return_value={'packets': packets(100, True)}):
            with self.assertRaisesRegex(MarkerError, 'inconsistent'):
                self.tools.sample_alignment(self.source, self.clip, 100, 40, 8)
        self.assertEqual(sample.call_count, 1)

    def test_failed_samples_are_bounded_and_short_clips_not_repeated(self):
        for duration, windows in [(100, [4, 8, 16, 32]), (5, [4, 5]), (2, [2])]:
            with self.subTest(duration=duration), \
                    patch.object(self.tools, 'packets', return_value=packets()) as sample, \
                    patch.object(self.tools, 'probe', return_value={'packets': []}):
                with self.assertRaisesRegex(InsufficientPacketMatches, 'sampling up to'):
                    self.tools.sample_alignment(self.source, self.clip, 100, duration, 8)
                self.assertEqual([c.args[2] for c in sample.call_args_list], windows)

    def test_sparse_but_contradictory_evidence_is_not_retried(self):
        source = packets(100)[:3]
        source[-1]['pts_time'] = '104'
        with patch.object(self.tools, 'packets', return_value=packets()[:3]) as sample, \
                patch.object(self.tools, 'probe', return_value={'packets': source}):
            with self.assertRaisesRegex(MarkerError, 'inconsistent'):
                self.tools.sample_alignment(self.source, self.clip, 100, 40, 8)
        self.assertEqual(sample.call_count, 1)

    def test_source_search_uses_absolute_end_and_packet_times(self):
        with patch.object(self.tools, 'packets', return_value=packets(30)), \
                patch.object(self.tools, 'probe', return_value={'packets': packets(130)}) as probe:
            self.tools.sample_alignment(self.source, self.clip, 100, 40, 8, tail=True)
        args = probe.call_args.args
        self.assertEqual(args[args.index('-read_intervals') + 1], '122.000000%140.500000')


if __name__ == '__main__':
    unittest.main()
