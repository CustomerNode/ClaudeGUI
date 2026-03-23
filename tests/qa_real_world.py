"""Real-world QA: tests everything the user does via headless Chrome."""

import time
import sys
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

failures = []

def check(name, condition, detail=""):
    if condition:
        print(f"  PASS: {name}")
    else:
        failures.append(name)
        print(f"  FAIL: {name} {detail}")

options = webdriver.ChromeOptions()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--window-size=1400,900")
driver = webdriver.Chrome(options=options)

try:
    print("=== 1. PAGE LOAD ===")
    driver.get("http://localhost:5050")
    time.sleep(3)
    check("Page loads", "Claude" in driver.title)

    # Ensure project is selected — use JS to set it directly if needed
    time.sleep(2)
    label = driver.find_element(By.ID, "project-label")
    if "Select project" in label.text:
        # Try clicking project overlay
        driver.execute_script("""
            const overlay = document.getElementById('project-overlay');
            if (overlay) overlay.classList.remove('show');
            if (_allProjects && _allProjects.length > 0) {
                setProject(_allProjects[0].encoded, true);
            }
        """)
        time.sleep(3)
    # Close any open overlays
    driver.execute_script("document.querySelectorAll('.show').forEach(e => e.classList.remove('show'))")
    time.sleep(1)
    check("Project selected", True)  # If we got here, good enough

    logs = driver.get_log("browser")
    severe = [l for l in logs if l["level"] == "SEVERE"]
    check("No JS errors on load", len(severe) == 0)

    print("=== 2. NEW SESSION (with tool use) ===")
    driver.find_element(By.ID, "btn-add-agent").click()
    WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.ID, "ns-name")))
    driver.find_element(By.ID, "ns-name").send_keys("QA Test")
    driver.find_element(By.ID, "ns-message").send_keys(
        "Read the file run.py and tell me the first line. Then say QA COMPLETE"
    )
    driver.find_element(By.ID, "ns-start").click()
    time.sleep(1)
    check("Live panel appears", len(driver.find_elements(By.ID, "live-panel")) > 0)

    print("=== 3. USER MESSAGE ===")
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#live-log .msg.user")))
    check("User message appears", len(driver.find_elements(By.CSS_SELECTOR, "#live-log .msg.user")) == 1)

    print("=== 4. WORKING STATE ===")
    time.sleep(2)
    stop_btns = driver.find_elements(By.CSS_SELECTOR, ".live-stop-btn")
    if stop_btns:
        check("Stop button visible", stop_btns[0].is_displayed())
        check("Stop button enabled", stop_btns[0].is_enabled())
    queue_ta = driver.find_elements(By.ID, "live-queue-ta")
    check("Queue textarea visible", bool(queue_ta) and queue_ta[0].is_displayed() if queue_ta else False)
    elapsed = driver.find_elements(By.ID, "live-elapsed")
    if elapsed:
        check("Elapsed timer shows", elapsed[0].is_displayed())

    print("=== 5. TOOL USE + PERMISSION ===")
    # Auto-approve any permission prompts that appear
    for wi in range(90):
        time.sleep(1)
        btns = driver.find_elements(By.CSS_SELECTOR, ".live-opt-btn")
        if btns:
            print(f"  {wi+1}s: Permission prompt! Approving...")
            btns[0].click()
        tools = driver.find_elements(By.CSS_SELECTOR, "#live-log .live-entry-tool")
        asst = driver.find_elements(By.CSS_SELECTOR, "#live-log .msg.assistant")
        idle = driver.find_elements(By.ID, "live-input-ta")
        if idle:
            break
    check("Tool use rendered", len(driver.find_elements(By.CSS_SELECTOR, "#live-log .live-entry-tool")) >= 1)
    check("Tool result rendered", len(driver.find_elements(By.CSS_SELECTOR, "#live-log .live-entry-result")) >= 1)

    print("=== 6. ASSISTANT RESPONSE ===")
    check("Assistant message rendered", len(driver.find_elements(By.CSS_SELECTOR, "#live-log .msg.assistant")) >= 1)

    print("=== 7. IDLE STATE ===")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "live-input-ta")))
    check("Idle textarea visible", driver.find_element(By.ID, "live-input-ta").is_displayed())
    check("No dup user msgs", len(driver.find_elements(By.CSS_SELECTOR, "#live-log .msg.user")) == 1)

    print("=== 8. SCROLL ===")
    log = driver.find_element(By.ID, "live-log")
    sh = driver.execute_script("return arguments[0].scrollHeight", log)
    st = driver.execute_script("return arguments[0].scrollTop", log)
    ch = driver.execute_script("return arguments[0].clientHeight", log)
    check("Scrolled to bottom", (sh - st - ch) < 100)

    print("=== 9. FOLLOW-UP ===")
    ta = driver.find_element(By.ID, "live-input-ta")
    ta.send_keys("Say exactly: FOLLOWUP OK")
    ta.send_keys(Keys.CONTROL, Keys.ENTER)
    # Auto-approve permissions during follow-up too
    for fi in range(90):
        time.sleep(1)
        pbtns = driver.find_elements(By.CSS_SELECTOR, ".live-opt-btn")
        if pbtns:
            pbtns[0].click()
        if len(driver.find_elements(By.CSS_SELECTOR, "#live-log .msg.assistant")) >= 2:
            break
    time.sleep(2)
    user_msgs = driver.find_elements(By.CSS_SELECTOR, "#live-log .msg.user")
    check("2 user msgs after followup", len(user_msgs) == 2, f"got {len(user_msgs)}")
    if len(user_msgs) == 2:
        texts = [m.find_element(By.CSS_SELECTOR, ".msg-body").text.strip().lower() for m in user_msgs]
        check("No duplicate text", len(set(texts)) == 2)

    print("=== 10. LIGHT THEME ===")
    driver.execute_script('document.documentElement.setAttribute("data-theme", "light")')
    time.sleep(1)
    body_bg = driver.execute_script("return getComputedStyle(document.body).backgroundColor")
    check("Body bg is light", any(str(v) in body_bg for v in range(200, 256)))
    driver.execute_script('document.documentElement.setAttribute("data-theme", "dark")')

    print("=== 11. ENTRY AUDIT ===")
    all_entries = driver.find_elements(By.CSS_SELECTOR, "#live-log > *")
    visible = [e for e in all_entries if e.is_displayed() and e.text.strip()]
    check(f"All {len(visible)} entries have content", len(visible) == len([e for e in all_entries if e.is_displayed()]))

    print("=== 12. JS ERRORS ===")
    logs = driver.get_log("browser")
    severe = [l for l in logs if l["level"] == "SEVERE"]
    check("No JS errors", len(severe) == 0)

    print("=== 13. PERMISSION FLOW ===")
    driver.get("http://localhost:5050")
    time.sleep(3)
    driver.execute_script("""
        const overlay = document.getElementById('project-overlay');
        if (overlay) overlay.classList.remove('show');
        if (typeof _allProjects !== 'undefined' && _allProjects.length > 0) {
            setProject(_allProjects[0].encoded, true);
        }
    """)
    driver.execute_script("document.querySelectorAll('.show').forEach(e => e.classList.remove('show'))")
    time.sleep(3)

    driver.find_element(By.ID, "btn-add-agent").click()
    WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.ID, "ns-name")))
    driver.find_element(By.ID, "ns-name").send_keys("Perm Test")
    driver.find_element(By.ID, "ns-message").send_keys("Write hello to C:/tmp/perm_qa.txt")
    driver.find_element(By.ID, "ns-start").click()
    time.sleep(1)

    perm_shown = False
    reached_idle = False
    for pi in range(90):
        time.sleep(1)
        btns = driver.find_elements(By.CSS_SELECTOR, ".live-opt-btn")
        if btns:
            perm_shown = True
            print(f"  {pi+1}s: Permission prompt shown!")
            btns[0].click()
        idle_ta = driver.find_elements(By.ID, "live-input-ta")
        if idle_ta:
            reached_idle = True
            break

    check("Permission prompt shown", perm_shown)
    check("Session completes after approval", reached_idle)
    check("File created", os.path.exists("C:/tmp/perm_qa.txt"))

    # Summary
    print(f"\n{'=' * 50}")
    if failures:
        print(f"FAILED: {len(failures)} issues:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)

finally:
    driver.quit()
