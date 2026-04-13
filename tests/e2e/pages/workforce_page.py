"""Page Object for the Workforce (folder hierarchy) view."""

from selenium.webdriver.common.by import By
from .base_page import BasePage


class WorkforcePage(BasePage):
    """Page object for the Workforce/Command Center view."""

    # --- Locators ---
    COMMAND_CENTER = (By.CSS_SELECTOR, ".workforce-command-center")
    FOLDER_ITEMS = (By.CSS_SELECTOR, ".workforce-folder-item")
    SESSION_ITEMS = (By.CSS_SELECTOR, ".workforce-session-item")
    BACK_BUTTON = (By.CSS_SELECTOR, ".workforce-back-btn")
    DEPT_HEADER = (By.CSS_SELECTOR, ".workforce-dept-header")
    NEW_SESSION_BTN = (By.CSS_SELECTOR, ".workforce-new-session")
    DELETE_SESSION_BTN = (By.CSS_SELECTOR, ".workforce-delete-session")

    # --- Actions ---

    def switch_to_workforce(self):
        """Switch to workforce view mode."""
        self.wait_js('typeof setViewMode === "function"')
        self.js('setViewMode("workforce")')

    def click_folder(self, index=0):
        """Click a folder item by index."""
        folders = self.driver.find_elements(*self.FOLDER_ITEMS)
        if folders and index < len(folders):
            folders[index].click()

    def click_back(self):
        """Click the back button."""
        self.wait_clickable(*self.BACK_BUTTON).click()

    # --- Queries ---

    def is_command_center_visible(self):
        """Check if the command center root is visible."""
        return bool(self.driver.find_elements(*self.COMMAND_CENTER))

    def folder_count(self):
        """Return number of visible folder items."""
        return len(self.driver.find_elements(*self.FOLDER_ITEMS))

    def session_count(self):
        """Return number of visible session items."""
        return len(self.driver.find_elements(*self.SESSION_ITEMS))
