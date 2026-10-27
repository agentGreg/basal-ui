"""Responsive and accessible presentation checks for basal-ui's server-rendered pages."""
import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and run: playwright install chromium")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser, ui, text_model):
    ui.load(text_model["id"])
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    tab = context.new_page()
    tab.set_default_timeout(120_000)
    errors = []
    tab.on("pageerror", lambda error: errors.append(str(error)))
    tab.goto(ui.url + "/#overview")
    tab.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
    yield tab
    context.close()
    assert not errors, errors


def test_all_views_fit_responsive_widths_and_question_controls_stay_named(page, tmp_path):
    views = ("overview", "models", "manager", "playground", "batch", "inspect", "api", "history", "logs")
    for width in (1440, 1280, 1024, 768, 390):
        page.set_viewport_size({"width": width, "height": 1000})
        for theme in ("light", "dark"):
            page.locator(f'[data-theme-choice="{theme}"]').click()
            for name in views:
                page.locator(f'.side-nav [data-view="{name}"]').click()
                assert page.evaluate("document.documentElement.scrollWidth") <= width, (width, theme, name)

    page.set_viewport_size({"width": 390, "height": 844})
    page.locator('.side-nav [data-view="playground"]').click()
    page.evaluate("""() => {
      addQuestion('matrix', {type:'act', instructions:'', criteria:{approve:'good',reject:'bad'},
        costs:{approve:{approve:0,reject:5},reject:{approve:5,reject:0}}});
      addQuestion('tools', {type:'tool', instructions:'', tools:[], extra:['answer_directly']});
    }""")
    for card in page.locator(".question").all():
        card_box = card.bounding_box()
        remove_box = card.locator(".remove-question").bounding_box()
        assert remove_box["x"] + remove_box["width"] <= card_box["x"] + card_box["width"] + 1
    assert page.locator(".qf-switch input[type=checkbox]").evaluate_all(
        "items => items.every(input => Math.abs(input.getBoundingClientRect().width - 34) < 1)"
    )
    assert page.locator(".question label[for]").evaluate_all(
        "labels => labels.every(label => !!document.getElementById(label.htmlFor))"
    )
    assert page.locator(".question input, .question select, .question textarea").evaluate_all("""items => items.every(input =>
      input.getAttribute('aria-label') ||
      (input.id && document.querySelector(`label[for=\\\"${input.id}\\\"]`)) ||
      input.closest('label'))""")
    page.locator('[data-theme-choice="light"]').click()
    page.screenshot(path=str(tmp_path / "basal-390-playground.png"), full_page=True)
