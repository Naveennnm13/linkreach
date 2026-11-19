"""Every LinkedIn selector and URL pattern in one place.

When LinkedIn changes its UI, `linkreach doctor` fails and points here. Each
entry is a list of fallbacks tried in order; put the newest working selector
first. Selectors use Playwright's CSS dialect (`:has-text()`, `:has()`).
"""

# ---------- URLs ----------
FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_URL = "https://www.linkedin.com/login"
# Company "People" tab, filtered by keyword. Only current employees appear here.
COMPANY_PEOPLE_URL = "https://www.linkedin.com/company/{slug}/people/?keywords={keywords}"

# ---------- login state ----------
LOGGED_OUT_URL_PARTS = ["/login", "/authwall", "/signup", "/uas/login", "/checkpoint/lg/"]
LOGGED_IN_MARKERS = [
    "nav.global-nav",
    "#global-nav",
    "img.global-nav__me-photo",
    "button.global-nav__primary-link-me-menu-trigger",
]

# ---------- stop conditions (checked after every page load) ----------
STOP_URL_PARTS = {
    "captcha": ["/checkpoint/challenge", "/checkpoint/", "captcha", "/security-verification"],
    "warning": ["/restricted", "/uas/account-restricted", "/help/linkedin/answer/a1342443"],
}
STOP_SELECTORS = {
    "captcha": [
        "iframe[src*='captcha']",
        "iframe[title*='captcha' i]",
        "#captcha-internal",
        "form#captcha-challenge",
        "div#game-core-frame",
    ],
    "warning": [
        "div[data-test-restriction-notice]",
    ],
}
STOP_PHRASES = {
    "captcha": [
        "let's do a quick security check",
        "quick security check",
        "security verification",
        "verify you're a human",
        "i'm not a robot",
        "please complete this security check",
    ],
    "warning": [
        "unusual activity",
        "your account has been restricted",
        "your account is temporarily restricted",
        "we've restricted your account",
        "temporarily restricted",
        "you've reached the weekly invitation limit",
        "weekly invitation limit",
        "we noticed some unusual",
    ],
}

# ---------- company People tab results ----------
PEOPLE_CARD = [
    "li.org-people-profile-card__profile-card-spacing",
    "div.org-people-profile-card",
    "li.grid__col:has(a[href*='/in/'])",
]
PEOPLE_CARD_FIELDS = {
    "url": (["a[href*='/in/']"], "href"),
    "name": ([
        "div.artdeco-entity-lockup__title",
        "div.org-people-profile-card__profile-title",
        "a[href*='/in/'] div",
    ], None),
    "title": ([
        "div.artdeco-entity-lockup__subtitle",
        "div.lt-line-clamp--multi-line",
    ], None),
}

# ---------- profile page ----------
PROFILE_NAME = ["main h1", "h1.text-heading-xlarge", "h1"]
PROFILE_HEADLINE = [
    "main div.text-body-medium.break-words",
    "div.pv-text-details__left-panel div.text-body-medium",
]
PROFILE_TOP_CARD = ["main section.artdeco-card:first-of-type", "section.pv-top-card", "main section"]
PROFILE_DEGREE = [
    "main span.dist-value",
    "main span:has-text('· 1st')",
    "main span:has-text('1st degree connection')",
]
PROFILE_ABOUT = [
    "section:has(#about) div.inline-show-more-text span[aria-hidden='true']",
    "section:has(#about) div.display-flex span[aria-hidden='true']",
    "section:has(#about) span[aria-hidden='true']",
]
PROFILE_FEATURED = [
    "section:has(#featured) a span[aria-hidden='true']",
    "section:has(#featured) span[aria-hidden='true']",
]
PROFILE_EDUCATION = [
    "section:has(#education) span[aria-hidden='true']",
    "section:has(#education)",
]
# Top-card action buttons; at least one of these exists on every profile.
PROFILE_ACTIONS = [
    "main button[aria-label*='to connect']",
    "main button[aria-label^='More actions']",
    "main button[aria-label*='Pending']",
    "main button:has-text('Message')",
    "main button:has-text('Follow')",
]

# ---------- connect flow ----------
CONNECT_BUTTON = [
    "main button[aria-label^='Invite'][aria-label$='to connect']",
    "main button.pvs-profile-actions__action:has-text('Connect')",
    "main button:has-text('Connect'):not(:has-text('Connections'))",
]
MORE_BUTTON = [
    "main button[aria-label^='More actions']",
    "main button[aria-label='More']",
]
MORE_MENU_CONNECT = [
    "div.artdeco-dropdown__content div[aria-label^='Invite'][aria-label$='to connect']",
    "div.artdeco-dropdown__content div[role='button']:has-text('Connect')",
    "div[role='menu'] div:has-text('Connect')",
]
PENDING_STATE = [
    "main button[aria-label*='Pending']",
    "main button:has-text('Pending')",
    "main button[aria-label^='Withdraw']",
]
ADD_NOTE_BUTTON = [
    "div[role='dialog'] button[aria-label='Add a note']",
    "div[role='dialog'] button:has-text('Add a note')",
]
NOTE_TEXTAREA = [
    "div[role='dialog'] textarea[name='message']",
    "div[role='dialog'] textarea#custom-message",
    "div[role='dialog'] textarea",
]
SEND_BUTTON = [
    "div[role='dialog'] button[aria-label='Send invitation']",
    "div[role='dialog'] button[aria-label='Send now']",
    "div[role='dialog'] button:has-text('Send')",
]
DIALOG_DISMISS = [
    "div[role='dialog'] button[aria-label='Dismiss']",
]
# Shown when LinkedIn asks for the person's email before connecting.
EMAIL_REQUIRED = ["div[role='dialog'] input[type='email']", "div[role='dialog'] input#email"]
