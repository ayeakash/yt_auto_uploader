"""
uploader.py — Selenium bulk-uploader for admin.babybillion.in
"""
from __future__ import annotations

import os
import re
import time
import logging
from config import (
    ADMIN_LOGIN_URL, ADMIN_UPLOAD_URL, ADMIN_BASE_URL,
    BB_USERNAME, BB_PASSWORD, SELENIUM_WAIT_SEC, UPLOAD_RETRY_MAX
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
    isComplete: body.indexOf('Processed Results') !== -1 || body.indexOf('Submit Batch for Approval') !== -1
};
return JSON.stringify(indicators);
"""
    MAX_POLLS = 60
    for poll_num in range(1, MAX_POLLS + 1):
        time.sleep(3)
        try:
            raw_comp = driver.execute_script(JS_COMPLETION) or "{}"
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
        except Exception as e:
            log.warning(f"Polling warning (poll #{poll_num}): {e}")

    log.error("Timed out waiting for upload completion / Job ID.")
    return None


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
        # Find file inputs
        file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
        if len(file_inputs) < 2:
            log.error(f"Expected at least 2 file inputs, found {len(file_inputs)}")
            return None

        csv_input = file_inputs[0]
        zip_input = file_inputs[1]

        log.info(f"Attaching CSV: {csv_path}")
        csv_input.send_keys(os.path.abspath(csv_path))
        time.sleep(1)

        log.info(f"Attaching ZIP: {zip_path}")
        zip_input.send_keys(os.path.abspath(zip_path))
        time.sleep(1)

        # Look for upload button
        upload_btn = driver.find_element(
            By.XPATH,
            "//button[contains(text(), 'Upload') or contains(text(), 'Submit') or @type='submit']"
        )
        log.info("Clicking Upload button...")
        upload_btn.click()

        return capture_job_id(driver)
    except Exception as e:
        log.error(f"Upload error: {e}")
        return None


def submit_batch_for_approval(driver) -> bool:
    _, _, _, By, _, _, _, NoSuchElementException, _ = _get_selenium()
    try:
        btn = driver.find_element(
            By.XPATH,
            "//button[contains(text(), 'Submit Batch for Approval') or contains(text(), 'Submit for Approval')]"
        )
        log.info("Clicking 'Submit Batch for Approval'...")
        btn.click()
        time.sleep(3)
        return True
    except NoSuchElementException:
        log.info("No approval button found (auto-submitted or already pending).")
        return True
    except Exception as e:
        log.error(f"Approval submission error: {e}")
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

        submit_batch_for_approval(driver)
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
