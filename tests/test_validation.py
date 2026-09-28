import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from llc_markers.engine import Settings, files_under, check_destination_path
from llc_markers.media import packet_offset
from llc_markers.model import MarkerError


class ValidationTests(unittest.TestCase):
    def test_nonfinite_packet_times_cannot_prove_alignment(self):
        for value in ('nan', 'inf', '-inf'):
            with self.subTest(value=value):
                source = [{'data_hash': str(i), 'pts_time': value} for i in range(6)]
                clip = [{'data_hash': str(i), 'pts_time': str(i)} for i in range(6)]
                with self.assertRaises(MarkerError):
                    packet_offset(source, clip)

    def test_failed_directory_scan_is_not_reported_as_empty_success(self):
        def denied(root, **kwargs):
            error = PermissionError('Access denied')
            error.filename = str(root / 'blocked')
            if kwargs.get('onerror'):
                kwargs['onerror'](error)
            return []

        with patch('llc_markers.engine.os.walk', side_effect=denied):
            with self.assertRaisesRegex(MarkerError, 'scan'):
                files_under(Path('clips'), {'.mp4'})

    def test_output_must_not_write_inside_original_source_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name in ('clips', 'sources'):
                (root / name).mkdir()
            settings = Settings(root/'clips', root/'clips', root/'sources', root/'work',
                                output_dir=root/'sources'/'outputs')
            with self.assertRaisesRegex(MarkerError, 'source'):
                settings.validate()

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows junctions')
    def test_junction_is_skipped_in_scans_and_rejected_for_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            outside, clips = root/'outside', root/'clips'
            outside.mkdir(); clips.mkdir()
            (outside/'important.mp4').write_bytes(b'untouched')
            link = clips/'Linked Folder'
            env = dict(os.environ, LLC_TEST_LINK=str(link), LLC_TEST_TARGET=str(outside))
            subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                "$ErrorActionPreference='Stop'; $null = New-Item -ItemType Junction -Path $env:LLC_TEST_LINK -Target $env:LLC_TEST_TARGET"],
                env=env, check=True, capture_output=True, timeout=15)
            try:
                self.assertEqual(files_under(clips, {'.mp4'}), [])
                settings = Settings(clips, clips, outside, root/'work')
                with self.assertRaisesRegex(MarkerError, 'linked'):
                    check_destination_path(settings, link/'important.mp4')
                self.assertEqual((outside/'important.mp4').read_bytes(), b'untouched')
            finally:
                # Remove only this verified directory entry, never its target.
                self.assertEqual(link.parent, clips)
                self.assertEqual(link.resolve(), outside)
                link.rmdir()
