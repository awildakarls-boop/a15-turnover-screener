"""End-to-end headless browser verification (desktop 1440x900 + mobile 390x844)
for both private real-data audit mode and public compliance mode.
Verifies zero external market API calls on page load/refresh and all interactive flows.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def verify_url(url: str, expected_normal: int, expected_free: int, search_term: str, label: str) -> dict:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    allowed_origin = f"{parsed.scheme}://{parsed.netloc}"
    result: dict = {"label": label, "url": url, "checks": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # 1. Desktop 1440x900 (fresh unauthenticated context)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors: list[str] = []
        external_hosts: list[str] = []

        page.on("pageerror", lambda e: errors.append(f"pageerror:{e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}:{m.text}") if m.type == "error" else None)
        page.on(
            "request",
            lambda req: external_hosts.append(req.url)
            if not req.url.startswith(allowed_origin)
            else None,
        )

        resp = page.goto(url, wait_until="networkidle", timeout=30000)
        assert resp is not None and resp.status == 200, f"[{label}] HTTP {resp.status if resp else 'None'}"
        page.wait_for_selector("#mainTbody tr.data-row", timeout=15000)

        delivery_badge = page.locator("#deliveryBadge").inner_text()
        target_date = page.locator("#metaTargetDate").inner_text()

        # Verify normal caliber button & board coverage section are removed
        assert page.locator("#btnCaliberNormal").count() == 0, f"[{label}] btnCaliberNormal should be removed"
        assert page.locator("#boardCoverageGrid").count() == 0, f"[{label}] boardCoverageGrid should be removed"

        # Verify direct free-float list count
        free_kpi = int(page.locator("#kpiSelectedCount").inner_text().replace(",", ""))
        free_rows = page.locator("#mainTbody tr.data-row").count()
        assert free_kpi == expected_free, f"[{label}] expected free KPI {expected_free}, got {free_kpi}"
        assert free_rows == expected_free, f"[{label}] expected free rows {expected_free}, got {free_rows}"

        # Click first row to expand details and verify required fields exist
        page.locator("#mainTbody tr.data-row").first.click()
        detail_text = page.locator("#mainTbody tr.detail-row").inner_text()
        for req_label in ["一句话主营", "15 分钟自由流通换手率", "前 15 分钟成交量占全天", "09:45 至收盘涨跌幅"]:
            assert req_label in detail_text, f"[{label}] missing detail label: {req_label}"

        # Test search filter
        page.fill("#inputSearch", search_term)
        filtered_rows = page.locator("#mainTbody tr.data-row").count()
        assert filtered_rows >= 1 and filtered_rows <= expected_free, f"[{label}] search '{search_term}' -> {filtered_rows}"
        page.click("#btnResetFilters")
        assert page.locator("#mainTbody tr.data-row").count() == expected_free

        # Test board filter
        first_board = page.locator("#mainTbody tr.data-row td").nth(2).inner_text().strip()
        page.select_option("#selectBoard", first_board)
        assert page.locator("#mainTbody tr.data-row").count() >= 1

        # Test sorting by close_price
        page.click("th[data-sort='close_price']")
        assert "sort-active" in (page.locator("th[data-sort='close_price']").get_attribute("class") or "")

        # Reload page and verify ZERO external requests (no market API called)
        page.reload(wait_until="networkidle")
        assert len(external_hosts) == 0, f"[{label}] unexpected external requests: {external_hosts}"
        assert len(errors) == 0, f"[{label}] JS/console errors: {errors}"

        # 2. Mobile 390x844 (fresh unauthenticated context)
        m_ctx = browser.new_context(viewport={"width": 390, "height": 844})
        m_page = m_ctx.new_page()
        m_errors: list[str] = []
        m_page.on("pageerror", lambda e: m_errors.append(str(e)))
        m_resp = m_page.goto(url, wait_until="networkidle", timeout=30000)
        assert m_resp is not None and m_resp.status == 200
        m_page.wait_for_selector("#mainTbody tr.data-row", timeout=15000)
        sticky_pos = m_page.eval_on_selector(".col-code", "el => getComputedStyle(el).position")
        scroll_w = m_page.eval_on_selector("#mainTableScroll", "el => el.scrollWidth")
        client_w = m_page.eval_on_selector("#mainTableScroll", "el => el.clientWidth")
        assert sticky_pos == "sticky", f"[{label}] mobile col-code position={sticky_pos}"
        assert scroll_w > client_w, f"[{label}] mobile table should be horizontally scrollable"
        assert len(m_errors) == 0, f"[{label}] mobile errors: {m_errors}"

        # Test clicking row on mobile
        m_page.locator("#mainTbody tr.data-row").first.click()
        assert m_page.locator("#mainTbody tr.detail-row").count() == 1

        browser.close()
        result["checks"] = {
            "http_status": resp.status,
            "delivery_badge": delivery_badge,
            "target_date": target_date,
            "desktop_free_count": free_kpi,
            "normal_list_and_board_coverage_removed": True,
            "detail_drawer_verified": True,
            "search_and_board_filter_verified": True,
            "sorting_verified": True,
            "zero_external_market_calls": True,
            "zero_console_errors": True,
            "mobile_sticky_and_scroll_verified": True,
            "mobile_scroll_width": scroll_w,
            "mobile_client_width": client_w,
        }
    return result


def verify_mode(mode: str, port: int, expected_normal: int, expected_free: int, search_term: str) -> dict:
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "serve_local.py"), "--mode", mode, "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(1.2)
    url = f"http://127.0.0.1:{port}/"
    try:
        return verify_url(url, expected_normal, expected_free, search_term, label=mode)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", type=str, default=None, help="Verify a live public HTTPS URL directly")
    parser.add_argument("--normal", type=int, default=15, help="Expected normal caliber count for --url")
    parser.add_argument("--free", type=int, default=37, help="Expected free caliber count for --url")
    parser.add_argument("--search", type=str, default="002086", help="Search keyword for --url")
    args = parser.parse_args()

    if args.url:
        r_live = verify_url(args.url, expected_normal=args.normal, expected_free=args.free, search_term=args.search, label="public_https")
        print("LIVE PUBLIC HTTPS VERIFY OK:", r_live["checks"])
        return

    r_priv = verify_mode("private", 8766, expected_normal=13, expected_free=35, search_term="002086")
    print("PRIVATE REAL DATA VERIFY OK:", r_priv["checks"])
    r_pub = verify_mode("public", 8767, expected_normal=4, expected_free=4, search_term="990001")
    print("PUBLIC COMPLIANCE MODE VERIFY OK:", r_pub["checks"])


if __name__ == "__main__":
    main()
