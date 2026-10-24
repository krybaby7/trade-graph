import json
import os
import subprocess
import sys

import pytest

from trade_graph.kernel import operational_diagnostics as diagnostics


def test_private_rotating_events_never_retain_messages(tmp_path):
    sink = diagnostics.PrivateDiagnostics(tmp_path / 'ops', max_bytes=512, backups=2)
    for _ in range(30):
        sink.emit('worker_failure', error=ValueError('SECRET private prompt'), detail='SECRET')
    files = list((tmp_path / 'ops').glob('worker.jsonl*'))
    assert len(files) <= 3
    assert sum(p.stat().st_size for p in files) <= 1536
    assert (tmp_path / 'ops').stat().st_mode & 0o077 == 0
    for path in files:
        assert path.stat().st_mode & 0o077 == 0
        raw = path.read_text()
        assert 'SECRET' not in raw and 'detail' not in raw
        for line in raw.splitlines():
            assert json.loads(line)['error_type'] == 'ValueError'


@pytest.mark.parametrize('kind', ['directory', 'file', 'backup'])
def test_symlinks_refused(tmp_path, kind):
    target = tmp_path / 'target'
    target.write_text('unchanged')
    ops = tmp_path / 'ops'
    if kind == 'directory':
        ops.symlink_to(tmp_path, target_is_directory=True)
    else:
        ops.mkdir(mode=0o700)
        (ops / ('worker.jsonl' if kind == 'file' else 'worker.jsonl.1')).symlink_to(target)
    with pytest.raises(ValueError):
        diagnostics.PrivateDiagnostics(ops)
    assert target.read_text() == 'unchanged'


def test_existing_public_directory_refused(tmp_path):
    ops = tmp_path / 'ops'
    ops.mkdir(mode=0o755)
    with pytest.raises(ValueError):
        diagnostics.PrivateDiagnostics(ops)


def test_actual_synthetic_child_captures_safe_categories_and_exits(tmp_path):
    ops = tmp_path / 'ops'
    code = '''import warnings
from trade_graph.kernel.operational_diagnostics import worker_context
with worker_context():
    print('SECRET model conversation')
    warnings.warn('SECRET credential', RuntimeWarning)
    raise ValueError('SECRET unredacted error')
'''
    env = {**os.environ, 'TRADE_GRAPH_DIAGNOSTICS_DIRECTORY': str(ops)}
    child = subprocess.Popen([sys.executable, '-c', code], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert child.wait(timeout=10) != 0
    raw = (ops / 'worker.jsonl').read_text()
    assert 'SECRET' not in raw
    events = [json.loads(line) for line in raw.splitlines()]
    assert {'worker_start', 'runtime_warning', 'worker_failure', 'worker_exit'} <= {e['event'] for e in events}
    assert events[-1]['stdout_bytes'] > 0
    assert events[-1]['threads'] >= 1
    assert child.poll() is not None
