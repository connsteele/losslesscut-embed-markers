"""Setup maintenance using only the Python standard library."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tomllib


RESET_TARGETS = ('.venv', 'losslesscut_embed_markers.egg-info', '__pycache__',
                 'llc_markers/__pycache__', 'tests/__pycache__')


def config_paths(repo: Path):
    config_file = repo / 'config.local.toml'
    config = tomllib.loads(config_file.read_text(encoding='utf-8-sig')) if config_file.exists() else {}
    paths = {key: (repo / Path(config[key])).resolve() for key in
             ('clips_dir', 'sources_dir', 'projects_dir', 'output_dir', 'work_dir') if key in config}
    paths.setdefault('work_dir', repo / '.llc-markers-work')
    return paths


def reset_plan(repo: Path):
    repo = repo.resolve()
    paths = config_paths(repo)
    for work in {paths['work_dir'], repo / '.llc-markers-work'}:
        if (work / 'run.lock').exists():
            raise ValueError(f'A run lock exists at {work / "run.lock"}. Finish the run before resetting setup.')
    targets = []
    for relative in RESET_TARGETS:
        target = repo / relative
        if not target.exists() and not target.is_symlink():
            continue
        if target.is_symlink() or target.resolve() != target or not target.resolve().is_relative_to(repo):
            raise ValueError(f'Reset target contains a link or leaves the repository: {target}')
        for protected in paths.values():
            if target == protected or target.is_relative_to(protected) or protected.is_relative_to(target):
                raise ValueError(f'Reset target overlaps a configured media/work directory: {target} / {protected}')
        targets.append(str(target))
    return {'repo': str(repo), 'targets': targets, 'work_dir': str(paths['work_dir'])}


def latest_report(repo: Path):
    reports = config_paths(repo)['work_dir'] / 'reports'
    candidates = list(reports.glob('*.txt'))
    if not candidates:
        raise ValueError('No readable reports exist yet. Run Preview or Apply first.')
    return max(candidates, key=lambda p: p.stat().st_mtime_ns)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['reset-plan', 'report'])
    args = p.parse_args(argv)
    repo = Path(__file__).resolve().parent.parent
    try:
        if args.command == 'reset-plan':
            print(json.dumps(reset_plan(repo)))
        else:
            report = latest_report(repo)
            print(f'Opening report: {report}')
            os.startfile(report)
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
