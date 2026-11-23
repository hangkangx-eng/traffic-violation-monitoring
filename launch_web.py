"""Start both the Web frontend and the API on the local computer."""
from pathlib import Path
import argparse
import json
import os
import site
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
PROJECT = Path(os.environ.get('TRAFFIC_PROJECT_ROOT', ROOT))
sys.dont_write_bytecode = True
site.addsitedir(str(PROJECT / '.venv' / 'Lib' / 'site-packages'))
# Web-only dependencies are kept separate from the desktop application's environment.
sys.path.insert(0, str(ROOT / 'web_deps'))
sys.path.insert(0, str(ROOT))
sys.path.append(str(PROJECT))
os.chdir(PROJECT)
os.environ['TRAFFIC_PROJECT_ROOT'] = str(PROJECT)
os.environ.setdefault('YOLO_AUTOINSTALL', 'false')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    url = f'http://127.0.0.1:{args.port}'
    if not args.no_browser:
        try:
            with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
                if json.load(response).get('service') == 'traffic-monitor-web':
                    webbrowser.open(url)
                    raise SystemExit(0)
        except (OSError, ValueError):
            pass
    if not args.no_browser:
        def open_when_ready():
            for _ in range(60):
                try:
                    with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open(url)
                            return
                except Exception:
                    time.sleep(.5)
        threading.Thread(target=open_when_ready, daemon=True).start()
    print('Traffic Monitor Web: ' + url, flush=True)
    print('API documentation: ' + url + '/docs', flush=True)
    import uvicorn
    uvicorn.run('backend.main:app', host='127.0.0.1', port=args.port, workers=1, log_level='info', timeout_graceful_shutdown=5)
