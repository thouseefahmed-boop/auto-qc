"""Read-only browser smoke checks against a running AutoQC instance.

Install playwright separately; run python tests/browser_smoke.py URL.
Does not change video labels, modes, testsets, or benchmark runs.
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://10.12.46.7:8810"
    out = Path(__file__).resolve().parents[1] / "data" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url, wait_until="domcontentloaded")
        page.get_by_role("heading", name="Your evaluation, at a glance").wait_for()
        page.screenshot(path=str(out / "overview.png"), full_page=True)
        page.get_by_role("button", name="+ Create testset").click()
        page.get_by_role("button", name="Preview selection").click()
        page.locator("#form-error .error-message").wait_for()
        assert "unique videos" in page.locator("#form-error").inner_text()
        page.locator("#count-normal").fill("0")
        page.get_by_role("button", name="Preview selection").click()
        page.locator("#preview-result").get_by_text("40 unique videos are available.").wait_for()
        page.get_by_role("button", name="Close", exact=True).click()
        page.locator('nav a[data-tab="videos"]').click()
        page.get_by_role("heading", name="Video library", exact=True).wait_for()
        page.locator("#label-filter").select_option("bad_product")
        page.get_by_role("button", name="Filter", exact=True).click()
        page.get_by_role("button", name="Play & review labels").first.wait_for()
        page.screenshot(path=str(out / "videos.png"), full_page=True)
        page.get_by_role("button", name="Play & review labels").first.click()
        page.get_by_role("heading", name="Review video labels").wait_for()
        page.wait_for_function("document.querySelector('dialog video').readyState >= 2", timeout=60000)
        page.wait_for_function("!document.querySelector('dialog video').paused && document.querySelector('dialog video').currentTime > 0.1", timeout=15000)
        player = page.locator("dialog video").evaluate("v => ({ready:v.readyState, muted:v.muted, paused:v.paused, ratio:getComputedStyle(v).aspectRatio})")
        assert player["muted"] and not player["paused"] and player["ratio"] == "3 / 4"
        page.get_by_role("button", name="Close", exact=True).click()
        page.locator('nav a[data-tab="testsets"]').click()
        page.get_by_role("heading", name="Frozen testsets").wait_for()
        assert page.locator("table tbody tr").count() >= 1
        page.locator('nav a[data-tab="modes"]').click()
        page.get_by_role("heading", name="Model modes", exact=True).wait_for()
        assert page.get_by_role("heading", name="cosmos4fps").count() == 1
        page.get_by_role("button", name="Edit configuration").first.click()
        assert json.loads(page.locator("#mode-config").input_value())["sampling_fps"] == 4
        page.get_by_role("button", name="Close", exact=True).click()
        page.locator('nav a[data-tab="benchmarks"]').click()
        page.get_by_role("heading", name="Benchmarks", exact=True).wait_for()
        page.get_by_role("button", name="+ Queue benchmark").click()
        page.get_by_role("heading", name="Queue a benchmark", exact=True).wait_for()
        assert page.locator(".run-mode").count() == 2
        page.get_by_role("button", name="Close", exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('nav a[data-tab="overview"]').click()
        page.get_by_role("heading", name="Your evaluation, at a glance").wait_for()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(out / "mobile.png"), full_page=True)
        browser.close()
    assert not errors, errors
    print(json.dumps({"browser": "passed", "playback": player, "screenshots": str(out)}))


if __name__ == "__main__":
    main()
