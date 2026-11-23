"""Create a Python 3.12 environment and launch the downloaded project."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
VENV=ROOT/'.venv'
PYTHON=VENV/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')


def run(args,**kwargs):
    return subprocess.run([str(arg) for arg in args],cwd=ROOT,check=True,**kwargs)


def is_python312(path):
    try:
        result=subprocess.run([str(path),'-c','import sys; raise SystemExit(sys.version_info[:2] != (3,12))'],
                              capture_output=True,timeout=10)
        return result.returncode==0
    except (OSError,subprocess.TimeoutExpired):
        return False


def find_python312():
    candidates=[PYTHON,sys.executable,shutil.which('python3.12')]
    launcher=shutil.which('py')
    if launcher:
        result=subprocess.run([launcher,'-0p'],capture_output=True,text=True,errors='replace')
        for line in result.stdout.splitlines():
            match=re.search(r'([A-Za-z]:\\.*python\.exe)\s*$',line)
            if match:candidates.append(match.group(1))
    for candidate in candidates:
        if candidate and is_python312(candidate):
            return str(candidate)
    raise RuntimeError('Python 3.12 was not found. Install Python 3.12, then run this launcher again.')


def lfs_pointer(path):
    if not path.is_file():return False
    with path.open('rb') as stream:
        return stream.read(80).startswith(b'version https://git-lfs.github.com/spec/v1')


def ensure_assets():
    assets=[ROOT/'yolo11s.pt',ROOT/'samples/traffic.mp4']
    if any(lfs_pointer(path) for path in assets):
        if shutil.which('git'):
            run(['git','lfs','pull'])
        if any(lfs_pointer(path) for path in assets):
            raise RuntimeError('Git LFS files were not downloaded. Install Git LFS, then run: git lfs pull')
    missing=[str(path.relative_to(ROOT)) for path in assets if not path.is_file()]
    if missing:raise RuntimeError('Missing repository assets: '+', '.join(missing)+'. Use git clone with Git LFS enabled.')


def requirements_hash():
    content=b''.join((ROOT/name).read_bytes() for name in ['requirements.txt','requirements-web.txt'])
    return hashlib.sha256(content).hexdigest()


def ensure_environment():
    interpreter=find_python312()
    if not is_python312(PYTHON):
        if (VENV/'pyvenv.cfg').exists():
            # Repair a moved Python 3.12 environment without deleting its packages.
            run([interpreter,'-m','venv','--upgrade',VENV])
        else:
            run([interpreter,'-m','venv',VENV])
    marker=VENV/'.traffic-requirements.sha256'
    digest=requirements_hash()
    if not marker.is_file() or marker.read_text().strip()!=digest:
        print('Preparing project dependencies. The first run requires internet access.',flush=True)
        run([PYTHON,'-m','pip','install','--upgrade','pip'])
        run([PYTHON,'-m','pip','install','--index-url','https://download.pytorch.org/whl/cpu',
             'torch==2.10.0','torchvision==0.25.0'])
        run([PYTHON,'-m','pip','install','-r','requirements.txt','-r','requirements-web.txt'])
        marker.write_text(digest,encoding='ascii')
    return PYTHON


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',choices=['web','desktop','setup','test'],default='web')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    try:
        ensure_assets()
        interpreter=ensure_environment()
        print('Project environment is ready.',flush=True)
        if args.action=='setup':return 0
        if args.action=='test':return run([interpreter,'-B','run_web_tests.py']).returncode
        if args.action=='desktop':return run([interpreter,'-B','-m','app.main']).returncode
        command=[interpreter,'-B','launch_web.py','--port',str(args.port)]
        if args.no_browser:command.append('--no-browser')
        return run(command).returncode
    except (RuntimeError,OSError,subprocess.CalledProcessError) as exc:
        print('\nStartup failed: '+str(exc),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
