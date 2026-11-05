from contextlib import contextmanager
from pathlib import Path
import time
from uuid import uuid4
from urllib.parse import urljoin
from .driver import PublisherDriver, LoginRequired, NeedsAttention, Transient
from . import selectors as S
from .humanize import pause
from .service import safe_result_url


class PlaywrightDriver(PublisherDriver):
    """Explicit, bounded Studio workflow. No stealth, captcha solving or cookie export.

    All contexts are created/used/closed in their owning worker thread. A persistent
    Chromium profile writes its own session state on context close.
    """
    def __init__(self, service):
        self.service, self.settings = service, service.settings

    def browser_options(self, account, headed=False):
        return dict(headless=False if headed else self.settings.publish_headless,
            viewport={'width': account.get('browser_width') or self.settings.browser_width,
                      'height': account.get('browser_height') or self.settings.browser_height},
            locale=account.get('browser_locale') or self.settings.browser_locale,
            timezone_id=account.get('browser_timezone') or self.settings.browser_timezone,
            timeout=self.settings.browser_timeout_ms)

    @contextmanager
    def context(self, account, headed=False):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise Transient() from None
        profile = self.settings.data_dir / 'profiles' / account['id']
        profile.mkdir(parents=True, exist_ok=True)
        options = self.browser_options(account, headed)
        if account.get('proxy_id'):
            row = self.service.require('proxies', account['proxy_id'])
            proxy = {'server': row['server']}
            if row['username']:
                proxy['username'] = row['username']
            password = self.service.secrets.get('proxy:' + row['id'])
            if password:
                proxy['password'] = password
            options['proxy'] = proxy
        with sync_playwright() as playwright:
            try:
                context = playwright.chromium.launch_persistent_context(str(profile), **options)
            except Exception:
                raise Transient() from None
            try:
                context.set_default_timeout(self.settings.browser_timeout_ms)
                context.set_default_navigation_timeout(self.settings.browser_timeout_ms)
                yield context
            finally:
                try:
                    context.close()
                except Exception:
                    pass  # User may have already closed the headed window.

    def screenshot(self, page, identifier):
        try:
            directory = self.settings.data_dir / 'screenshots'
            directory.mkdir(parents=True, exist_ok=True)
            masks = [frame.locator(S.SENSITIVE) for frame in page.frames]
            page.screenshot(path=str(directory / f'{identifier}-{uuid4().hex[:8]}.png'),
                full_page=True, mask=masks, timeout=5000)
        except Exception:
            pass

    def guard(self, page, scope):
        if '/login' in page.url or S.visible(scope, S.LOGIN):
            raise LoginRequired()
        if S.visible(page, S.CHALLENGE) or S.visible(scope, S.CHALLENGE) or S.visible(scope, S.BANNED):
            raise NeedsAttention()

    def navigate(self, page):
        try:
            page.goto(S.UPLOAD_URL, wait_until='domcontentloaded', timeout=self.settings.browser_timeout_ms)
        except Exception:
            raise Transient() from None

    def check_login(self, account):
        with self.context(account) as context:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                self.navigate(page)
                scope = S.root(page)
                self.guard(page, scope)
                S.first(scope, S.FILE, self.settings.browser_timeout_ms, 'attached')
                return True
            except LoginRequired:
                self.screenshot(page, account['id'])
                return False
            except Exception:
                self.screenshot(page, account['id'])
                raise NeedsAttention() from None

    def open_login(self, account):
        with self.context(account, headed=True) as context:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(S.UPLOAD_URL, wait_until='domcontentloaded', timeout=self.settings.browser_timeout_ms)
                deadline = time.monotonic() + self.settings.login_timeout_seconds
                while time.monotonic() < deadline:
                    if page.is_closed():
                        return False
                    scope = S.root(page)
                    # A file input on Studio is a positive signal; absence of a login
                    # form alone is never sufficient to declare a session valid.
                    if '/login' not in page.url and scope.locator(S.FILE).count():
                        if S.visible(scope, S.BANNED):
                            raise NeedsAttention()
                        # In a headed login window challenges belong to the user.
                        # Wait for their resolution without interacting with them.
                        if (not S.visible(scope, S.LOGIN) and not S.visible(page, S.CHALLENGE)
                                and not S.visible(scope, S.CHALLENGE)):
                            return True
                    page.wait_for_timeout(1000)
                self.screenshot(page, account['id'])
                return False
            except Exception:
                if page.is_closed():
                    return False  # Profile is retained; Check can confirm next time.
                self.screenshot(page, account['id'])
                raise NeedsAttention() from None

    def publish(self, account, job):
        if not Path(job['video_path']).is_file():
            raise NeedsAttention()
        with self.context(account) as context:
            page = context.pages[0] if context.pages else context.new_page()
            submitted = False
            try:
                self.navigate(page)
                scope = S.root(page)
                self.guard(page, scope)
                S.first(scope, S.FILE, self.settings.browser_timeout_ms, 'attached').set_input_files(job['video_path'])
                pause(page)
                editor = S.first(scope, S.CAPTION, self.settings.upload_timeout_ms)
                text = job['caption'] + ''.join(' #' + tag.lstrip('#') for tag in job['hashtags'])
                editor.fill(text.strip())
                pause(page)
                # Defaults vary by account: explicitly set every requested control.
                S.first(scope, S.PRIVACY).click()
                S.first(scope, S.PRIVACY_OPTIONS[job['privacy']]).click()
                for selector, enabled in ((S.COMMENT, job['allow_comment']), (S.DUET, job['allow_duet']),
                        (S.STITCH, job['allow_stitch'])):
                    S.set_toggle(scope, selector, enabled, self.settings.browser_timeout_ms)
                if job['ai_generated'] or scope.locator(S.AI).count():
                    S.set_toggle(scope, S.AI, job['ai_generated'], self.settings.browser_timeout_ms)
                self.guard(page, scope)
                post = S.first(scope, S.POST, self.settings.upload_timeout_ms)
                # Locator.click waits for enabled/actionable with an explicit timeout.
                # Set the flag before the click because a timeout can follow delivery.
                submitted = True
                post.click(timeout=self.settings.upload_timeout_ms)
                if S.visible(scope, S.POST_NOW):
                    S.first(scope, S.POST_NOW).click()
                # Success requires a positive confirmation, never "no exception".
                try:
                    S.first(scope, S.SUCCESS, self.settings.browser_timeout_ms)
                except Exception:
                    page.wait_for_url(S.CONTENT_URL, timeout=self.settings.browser_timeout_ms)
                # Never guess the newest account video: it could be an older upload.
                # A result link is only trusted when present inside a success dialog.
                link = scope.locator(S.SUCCESS_LINK).first
                if link.count():
                    return safe_result_url(urljoin(page.url, link.get_attribute('href') or ''))
                return None
            except (LoginRequired, NeedsAttention, Transient):
                self.screenshot(page, job['id'])
                if submitted:
                    raise NeedsAttention() from None
                raise
            except Exception:
                self.screenshot(page, job['id'])
                try:
                    self.guard(page, S.root(page))
                except LoginRequired:
                    if not submitted:
                        raise
                except NeedsAttention:
                    pass
                raise NeedsAttention() from None
