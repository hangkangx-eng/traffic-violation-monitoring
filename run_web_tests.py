from pathlib import Path
import os
import runpy
import site
import sys

root = Path(__file__).resolve().parent
project = Path(os.environ.get('TRAFFIC_PROJECT_ROOT', root))
site.addsitedir(str(project / '.venv/Lib/site-packages'))
sys.path.insert(0, str(root / 'web_deps'))
sys.path.insert(0, str(root))
sys.path.append(str(project))
sys.dont_write_bytecode = True
runpy.run_path(str(root / 'tests/test_web.py'), run_name='__main__')
