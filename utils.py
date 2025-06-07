import asyncio
import copy
import logging
import re
import time
import traceback
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timedelta

import lxml
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad
from django.conf import settings
from lxml import etree
from lxml.html.clean import Cleaner
from storages.backends.s3boto3 import S3Boto3Storage

from config.buckets import STANDARD_BUCKETS
from config.constants import EXPLICIT_SITE_URL
from config.recipients import WST_ERROR_REPORT_RECIPIENTS
from config.utils import encrypt_string
from contify.cutils.utils import send_mail_via_sendgrid
from contify.website_tracking.constants import (
    S3_FILE_HEADERS, WST_PATH, WST_SECRET_KEY, AUTH_PAGE_URL
)

logger = logging.getLogger(__name__)


def get_storage(bucket_name=None):
    bucket_name = bucket_name or settings.CLIENT_MEDIA_BUCKET_NAME
    storage = S3Boto3Storage(
        bucket=bucket_name,
        secure_urls=False,
        custom_domain=bucket_name,
        headers={"Cache-Control": S3_FILE_HEADERS}
    )
    return storage


def get_diff_info_html(obj, action="added", base_url="", show_hide=False):
    tmp_tag_list = []
    diff_info = getattr(obj, f"{action}_diff_info", {})
    for k, v in diff_info.items():
        if k == "T":
            for i in v:
                tmp_tag_list.append(
                    f"<span class='diff_info_{action}'>{i}</span>"
                )
            tmp_tag_list.append("<br/>")

        elif k == "I":
            for i in v:
                img_src = base_url + i if base_url and i.startswith("/") else i
                tmp_tag_list.append(
                    f"<img src='{img_src}' style='width: 60px; height: 40px; "
                    f"display: inline !important;' alt={i} "
                    f"class='diff_info_{action}' async/>"
                )
            tmp_tag_list.append("<br/>")

        elif k == "L":
            for i in v:
                a_href = base_url + i if base_url and i.startswith("/") else i
                tmp_tag_list.append(
                    f"<span class='diff_info_{action}'>"
                    f"<a href='{a_href}'>{i}</a>"
                    f"</span>"
                )
                tmp_tag_list.append("<br/>")

    if show_hide:
        if len(tmp_tag_list) > 5:
            t_id = "{}-{}".format(action, obj.id)
            tmp_tag_list.insert(
                5, '<span id="{0}-dots" class="cfy-dots">...</span>'
                   '<span id="{0}-more" style="display: none;">'.format(t_id)
            )
            tmp_tag_list.append("</span>")
            return " ".join(tmp_tag_list), '<button class="moreLessBtn" data-objID={}>Read more</button>'.format(t_id)
        return " ".join(tmp_tag_list), ""

    if not tmp_tag_list:
        return ""

    return " ".join(tmp_tag_list)


def get_std_bucket_name(bucket_id, language='en'):
    translations = STANDARD_BUCKETS[bucket_id]['name_translations']
    return translations.get(language, translations.get('en'))


def clean_invisible_element(html):
    raw_html = copy.deepcopy(html)

    if not raw_html:
        return raw_html

    try:
        html_root = lxml.html.document_fromstring(raw_html)

        cleaner = Cleaner()
        cleaner.scripts = True
        cleaner.javascript = True
        cleaner.comments = True
        cleaner.style = True
        cleaner.inline_style = True
        cleaner.links = True
        cleaner.meta = True
        cleaner.page_structure = True
        cleaner.embedded = True
        cleaner.frames = True

        # cleaner.forms = True
        # cleaner.annoying_tags = True
        # cleaner.remove_unknown_tags = True

        cleaner.safe_attrs_only = True
        cleaner.safe_attrs = {
            'scope', 'rules', 'media', 'nohref', 'type', 'usemap', 'abbr',
            'selected', 'start', 'value', 'src', 'cite', 'readonly',
            'datetime', 'headers', 'disabled', 'checked', 'multiple', 'nowrap',
            'hreflang', 'span', 'href', 'shape', 'summary', 'axis', 'method',
            'alt', 'compact', 'frame', 'coords', 'title', 'accesskey'
        }

        cleaner.remove_tags = [
            "base", "noscript", "svg", "defs", "polygon", "rect", "path",
            "area"
        ]

        html = cleaner.clean_html(html_root)
        # return html.text_content()
        raw_html = etree.tounicode(html, method="html")
    except Exception as e:
        logger.exception(
            "Unable to clean the html to generate the hash, error: {}".format(
                e
            )
        )
        # return raw_html

    raw_html = re.sub(r"&nbsp;", " ", raw_html)
    raw_html = re.sub(r"\s+", " ", raw_html)
    return raw_html


def encode_change_log_url(obj_id, is_rel=False):
    enc_hash = encrypt_string(f"{obj_id}")
    if is_rel:
        return f"/{WST_PATH}/{enc_hash}/change-log/"

    return "{}{}/{}/change-log/".format(EXPLICIT_SITE_URL, WST_PATH, enc_hash)


async def handle_cookie_accept_xpath(
        page=None, accept_cookie_xpath=None,
        accept_cookie_button=False, max_retries=0
):
    """
    Generate the XPath for the cookie accept button on the given URL.
    Additionally, click on the accept cookie button if "accept_cookie_button"
    is True.

    Args:
        page: The Pyppeteer page instance.
        accept_cookie_xpath: XPath stored in the database or explicitly provided.
        accept_cookie_button: Set to True to click on the accept cookie button.
        max_retries: The maximum number of times to retry if an exception is raised.

    Returns:
        Tuple:
            bool: True if the button is accepted/clicked, False otherwise.
            string/None: XPath if found, None if not.

    Usage:
    click_success, xpath_generated = await handle_cookie_accept_xpath(
                                    accept_cookie_xpath=accept_cookie_xpath,
                                    page=page, accept_cookie_button=True)
    """
    xpath = None
    success = False
    if not page:
        return False, accept_cookie_xpath

    async def accept_xpath_button(cookie_xpath=None):
        """
        Helper function to click on the cookie consent button.

        """
        try:
            if cookie_xpath and isinstance(cookie_xpath, str):
                locator = page.locator(f"xpath={cookie_xpath}")
                count = await locator.count()
                # accept_cookie_xpath_button = await page.xpath(cookie_xpath)
                click_success = False
                if count > 0:
                    # done and pending are sets
                    # Note: asyncio.wait() does not raise TimeoutError! Futures
                    #        that aren't done when the timeout occurs are
                    #        returned in the pending set.
                    done, pending = await asyncio.wait([
                        locator.first.click(),
                        page.wait_for_load_state("load", timeout=100000)
                    ])
                    logger.info(
                        f"For consent button: {cookie_xpath}, Done: {done}, "
                        f"Pending: {pending}."
                    )
                    if done:
                        click_success = True
                    return click_success, cookie_xpath

        except PlaywrightTimeoutError:
            logger.info("generate_cookie_accept_xpath! Ran into TimeoutError.")
            return False, xpath
        except Exception as err:
            logger.error("generate_cookie_accept_xpath! Ran into Unexpected "
                         f"Error: {err} with Traceback: {traceback.format_exc()}, "
                         "while generating xpath for cookie button")
            return False, xpath

        return False, xpath

    if accept_cookie_xpath and accept_cookie_button:
        # If XPath is provided/stored in the database and passed.
        success, xpath = await accept_xpath_button(cookie_xpath=accept_cookie_xpath)

    # accept_cookie_button: Set to True to click on the button only if a page
    # instance is provided, as we can create our own page and generate the
    # XPath without clicking on the button if the browser instance is provided.

    for attempt in range(0, max_retries + 1):
        try:
            await page.wait_for_selector("body", timeout=2000)
            await page.wait_for_timeout(2000)  # wait 2 seconds

            # Find the "anchor or button" element with text
            # "Accept All Cookies", "Accept Cookies", "Accept All"
            # In case you find a new text type button, just add it to the
            # searchTexts list.
            xpath = await page.evaluate("""() => {
                const searchTexts = ["Accept All Cookies", "Accept Cookies", "Accept All"];
                const results = [];
                const aElements = document.querySelectorAll('a');
                const buttonElements = document.querySelectorAll('button');
            
                function isValidElement(element) {
                    const text = element.textContent.trim();
                    return searchTexts.includes(text);
                }
            
                aElements.forEach(element => {
                    const hasOnlyText = element.children.length === 0 || (element.children.length === 1 && element.children[0].nodeType === Node.TEXT_NODE);
                    if (isValidElement(element) && hasOnlyText) {
                        results.push(element);
                    }
                });
            
                buttonElements.forEach(element => {
                    const hasOnlyText = element.children.length === 0 || (element.children.length === 1 && element.children[0].nodeType === Node.TEXT_NODE);
                    if (isValidElement(element) && hasOnlyText) {
                        results.push(element);
                    }
                });
            
                return results.map(result => {
                    const selfXpath = document.evaluate(
                        `ancestor-or-self::*[self::a or self::button][1]`,
                        result,
                        null,
                        XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
                        null
                    );
                    const parentXpath = document.evaluate(
                        `ancestor-or-self::*[self::a or self::button][1]/parent::*[1]`,
                        result,
                        null,
                        XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
                        null
                    );
                    const paths = [];
                    for (let i = 0; i < selfXpath.snapshotLength; i++) {
                        const selfNode = selfXpath.snapshotItem(i);
                        const parentNode = parentXpath.snapshotItem(i);
                        const tagName = selfNode.tagName.toLowerCase();
                        const id = selfNode.id ? `[@id="${selfNode.id}"]` : '';
                        const className = selfNode.className ? `[@class="${selfNode.className}"]` : '';
                        const selfPath = `//${tagName}${id}${className}`;
                        if (selfNode !== parentNode) {
                            const parentTagName = parentNode.tagName.toLowerCase();
                            const parentId = parentNode.id ? `[@id="${parentNode.id}"]` : '';
                            const parentClassName = parentNode.className ? `[@class="${parentNode.className}"]` : '';
                            const parentPath = `//${parentTagName}${parentId}${parentClassName}`;
                            paths.push(`${parentPath}${selfPath}`);
                        } else {
                            paths.push(selfPath);
                        }
                    }
                    return paths.join(' | ');
                }).join(' | ');
            }""")

            if accept_cookie_button:
                try:
                    success, _ = await accept_xpath_button(cookie_xpath=xpath)
                except Exception as e:
                    logger.error(
                        "generate_cookie_accept_xpath! Ran into Error: "
                        f"{e} with Traceback: {traceback.format_exc()}, "
                        "while generating xpath for cookie button"
                    )
                    pass

        except Exception as err:
            logger.info(f"While evaluating cookie xpath Error: {err}")
            if attempt < max_retries:
                await page.wait_for_timeout(3000)  # Wait for a few seconds before retrying

    return success, xpath


def generate_enc_token(expiration_time):
    # Generating a random initialization vector
    token = 'u47WC2-QOiI4UAkDTjb4BO6zicRFcYpzBltTs4ClI5c=' # 999999999999
    try:
        logger.info(f"Generating token for {expiration_time}")
        iv = get_random_bytes(AES.block_size)
        cipher = AES.new(WST_SECRET_KEY, AES.MODE_CBC, iv=iv)

        # Convert expiration_time to bytes and pad the data
        data = str(expiration_time).encode()
        ciphertext = cipher.encrypt(pad(data, AES.block_size))
        token_data = iv + ciphertext

        # Encoding the token
        token = urlsafe_b64encode(token_data).decode()
    except Exception as err:
        logger.info(
            f"Unknown Error Occurred while encrypting token: {expiration_time}. "
            f"Error: {err}, Traceback: {traceback.format_exc()}"
        )
        pass
    return token


def decrypt_token(encrypted_token):
    decrypted_data = '999999999999'
    try:
        logger.info(f"Decrypting Encrypted Token: {encrypted_token}")
        # Decode the token from a URL-safe base64 string
        token_data = urlsafe_b64decode(encrypted_token)

        iv = token_data[:AES.block_size]
        ciphertext = token_data[AES.block_size:]
        cipher = AES.new(WST_SECRET_KEY, AES.MODE_CBC, iv=iv)

        # Decrypting the data
        decrypted_data = unpad(
            cipher.decrypt(ciphertext), AES.block_size
        ).decode()
    except Exception as err:
        logger.info(
            "Unknown Error Occurred while decrypting "
            f"encrypted_token: {encrypted_token}. "
            f"Error: {err}, Traceback: {traceback.format_exc()}"
        )
        pass
    return decrypted_data


def prepare_error_report(error_dic, count, command_type):
    """
    send an email with error details via sendgrid if any
    @param
        error_dic: error dictionary containing an error type,
                   obj id, and traceback
        count: count of processed items
        command_type: type of Django management command
        (e.g., 'FetchWebSource', 'ProcessWebSnapshot', 'ProcessDiffHtml')

    @return email_sent(bool): email sent confirmation
    """
    subject_cmd_name = ' '.join(command_type.split('_')).title()
    logger.info(f"{subject_cmd_name.replace(' ','')}! Sending Error Report.")
    subject = f'WST | {subject_cmd_name} | Report - {datetime.now().strftime("%d, %b. %Y %H:%M:%S")}'
    body = ''
    error_counter = 0
    object_name = re.sub(r"Process|Fetch", "", subject_cmd_name).strip()
    for e_type, error_list in error_dic.items():
        body = f'{body}\n*{e_type}\n'
        for errors in error_list:
            for obj_id, trace in errors.items():
                body = (
                    f'{body}'
                    f' \n {object_name} id: {obj_id}'
                    f' \n {trace}\n\n'
                    f'------------------------------------------------------\n'
                )
                error_counter += 1
    html = (
        f'<h3>Total no. of {object_name} processed: {count}</h3>'
        f'<h3 style="color:red">Total no. of error occur while processing: {error_counter}</h3>'
    )
    email_sent = False
    try:
        send_mail_via_sendgrid(
            sender=settings.DEFAULT_FROM_EMAIL,
            recipient=WST_ERROR_REPORT_RECIPIENTS, subject=subject, html=html,
            text_attachment=body if error_counter > 0 else None
        )
        email_sent = True
        logger.info(
            f"{subject_cmd_name.replace(' ', '')}!, Email Successfully sent."
        )
    except Exception as e:
        logger.info(
            f"{subject_cmd_name.replace(' ','')}!, Something went wrong with "
            f"exception {e} and traceback: {traceback.format_exc()}"
        )
        pass
    
    return email_sent


def set_values(string):
    """Convert comma-separated ID string into list of integer IDs."""
    return list(map(int, string.split(","))) if string else []


async def handle_cookie_dialog(page, xpath=None):
    """
    Handles cookie consent popups.
    - If an XPath is provided, it tries to click the element using XPath.
    - If no XPath is provided or clicking fails, it searches for common
     'Accept' buttons.
    """
    try:
        if xpath:
            await page.wait_for_selector(f'xpath={xpath}', timeout=3000)
            await page.locator(f'xpath={xpath}').first.click()
            await page.wait_for_timeout(1000)
            return

        await page.wait_for_selector("button, a", timeout=2000)
        search_texts = (
            "Accept All Cookies", "Accept Cookies", "Accept All", "Accept all"
            "Accept", "Allow", "Agree", "Allow all", "Confirm"
        )
        elements = await page.locator("button, a").all()
        for element in elements:
            text = await element.inner_text()
            if (
                    text and
                    any(accept_text in text for accept_text in search_texts)
            ):
                await element.click()
                await page.wait_for_timeout(1000)
                break
    except Exception:
        pass


async def authenticate_context(context, dh_id):
    """
    Authenticates the Playwright context. [Used for DiffHtml processing Job]
    """
    expiration_timestamp = int(
        (datetime.now() + timedelta(hours=24)).timestamp()
    )
    token = generate_enc_token(f"{expiration_timestamp}")
    try:
        page = await context.new_page()
        await page.set_extra_http_headers({"WST-Auth-Key": token})
        logger.info(
            f"authenticate_context: "
            f"Token generated: {token}, expires at: {expiration_timestamp}."
        )
        response = await page.goto(
            AUTH_PAGE_URL, wait_until="domcontentloaded"
        )
        if not (response and response.status == 200):
            logger.info(
                f"authenticate_context: Authentication failed. "
                f"DiffHtml ID: {dh_id} | URL: {AUTH_PAGE_URL} | "
                f"Status: {response.status if response else None}"
            )
            raise Exception("Authentication failed")  # Explicitly raise exception for better control
        await page.close()

    except Exception as e:
        logger.info(
            f"Error for DiffHtml ID: "
            f"{dh_id} | Error: {e}, Traceback: {traceback.format_exc()}"
        )
        raise

    return context


async def close_all_popups(page, max_layers=3):
    """
    Closes up to `max_layers` popups/modals by clicking 'X' or 'dismiss' buttons.
    Designed to handle stacked or dynamically loaded popups.
    Simulates human-like clicking behavior to avoid screen glitches.
    """
    close_selectors = [
        'button[aria-label="Close"]',
        'button[aria-label="Dismiss"]',
        'button[title="Close"]',
        'button[title="Dismiss"]',
        'button:has-text("×")',
        'button:has-text("Close")',
        'button:has-text("Dismiss")',
        'button:has-text("Cancel")',
        'button:has-text("Confirm")',
        'a:has-text("Cancel")',
        'a.cancel',
        'button.confirm',
        'button[data-testid="close-modal"]',
        'button.btn-close',
        '.close-button', '.popup-close', '.modal-close', '.overlay-close',
        '[class*="close"]', '[id*="close"]',
    ]

    seen_elements = set()
    for _ in range(max_layers):
        found = False
        for selector in close_selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                for i in range(count):
                    element = locator.nth(i)
                    if await element.is_visible():
                        box = await element.bounding_box()
                        if not box:
                            continue
                        identifier = f"{box['x']}-{box['y']}-{box['width']}-{box['height']}"
                        if identifier in seen_elements:
                            continue
                        seen_elements.add(identifier)

                        # Move and click like a human
                        await page.mouse.move(
                            box["x"] + box["width"] / 2,
                            box["y"] + box["height"] / 2
                        )
                        await page.mouse.click(
                            box["x"] + box["width"] / 2,
                            box["y"] + box["height"] / 2
                        )
                        await page.wait_for_timeout(800)
                        found = True
            except:
                continue

        if not found:
            break
