"""Small synthetic media tests; all fixtures stay in the process TEMP directory."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from llc_markers.engine import Settings, process_batch, file_hash, state_path
from llc_markers.media import MediaTools, read_chapters
from llc_markers.model import Chapter, MarkerError, build_chapters


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg/FFprobe required')
class MediaTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='llc-markers-test-')
        self.root = Path(self.temporary.name).resolve()
        for folder in ('source', 'cuts', 'work'):
            (self.root / folder).mkdir()
        self.tools = MediaTools('ffmpeg','ffprobe',self.root / 'work')
        self.settings = Settings(self.root/'cuts',self.root/'cuts',self.root/'source',self.root/'work')
        self.source = self.root / 'source' / 'source.mp4'
        self.command(['-f','lavfi','-i','testsrc2=size=96x64:rate=10:duration=12',
                      '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=12',
                      '-c:v','libx264','-g','20','-bf','0','-c:a','aac',str(self.source)])
        self.clip = self.root / 'cuts' / 'PRE Sample.mp4'
        self.command(['-ss','2.35','-i',str(self.source),'-t','5.55','-c','copy',
                      '-avoid_negative_ts','make_zero',str(self.clip)])
        self.empty = self.root / 'cuts' / 'PRE No markers.mp4'
        self.command(['-ss','8','-i',str(self.source),'-t','3.7','-c','copy',
                      '-avoid_negative_ts','make_zero',str(self.empty)])
        self.project = self.root / 'cuts' / 'source-proj.llc'
        self.save_project('Test statue')

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, args):
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-n',*args],check=True,capture_output=True)

    def save_project(self, label):
        entries = [{'start':2.35,'end':7.9,'name':'Sample'},
                   {'start':8,'end':11.7,'name':'No markers'}]
        if label:
            entries.append({'start':4.21,'name':label})
        self.project.write_text(json.dumps({'version':2,'mediaFileName':'source.mp4','cutSegments':entries}),encoding='utf-8')

    def run_batch(self, apply=False):
        report = process_batch(self.settings, apply, emit=lambda s: None)
        self.assertFalse(report['had_problems'], report['results'])
        return report


class IntegrationTests(MediaTestCase):
    def test_preview_apply_rerun_update_and_remove(self):
        original = file_hash(self.clip)
        empty_hash = file_hash(self.empty)
        preview = self.run_batch()
        self.assertEqual(preview['summary'], {'ready':1,'no_markers':1})
        self.assertEqual(file_hash(self.clip), original)
        updated = self.run_batch(True)
        self.assertEqual(updated['summary'], {'updated':1,'no_markers':1})
        entry = next(x for x in updated['results'] if x['status']=='updated')
        self.assertEqual(file_hash(Path(entry['backup'])), original)
        self.assertEqual(file_hash(self.empty), empty_hash)
        self.assertTrue(self.project.exists())
        self.assertEqual(updated['cleanup_results'], [])
        chapters = read_chapters(self.tools.info(self.clip))
        self.assertEqual([c.title for c in chapters], ['Clip start','Test statue'])
        self.assertAlmostEqual(chapters[1].start,entry['markers'][0]['clip_seconds'],delta=.002)
        self.assertEqual(self.run_batch(True)['summary'], {'up_to_date':1,'no_markers':1})
        self.save_project('Changed label')
        self.assertEqual(self.run_batch(True)['summary']['updated'],1)
        self.assertEqual(read_chapters(self.tools.info(self.clip))[-1].title, 'Changed label')
        self.save_project(None)
        self.assertEqual(self.run_batch(True)['summary']['updated'],1)
        self.assertEqual(read_chapters(self.tools.info(self.clip)), [])
        self.assertEqual(file_hash(self.empty), empty_hash)

    def test_copy_output_preserves_input_and_refuses_unowned_output(self):
        self.settings.output_dir = self.root / 'outputs'
        original = file_hash(self.clip)
        report = self.run_batch(True)
        self.assertEqual(report['summary']['updated'],1)
        self.assertEqual(file_hash(self.clip),original)
        self.assertEqual(self.run_batch(True)['summary']['up_to_date'],1)
        for state in (self.root/'work'/'state').glob('*.json'):
            state.unlink()
        report = process_batch(self.settings,True,emit=lambda s:None)
        self.assertEqual(report['summary']['error'],1)
        self.assertEqual(file_hash(self.clip),original)

    def test_ambiguous_matches_are_untouched(self):
        duplicate = self.root / 'cuts' / 'PRE_Sample.mp4'
        shutil.copy2(self.clip,duplicate)
        original = file_hash(self.clip)
        report = process_batch(self.settings,True,emit=lambda s:None)
        self.assertEqual(report['summary']['ambiguous'],1)
        self.assertEqual(file_hash(self.clip),original)
        self.assertEqual(read_chapters(self.tools.info(self.clip)),[])

    def test_multiple_projects_multiple_markers_and_existing_chapters(self):
        # A second project exports a distinct clip from the same original.
        second = self.root / 'cuts' / 'PRE Another.mp4'
        meta = self.root / 'existing.ffmetadata'
        meta.write_text(';FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=5500\ntitle=Existing chapter\n')
        self.command(['-i',str(self.clip),'-f','ffmetadata','-i',str(meta),
                      '-map','0','-map_chapters','1','-c','copy',str(second)])
        entries = [{'start':2.35,'end':7.9,'name':'Another'},
                   {'start':3.12,'name':'First = #; café'},
                   {'start':5.72,'name':'Second marker'},
                   {'start':11.9,'name':'Outside exports'}]
        (self.root/'cuts'/'second.llc').write_text(json.dumps({'version':2,'mediaFileName':'source.mp4','cutSegments':entries}),encoding='utf-8')
        report = process_batch(self.settings, True, emit=lambda s: None)
        self.assertEqual(report['project_count'],2)
        self.assertEqual(report['summary']['updated'],2)
        self.assertEqual([c.title for c in read_chapters(self.tools.info(second))],
                         ['Existing chapter','First = #; café','Second marker'])
        self.assertEqual(len(report['unused_markers']),1)
        self.assertEqual(report['outcome'], 'completed_with_attention')
        self.assertEqual(report['error_count'], 0)
        self.assertIn('unused point marker', Path(report['report_path']).with_suffix('.txt').read_text(encoding='utf-8'))
        repeated = process_batch(self.settings, True, emit=lambda s: None)
        self.assertEqual(repeated['summary']['up_to_date'],2)

    def test_failed_verification_does_not_replace_export(self):
        original = file_hash(self.clip)
        with patch.object(MediaTools,'stream_hashes',side_effect=['original-hash','wrong-hash']):
            report = process_batch(self.settings,True,emit=lambda s:None)
        self.assertEqual(report['summary']['error'],1)
        self.assertEqual(file_hash(self.clip),original)
        self.assertEqual(read_chapters(self.tools.info(self.clip)),[])

    def test_boundary_and_unnamed_missing_export_report_explains_saved_ranges(self):
        self.project.write_text(json.dumps({'version': 2, 'mediaFileName': 'source.mp4', 'cutSegments': [
            {'start': 2.35, 'end': 7.9, 'name': 'Sample'},
            {'start': 7.9, 'name': 'At cut end'},
            {'start': 10, 'end': 11, 'name': ''}]}), encoding='utf-8')
        self.settings.delete_projects_after_success = True
        before = file_hash(self.clip)
        report = process_batch(self.settings, False, emit=lambda s: None)
        self.assertEqual(report['unused_markers'][0]['reason'], 'at_cut_end')
        self.assertEqual(report['cleanup_summary'], {'retained': 1})
        missing = next(x for x in report['results'] if x['status'] == 'missing_export')
        self.assertEqual(missing['segment_start'], 10)
        readable = Path(report['report_path']).with_suffix('.txt').read_text(encoding='utf-8')
        self.assertIn('exactly at an excluded cut end', readable)
        self.assertIn('Cut ending here: Sample', readable)
        self.assertIn('Source: source.mp4 @ 10.000s - 11.000s', readable)
        self.assertIn('(unnamed segment)', readable)
        self.assertEqual(file_hash(self.clip), before)
        self.assertTrue(self.project.exists())


class PlaceholderTests(MediaTestCase):
    def add_existing_chapters(self, path, titles):
        info = self.tools.info(path)
        duration = float(info['format']['duration'])
        chapters = build_chapters([], [Chapter(i, i + .1, title) for i, title in enumerate(titles)], duration, path.suffix)
        replacement = self.root / 'work' / path.name
        self.tools.remux(path, replacement, chapters, info)
        os.replace(replacement, path)

    def test_preview_apply_and_restore_existing_placeholders(self):
        self.add_existing_chapters(self.clip, ['Start', 'Unnamed 2', 'Story beat'])
        self.run_batch(True)
        original = file_hash(self.clip)
        self.assertIn('Unnamed 2', [c.title for c in read_chapters(self.tools.info(self.clip))])
        self.settings.remove_placeholder_chapters = True
        preview = self.run_batch()
        self.assertEqual(file_hash(self.clip), original)
        self.assertEqual(preview['placeholder_count'], 2)
        self.assertIn('Excluded existing placeholder', Path(preview['report_path']).with_suffix('.txt').read_text(encoding='utf-8'))
        result = self.run_batch(True)
        self.assertEqual(result['summary']['updated'], 1)
        labels = [c.title for c in read_chapters(self.tools.info(self.clip))]
        self.assertEqual(labels, ['Clip start', 'Story beat', 'Test statue'])
        self.assertEqual(self.run_batch(True)['summary']['up_to_date'], 1)
        self.settings.remove_placeholder_chapters = False
        self.run_batch(True)
        self.assertEqual([c.title for c in read_chapters(self.tools.info(self.clip))],
                         ['Start', 'Unnamed 2', 'Story beat', 'Test statue'])

    def test_placeholder_only_clip_can_be_cleaned_without_point_markers(self):
        self.save_project(None)
        self.add_existing_chapters(self.clip, ['Start', 'Unnamed 1'])
        original_hashes = self.tools.stream_hashes(self.clip)
        self.settings.remove_placeholder_chapters = True
        report = self.run_batch(True)
        self.assertEqual(report['summary'], {'updated': 1, 'no_markers': 1})
        self.assertEqual(read_chapters(self.tools.info(self.clip)), [])
        self.assertEqual(self.tools.stream_hashes(self.clip), original_hashes)
        self.assertEqual(self.run_batch(True)['summary']['up_to_date'], 1)

    def test_llc_marker_names_are_never_filtered(self):
        self.add_existing_chapters(self.clip, ['Start', 'Unnamed 1'])
        self.settings.remove_placeholder_chapters = True
        for label in ('Start', 'Unnamed 1'):
            with self.subTest(label=label):
                self.save_project(label)
                self.run_batch(True)
                self.assertEqual([c.title for c in read_chapters(self.tools.info(self.clip))], ['Clip start', label])

    def test_copy_mode_keeps_original_placeholder_chapters(self):
        self.save_project(None)
        self.add_existing_chapters(self.clip, ['Start', 'Unnamed 1'])
        self.settings.remove_placeholder_chapters = True
        self.settings.output_dir = self.root / 'outputs'
        original = file_hash(self.clip)
        self.run_batch(True)
        self.assertEqual(file_hash(self.clip), original)
        self.assertEqual(read_chapters(self.tools.info(self.settings.output_dir / self.clip.name)), [])
        self.assertFalse((self.settings.output_dir / self.empty.name).exists())


class CleanupTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.settings.delete_projects_after_success = True

    def unchecked_batch(self, apply=True):
        return process_batch(self.settings, apply, emit=lambda s: None)

    def change_project(self, change):
        project = json.loads(self.project.read_text(encoding='utf-8'))
        change(project)
        self.project.write_text(json.dumps(project), encoding='utf-8')

    def test_preview_then_apply_backs_up_exact_project_and_preserves_unmarked_export(self):
        project_hash = file_hash(self.project)
        clip_hash = file_hash(self.clip)
        empty_hash = file_hash(self.empty)
        preview = self.run_batch()
        self.assertEqual(preview['cleanup_summary'], {'would_delete': 1})
        self.assertEqual(file_hash(self.project), project_hash)
        self.assertEqual(file_hash(self.clip), clip_hash)
        self.assertFalse((self.settings.work_dir / 'Project Backups').exists())
        report = self.run_batch(True)
        self.assertEqual(report['cleanup_summary'], {'deleted': 1})
        self.assertFalse(self.project.exists())
        backup = Path(report['cleanup_results'][0]['backup'])
        self.assertEqual(file_hash(backup), project_hash)
        self.assertEqual(file_hash(self.empty), empty_hash)
        unmarked = next(r for r in report['results'] if r['status'] == 'no_markers')
        self.assertGreaterEqual(unmarked['matched_packets'], 5)
        self.assertIn('Test statue', [c.title for c in read_chapters(self.tools.info(self.clip))])
        saved = json.loads(Path(report['report_path']).read_text(encoding='utf-8'))
        self.assertEqual(saved['cleanup_results'], report['cleanup_results'])
        self.assertIn(str(backup), Path(report['report_path']).with_suffix('.txt').read_text(encoding='utf-8'))
        # Restoring to the original path reuses the existing media processing state.
        shutil.copy2(backup, self.project)
        self.settings.delete_projects_after_success = False
        restored = self.run_batch(True)
        self.assertEqual(restored['summary'], {'up_to_date': 1, 'no_markers': 1})

    def test_projects_with_only_unmarked_exports_are_verified_and_cleaned(self):
        self.save_project(None)
        before = [file_hash(self.clip), file_hash(self.empty)]
        report = self.run_batch(True)
        self.assertEqual(report['summary'], {'no_markers': 2})
        self.assertEqual(report['cleanup_summary'], {'deleted': 1})
        self.assertEqual([file_hash(self.clip), file_hash(self.empty)], before)

    def test_unmarked_export_must_match_original_before_cleanup(self):
        self.settings.delete_projects_after_success = False
        self.run_batch(True)
        self.settings.delete_projects_after_success = True
        alignment = MediaTools.alignment

        def fail_unmarked(tools, source, clip, *args):
            if clip == self.empty:
                raise MarkerError('Unmarked export does not match the source')
            return alignment(tools, source, clip, *args)

        with patch.object(MediaTools, 'alignment', new=fail_unmarked):
            report = self.unchecked_batch()
        self.assertTrue(report['had_problems'])
        self.assertEqual(report['cleanup_summary'], {'retained': 1})
        self.assertTrue(self.project.exists())

    def test_missing_export_and_unused_marker_each_keep_project(self):
        self.change_project(lambda p: p['cutSegments'].append({'start': 11.9, 'name': 'Not exported'}))
        report = self.unchecked_batch()
        self.assertEqual(report['cleanup_summary'], {'retained': 1})
        self.assertIn('outside', report['cleanup_results'][0]['message'])
        self.assertTrue(self.project.exists())
        self.save_project('Test statue')
        self.empty.unlink()
        report = self.unchecked_batch()
        self.assertEqual(report['summary']['missing_export'], 1)
        self.assertEqual(report['cleanup_summary'], {'retained': 1})
        self.assertTrue(self.project.exists())

    def test_ambiguous_clip_keeps_project(self):
        shutil.copy2(self.clip, self.clip.with_name('PRE_Sample.mp4'))
        report = self.unchecked_batch()
        self.assertEqual(report['summary']['ambiguous'], 1)
        self.assertEqual(report['cleanup_summary'], {'retained': 1})
        self.assertTrue(self.project.exists())

    def test_invalid_and_empty_projects_are_retained(self):
        for value in ('{broken json', '{"version":2,"mediaFileName":"source.mp4","cutSegments":[]}'):
            with self.subTest(project=value):
                self.project.write_text(value, encoding='utf-8')
                report = self.unchecked_batch()
                self.assertEqual(report['cleanup_summary'], {'retained': 1})
                self.assertTrue(self.project.exists())

    def test_failed_media_verification_keeps_project(self):
        original = file_hash(self.clip)
        with patch.object(MediaTools, 'stream_hashes', side_effect=['original-hash', 'wrong-hash']):
            report = self.unchecked_batch()
        self.assertEqual(report['summary']['error'], 1)
        self.assertEqual(report['cleanup_summary'], {'retained': 1})
        self.assertEqual(file_hash(self.clip), original)
        self.assertTrue(self.project.exists())

    def test_project_edit_with_same_size_and_timestamp_prevents_cleanup(self):
        info = MediaTools.info
        edited = False
        original = file_hash(self.clip)

        def edit_after_scan(tools, path):
            nonlocal edited
            if not edited:
                edited = True
                stat = self.project.stat()
                self.save_project('Edit statue')  # Same byte length as 'Test statue'.
                os.utime(self.project, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return info(tools, path)

        with patch.object(MediaTools, 'info', new=edit_after_scan):
            report = self.unchecked_batch()
        self.assertTrue(self.project.exists())
        self.assertEqual(report['cleanup_summary'], {'retained': 1})
        self.assertTrue(any('Project changed' in r['message'] for r in report['results']))
        self.assertEqual(file_hash(self.clip), original)  # Rejected before any media write.

    def test_export_changed_after_processing_prevents_cleanup(self):
        from llc_markers.engine import cleanup_projects

        def change_then_cleanup(*args, **kwargs):
            stat = self.clip.stat()
            with self.clip.open('r+b') as handle:
                handle.seek(-1, 2)
                value = handle.read(1)[0]
                handle.seek(-1, 2)
                handle.write(bytes([value ^ 1]))
            os.utime(self.clip, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return cleanup_projects(*args, **kwargs)

        with patch('llc_markers.engine.cleanup_projects', side_effect=change_then_cleanup):
            report = self.unchecked_batch()
        self.assertTrue(self.project.exists())
        self.assertEqual(report['cleanup_summary'], {'error': 1})
        self.assertIn('Export content changed', report['cleanup_results'][0]['message'])

    def test_pending_media_transaction_prevents_cleanup(self):
        pending = state_path(self.settings, self.clip).with_suffix('.pending.json')
        pending.parent.mkdir(parents=True)
        pending.write_text('{}', encoding='utf-8')
        report = self.unchecked_batch()
        self.assertEqual(report['summary']['error'], 1)
        self.assertEqual(report['cleanup_summary'], {'retained': 1})
        self.assertTrue(self.project.exists())

    def test_interruption_after_delete_leaves_verified_backup_receipt(self):
        unlink = Path.unlink
        expected_hash = file_hash(self.project)

        def interrupt_after_delete(path, *args, **kwargs):
            result = unlink(path, *args, **kwargs)
            if path == self.project:
                raise KeyboardInterrupt()
            return result

        with patch.object(Path, 'unlink', new=interrupt_after_delete):
            with self.assertRaises(KeyboardInterrupt):
                self.unchecked_batch()
        self.assertFalse(self.project.exists())
        reports = list((self.settings.work_dir / 'reports').glob('*.json'))
        self.assertEqual(len(reports), 1)
        receipt = json.loads(reports[0].read_text(encoding='utf-8'))['cleanup_results'][0]
        self.assertEqual(receipt['status'], 'prepared')
        self.assertEqual(receipt['sha256'], expected_hash)
        self.assertEqual(file_hash(Path(receipt['backup'])), expected_hash)

    def test_project_backup_failure_prevents_delete(self):
        original_hash = file_hash

        def bad_backup_hash(path):
            if 'Project Backups' in path.parts:
                return 'incorrect checksum'
            return original_hash(path)

        with patch('llc_markers.engine.file_hash', side_effect=bad_backup_hash):
            report = self.unchecked_batch()
        self.assertEqual(report['cleanup_summary'], {'error': 1})
        self.assertIn('backup verification failed', report['cleanup_results'][0]['message'])
        self.assertTrue(self.project.exists())

    def test_delete_failure_preserves_project_and_records_verified_backup(self):
        unlink = Path.unlink

        def deny_project_delete(path, *args, **kwargs):
            if path == self.project:
                raise PermissionError('Project still open')
            return unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', new=deny_project_delete):
            report = self.unchecked_batch()
        self.assertEqual(report['cleanup_summary'], {'error': 1})
        self.assertTrue(self.project.exists())
        backup = Path(report['cleanup_results'][0]['backup'])
        self.assertEqual(file_hash(backup), file_hash(self.project))

    def test_copy_mode_and_already_processed_output_are_verified(self):
        self.settings.output_dir = self.root / 'outputs'
        self.settings.delete_projects_after_success = False
        self.run_batch(True)
        self.settings.delete_projects_after_success = True
        before = file_hash(self.clip)
        report = self.run_batch(True)
        self.assertEqual(report['summary'], {'up_to_date': 1, 'no_markers': 1})
        self.assertEqual(report['cleanup_summary'], {'deleted': 1})
        self.assertEqual(file_hash(self.clip), before)
        self.assertFalse((self.settings.output_dir / self.empty.name).exists())
        self.assertIn('Test statue', [c.title for c in read_chapters(
            self.tools.info(self.settings.output_dir / self.clip.name))])

    def test_up_to_date_clip_must_match_previous_verified_checksum(self):
        self.settings.delete_projects_after_success = False
        self.run_batch(True)
        record_path = state_path(self.settings, self.clip)
        record = json.loads(record_path.read_text(encoding='utf-8'))
        record['output_sha256'] = 'wrong previous checksum'
        record_path.write_text(json.dumps(record), encoding='utf-8')
        self.settings.delete_projects_after_success = True
        report = self.unchecked_batch()
        self.assertEqual(report['summary']['error'], 1)
        self.assertEqual(report['cleanup_summary'], {'retained': 1})
        self.assertTrue(self.project.exists())

    def test_cleanup_is_per_project_and_preserves_relative_backup_paths(self):
        nested = self.root / 'cuts' / 'Nested'
        nested.mkdir()
        second = nested / self.project.name
        second.write_text(json.dumps({'version': 2, 'mediaFileName': 'source.mp4',
            'cutSegments': [{'start': 8, 'end': 11.7, 'name': 'Missing export'}]}), encoding='utf-8')
        report = self.unchecked_batch()
        self.assertEqual(report['cleanup_summary'], {'retained': 1, 'deleted': 1})
        self.assertTrue(second.exists())
        self.assertFalse(self.project.exists())
        shutil.copy2(self.empty, self.empty.with_name('PRE Missing export.mp4'))
        report = self.unchecked_batch()
        self.assertEqual(report['cleanup_summary'], {'deleted': 1})
        backup = Path(report['cleanup_results'][0]['backup'])
        self.assertEqual(backup.parts[-2:], ('Nested', second.name))
        self.assertFalse(second.exists())


if __name__ == '__main__':
    unittest.main()
