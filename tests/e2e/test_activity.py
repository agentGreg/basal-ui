"""Browser regressions for oldest-first history draining and log-buffer restarts."""
from urllib.parse import parse_qs, urlparse

import pytest

from tests.e2e.test_pages import browser


@pytest.fixture
def page(browser, ui):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    tab = context.new_page()
    tab.set_default_timeout(10_000)
    tab.goto(ui.url + "/#overview")
    tab.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache")
    yield tab
    context.close()


def summary(index):
    return {"id": f"h{index}", "ts": 1, "model": "test", "source": "api", "status": 200,
            "latency_ms": 1, "state_preview": f"state {index}", "answers": {}, "deferred": False,
            "truncated": False}


def test_history_poll_drains_more_than_one_oldest_first_page(page):
    requests = []

    def history(route):
        params = parse_qs(urlparse(route.request.url).query)
        requests.append(params)
        if params.get("order") == ["asc"]:
            start = int(params["after"][0][1:]) + 1
            rows = [summary(i) for i in range(start, min(start + 100, 300))]
        else:
            rows = [summary(i) for i in range(149, 49, -1)]
        route.fulfill(json={"items": rows, "models": ["test"]})

    page.route("**/api/history?**", history)
    page.click('.side-nav [data-view="history"]')
    page.wait_for_function("() => document.querySelectorAll('#historyTable tr.history-row').length === 100")
    page.evaluate("() => loadHistory('poll')")
    page.wait_for_function("() => document.querySelectorAll('#historyTable tr.history-row').length === 250")
    assert page.evaluate("() => historyAfter") == "h299"
    assert [r.get("after", [None])[0] for r in requests if r.get("order") == ["asc"]] == ["h149", "h249"]


def test_logs_clear_old_lines_when_source_buffer_generation_changes(page):
    def logs(route):
        params = parse_qs(urlparse(route.request.url).query)
        restarted = params.get("generation") == ["first"]
        generation, text = ("second", "new buffer line") if restarted else ("first", "old buffer line")
        route.fulfill(json={"sources": [{"id": "basal-ui", "state": "ui"}], "generation": generation,
                            "lines": [{"seq": 1, "ts": 1, "text": text}]})

    page.route("**/api/logs?**", logs)
    page.click('.side-nav [data-view="logs"]')
    page.wait_for_function("() => document.querySelector('#logOutput').textContent.includes('old buffer line')")
    page.evaluate("() => loadLogs()")
    page.wait_for_function("() => document.querySelector('#logOutput').textContent.includes('new buffer line')")
    assert "old buffer line" not in page.locator("#logOutput").inner_text()
