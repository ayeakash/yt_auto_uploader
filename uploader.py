"""
uploader.py — Selenium bulk-uploader for admin.babybillion.in
"""
from __future__ import annotations

import os
import re
import json
import time
import logging
from config import (
    ADMIN_LOGIN_URL, ADMIN_UPLOAD_URL,
    BB_USERNAME, BB_PASSWORD, SELENIUM_WAIT_SEC, LOG_DIR
)

log = logging.getLogger(__name__)


def _get_selenium():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException, NoSuchElementException
        from webdriver_manager.chrome import ChromeDriverManager
        return (webdriver, Options, Service, By, WebDriverWait, EC,
                TimeoutException, NoSuchElementException, ChromeDriverManager)
    except ImportError as e:
        raise ImportError(f"Selenium not installed: {e}\nRun: pip install selenium webdriver-manager")


def build_driver(headless: bool = False):
    webdriver, Options, Service, By, WebDriverWait, EC, _, _, ChromeDriverManager = _get_selenium()
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


def login(driver) -> bool:
    _, _, _, By, WebDriverWait, EC, TimeoutException, _, _ = _get_selenium()
    log.info("Navigating to login page …")
    driver.get(ADMIN_LOGIN_URL)
    time.sleep(2)

    if "/login" not in driver.current_url:
        log.info("Already logged in.")
        return True

    try:
        user_field = WebDriverWait(driver, SELENIUM_WAIT_SEC).until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR, "input[type='text'], input[name='username'], input[name='email']"
            ))
        )
        user_field.clear()
        user_field.send_keys(BB_USERNAME)

        pass_field = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        pass_field.clear()
        pass_field.send_keys(BB_PASSWORD)

        btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        btn.click()
        log.info("Login button clicked. Waiting for redirect...")

        redirected = False
        for _ in range(15):
            time.sleep(1)
            if "/login" not in driver.current_url:
                redirected = True
                break

        if not redirected:
            log.error("Login failed -- still on login page. Check credentials.")
            return False

        log.info(f"Logged in successfully. Current URL: {driver.current_url}")
        return True
    except Exception as e:
        log.error(f"Login error: {e}")
        return False


def capture_job_id(driver) -> str | None:
    uuid_pat = re.compile(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        re.IGNORECASE
    )
    JS_COLLECT = """
var parts = [];
document.querySelectorAll('input, textarea').forEach(function(el) {
    var v = el.value || '';
    if (v) parts.push('INPUT:' + v);
});
document.querySelectorAll('p, span, div, h1, h2, h3, h4, li, td, th, button').forEach(function(el) {
    var t = (el.innerText || el.textContent || '').trim();
    if (t && t.length < 300) parts.push('TEXT:' + t);
});
return parts.join('\\n');
"""
    JS_COMPLETION = """
var body = document.body ? (document.body.innerText || document.body.textContent || '') : '';
var indicators = {
    hasProcessedResults: body.indexOf('Processed Results') !== -1,
    hasSubmitForApproval: body.indexOf('Submit Batch for Approval') !== -1,
    hasCompletedWaiting: body.indexOf('completed and waiting') !== -1,
    hasUploadPaused: body.indexOf('Upload is paused') !== -1,
    hasBatchComplete: body.indexOf('is complete') !== -1,
    isComplete: body.indexOf('Processed Results') !== -1 || body.indexOf('Submit Batch for Approval') !== -1
        || body.indexOf('Upload is paused') !== -1 || body.indexOf('is complete') !== -1
};
return JSON.stringify(indicators);
"""
    MAX_POLLS = 160  # ~8 minutes; larger batches can take a while to process server-side
    last_comp = "{}"
    for poll_num in range(1, MAX_POLLS + 1):
        time.sleep(3)
        try:
            raw_comp = driver.execute_script(JS_COMPLETION) or "{}"
            last_comp = raw_comp
            import json as _json
            comp = _json.loads(raw_comp)

            rendered = driver.execute_script(JS_COLLECT) or ""
            m = uuid_pat.search(rendered)
            if m:
                job_id = m.group(1)
                log.info(f"Captured Job ID: {job_id}")
                return job_id

            url_m = uuid_pat.search(driver.current_url)
            if url_m:
                job_id = url_m.group(1)
                log.info(f"Captured Job ID from URL: {job_id}")
                return job_id

            if comp.get("isComplete"):
                log.info("Batch completion detected on dashboard.")
                return f"job_auto_{int(time.time())}"

            if poll_num % 10 == 0:
                log.info(f"  Still waiting for Job ID (poll #{poll_num}/{MAX_POLLS})… url={driver.current_url} indicators={raw_comp}")
        except Exception as e:
            log.warning(f"Polling warning (poll #{poll_num}): {e}")

    log.error(f"Timed out waiting for upload completion / Job ID. Last URL: {driver.current_url}. Last indicators: {last_comp}")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        shot_path = os.path.join(LOG_DIR, f"upload_timeout_{int(time.time())}.png")
        driver.save_screenshot(shot_path)
        log.error(f"Saved timeout screenshot: {shot_path}")
    except Exception as e:
        log.warning(f"Could not save timeout screenshot: {e}")
    return None


# Match the real submit control by its own label. Note the page also has an
# "Upload" tab and an "Upload History" tab: any locator loose enough to match
# "Upload" (or the first button on the page) silently grabs a tab instead --
# see click_submit_and_verify below.
JS_CLICK_SUBMIT = """
var btn = Array.from(document.querySelectorAll('button')).find(function (b) {
    var t = (b.textContent || '').trim();
    return t.indexOf('Submit') !== -1 && t.indexOf('Approval') === -1;
});
if (!btn) { return 'not_found'; }
if (btn.disabled) { return 'disabled'; }
btn.click();
return 'clicked';
"""

JS_SUBMIT_ACCEPTED = """
var body = document.body ? (document.body.innerText || '') : '';
return JSON.stringify({
    moved: body.indexOf('Uploading files') !== -1 || body.indexOf('Processing') !== -1
        || body.indexOf('Batch status') !== -1 || body.indexOf('Upload is paused') !== -1
        || body.indexOf('is complete') !== -1,
    stillReady: body.indexOf('ready to submit') !== -1
});
"""


def click_submit_and_verify(driver, timeout: int = 180) -> bool:
    """Press 'Submit & Process' and confirm the app actually reacted.

    The bug this replaces: the button was located with

        //button[contains(text(), 'Upload') or contains(text(), 'Submit')
                 or @type='submit']

    and find_element returns the first match in document order -- which on
    this page is the "Upload" *tab*, not the submit button. So every run
    clicked the already-active tab: no exception, no state change, and then
    an 8-minute poll for a Job ID that could never arrive. It also made the
    "wait for enabled" step meaningless, since a tab is never disabled.

    Hence: locate by the button's own label, wait for it to genuinely
    enable (the CMS validates the CSV/ZIP client-side first, which scales
    with ZIP size), then verify the page actually left the "ready to
    submit" state instead of trusting the click.
    """
    deadline = time.time() + timeout
    attempts = 0

    while time.time() < deadline:
        state = driver.execute_script(JS_CLICK_SUBMIT)

        if state == "disabled":
            time.sleep(1)
            continue
        if state == "not_found":
            log.warning("Submit button not on the page yet…")
            time.sleep(1)
            continue

        attempts += 1
        log.info(f"Clicked 'Submit & Process' (attempt {attempts}); verifying it registered…")

        for _ in range(20):  # up to ~10s for React to react
            time.sleep(0.5)
            probe = json.loads(driver.execute_script(JS_SUBMIT_ACCEPTED) or "{}")
            if probe.get("moved") or not probe.get("stillReady"):
                log.info("Submit accepted -- upload/processing has started.")
                return True

        log.warning(f"Click #{attempts} left the page on 'ready to submit'; retrying…")

    log.error(f"Submit button never responded after {attempts} click attempt(s).")
    return False


def upload_batch_files(driver, csv_path: str, zip_path: str) -> str | None:
    _, _, _, By, WebDriverWait, EC, TimeoutException, NoSuchElementException, _ = _get_selenium()

    log.info(f"Navigating to upload page: {ADMIN_UPLOAD_URL}")
    driver.get(ADMIN_UPLOAD_URL)
    time.sleep(3)

    if "/login" in driver.current_url:
        log.info("Session expired, re-logging in...")
        if not login(driver):
            return None
        driver.get(ADMIN_UPLOAD_URL)
        time.sleep(3)

    try:
        # The upload form renders client-side, so the inputs may not exist yet
        # on first look -- wait for both rather than failing the whole batch.
        try:
            WebDriverWait(driver, SELENIUM_WAIT_SEC).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, "input[type='file']")) >= 2
            )
        except TimeoutException:
            found = len(driver.find_elements(By.CSS_SELECTOR, "input[type='file']"))
            log.error(f"Expected at least 2 file inputs, found {found} after {SELENIUM_WAIT_SEC}s")
            return None

        file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
        csv_input = file_inputs[0]
        zip_input = file_inputs[1]

        log.info(f"Attaching CSV: {csv_path}")
        csv_input.send_keys(os.path.abspath(csv_path))
        time.sleep(1)

        log.info(f"Attaching ZIP: {zip_path}")
        zip_input.send_keys(os.path.abspath(zip_path))

        log.info("Waiting for Submit button to enable (client-side file validation)...")
        if not click_submit_and_verify(driver):
            return None

        return capture_job_id(driver)
    except Exception as e:
        log.error(f"Upload error: {e}")
        return None


def submit_batch_for_approval(driver) -> bool:
    _, _, _, By, WebDriverWait, _, TimeoutException, NoSuchElementException, _ = _get_selenium()
    for attempt in range(1, 3):
        try:
            btn = driver.find_element(
                By.XPATH,
                "//button[contains(text(), 'Submit Batch for Approval') or contains(text(), 'Submit for Approval')]"
            )
            log.info("Clicking 'Submit Batch for Approval' (attempt %d/2)...", attempt)
            btn.click()
            WebDriverWait(driver, SELENIUM_WAIT_SEC).until(
                lambda d: (
                    "Submitted for approval" in (d.find_element(By.TAG_NAME, "body").text or "")
                    or len(d.find_elements(By.CSS_SELECTOR, "input[type='file']")) >= 2
                )
            )
            log.info("Approval submission confirmed by the dashboard.")
            return True
        except TimeoutException:
            log.warning("Approval attempt %d was not confirmed; refreshing before retry", attempt)
            driver.refresh()
            time.sleep(3)
        except NoSuchElementException:
            body = driver.find_element(By.TAG_NAME, "body").text or ""
            if "waiting for approval submission" not in body:
                log.info("Approval button is gone and no pending approval remains.")
                return True
            log.warning("Approval button temporarily unavailable on attempt %d", attempt)
            driver.refresh()
            time.sleep(3)
        except Exception as e:
            log.error(f"Approval submission error: {e}")
            return False
    log.error("Approval submission remained unconfirmed after two attempts")
    return False


def run_batch_upload(batch_name: str, csv_path: str, zip_path: str, headless: bool = False) -> dict:
    """
    High-level handler to run upload for a single batch.
    Returns: {"status": "submitted"|"failed", "job_id": job_id, "batch_name": batch_name}
    """
    driver = None
    try:
        driver = build_driver(headless=headless)
        if not login(driver):
            return {"status": "failed", "job_id": None, "batch_name": batch_name, "reason": "login_failed"}

        job_id = upload_batch_files(driver, csv_path, zip_path)
        if not job_id:
            return {"status": "failed", "job_id": None, "batch_name": batch_name, "reason": "upload_failed"}

        if not submit_batch_for_approval(driver):
            return {
                "status": "failed",
                "job_id": job_id,
                "batch_name": batch_name,
                "reason": "approval_failed",
            }
        return {"status": "submitted", "job_id": job_id, "batch_name": batch_name}
    except Exception as e:
        log.error(f"Execution error uploading {batch_name}: {e}")
        return {"status": "failed", "job_id": None, "batch_name": batch_name, "reason": str(e)}
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
