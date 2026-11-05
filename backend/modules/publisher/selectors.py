"""TikTok Studio selectors, adapted from MIT reference flows (SAU / tiktok-uploader).

Prefer stable data-e2e/role labels; older Studio uses an upload iframe. No
dynamic React IDs. Schedule/cover are documented here for future UI repairs;
M1 uses local scheduling and TikTok's default cover rather than its calendar.
"""
UPLOAD_URL = 'https://www.tiktok.com/tiktokstudio/upload?lang=en'
LOGIN_URL = 'https://www.tiktok.com/login?lang=en'
IFRAME = 'iframe[data-tt="Upload_index_iframe"]'
FILE = 'input[type="file"][accept*="video"], input[type="file"][accept*="mp4"], input[type="file"]:not([accept*="image"])'
CAPTION = 'div.public-DraftEditor-content[contenteditable="true"], div[contenteditable="true"][role="textbox"], div[contenteditable="true"]'
LOGIN = '[data-e2e="login-button"], form:has(input[type="password"]), a[href*="/login"]:visible'
CHALLENGE = '[id*="captcha" i]:visible, [class*="captcha" i]:visible, iframe[src*="captcha" i]:visible, [data-e2e="verify-center"]:visible'
BANNED = 'text=/account (is |has been )?(suspended|banned)/i'
PRIVACY = '[data-e2e="video-visibility"], .tiktok-select-selector, [role="combobox"]'
PRIVACY_OPTIONS = {
    'public': '[role="option"]:text-is("Public"), [role="option"]:text-is("Everyone"), text="Everyone"',
    'friends': '[role="option"]:text-is("Friends"), text="Friends"',
    'private': '[role="option"]:text-is("Private"), [role="option"]:text-is("Only you"), text="Only you"',
}
AI = '[role="switch"][aria-label="AI-generated content"], input[aria-label="AI-generated content"], label:has-text("AI-generated content") input'
COMMENT = 'xpath=//input[@aria-label="Comment"] | //label[contains(.,"Comment")]//input | //label[normalize-space(.)="Comment"]/following-sibling::div/input'
DUET = 'xpath=//input[@aria-label="Duet"] | //label[contains(.,"Duet")]//input | //label[normalize-space(.)="Duet"]/following-sibling::div/input'
STITCH = 'xpath=//input[@aria-label="Stitch"] | //label[contains(.,"Stitch")]//input | //label[normalize-space(.)="Stitch"]/following-sibling::div/input'
SENSITIVE = 'input, textarea, [contenteditable="true"]'
POST = 'button[data-e2e="post_video_button"], div.btn-post > button, div.button-group button:has-text("Post")'
POST_NOW = 'button:has-text("Post now")'
SUCCESS = 'text=/Your video has been uploaded|Video published|视频已发布/'
CONTENT_URL = '**/tiktokstudio/content*'
RESULT_LINK = 'a[href*="/video/"]'
SUCCESS_LINK = '[role="dialog"]:has-text("Video published") a[href*="/video/"], [role="dialog"]:has-text("Your video has been uploaded") a[href*="/video/"]'
COVER = '.cover-container, .edit-container'
COVER_FILE = 'input[type="file"][accept*="image"]'
SCHEDULE = 'label:has-text("Schedule"), [aria-label="Schedule"]'
SCHEDULE_DATE = '.date-picker-input, .scheduled-picker .TUXInputBox'
SCHEDULE_TIME = '.time-picker-input'


def root(page):
    return page.frame_locator(IFRAME) if page.locator(IFRAME).count() else page


def first(scope, selector, timeout=30000, state='visible'):
    locator = scope.locator(selector).first
    locator.wait_for(state=state, timeout=timeout)
    return locator


def visible(scope, selector):
    return scope.locator(selector).first.is_visible()


def set_toggle(scope, selector, enabled, timeout=30000):
    control = first(scope, selector, timeout)
    role = control.get_attribute('role')
    if role == 'switch':
        state = control.get_attribute('aria-checked')
        if state not in ('true', 'false'):
            raise ValueError('开关状态无法确认')
        if (state == 'true') != enabled:
            control.click(timeout=timeout)
        if control.get_attribute('aria-checked') != str(enabled).lower():
            raise ValueError('开关设置未生效')
    else:
        control.set_checked(enabled, timeout=timeout)
