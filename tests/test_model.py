import json
from pathlib import Path
import tempfile
import unittest

from llc_markers.engine import base_chapters, match_clip, Settings
from llc_markers.media import packet_offset
from llc_markers.model import (Chapter, Marker, MarkerError, Project, Segment, build_chapters,
                               chapter_dicts, ffmetadata, filename_matches,
                               marker_chapters, markers_for_segment, read_project)


class ModelTests(unittest.TestCase):
    def test_json5_and_point_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.llc'
            path.write_text("""{version:2, mediaFileName:'source.mp4', // comment
                cutSegments:[{start:1,end:9,name:'Clip',},
                {start:3.25,name:'Café statue',selected:false,},],}""", encoding='utf-8')
            project = read_project(path)
            self.assertEqual(project.markers, [Marker(3.25, 'Café statue')])
            self.assertEqual(project.segments[0].end, 9)

    def test_bad_project_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.llc'
            for value in [
                {'version':1},
                {'version':2,'mediaFileName':'a.mp4','cutSegments':[{'start':float('nan'),'name':'bad'}]},
                {'version':2,'mediaFileName':'a.mp4','cutSegments':[{'start':1,'end':0}]},
                {'version':2,'mediaFileName':'a.mp4','cutSegments':[{'start':1,'name':''}]},
            ]:
                path.write_text(json.dumps(value))
                with self.assertRaises(MarkerError):
                    read_project(path)

    def test_matching_is_normalized_not_fuzzy(self):
        self.assertTrue(filename_matches('A1 Cai - Statue', 'FW A1 Cai _ Statue.mp4', 'FW '))
        self.assertTrue(filename_matches('Cai: Statue?', 'Cai_ Statue_.mp4', ''))
        self.assertFalse(filename_matches('Cai Statue', 'FW Cai Statue alternate.mp4', 'FW '))
        self.assertFalse(filename_matches('', 'FW.mp4', 'FW '))

    def test_boundary_and_overlap(self):
        p = Project(Path('p.llc'), 's.mp4', [], [Marker(0,'a'),Marker(5,'b'),Marker(10,'c')])
        self.assertEqual([m.name for m in markers_for_segment(p, Segment(0,0,5,'a'))], ['a'])
        self.assertEqual([m.name for m in markers_for_segment(p, Segment(1,5,10,'b'))], ['b'])
        self.assertEqual([m.name for m in markers_for_segment(p, Segment(2,4,6,'c'))], ['b'])

    def test_actual_offset_not_requested_start(self):
        chapters = marker_chapters([Marker(58.523385253125,'Castor')], 19.680013, 99.6, 60)
        self.assertAlmostEqual(chapters[0].start, 38.843372253125)
        with self.assertRaises(MarkerError):
            marker_chapters([Marker(1,'outside')], 2, 99, 60)

    def test_mp4_needs_zero_start_and_contiguous_chapters(self):
        result = build_chapters([], [Chapter(3.25678,3.27,'Marker')], 10, '.mp4')
        self.assertEqual(result, [Chapter(0,3.257,'Clip start'), Chapter(3.257,10,'Marker')])
        self.assertEqual(build_chapters([], [], 10, '.mp4'), [])

    def test_mkv_does_not_add_start_marker(self):
        chapter = Chapter(3.25678,3.27,'Marker')
        self.assertEqual(build_chapters([], [chapter], 10, '.mkv'), [chapter])

    def test_same_time_titles_preserved_once(self):
        result = build_chapters([Chapter(0,2,'Existing')],
                                [Chapter(0,0.02,'One'), Chapter(0.0001,0.02,'Two'),Chapter(0,0.02,'One')],10,'.mp4')
        self.assertEqual(result, [Chapter(0,10,'Existing | One | Two')])

    def test_metadata_escaping(self):
        value = ffmetadata([Chapter(0,1,'name=two;#\\\n雪')])
        self.assertIn('title=name\\=two\\;\\#\\\\\\\n雪', value)

    def test_packet_alignment_rejects_inconsistent_mapping(self):
        source = [{'data_hash':str(i),'pts_time':str(i+19.68)} for i in range(8)]
        clip = [{'data_hash':str(i),'pts_time':str(i)} for i in range(8)]
        offset, count = packet_offset(source, clip)
        self.assertAlmostEqual(offset, 19.68)
        self.assertEqual(count, 8)
        clip[-1]['pts_time'] = '8'
        with self.assertRaises(MarkerError):
            packet_offset(source, clip)
        with self.assertRaises(MarkerError):
            packet_offset(source[:3], clip[:3])

    def test_managed_chapters_can_change_without_losing_existing(self):
        base = [Chapter(0,10,'Existing')]
        current = build_chapters(base, [Chapter(4,4.02,'Old marker')],10,'.mp4')
        state = {'base_chapters':chapter_dicts(base),'chapters':chapter_dicts(current)}
        self.assertEqual(base_chapters(current, state), base)
        with self.assertRaises(MarkerError):
            base_chapters([Chapter(0,10,'External edit')], state)
        self.assertEqual(base_chapters([], state), [])

    def test_explicit_mapping_cannot_escape_clip_directory(self):
        root = Path.cwd().resolve()
        s = Settings(root, root / 'clips', root, root / 'work')
        p = Project(root / 'p.llc', 'source.mp4', [], [])
        with self.assertRaises(MarkerError):
            match_clip(p, Segment(0,0,10,''), s, [root / 'outside.mp4'], {'p.llc::0':'../outside.mp4'})


if __name__ == '__main__':
    unittest.main()
