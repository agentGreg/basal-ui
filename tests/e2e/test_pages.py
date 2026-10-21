"""The pages of basal-ui in Chromium (Playwright, basal[e2e]): every view renders without a script error, the
Playground builds, sends and renders every question type, the load options dialog, Batch, History, Logs and Inspect."""
import json
import re

import pytest

from tests.e2e.support import CHOICE, EXAMPLES, advanced_requests

sync_api = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and run: playwright install chromium")

VIEWS = {"overview": "Overview", "models": "Models", "manager": "Model manager", "playground": "Playground",
         "batch": "Batch", "inspect": "Inspect", "api": "API server", "history": "History", "logs": "Logs"}
WAIT = 120_000  # ms: a decision of a large model, a model start


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, ui, text_model):
    """A fresh tab on basal-ui with the text model loaded; fails the test on any uncaught script error."""
    ui.load(text_model["id"])
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    tab.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error"
           and "Failed to load resource" not in m.text else None)  # 4xx answers the page expects are not errors
    tab.set_default_timeout(WAIT)
    tab.goto(ui.url + "/#overview")
    tab.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
    yield tab
    context.close()
    assert not errors, errors


def view(page, name):
    page.click(f'.side-nav [data-view="{name}"]')
    sync_api.expect(page.locator("#pageTitle")).to_have_text(VIEWS[name])


def test_every_view_renders(page, text_model):
    for name in VIEWS:
        view(page, name)
        sync_api.expect(page.locator(f'section.view[data-page="{name}"]').first).to_be_visible()
    view(page, "overview")
    sync_api.expect(page.locator("#loadedModels")).to_contain_text(text_model["id"])
    sync_api.expect(page.locator("#footerState")).to_contain_text("ready")


def normalised(questions):
    """The builder leaves out the values that are the engine's defaults (compress unit "sentence")."""
    out = json.loads(json.dumps(questions))
    for q in out.values():
        if q.get("type") == "compress" and q.get("unit") == "sentence":
            del q["unit"]
    return out


@pytest.mark.parametrize("request_", advanced_requests(), ids=lambda r: r["id"])
def test_the_playground_builds_sends_and_renders_each_question_type(page, text_model, request_):
    view(page, "playground")
    page.select_option("#playgroundModel", text_model["id"])
    index = page.evaluate("id => examples.findIndex(e => e.file === 'advanced' && e.id === id)", request_["id"])
    page.select_option("#examples", str(index))
    built = page.evaluate("() => buildRequest()")
    assert built["state"] == request_["state"]
    assert built["questions"] == normalised(request_["questions"])  # the builder round-trips the request
    page.click("#sendButton")
    page.wait_for_selector("#results article.answer")
    sync_api.expect(page.locator("#decideError")).to_be_hidden()
    articles = page.locator("#results article.answer")
    sync_api.expect(articles).to_have_count(len(request_["questions"]))
    q_type = next(iter(request_["questions"].values()))["type"]
    sync_api.expect(articles.first.locator("h3 .chip").first).to_have_text(q_type)
    if q_type in ("compress", "extract"):
        sync_api.expect(page.locator("#results .source-view mark").first).to_be_visible()
    if q_type == "verify":
        sync_api.expect(articles.first.locator("tbody tr")).to_have_count(3)


def test_the_builder_switches_types_and_edits_a_cost_matrix(page):
    view(page, "playground")
    page.evaluate("() => setRequest({state: 'Paczka nie dotarła.', questions: {}})")
    card = page.locator(".question").first
    card.locator(".q-type").select_option("act")
    card.locator('[data-f="criteria"]').fill("late: opóźnienie\nlost: zaginięcie")
    card.locator('[data-cost-mode="matrix"]').click()
    sync_api.expect(card.locator(".cost-table thead th")).to_have_text(["Action", "late", "lost", ""])
    card.locator(".add-action").click()
    row = card.locator(".cost-table tbody tr").last
    row.locator(".cost-action").fill("human")
    for cell in row.locator(".cost-cell").all():
        cell.fill("2")
    costs = page.evaluate("() => buildRequest().questions.question.costs")
    assert costs["human"] == {"late": 2, "lost": 2} and costs["defer"] == {"late": 1, "lost": 1}
    card.locator(".q-type").select_option("tool")
    card.locator('[data-f="tools"]').fill("not json")
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text("tools must be a JSON array")


def test_safety_strip_and_inspect_button_on_a_decision(page, text_model):
    view(page, "playground")
    page.evaluate("r => setRequest(r)", CHOICE)
    page.click("#sendButton")
    strip = page.locator("#results .chips.safety")
    sync_api.expect(strip.first).to_contain_text(
        re.compile("Automatable|Below the certified|No certificate|uncalibrated|calibrated"))
    sync_api.expect(page.locator("#inspectDecision")).to_be_visible()
    sync_api.expect(page.locator("#results")).to_contain_text("Longest prompt")


def test_the_load_options_dialog_lists_the_modes(page, text_model):
    view(page, "overview")  # the loaded model's ⚙: restart with other options
    page.click(f'#loadedModels .options-model[data-id="{text_model["id"]}"]')
    dialog = page.locator("#loadOptions")
    sync_api.expect(dialog).to_be_visible()
    sync_api.expect(page.locator("#optionsApply")).to_have_text("Restart")
    sync_api.expect(dialog.locator(f'input[name="optMode"][value="{text_model["mode"]}"]')).to_be_checked()
    assert dialog.locator('input[name="optMode"]').count() == 16
    assert dialog.locator('input[name="optMode"][disabled]').count() >= 1
    sync_api.expect(dialog.locator("#optExperimentalNote")).not_to_be_empty()
    page.click("#optionsCancel")
    sync_api.expect(dialog).to_be_hidden()


def test_batch_view_runs_a_file_and_lists_the_answers(page, text_model, tmp_path):
    view(page, "batch")
    page.select_option("#batchModel", text_model["id"])
    page.fill("#batchInput", str(EXAMPLES / "questions.jsonl"))
    page.fill("#batchOutput", str(tmp_path / "answers.jsonl"))
    page.fill("#batchConcurrency", "4")
    page.click("#runBatch")
    sync_api.expect(page.locator("#downloadBatch")).to_be_visible()
    sync_api.expect(page.locator("#batchRows tbody tr")).to_have_count(20)
    sync_api.expect(page.locator("#batchSummary")).to_contain_text("20 items")


def test_history_and_logs_views(page, ui, text_model):
    ui.decide(text_model["id"], CHOICE)
    view(page, "history")
    rows = page.locator("#historyTable tr.history-row")
    sync_api.expect(rows.first).to_be_visible()
    rows.first.click()
    sync_api.expect(page.locator("#historyTable .history-detail .history-request")).to_contain_text('"questions"')
    view(page, "logs")
    sync_api.expect(page.locator("#logSources")).to_contain_text(text_model["id"].split("/")[-1])


def test_inspect_view_embeds_basal_viz_and_explains_a_playground_decision(page, ui, bf16_model):
    view(page, "inspect")
    page.select_option("#inspectModel", bf16_model["id"])
    page.click("#startInspect")
    frame_el = page.locator("#vizFrame")
    sync_api.expect(frame_el).to_be_visible(timeout=600_000)
    frame = page.frame_locator("#vizFrame")
    sync_api.expect(frame.locator("#modelchip")).to_contain_text(bf16_model["id"])
    view(page, "playground")
    page.evaluate("r => setRequest(r)", CHOICE)
    page.click("#sendButton")
    page.click("#inspectDecision")
    sync_api.expect(page.locator("#pageTitle")).to_have_text("Inspect")
    sync_api.expect(frame.locator("#out .result").first).to_be_visible()
    sync_api.expect(frame.locator("#out")).to_contain_text("Do którego działu")
    page.click("#stopInspect")
    sync_api.expect(frame_el).to_be_hidden()
    assert ui.get("/api/inspect")["state"] == "idle"
