"""Every LinkedIn selector and URL pattern in one place.

When LinkedIn changes its UI, `linkreach doctor` fails and points here. Each
entry is a list of fallbacks tried in order; put the newest working selector
first. Selectors use Playwright's CSS dialect (`:has-text()`, `:has()`).
"""

# ---------- URLs ----------
FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_URL = "https://www.linkedin.com/login"
# Company "People" tab. LinkedIn now ignores ?keywords= here (checked 2026-11-21), so this page
# is used to read the company's numeric id, and the search runs on PEOPLE_SEARCH_URL instead.
COMPANY_PEOPLE_URL = "https://www.linkedin.com/company/{slug}/people/?keywords={keywords}"
# People search limited to current employees of one company. This is where the People tab's own
# keyword box sends you, and it honours keywords (including OR).
PEOPLE_SEARCH_URL = (
    "https://www.linkedin.com/search/results/people/?keywords={keywords}"
    "&currentCompany=%5B%22{company_id}%22%5D&origin=COMPANY_PAGE_CANNED_SEARCH"
)
# Links on the company page that carry its numeric id as currentCompany=["1035"].
COMPANY_ID_LINK = ["main a[href*='currentCompany=']"]

# ---------- login state ----------
LOGGED_OUT_URL_PARTS = ["/login", "/authwall", "/signup", "/uas/login", "/checkpoint/lg/"]
LOGGED_IN_MARKERS = [
    # Top-bar links that only exist when signed in. LinkedIn's class names are now
    # scrambled per build (e.g. "cfsjtu cfsjts"), so prefer hrefs over classes.
    'a[href*="/mynetwork/"]',
    'a[href*="/messaging/"]',
    'a[href*="/notifications/"]',
    # Older UI, kept as fallbacks.
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

# ---------- people search results ----------
# Checked against the live UI on 2026-11-21. Class names are scrambled per build, so these use
# roles, data-view-name hooks, aria-labels and hrefs. The last entries are the pre-2026 UI.
# LinkedIn serves at least two result layouts (one with data-view-name hooks, one without).
# Both put the name link directly inside a <p> next to "• 2nd", with the headline in the next <p>.
PEOPLE_CARD = [
    "main div[role='listitem']:has(a[href*='/in/'])",
    "li.org-people-profile-card__profile-card-spacing",
]
_NAME_LINK = "p > a[href*='/in/']"
PEOPLE_CARD_FIELDS = {
    "url": ([_NAME_LINK, "a[href*='/in/']"], "href"),
    "name": ([_NAME_LINK, "div.artdeco-entity-lockup__title"], None),
    "title": (["p:has(> a[href*='/in/']) + div p", "div.artdeco-entity-lockup__subtitle"], None),
    # "Current: Senior Technical Recruiter at Microsoft" (or "Past: ..."). linkedin.py uses it as
    # the title only when it says Current, since headlines are often slogans.
    "snippet": (["[id^='SearchResultssnippet_'] p"], None),
}

# ---------- profile page ----------
# Every profile section is a card whose id ends in its name: ...Topcard, ...About, ...Featured,
# ...SalesInsightsOrHighlights. Experience and Education load only after scrolling.
TOP = "[id$='Topcard']"
PROFILE_NAME = [f"{TOP} h2", "section[aria-label='Primary content'] h2", "main h1"]
# Old-UI headline element. The new top card has no hook for it, so linkedin.pick_headline()
# chooses it from PROFILE_TOPCARD_PARAS (skipping pronouns and the "· 2nd" degree line).
PROFILE_HEADLINE = ["main div.text-body-medium.break-words"]
PROFILE_TOPCARD_PARAS = [f"{TOP} p", "section[aria-label='Primary content'] p"]
PROFILE_TOP_CARD = [TOP, "section[aria-label='Primary content']"]
# The top card also holds a hidden "· 1st" template line, hence :visible.
PROFILE_DEGREE = [
    f"{TOP} p:visible:text-matches('^.{{0,3}}(1st|2nd|3rd)')",
    "main span.dist-value",
]
PROFILE_ABOUT = [
    "[id$='About'] [data-testid='expandable-text-box']",
    "[id$='About'] p",
    "section:has(#about) span[aria-hidden='true']",
]
PROFILE_FEATURED = ["[id$='Featured'] li p", "section:has(#featured) span[aria-hidden='true']"]
PROFILE_EDUCATION = ["[id$='Education'] p", "section:has(#education) span[aria-hidden='true']"]
# "You both studied at Northeastern University" appears here without scrolling.
PROFILE_HIGHLIGHTS = ["[id$='SalesInsightsOrHighlights'] p"]
# Top-card action controls; at least one exists on every profile.
PROFILE_ACTIONS = [
    f"{TOP} [aria-label^='Invite'][aria-label$='to connect']",
    f"{TOP} button[aria-label='More']",
    f"{TOP} a[href*='/messaging/compose/']",
    f"{TOP} button[aria-label^='Follow']",
    f"{TOP} :is(a, button):has-text('Pending')",
]

# ---------- connect flow ----------
# SAFETY: every profile page also lists other people ("People you may know", "More profiles for
# you") with their own "Invite X to connect" buttons. Connect selectors must stay inside the top
# card, and the first choice is the invite link carrying this profile's own vanity name.
CONNECT_FOR_SLUG = [
    TOP + " a[href*='custom-invite/?vanityName={slug}&']",
    TOP + " a[href$='custom-invite/?vanityName={slug}']",
]
CONNECT_BUTTON = [
    f"{TOP} [aria-label^='Invite'][aria-label$='to connect']",
    "section[aria-label='Primary content'] [aria-label^='Invite'][aria-label$='to connect']",
]
MORE_BUTTON = [f"{TOP} button[aria-label='More']", "section[aria-label='Primary content'] button[aria-label='More']"]
# Not seen live yet: on the checked profile Connect was on the card and the menu had no Connect.
MORE_MENU_CONNECT = [
    "[role='menu'] [role='menuitem'][aria-label^='Invite'][aria-label$='to connect']",
    "[role='menu'] [role='menuitem']:has-text('Connect')",
]
# Not seen live yet (needs a sent invitation). Scoped to the top card.
PENDING_STATE = [
    f"{TOP} [aria-label^='Pending']",
    f"{TOP} [aria-label*='withdraw invitation' i]",
    f"{TOP} :is(a, button):has-text('Pending')",
]
ADD_NOTE_BUTTON = ["[role='dialog'] button[aria-label='Add a note']", "[role='dialog'] button:has-text('Add a note')"]
NOTE_TEXTAREA = [
    "[role='dialog'] textarea[name='message']",
    "[role='dialog'] textarea#custom-message",
    "[role='dialog'] textarea",
]
# SAFETY: the first dialog also has "Send without a note". Never match on a bare "Send" substring.
SEND_BUTTON = [
    "[role='dialog'] button[aria-label='Send invitation']",
    "[role='dialog'] button:text-is('Send')",
]
DIALOG_DISMISS = ["[role='dialog'] button[aria-label='Dismiss']"]
# Shown when LinkedIn asks for the person's email before connecting.
EMAIL_REQUIRED = ["[role='dialog'] input[type='email']", "[role='dialog'] input#email"]
