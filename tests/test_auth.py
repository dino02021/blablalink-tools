import pytest

from lib import auth


class FakeElement:
    def __init__(self, page, kind):
        self.page = page
        self.kind = kind
        self.value = None

    async def is_visible(self, timeout=None):
        if self.kind == "region":
            return self.page.region_open
        return True

    async def is_enabled(self):
        return True

    async def click(self, timeout=None, force=False):
        if self.kind == "opener":
            self.page.region_open = True
        elif self.kind == "region":
            self.page.region_open = False
            self.page.region_selected = True
        elif self.kind == "login":
            self.page.login_clicked = True

    async def fill(self, value, timeout=None):
        self.value = value


class FakeLocator:
    def __init__(self, elements):
        self.elements = elements

    async def count(self):
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]


class FakePage:
    def __init__(self):
        self.region_open = False
        self.region_selected = False
        self.login_clicked = False
        self.account = FakeElement(self, "account")
        self.password = FakeElement(self, "password")

    def locator(self, selector, has_text=None):
        if selector == "li" and has_text == auth.REGION_TARGET:
            return FakeLocator([FakeElement(self, "region")] if self.region_open else [])
        if selector == "button" and has_text == "選擇地區":
            return FakeLocator([FakeElement(self, "opener")])
        if selector == auth.ACCOUNT_INPUT_SELECTOR:
            return FakeLocator([self.account])
        if selector == auth.PASSWORD_INPUT_SELECTOR:
            return FakeLocator([self.password])
        if selector == auth.LOGIN_BUTTON_SELECTOR:
            return FakeLocator([FakeElement(self, "login")])
        return FakeLocator([])

    async def wait_for_timeout(self, timeout):
        return None


@pytest.mark.asyncio
async def test_choose_region_opens_new_dropdown_before_selecting():
    page = FakePage()
    steps = []

    assert await auth.choose_region_once(page, steps) is True
    assert page.region_selected is True
    assert steps == [f"region:{auth.REGION_TARGET}"]


@pytest.mark.asyncio
async def test_fill_credentials_and_click_login_with_compatible_selectors():
    page = FakePage()
    steps = []

    assert await auth.fill_creds_once(page, "user@example.com", "secret", steps) is True
    assert page.account.value == "user@example.com"
    assert page.password.value == "secret"
    assert await auth.click_login_once(page, steps) is True
    assert page.login_clicked is True
    assert steps == ["filledUserPass", "loginClicked"]
