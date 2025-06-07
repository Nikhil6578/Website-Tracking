from django.conf import settings

from config import cfy_env as env
from config.settings.base import LANGUAGES
from config.constants import EXPLICIT_WSTR_SITE_URL


S3_FILE_HEADERS = "no-transform,public,max-age=2592000,s-maxage=2592000"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)  # Maintain an updated User-Agent[at least top 5-10].

PUPPETEER_EXECUTABLE_PATH = (
    '/home/contifyweb/.local/share/pyppeteer/local-chromium/588429/chrome-linux/chrome' if not settings.DEBUG else None
)
PUPPETEER_TIMEOUT = 0  # 60000

TAGS_SINGLE_FIELDS = [
    "source", "language", "published_by_company", "rating", "content_type"
]

TAGS_MULTI_FIELDS = [
    "locations", "companies", "persons", "industries", "topics", "business_events",
    "themes", "custom_tags", "source_locations", "companies_hq_locations"
]

STD_BUCKET_ID_FIELDS_MAP = {
    "language": "language",
    "content_source": "source",
    "published_by_company": "published_by_company",
    "rating": "rating",
    "content_type": "content_type",
    "location": "locations",
    "source_location": "source_locations",
    "company_hq_location": "companies_hq_locations",
    "company": "companies",
    "combined_company": "companies",
    "person": "persons",
    "combined_person": "persons",
    "industry": "industries",
    "topic": "topics",
    "business_event": "business_event",
    "theme": "themes"
}

SUPPORTED_LANGUAGES = dict(LANGUAGES)

WST_PATH = "website-tracking"
WST_ADMIN_PREFIX_URL = f"/{WST_PATH}"

FF_OLD_DIFF_IMAGE_URL = (
    "https://webtestmedia.contify.com/web_track/images/diff_content/old_image/"
    "19-12-2020-21-00-38_None.jpeg"
)

FF_OLD_DIFF_HTML = '<html><body style="background: darkgrey;"></body></html>'


WU_TAG_INFO = {
    "source": {
        "model_info": ("penseive", "ContentSource")
    },
    "published_by_company": {
        "model_info": ("penseive", "Company")
    },
    "rating": {
        "model_info": ("penseive", "Rating")
    },
    "content_type": {
        "model_info": ("penseive", "ContentType")
    },
    "locations": {
        "model_info": ("penseive", "Location")
    },
    "source_locations": {
        "model_info": ("penseive", "Location")
    },
    "companies_hq_locations": {
        "model_info": ("penseive", "Location")
    },
    "companies": {
        "model_info": ("penseive", "Company")
    },
    "persons": {
        "model_info": ("penseive", "Person")
    },
    "industries": {
        "model_info": ("penseive", "Industry")
    },
    "topics": {
        "model_info": ("penseive", "Topic")
    },
    "business_events": {
        "model_info": ("penseive", "BusinessEvent")
    },
    "themes": {
        "model_info": ("penseive", "Theme")
    },
    "custom_tags": {
        "model_info": ("penseive", "CustomTag")
    }
}


# Do not want to send "old_diff_html" or "old_diff_html" name in URL and doing
# this just to make non readable by external user
ENC_DIFF_FIELD_MAP = {
    "old_diff_html": "6050h6jz2UFnWzTIWp",
    "new_diff_html": "a6RzQGFyaRnfdQ5qbg"
}
REV_ENC_DIFF_FIELD_MAP = {
    "6050h6jz2UFnWzTIWp": "old_diff_html",
    "a6RzQGFyaRnfdQ5qbg": "new_diff_html"
}

web_update_admin_url_format = "{}website_tracking/webupdate/{}"

WST_AUTH_PASS_KEY_MAP = {
    'EMAIL': env('WST_JOB_EMAIL', default=''),
    'PASS': env('WST_JOB_PASS', default=''),
}
WST_SECRET_KEY = b'ACb7de8c193f2c8aa9beac85aad10con'


# Playwright configs
AUTH_PAGE_URL = f"{EXPLICIT_WSTR_SITE_URL}SVEx2XKZQi/e1pore0Hbq/"

DEFAULT_VIEWPORT_WIDTH = 1920
DEFAULT_VIEWPORT_HEIGHT = 1080

CONTEXT_DICT = {
    "user_agent": USER_AGENT,
    "ignore_https_errors": True,
    "viewport": {"width": DEFAULT_VIEWPORT_WIDTH,
                 "height": DEFAULT_VIEWPORT_HEIGHT},
    "extra_http_headers": {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept": "*/*",
        "Connection": "keep-alive",
        "DNT": "1",
        "Access-Control-Allow-Origin": "*",
    },
}
