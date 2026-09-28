"""Adversarial cases found during the September code audit; synthetic files only."""
import json
import os
import shutil
from unittest.mock import patch

from test_integration import MediaTestCase
from llc_markers.engine import process_batch, state_path, file_hash
from llc_markers import engine
from llc_markers.media import MediaTools


class AuditTests(MediaTestCase):
    def unchecked(self):
        return process_batch(self.settings, True, emit=lambda _: None)

    def test_malformed_state_is_a_clip_error_and_other_clips_continue(self):
        path = state_path(self.settings, self.clip)
        path.parent.mkdir(parents=True)
        original = file_hash(self.clip)
        for value in ([], None, {'version': 1}, {'version': 1, 'chapters': None}):
            with self.subTest(state=value):
                path.write_text(json.dumps(value))
                report = self.unchecked()
                self.assertEqual(report['summary'].get('error'), 1)
                self.assertEqual(report['summary'].get('no_markers'), 1)
                self.assertEqual(file_hash(self.clip), original)

    def test_new_copy_destination_appearing_during_remux_is_preserved(self):
        self.settings.output_dir = self.root / 'outputs'
        self.settings.output_dir.mkdir()
        destination = self.settings.output_dir / self.clip.name
        remux = MediaTools.remux
        intruder_hash = file_hash(self.empty)

        def external_write(tools, *args):
            remux(tools, *args)
            shutil.copy2(self.empty, destination)

        with patch.object(MediaTools, 'remux', new=external_write):
            report = self.unchecked()
        self.assertEqual(report['summary'].get('error'), 1)
        self.assertEqual(file_hash(destination), intruder_hash)

    def test_ambiguous_candidates_cannot_be_claimed_by_another_project(self):
        duplicate = self.clip.with_name('PRE_Sample.mp4')
        shutil.copy2(self.clip, duplicate)
        second = self.root / 'cuts' / 'second.llc'
        second.write_text(json.dumps({'version': 2, 'mediaFileName': 'source.mp4', 'cutSegments': [
            {'start': 2.35, 'end': 7.9, 'name': 'Another'}, {'start': 4.21, 'name': 'Another marker'}]}))
        mapping = self.root / 'mapping.json'
        mapping.write_text(json.dumps({'second.llc::0': self.clip.name}))
        self.settings.mapping_file = mapping
        original = file_hash(self.clip)
        report = self.unchecked()
        self.assertEqual(report['summary'].get('updated', 0), 0)
        self.assertEqual(report['summary'].get('ambiguous'), 2)
        self.assertEqual(file_hash(self.clip), original)

    def test_recording_beside_project_is_never_treated_as_an_export(self):
        recording = self.root / 'cuts' / 'PRE Recording.mp4'
        shutil.copy2(self.source, recording)
        project = json.loads(self.project.read_text())
        project['mediaFileName'] = recording.name
        self.project.write_text(json.dumps(project))
        second = self.root / 'cuts' / 'recording.llc'
        second.write_text(json.dumps({'version': 2, 'mediaFileName': 'source.mp4', 'cutSegments': [
            {'start': 0, 'end': 12, 'name': 'Recording'}, {'start': 4, 'name': 'Would alter original'}]}))
        original = file_hash(recording)
        report = self.unchecked()
        self.assertEqual(file_hash(recording), original)
        item = next(x for x in report['results'] if x.get('clip') == str(recording))
        self.assertEqual(item['status'], 'error')

    def test_changed_copy_with_preserved_size_and_timestamp_is_not_overwritten(self):
        self.settings.output_dir = self.root / 'outputs'
        self.run_batch(True)
        destination = self.settings.output_dir / self.clip.name
        before = destination.stat()
        with destination.open('r+b') as handle:
            handle.seek(-1, 2)
            value = handle.read(1)[0]
            handle.seek(-1, 2)
            handle.write(bytes([value ^ 1]))
        os.utime(destination, ns=(before.st_atime_ns, before.st_mtime_ns))
        changed = file_hash(destination)
        self.save_project('New label')
        report = self.unchecked()
        self.assertEqual(report['summary'].get('error'), 1)
        self.assertEqual(file_hash(destination), changed)

    def test_changed_input_with_same_signature_regenerates_its_copy(self):
        self.settings.output_dir = self.root / 'outputs'
        self.run_batch(True)
        destination = self.settings.output_dir / self.clip.name
        before = self.clip.stat()
        # Alter harmless encoder metadata with exactly the same byte length.
        data = self.clip.read_bytes()
        self.assertIn(b'Lavf', data)
        self.clip.write_bytes(data.replace(b'Lavf', b'Test', 1))
        os.utime(self.clip, ns=(before.st_atime_ns, before.st_mtime_ns))
        report = self.unchecked()
        self.assertEqual(report['summary'].get('updated'), 1)
        self.assertIn(b'Test', self.clip.read_bytes())
        state = json.loads(state_path(self.settings, destination).read_text())
        self.assertEqual(state['input_sha256'], file_hash(self.clip))

    def test_project_edit_during_remux_with_same_signature_does_not_install_stale_markers(self):
        remux = MediaTools.remux
        original = file_hash(self.clip)

        def edit_project(tools, *args):
            remux(tools, *args)
            before = self.project.stat()
            self.save_project('Edit statue')
            os.utime(self.project, ns=(before.st_atime_ns, before.st_mtime_ns))

        with patch.object(MediaTools, 'remux', new=edit_project):
            report = self.unchecked()
        self.assertEqual(report['summary'].get('error'), 1)
        self.assertEqual(file_hash(self.clip), original)

    def test_state_write_failure_after_replace_leaves_recoverable_pending_transaction(self):
        self.settings.delete_projects_after_success = True
        statefile = state_path(self.settings, self.clip)
        write_json = engine.write_json

        def fail_final_state(path, value):
            if path == statefile:
                raise OSError('Simulated disk failure writing final state')
            return write_json(path, value)

        original = file_hash(self.clip)
        with patch.object(engine, 'write_json', side_effect=fail_final_state):
            report = self.unchecked()
        self.assertEqual(report['summary'].get('error'), 1)
        self.assertTrue(self.project.exists())
        pending = statefile.with_suffix('.pending.json')
        record = json.loads(pending.read_text())
        self.assertEqual(record['output_sha256'], file_hash(self.clip))
        self.assertEqual(file_hash(type(self.clip)(record['backup'])), original)
        next_run = self.unchecked()
        self.assertEqual(next_run['summary'].get('error'), 1)
        self.assertTrue(pending.exists())

    def test_existing_copy_modified_during_remux_is_preserved(self):
        self.settings.output_dir = self.root / 'outputs'
        self.run_batch(True)
        destination = self.settings.output_dir / self.clip.name
        self.save_project('Changed marker')
        remux = MediaTools.remux
        changed = None

        def modify_copy(tools, *args):
            nonlocal changed
            remux(tools, *args)
            before = destination.stat()
            data = destination.read_bytes()
            destination.write_bytes(data.replace(b'Lavf', b'Test', 1))
            os.utime(destination, ns=(before.st_atime_ns, before.st_mtime_ns))
            changed = file_hash(destination)

        with patch.object(MediaTools, 'remux', new=modify_copy):
            report = self.unchecked()
        self.assertEqual(report['summary'].get('error'), 1)
        self.assertEqual(file_hash(destination), changed)

    def test_in_place_export_changed_after_remux_with_same_signature_is_preserved(self):
        remux = MediaTools.remux
        changed = None

        def modify_input(tools, *args):
            nonlocal changed
            remux(tools, *args)
            before = self.clip.stat()
            data = self.clip.read_bytes()
            self.clip.write_bytes(data.replace(b'Lavf', b'Test', 1))
            os.utime(self.clip, ns=(before.st_atime_ns, before.st_mtime_ns))
            changed = file_hash(self.clip)

        with patch.object(MediaTools, 'remux', new=modify_input):
            report = self.unchecked()
        self.assertEqual(report['summary'].get('error'), 1)
        self.assertEqual(file_hash(self.clip), changed)
