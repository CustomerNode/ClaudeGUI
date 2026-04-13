"""Page Object for the session Manage dropdown (Duplicate, Fork, Rewind)."""

from selenium.webdriver.common.by import By
from .base_page import BasePage


class SessionManagePage(BasePage):
    """Page object for session management operations."""

    # --- Locators ---
    MANAGE_BTN = (By.ID, "btn-actions")
    DUPLICATE_BTN = (By.ID, "btn-duplicate")
    FORK_BTN = (By.ID, "btn-fork")
    REWIND_BTN = (By.ID, "btn-rewind")
    FORK_REWIND_BTN = (By.ID, "btn-fork-rewind")
    MESSAGE_PICKER = (By.ID, "pm-overlay")
    PICKER_TITLE = (By.CSS_SELECTOR, "#pm-overlay .pm-title")
    TIMELINE_ROWS = (By.CSS_SELECTOR, "#msg-timeline .tl-row")
    SNAPSHOT_ROWS = (By.CSS_SELECTOR, "#msg-timeline .tl-snap")
    CONFIRM_BTN = (By.ID, "pm-confirm")

    # --- Actions ---

    def open_manage_dropdown(self):
        """Click the Manage/Actions button to open the dropdown."""
        self.wait_clickable(*self.MANAGE_BTN).click()

    def click_duplicate(self):
        """Click the Duplicate option."""
        self.wait_clickable(*self.DUPLICATE_BTN).click()

    def click_fork(self):
        """Click the Fork option."""
        self.wait_clickable(*self.FORK_BTN).click()

    def click_rewind(self):
        """Click the Rewind Code option."""
        btn = self.driver.find_element(*self.REWIND_BTN)
        if not btn.is_displayed():
            self.open_manage_dropdown()
            self.settle()
        try:
            btn.click()
        except Exception:
            self.js("showMessagePicker(arguments[0], 'rewind')",
                    self.js("return window._activeSessionId || ''"))

    def click_fork_rewind(self):
        """Click the Fork + Rewind option."""
        self.wait_clickable(*self.FORK_REWIND_BTN).click()

    def wait_for_picker(self, timeout=None):
        """Wait for the message picker overlay to appear."""
        timeout = timeout or self.DEFAULT_TIMEOUT
        return self.wait_visible(*self.MESSAGE_PICKER, timeout=timeout)

    def select_timeline_row(self, index=0):
        """Click a timeline row by index."""
        rows = self.driver.find_elements(*self.TIMELINE_ROWS)
        if rows and index < len(rows):
            rows[index].click()

    def select_snapshot_row(self):
        """Click the first timeline row that has a snapshot indicator."""
        rows = self.driver.find_elements(*self.TIMELINE_ROWS)
        for row in rows:
            if row.find_elements(By.CSS_SELECTOR, ".tl-snap"):
                row.click()
                return row
        return None

    def confirm(self):
        """Click the Confirm button in the picker."""
        self.wait_clickable(*self.CONFIRM_BTN).click()

    # --- Queries ---

    def get_picker_title(self):
        """Return the title text of the message picker overlay."""
        els = self.driver.find_elements(*self.PICKER_TITLE)
        return els[0].text if els else ""

    def timeline_row_count(self):
        """Return number of timeline rows."""
        return len(self.driver.find_elements(*self.TIMELINE_ROWS))

    def snapshot_count(self):
        """Return number of snapshot indicators."""
        return len(self.driver.find_elements(*self.SNAPSHOT_ROWS))

    def is_picker_visible(self):
        """Check if the message picker overlay is visible."""
        els = self.driver.find_elements(*self.MESSAGE_PICKER)
        return els[0].is_displayed() if els else False
