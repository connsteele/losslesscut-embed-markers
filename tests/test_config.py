from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest

from llc_markers.cli import parser, settings_from_args
from llc_markers.engine import DEFAULT_WORK_DIR
from llc_markers.model import MarkerError


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class ConfigurationTests(unittest.TestCase):
    def test_minimal_config_defaults_projects_work_and_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / 'minimal.toml'
            config.write_text('clips_dir = "Cut"\nsources_dir = "Unsorted"\n')
            settings = settings_from_args(parser().parse_args(['--config', str(config)]))
            self.assertEqual(settings.clips_dir, root / 'Cut')
            self.assertEqual(settings.projects_dir, settings.clips_dir)
            self.assertEqual(settings.sources_dir, root / 'Unsorted')
            self.assertEqual(settings.work_dir, DEFAULT_WORK_DIR)
            self.assertEqual(settings.prefix, 'PRE ')
            self.assertFalse(settings.delete_projects_after_success)
            self.assertFalse(settings.remove_placeholder_chapters)

    def test_placeholder_setting_and_override(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'cleanup.toml'
            config.write_text('clips_dir = "."\nsources_dir = "."\nremove_placeholder_chapters = true\n')
            settings = settings_from_args(parser().parse_args(['--config', str(config)]))
            self.assertTrue(settings.remove_placeholder_chapters)
            settings = settings_from_args(parser().parse_args([
                '--config', str(config), '--no-remove-placeholder-chapters']))
            self.assertFalse(settings.remove_placeholder_chapters)
            config.write_text('clips_dir = "."\nsources_dir = "."\nremove_placeholder_chapters = "false"\n')
            settings = settings_from_args(parser().parse_args(['--config', str(config)]))
            with self.assertRaisesRegex(MarkerError, 'true or false'):
                settings.validate()

    def test_cleanup_toggle_and_cli_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'cleanup.toml'
            config.write_text('clips_dir = "Cut"\nsources_dir = "Unsorted"\n'
                              'delete_projects_after_success = true\n')
            settings = settings_from_args(parser().parse_args(['--config', str(config)]))
            self.assertTrue(settings.delete_projects_after_success)
            settings = settings_from_args(parser().parse_args([
                '--config', str(config), '--no-delete-projects-after-success']))
            self.assertFalse(settings.delete_projects_after_success)
            config.write_text('clips_dir = "Cut"\nsources_dir = "Unsorted"\n')
            settings = settings_from_args(parser().parse_args([
                '--config', str(config), '--delete-projects-after-success']))
            self.assertTrue(settings.delete_projects_after_success)

    def test_cleanup_requires_a_real_boolean(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'cleanup.toml'
            for invalid in ('"false"', '"true"', '1', '[]'):
                with self.subTest(invalid=invalid):
                    config.write_text('clips_dir = "."\nsources_dir = "."\n'
                                      f'delete_projects_after_success = {invalid}\n')
                    settings = settings_from_args(parser().parse_args(['--config', str(config)]))
                    with self.assertRaisesRegex(MarkerError, 'true or false'):
                        settings.validate()

    def test_cli_clips_override_also_changes_implicit_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / 'minimal.toml'
            config.write_text('clips_dir = "Cut"\nsources_dir = "Unsorted"\n')
            settings = settings_from_args(parser().parse_args([
                '--config', str(config), '--clips-dir', str(root / 'Other')]))
            self.assertEqual(settings.projects_dir, root / 'Other')

    def test_explicit_overrides_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / 'overrides.toml'
            config.write_text('clips_dir = "Cut"\nsources_dir = "Unsorted"\n'
                              'projects_dir = "Projects"\nwork_dir = "Work"\nprefix = "FW "\n')
            settings = settings_from_args(parser().parse_args([
                '--config', str(config), '--clips-dir', str(root / 'Other')]))
            self.assertEqual(settings.projects_dir, root / 'Projects')
            self.assertEqual(settings.work_dir, root / 'Work')
            self.assertEqual(settings.prefix, 'FW ')
            overridden = settings_from_args(parser().parse_args([
                '--config', str(config), '--projects-dir', str(root / 'More Projects'),
                '--work-dir', str(root / 'More Work'), '--prefix', 'TEST ']))
            self.assertEqual(overridden.projects_dir, root / 'More Projects')
            self.assertEqual(overridden.work_dir, root / 'More Work')
            self.assertEqual(overridden.prefix, 'TEST ')

    def test_default_work_directory_is_independent_of_current_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with working_directory(root):
                settings = settings_from_args(parser().parse_args([
                    '--clips-dir', 'Cut', '--sources-dir', 'Unsorted']))
            self.assertEqual(settings.work_dir, DEFAULT_WORK_DIR)
            self.assertEqual(settings.projects_dir, root / 'Cut')
            self.assertEqual(settings.work_dir.parent, Path(__file__).resolve().parent.parent)


if __name__ == '__main__':
    unittest.main()
