"""Local backend inventory and isolated, explicitly requested inference checks."""
import dataclasses
import json
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time
import re
from concurrent.futures import ThreadPoolExecutor
import httpx
from . import catalog, hardware, options
from .jobs import Cancelled

BACKEND_TEMPLATES = {
    "backend_mlx_device": "This Basal integration supports MLX on Apple Silicon.",
    "backend_cuda_linux": "This Basal integration requires NVIDIA CUDA on Linux.",
    "backend_strix": "Requires AMD Strix Halo with a compatible ROCm PyTorch installation.",
    "backend_tpu": "No accessible TPU detected; requires a TPU host and JAX/libtpu.",
    "backend_status_installed": "Installed",
    "backend_status_not_installed": "Not installed",
    "backend_status_needs_attention": "Needs attention",
    "backend_status_unavailable": "Unavailable on this machine",
    "backend_test_status_starting": "starting",
    "backend_test_status_passed": "passed",
    "backend_test_status_cancelled": "cancelled",
    "backend_test_status_error": "error",
    "backend_setup_default": "Commands target this app environment. Restart basal-ui after installation, then Refresh.",
    "backend_setup_separate": "Use a separate environment: these packages bring their own PyTorch dependencies. Install this same Basal fork with its UI dependencies there and launch basal-ui from that environment. Installing there does not change this inventory.",
    "backend_setup_platform": "Use the platform-specific installer in the documentation for the correct PyTorch/ROCm build; do not replace a working GPU build with a generic wheel.",
    "backend_reason_missing": "Missing: {packages}",
    "backend_reason_unverified": "Dependency/device checks passed; inference is not verified.",
}


def _message_fields(text):
    if text is None:
        return None, None
    for code, template in BACKEND_TEMPLATES.items():
        keys = re.findall(r"\{(\w+)\}", template)
        pattern = re.escape(template)
        for key in keys:
            pattern = pattern.replace(re.escape("{" + key + "}"), f"(?P<{key}>.*?)", 1)
        match = re.fullmatch(pattern, text)
        if match:
            return code, match.groupdict()
    return None, None


# Names describe engines, not checkpoint formats or quantizations.
BACKENDS = {
    'torch': ('PyTorch', ('torch',), ('eager', 'mps', 'fast', 'fast-nocompile', 'fast-exit', 'fp8', 'nvfp4'), 'https://pytorch.org/get-started/locally/'),
    'mlx': ('MLX', ('mlx', 'mlx_lm'), ('mlx', 'mlx-q8'), 'https://ml-explore.github.io/mlx/build/html/install.html'),
    'gguf': ('llama.cpp (in process)', ('llama_cpp',), ('gguf',), 'https://llama-cpp-python.readthedocs.io/en/latest/'),
    'vllm': ('vLLM', ('vllm',), ('vllm',), 'https://docs.vllm.ai/en/stable/getting_started/installation/gpu/'),
    'sglang': ('SGLang', ('sglang',), ('sglang',), 'https://docs.sglang.io/docs/get-started/install'),
    'strix': ('Strix Halo', ('torch', 'triton'), ('strix',), 'https://rocm.docs.amd.com/projects/install-on-linux/en/latest/'),
    'tpu': ('TPU / JAX', ('jax', 'libtpu'), ('tpu',), 'https://docs.jax.dev/en/latest/installation.html#tpu'),
}
PROBE_TIMEOUT = 20
START_TIMEOUT = 1800
DECIDE_TIMEOUT = 120
PROBE = r'''
import importlib, importlib.metadata, importlib.util, json, sys
kind, modules, device = json.loads(sys.argv[1])
versions, missing = {}, []
for module in modules:
    distribution = {'mlx_lm':'mlx-lm','llama_cpp':'llama-cpp-python'}.get(module,module)
    try: versions[distribution] = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError: versions[distribution] = None
    if importlib.util.find_spec(module) is None: missing.append(module)
result = {'versions':versions, 'missing':missing, 'error':None}
if not missing:
    try:
        for module in modules: importlib.import_module(module)
        if kind in ('torch','strix'):
            import torch
            torch.ones(1,device=device).cpu()
        elif kind == 'mlx':
            import mlx.core as mx
            mx.eval(mx.ones((1,)))
        elif kind == 'tpu':
            import jax
            assert any(d.platform=='tpu' for d in jax.devices()), 'No TPU device accessible'
        elif kind in ('vllm','sglang'):
            import torch
            assert torch.cuda.is_available(), 'No CUDA device accessible'
            torch.ones(1,device='cuda').cpu()
    except Exception as exc: result['error'] = str(exc)
print('BASAL_PROBE='+json.dumps(result))
'''


def probe(backend, modules, hw):
    device = 'cuda' if hw.platform == 'cuda' else 'mps' if 'mps' in hw.engines else 'cpu'
    try:
        result = subprocess.run([sys.executable, '-c', PROBE, json.dumps([backend, modules, device])],
                                capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        line = next((s for s in reversed(result.stdout.splitlines()) if s.startswith('BASAL_PROBE=')), None)
        if result.returncode or line is None:
            return {'versions': {}, 'missing': [], 'error': (result.stderr or result.stdout)[-2000:] or 'Probe failed'}
        return json.loads(line.partition('=')[2])
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        return {'versions': {}, 'missing': [], 'error': f'Probe failed: {exc}'}


def machine_reason(backend, hw):
    if backend == 'mlx' and hw.platform != 'apple':
        return BACKEND_TEMPLATES['backend_mlx_device']
    if backend in ('vllm', 'sglang') and (hw.platform != 'cuda' or 'rocm' in hw.engines or sys.platform != 'linux'):
        return BACKEND_TEMPLATES['backend_cuda_linux']
    if backend == 'strix' and 'strix' not in hw.engines:
        return BACKEND_TEMPLATES['backend_strix']
    if backend == 'tpu' and 'tpu' not in hw.engines:
        return BACKEND_TEMPLATES['backend_tpu']
    return None


def setup(backend, hw):
    executable = shlex.quote(sys.executable)
    command, note = '', BACKEND_TEMPLATES['backend_setup_default']
    if backend == 'mlx':
        command = f'{executable} -m pip install "mlx>=0.32" "mlx-lm>=0.32"'
    elif backend == 'gguf':
        prefix = 'CMAKE_ARGS="-DGGML_METAL=on" ' if hw.platform == 'apple' else 'CMAKE_ARGS="-DGGML_CUDA=on" ' if hw.platform == 'cuda' and 'rocm' not in hw.engines else ''
        command = prefix + f'{executable} -m pip install "llama-cpp-python>=0.3.36"'
    elif backend in ('vllm', 'sglang'):
        package = '"vllm>=0.11"' if backend == 'vllm' else '"sglang[srt]==0.5.21"'
        command = 'uv venv --python 3.12 .venv-' + backend + '\nuv pip install --python .venv-' + backend + '/bin/python ' + package
        note = BACKEND_TEMPLATES['backend_setup_separate']
    elif backend == 'tpu':
        command = f'{executable} -m pip install "jax[tpu]==0.11.1"'
    else:
        note = BACKEND_TEMPLATES['backend_setup_platform']
    note_code, note_params = _message_fields(note)
    return {'command': command, 'note': note, 'note_code': note_code, 'note_params': note_params}


def matching_modes(backend, variant, hw):
    # Hypothetically provide the backend so absent packages do not hide compatible model families.
    capable = dataclasses.replace(hw, engines=hw.engines | {backend})
    assessment = catalog.assess(variant, capable)
    candidates = options.available_modes(variant, capable, assessment)
    result = []
    for item in candidates:
        mode = item['mode']
        if mode not in BACKENDS[backend][2] or not item['available']:
            continue
        if mode == 'mps' and (hw.platform != 'apple' or 'mps' not in hw.engines):
            continue
        if backend in ('vllm', 'sglang', 'tpu', 'strix') and variant.port != 'bf16' and not (backend == 'vllm' and variant.port in ('FP8', 'NVFP4')):
            continue
        if variant.port == 'GGUF' and variant.weights_only and not variant.base_repo:
            continue
        result.append(mode)
    return result


def estimate(variant, hw, mode):
    if not variant.weights_bytes or variant.weights_bytes <= 0:
        raise ValueError('Model size is unknown; cannot safely reserve test memory')
    assessment = catalog.assess(variant, hw)
    needed = max(assessment.est_memory_bytes or 0, int(variant.weights_bytes * 1.2) +
                 (catalog.GRAPH_ENGINE_OVERHEAD if mode in catalog.GRAPH_ENGINE_MODES else catalog.GIB))
    if mode in ('vllm', 'sglang'):
        needed = max(needed, int(hw.memory_bytes * options.DEFAULTS['gpu_memory']))
    return dataclasses.replace(assessment, mode=mode, est_memory_bytes=needed)


class Manager:
    def __init__(self, fleet, variants, hardware_fn, settings_mod):
        self.fleet, self.variants, self.hardware_fn, self.settings = fleet, variants, hardware_fn, settings_mod
        self.lock = threading.RLock()
        self.cached = None
        self.detected_hw = None
        self.tests = {}  # session-local restoration records survive later test jobs

    def inventory(self, refresh=False):
        with self.lock:
            if (self.detected_hw is None or refresh) and self.hardware_fn is hardware.detect:
                command = 'import json; from basal.ui.hardware import detect; print("BASAL_HW="+json.dumps(detect().to_dict()))'
                try:
                    process = subprocess.run([sys.executable, '-c', command], capture_output=True, text=True, timeout=PROBE_TIMEOUT)
                    payload = next(line[len('BASAL_HW='):] for line in process.stdout.splitlines() if line.startswith('BASAL_HW='))
                    values = json.loads(payload)
                    values['engines'] = frozenset(values['engines'])
                    if values['cuda_capability'] is not None:
                        values['cuda_capability'] = tuple(values['cuda_capability'])
                    self.detected_hw = hardware.Hardware(**values)
                except (StopIteration, ValueError, OSError, subprocess.TimeoutExpired) as exc:
                    raise RuntimeError('Hardware refresh failed; previous results have not been replaced') from exc
            hw = self.current_hardware()
            if self.cached is None or refresh:
                compatible = [key for key in BACKENDS if machine_reason(key, hw) is None]
                with ThreadPoolExecutor(max_workers=3) as pool:
                    results = dict(zip(compatible, pool.map(lambda key: probe(key, BACKENDS[key][1], hw), compatible)))
                self.cached = results
            variants = self.variants()
            entries = []
            for key, (name, packages, modes, docs) in BACKENDS.items():
                reason = machine_reason(key, hw)
                check = self.cached.get(key, {'versions': {}, 'missing': [], 'error': 'Refresh to check this backend'})
                status = ('unavailable' if reason else 'not_installed' if check['missing'] else
                          'needs_attention' if check['error'] else 'installed')
                models = []
                if not reason:
                    for v in variants:
                        allowed = matching_modes(key, v, hw)
                        if not allowed:
                            continue
                        try:
                            assessment = estimate(v, hw, allowed[0])
                            needed = assessment.est_memory_bytes
                        except ValueError:
                            needed = None
                        with self.fleet.lock:
                            runner = self.fleet.runners.get(v.id)
                        revision = (runner.options.get('revision') if runner is not None
                                    and runner.status()['state'] == 'ready'
                                    else self.settings.load_options(v.id).get('revision'))
                        models.append({'id': v.id, 'family': v.family, 'tier': v.tier, 'format': v.label,
                                       'modes': allowed, 'revision': v.base_revision,
                                       'downloaded': bool(catalog.is_downloaded(
                                           v, self.settings.models_dir(), revision=revision)),
                                       'estimated_memory_bytes': needed,
                                       'mode_estimates': {mode: estimate(v, hw, mode).est_memory_bytes for mode in allowed} if needed else {}})
                    models.sort(key=lambda m: (m['estimated_memory_bytes'] is None, m['estimated_memory_bytes'] or 0, m['id']))
                if reason:
                    reason_text = reason
                    reason_code, reason_params = _message_fields(reason)
                elif check['error']:
                    reason_text = check['error']
                    reason_code, reason_params = _message_fields(reason_text)
                elif check['missing']:
                    reason_text = BACKEND_TEMPLATES['backend_reason_missing'].format(
                        packages=', '.join(check['missing']))
                    reason_code, reason_params = _message_fields(reason_text)
                else:
                    reason_text = BACKEND_TEMPLATES['backend_reason_unverified']
                    reason_code, reason_params = _message_fields(reason_text)
                entries.append({'id': key, 'name': name, 'status': status,
                                'status_code': f'backend_status_{status}', 'status_params': {},
                                'compatible': reason is None, 'reason': reason_text,
                                'reason_code': reason_code, 'reason_params': reason_params,
                                'versions': check['versions'], 'modes': modes, 'models': models,
                                'setup': setup(key, hw), 'documentation': docs})
            entries.sort(key=lambda e: (not e['compatible'], e['name']))
            return {'environment': {'python': sys.executable, 'version': sys.version.split()[0], 'prefix': sys.prefix},
                    'hardware': hw.to_dict(), 'backends': entries, 'tests': self.records()}

    def current_hardware(self):
        current = self.hardware_fn()
        return dataclasses.replace(self.detected_hw, available_bytes=current.available_bytes) if self.detected_hw else current

    def records(self):
        return [{k: v for k, v in record.items() if k != 'snapshots'} | {
            'status_code': f"backend_test_status_{record['status']}", 'status_params': {},
            'stopped_models': [{'id': s['variant'].id, 'mode': s['mode'], 'error': s.get('restore_error')}
                               for s in record['snapshots']]} for record in self.tests.values()]

    def start(self, body):
        import secrets
        if not isinstance(body, dict):
            raise ValueError('Test request must be an object')
        backend, model_id, mode = body.get('backend'), body.get('model'), body.get('mode')
        entry = next((e for e in self.inventory()['backends'] if e['id'] == backend), None)
        if entry is None or entry['status'] != 'installed':
            raise ValueError('Select an installed, compatible backend with passing checks')
        match = next((m for m in entry['models'] if m['id'] == model_id), None)
        if match is None or not match['downloaded'] or mode not in match['modes']:
            raise ValueError('Select a compatible downloaded model and mode')
        variants = {v.id: v for v in self.variants()}
        variant, hw = variants[model_id], self.current_hardware()
        assessment = estimate(variant, hw, mode)
        local = catalog.is_downloaded(variant, self.settings.models_dir())
        if local is None:
            raise ValueError('Downloaded files are missing; no download will be attempted')
        # Pin the selected cache snapshot so a later main-branch refresh cannot change the test.
        revision = Path(local).name if len(Path(local).name) == 40 else variant.base_revision
        chosen = {'mode': mode, **({'revision': revision} if revision else {})}
        selected = body.get('unload', [])
        if not isinstance(selected, list) or any(not isinstance(x, dict) or set(x) != {'id', 'generation'} or
                                               not isinstance(x['id'], str) or not isinstance(x['generation'], str) for x in selected):
            raise ValueError('unload must contain model id and generation pairs')
        ids = [x['id'] for x in selected]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate unload selection')
        fleet = self.fleet
        with self.lock, fleet.lock:
            current = fleet.jobs.get('backend-test')
            if fleet.stopping or current and current.running or any(r.get('restoring') for r in self.tests.values()):
                raise RuntimeError('A backend test or restoration is already running')
            snapshots = []
            for item in selected:
                runner = fleet.ready(item['id'])
                if runner.generation != item['generation'] or fleet.leases.get(runner, 0):
                    raise RuntimeError('Unload selection changed or is busy; refresh and confirm again')
                if item['id'] not in variants or not runner.serve_snapshot:
                    raise RuntimeError('Cannot capture the active model configuration')
                active_options = dict(runner.options)
                if runner.loaded_path:
                    cached_revision = Path(runner.loaded_path).name
                    if len(cached_revision) == 40 and all(c in '0123456789abcdef' for c in cached_revision):
                        active_options['revision'] = cached_revision
                snapshots.append({'variant': variants[item['id']], 'runner': runner, 'mode': runner.mode,
                                  'options': active_options, 'media': runner.media_requested,
                                  'serve': dict(runner.serve_snapshot), 'generation': runner.generation,
                                  'estimated_memory_bytes': runner.est_memory_bytes})
            memory = fleet._memory()
            needed = assessment.est_memory_bytes
            freed = sum(s['runner'].est_memory_bytes or 0 for s in snapshots)
            if memory['committed_bytes'] - freed + needed > memory['budget_bytes'] - memory['reserve_bytes']:
                raise RuntimeError('Insufficient memory budget; select idle models to unload or choose a smaller test model')
            if hw.available_bytes is None or hw.available_bytes + freed < needed + memory['reserve_bytes']:
                raise RuntimeError('Available memory is unknown or insufficient for this test')
            token = secrets.token_hex(12)
            reservation = 'backend-test:' + token
            fleet.draining.update(ids)
            fleet.reserved[reservation] = needed
            record = {'id': token, 'backend': backend, 'model': model_id, 'mode': mode, 'revision': revision,
                      'status': 'starting', 'result': None, 'error': None, 'snapshots': [], 'restoring': False}
            self.tests[token] = record
            try:
                fleet.jobs.start('backend-test', model_id, f'{entry["name"]} · {mode}', self._test,
                                 record, assessment, chosen, snapshots, reservation)
            except BaseException:
                fleet.draining.difference_update(ids)
                fleet.reserved.pop(reservation, None)
                self.tests.pop(token, None)
                raise
            return {'id': token}

    def _test(self, job, record, assessment, chosen, snapshots, reservation):
        fleet, started = self.fleet, time.monotonic()
        ids = [s['variant'].id for s in snapshots]
        detached = []
        try:
            job.progress('checking selected models')
            for snapshot in snapshots:
                job.check()
                runner = snapshot['runner']
                response = httpx.get(runner.base_url + '/health', headers=runner._headers(), timeout=2)
                response.raise_for_status()
                health = response.json()
                if (health.get('status') != 'ok' or type(health.get('running')) is not int or
                        type(health.get('queued')) is not int or health['running'] or health['queued']):
                    raise RuntimeError('Selected model activity is busy or unknown; nothing was unloaded')
            job.check()
            with fleet.lock:
                if fleet.stopping:
                    raise Cancelled()
                for snapshot in snapshots:
                    runner = snapshot['runner']
                    if (fleet.runners.get(snapshot['variant'].id) is not runner or
                            runner.generation != snapshot['generation'] or fleet.leases.get(runner, 0)):
                        raise RuntimeError('Selected model changed; nothing was unloaded')
                for snapshot in snapshots:
                    runner = fleet.runners.pop(snapshot['variant'].id)
                    process, estimate_bytes = runner.detach()
                    fleet.retiring[runner] = estimate_bytes
                    detached.append((runner, process))
                record['snapshots'] = snapshots
            job.progress('unloading selected models')
            for runner, process in detached:
                runner._terminate(process)
                with fleet.lock:
                    fleet.retiring.pop(runner, None)
            job.check()
            with fleet.lock:
                hw = self.current_hardware()
                reserve = fleet._memory()['reserve_bytes']
                if hw.available_bytes is None or hw.available_bytes < assessment.est_memory_bytes + reserve:
                    raise RuntimeError('Not enough free memory after unloading; stopped models can be restored')
                runner = fleet.start_temporary('backend-test', assessment, job.log, chosen,
                                               local_only=True, reservation=reservation)
            job.progress('starting model')
            deadline = time.monotonic() + START_TIMEOUT
            while True:
                job.check()
                state = runner.status()
                if state['state'] == 'ready':
                    break
                if state['state'] in ('error', 'idle'):
                    raise RuntimeError(state['error'] or 'Test model stopped')
                if time.monotonic() >= deadline:
                    raise RuntimeError('Model startup timed out')
                time.sleep(.1)
            job.progress('testing inference')
            # Use a worker so cancellation can terminate the child while HTTP is outstanding.
            completed, reply = threading.Event(), {}
            def decide():
                try:
                    reply['response'] = runner.decide({'state': 'The sky is blue.', 'questions': {
                        'check': {'type': 'choice', 'instructions': 'Choose the color of the sky.',
                                  'criteria': {'blue': 'blue', 'red': 'red'}}}})
                except Exception as exc:
                    reply['error'] = exc
                finally:
                    completed.set()
            threading.Thread(target=decide, daemon=True).start()
            deadline = time.monotonic() + DECIDE_TIMEOUT
            while not completed.wait(.1):
                job.check()
                if time.monotonic() >= deadline:
                    raise RuntimeError('Inference timed out')
            job.check()
            if 'error' in reply:
                raise reply['error']
            response = reply['response']
            response.raise_for_status()
            answer = response.json().get('answers', {}).get('check', {})
            if not isinstance(answer, dict) or answer.get('choice') not in ('blue', 'red'):
                raise RuntimeError('Backend returned an invalid decision response')
            result = {k: record[k] for k in ('backend', 'model', 'mode', 'revision')}
            result.update(elapsed_seconds=round(time.monotonic() - started, 2), response_valid=True)
            record.update(status='passed', result=result)
            return result
        except BaseException as exc:
            record.update(status='cancelled' if isinstance(exc, Cancelled) else 'error', error=str(exc))
            raise
        finally:
            fleet.stop_temporary('backend-test')
            # Termination is idempotent, including an exception during the detach transition.
            for runner, process in detached:
                runner._terminate(process)
                with fleet.lock:
                    fleet.retiring.pop(runner, None)
            with fleet.lock:
                fleet.reserved.pop(reservation, None)
                fleet.draining.difference_update(ids)

    def restore(self, token):
        with self.lock:
            record = self.tests.get(token)
            if record is None:
                raise ValueError('Unknown test session')
            if record['restoring'] or (self.fleet.jobs.get('backend-test') and self.fleet.jobs.get('backend-test').running):
                raise RuntimeError('A test or restoration is running')
            record['restoring'] = True
            try:
                self.fleet.jobs.start('backend-restore', record['model'], 'Restore stopped models', self._restore, record)
            except BaseException:
                record['restoring'] = False
                raise
        return {'id': token}

    def _restore(self, job, record):
        fleet, outcomes = self.fleet, []
        try:
            for snapshot in list(record['snapshots']):
                job.check()
                model_id = snapshot['variant'].id
                runner = None
                try:
                    hw = self.current_hardware()
                    assessment = estimate(snapshot['variant'], hw, snapshot['mode'])
                    active_options = {**snapshot['options'], 'mode': snapshot['mode']}
                    needed = max(assessment.est_memory_bytes, snapshot['estimated_memory_bytes'] or 0)
                    if snapshot['mode'] in ('vllm', 'sglang'):
                        needed = max(needed, int(hw.memory_bytes * active_options.get('gpu_memory', options.DEFAULTS['gpu_memory'])))
                    assessment = dataclasses.replace(assessment, est_memory_bytes=needed)
                    options.validate(active_options, snapshot['mode'])
                    compatible = any(snapshot['mode'] in matching_modes(key, snapshot['variant'], hw)
                                     for key in BACKENDS if machine_reason(key, hw) is None)
                    if not compatible:
                        raise RuntimeError('The previous execution mode is no longer compatible')
                    if not catalog.is_downloaded(snapshot['variant'], self.settings.models_dir(),
                                                 revision=active_options.get('revision')):
                        raise RuntimeError('Original model files are missing')
                    with fleet.lock:
                        if model_id in fleet.runners:
                            raise RuntimeError('Model is already present; restoration will not replace it')
                        if hw.available_bytes is None or hw.available_bytes < assessment.est_memory_bytes + fleet._memory()['reserve_bytes']:
                            raise RuntimeError('Insufficient available memory for restoration')
                        fleet.load(assessment, media=snapshot['media'], options=active_options,
                                   snapshot=snapshot['serve'], local_only=True, remember=False)
                        runner = fleet.runners[model_id]
                    job.progress(f'restoring {model_id}')
                    deadline = time.monotonic() + START_TIMEOUT
                    while runner.status()['state'] != 'ready':
                        job.check()
                        state = runner.status()
                        if state['state'] in ('idle', 'error') or time.monotonic() >= deadline:
                            raise RuntimeError(state.get('error') or 'Restoration stopped or timed out')
                        time.sleep(.1)
                    record['snapshots'].remove(snapshot)
                    outcomes.append({'id': model_id, 'restored': True})
                except BaseException as exc:
                    snapshot['restore_error'] = str(exc) or 'Restoration cancelled'
                    if runner is not None:
                        with fleet.lock:
                            if fleet.runners.get(model_id) is runner:
                                # Do not leave a half-started restore holding resources.
                                fleet.unload(model_id, forget=False)
                    if isinstance(exc, Cancelled):
                        raise
                    outcomes.append({'id': model_id, 'restored': False, 'error': str(exc)})
            return {'models': outcomes}
        finally:
            record['restoring'] = False
