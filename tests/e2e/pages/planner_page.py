"""Page Object for the AI Planner modal and slide-out."""

from selenium.webdriver.common.by import By
from .base_page import BasePage


class PlannerPage(BasePage):
    """Page object for the AI Planner popup and slide-out panel."""

    # --- Locators ---
    PLANNER_POPUP = (By.CSS_SELECTOR, ".planner-popup")
    PLANNER_INPUT = (By.CSS_SELECTOR, ".planner-input textarea")
    PLANNER_SUBMIT = (By.CSS_SELECTOR, ".planner-submit")
    SLIDEOUT = (By.CSS_SELECTOR, ".planner-slideout")
    SLIDEOUT_SPINNER = (By.CSS_SELECTOR, ".planner-slideout .spinner")
    TREE_ROOT = (By.CSS_SELECTOR, ".planner-tree-root")
    TREE_NODES = (By.CSS_SELECTOR, ".planner-tree-node")
    ACCEPT_BTN = (By.CSS_SELECTOR, ".planner-accept-btn")
    REFINE_INPUT = (By.CSS_SELECTOR, ".planner-refine-input")

    # --- Actions ---

    def open_planner(self):
        """Open the AI Planner popup."""
        self.js('openPlannerPopup()')

    def submit_prompt(self, text):
        """Type a prompt and submit it."""
        textarea = self.wait_visible(*self.PLANNER_INPUT)
        textarea.clear()
        textarea.send_keys(text)
        self.driver.find_element(*self.PLANNER_SUBMIT).click()

    def wait_for_slideout(self, timeout=None):
        """Wait for the planner slide-out to appear."""
        timeout = timeout or self.LONG_TIMEOUT
        return self.wait_visible(*self.SLIDEOUT, timeout=timeout)

    def wait_for_tree(self, timeout=None):
        """Wait for the plan tree to render."""
        timeout = timeout or self.LONG_TIMEOUT
        return self.wait_for(*self.TREE_ROOT, timeout=timeout)

    def click_accept(self):
        """Click the Accept Plan button."""
        self.wait_clickable(*self.ACCEPT_BTN).click()

    # --- Queries ---

    def tree_node_count(self):
        """Return number of tree nodes rendered."""
        return len(self.driver.find_elements(*self.TREE_NODES))

    def is_slideout_visible(self):
        """Check if the planner slide-out is open."""
        return bool(self.driver.find_elements(*self.SLIDEOUT))
