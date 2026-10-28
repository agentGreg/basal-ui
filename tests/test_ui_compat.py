"""The API server's System One checks and OpenAI Decisions translation in front of a stock basal-serve child."""

import hashlib
import json

import httpx
import pytest
from starlette.testclient import TestClient

from basal_ui import gateway, settings
from basal_ui.auth import ApiKeys
from basal_ui.fleet import Fleet, Runner
from basal_ui.hardware import Hardware

HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())
MODEL = "served-model"
REAL_CLIENT = httpx.AsyncClient


class Child:
    """basal-serve 2.4.1 over HTTP: POST /v1/systemone answers each question, or {"error"} with its status."""

    def __init__(self, fail=None):
        self.fail, self.requests = fail, []

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        if self.fail:
            return httpx.Response(422 if self.fail == "value" else 500, json={"error": f"{self.fail} failure"})
        answers = {}
        for name, q in body["questions"].items():
            if q["type"] == "noul":
                answers[name] = {"noul": 0.8, "probabilities": {"true": 0.8, "false": 0.2}, "confidence": 0.8}
            elif q["type"] == "score":
                keys = (
                    list(q["criteria"])
                    if isinstance(q["criteria"], dict)
                    else [str(i) for i in range(len(q["criteria"]))]
                )
                answers[name] = {"score": 0.7, "probabilities": {key: 0.5 for key in keys}, "confidence": 0.5}
            else:
                keys = list(q["criteria"])
                answers[name] = {
                    "choice": keys[0],
                    "probabilities": {k: 0.75 if i == 0 else 0.25 for i, k in enumerate(keys)},
                    "confidence": 0.75,
                }
        return httpx.Response(
            200, json={"model": MODEL, "answers": answers, "usage": {"input_tokens": 12, "output_tokens": 0}}
        )


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))


@pytest.fixture
def make(tmp_path, monkeypatch):
    def make(child=None, keys=None, cors=()):
        child = child or Child()
        monkeypatch.setattr(
            gateway.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(child), **kw)
        )
        fleet = Fleet(
            models_dir_fn=lambda: tmp_path, platform_fn=lambda: "cpu", settings_mod=settings, hardware_fn=lambda: HW
        )
        runner = Runner()
        runner.state, runner.model, runner.base_url = "ready", MODEL, "http://child"
        fleet.runners[MODEL] = runner
        return TestClient(gateway.build_app(fleet, fleet.stats, keys, cors), base_url="http://127.0.0.1"), child

    return make


def post(client, path, body, **kwargs):
    return client.post(path, json={"model": MODEL, **body}, **kwargs)


def test_systemone_codes_request_ids_and_alias_media(make):
    c, child = make()
    with c:
        response = post(
            c,
            "/v1/systemone",
            {
                "state": "hi",
                "questions": {"x": {"type": "choice", "criteria": {"a": "A", "b": "B"}}},
                "images": ["data:image/png;base64,aGk="],
            },
            headers={"x-request-id": "client:42"},
        )
        assert response.status_code == 200
        assert response.headers["x-request-id"] == response.headers["x-typesafe-request-id"] == "client:42"
        assert child.requests[0]["media"] == [{"type": "image", "data": "aGk="}] and "images" not in child.requests[0]
        assert c.post("/v1/systemone", content="{").json()["code"] == "INVALID_JSON"
        assert c.post("/v1/systemone", content="[]", headers={"content-type": "application/json"}).status_code == 400
        malformed_id = c.get("/health", headers={"x-request-id": "!bad"})
        assert malformed_id.headers["x-request-id"].startswith("req_")
        assert (
            post(
                c,
                "/v1/systemone",
                {"state": "x", "questions": {"q": {"type": "noul"}}, "images": ["https://example.test/a"]},
            ).status_code
            == 422
        )
        native = {
            "state": "hi",
            "questions": {"x": {"type": "choice", "criteria": {"a": "A", "b": "B"}}},
            "media": [{"type": "image", "data": "data:image/png;base64,aGk="}],
        }
        assert post(c, "/v1/systemone", native).status_code == 200
        assert child.requests[-1]["media"] == native["media"]
    invalid, _ = make(Child("value"))
    with invalid:
        rejected = post(invalid, "/v1/systemone", {"state": "hi", "questions": {"q": {"type": "noul"}}})
        assert rejected.status_code == 422 and rejected.json()["code"] == "INVALID_REQUEST"
        assert rejected.json()["detail"][0]["msg"] == "value failure"


@pytest.mark.parametrize(
    "body",
    [
        {"state": "x"},
        {"state": "x", "questions": []},
        {"state": "x", "questions": {"bad": "question"}},
        {"state": "x", "questions": {"bad": {"type": "choice", "criteria": "not a mapping"}}},
    ],
)
def test_invalid_systemone_shapes_are_request_errors_that_never_reach_the_child(make, body):
    c, child = make()
    with c:
        response = post(c, "/v1/systemone", body)
    assert response.status_code == 422 and response.json()["code"] == "INVALID_REQUEST"
    assert child.requests == []


@pytest.mark.parametrize("path", ["/v1/systemone", "/v1/decisions"])
def test_child_failures_are_internal_errors_in_the_path_shape(make, path):
    c, _ = make(Child("runtime"))
    body = (
        {"state": "x", "questions": {"q": {"type": "noul"}}}
        if path == "/v1/systemone"
        else {"input": "x", "questions": [{"type": "predicate", "instructions": "valid?"}]}
    )
    with c:
        response = post(c, path, body)
    assert response.status_code == 500
    assert (response.json() if path == "/v1/systemone" else response.json()["error"])["code"] == "INTERNAL_ERROR"


def test_decisions_all_question_types_and_order_and_alias(make):
    c, child = make()
    request = {
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "one"},
                    {"type": "input_text", "text": "two"},
                    {"type": "input_image", "image_url": "data:image/png;base64,aGk="},
                ],
            }
        ],
        "questions": [
            {"type": "predicate", "instructions": "valid?"},
            {"type": "choice", "name": "pick", "instructions": "choose", "choices": [{"value": True}, {"value": "no"}]},
            {
                "type": "score",
                "name": "rating",
                "instructions": "rate",
                "levels": [{"label": "low", "description": "Poor"}, {"label": "high"}],
            },
        ],
    }
    with c:
        response = post(c, "/v1/decisions", request)
    assert response.status_code == 200
    payload = response.json()
    assert [a["type"] for a in payload["answers"]] == ["predicate", "choice", "score"]
    assert [a["name"] for a in payload["answers"]] == [None, "pick", "rating"]
    assert payload["answers"][1]["choice"] is True
    assert payload["model"] == MODEL and payload["usage"]["input_tokens"] == 12
    assert child.requests[0]["state"] == "one\n\ntwo"
    assert child.requests[0]["media"] == [{"type": "image", "data": "aGk="}]


@pytest.mark.parametrize(
    "question",
    [
        *[
            {"type": "choice", "instructions": "choose", "choices": [{"value": str(i)} for i in range(n)]}
            for n in (1, 256)
        ],
        *[{"type": "score", "instructions": "rate", "levels": [{"label": str(i)} for i in range(n)]} for n in (1, 11)],
        {"type": "choice", "instructions": "choose", "choices": [{"value": True}, {"value": "true"}]},
    ],
)
def test_decision_option_limits_and_duplicates(make, question):
    c, child = make()
    with c:
        assert post(c, "/v1/decisions", {"input": "x", "questions": [question]}).status_code == 400
        assert post(c, "/decisions", {"input": "x", "questions": []}).status_code == 400
        assert post(c, "/decisions", {"input": "x", "questions": [{}] * 201}).status_code == 400
    assert child.requests == []


def test_decision_score_level_labels_must_be_unique_and_described_levels_round_trip(make):
    c, child = make()
    question = {
        "type": "score",
        "name": "rating",
        "instructions": "rate",
        "levels": [{"label": "a"}, {"label": "a"}, {"label": "b"}],
    }
    with c:
        response = post(c, "/v1/decisions", {"input": "x", "questions": [question]})
        assert response.status_code == 400
        assert response.json()["error"] | {"param": None} == {
            "message": "score level labels must be unique",
            "type": "invalid_request_error",
            "param": None,
            "code": "INVALID_REQUEST",
        }
        question["levels"] = [{"label": "low", "description": "Poor"}, {"label": "high", "description": "Good"}]
        response = post(c, "/v1/decisions", {"input": "x", "questions": [question]})
    assert response.status_code == 200
    assert [item["label"] for item in response.json()["answers"][0]["probabilities"]] == ["low", "high"]
    assert [item["probability"] for item in response.json()["answers"][0]["probabilities"]] == [0.5, 0.5]
    assert child.requests[-1]["questions"]["rating"]["criteria"] == {"low": "Poor", "high": "Good"}


def test_oversized_or_too_many_alias_media_are_rejected_before_decoding(make, monkeypatch):
    from basal_ui import compat

    def fail_decode(*args, **kwargs):
        raise AssertionError("oversized media must be rejected before decoding")

    c, child = make()
    native = {
        "state": "x",
        "questions": {"q": {"type": "choice", "criteria": {"a": "A", "b": "B"}}},
        "media": [{"type": "image", "data": "aGk="}],
    }
    with c:
        assert post(c, "/v1/systemone", {**native, "images": ["data:image/png;base64,aGk="] * 8}).status_code == 422
        monkeypatch.setattr(compat.base64, "b64decode", fail_decode)
        encoded = "A" * ((20 * 1024 * 1024 * 4 // 3) + 4)
        assert (
            post(
                c, "/v1/systemone", {**native, "media": [], "images": [f"data:image/png;base64,{encoded}"]}
            ).status_code
            == 422
        )
    assert child.requests == []


def test_decision_unsupported_parameter_and_remote_image(make):
    c, _ = make()
    with c:
        response = post(
            c, "/v1/decisions", {"input": "x", "questions": [{"type": "predicate", "instructions": "x"}], "bad": 1}
        )
        assert (
            response.json()["error"]["code"] == "unsupported_parameter" and response.json()["error"]["param"] == "bad"
        )
        remote = {
            "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "https://example.test/a"}]}],
            "questions": [{"type": "predicate", "instructions": "x"}],
        }
        assert post(c, "/v1/decisions", remote).status_code == 400


def test_auth_envelopes_request_ids_and_cors(make, tmp_path):
    keys = tmp_path / "keys.json"
    keys.write_text(f'{{"keys": [{{"name": "user", "sha256": "{hashlib.sha256(b"secret").hexdigest()}"}}]}}')
    c, _ = make(keys=ApiKeys(keys), cors=("https://allowed.example",))
    with c:
        one = c.post("/v1/systemone", json={})
        assert one.status_code == 401 and one.json()["code"] == "UNAUTHORIZED"
        openai = c.post("/v1/decisions", json={})
        assert openai.status_code == 401 and openai.json()["error"]["type"] == "authentication_error"
        assert openai.headers["x-request-id"] == openai.headers["x-typesafe-request-id"]
        cors = c.post("/v1/systemone", json={}, headers={"Origin": "https://allowed.example"})
        assert "x-request-id" in cors.headers["access-control-expose-headers"].lower()
