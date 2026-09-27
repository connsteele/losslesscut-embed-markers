import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from llc_markers.maintenance import latest_report, reset_plan


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='llc-setup-reset-')
        self.root = Path(self.temp.name).resolve() / 'Repository With Spaces'
        self.root.mkdir()
        for folder in ('.venv', 'losslesscut_embed_markers.egg-info', 'llc_markers/__pycache__',
                       'tests/__pycache__', '.llc-markers-work/backups', '.llc-markers-work/state',
                       'Saved Work/reports', 'Clips', 'Sources'):
            directory = self.root / folder
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'keep.txt').write_text(folder)
        self.config = self.root / 'config.local.toml'
        self.config.write_text('clips_dir = "Clips"\nsources_dir = "Sources"\nwork_dir = "Saved Work"\n')
        (self.root / 'Clips' / 'footage.mp4').write_bytes(b'media must remain unchanged')
        (self.root / 'config.example.toml').write_text('# example config')
        repo = Path(__file__).resolve().parent.parent
        shutil.copy2(repo / 'reset-setup.ps1', self.root / 'reset-setup.ps1')
        shutil.copy2(repo / 'run.cmd', self.root / 'run.cmd')
        shutil.copy2(repo / 'llc_markers' / 'maintenance.py', self.root / 'llc_markers' / 'maintenance.py')

    def tearDown(self):
        self.temp.cleanup()

    def run_reset(self):
        return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                               '-File', str(self.root / 'reset-setup.ps1')],
                              capture_output=True, text=True, timeout=30)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows reset launcher')
    def test_actual_reset_preserves_config_media_backups_and_both_work_directories(self):
        protected = [p for p in self.root.rglob('*') if p.is_file() and
                     not any(part in {'.venv', '__pycache__', 'losslesscut_embed_markers.egg-info'} for part in p.parts)]
        before = {p: p.read_bytes() for p in protected}
        result = self.run_reset()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.root / '.venv').exists())
        self.assertFalse((self.root / 'tests/__pycache__').exists())
        self.assertFalse((self.root / 'losslesscut_embed_markers.egg-info').exists())
        self.assertEqual(self.run_reset().returncode, 0)  # Already-reset setups are harmless.

    def test_plan_refuses_active_run_and_overlapping_storage(self):
        lock = self.root / 'Saved Work' / 'run.lock'
        lock.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'run lock'):
            reset_plan(self.root)
        lock.unlink()
        for setting in ('work_dir', 'clips_dir', 'sources_dir', 'projects_dir', 'output_dir'):
            with self.subTest(setting=setting):
                self.config.write_text(f'{setting} = ".venv/Important Files"\n')
                with self.assertRaisesRegex(ValueError, 'overlaps'):
                    reset_plan(self.root)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows reset launcher')
    def test_failed_guard_removes_nothing(self):
        self.config.write_text('work_dir = ".venv/Backups"\n')
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.run_reset()
        self.assertEqual(result.returncode, 1)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows menu')
    def test_menu_reset_option_works_without_an_installed_environment(self):
        before_config = self.config.read_bytes()
        result = subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(self.root / 'run.cmd')],
                                input='4\n', capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Setup reset complete', result.stdout)
        self.assertFalse((self.root / '.venv').exists())
        self.assertEqual(self.config.read_bytes(), before_config)
        self.assertTrue((self.root / '.llc-markers-work/backups/keep.txt').exists())

    def test_report_opens_from_configured_work_directory(self):
        report = self.root / 'Saved Work' / 'reports' / 'latest.txt'
        report.write_text('Completed with items needing attention')
        os.utime(report, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
        self.assertEqual(latest_report(self.root), report)


if __name__ == '__main__':
    unittest.main()
