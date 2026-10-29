"""The pages of basal-ui in Chromium (Playwright, basal[e2e]): every view renders without a script error, the
Playground builds, sends and renders every question type, the load options dialog, Batch, History, Logs and Inspect."""

import json
import re

import pytest

from tests.e2e.support import CHOICE, EXAMPLES, advanced_requests

sync_api = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and run: playwright install chromium")

VIEWS = {
    "backends": "Backends",
    "overview": "Overview",
    "models": "Models",
    "manager": "Model manager",
    "playground": "Playground",
    "batch": "Batch",
    "inspect": "Inspect",
    "api": "API server",
    "history": "History",
    "logs": "Logs",
}
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
    tab.on(
        "console",
        lambda m: (
            errors.append(f"console: {m.text}")
            if m.type == "error" and "Failed to load resource" not in m.text
            else None
        ),
    )  # 4xx answers the page expects are not errors
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


def test_visible_ui_text_meets_wcag_contrast_in_both_themes(page):
    def low_contrast():
        return page.evaluate("""() => {
          const rgb = value => {
            const m = value.match(/[\\d.]+/g);
            return m ? m.slice(0,3).map(Number) : null;
          };
          const alpha = value => value.startsWith('rgba(') ? Number(value.match(/,\\s*([\\d.]+)\\s*\\)$/)?.[1] ?? 1) : 1;
          const luminance = c => {
            const s = c.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
            return .2126*s[0] + .7152*s[1] + .0722*s[2];
          };
          const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
          const targets = [...document.querySelectorAll('button,.badge,.chip,[role="tab"],.side-nav button,[role="alert"],.banner')];
          return targets.filter(el => visible(el) && !(el instanceof HTMLButtonElement && el.disabled) && el.textContent.trim()).flatMap(el => {
            const style = getComputedStyle(el), fg = rgb(style.color);
            const ancestors=[];for(let node=el;node;node=node.parentElement)ancestors.unshift(node);
            let bg=[255,255,255], opacity=1;
            for(const node of ancestors){
              const computed=getComputedStyle(node), color=computed.backgroundColor, a=alpha(color), c=rgb(color);
              if(c)bg=c.map((v,i)=>v*a+bg[i]*(1-a));
              opacity*=Number(computed.opacity);
            }
            if (!fg) return [];
            const displayed=fg.map((v,i)=>v*opacity+bg[i]*(1-opacity));
            const ratio = (Math.max(luminance(displayed), luminance(bg)) + .05) / (Math.min(luminance(displayed), luminance(bg)) + .05);
            const size = parseFloat(style.fontSize), bold = parseInt(style.fontWeight, 10) >= 700;
            const required = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
            return ratio + .01 < required ? [{text: el.textContent.trim().slice(0,60), ratio, required, fg: style.color, bg}] : [];
          });
        }""")

    for theme in ("light", "dark"):
        page.evaluate("theme => applyTheme(theme)", theme)
        page.wait_for_timeout(300)
        for name in VIEWS:
            view(page, name)
            assert not low_contrast(), f"{theme} {name}: {low_contrast()}"
            if name == "models":
                page.click("#allModelsTab")
                assert not low_contrast(), f"{theme} models/all: {low_contrast()}"
    page.evaluate("() => applyTheme('system')")


@pytest.mark.parametrize("request_", advanced_requests(), ids=lambda r: r["id"])
def test_the_playground_builds_sends_and_renders_each_question_type(page, text_model, request_):
    view(page, "playground")
    page.select_option("#playgroundModel", text_model["id"])
    index = page.evaluate("id => examples.findIndex(e => e.file === 'advanced' && e.id === id)", request_["id"])
    page.select_option("#examples", str(index))
    built = page.evaluate("() => buildRequest()")
    assert built["state"] == request_["state"]
    assert built["questions"] == request_["questions"]  # exactly the request: the builder never rewrites it
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


def test_playground_request_names_media_and_history_replay_are_lossless_until_send(page, text_model):
    view(page, "playground")
    page.evaluate("() => { addQuestion(); addQuestion(); }")
    assert page.evaluate("() => [...document.querySelectorAll('.q-name')].slice(-2).map(x => x.value)") == [
        "question2",
        "question3",
    ]

    page.evaluate("() => { document.querySelector('.q-name').value = '__proto__'; }")
    assert page.evaluate("() => Object.hasOwn(buildRequest().questions, '__proto__')")

    request = {
        "model": text_model["id"],
        "state": "inspect",
        "questions": {"q": {"type": "choice", "instructions": "Choose", "criteria": {"a": None, "b": None}}},
        "media": [{"type": "image", "data": "aGVsbG8="}],
        "detail": "high",
    }
    page.evaluate("r => setRequest(r)", request)
    assert page.evaluate("() => selectedModel") == text_model["id"]
    assert page.evaluate("() => [selectedMedia.length, $('#mediaDetail').value, buildRequest().media[0].data]") == [
        1,
        "high",
        "aGVsbG8=",
    ]
    unsupported = {**request, "vendor_extension": {"keep": True}}
    page.evaluate("r => setRequest(r)", unsupported)
    assert page.locator("#editJsonToggle").is_checked()
    sync_api.expect(page.locator("#mediaError")).to_be_visible()
    assert page.evaluate("() => JSON.parse($('#requestJson').value).vendor_extension") == {"keep": True}
    assert page.evaluate("() => selectedMedia.length") == 0
    for unsupported_value in (
        {"media": "opaque"},
        {"media": [{"type": "image", "data": "aGVsbG8=", "name": "x.png"}]},
        {"lang": "fr"},
    ):
        page.evaluate("([base,extra]) => setRequest({...base,...extra})", [request, unsupported_value])
        assert page.locator("#editJsonToggle").is_checked()
        assert page.evaluate("() => JSON.parse($('#requestJson').value)") == {**request, **unsupported_value}

    page.evaluate(
        "r => setRequest(r)",
        {"state": "inspect", "questions": {"q": request["questions"]["q"], "q2": request["questions"]["q"]}},
    )
    page.locator(".q-name").first.fill("same")
    page.locator(".q-name").nth(1).fill("same")
    decide_calls = []

    def reject_decision(route):
        decide_calls.append(1)
        route.fulfill(status=500, body="unexpected send")

    page.route("**/api/decide", reject_decision)
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text("non-blank and unique")
    assert page.locator(".q-name:invalid").count() == 1
    assert not decide_calls

    page.locator(".q-name").nth(1).fill("")
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text("non-blank and unique")
    assert not decide_calls
    preset_calls = []

    def count_presets(route):
        if route.request.method == "PUT":
            preset_calls.append(route.request.method)
        route.continue_()

    page.route("**/api/presets", count_presets)
    page.click("#saveRequestPreset")
    page.fill("#presetName", "invalid")
    page.click("#presetNameSave")
    assert "PUT" not in preset_calls

    page.evaluate(
        "([model,r]) => replayRequest({model,replay_request:r,replay_error:null})",
        [text_model["id"], {"state": "replayed", "questions": {"q": request["questions"]["q"]}}],
    )
    assert page.locator("#stateInput").input_value() == "replayed"
    assert page.locator("#results article.answer").count() == 0
    assert not decide_calls


def test_server_messages_translate_in_polish_and_remain_english(page):
    page.evaluate("localStorage.setItem('basal-ui.lang', 'pl')")
    page.reload()
    page.wait_for_function("() => typeof statusCache !== 'undefined' && modelData.length > 0")
    fixture = page.evaluate("""async () => {
      const data = await fetch('/api/models').then(r => r.json());
      if (!data.models.some(m => !m.runnable && m.reason_code)) {
        const source = data.models.find(m => m.runnable);
        data.models.push({...source, id: `${source.id}:e2e-unavailable`, runnable: false,
          reason: 'needs an NVIDIA GPU (the CUDA graph engine)', reason_code: 'cuda_graph_gpu',
          reason_params: {}});
      }
      const source = data.models.find(m => m.runnable);
      const packed = {...source, id: '__e2e_packed__', family: 'basal-2.0', tier: 'mini',
        label: 'MLX 8-bit', runnable: true};
      const multimodal = {...source, id: '__e2e_multimodal__', family: 'basal-omni-1.0',
        tier: 'mini', runnable: true};
      data.models.push(packed, multimodal);
      const packedParams = {tier: 'mini', label: 'MLX 8-bit', estimate: 3.7, memory: 6.4};
      const packedText = 'mini in MLX 8-bit: needs about 3.7 GB of 6.4 GB available; about 1.5x faster with --mlx-packed (Load options)';
      const multimodalParams = {kinds: ['image', 'audio'], mode: 'eager'};
      const multimodalText = 'images and audio, served with --mode eager';
      data.suggestions.push(
        {slot: 'recommended', id: packed.id, why: packedText, why_code: 'suggest_packed', why_params: packedParams},
        {slot: 'multimodal', id: multimodal.id, why: multimodalText,
          why_code: 'suggest_multimodal', why_params: multimodalParams});
      renderModels(data);
      return {data, unavailable: data.models.find(m => !m.runnable && m.reason_code),
              suggestion: data.suggestions.find(s => s.why_code),
              packed: data.suggestions.find(s => s.id === packed.id),
              multimodal: data.suggestions.find(s => s.id === multimodal.id)};
    }""")
    unavailable, suggestion = fixture["unavailable"], fixture["suggestion"]
    assert unavailable and suggestion
    page.click('.side-nav [data-view="models"]')
    page.click("#allModelsTab")
    page.check("#notRunnableToggle")
    page.check("#olderToggle")  # the unavailable model may be of an older family (basal-1.5 GGUF)
    translated_reason = page.evaluate("m => ts(m.reason, m.reason_code, m.reason_params)", unavailable)
    assert translated_reason != unavailable["reason"]
    reason = page.locator("#modelsTable .reason").filter(has_text=translated_reason)
    sync_api.expect(reason.first).to_be_visible()
    sync_api.expect(reason.first).to_have_text(translated_reason)

    page.click("#suggestedTab")
    translated_why = page.evaluate("s => ts(s.why, s.why_code, s.why_params)", suggestion)
    assert translated_why != suggestion["why"]
    sync_api.expect(page.locator("#suggested .why").filter(has_text=translated_why).first).to_be_visible()
    for key in ("packed", "multimodal"):
        card = fixture[key]
        translated = page.evaluate("s => ts(s.why, s.why_code, s.why_params)", card)
        assert translated != card["why"]
        sync_api.expect(page.locator("#suggested .why").filter(has_text=translated).first).to_be_visible()
    assert "obrazy i audio" in page.evaluate("s => ts(s.why, s.why_code, s.why_params)", fixture["multimodal"])
    assert "--mlx-packed" in page.evaluate("s => ts(s.why, s.why_code, s.why_params)", fixture["packed"])
    assert (
        page.evaluate("""() => ts('mini in bf16: needs about — of a 8.6 GB budget',
      'suggest_recommended_budget', {tier: 'mini', label: 'bf16', estimate: null, memory: 8.6})""")
        == "mini w formacie bf16: potrzeba około — z budżetu 8.6 GB"
    )

    options_target = page.evaluate("""async () => {
      for (const m of modelData.filter(x => x.runnable)) {
        const data = await fetch(`/api/models/options?id=${encodeURIComponent(m.id)}`).then(r => r.json());
        const mode = data.modes.find(x => x.reason_code);
        if (mode) return {id: m.id, mode, text: ts(mode.reason, mode.reason_code, mode.reason_params)};
      }
      return null;
    }""")
    assert options_target
    page.click("#allModelsTab")
    page.locator(f'#modelsTable .options-model[data-id="{options_target["id"]}"]').click()
    sync_api.expect(page.locator("#loadOptions")).to_be_visible()
    sync_api.expect(
        page.locator("#loadOptions .option-mode.unavailable small").filter(has_text=options_target["text"]).first
    ).to_be_visible()
    assert options_target["text"] != options_target["mode"]["reason"]

    page.evaluate("localStorage.setItem('basal-ui.lang', 'en')")
    page.reload()
    page.wait_for_function("() => typeof statusCache !== 'undefined' && modelData.length > 0")
    page.evaluate("data => renderModels(data)", fixture["data"])
    page.click('.side-nav [data-view="models"]')
    page.click("#allModelsTab")
    page.check("#notRunnableToggle")
    page.check("#olderToggle")  # the unavailable model may be of an older family (basal-1.5 GGUF)
    sync_api.expect(page.locator("#modelsTable .reason").filter(has_text=unavailable["reason"]).first).to_be_visible()
    page.click("#suggestedTab")
    sync_api.expect(page.locator("#suggested .why").filter(has_text=suggestion["why"]).first).to_be_visible()
    page.click("#allModelsTab")
    page.locator(f'#modelsTable .options-model[data-id="{options_target["id"]}"]').click()
    sync_api.expect(
        page.locator("#loadOptions .option-mode.unavailable small")
        .filter(has_text=options_target["mode"]["reason"])
        .first
    ).to_be_visible()


UNTRANSLATED = """() => {  // visible text and labels that are a key of the Polish dictionary, shown in English
  const shown = el => el && el.getClientRects().length > 0;
  const english = s => Object.hasOwn(PL, s) && PL[s] !== s;
  const out = new Set(), walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walk.nextNode());) { const s = n.nodeValue.trim(); if (s && shown(n.parentElement) && english(s)) out.add(s); }
  for (const a of ['title', 'placeholder', 'aria-label'])
    for (const el of document.querySelectorAll(`[${a}]`)) { const s = el.getAttribute(a).trim(); if (shown(el) && english(s)) out.add(`@${a}: ${s}`); }
  return [...out];
}"""


def test_polish_ui_leaves_no_frontend_text_in_english(page, text_model):
    page.evaluate("localStorage.setItem('basal-ui.lang', 'pl')")
    page.reload()
    page.wait_for_function("() => typeof statusCache !== 'undefined' && modelData.length > 0")
    assert page.evaluate("() => [document.documentElement.lang, LANG]") == ["pl", "pl"]
    left = {}
    for name in VIEWS:
        page.click(f'.side-nav [data-view="{name}"]')
        sync_api.expect(page.locator(f'section.view[data-page="{name}"]').first).to_be_visible()
        if name == "manager":
            page.evaluate("""() => {
              statsData.benchmarks = {stub: [{kind:'modes', model:'Remek/basal-2.0-mini',
                set:'bundled', when:1, modes:[{mode:'eager',dec_s:2}]}]};
              renderBenchmarkResults();
            }""")
        page.wait_for_timeout(300)  # views render their lists after their own requests
        if name == "history":
            page.evaluate("""() => renderHistory(['mcp','api','playground'].map((source,i) => ({
              id:'stub'+i,ts:1,model:'Remek/basal-2.0-mini',source,state_preview:'test',
              answers:{},latency_ms:1,status:'ok'
            })))""")
        left[name] = page.evaluate(UNTRANSLATED)
        if name == "models":
            for tab in ("#suggestedTab", "#allModelsTab"):
                page.click(tab)
                left[f"models {tab}"] = page.evaluate(UNTRANSLATED)
    page.click('.side-nav [data-view="models"]')
    page.locator(f'#modelsTable .options-model[data-id="{text_model["id"]}"]').click()
    sync_api.expect(page.locator("#loadOptions")).to_be_visible()
    left["load options"] = page.evaluate(UNTRANSLATED)
    page.click("#optionsCancel")
    page.evaluate("id => openModelCard(id)", text_model["id"])
    sync_api.expect(page.locator("#modelCardDialog")).to_be_visible()
    left["model card"] = page.evaluate(UNTRANSLATED)
    assert not {k: v for k, v in left.items() if v}, left
    assert page.locator(".side-nav [data-view='manager']").inner_text().strip() == "Menedżer modeli"


def test_model_manager_disk_rows_stack_without_overflow_at_mobile_width(page, text_model):
    page.set_viewport_size({"width": 390, "height": 844})
    view(page, "manager")
    rows = page.locator("#diskModels .disk-row")
    sync_api.expect(rows.filter(has=page.locator(f'[data-id="{text_model["id"]}"]'))).to_be_visible()
    for index in range(rows.count()):
        row = rows.nth(index)
        assert row.locator(".disk-info").evaluate("(el) => el.getBoundingClientRect().width") > 100
        assert row.locator(".disk-actions").evaluate("""el => {
          const row = el.closest('.disk-row'), a = el.getBoundingClientRect(), b = row.getBoundingClientRect();
          return a.left >= b.left && a.right <= b.right;
        }""")


def test_media_example_loads_with_a_notice_and_draws_element_probabilities(page, text_model):
    view(page, "playground")
    page.select_option("#playgroundModel", text_model["id"])
    index = page.evaluate("() => examples.findIndex(e => e.id === 'media-01-checkout-elements')")
    page.select_option("#examples", str(index))
    sync_api.expect(page.locator("#mediaError")).to_contain_text("Load with media")  # a text model takes no images
    assert page.evaluate("() => selectedMedia.map(m => m.file.name)") == ["checkout.png"]
    sync_api.expect(page.locator("#mediaField")).to_be_visible()
    sync_api.expect(page.locator("#mediaInput")).to_be_disabled()
    decide_calls = []
    page.route("**/api/decide", lambda route: (decide_calls.append(1), route.fulfill(status=500, body="sent")))
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text("does not take images")  # refused before sending
    assert not decide_calls
    page.evaluate("() => { loadedMedia = ['image', 'audio']; }")  # as on a media model: evidence is what is refused
    page.check("#editJsonToggle")
    page.evaluate("""() => { const r = JSON.parse($('#requestJson').value); r.questions.click_target.evidence = true;
      $('#requestJson').value = JSON.stringify(r); }""")
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text('turn off "Evidence spans" on click_target')
    assert not decide_calls
    page.evaluate("() => { loadedMedia = []; }")
    page.evaluate("""() => renderResults({answers: {click_target: {type: 'choice', choice: '1',
      probabilities: {'1': 0.7, '2': 0.2, '3': 0.05, '4': 0.05}}}}, buildRequest())""")
    overlay = page.locator("#results .image-source svg")
    assert overlay.locator("rect").count() == 4
    assert "checkout.png" in overlay.get_attribute("aria-label")
    sync_api.expect(overlay.locator("text").first).to_have_text("1 · 70.0%")
    page.select_option(
        "#examples", str(page.evaluate("() => examples.findIndex(e => e.id === 'media-04-voice-complaint')"))
    )
    sync_api.expect(page.locator("#mediaItems audio.media-audio")).to_have_count(1)
    assert page.evaluate("() => selectedMedia.map(m => m.file.name)") == ["complaint_pl.wav"]


RUBRIC = {
    "state": "Od wczoraj nie mogę zalogować się do aplikacji.",
    "questions": {
        "category": {
            "type": "classify",
            "option_keys": "show",
            "labels": [{"name": "bug", "rubric": "błąd aplikacji"}, {"name": "billing"}],
        },
        "dept": {"type": "choice", "instructions": "Który dział?", "criteria": ["it", "sales"]},
    },
}


def test_the_builder_keeps_what_it_cannot_write_in_its_own_form(page):
    view(page, "playground")
    page.evaluate("r => setRequest(r)", RUBRIC)
    assert page.evaluate("() => buildRequest().questions") == RUBRIC["questions"]
    cards = page.locator(".question")
    cards.nth(0).locator(".q-instructions-input").fill("Jaka to kategoria?")  # an edit elsewhere on the card
    cards.nth(1).locator(".q-instructions-input").fill("Do którego działu?")
    built = page.evaluate("() => buildRequest().questions")
    assert built["category"]["labels"] == RUBRIC["questions"]["category"]["labels"]  # still {name, rubric} objects
    assert built["category"]["option_keys"] == "show" and built["category"]["instructions"] == "Jaka to kategoria?"
    assert built["dept"]["criteria"] == ["it", "sales"] and "option_keys" not in built["dept"]
    cards.nth(0).locator('[data-f="labels"]').fill("bug: błąd aplikacji\nbilling: płatności\nother")
    assert page.evaluate("() => buildRequest().questions.category.labels") == [
        {"name": "bug", "rubric": "błąd aplikacji"},
        {"name": "billing", "rubric": "płatności"},
        {"name": "other"},
    ]


def test_no_automation_claim_where_the_certificate_does_not_hold(page, text_model):
    """A media answer (calibration fitted on text) or one with unvalidated calibration is never "Automatable"."""
    view(page, "playground")
    probe = """([model, calibration]) => {
      const m = statusCache.models.find(x => x.id === model);
      const saved = m.entry; m.entry = {...saved, certified: {"0.01": {choice: 0.5}, "0.05": {choice: 0.4}},
                                        temperature_per_prim: {choice: 1.2}};
      renderResults({answers: {q: {type: 'choice', choice: 'a', probabilities: {a: 0.99, b: 0.01}, confidence: 0.99,
                                   ...(calibration ? {calibration} : {})}}},
                    {model, state: 's', questions: {q: {type: 'choice', criteria: {a: 'A', b: 'B'}}}});
      m.entry = saved;
      return document.querySelector('#results .chips.safety').textContent;
    }"""
    for calibration in ("text", "unvalidated"):
        strip = page.evaluate(probe, [text_model["id"], calibration])
        assert "Automatable" not in strip and "Not certified for this answer" in strip
    probe = """([model, answer, warning]) => {
      const m=statusCache.models.find(x=>x.id===model), saved=m.entry;
      m.entry={...saved,certified:{"0.01":{choice:0.5},"0.05":{choice:0.4}},temperature_per_prim:{choice:1.2},
               ...(warning?{calibration_warning:warning}:{})};
      renderResults({answers:{q:{type:'choice',choice:'a',probabilities:{a:0.99,b:0.01},confidence:0.99,...answer}}},
        {model,state:'s',questions:{q:{type:'choice',criteria:{a:'A',b:'B'}}}});
      m.entry=saved; return document.querySelector('#results .chips.safety').textContent;
    }"""
    for answer, warning in (({"deferred": True}, None), ({"max_error": "0.01"}, None), ({}, "missing calibration")):
        strip = page.evaluate(probe, [text_model["id"], answer, warning])
        assert "Automatable" not in strip and "review" in strip


def test_string_state_is_preserved_until_the_state_field_is_edited(page):
    view(page, "playground")
    literal = '{"not": "json"}'
    page.evaluate("state => setRequest({state, questions: {}})", literal)
    assert page.evaluate("() => buildRequest().state") == literal
    page.evaluate("() => setRequest({state: {structured: true}, questions: {}})")
    assert page.evaluate("() => buildRequest().state") == {"structured": True}
    page.evaluate("state => setRequest({state, questions: {}})", literal)
    page.fill("#stateInput", '{"edited": true}')
    assert page.evaluate("() => buildRequest().state") == {"edited": True}


def test_edit_json_rebuilds_the_builder_and_keeps_invalid_json_open(page):
    view(page, "playground")
    page.check("#editJsonToggle")
    page.fill("#requestJson", "{ invalid")
    page.click("#editJsonToggle")  # stays checked: the toggle refuses to leave invalid JSON
    sync_api.expect(page.locator("#editJsonToggle")).to_be_checked()
    sync_api.expect(page.locator("#editJsonArea")).to_be_visible()
    sync_api.expect(page.locator("#mediaError")).to_contain_text("Invalid request JSON")
    request = {
        "state": "builder from edited JSON",
        "questions": {"priority": {"type": "choice", "criteria": ["low", "high"]}},
        "facts": "auto",
        "lang": "pl",
    }
    page.fill("#requestJson", json.dumps(request))
    page.uncheck("#editJsonToggle")
    sync_api.expect(page.locator("#builder")).to_be_visible()
    assert page.evaluate("() => buildRequest()")["questions"] == request["questions"]
    assert page.evaluate("() => buildRequest().state") == request["state"]


def test_the_builder_switches_types_and_edits_a_cost_matrix(page):
    view(page, "playground")
    page.evaluate("() => setRequest({state: 'Paczka nie dotarła.', questions: {}})")
    page.evaluate("() => addQuestion()")
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
    cell = row.locator(".cost-cell").first
    cell.fill("")
    assert page.evaluate("() => document.querySelector('.question')._error") == "every cost cell needs a number"
    costs = page.evaluate("() => buildRequest().questions.question.costs")
    assert "late" not in costs["human"]
    sync_api.expect(card.locator(".q-error")).to_contain_text("every cost cell needs a number")
    card.locator(".q-type").select_option("tool")
    card.locator('[data-f="tools"]').fill("not json")
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text("tools must be a JSON array")


def test_safety_strip_and_inspect_button_on_a_decision(page, ui, text_model):
    view(page, "playground")
    page.evaluate("r => setRequest(r)", CHOICE)
    page.click("#sendButton")
    strip = page.locator("#results .chips.safety")
    sync_api.expect(strip.first).to_contain_text(
        re.compile("Automatable|Below the certified|No certificate|uncalibrated|calibrated")
    )
    sync_api.expect(page.locator("#inspectDecision")).to_be_visible()
    if "context" in (ui.model(text_model["id"])["entry"] or {}):  # the meter needs the engine's context policy
        sync_api.expect(page.locator("#results")).to_contain_text("Longest prompt")


def test_the_load_options_dialog_lists_the_modes(page, text_model):
    view(page, "overview")  # the loaded model's ⚙: restart with other options
    page.click(f'#loadedModels .options-model[data-id="{text_model["id"]}"]')
    dialog = page.locator("#loadOptions")
    sync_api.expect(dialog).to_be_visible()
    sync_api.expect(page.locator("#optionsApply")).to_have_text("Restart")
    sync_api.expect(page.locator("#optionsSetupNote")).to_be_hidden()
    sync_api.expect(dialog.locator(f'input[name="optMode"][value="{text_model["mode"]}"]')).to_be_checked()
    assert dialog.locator('input[name="optMode"]').count() == 16
    assert dialog.locator('input[name="optMode"][disabled]').count() >= 1
    sync_api.expect(dialog.locator("#optExperimentalNote")).not_to_be_empty()
    for key, modes in (("gemma_attn", "fast"), ("eager_attn", "eager"), ("merge", "fast"), ("ple_offload", "eager")):
        field = dialog.locator(f'[data-opt="{key}"]')  # each switch shows for the backend that reads it
        assert modes in field.get_attribute("data-modes").split() and (
            "eager" in field.get_attribute("data-modes").split()
        ) == (modes == "eager")
        if text_model["mode"] not in field.get_attribute("data-modes").split():
            sync_api.expect(field).to_be_hidden()
    page.click("#optionsCancel")
    sync_api.expect(dialog).to_be_hidden()


def test_models_set_up_without_loading_and_the_model_manager_loads(page, ui, text_model):
    view(page, "manager")  # the fixture loaded the model: its row on disk offers Unload
    row = page.locator("#diskModels .disk-row", has=page.locator(f'[data-id="{text_model["id"]}"]'))
    sync_api.expect(row.locator(".unload-model")).to_be_visible()
    view(page, "models")
    page.click("#allModelsTab")
    assert page.locator("#models .load-model").count() == 0  # Models downloads, sets up and deletes
    loads = []
    page.route("**/api/load", lambda route: (loads.append(1), route.fulfill(status=500, body="loaded")))
    page.click(f'#modelsTable .options-model[data-action="save"][data-id="{text_model["id"]}"]')
    sync_api.expect(page.locator("#optionsApply")).to_have_text("Save")
    sync_api.expect(page.locator("#optionsSetupNote")).to_be_visible()
    sync_api.expect(page.locator("#optionsSetupNote")).to_contain_text("Media loads choose their mode")
    sync_api.expect(page.locator("#optionsRemember")).to_be_hidden()
    page.fill("#opt-revision", "main")
    page.click("#optionsApply")
    sync_api.expect(page.locator("#loadOptions")).to_be_hidden()
    try:
        saved = ui.get("/api/models/options", params={"id": text_model["id"]})
        assert saved["saved"] and saved["options"]["revision"] == "main" and not loads
    finally:
        ui.put("/api/models/options", {"id": text_model["id"], "options": {}})


def test_models_tabs_and_manager_loaded_section(page, text_model):
    view(page, "manager")
    positions = page.evaluate("""() => ['statsTable','benchmarks','calibrationBlock','diskModels','modelsDir']
      .map(id => document.getElementById(id).getBoundingClientRect().top)""")
    assert positions == sorted(positions)
    view(page, "models")
    assert page.locator('#models [role="tab"]').all_text_contents() == ["Suggested", "All models"]
    sync_api.expect(page.locator("#suggestedTab")).to_have_attribute("aria-selected", "true")
    page.evaluate("localStorage.setItem('basal-ui.tab', 'loaded')")
    page.reload()
    page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
    view(page, "models")
    sync_api.expect(page.locator("#suggestedTab")).to_have_attribute("aria-selected", "true")


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


# the language buttons reload the page: until the new page's scripts run, LANG and statusCache do not exist
SWITCHED = """lang => typeof LANG !== 'undefined' && typeof statusCache !== 'undefined' && LANG === lang
  && !sessionStorage.getItem('basal-ui.language-draft') && statusCache && modelData.length > 0"""


def test_playground_builder_draft_survives_language_changes_once(page, text_model):
    view(page, "playground")
    request = {
        "model": text_model["id"],
        "state": "Review this order",
        "facts": "auto",
        "lang": "en",
        "questions": {
            "decision": {"type": "choice", "instructions": "What next?", "criteria": {"buy": "Buy", "wait": "Wait"}},
            "risk": {"type": "noul", "instructions": "Is it risky?"},
        },
        "media": [{"type": "image", "data": "aGVsbG8="}],
    }
    page.evaluate("r => setRequest(r)", request)
    page.locator('[data-lang-choice="pl"]').click()
    page.wait_for_function(SWITCHED, arg="pl")
    assert page.evaluate(
        "() => [selectedModel, $('#stateInput').value, $('#factsToggle').checked, $('#langSelect').value]"
    ) == [text_model["id"], "Review this order", True, "en"]
    assert page.evaluate(
        "() => JSON.parse(buildRequest().questions.decision.criteria ? JSON.stringify(buildRequest().questions.decision.criteria) : '{}')"
    ) == {"buy": "Buy", "wait": "Wait"}
    assert page.evaluate("() => selectedMedia.length") == 0
    sync_api.expect(page.locator("#mediaError")).to_contain_text("dołącz media ponownie")
    page.locator('[data-lang-choice="en"]').click()
    page.wait_for_function(SWITCHED, arg="en")
    assert page.evaluate(
        "() => [selectedModel, $('#stateInput').value, $('#factsToggle').checked, $('#langSelect').value]"
    ) == [text_model["id"], "Review this order", True, "en"]
    assert page.evaluate("() => buildRequest().questions.decision.criteria") == {"buy": "Buy", "wait": "Wait"}
    assert page.evaluate("() => Object.keys(buildRequest().questions)") == ["decision", "risk"]
    assert page.evaluate("() => selectedMedia.length") == 0
    page.reload()
    page.wait_for_function("() => statusCache && modelData.length > 0")
    sync_api.expect(page.locator("#stateInput")).to_have_value("")


def test_playground_edit_json_draft_keeps_invalid_text(page):
    view(page, "playground")
    page.check("#editJsonToggle")
    text = '{"state": "unfinished", "questions": ['
    page.fill("#requestJson", text)
    page.locator('[data-lang-choice="pl"]').click()
    page.wait_for_function(SWITCHED, arg="pl")
    sync_api.expect(page.locator("#requestJson")).to_have_value(text)
    assert page.locator("#editJsonToggle").is_checked()
    page.reload()
    page.wait_for_function("() => statusCache && modelData.length > 0")
    assert not page.locator("#editJsonToggle").is_checked()
    assert "unfinished" not in page.locator("#requestJson").input_value()
    sync_api.expect(page.locator("#builder")).to_be_visible()


@pytest.mark.parametrize("draft_case", ["blank-name", "duplicate-names", "invalid-tools", "invalid-schema"])
def test_playground_incomplete_builder_draft_survives_language_switch_once(page, draft_case):
    view(page, "playground")
    if draft_case == "blank-name":
        page.evaluate(
            "() => { $('#questions').innerHTML = ''; addQuestion('first', {type:'choice', criteria:{yes:'Yes'}}); $('.q-name').value = ''; }"
        )
    elif draft_case == "duplicate-names":
        page.evaluate(
            "() => { $('#questions').innerHTML = ''; addQuestion('same', {type:'choice', criteria:{yes:'Yes'}}); addQuestion('same', {type:'noul'}); }"
        )
    elif draft_case == "invalid-tools":
        page.evaluate(
            "() => { $('#questions').innerHTML = ''; addQuestion('tools', {type:'tool', tools:[{name:'lookup'}]}); }"
        )
        page.fill('.question [data-f="tools"]', '{"unfinished":')
    else:
        page.evaluate(
            "() => { $('#questions').innerHTML = ''; addQuestion('schema', {type:'extract', schema:{type:'object', properties:{}}}); }"
        )
        page.fill('.question [data-f="schema"]', '{"unfinished":')

    page.locator('[data-lang-choice="pl"]').click()
    page.wait_for_function(SWITCHED, arg="pl")
    if draft_case == "blank-name":
        sync_api.expect(page.locator(".question .q-name")).to_have_value("")
    elif draft_case == "duplicate-names":
        assert page.locator(".question .q-name").evaluate_all("(els) => els.map(el => el.value)") == ["same", "same"]
    elif draft_case == "invalid-tools":
        sync_api.expect(page.locator('.question [data-f="tools"]')).to_have_value('{"unfinished":')
    else:
        sync_api.expect(page.locator('.question [data-f="schema"]')).to_have_value('{"unfinished":')

    page.reload()
    page.wait_for_function("() => statusCache && modelData.length > 0")
    sync_api.expect(page.locator("#stateInput")).to_have_value("")
    assert page.locator(".question .q-name").evaluate_all("(els) => els.map(el => el.value)") == ["question"]
    assert not page.locator("#mediaError").is_visible()


def test_playground_language_draft_preserves_blank_act_matrix_rows(page):
    view(page, "playground")
    page.evaluate(
        "() => { $('#questions').innerHTML = ''; addQuestion('decision', {type:'act', criteria:{buy:'Buy', wait:'Wait'}, costs:{wrong:10}}); }"
    )
    page.locator('.question [data-cost-mode="matrix"]').click()
    page.locator(".question .cost-action").nth(0).fill("")
    page.locator(".question .cost-action").nth(1).fill("custom")
    page.locator(".question tbody tr").nth(1).locator(".cost-cell").nth(0).fill("17")
    page.locator(".question tbody tr").nth(1).locator(".cost-cell").nth(1).fill("23")

    page.locator('[data-lang-choice="pl"]').click()
    page.wait_for_function(SWITCHED, arg="pl")
    assert page.locator(".question .cost-action").evaluate_all("(els) => els.map(el => el.value)") == ["", "custom"]
    assert page.locator(".question tbody tr").evaluate_all(
        "(rows) => rows.map(row => [...row.querySelectorAll('.cost-cell')].map(cell => cell.value))"
    ) == [["0", "10"], ["17", "23"]]


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
    request = {**CHOICE, "state": "Umowa podpisana 3 marca 2025.", "facts": "auto", "lang": "pl"}
    page.evaluate("r => setRequest(r)", request)
    page.click("#sendButton")
    page.click("#inspectDecision")
    sync_api.expect(page.locator("#pageTitle")).to_have_text("Inspect")
    sync_api.expect(frame.locator("#out .result").first).to_be_visible()
    sync_api.expect(frame.locator("#out")).to_contain_text("Do którego działu")
    sync_api.expect(frame.locator("#out .result").first).to_contain_text("Polish prompt")
    sync_api.expect(frame.locator("#out .result .prompt")).to_contain_text("Fakty pomocnicze")
    notice = page.locator("#inspectNotice")
    sync_api.expect(notice).to_be_visible()
    if page.input_value("#playgroundModel") != bf16_model["id"]:  # the served decision came from other weights
        sync_api.expect(notice).to_contain_text("weights: served")
    page.click("#stopInspect")
    sync_api.expect(frame_el).to_be_hidden()
    assert ui.get("/api/inspect")["state"] == "idle"


def test_the_inspect_handoff_names_every_difference_and_refuses_what_it_cannot_explain(page, text_model):
    """inspectHandoff on a served model whose engine, option orders and temperatures differ from the inspector's."""
    probe = """([model, request]) => {
      const saved = statusCache, served = statusCache.models.find(x => x.id === model);
      statusCache = {...saved, inspect: {model, state: 'ready', mode: 'eager', device: 'mps', orders: 1,
                                         temperatures: {choice: 9}},
                     models: [{...served, mode: 'mlx', options: {think: true},
                               entry: {...served.entry, orders: 2, temperature_per_prim: {choice: 1.2}}}]};
      try { return inspectHandoff({model, ...request}); } finally { statusCache = saved; }
    }"""
    mixed = {
        "state": "s",
        "think": "auto",
        "early_exit": "0.99",
        "facts": "auto",
        "questions": {
            "dept": {"type": "choice", "criteria": {"a": "A", "b": "B"}},
            "intent": {"type": "classify", "labels": {"x": "X", "y": "Y"}},
        },
    }
    handoff = page.evaluate(probe, [text_model["id"], mixed])
    assert list(handoff["body"]["questions"]) == ["dept"] and handoff["body"]["facts"] == "auto"
    assert not {"model", "think", "early_exit"} & set(handoff["body"])
    notes = " | ".join(handoff["notes"])
    for part in (
        "intent: task questions are left out",
        "engine: served mlx, explained eager on mps",
        "option orders: served 2, explained 1",
        "calibration: the served temperatures differ",
        "thinking: explained without the reasoning",
        "early exit 0.99",
    ):
        assert part in notes, notes
    assert (
        "remove the media to inspect this request"
        in page.evaluate(probe, [text_model["id"], {**mixed, "media": [{"type": "image"}]}])["blocked"]
    )
    only_tasks = {"state": "s", "questions": {"intent": mixed["questions"]["intent"]}}
    assert "only task questions" in page.evaluate(probe, [text_model["id"], only_tasks])["blocked"]
    other = page.evaluate(
        """([model, request]) => {
      const saved = statusCache; statusCache = {...saved, inspect: {model: 'Remek/basal-1.0-max', state: 'ready'}};
      try { return inspectHandoff({model, ...request}); } finally { statusCache = saved; }
    }""",
        [text_model["id"], mixed],
    )
    assert "the inspector runs Remek/basal-1.0-max" in other["blocked"]


def test_the_tool_builder_sets_the_selection_of_several_tools(page):
    view(page, "playground")
    tools = [{"name": "refund", "description": "zwrot"}, {"name": "track", "description": "śledzenie"}]
    request = {
        "state": "Chcę zwrotu i numeru przesyłki.",
        "questions": {
            "next": {
                "type": "tool",
                "tools": tools,
                "multi": True,
                "objective": "f1",
                "min": 1,
                "max": 2,
                "exclusive": ["refund"],
                "fallback": "track",
                "fallback_threshold": 0.3,
            }
        },
    }
    page.evaluate("r => setRequest(r)", request)
    assert page.evaluate("() => buildRequest().questions") == request["questions"]
    card = page.locator(".question").first
    sync_api.expect(card.locator(".multi-tool-only")).to_be_visible()
    card.locator('[data-f="max"]').fill("1")
    card.locator('[data-f="objective"]').select_option("threshold")
    card.locator('[data-f="threshold"]').fill("0.6")
    built = page.evaluate("() => buildRequest().questions.next")
    assert built["max"] == 1 and built["threshold"] == 0.6 and "objective" not in built
    assert built["exclusive"] == ["refund"] and built["fallback"] == "track"
    card.locator('[data-f="multi"]').uncheck()
    sync_api.expect(card.locator(".multi-tool-only")).to_be_hidden()
    built = page.evaluate("() => buildRequest().questions.next")
    assert not {"multi", "min", "max", "exclusive", "fallback", "threshold"} & set(built)


def test_the_calibration_view_imports_a_file_fitted_elsewhere(page, ui, text_model, tmp_path):
    shipped = ui.get("/api/calibrations", params={"id": text_model["id"]})["shipped"]["file"]
    view(page, "manager")  # Loaded section contains Calibration
    page.select_option("#calibrationModel", text_model["id"])
    page.fill("#calibrationImportPath", shipped)
    sync_api.expect(page.locator("#calibrationFiles table")).to_be_visible()
    before = page.locator("#calibrationFiles .chip", has_text="imported").count()
    page.click("#importCalibration")
    sync_api.expect(page.locator("#calibrationImportPath")).to_have_value("")  # done: the field is cleared
    sync_api.expect(page.locator("#calibrationFiles .chip", has_text="imported")).to_have_count(before + 1)
    row = page.locator("#calibrationFiles tbody tr", has=page.locator(".chip", has_text="imported")).first
    sync_api.expect(row.locator("td").nth(3)).to_contain_text("5%")  # the shipped file's certified thresholds
    page.fill("#calibrationImportPath", str(tmp_path / "none.json"))
    page.click("#importCalibration")
    sync_api.expect(page.locator(".toast.error").last).to_contain_text("cannot read")


def test_theme_toggle_persists_and_sets_the_document_theme(page):
    page.click('[data-theme-choice="dark"]')
    assert page.evaluate("() => [document.documentElement.dataset.theme, localStorage.getItem('basal-ui.theme')]") == [
        "dark",
        "dark",
    ]


def test_control_enter_sends_a_playground_decision(page):
    view(page, "playground")
    page.evaluate("""() => {
      api = async (path, options) => {
        window.sentDecision = path === '/api/decide' && options.method === 'POST';
        return {answers: {q: {type: 'choice', choice: 'yes'}}, usage: {}};
      };
      $('#editJsonToggle').checked = true;
      $('#requestJson').value = JSON.stringify({state: 'state', questions: {q: {type: 'choice', criteria: ['yes']}}});
    }""")
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("Control+Enter")
    page.wait_for_function("() => window.sentDecision === true")


def test_question_mark_opens_shortcut_help(page):
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("?")
    sync_api.expect(page.locator("#shortcutsDialog")).to_be_visible()
    sync_api.expect(page.locator("#shortcutsDialog")).to_contain_text("Ctrl/⌘ + Enter")


def test_alt_digit_opens_the_nth_page_by_key_code(page):
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("Alt+2")
    sync_api.expect(page.locator("#pageTitle")).to_have_text("Models")
    # macOS Option+3 types "£" / "#": the key changes with the layout, the code does not
    page.evaluate("""() => document.body.dispatchEvent(new KeyboardEvent('keydown',
      {key: '£', code: 'Digit3', altKey: true, bubbles: true}))""")
    sync_api.expect(page.locator("#pageTitle")).to_have_text("Model manager")


CENTRED = """sel => {  // where an open modal dialog sits in the viewport
  const d = document.querySelector(sel), r = d.getBoundingClientRect();
  return {open: d.open, shown: r.width > 0 && r.height > 0,
          inside: r.left >= 0 && r.top >= 0 && r.right <= innerWidth + 0.5 && r.bottom <= innerHeight + 0.5,
          dx: Math.round((r.left + r.right) / 2 - innerWidth / 2), dy: Math.round((r.top + r.bottom) / 2 - innerHeight / 2)};
}"""
DIALOGS = {  # every modal of basal-ui and how it opens (without waiting on the user)
    "#confirmDialog": "() => { void confirmDialog({title: 'Delete?', description: 'x', confirmLabel: 'Delete'}); }",
    "#modelCardDialog": "id => openModelCard(id)",
    "#loadOptions": "id => openLoadOptions(id, false, 'save')",
    "#shortcutsDialog": "() => $('#shortcutsDialog').showModal()",
    "#presetNameDialog": "() => $('#presetNameDialog').showModal()",
    "#reportDialog": "() => { $('#reportText').textContent = 'report'; $('#reportDialog').showModal(); }",
    "#backendTestDialog": "() => $('#backendTestDialog').showModal()",
    "#mediaLightbox": "() => $('#mediaLightbox').showModal()",
}


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_every_modal_opens_centred_in_the_viewport_from_any_view(page, text_model, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    view(page, "overview")  # the calibration report lives in Model manager: it must show from any view
    for selector, open_it in DIALOGS.items():
        page.evaluate(open_it, text_model["id"])
        page.wait_for_function("s => document.querySelector(s).open", arg=selector)
        page.wait_for_timeout(250)  # the model card and load options fade in
        assert page.evaluate(CENTRED, selector) == {"open": True, "shown": True, "inside": True, "dx": 0, "dy": 0}, (
            selector
        )
        page.evaluate("s => document.querySelector(s).close()", selector)


DIALOG_LAYOUT = """([dialog, body, header, footer]) => {
  const d = document.querySelector(dialog), b = document.querySelector(body), r = d.getBoundingClientRect();
  const top = document.querySelector(header).getBoundingClientRect(), bottom = document.querySelector(footer).getBoundingClientRect();
  const before = [top.top, bottom.bottom]; b.scrollTop = b.scrollHeight;
  const after = [document.querySelector(header).getBoundingClientRect().top, document.querySelector(footer).getBoundingClientRect().bottom];
  return {inViewport: r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth,
          centred: Math.abs((r.left + r.right) / 2 - innerWidth / 2) < 2, dialogScrolls: d.scrollHeight > d.clientHeight + 1,
          bodyScrolls: b.scrollHeight > b.clientHeight && b.scrollTop > 0, fixedEnds: before.every((v, i) => Math.abs(v - after[i]) < 1),
          footerShown: bottom.bottom <= innerHeight};
}"""


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_options_dialog_keeps_its_header_and_actions_while_the_options_scroll(page, text_model, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.evaluate("id => openLoadOptions(id, false, 'save')", text_model["id"])
    sync_api.expect(page.locator("#loadOptions")).to_be_visible()
    page.evaluate(
        "() => document.querySelectorAll('#optionsForm details').forEach(d => { d.open = true; })"
    )  # all expanded
    page.wait_for_timeout(500)  # Basecoat animates a <details> opening
    layout = page.evaluate(
        DIALOG_LAYOUT, ["#loadOptions", "#optionsForm", "#loadOptions > h3", "#loadOptions .dialog-actions"]
    )
    assert layout == {
        "inViewport": True,
        "centred": True,
        "dialogScrolls": False,
        "bodyScrolls": True,
        "fixedEnds": True,
        "footerShown": True,
    }
    switches = [s.bounding_box() for s in page.locator("#optionsForm input[type=checkbox]:visible").all()]
    assert switches and all(s["height"] < 32 for s in switches)  # the compact field sizing leaves the switches alone
    page.click("#optionsCancel")
    page.evaluate("id => openLoadOptions(id, false, 'save')", text_model["id"])
    assert page.evaluate("() => $('#optionsForm').scrollTop") == 0  # a reopened dialog starts at the top


def test_model_card_keeps_its_header_while_the_card_scrolls(page, text_model):
    page.set_viewport_size({"width": 1440, "height": 700})
    page.evaluate("id => openModelCard(id)", text_model["id"])
    sync_api.expect(page.locator("#modelCardDialog")).to_be_visible()
    page.evaluate("() => document.querySelectorAll('#modelCardBody details').forEach(d => { d.open = true; })")
    page.wait_for_timeout(500)  # Basecoat animates a <details> opening
    layout = page.evaluate(
        DIALOG_LAYOUT, ["#modelCardDialog", "#modelCardBody", ".model-card-head", ".model-card-head"]
    )
    assert layout["inViewport"] and layout["centred"] and not layout["dialogScrolls"]
    assert layout["bodyScrolls"] and layout["fixedEnds"]
    title = page.locator("#modelCardTitle").inner_text()
    assert title.count("·") == 2 and title.split(" · ")[0] == text_model["family"]  # family · tier · format, once
    facts = page.locator(".model-card-facts dt").all_inner_texts()
    assert not {"Family", "Tier", "Port"} & set(facts)


OPEN_SECTIONS = "() => [...document.querySelectorAll('#optionsForm details.option-section')].filter(d => d.open).map(d => d.dataset.section)"


def test_options_sections_collapse_open_on_changes_and_remember_the_users_choice(page, text_model):
    page.evaluate("id => openLoadOptions(id, false, 'save')", text_model["id"])
    sync_api.expect(page.locator("#loadOptions")).to_be_visible()
    sections = page.evaluate(
        "() => [...document.querySelectorAll('#optionsForm details.option-section')].map(d => d.dataset.section)"
    )
    assert len(sections) >= 6 and "Batching" in sections
    assert page.evaluate(OPEN_SECTIONS) == ["Engine"]  # defaults: only the engine is open
    batch = page.locator('#optionsForm details[data-section="Batching"]')
    sync_api.expect(batch.locator("#opt-max_batch")).to_be_hidden()
    # a saved non-default value opens its section
    page.evaluate("() => renderOptionsForm({...optionsState.data.defaults, max_batch: 8})")
    assert page.evaluate(OPEN_SECTIONS) == ["Engine", "Batching"]
    # a collapsed section still saves what it holds
    page.evaluate("() => { $('#opt-max_batch').value = '12'; }")
    batch.locator("summary").click()  # collapse it
    sync_api.expect(batch.locator("#opt-max_batch")).to_be_hidden()
    assert page.evaluate("() => collectOptions().max_batch") == 12
    # the user's toggles are kept for the next dialog (also for another model), the defaults are not
    page.locator('#optionsForm details[data-section="Engine"] > summary').click()
    page.locator('#optionsForm details[data-section="Caching"] > summary').press("Enter")
    page.click("#optionsCancel")
    page.evaluate("id => openLoadOptions(id, false, 'save')", text_model["id"])
    assert page.evaluate(OPEN_SECTIONS) == ["Caching"]
    page.click("#optionsCancel")


def test_model_card_long_blocks_start_collapsed(page, text_model):
    page.evaluate("id => openModelCard(id)", text_model["id"])
    sync_api.expect(page.locator("#modelCardDialog")).to_be_visible()
    blocks = page.locator("#modelCardBody details")
    assert blocks.count() >= 1
    assert page.evaluate("() => [...document.querySelectorAll('#modelCardBody details')].every(d => !d.open)")
    sync_api.expect(blocks.first.locator("pre")).to_be_hidden()
    blocks.first.locator("summary").click()
    sync_api.expect(blocks.first.locator("pre")).to_be_visible()
    sync_api.expect(page.locator("#modelCardBody .model-card-facts")).to_be_visible()  # the facts stay in view


def test_model_card_dialog_renders_api_card_data(page):
    page.evaluate("""() => {
      api = async () => ({
        id: 'community/model', repo: 'community/model', url: 'https://huggingface.co/community/model',
        label: 'Model label', author: 'community', community: true, base_repo: 'base/model',
        family: 'basal-2.0', tier: 'mini', port: 'MLX', size_bytes: 1000, media: [],
        downloaded: true, revisions: ['abc'], local_dir: '/models/model', basal_json: {prompt_format: 'chat'},
        shipped_calibration: {thresholds: {'0.01': {choice: 0.9}}},
        assessment: {mode: 'mlx', runnable: true, fit: 'fits', fit_now: 'fits', est_memory_bytes: 2000},
        loaded: null, readme: 'A model README.'
      });
      openModelCard('community/model');
    }""")
    sync_api.expect(page.locator("#modelCardDialog")).to_contain_text("Model label")
    sync_api.expect(page.locator("#modelCardDialog")).to_contain_text("base/model")
    sync_api.expect(page.locator("#modelCardDialog")).to_contain_text("A model README.")


def test_result_views_drop_descriptions_that_only_repeat_the_key(page):
    criteria = {"88412": "88412", "4": "[4] Payment status", "7": "[7]", "9": "[9]x", "none": "Nie podano numeru"}
    page.evaluate(
        """c => renderResults({answers: {q: {type: 'choice', choice: '4', confidence: 0.6,
      probabilities: {'4': 0.6, '88412': 0.2, '7': 0.1, '9': 0.05, none: 0.05}}}},
      {questions: {q: {type: 'choice', criteria: c}}})""",
        criteria,
    )
    sync_api.expect(page.locator("#results .answer .headline")).to_have_text("4 · Payment status")
    bars = page.locator("#results .prob > span").all_inner_texts()
    assert bars == ["4 · Payment status", "88412", "7", "9 · [9]x", "none · Nie podano numeru"]
    assert "4 · [4]" not in page.locator("#results").inner_text()


ANSWERING = """([model, batch]) => {  // the status of this basal-ui with requests and a batch in flight (no polling)
  const s = structuredClone(statusCache), m = s.models.find(x => x.id === model);
  m.inflight = [{source: 'playground', seconds: 2.4}, {source: 'api', seconds: 0.3}];
  m.health = {reachable: true, running: 2, queued: 1};
  s.jobs = batch ? {batch: {kind: 'batch', running: true, model, phase: 'running', phase_code: 'phase_running',
                            phase_params: {}, done: 3, total: 10}} : {};
  renderStatus(s); clearTimeout(pollTimer);
}"""


def test_overview_shows_the_inference_in_flight_and_idle_otherwise(page, text_model):
    view(page, "overview")
    order = page.evaluate("""() => ['#hardware', '#inference', '#status', '#overview > .dashboard-grid']
      .map(s => document.querySelector(s).getBoundingClientRect().top)""")
    assert order == sorted(order) and len(set(order)) == 4  # Hardware, then Inference, Loaded models, the summaries
    rows = page.locator("#inferenceNow .inference-row")
    sync_api.expect(rows).to_have_count(1)
    sync_api.expect(rows.first).to_contain_text("Idle")
    assert page.locator("#inferenceNow .status-dot.active").count() == 0
    page.evaluate(ANSWERING, [text_model["id"], True])
    sync_api.expect(rows).to_have_count(2)
    sync_api.expect(rows.nth(0)).to_contain_text(text_model["id"])
    sync_api.expect(rows.nth(0)).to_contain_text("2 running · 1 queued · Playground 1 · API server 1 · longest 2.4 s")
    sync_api.expect(rows.nth(1)).to_contain_text("Batch")
    sync_api.expect(rows.nth(1)).to_contain_text("3 / 10")
    assert rows.nth(1).locator(".progress span").get_attribute("style") == "width:30%"
    assert page.locator("#inferenceNow .status-dot.active").count() == 2
    sync_api.expect(
        page.locator(f'#loadedModels .model-row:has-text("{text_model["id"]}") .status-dot.active')
    ).to_have_count(1)
    sync_api.expect(page.locator("#footerState")).to_contain_text("1 answering")
    assert "active" in page.locator("#footerDot").get_attribute("class")
    # basal-serve's counters not read yet (zeros) while basal-ui waits on requests: the requests are what is shown
    page.evaluate(
        """m => { const s = structuredClone(statusCache); const x = s.models.find(y => y.id === m);
      x.inflight = [{source: 'playground', seconds: 0.5}, {source: 'playground', seconds: 0.2}];
      x.health = {reachable: true, running: 0, queued: 0}; s.jobs = {}; renderStatus(s); clearTimeout(pollTimer); }""",
        text_model["id"],
    )
    sync_api.expect(rows).to_have_count(1)
    sync_api.expect(rows.first).to_contain_text("2 requests · Playground 2 · longest 0.5 s")
    assert "running" not in rows.first.inner_text()
    page.evaluate(
        """m => { const s = structuredClone(statusCache); const x = s.models.find(y => y.id === m);
      x.inflight = []; x.health = {reachable: true, running: 0, queued: 0}; s.jobs = {}; renderStatus(s); clearTimeout(pollTimer); }""",
        text_model["id"],
    )
    sync_api.expect(rows).to_have_count(1)
    sync_api.expect(rows.first).to_contain_text("Idle")
    assert page.locator(".status-dot.active").count() == 0
    assert "answering" not in page.locator("#footerState").inner_text()


def test_overview_inference_after_a_decision_and_in_polish(page, ui, text_model):
    ui.decide(text_model["id"], CHOICE)
    view(page, "overview")
    sync_api.expect(page.locator("#inferenceNow")).to_contain_text("last decision")  # the idle line says when
    assert page.evaluate("() => !$('#overview').hidden")
    page.evaluate("localStorage.setItem('basal-ui.lang', 'pl')")
    page.reload()
    page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache?.models?.length > 0")
    page.evaluate(ANSWERING, [text_model["id"], True])
    rows = page.locator("#inferenceNow .inference-row")
    sync_api.expect(rows.nth(0)).to_contain_text(
        "2 w toku · 1 w kolejce · Playground 1 · Serwer API 1 · najdłuższe 2.4 s"
    )
    sync_api.expect(page.locator("#inference .section-index")).to_have_text("WNIOSKOWANIE")


def test_a_running_batch_shows_on_the_overview(page, ui, text_model, tmp_path):
    many = tmp_path / "many.jsonl"
    many.write_text((EXAMPLES / "questions.jsonl").read_text() * 50)
    ui.post(
        "/api/batch",
        {"id": text_model["id"], "input": str(many), "output": str(tmp_path / "out.jsonl"), "concurrency": 1},
    )
    try:
        view(page, "overview")
        row = page.locator("#inferenceNow .inference-row", has_text="Batch")
        sync_api.expect(row).to_be_visible(timeout=10_000)
        sync_api.expect(row).to_contain_text(" / 1000")  # done / total of the real job
    finally:
        ui.delete("/api/batch")
        ui.wait_job("batch")


def test_usage_line_includes_branches_and_lane(page):
    page.evaluate("""() => renderResults(
      {answers: {q: {type: 'choice', choice: 'yes'}},
       usage: {latency_ms: 1, branches: 3, second_order_branches: 2, lane: 'long'}},
      {model: selectedModel, questions: {q: {type: 'choice'}}}
    )""")
    line = page.locator("#results .answer-meta").first
    sync_api.expect(line).to_contain_text("branches: 3")
    sync_api.expect(line).to_contain_text("second_order_branches: 2")
    sync_api.expect(line).to_contain_text("lane: long")


def test_load_preset_save_and_apply_round_trip(page, text_model):
    view(page, "overview")
    page.click(f'#loadedModels [data-id="{text_model["id"]}"].options-model')
    dialog = page.locator("#loadOptions")
    sync_api.expect(dialog).to_be_visible()
    page.fill("#opt-revision", "test-revision")
    page.click("#saveLoadPreset")
    page.fill("#presetName", "e2e-load-preset")
    page.click("#presetNameSave")
    sync_api.expect(dialog).to_contain_text("e2e-load-preset")
    page.select_option("#loadPresetSelect", "e2e-load-preset")
    page.fill("#opt-revision", "")
    page.click("#applyLoadPreset")
    sync_api.expect(page.locator("#opt-revision")).to_have_value("test-revision")
    page.click("#deleteLoadPreset")
    sync_api.expect(dialog.locator("#loadPresetSelect")).not_to_contain_text("e2e-load-preset")
    page.click("#optionsCancel")


def test_revision_option_is_shown_as_a_loaded_model_chip(page, text_model):
    page.evaluate(
        """id => {
      const model = statusCache.models.find(x => x.id === id);
      model.options ||= {};
      model.options.revision = 'release-1';
      renderLoadedRows(statusCache.models);
    }""",
        text_model["id"],
    )
    sync_api.expect(page.locator("#loadedModels")).to_contain_text("revision release-1")
    view(page, "models")
    page.evaluate(
        """id => renderModels({suggestions: [], models: modelData.map(m => m.id === id
      ? {...m, revision: 'release-1'} : m)})""",
        text_model["id"],
    )
    row = page.locator("#modelsTable tr", has=page.locator(f'[data-id="{text_model["id"]}"]'))
    sync_api.expect(row.first).to_contain_text("revision release-1")
