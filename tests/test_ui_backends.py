"""Backend lifecycle checks use fake children; hardware inference lives in e2e."""
import dataclasses
import threading
import time

import httpx
import pytest

from basal.ui import backends, catalog, settings
from basal.ui.app import Runner
from tests.test_ui_app import HW, MINI, SMALL, make_fleet, wait_for


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv('BASAL_UI_CONFIG', str(tmp_path / 'settings.json'))
    hw = dataclasses.replace(HW, engines=frozenset({'torch'}), available_bytes=HW.memory_bytes)
    fleet = make_fleet(tmp_path, hw)
    monkeypatch.setattr(backends, 'probe', lambda *a: {'versions': {'torch': 'test'}, 'missing': [], 'error': None})
    monkeypatch.setattr(catalog, 'is_downloaded', lambda *a, **kw: tmp_path / ('a' * 40))
    def start(self, assessment, gen, media=False, cache_dir=None, serve=None):
        with self.lock:
            self.state, self.ready_at = 'ready', time.time()
            self.loaded_path = str(tmp_path / ('a' * 40))
            self.base_url = 'http://127.0.0.1:1'
            self.serve_snapshot = self.snapshot_override or {'log_decisions': '', 'max_len': 4096, 'calibration': None}
    monkeypatch.setattr(Runner, '_start', start)
    monkeypatch.setattr(Runner, 'decide', lambda *a, **kw: httpx.Response(200, json={'answers': {'check': {'choice': 'blue'}}}, request=httpx.Request('POST', 'http://test')))
    monkeypatch.setattr(httpx, 'get', lambda *a, **kw: httpx.Response(200, json={'status': 'ok', 'running': 0, 'queued': 0}, request=httpx.Request('GET', 'http://test')))
    result = backends.Manager(fleet, lambda: [MINI, SMALL], lambda: hw, settings)
    yield result
    fleet.shutdown()


def request(unload=None):
    return {'backend': 'torch', 'model': MINI.id, 'mode': 'eager', 'unload': unload or []}


def finish(manager, kind='backend-test'):
    wait_for(lambda: not manager.fleet.jobs.get(kind).running)
    return manager.fleet.jobs.get(kind)


def loaded(manager, variant=SMALL):
    manager.fleet.load(catalog.assess(variant, manager.current_hardware()))
    wait_for(lambda: manager.fleet.runners[variant.id].state == 'ready')
    runner = manager.fleet.runners[variant.id]
    return runner, {'id': variant.id, 'generation': runner.generation}


def test_inventory_missing_packages_still_shows_compatible_models(manager, monkeypatch):
    monkeypatch.setattr(backends, 'probe', lambda *a: {'versions': {}, 'missing': ['torch'], 'error': None})
    data = manager.inventory()
    torch = next(b for b in data['backends'] if b['id'] == 'torch')
    assert torch['status'] == 'not_installed' and torch['models']
    assert next(b for b in data['backends'] if b['id'] == 'mlx')['status'] == 'unavailable'
    assert 'basal[sglang]' not in backends.setup('sglang', manager.current_hardware())['command']



def test_inventory_codes_machine_reasons_and_setup_notes(manager):
    data = manager.inventory()
    mlx = next(b for b in data['backends'] if b['id'] == 'mlx')
    assert mlx['reason_code'] == 'backend_mlx_device' and mlx['reason_params'] == {}
    assert mlx['reason'] == backends.BACKEND_TEMPLATES[mlx['reason_code']].format(**mlx['reason_params'])
    assert mlx['setup']['note_code'] == 'backend_setup_default'
    assert mlx['setup']['note'] == backends.BACKEND_TEMPLATES[mlx['setup']['note_code']].format(
        **mlx['setup']['note_params'])

def test_private_test_does_not_change_settings_history_or_stats(manager, tmp_path):
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    manager.start(request())
    assert finish(manager).phase == 'done'
    assert manager.records()[0]['status'] == 'passed'
    assert not manager.fleet.temporary and not manager.fleet.reserved
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


def test_unload_and_restore_preserves_active_snapshot(manager):
    runner, selected = loaded(manager)
    runner.serve_snapshot = {'log_decisions': '/tmp/old.jsonl', 'max_len': 1234, 'calibration': None}
    manager.start(request([selected]))
    assert finish(manager).phase == 'done'
    record = manager.records()[0]
    assert SMALL.id not in manager.fleet.runners and record['stopped_models']
    manager.restore(record['id'])
    assert finish(manager, 'backend-restore').phase == 'done'
    restored = manager.fleet.runners[SMALL.id]
    assert restored.serve_snapshot['max_len'] == 1234
    assert restored.options['revision'] == 'a' * 40
    assert restored.local_only and not restored.remember
    assert not manager.records()[0]['stopped_models']


@pytest.mark.parametrize('health', [{'status':'ok'}, {'status':'ok','running':1,'queued':0}, {'status':'ok','running':0,'queued':True}])
def test_unknown_or_busy_health_never_unloads(manager, monkeypatch, health):
    runner, selected = loaded(manager)
    monkeypatch.setattr(httpx, 'get', lambda *a, **kw: httpx.Response(200, json=health, request=httpx.Request('GET','http://test')))
    manager.start(request([selected]))
    assert finish(manager).phase == 'error'
    assert manager.fleet.runners[SMALL.id] is runner and runner.state == 'ready'
    assert not manager.fleet.draining and not manager.fleet.reserved


def test_active_lease_and_stale_generation_refused(manager):
    runner, selected = loaded(manager)
    with manager.fleet.lease(SMALL.id):
        with pytest.raises(RuntimeError, match='busy'):
            manager.start(request([selected]))
    with pytest.raises(RuntimeError, match='changed'):
        manager.start(request([{**selected, 'generation': 'old'}]))
    assert not manager.fleet.leases


def test_whole_job_lease_blocks_test_between_requests(manager):
    runner, selected = loaded(manager)
    release = threading.Event()
    manager.fleet.start_model_job('batch', SMALL.id, 'test', lambda job: release.wait(2))
    try:
        with pytest.raises(RuntimeError, match='busy'):
            manager.start(request([selected]))
    finally:
        release.set()
    finish(manager, 'batch')
    assert not manager.fleet.leases


def test_cancel_inference_cleans_temporary_and_reservation(manager, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(Runner, 'decide', lambda *a: release.wait(2))
    manager.start(request())
    wait_for(lambda: manager.fleet.jobs.get('backend-test').phase == 'testing inference')
    manager.fleet.jobs.cancel('backend-test')
    assert finish(manager).phase == 'cancelled'
    release.set()
    assert not manager.fleet.temporary and not manager.fleet.reserved and not manager.fleet.draining


def test_restore_does_not_replace_new_model(manager):
    _, selected = loaded(manager)
    token = manager.start(request([selected]))['id']
    finish(manager)
    replacement, _ = loaded(manager)
    manager.restore(token)
    finish(manager, 'backend-restore')
    assert manager.fleet.runners[SMALL.id] is replacement
    assert 'already present' in manager.records()[0]['stopped_models'][0]['error']


def test_local_only_missing_files_never_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, 'is_downloaded', lambda *a, **kw: None)
    runner = Runner(models_dir_fn=lambda: tmp_path)
    monkeypatch.setattr(runner, '_download', lambda *a: pytest.fail('must not download'))
    runner.load(catalog.assess(MINI, HW), local_only=True, temporary=True)
    wait_for(lambda: runner.state == 'error')
    assert 'never downloads' in runner.error


def test_probe_timeout_is_attention(monkeypatch):
    def timeout(*a, **kw):
        raise backends.subprocess.TimeoutExpired('probe', 20)
    monkeypatch.setattr(backends.subprocess, 'run', timeout)
    assert 'Probe failed' in backends.probe('torch', ('torch',), HW)['error']


@pytest.mark.parametrize('platform,engines,expected', [
    ('cpu', {'torch'}, {'torch','gguf'}),
    ('apple', {'torch','mps','mlx'}, {'torch','mlx','gguf'}),
    ('cuda', {'torch'}, {'torch','gguf','vllm','sglang'}),
    ('cuda', {'torch','rocm','strix'}, {'torch','gguf','strix'}),
    ('tpu', {'tpu'}, {'torch','gguf','tpu'}),
])
def test_platform_inventory_rules(monkeypatch, platform, engines, expected):
    monkeypatch.setattr(backends.sys, 'platform', 'linux')
    hw = dataclasses.replace(HW, platform=platform, engines=frozenset(engines))
    assert {key for key in backends.BACKENDS if backends.machine_reason(key, hw) is None} == expected


def test_admission_gate_covers_health_check_and_reserves_capacity(manager, monkeypatch):
    runner, selected = loaded(manager)
    checking, release = threading.Event(), threading.Event()
    def health(*a, **kw):
        checking.set()
        release.wait(2)
        return httpx.Response(200, json={'status':'ok','running':0,'queued':0}, request=httpx.Request('GET','http://test'))
    monkeypatch.setattr(httpx, 'get', health)
    manager.start(request([selected]))
    assert checking.wait(1)
    try:
        with pytest.raises(RuntimeError):
            with manager.fleet.lease(SMALL.id):
                pytest.fail('admitted during drain')
        assert manager.fleet.reserved
        available = manager.fleet._memory()['budget_bytes'] - manager.fleet._memory()['reserve_bytes']
        with pytest.raises(RuntimeError):
            manager.fleet.check_budget(available)
    finally:
        release.set()
    finish(manager)


def test_cancel_startup_preserves_restore_record(manager, monkeypatch):
    _, selected = loaded(manager)
    monkeypatch.setattr(Runner, '_start', lambda *a, **kw: None)
    manager.start(request([selected]))
    wait_for(lambda: manager.fleet.jobs.get('backend-test').phase == 'starting model')
    manager.fleet.jobs.cancel('backend-test')
    assert finish(manager).phase == 'cancelled'
    assert manager.records()[0]['stopped_models'][0]['id'] == SMALL.id
    assert not manager.fleet.temporary and not manager.fleet.reserved


def test_restore_missing_files_retains_snapshot(manager, monkeypatch):
    _, selected = loaded(manager)
    token = manager.start(request([selected]))['id']
    finish(manager)
    monkeypatch.setattr(catalog, 'is_downloaded', lambda *a, **kw: None)
    manager.restore(token)
    finish(manager, 'backend-restore')
    assert 'files are missing' in manager.records()[0]['stopped_models'][0]['error']
    assert not manager.fleet.runners


def test_test_deadlines_cleanup(manager, monkeypatch):
    monkeypatch.setattr(Runner, '_start', lambda *a, **kw: None)
    monkeypatch.setattr(backends, 'START_TIMEOUT', 0)
    manager.start(request())
    assert finish(manager).phase == 'error'
    assert 'timed out' in manager.records()[0]['error']
    assert not manager.fleet.temporary and not manager.fleet.reserved


def test_backend_api_contract(manager, tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_ui_app import app_for
    app, _ = app_for(tmp_path, [MINI, SMALL], hw=manager.current_hardware(), fleet=manager.fleet)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/api/backends').status_code == 200
        _, selected = loaded(manager)
        manager.fleet.draining.add(SMALL.id)
        assert client.post('/api/unload', json={'id': SMALL.id}).status_code == 409
        manager.fleet.draining.clear()
        assert client.post('/api/backends/tests', json=request() | {'mode':'fake'}).status_code == 400
        assert client.delete('/api/backends/tests/unknown').status_code == 404
        response = client.post('/api/backends/tests', json=request())
        assert response.status_code == 202
        finish(manager)
        assert client.get('/api/backends/tests').json()['tests'][0]['status'] == 'passed'
