"""basal-ui: a local web app that detects the hardware, suggests basal models (size and format), downloads them,
starts basal-serve for each and runs decisions in a playground.

  basal-ui --port 7860      then open http://127.0.0.1:7860

Every loaded model runs in its own basal-serve subprocess on a free loopback port that only basal-ui knows;
/api/decide and the API server (basal_ui/gateway.py, one port for every model, with its API keys) forward to it, so a
crashed or unloaded model never takes the UI down.
"""
import argparse
import asyncio
import base64
import dataclasses
import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import catalog, hardware, settings
from . import options as load_options
from .fleet import Fleet
from .gateway import Gateway
from .history import History, replay_fields
from .stats import Stats

STATIC = Path(__file__).parent / "static"
EXAMPLES = Path(__file__).parent / "examples"
MEDIA = EXAMPLES / "media"  # the files the bundled examples name in media[i].file
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac"}
FAMILY_ORDER = {f"basal-{n}": i for i, n in enumerate(("2.0", "omni-1.1", "omni-1.0", "omni", "1.5", "1.0"))}


def _inline(m):
    """A bundled media item {"type", "file"} -> {"type", "data": data URL}; the file lives in basal_ui/examples/media/."""
    name = m["file"]
    if Path(name).name != name:
        raise ValueError(f"media file must be a bare name, got {name!r}")
    path = MEDIA / name
    return {"type": m["type"], "data": f"data:{MIME[path.suffix]};base64,{base64.b64encode(path.read_bytes()).decode()}"}


def examples():
    """The engine's bundled examples (basal-viz's list) and basal-ui's own requests (basal_ui/examples: every advanced
    question type, and media examples), in basal-viz's shape. Media examples name their files (media[i].file, inlined
    as data URLs into the request) and may carry "elements", the boxes of a choice question's options in an image:
    both stay outside the request, as "media_files" (aligned with request.media) and "elements"."""
    from basal.run import to_request
    from basal.viz import examples as engine_examples
    out = engine_examples()
    for path in sorted(EXAMPLES.glob("*.jsonl")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            item = json.loads(line)
            elements = item.pop("elements", None)
            ex = {"file": path.stem, "id": item.get("id") or f"{path.stem}-{n + 1}", "request": to_request(item)}
            if item.get("media"):
                ex["media_files"] = [m.get("file") for m in item["media"]]
                ex["request"]["media"] = [_inline(m) if "file" in m else m for m in item["media"]]
            if elements:
                ex["elements"] = elements
            out.append(ex)
    return out


def ui_version():
    """basal-ui's installed version ("0" in a source tree that was never installed)."""
    import importlib.metadata
    try:
        return importlib.metadata.version("basal-ui")
    except importlib.metadata.PackageNotFoundError:
        return "0"


def host_middleware(app, allowed_hosts):
    """Apply basal-ui's Host-header allowlist to an ASGI app."""
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    return app


def create_app(catalog_fn=catalog.list_variants, hardware_fn=hardware.detect, fleet=None,
               settings_mod=settings, allowed_hosts=("127.0.0.1", "localhost", "::1"), gateway=None, stats=None,
               history=None) -> FastAPI:
    stats = stats or Stats(settings_mod.stats_path())
    history = history or History(settings_mod.history_path())
    fleet = fleet or Fleet(models_dir_fn=settings_mod.models_dir, platform_fn=lambda: hardware_fn().platform,
                           settings_mod=settings_mod, hardware_fn=hardware_fn, stats=stats, history=history)
    gateway = gateway or Gateway(fleet, settings_mod, stats)
    allowed_hosts = tuple(allowed_hosts)
    from .inspect import Inspector, inspect_app, inspectable
    inspector = Inspector()

    def hw():
        return hardware_fn()

    def prepare(variant, media, options):
        """(assessment, the non-default load options) of a load; ValueError says why this machine cannot load it so:
        an unavailable mode, invalid options, or a variant the default mode cannot serve."""
        current = hw()
        assessment = catalog.assess(variant, current)
        merged = load_options.validate(options, assessment.mode)
        if media:
            modes = {m["mode"]: m for m in load_options.available_media_modes(assessment, current)}
            chosen = modes.get(merged["mode"] or "eager")
            if chosen is None:
                raise ValueError("--media on needs --mode eager or vllm (the media paths of basal-omni)")
            if not chosen["available"]:
                raise ValueError(chosen["reason"])
            assessment = dataclasses.replace(assessment, mode=merged["mode"] or "eager")
        elif merged["mode"]:
            mode = next(m for m in load_options.available_modes(variant, current, assessment)
                        if m["mode"] == merged["mode"])
            if not mode["available"]:
                raise ValueError(f"--mode {merged['mode']}: {mode['reason']}")
            assessment = dataclasses.replace(assessment, mode=merged["mode"])
        elif not assessment.runnable:
            raise ValueError(assessment.reason)
        load_options.check_combination(variant, current, merged, assessment.mode, media)
        return assessment, load_options.non_default(merged)

    auto_load_stop = threading.Event()
    auto_load_lock = threading.Lock()

    def _auto_load():
        try:
            variants, _ = catalog_fn(cache_dir=settings_mod.models_dir())
        except Exception as exc:  # the catalog is unavailable: nothing to load
            print(f"basal-ui: auto-load skipped: {exc}", file=sys.stderr)
            fleet.event("warn", f"auto-load skipped: {exc}")
            return
        for last in settings_mod.last_models():
            if auto_load_stop.is_set():
                return
            try:
                variant = next((v for v in variants if v.id == last["id"]), None)
                if variant is None:
                    raise ValueError("model is unknown")
                assessment, options = prepare(variant, last.get("media", False), last.get("options"))
                with auto_load_lock:
                    if not auto_load_stop.is_set():
                        fleet.load(assessment, media=last.get("media", False), options=options)
                        fleet.event("info", f"auto-load: {last['id']}")
            except Exception as exc:
                print(f"basal-ui: auto-load of {last['id']} skipped: {exc}", file=sys.stderr)
                fleet.event("warn", f"auto-load of {last['id']} skipped: {exc}")

    @asynccontextmanager
    async def lifespan(_app):
        auto_load_thread = None
        if settings_mod.load().get("auto_load") and settings_mod.last_models():
            auto_load_thread = threading.Thread(target=_auto_load, daemon=True)
            auto_load_thread.start()
        inspector.bind(asyncio.get_running_loop())  # the explainer's batching loop runs on this one
        await asyncio.to_thread(gateway.start)
        yield
        with auto_load_lock:
            auto_load_stop.set()
        if auto_load_thread is not None:
            while auto_load_thread.is_alive():
                auto_load_thread.join(timeout=5)
        while not gateway.stop():
            fleet.event("error", "API server shutdown is still draining; waiting before tearing down shared state")
            await asyncio.sleep(1)
        inspector.stop()
        fleet.shutdown()
        stats.close()
        settings_mod.remove_keys_file()

    app = host_middleware(FastAPI(title="basal-ui", lifespan=lifespan), allowed_hosts)
    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/viz", inspect_app(inspector.explainer, examples))  # basal-viz for the Inspect view


    @app.middleware("http")
    async def refuse_cross_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and origin and "*" not in allowed_hosts:
            try:
                parsed = urlsplit(origin)
                origin_host = parsed.hostname
                origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
                request_port = request.url.port or (443 if request.url.scheme == "https" else 80)
            except ValueError:
                parsed, origin_host = None, None
                origin_port = request_port = None
            if (origin_host is None or origin_host.lower() not in {host.lower() for host in allowed_hosts}
                    or parsed.scheme != request.url.scheme or parsed.path or parsed.query or parsed.fragment
                    or parsed.username is not None or parsed.password is not None or origin_port != request_port):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def revalidate_static(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):  # like index.html: an updated basal-ui must not run cached scripts
            response.headers["Cache-Control"] = "no-cache"  # the ETag keeps an unchanged file a 304
        return response

    @app.get("/")
    def index():
        # revalidate on every load: a cached copy from an older basal-ui would talk to the new API
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    def find_variant(model_id):
        variants, _ = catalog_fn(cache_dir=settings_mod.models_dir())
        variant = next((v for v in variants if v.id == model_id), None)
        if variant is None:
            raise HTTPException(400, f"unknown model {model_id!r}")
        return variant

    def full_status():
        return {**fleet.status(), "serving": gateway.status(), "inspect": inspector.state()}

    def serve_response():
        config = settings_mod.serve_settings()
        available = None
        hint = None
        try:
            from basal.mcp_server import available as mcp_available
            hint = mcp_available()
            available = hint is None
        except ImportError as exc:
            hint = str(exc)
        usage = gateway.key_usage()
        lan = fleet.lan_ip() if config["host"] == "0.0.0.0" else None
        return {"settings": config, "auto_load": settings_mod.load().get("auto_load", False),
                "keys": [{**{k: v for k, v in key.items() if k != "sha256"},
                          "last_used": usage.get(key["name"])} for key in settings_mod.keys()],
                "mcp_available": available, "mcp_hint": hint, "lan_ip": lan}

    @app.get("/api/serve")
    def get_serve():
        return serve_response()

    @app.put("/api/serve")
    def put_serve(body: dict = Body(...)):
        try:
            if "auto_load" in body and not isinstance(body["auto_load"], bool):
                raise ValueError("auto_load must be a boolean")
            patch = {k: v for k, v in body.items() if k != "auto_load"}
            if patch:
                settings_mod.update_serve(patch)
            if "auto_load" in body:
                settings_mod.set_auto_load(body["auto_load"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return serve_response()

    @app.post("/api/serve/restart")
    def restart_serve():
        fleet.event("info", "API server restart requested")
        return gateway.restart()

    @app.post("/api/serve/keys", status_code=201)
    def post_key(body: dict = Body(...)):
        try:
            record, secret = settings_mod.create_key(body.get("name"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {**record, "key": secret}

    @app.delete("/api/serve/keys/{name}")
    def remove_key(name: str):
        try:
            settings_mod.delete_key(name)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"deleted": name}

    @app.get("/api/memory")
    def get_memory():
        current = hw()
        return {"settings": settings_mod.memory_settings(), "hardware_budget_bytes": current.memory_bytes,
                "available_bytes": current.available_bytes}

    @app.put("/api/memory")
    def put_memory(body: dict = Body(...)):
        try:
            settings_mod.update_memory(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return get_memory()

    @app.post("/api/restart", status_code=202)
    def restart_model(body: dict = Body(...)):
        model_id = body.get("id")
        current = next((m for m in fleet.status()["models"] if m["id"] == model_id), None)
        if current is None:
            raise HTTPException(404, f"model {model_id!r} is not loaded")
        if current["state"] in ("downloading", "starting"):
            raise HTTPException(409, "model is still loading")
        variants, _ = catalog_fn(cache_dir=settings_mod.models_dir())
        variant = next((v for v in variants if v.id == model_id), None)
        if variant is None:
            raise HTTPException(400, "loaded model is no longer available")
        try:
            options = body.get("options")
            assessment, options = prepare(variant, current.get("media_requested", False),
                                          current.get("options") if options is None else options)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            fleet.restart(assessment, media=current.get("media_requested", False), options=options)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return full_status()

    from .backends import Manager as BackendManager
    backend_manager = BackendManager(fleet, lambda: catalog_fn(cache_dir=settings_mod.models_dir())[0],
                                     hardware_fn, settings_mod)
    app.state.backends = backend_manager

    @app.get("/api/backends")
    def get_backends(refresh: bool = False):
        try:
            return backend_manager.inventory(refresh)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/backends/tests")
    def get_backend_tests():
        with backend_manager.lock:
            return {"tests": backend_manager.records()}

    @app.post("/api/backends/tests", status_code=202)
    def start_backend_test(body: dict = Body(...)):
        try:
            return backend_manager.start(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (RuntimeError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/backends/tests/{test_id}")
    def cancel_backend_test(test_id: str):
        with backend_manager.lock:
            record = backend_manager.tests.get(test_id)
            if record is None:
                raise HTTPException(404, "Unknown test session")
            if record['status'] == 'starting':
                fleet.jobs.cancel('backend-test')
            if record['restoring']:
                fleet.jobs.cancel('backend-restore')
        return {"cancelled": test_id}

    @app.post("/api/backends/tests/{test_id}/restore", status_code=202)
    def restore_backend_models(test_id: str):
        try:
            return backend_manager.restore(test_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/hardware")
    def get_hardware():
        return hw().to_dict()

    def settings_response():
        from huggingface_hub.constants import HF_HUB_CACHE
        models = Path(settings_mod.models_dir()).resolve()
        default = Path(HF_HUB_CACHE).resolve()
        return {"models_dir": str(models), "default_models_dir": str(default), "is_default": models == default,
                "check_updates": settings_mod.check_updates()}

    @app.get("/api/settings")
    def get_settings():
        return settings_response()

    @app.put("/api/settings")
    def put_settings(body: dict = Body(...)):
        models_dir = body.get("models_dir")
        check_updates = body.get("check_updates")
        if "check_updates" in body and not isinstance(check_updates, bool):
            raise HTTPException(400, "check_updates must be a boolean")
        if models_dir is not None and not isinstance(models_dir, str):
            raise HTTPException(400, "models_dir must be a string or null")
        try:
            if "models_dir" in body:
                fleet.while_idle(lambda: settings_mod.set_models_dir(models_dir))
            if "check_updates" in body:
                settings_mod.set_check_updates(check_updates)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return settings_response()

    @app.get("/api/models")
    def get_models(refresh: bool = False):
        cache_dir = settings_mod.models_dir()
        current_hw = hw()
        variants, source = catalog_fn(refresh=refresh, cache_dir=cache_dir)
        assessed = [catalog.assess(v, current_hw) for v in variants]
        assessed.sort(key=lambda a: (FAMILY_ORDER.get(a.variant.family, 99),
                                     catalog.TIER_ORDER.get(a.variant.tier, 99), a.variant.port, a.variant.label))
        fields = ("id", "repo", "author", "community", "base_repo", "family", "tier", "port", "label", "gguf_file",
                  "weights_bytes", "experimental", "recurrent")
        on_disk = {a.variant.id: catalog.downloaded_bytes(a.variant, cache_dir) for a in assessed}
        saved = settings_mod.load().get("load_options")  # once, not load_options() per row
        revisions = {k: v.get("revision") for k, v in saved.items() if isinstance(v, dict)} if isinstance(
            saved, dict) else {}
        return {"source": source, "suggestions": catalog.suggest(assessed, current_hw),
                "models_dir": str(Path(cache_dir).resolve()), "models": [
            {**{k: getattr(a.variant, k) for k in fields}, "runnable": a.runnable, "reason": a.reason,
             "reason_code": catalog.message_fields(a.reason, "")[0],
             "reason_params": catalog.message_fields(a.reason, "")[1],
             "mode": a.mode, "est_memory_bytes": a.est_memory_bytes, "fit": a.fit, "fit_now": a.fit_now,
             "media": list(a.variant.media), "media_mode": a.media_mode, "media_reason": a.media_reason,
             "media_reason_code": catalog.message_fields(a.media_reason, "")[0],
             "media_reason_params": catalog.message_fields(a.media_reason, "")[1],
             "downloaded": on_disk[a.variant.id] is not None, "downloaded_bytes": on_disk[a.variant.id],
             "revision": revisions.get(a.variant.id), "inspect_reason": inspectable(a.variant)}
            for a in assessed]}

    @app.get("/api/presets")
    def get_presets():
        return {"load": settings_mod.presets().get("load", {}),
                "request": settings_mod.presets().get("request", {})}

    @app.put("/api/presets")
    def put_preset(body: dict = Body(...)):
        kind, name, value = body.get("kind"), body.get("name"), body.get("value")
        try:
            if kind == "load" and value is not None:
                value = load_options.non_default(load_options.validate(value))
            elif kind == "request" and value is not None:
                if not isinstance(value, dict) or not isinstance(value.get("questions"), dict) or not value["questions"]:
                    raise ValueError("request preset must contain a non-empty questions object")
                value = {k: v for k, v in value.items() if k != "model"}
            settings_mod.set_preset(kind, name, value)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return get_presets()

    @app.get("/api/models/card")
    def get_model_card(id: str):
        variant = find_variant(id)
        cache_dir = settings_mod.models_dir()
        local_dir = catalog.is_downloaded(variant, cache_dir, revision=effective_revision(variant))
        assessment = catalog.assess(variant, hw())
        loaded = next((m.get("entry") for m in fleet.status()["models"] if m["id"] == variant.id), None)
        from huggingface_hub import scan_cache_dir
        from huggingface_hub.errors import CacheNotFound
        try:
            info = scan_cache_dir(cache_dir)
            revisions = sorted({s.commit_hash for repo in info.repos if repo.repo_id == variant.repo
                                for s in repo.revisions})
        except (CacheNotFound, OSError, ValueError):
            revisions = []
        revision = served_revision(variant) or "main"
        readme = None
        if local_dir and (Path(local_dir) / "README.md").is_file():
            readme = (Path(local_dir) / "README.md").read_text(encoding="utf-8", errors="replace")[:20000]
        else:
            try:
                response = httpx.get(f"https://huggingface.co/{variant.repo}/raw/{revision}/README.md", timeout=5)
                if response.is_success:
                    readme = response.text[:20000]
            except httpx.HTTPError:
                pass

        def metadata(name):
            """A JSON object of the repository (basal.json, config.json): from the snapshot, else from the Hub."""
            try:
                if local_dir:
                    data = json.loads((Path(local_dir) / name).read_text(encoding="utf-8"))
                else:
                    response = httpx.get(f"https://huggingface.co/{variant.repo}/raw/{revision}/{name}", timeout=5)
                    data = response.json() if response.is_success else None
            except (OSError, ValueError, httpx.HTTPError):
                return None
            return data if isinstance(data, dict) else None

        basal_json, config_json = metadata("basal.json"), metadata("config.json")
        shipped_calibration = None
        if local_dir:
            from . import calibration
            try:
                path, _ = resolve_shipped_calibration(variant, local_dir, assessment)
                shipped_calibration = calibration.summary(path) if path.is_file() else None
            except (OSError, ValueError, KeyError) as exc:
                shipped_calibration = {"error": str(exc)}
        return {"id": variant.id, "repo": variant.repo, "url": f"https://huggingface.co/{variant.repo}",
                "author": variant.author, "community": variant.community, "base_repo": variant.base_repo,
                "base_revision": variant.base_revision,
                "family": variant.family, "tier": variant.tier, "port": variant.port, "label": variant.label,
                "size_bytes": variant.weights_bytes, "media": list(variant.media), "downloaded": local_dir is not None,
                "local_dir": str(local_dir) if local_dir else None, "revisions": revisions, "basal_json": basal_json,
                "quantisation": catalog.quantisation(variant, basal_json, config_json),
                "shipped_calibration": shipped_calibration, "readme": readme,
                "assessment": {"mode": assessment.mode, "runnable": assessment.runnable,
                               "reason": assessment.reason,
                               "reason_code": catalog.message_fields(assessment.reason, "")[0],
                               "reason_params": catalog.message_fields(assessment.reason, "")[1],
                               "est_memory_bytes": assessment.est_memory_bytes,
                               "fit": assessment.fit, "fit_now": assessment.fit_now},
                "modes": load_options.available_modes(variant, hw(), assessment), "loaded": loaded}

    @app.get("/api/updates")
    def get_updates():
        from packaging.version import InvalidVersion, Version

        from . import engine, updates

        def newer(latest, installed):
            try:
                return latest is not None and Version(latest) > Version(installed)
            except InvalidVersion:
                return False

        enabled = settings_mod.check_updates()
        latest, error = updates.latest(updates.UI_REPO) if enabled else (None, None)
        installed = ui_version()
        engine_status = engine.status()
        engine_latest, engine_error = updates.latest(updates.ENGINE_REPO) if enabled else (None, None)
        variants, source = catalog_fn(cache_dir=settings_mod.models_dir())
        # only the hub catalog is the whole catalog: the cache fallback would seed (or report) a partial list
        new_models = settings_mod.update_seen_ids(v.id for v in variants) if source == "hub" else []
        return {"enabled": enabled, "installed": installed, "latest": latest,
                "url": f"https://github.com/{updates.UI_REPO}/releases/tag/v{latest}" if latest else None,
                "newer": newer(latest, installed), "error": error, "new_models": new_models, "catalog": source,
                "engine": {**engine_status, "latest": engine_latest, "error": engine_error,
                           "newer": newer(engine_latest, engine_status["version"]),
                           "latest_supported": engine_latest is not None and engine.supported(engine_latest),
                           "url": (f"https://github.com/{updates.ENGINE_REPO}/releases/tag/v{engine_latest}"
                                   if engine_latest else None)}}

    @app.post("/api/updates/seen")
    def mark_updates_seen():
        variants, source = catalog_fn(cache_dir=settings_mod.models_dir())
        if source == "hub":
            settings_mod.mark_models_seen(v.id for v in variants)
        return get_updates()

    @app.get("/api/models/options")
    def get_model_options(id: str):
        variant = find_variant(id)
        current = hw()
        assessment = catalog.assess(variant, current)
        saved = settings_mod.load_options(variant.id)
        try:
            merged = load_options.validate(saved, assessment.mode)
        except ValueError:  # options saved by another basal-ui version
            merged = dict(load_options.DEFAULTS)
        family_ok = variant.family == "basal-2.0" and variant.tier == "4.5B"
        packed_reason = load_options.mlx_packed_unavailable(variant, current)
        packed_code, packed_params = load_options.option_message_fields(packed_reason)
        think_reason = None if family_ok else load_options.THINK_REASON
        think_code, think_params = load_options.option_message_fields(think_reason)
        adapter_reason = load_options.adapter_unavailable()
        adapter_code, adapter_params = load_options.option_message_fields(adapter_reason)
        return {"modes": load_options.available_modes(variant, current, assessment), "options": merged,
                "saved": bool(saved), "defaults": load_options.DEFAULTS,
                "media_modes": load_options.available_media_modes(assessment, current),
                "mlx_packed_reason": packed_reason, "mlx_packed_reason_code": packed_code,
                "mlx_packed_reason_params": packed_params,
                "adapter_reason": adapter_reason, "adapter_reason_code": adapter_code,
                "adapter_reason_params": adapter_params,
                "think": {"model": family_ok, "vllm": "vllm" in current.engines, "reason": think_reason,
                          "reason_code": think_code, "reason_params": think_params}}

    @app.put("/api/models/options")
    def put_model_options(body: dict = Body(...)):
        variant = find_variant(body.get("id"))
        try:
            current = hw()
            default_mode = catalog.assess(variant, current).mode
            merged = load_options.validate(body.get("options"), default_mode)
            load_options.check_combination(variant, current, merged, default_mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        settings_mod.set_load_options(variant.id, load_options.non_default(merged))
        return get_model_options(variant.id)

    @app.post("/api/models/delete")
    def delete_model(body: dict = Body(...)):
        variant = find_variant(body.get("id"))
        try:
            freed = fleet.while_not_busy(variant.repo, lambda: catalog.delete(variant, settings_mod.models_dir()))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"freed_bytes": freed}

    @app.post("/api/download", status_code=202)
    def download(body: dict = Body(...)):
        variant = find_variant(body.get("id"))
        if not fleet.download(variant):
            raise HTTPException(409, "already on disk")
        return full_status()

    @app.post("/api/load", status_code=202)
    def load(body: dict = Body(...)):
        variant = find_variant(body.get("id"))
        media = body.get("media", False)
        if not isinstance(media, bool):
            raise HTTPException(400, "media must be a boolean")
        try:
            if "options" in body:
                options = body["options"]
            else:  # remembered; their engine mode applies with media only when it is a media mode
                options = settings_mod.load_options(variant.id)
                if media and options.get("mode") not in (None, "eager", "vllm"):
                    options = {k: v for k, v in options.items() if k != "mode"}
            assessment, options = prepare(variant, media, options)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            fleet.load(assessment, media=media, options=options)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return full_status()

    @app.post("/api/unload")
    def unload(body: dict | None = Body(None)):
        model_id = (body or {}).get("id")
        try:
            fleet.unload(model_id)
        except KeyError as exc:
            raise HTTPException(404, f"model {model_id!r} is not loaded") from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return full_status()

    @app.get("/api/status")
    def status():
        return full_status()

    @app.get("/api/examples")
    def get_examples():
        return examples()

    @app.post("/api/decide")
    def decide(body: dict = Body(...)):  # sync: runs in the threadpool, a slow decision does not block the UI
        body = dict(body)
        model_id = body.pop("model", None)
        if not isinstance(model_id, str) or not model_id:
            raise HTTPException(400, f"model is required; loaded: {fleet.loaded_ids()}")
        try:
            r = fleet.decide(model_id, body, source="playground")
        except KeyError as exc:
            raise HTTPException(404, f"model {model_id!r} is not loaded") from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"basal-serve: {exc}") from exc
        return Response(r.content, status_code=r.status_code,
                        headers={k: r.headers[k] for k in ("x-request-id", "x-typesafe-request-id") if k in r.headers},
                        media_type=r.headers.get("content-type", "application/json"))

    @app.get("/api/stats")
    def get_stats():
        return stats.snapshot()

    @app.delete("/api/stats")
    def reset_stats():
        stats.reset()
        return {"reset": "all"}

    @app.delete("/api/stats/{model_id:path}")
    def reset_model_stats(model_id: str):
        stats.reset(model_id)
        return {"reset": model_id}

    @app.post("/api/benchmark", status_code=202)
    def post_benchmark(body: dict = Body(...)):
        """kind benchmark (the preset), loadtest, determinism (a loaded model) or modes (a downloaded model, loaded
        in basal-ui's process mode by mode); one measurement at a time."""
        from . import bench

        model_id, kind = body.get("id"), body.get("kind", "benchmark")
        if kind not in ("benchmark", "loadtest", "determinism", "modes"):
            raise HTTPException(400, "kind must be benchmark, loadtest, determinism or modes")
        if kind == "modes":
            return start_modes(model_id, body)
        try:
            label, paths = bench.resolve_set(body.get("set", "bundled"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            runner = fleet.ready(model_id)
            if kind == "benchmark":
                return fleet.start_model_job("benchmark", model_id, label, bench.benchmark_job, fleet, model_id, paths,
                                        label)
            try:
                params = bench.loadtest_params(body.get("params") or {}, kind,
                                               (runner.models_entry or {}).get("early_exit") or [])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if params["synthetic"]:
                label = f"synthetic {params['synthetic']} tokens"
            return fleet.start_model_job("benchmark", model_id, f"{kind}: {label}", bench.loadtest_job, fleet, model_id,
                                    label, paths, params)
        except KeyError as exc:
            raise HTTPException(404, f"model {model_id!r} is not loaded") from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    def modes_context(variant):
        """(local dir, assessment, merged load options, the modes a comparison may run) of a downloaded variant."""
        from . import bench

        local_dir = downloaded(variant)
        current = hw()
        assessment = catalog.assess(variant, current)
        options = load_options.validate(settings_mod.load_options(variant.id), assessment.mode)
        return local_dir, assessment, options, bench.comparison_modes(
            load_options.available_modes(variant, current, assessment))

    def start_modes(model_id, body):
        from . import bench

        variant = find_variant(model_id)
        local_dir, assessment, options, selectable = modes_context(variant)
        modes, n, lat_n = body.get("modes"), body.get("n", 100), body.get("lat_n", 30)
        if not isinstance(modes, list) or not modes or len(set(modes)) != len(modes):
            raise HTTPException(400, "modes must be a list of distinct modes")
        unknown = [m for m in modes if m not in selectable]
        if unknown:
            raise HTTPException(400, f"{unknown[0]} cannot run here; selectable: {selectable}")
        for key, value, lo in (("n", n, 10), ("lat_n", lat_n, 5)):
            if isinstance(value, bool) or not isinstance(value, int) or value < lo:
                raise HTTPException(400, f"{key} must be an integer >= {lo}")
        if fleet.jobs.get("benchmark") is not None and fleet.jobs.get("benchmark").running:
            raise HTTPException(409, "a benchmark job is already running")
        try:
            fleet.reserve("modes", bench.comparison_memory(modes, assessment.est_memory_bytes))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        try:
            return fleet.jobs.start("benchmark", variant.id, f"modes: {', '.join(modes)}", bench.modes_job, fleet,
                                    variant, local_dir, modes, n, lat_n, options)
        except RuntimeError as exc:
            fleet.release("modes")
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/modes")
    def get_comparison_modes(id: str):
        variant = find_variant(id)
        return {"modes": modes_context(variant)[3]}

    @app.delete("/api/benchmark")
    def cancel_benchmark():
        return fleet.jobs.cancel("benchmark")

    def served_revision(variant):
        """The revision the model is loaded at (load option), None for the default branch."""
        return settings_mod.load_options(variant.id).get("revision")

    def effective_revision(variant):
        with fleet.lock:
            runner = fleet.runners.get(variant.id)
        return (runner.options.get("revision") if runner is not None and runner.status()["state"] == "ready"
                else served_revision(variant))

    def resolve_shipped_calibration(variant, local_dir, assessment):
        from . import calibration
        from basal.prompt import model_layout
        from basal.server import MODES

        with fleet.lock:
            runner = fleet.runners.get(variant.id)
        active = runner is not None and runner.status()["state"] == "ready"
        saved = settings_mod.load_options(variant.id)
        options = load_options.validate(runner.options if active else saved, assessment.mode)
        mode = runner.mode if active else options.get("mode") or assessment.mode or "eager"
        quant = options.get("quant") or MODES[mode][1]
        preset, adapter_dir = options.get("vllm_preset"), options.get("adapter")
        layout = options.get("layout") or model_layout(local_dir)
        path = calibration.shipped_file(local_dir, mode, preset, quant, layout, adapter_dir)
        context = {"revision": (runner.options.get("revision") if active else options.get("revision"))
                   or variant.base_revision or "default", "mode": mode, "quant": quant,
                   "preset": preset if mode == "vllm" else None, "layout": layout,
                   "adapter": adapter_dir, "active": active}
        return path, context

    def downloaded(variant):
        local_dir = catalog.is_downloaded(variant, settings_mod.models_dir(), revision=served_revision(variant))
        if not local_dir:
            raise HTTPException(400, f"download {variant.id} first")
        return Path(local_dir)

    @app.post("/api/calibrate", status_code=202)
    def post_calibrate(body: dict = Body(...)):
        from . import calibration

        variant = find_variant(body.get("id"))
        local_dir = downloaded(variant)
        source = body.get("input")
        if not isinstance(source, str) or not source.strip():
            raise HTTPException(400, "input must be the path of a labelled JSONL file (docs/CALIBRATION.md)")
        source = Path(source).expanduser()
        try:
            with source.open("rb") as f:
                f.read(1)
        except OSError as exc:
            raise HTTPException(400, f"cannot read {source}: {exc.strerror or exc}") from exc
        advanced = body.get("advanced") or {}
        if not isinstance(advanced, dict):
            raise HTTPException(400, "advanced must be an object")
        allowed = {"strict", "reference", "min_per_type", "min_blend", "min_family", "min_per_set", "concurrency",
                   "compare", "save_rows", "report_only"}
        if set(advanced) - allowed:
            raise HTTPException(400, f"unknown advanced calibration option: {min(set(advanced) - allowed)}")
        for key in ("strict", "save_rows", "report_only"):
            if key in advanced and not isinstance(advanced[key], bool):
                raise HTTPException(400, f"{key} must be a boolean")
        for key, minimum in (("min_per_type", 1), ("min_blend", 0), ("min_family", 1), ("min_per_set", 1),
                             ("concurrency", 1)):
            if key in advanced and (type(advanced[key]) is not int or advanced[key] < minimum or
                                    (key == "concurrency" and advanced[key] > 256)):
                raise HTTPException(400, f"{key} must be an integer >= {minimum}")
        reference = advanced.get("reference")
        if reference is not None and not isinstance(reference, str):
            raise HTTPException(400, "reference must be basal-1.5, a path, or null")
        compare = advanced.get("compare", "shipped")
        if not isinstance(compare, str) or compare not in ("shipped", "none") and not compare.strip():
            raise HTTPException(400, "compare must be shipped, none, or a calibration path")
        for key, value, names in (("reference", reference, ("basal-1.5",)), ("compare", compare, ("shipped", "none"))):
            if value and value not in names:
                path = Path(value).expanduser()
                if not path.is_file():
                    raise HTTPException(400, f"{key}: {value} is not a readable file")
                advanced[key] = str(path)
        from_rows = body.get("from_rows", False)
        if not isinstance(from_rows, bool):
            raise HTTPException(400, "from_rows must be a boolean")
        advanced["from_rows"] = from_rows
        units = []
        if not from_rows:
            try:
                _, units = calibration.labelled(source)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        in_process = bool(units)
        targets = body.get("targets") or list(calibration.TARGETS)
        try:
            if not isinstance(targets, list) or not targets or not all(0 < float(t) < 1 for t in targets):
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(400, "targets must be a list of max_error values between 0 and 1") from None
        if from_rows:  # a refit of saved rows runs no engine: no load options, no memory check
            assessment, options = catalog.assess(variant, hw()), {}
        else:
            try:
                assessment, options = prepare(variant, False, settings_mod.load_options(variant.id))
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        out = settings_mod.calibrations_dir(variant.id) / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
        label = f"{variant.id} on {source.name}" + (" (engine in process: task questions)" if in_process else "")
        reserve = in_process and not from_rows
        try:
            if reserve:
                fleet.reserve("calibrate", assessment.est_memory_bytes)
            elif not from_rows:
                with fleet.lock:
                    fleet.check_budget(assessment.est_memory_bytes)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        engine_args = None
        if in_process and not from_rows:
            engine_args = calibration.engine_flags(catalog.serve_args(
                variant, local_dir, assessment.mode, platform=hw().platform) + load_options.args(options))
        advanced["reserve"] = reserve
        try:
            return fleet.jobs.start("calibrate", variant.id, label, calibration.run_calibration, fleet, assessment,
                                    local_dir, source, out, [str(float(t)) for t in targets], options, engine_args,
                                    advanced)
        except RuntimeError as exc:
            if reserve:
                fleet.release("calibrate")
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/calibrate")
    def cancel_calibrate():
        state = fleet.jobs.cancel("calibrate")
        fleet.stop_temporary("calibrate")  # basal-calibrate's requests then fail and the job ends
        return state

    def calibrations_response(variant):
        from . import calibration

        revision = effective_revision(variant)
        local_dir = catalog.is_downloaded(variant, settings_mod.models_dir(), revision=revision)
        shipped = None
        if local_dir:
            try:
                assessment = catalog.assess(variant, hw())
                path, context = resolve_shipped_calibration(variant, local_dir, assessment)
                shipped = {"file": str(path), "context": context}
                try:
                    shipped.update(calibration.summary(path))
                except (OSError, ValueError) as exc:
                    shipped["error"] = str(exc)
            except (OSError, ValueError, KeyError) as exc:
                shipped = {"error": str(exc)}
        return {"selected": settings_mod.calibration_selection().get(variant.id),
                "files": calibration.listing(variant.id, settings_mod, local_dir), "shipped": shipped}

    @app.post("/api/calibrations/import")
    def import_calibration(body: dict = Body(...)):
        """A calibration file fitted elsewhere (basal-calibrate on another machine, a colleague's file): copied into the
        model's calibrations, then selectable; a file of another model is listed with basal-serve's warning."""
        from . import calibration

        variant = find_variant(body.get("id"))
        downloaded(variant)  # the listing checks the file against the model's directory
        source = body.get("path")
        if not isinstance(source, str) or not source.strip():
            raise HTTPException(400, "path must name a calibration file (JSON)")
        try:
            target = calibration.import_file(variant.id, settings_mod, source)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        fleet.event("info", f"calibration imported for {variant.id}: {source} -> {target}")
        return {"file": str(target), **calibrations_response(variant)}

    @app.get("/api/calibrations")
    def get_calibrations(id: str):
        return calibrations_response(find_variant(id))

    @app.post("/api/calibrations/select")
    def select_calibration(body: dict = Body(...)):
        from . import calibration

        variant = find_variant(body.get("id"))
        path = body.get("file")
        if path is not None and path not in {f["file"] for f in calibration.listing(variant.id, settings_mod)}:
            raise HTTPException(400, f"{path!r} is not one of the calibrations of {variant.id} in basal-ui")
        settings_mod.select_calibration(variant.id, path)
        fleet.event("info", f"calibration for {variant.id}: {path or 'shipped'} (applies at the next start)")
        return calibrations_response(variant)

    @app.get("/api/calibrations/report")
    def get_calibration_report(id: str, file: str):
        from . import calibration

        variant = find_variant(id)
        if file not in {f["file"] for f in calibration.listing(variant.id, settings_mod)}:
            raise HTTPException(404, f"{file!r} is not one of the calibrations of {variant.id} in basal-ui")
        try:
            return {"report": Path(file).with_suffix(".report.txt").read_text(encoding="utf-8")}
        except OSError as exc:
            raise HTTPException(404, f"no report for {file}") from exc

    @app.post("/api/inspect", status_code=202)
    def post_inspect(body: dict = Body(...)):
        from . import calibration

        variant = find_variant(body.get("id"))
        reason = inspectable(variant)
        if reason:
            raise HTTPException(400, reason)
        local_dir = downloaded(variant)
        cal = body.get("calibration")
        if cal is not None and cal not in {f["file"] for f in calibration.listing(variant.id, settings_mod)}:
            raise HTTPException(400, f"{cal!r} is not one of the calibrations of {variant.id} in basal-ui")
        orders = body.get("orders")
        if orders not in (None, 1, 2):
            raise HTTPException(400, "orders must be 1, 2 or null (the model's own)")
        job = fleet.jobs.get("inspect")
        if job is not None and job.running:
            raise HTTPException(409, "the inspector is loading a model")
        inspector.stop()
        current = hw()
        try:
            fleet.reserve("inspect", catalog.assess(variant, current).est_memory_bytes)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

        def start(job):
            try:
                return inspector.start(job, variant, local_dir, current, cal, orders,
                                       on_exit=lambda: fleet.release("inspect"))
            except BaseException:
                fleet.release("inspect")
                raise

        try:
            return fleet.jobs.start("inspect", variant.id, variant.id, start)
        except RuntimeError as exc:
            fleet.release("inspect")
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/inspect")
    def stop_inspect():
        fleet.jobs.cancel("inspect")
        inspector.stop()
        fleet.event("info", "inspector stopped")
        return inspector.state()

    @app.get("/api/inspect")
    def get_inspect():
        return inspector.state()

    @app.post("/api/batch", status_code=202)
    def post_batch(body: dict = Body(...)):
        from . import batch

        model_id = body.get("id")
        source = body.get("input")
        if not isinstance(source, str) or not source.strip():
            raise HTTPException(400, "input must be the path of a JSONL file (docs/SERVING.md#inference-on-a-jsonl-file)")
        source = Path(source).expanduser()
        try:
            lines = [line for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, UnicodeDecodeError) as exc:
            raise HTTPException(400, f"cannot read {source}: {getattr(exc, 'strerror', None) or exc}") from exc
        for n, line in enumerate(lines, 1):
            try:
                item = json.loads(line)
            except ValueError:
                item = None
            if not isinstance(item, dict) or not ("questions" in item or "options" in item):
                raise HTTPException(400, f"{source.name}: line {n} is neither a simple item (state, question, "
                                         "options) nor a /v1/systemone request")
        if not lines:
            raise HTTPException(400, f"{source} has no lines")
        out = Path(body["output"]).expanduser() if body.get("output") else batch.default_output(source)
        if out.resolve() == source.resolve():
            raise HTTPException(400, "output must not be the input file")
        if not out.parent.is_dir() or not os.access(out.parent, os.W_OK):
            raise HTTPException(400, f"cannot write to {out.parent}")
        concurrency = body.get("concurrency", 16)
        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 256:
            raise HTTPException(400, "concurrency must be an integer between 1 and 256")
        try:
            runner = fleet.ready(model_id)
        except KeyError as exc:
            raise HTTPException(404, f"model {model_id!r} is not loaded") from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        early_exit = body.get("early_exit")
        levels = (runner.models_entry or {}).get("early_exit") or []
        if early_exit is not None and early_exit not in levels:
            raise HTTPException(400, f"early_exit must be one of {levels} for {model_id}" if levels else
                                f"{model_id} has no early exits (basal-serve --mode fast-exit)")
        try:
            return fleet.start_model_job("batch", model_id, f"{model_id} on {source.name}", batch.run_batch, fleet,
                                    model_id, source, out, concurrency, early_exit)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/batch")
    def cancel_batch():
        return fleet.jobs.cancel("batch")

    def batch_output():
        job = fleet.jobs.get("batch")
        state = job.state() if job is not None else None
        if not state or state["phase"] != "done" or not Path(state["result"]["output"]).is_file():
            raise HTTPException(404, "no finished batch")
        return Path(state["result"]["output"])

    @app.get("/api/batch/rows")
    def get_batch_rows(offset: int = 0, limit: int = 100):
        from . import batch

        return batch.rows(batch_output(), max(0, offset), max(1, min(limit, 1000)))

    @app.get("/api/batch/download")
    def download_batch():
        path = batch_output()
        return FileResponse(path, filename=path.name, media_type="application/x-ndjson")

    @app.get("/api/benchmarks")
    def get_benchmarks():
        return stats.snapshot()["benchmarks"]

    @app.get("/api/history")
    def get_history(model: str | None = None, source: str | None = None, q: str | None = None, limit: int = 100,
                    before: str | None = None, after: str | None = None, order: str = "desc"):
        if order not in ("asc", "desc"):
            raise HTTPException(400, "order must be asc or desc")
        items = history.list(model=model or None, source=source or None, q=q or None,
                             limit=max(1, min(limit, 500)), before=before, after=after, order=order)
        return {"items": items, "models": history.models()}

    @app.get("/api/history/{record_id}")
    def get_history_record(record_id: str):
        record = history.get(record_id)
        if record is None:
            raise HTTPException(404, f"unknown history record {record_id!r}")
        return {**record, **replay_fields(record.get("request", {}))}

    @app.delete("/api/history")
    def clear_history():
        return {"cleared": history.clear()}

    @app.get("/api/logs")
    def get_logs(source: str = "basal-ui", after: int = 0, generation: str | None = None):
        with fleet.lock:
            buffers = dict(fleet.logs)
            runners = dict(fleet.runners)
        if source not in buffers:
            raise HTTPException(404, f"unknown log source {source!r}")
        sources = [{"id": "basal-ui", "state": "ui"}]
        loaded = [key for key in runners if key in buffers]
        jobs = fleet.jobs.states()
        job_logs = [key for key in buffers if key.startswith("job:")]
        gone = [key for key in buffers if key != "basal-ui" and key not in runners and key not in job_logs]
        sources += [{"id": key, "state": runners[key].state} for key in loaded]
        sources += [{"id": key, "state": "running" if jobs.get(key[4:], {}).get("running") else "done"}
                    for key in job_logs]
        sources += [{"id": key, "state": "gone"} for key in gone]
        buffer = buffers[source]
        changed = generation is not None and generation != buffer.generation
        return {"sources": sources, "generation": buffer.generation,
                "lines": buffer.since(0 if changed else after)}

    return app


def main():
    from .engine import check
    engine = check()
    ap = argparse.ArgumentParser(description="basal browser UI: hardware check, model choice, playground")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7860)
    a = ap.parse_args()
    if engine["experimental"]:
        commit = f"@{engine['commit'][:7]}" if engine["commit"] else ""
        print(f"basal-ui: experimental engine {engine['version']}{commit} (supported: {engine['supported']})",
              flush=True)
    wildcard = a.host in ("0.0.0.0", "::")
    allowed = ["*"] if wildcard else ["127.0.0.1", "localhost", "::1", a.host]
    if wildcard:
        print("WARNING: basal-ui has no authentication; it is exposed to all network hosts.", flush=True)
    import uvicorn
    print(f"basal-ui -> http://{a.host}:{a.port}", flush=True)
    uvicorn.run(create_app(allowed_hosts=allowed), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
