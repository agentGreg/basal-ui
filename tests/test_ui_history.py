import pytest

from basal_ui.history import History, make_record, replay_fields


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))


def record(model="m", source="api", state="state", answers=None):
    response = {
        "answers": answers or {"q": {"type": "choice", "choice": "yes", "confidence": 0.9}},
        "usage": {"input_tokens": 7},
    }
    return make_record(model, source, {"model": model, "state": state, "questions": {"q": {}}}, 200, response, 3.0)


def test_list_filters_search_and_pages_newest_first(tmp_path):
    history = History(tmp_path / "h.jsonl")
    records = [
        record("a", "api", "Umowa najmu"),
        record("b", "playground", "invoice overdue"),
        record("a", "mcp", "Faktura VAT"),
        record("b", "api", "umowa o pracę"),
    ]
    for r in records:
        history.append(r)
    ids = [r["id"] for r in records]
    assert [r["id"] for r in history.list()] == ids[::-1]
    assert "request" not in history.list()[0] and history.get(ids[0])["request"]["state"] == "Umowa najmu"
    assert [r["id"] for r in history.list(model="a")] == [ids[2], ids[0]]
    assert [r["id"] for r in history.list(source="api")] == [ids[3], ids[0]]
    assert [r["id"] for r in history.list(q="UMOWA")] == [ids[3], ids[0]]
    assert [r["id"] for r in history.list(limit=2)] == [ids[3], ids[2]]
    assert [r["id"] for r in history.list(before=ids[2])] == [ids[1], ids[0]]
    assert [r["id"] for r in history.list(after=ids[1])] == [ids[3], ids[2]]
    assert [r["id"] for r in history.list(after=ids[1], order="asc")] == [ids[2], ids[3]]
    assert history.models() == ["a", "b"]
    first = history.list()[-1]
    assert (first["questions"], first["input_tokens"], first["answers"]) == (
        1,
        7,
        {"q": {"type": "choice", "best": "yes", "confidence": 0.9}},
    )


def test_oldest_first_poll_pages_drain_a_burst_with_equal_timestamps(tmp_path):
    history = History(tmp_path / "h.jsonl", keep=200)
    records = [record(state=f"s{i}") for i in range(150)]
    for item in records:
        item["ts"] = 1
        history.append(item)
    cursor, received = records[0]["id"], []
    while True:
        page = history.list(after=cursor, limit=100, order="asc")
        received.extend(page)
        if len(page) < 100:
            break
        cursor = page[-1]["id"]
    assert [item["id"] for item in received] == [item["id"] for item in records[1:]]


def test_a_failed_write_leaves_memory_unchanged(tmp_path):
    path = tmp_path / "h.jsonl"
    history = History(path)
    history.append(record(state="kept"))
    before = history.list()
    path.unlink()
    path.mkdir()  # the file cannot be opened for writing any more
    with pytest.raises(OSError):
        history.append(record(state="lost"))
    assert history.list() == before


def test_media_bytes_are_replaced_by_their_size_and_states_are_previewed():
    body = {
        "state": {"text": "x" * 300},
        "images": [{"data": "QUJD", "mime": "image/png"}],
        "questions": {"q": {"type": "noul"}},
    }
    r = make_record("m", "playground", body, 422, {"error": "bad"}, 1.0)
    assert r["request"]["images"] == [{"data": "<4 bytes>", "mime": "image/png"}]
    url = "data:image/png;base64," + "A" * 100
    openai = make_record("m", "api", {"input": [{"type": "input_image", "image_url": url}]}, 200, {}, 1.0)
    assert openai["request"]["input"][0]["image_url"] == f"<{len(url)} bytes>"
    assert body["images"][0]["data"] == "QUJD"  # the caller's body is untouched
    assert len(r["state_preview"]) == 160 and r["state_preview"].startswith('{"text": "xxx')
    assert (r["status"], r["answers"], r["response"]) == (422, {}, {"error": "bad"})


def test_a_field_named_data_outside_media_is_kept_and_replays():
    body = {
        "state": "A user reports a leak.",
        "questions": {"q": {"type": "choice", "criteria": {"data": "A data or privacy problem", "other": "Other"}}},
        "media": [{"type": "image", "data": "QUJD"}],
    }
    r = make_record("m", "playground", body, 200, {}, 1.0)
    assert r["request"]["questions"] == body["questions"]
    assert r["request"]["media"] == [{"type": "image", "data": "<4 bytes>"}]
    audio = {"type": "input_audio", "input_audio": {"data": "QUJD", "format": "wav"}}
    recorded = make_record("m", "api", {"input": [audio]}, 200, {}, 1.0)["request"]
    assert recorded["input"][0]["input_audio"]["data"] == "<4 bytes>"
    del body["media"]
    fields = replay_fields(make_record("m", "playground", body, 200, {}, 1.0)["request"])
    assert fields["replay_error"] is None
    assert fields["replay_request"]["questions"]["q"]["criteria"]["data"] == "A data or privacy problem"


def test_decisions_api_history_has_a_playground_replay_without_changing_recorded_request():
    request = {
        "model": "m",
        "input": "A state",
        "questions": [
            {"type": "choice", "name": "q", "instructions": "Choose", "choices": [{"value": "yes"}, {"value": "no"}]}
        ],
        "store": True,
    }
    record_ = make_record("m", "api", request, 200, {}, 1.0)
    fields = replay_fields(record_["request"])
    assert record_["request"] == request
    assert fields == {
        "replay_request": {
            "state": "A state",
            "questions": {"q": {"type": "choice", "instructions": "Choose", "criteria": {"yes": None, "no": None}}},
            "model": "m",
        },
        "replay_error": None,
    }


def test_redacted_media_blocks_history_replay_with_explanation():
    request = {
        "model": "m",
        "input": [
            {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64," + "A" * 100}]}
        ],
        "questions": [{"type": "predicate", "instructions": "Is this a cat?"}],
    }
    record_ = make_record("m", "api", request, 200, {}, 1.0)
    fields = replay_fields(record_["request"])
    assert fields["replay_request"] is None
    assert "original media attachments" in fields["replay_error"]
    text = replay_fields({"state": "The case says <120 bytes> but has no attachment.", "questions": {}})
    assert text["replay_request"]["state"].endswith("<120 bytes> but has no attachment.")
    assert text["replay_error"] is None


def test_records_flag_deferred_answers_and_truncated_states():
    r = make_record(
        "m",
        "api",
        {"state": "s", "questions": {"q": {"type": "act"}}},
        200,
        {"answers": {"q": {"type": "act", "deferred": True}}, "usage": {"truncated": {"dropped_tokens": 3}}},
        1.0,
    )
    assert r["deferred"] is True and r["truncated"] is True
    plain = record("a", "api", "s")
    assert plain["deferred"] is False and plain["truncated"] is False


def test_persistence_rotation_and_clear(tmp_path):
    path = tmp_path / "h.jsonl"
    history = History(path, keep=3)
    for i in range(510):
        history.append(record(state=f"s{i}"))
    assert len(path.read_text().splitlines()) == 9  # the 504th append rewrote the file with the last 3, then 6 more
    assert [r["state_preview"] for r in History(path, keep=3).list()] == ["s509", "s508", "s507"]
    assert path.stat().st_mode & 0o777 == 0o600
    with path.open("a") as f:
        f.write("{broken\n")
    assert len(History(path, keep=3).list()) == 3
    assert history.clear() == 3
    assert history.list() == [] and History(path).list() == []
