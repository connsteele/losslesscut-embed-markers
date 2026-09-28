import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from llc_markers.engine import Settings, process_batch, file_hash
from llc_markers.media import MediaTools, read_chapters


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg/FFprobe required')
class Av1Tests(unittest.TestCase):
    def test_av1_two_flac_tracks_survive_chapter_write_and_repeat_run(self):
        with tempfile.TemporaryDirectory(prefix='llc-av1-test-') as directory:
            root = Path(directory).resolve()
            for folder in ('sources', 'clips', 'work'):
                (root/folder).mkdir()
            tools = MediaTools('ffmpeg', 'ffprobe', root/'work')
            if 'libaom-av1' not in tools.run(['ffmpeg', '-hide_banner', '-encoders']):
                self.skipTest('The optional AV1 fixture needs libaom-av1')
            source = root/'sources'/'source.mp4'
            tools.run(['ffmpeg', '-v', 'error', '-n',
                       '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=30:duration=10',
                       '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=10',
                       '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                       '-map', '0:v', '-map', '1:a', '-map', '2:a', '-t', '10',
                       '-c:v', 'libaom-av1', '-cpu-used', '8', '-g', '30', '-crf', '45',
                       '-c:a', 'flac', str(source)])
            clip = root/'clips'/'PRE AV1.mp4'
            tools.run(['ffmpeg', '-v', 'error', '-n', '-ss', '1.2', '-i', str(source),
                       '-t', '7.6', '-map', '0', '-c', 'copy', '-avoid_negative_ts', 'make_zero', str(clip)])
            project = root/'clips'/'source.llc'
            project.write_text(json.dumps({'version':2, 'mediaFileName':'source.mp4', 'cutSegments':[
                {'start':1.2, 'end':8.8, 'name':'AV1'}, {'start':4.2, 'name':'AV1 marker'}]}))
            settings = Settings(root/'clips', root/'clips', root/'sources', root/'work')
            encoded = tools.stream_hashes(clip)
            report = process_batch(settings, True, emit=lambda _: None)
            self.assertFalse(report['had_problems'], report['results'])
            self.assertEqual(report['summary'], {'updated':1})
            self.assertEqual(tools.stream_hashes(clip), encoded)
            self.assertEqual([c.title for c in read_chapters(tools.info(clip))], ['Clip start', 'AV1 marker'])
            original = file_hash(clip)
            repeated = process_batch(settings, True, emit=lambda _: None)
            self.assertEqual(repeated['summary'], {'up_to_date':1})
            self.assertEqual(file_hash(clip), original)
