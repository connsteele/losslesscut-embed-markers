import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows menu')
class MenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='llc-menu-')
        self.root = Path(self.temp.name) / 'Menu With Spaces & Symbols!'
        self.root.mkdir()
        repo = Path(__file__).resolve().parent.parent
        shutil.copy2(repo / 'select-action.ps1', self.root / 'select-action.ps1')
        launcher = (repo / 'run.cmd').read_text().replace('.venv\\Scripts\\python.exe', sys.executable)
        (self.root / 'run.cmd').write_text(launcher)
        package = self.root / 'llc_markers'
        package.mkdir()
        (package / '__init__.py').write_text('')
        (package / '__main__.py').write_text(
            'import sys\nfrom pathlib import Path\nPath("job.txt").write_text(sys.argv[1])\n')

    def tearDown(self):
        self.temp.cleanup()

    def command(self):
        return [os.environ['COMSPEC'], '/d', '/c', 'run.cmd']

    def test_number_alone_waits_then_enter_starts_requested_job(self):
        for selection, mode in [('1', 'preview'), ('2', 'apply')]:
            with self.subTest(mode=mode):
                (self.root / 'job.txt').unlink(missing_ok=True)
                process = subprocess.Popen(self.command(), cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True)
                output = []
                prompted = threading.Event()

                def read_output():
                    while True:
                        char = process.stdout.read(1)
                        if not char:
                            break
                        output.append(char)
                        if ''.join(output[-20:]).endswith('press Enter: '):
                            prompted.set()

                reader = threading.Thread(target=read_output, daemon=True)
                reader.start()
                try:
                    self.assertTrue(prompted.wait(10), ''.join(output))
                    process.stdin.write(selection)
                    process.stdin.flush()
                    time.sleep(.25)
                    self.assertIsNone(process.poll())
                    self.assertFalse((self.root / 'job.txt').exists())
                    process.stdin.write('\n')
                    process.stdin.close()
                    self.assertEqual(process.wait(15), 0)
                    reader.join(3)
                    self.assertEqual((self.root / 'job.txt').read_text(), mode)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    if not process.stdin.closed:
                        process.stdin.close()
                    reader.join(3)
                    process.stdout.close()

    def test_invalid_blank_and_shell_text_do_not_launch_a_job(self):
        result = subprocess.run(self.command(), cwd=self.root, input='\n12\n0\n2 & echo unsafe\n"2"\n5\n',
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count('No action was started.'), 5)
        self.assertFalse((self.root / 'job.txt').exists())

    def test_closed_input_exits_without_running_a_job(self):
        result = subprocess.run(self.command(), cwd=self.root, input='', capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / 'job.txt').exists())

    def test_missing_prompt_helper_does_not_fall_through_to_preview(self):
        (self.root / 'select-action.ps1').unlink()
        result = subprocess.run(self.command(), cwd=self.root, input='', capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.root / 'job.txt').exists())
