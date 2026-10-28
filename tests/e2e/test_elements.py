"""Playground screenshot element editing, serialization and evidence behavior."""

import io
import json
import struct
import wave
import zlib

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and run: playwright install chromium")


def png(width=1000, height=600):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row = b"\x00" + b"\xf0\xf0\xf0" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def wav():
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 160)
    return out.getvalue()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, ui, text_model):
    ui.load(text_model["id"])
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.goto(ui.url + "/#playground")
    tab.wait_for_function(
        "() => typeof selectedMedia !== 'undefined' && document.querySelector('#playgroundModel').options.length"
    )
    tab.select_option("#playgroundModel", text_model["id"])
    tab.evaluate("() => { loadedMedia=['image']; $('#mediaInput').disabled=false; $('#mediaField').hidden=false; }")
    tab.set_input_files(
        "#mediaInput",
        [
            {"name": "screen.png", "mimeType": "image/png", "buffer": png()},
            {"name": "second.png", "mimeType": "image/png", "buffer": png()},
        ],
    )
    tab.wait_for_function("() => selectedMedia.length === 2")
    yield tab
    context.close()
    assert not errors, errors


def canvas_point(page, x, y):
    box = page.locator(".elements-dialog canvas").bounding_box()
    return box["x"] + x * box["width"] / 1000, box["y"] + y * box["height"] / 600


def mark_image(page, index=0, label=""):
    page.locator(f'[data-mark="{index}"]').click()
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width === 1000")
    a, b = canvas_point(page, 80, 70), canvas_point(page, 230, 190)
    page.mouse.move(*a)
    page.mouse.down()
    page.mouse.move(*b)
    page.mouse.up()
    if label:
        page.locator("[data-label='0']").fill(label)
    page.locator(".elements-save").click()
    page.locator(".elements-dialog").wait_for(state="detached")


PIXEL = """([x, y]) => [...document.querySelector('.elements-dialog canvas').getContext('2d').getImageData(x, y, 1, 1).data]"""


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_element_editor_opens_centred_in_the_viewport(page, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.locator(".mark-elements").first.click()
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width === 1000")
    box = page.evaluate("""() => { const r = document.querySelector('.elements-dialog').getBoundingClientRect();
      return {inside: r.left >= 0 && r.top >= 0 && r.right <= innerWidth + 0.5 && r.bottom <= innerHeight + 0.5,
              dx: Math.round((r.left + r.right) / 2 - innerWidth / 2), dy: Math.round((r.top + r.bottom) / 2 - innerHeight / 2)}; }""")
    assert box == {"inside": True, "dx": 0, "dy": 0}
    page.locator(".elements-cancel").click()


def test_element_editor_shows_the_image_and_badges_where_save_paints_them(page):
    page.locator(".mark-elements").first.click()
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width === 1000")
    assert page.evaluate(PIXEL, [500, 300]) == [240, 240, 240, 255]  # the screenshot, not an empty canvas
    a, b = canvas_point(page, 300, 200), canvas_point(page, 500, 320)
    page.mouse.move(*a)
    page.mouse.down()
    page.mouse.move(*b, steps=5)
    page.mouse.up()
    editor_badge = page.evaluate(PIXEL, [304, 190])  # the badge sits just above the box's top-left corner
    assert editor_badge[0] > 200 and editor_badge[1] < 150 and editor_badge[2] < 80, editor_badge
    assert page.evaluate(PIXEL, [400, 260]) == [240, 240, 240, 255]  # the box's inside stays visible
    page.locator(".elements-save").click()
    page.locator(".elements-dialog").wait_for(state="detached")
    saved_badge = page.evaluate("""async () => {
      const img = new Image(); img.src = `data:image/png;base64,${selectedMedia[0].data}`; await img.decode();
      const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
      const x = c.getContext('2d'); x.drawImage(img, 0, 0); return [...x.getImageData(304, 190, 1, 1).data];
    }""")
    assert saved_badge[0] > 200 and saved_badge[1] < 150 and saved_badge[2] < 80, saved_badge


def test_element_editor_drawing_geometry_labels_and_cleanup(page):
    source_data = page.evaluate("() => selectedMedia[0].data")
    page.locator(".mark-elements").first.click()
    canvas = page.locator(".elements-dialog canvas")
    canvas.wait_for(state="visible")
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width === 1000")
    a, b = canvas_point(page, 30, 30), canvas_point(page, 100, 90)
    page.mouse.move(*a)
    page.mouse.down()
    page.mouse.move(*b)
    page.mouse.up()
    page.locator(".elements-cancel").click()
    page.locator(".elements-dialog").wait_for(state="detached")
    assert page.evaluate("() => selectedMedia[0].data") == source_data
    assert page.evaluate("() => !selectedMedia[0].elements")
    page.locator(".mark-elements").first.click()
    canvas.wait_for(state="visible")
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width === 1000")
    # The 1000px source is rendered smaller; pointer coordinates must be scaled back to natural pixels.
    tiny_a, tiny_b = canvas_point(page, 10, 10), canvas_point(page, 13, 13)
    page.mouse.move(*tiny_a)
    page.mouse.down()
    page.mouse.move(*tiny_b)
    page.mouse.up()
    assert page.locator(".element-label-row").count() == 0
    for a, b in [((50, 50), (200, 150)), ((300, 50), (450, 150)), ((550, 50), (700, 150))]:
        a, b = canvas_point(page, *a), canvas_point(page, *b)
        page.mouse.move(*a)
        page.mouse.down()
        page.mouse.move(*b)
        page.mouse.up()
    assert page.locator(".element-label-row").count() == 3
    boxes = page.evaluate(
        "() => { const c=$('.elements-dialog canvas'),r=c.getBoundingClientRect(); return r.width < c.width; }"
    )
    assert boxes
    page.locator('[data-label="0"]').fill("Pay now")
    page.locator('[data-label="2"]').fill("Continue")
    # Move and resize box one, then delete box two and verify compact numbering.
    first = canvas_point(page, 100, 100)
    page.mouse.move(*first)
    page.mouse.down()
    page.mouse.move(first[0] + 15, first[1] + 12)
    page.mouse.up()
    corner = canvas_point(page, 220, 160)
    page.mouse.move(*corner)
    page.mouse.down()
    page.mouse.move(corner[0] + 15, corner[1] + 15)
    page.mouse.up()
    page.locator(".element-label-row").nth(1).locator("[data-select]").click()
    page.keyboard.press("Delete")
    assert page.locator(".element-label-row").count() == 2
    assert [page.locator(f'[data-select="{i}"]').inner_text() for i in range(2)] == ["[1]", "[2]"]
    # Clamp a final draw at the far image edges.
    a, b = canvas_point(page, 960, 560), canvas_point(page, 1030, 640)
    page.mouse.move(*a)
    page.mouse.down()
    page.mouse.move(*b)
    page.mouse.up()
    assert page.locator(".element-label-row").count() == 3
    page.locator("[data-label='2']").fill("Edge")
    # Exercise the hard cap by adding 23 small boxes at unused image positions.
    for i in range(23):
        x = 20 + (i % 10) * 90
        y = 220 + (i // 10) * 90
        a, b = canvas_point(page, x, y), canvas_point(page, x + 25, y + 25)
        page.mouse.move(*a)
        page.mouse.down()
        page.mouse.move(*b)
        page.mouse.up()
    assert page.locator(".element-label-row").count() == 26
    a, b = canvas_point(page, 900, 500), canvas_point(page, 950, 550)
    page.mouse.move(*a)
    page.mouse.down()
    page.mouse.move(*b)
    page.mouse.up()
    sync_api.expect(page.locator(".elements-error")).to_contain_text("up to 26")
    page.locator(".elements-save").click()
    page.locator(".elements-dialog").wait_for(state="detached")
    assert page.locator(".q-name").nth(1).input_value() == "element"
    assert page.locator(".q-name").count() == 2
    assert page.locator(".q-type").nth(1).input_value() == "choice"
    assert page.locator(".q-type").nth(1).is_disabled()
    assert page.locator('[data-f="criteria"]').nth(1).is_disabled()
    assert page.locator(".mark-elements").nth(1).is_hidden()
    data = page.evaluate(
        "() => { const m=selectedMedia[0]; return {name:m.file.name,data:m.data,originalData:m.originalData,elements:m.elements,req:buildRequest()}; }"
    )
    item, request = data, data["req"]
    assert item["name"] == "screen-marked.png"
    assert item["originalData"] and item["data"] != item["originalData"]
    assert item["elements"]["size"] == [1000, 600]
    assert len(item["elements"]["boxes"]) == 26
    assert item["elements"]["boxes"]["1"][0] > 50
    assert item["elements"]["boxes"]["1"][2] > 150
    assert item["elements"]["boxes"]["3"][0] + item["elements"]["boxes"]["3"][2] <= 1000
    assert item["elements"]["boxes"]["3"][1] + item["elements"]["boxes"]["3"][3] <= 600
    assert request["questions"]["element"]["criteria"]["1"] == "[1] Pay now"
    assert request["questions"]["element"]["criteria"]["2"] == "[2] Continue"
    assert request["questions"]["element"]["criteria"]["3"] == "[3] Edge"
    page.locator(".q-instructions-input").nth(1).fill("Choose an element")
    page.locator(".q-name").nth(1).fill("target")
    assert page.evaluate("() => selectedMedia[0].elements.question") == "target"
    assert page.evaluate("() => buildRequest().questions.target.instructions") == "Choose an element"
    # Reopening keeps the original pixels and current boxes; clear + save removes both.
    page.locator(".mark-elements").first.click()
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width === 1000")
    assert page.locator(".element-label-row").count() == 26
    page.locator(".elements-clear").click()
    page.locator(".elements-save").click()
    assert page.evaluate("() => selectedMedia[0].data") == source_data
    # Mark again and remove its question: the screenshot is restored as well.
    page.locator(".mark-elements").first.click()
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width === 1000")
    a, b = canvas_point(page, 40, 40), canvas_point(page, 140, 120)
    page.mouse.move(*a)
    page.mouse.down()
    page.mouse.move(*b)
    page.mouse.up()
    page.locator(".elements-save").click()
    page.locator(".remove-question").nth(1).click()
    assert page.locator(".q-name").count() == 1
    assert page.evaluate("() => selectedMedia[0].data") == source_data
    # Removing a marked image also removes its linked question.
    page.locator(".mark-elements").first.click()
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width === 1000")
    a, b = canvas_point(page, 40, 40), canvas_point(page, 140, 120)
    page.mouse.move(*a)
    page.mouse.down()
    page.mouse.move(*b)
    page.mouse.up()
    page.locator(".elements-save").click()
    page.locator("[data-remove='0']").click()
    assert page.locator(".q-name").count() == 1


def test_marked_request_payload_overlay_presets_and_evidence(page):
    page.locator("#addQuestion").click()
    evidence = page.locator(".question [data-f='evidence']").first
    assert evidence.is_disabled()
    sync_api.expect(page.locator(".media-evidence-note").first).to_contain_text("state text")
    page.locator("[data-remove='1']").click()
    page.locator("[data-remove='0']").click()
    assert page.locator(".question [data-f='evidence']").first.is_enabled()
    while page.locator(".remove-question").count():
        page.locator(".remove-question").first.click()
    page.set_input_files("#mediaInput", {"name": "screen.png", "mimeType": "image/png", "buffer": png()})
    page.wait_for_function("() => selectedMedia.length === 1")

    page.locator(".mark-elements").first.click()
    page.locator(".elements-dialog canvas").wait_for(state="visible")
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width === 1000")
    a, b = canvas_point(page, 80, 70), canvas_point(page, 230, 190)
    page.mouse.move(*a)
    page.mouse.down()
    page.mouse.move(*b)
    page.mouse.up()
    page.locator("[data-label='0']").fill("Button")
    page.locator(".elements-save").click()
    # The evidence guard still rejects raw JSON carrying evidence and media.
    page.check("#editJsonToggle")
    page.evaluate(
        "() => { const r=JSON.parse($('#requestJson').value); r.questions.element.evidence=true; $('#requestJson').value=JSON.stringify(r); }"
    )
    calls = []
    page.route(
        "**/api/decide",
        lambda route: (
            calls.append(route.request.post_data_json)
            or route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {"answers": {"element": {"type": "choice", "choice": "1", "probabilities": {"1": 0.9}}}}
                ),
            )
        ),
    )
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text("turn off")
    assert not calls
    page.evaluate(
        "() => { const r=JSON.parse($('#requestJson').value); r.questions.element.evidence=false; $('#requestJson').value=JSON.stringify(r); }"
    )

    request = []
    page.route(
        "**/api/decide",
        lambda route: (
            request.append(route.request.post_data_json),
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {"answers": {"element": {"type": "choice", "choice": "1", "probabilities": {"1": 0.9}}}}
                ),
            ),
        ),
    )
    page.click("#sendButton")
    sync_api.expect(page.locator("#results article.answer")).to_be_visible()
    assert len(request) == 1
    body = request[0]
    assert body["questions"]["element"]["criteria"] == {"1": "[1] Button"}
    assert body["media"][0]["data"] == page.evaluate("() => selectedMedia[0].data")
    assert "_elements" not in body and "originalData" not in body
    svg = page.locator("#results .image-source svg")
    sync_api.expect(svg).to_be_visible()
    assert svg.locator("rect").count() == 1
    assert "screen-marked.png" in svg.get_attribute("aria-label")
    rect_x = float(svg.locator("rect").get_attribute("x"))
    label_x = float(svg.locator("text").get_attribute("x"))
    assert label_x >= rect_x + 1000 / 28  # after the painted badge (1000 px wide image), not over its number
    original = page.evaluate("() => selectedMedia[0].originalData")
    image_data = page.evaluate("() => selectedMedia[0].data")
    pixel = page.evaluate(
        "async data=>{const i=new Image();i.src='data:image/png;base64,'+data;await i.decode();const c=document.createElement('canvas');c.width=i.width;c.height=i.height;const x=c.getContext('2d');x.drawImage(i,0,0);return [...x.getImageData(85,40,1,1).data]}",
        image_data,
    )
    assert image_data != original and pixel[0] > 180 and pixel[1] < 150 and pixel[2] < 100

    page.click("#saveRequestPreset")
    page.fill("#presetName", "marked request")
    page.click("#presetNameSave")
    page.wait_for_function("() => presetData.request['marked request']")
    saved = page.evaluate("() => presetData.request['marked request']")
    assert saved["_elements"]["marked"][0]["originalData"] == original
    assert "_elements" not in body
    page.evaluate("() => setRequest({state:'reset',questions:{other:{type:'choice',criteria:{yes:null,no:null}}}})")
    page.select_option("#examples", "preset:marked request")
    assert page.locator(".q-name").first.input_value() == "element"
    assert page.evaluate("() => selectedMedia[0].originalData") == original
    assert page.evaluate("() => buildRequest().media[0].data") == image_data
    assert not page.locator("#editJsonToggle").is_checked()
    page.evaluate(
        "() => renderResults({answers:{element:{type:'choice',choice:'1',probabilities:{'1':0.9}}}},buildRequest())"
    )
    assert page.locator("#results .image-source svg rect").count() == 1


def load_checkout_example(page):
    index = page.evaluate("() => examples.findIndex(e => e.id === 'media-01-checkout-elements')")
    assert index >= 0
    page.select_option("#examples", str(index))
    page.wait_for_function("() => selectedMedia[0]?.file.name === 'checkout.png'")


def test_saving_bundled_element_marks_preserves_structured_options(page):
    load_checkout_example(page)
    page.locator(".mark-elements").first.click()
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width === 800")
    assert page.locator("[data-label='0']").input_value() == "Pay now"
    assert page.locator("[data-label='1']").input_value() == "I accept the terms and conditions"
    page.locator(".elements-save").click()
    page.locator(".elements-dialog").wait_for(state="detached")
    criteria = page.evaluate("() => buildRequest().questions.click_target.criteria")
    assert criteria["1"] == {"element": "[1] Pay now", "role": "button"}
    assert criteria["2"] == {"element": "[2] I accept the terms and conditions", "role": "checkbox", "checked": False}


def test_bundled_options_follow_a_relabelled_or_renumbered_box(page):
    load_checkout_example(page)
    before = page.evaluate("() => buildRequest().questions.click_target.criteria")
    page.locator(".mark-elements").first.click()
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width === 800")
    page.locator("[data-label='0']").fill("Pay")  # box 1 relabelled
    page.locator(".elements-dialog [data-delete='1']").click()  # box 2 removed: box 3 becomes 2, box 4 becomes 3
    page.locator(".elements-save").click()
    page.locator(".elements-dialog").wait_for(state="detached")
    criteria = page.evaluate("() => buildRequest().questions.click_target.criteria")
    assert criteria["1"] == "[1] Pay"
    assert criteria["2"] != before["2"] and criteria["2"].startswith("[2] ")  # not the removed checkbox's option
    assert criteria["3"].startswith("[3] ") and len(criteria) == 3


def test_bundled_element_marks_own_and_remove_the_question_when_renamed(page):
    load_checkout_example(page)
    page.locator(".q-name").nth(1).fill("renamed_target")
    assert page.evaluate("() => selectedMedia[0].elements.question") == "renamed_target"
    page.locator("[data-remove='0']").click()
    assert page.locator(".q-name").count() == 1
    assert page.locator(".q-name").first.input_value() == "operation"


def test_decision_overlay_uses_media_snapshot_from_send_time(page):
    page.evaluate("() => { loadedMedia=['image']; $('#mediaInput').disabled=false; }")
    load_checkout_example(page)
    requests = []
    page.route("**/api/decide", lambda route: requests.append(route))
    with page.expect_request("**/api/decide"):
        page.click("#sendButton")
    assert requests
    invoice = page.evaluate("() => examples.findIndex(e => e.id === 'media-02-invoice')")
    page.select_option("#examples", str(invoice))  # change selected image before the response arrives
    requests[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "answers": {
                    "click_target": {
                        "type": "choice",
                        "choice": "1",
                        "probabilities": {"1": 0.7, "2": 0.2, "3": 0.05, "4": 0.05},
                    }
                }
            }
        ),
    )
    page.wait_for_selector("#results .answer")
    overlay = page.locator("#results .image-source svg")
    assert overlay.locator("rect").count() == 4
    assert "checkout.png" in overlay.get_attribute("aria-label")


def save_request_preset(page, name):
    page.click("#saveRequestPreset")
    page.fill("#presetName", name)
    page.click("#presetNameSave")
    page.wait_for_function(f"() => Boolean(presetData.request[{json.dumps(name)}])")


def test_replacing_or_clearing_media_removes_linked_element_question(page):
    mark_image(page, label="Button")
    assert page.evaluate("() => selectedMedia[0].elements.question") == "element"
    page.set_input_files("#mediaInput", {"name": "replacement.png", "mimeType": "image/png", "buffer": png()})
    page.wait_for_function("() => selectedMedia.length===1&&selectedMedia[0].file.name==='replacement.png'")
    assert page.evaluate("() => selectedMedia[0].elements") is None
    assert page.locator(".q-name").count() == 1
    assert page.locator(".q-name").first.input_value() == "question"

    mark_image(page, label="Next")
    page.evaluate("() => clearSelectedMedia()")
    assert page.evaluate("() => selectedMedia.length") == 0
    assert page.locator(".q-name").count() == 1
    assert page.locator(".q-name").first.input_value() == "question"


def test_mixed_media_presets_keep_indexes_fallback_and_json_marks(page):
    page.evaluate("() => { loadedMedia=['image','audio']; $('#mediaInput').disabled=false; }")
    page.set_input_files(
        "#mediaInput",
        [
            {"name": "call.wav", "mimeType": "audio/wav", "buffer": wav()},
            {"name": "screen.png", "mimeType": "image/png", "buffer": png()},
        ],
    )
    page.wait_for_function("() => selectedMedia.length===2")
    mark_image(page, index=1, label="Button")
    save_request_preset(page, "mixed media")
    stored = page.evaluate("() => presetData.request['mixed media']")
    assert stored["_elements"]["mediaNames"] == ["call.wav", "screen-marked.png"]
    assert stored["_elements"]["marked"][0]["index"] == 1

    page.evaluate("() => setRequest({state:'reset',questions:{other:{type:'choice',criteria:{yes:null,no:null}}}})")
    page.select_option("#examples", "preset:mixed media")
    assert page.evaluate("() => selectedMedia.map(m=>m.file.name)") == ["call.wav", "screen-marked.png"]
    assert page.evaluate("() => selectedMedia[1].elements.image") == 1
    assert page.evaluate("() => Boolean(selectedMedia[1].originalData)")
    page.check("#editJsonToggle")
    page.uncheck("#editJsonToggle")
    assert page.evaluate("() => selectedMedia[1].elements.image===1&&Boolean(selectedMedia[1].originalData)")
    assert page.evaluate("() => selectedMedia[1].file.name") == "screen-marked.png"

    page.evaluate(
        "() => { presetData.request['mixed media'].vendor_extension={kept:true};renderRequestPresets();$('#examples').value=''; }"
    )
    page.select_option("#examples", "preset:mixed media")
    assert page.locator("#editJsonToggle").is_checked()
    assert page.evaluate("() => selectedMedia.map(m=>m.file.name)") == ["call.wav", "screen-marked.png"]
    assert page.evaluate("() => selectedMedia[1].elements.image===1&&Boolean(selectedMedia[1].originalData)")
    raw = page.evaluate("() => $('#requestJson').value")
    assert "vendor_extension" in raw and "_elements" not in raw


def test_bundled_element_preset_without_original_bytes_restores_and_unlinks(page):
    index = page.evaluate("() => examples.findIndex(e=>e.id==='media-01-checkout-elements')")
    page.select_option("#examples", str(index))
    assert page.evaluate("() => selectedMedia[0].elements.question") == "click_target"
    assert page.evaluate("() => selectedMedia[0].originalData") is None
    save_request_preset(page, "bundled elements")
    saved = page.evaluate("() => presetData.request['bundled elements']")
    assert "originalData" not in saved["_elements"]["marked"][0]
    page.evaluate("() => setRequest({state:'reset',questions:{other:{type:'choice',criteria:{yes:null,no:null}}}})")
    page.select_option("#examples", "preset:bundled elements")
    assert page.evaluate("() => selectedMedia[0].elements.question") == "click_target"
    assert page.evaluate("() => selectedMedia[0].originalData") is None
    page.locator('[data-mark="0"]').click()
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width===800")
    page.locator(".elements-save").click()
    assert page.evaluate("() => Boolean(selectedMedia[0].originalData)")
    page.evaluate(
        "() => [...document.querySelectorAll('.question')].find(c=>$('.q-name',c).value==='click_target').querySelector('.remove-question').click()"
    )
    assert page.evaluate("() => selectedMedia[0].elements") is None


def test_question_name_collision_and_editing_preserve_linked_instructions(page):
    page.evaluate(
        "() => addQuestion('element',{type:'choice',instructions:'Existing instructions',criteria:{old:'Existing option'}})"
    )
    mark_image(page, label="New button")
    assert page.evaluate("() => selectedMedia[0].elements.question") == "element2"
    assert page.locator(".q-name").nth(1).input_value() == "element"
    assert page.locator(".q-instructions-input").nth(1).input_value() == "Existing instructions"
    element_card = page.locator(".question").nth(2)
    element_card.locator(".q-instructions-input").fill("Keep these instructions")
    page.locator(".mark-elements").first.click()
    page.wait_for_function("() => document.querySelector('.elements-dialog canvas').width===1000")
    page.locator(".elements-save").click()
    assert page.evaluate("() => selectedMedia[0].elements.question") == "element2"
    assert element_card.locator(".q-name").input_value() == "element2"
    assert element_card.locator(".q-instructions-input").input_value() == "Keep these instructions"
    assert page.locator(".q-name").count() == 3


def test_invalid_preset_geometry_is_rejected_before_svg_rendering(page):
    mark_image(page, label="Button")
    save_request_preset(page, "unsafe geometry")
    page.evaluate("""() => {
      const boxes=presetData.request['unsafe geometry']._elements.marked[0].boxes;
      boxes['1'][0]='0\" onload=\"alert(1)';
      renderRequestPresets();
    }""")
    page.evaluate("() => setRequest({state:'reset',questions:{other:{type:'choice',criteria:{yes:null,no:null}}}})")
    page.select_option("#examples", "preset:unsafe geometry")
    assert page.evaluate("() => selectedMedia[0].elements") is None
    page.evaluate(
        "() => renderResults({answers:{element:{type:'choice',choice:'1',probabilities:{'1':0.9}}}},buildRequest())"
    )
    assert page.locator("#results .image-source svg").count() == 0
    assert page.locator("#results [onload]").count() == 0


def test_media_attachment_clears_evidence_and_language_draft_omits_media_bytes(page):
    page.evaluate("() => clearSelectedMedia()")
    evidence = page.locator(".question [data-f='evidence']").first
    evidence.check()
    page.set_input_files("#mediaInput", {"name": "draft.png", "mimeType": "image/png", "buffer": png()})
    page.wait_for_function("() => selectedMedia.length===1")
    assert evidence.is_disabled() and not evidence.is_checked()
    assert not page.evaluate("() => buildRequest().questions.question.evidence")
    mark_image(page, label="Draft button")
    page.evaluate("() => preparePlaygroundLanguageSwitch()")
    draft = page.evaluate("() => sessionStorage.getItem('basal-ui.language-draft')")
    assert "data:" not in draft and "_elements" not in draft

    page.check("#editJsonToggle")
    page.evaluate("""() => {
      const request=JSON.parse($('#requestJson').value);
      request._elements={originalData:'private-image-data'};
      $('#requestJson').value=JSON.stringify(request);
      preparePlaygroundLanguageSwitch();
    }""")
    json_draft = page.evaluate("() => JSON.parse(sessionStorage.getItem('basal-ui.language-draft'))")
    assert "data:" not in json_draft["text"] and "_elements" not in json_draft["text"]
    assert "private-image-data" not in json_draft["text"]


def test_edit_json_media_language_switch_restores_attach_notice(page):
    request = page.evaluate("() => buildRequest()")
    page.evaluate("() => clearSelectedMedia()")
    page.check("#editJsonToggle")
    page.evaluate("request => $('#requestJson').value=JSON.stringify(request)", request)
    page.evaluate("() => preparePlaygroundLanguageSwitch()")
    draft = page.evaluate("() => JSON.parse(sessionStorage.getItem('basal-ui.language-draft'))")
    assert draft["hadMedia"] is True
    assert "media" not in json.loads(draft["text"])
    assert request["media"][0]["data"] not in draft["text"]
    page.evaluate("() => restorePlaygroundLanguageDraft()")
    sync_api.expect(page.locator("#mediaError")).to_contain_text("attached again")


def test_json_removing_linked_question_restores_unmarked_image(page):
    mark_image(page, label="Button")
    original = page.evaluate("() => selectedMedia[0].originalData")
    page.check("#editJsonToggle")
    page.evaluate("""() => {
      const request=JSON.parse($('#requestJson').value);
      delete request.questions.element;
      $('#requestJson').value=JSON.stringify(request);
    }""")
    page.uncheck("#editJsonToggle")
    assert page.evaluate("() => selectedMedia[0].elements") is None
    assert page.evaluate("() => selectedMedia[0].data") == original
    assert "element" not in page.evaluate("() => buildRequest().questions")


def test_preset_fallback_preserves_unsupported_media_fields(page):
    mark_image(page, label="Button")
    page.check("#editJsonToggle")
    request = page.evaluate("""() => {
      const request=JSON.parse($('#requestJson').value);
      request.vendor_extension={version:7};
      request.media[0].vendor_attachment={source:'scanner'};
      $('#requestJson').value=JSON.stringify(request);
      return request;
    }""")
    save_request_preset(page, "unsupported media fields")
    page.evaluate("() => setRequest({state:'reset',questions:{other:{type:'choice',criteria:{yes:null,no:null}}}})")
    page.select_option("#examples", "preset:unsupported media fields")
    restored = json.loads(page.locator("#requestJson").input_value())
    assert restored == request
    assert restored["media"][0]["vendor_attachment"] == {"source": "scanner"}
    assert page.evaluate("() => selectedMedia[0].elements.question") == "element"
    assert page.evaluate("() => selectedMedia[0].file.name") == "screen-marked.png"
