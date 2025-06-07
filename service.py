
import asyncio
import copy
import json
import hashlib
import logging
import re
import traceback

from asgiref.sync import sync_to_async
from collections import defaultdict
from datetime import datetime
from io import StringIO
from lxml import etree

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from lxml.etree import XPathError

from playwright.async_api import async_playwright

from config.buckets import STANDARD_BUCKETS
from config.constants import (
    DEFAULT_COMPANY_LOGO, CONTIFY_FOR_SALES_COMPANY_PREFERENCE_ID,
    WST_SCREENSHOT_IP, WST_SCREENSHOT_PORT
)
from config.elastic_search import get_es
from config.elastic_search import utils as es_utils
from config.redis.accessors import BucketCacheReader
from config.story.utils import solr_uuid_to_story_entity_info
from config.utils import (
    story_entity_id_to_solr_id
)
from contify.cutils.cfy_enum import EmailPriority, DocType, StoryStatus
from contify.cutils.timeline import Timeline
from contify.penseive.models import CustomTag
from contify.subscriber.utils import get_client_buckets_info
from contify.website_tracking import cfy_enum as wt_enum
from contify.website_tracking import constants as wt_constant
from contify.website_tracking import mails as wu_mails
from contify.website_tracking.change_log import (
    newsfeed_create_webupdate_journal
)
from contify.website_tracking.diff_html.sub_tree_match import (
    CFYDiffer, HTMLFormatter
)
from contify.website_tracking.diff_html.utils import split_html, patch_base_tag
from contify.website_tracking.execptions import (
    NoChangeInWebUpdateEdit, FetchWebSourceError
)
from contify.website_tracking.indexer_raw import index_web_updates
from contify.website_tracking.mails import get_article, send_mail_analyst
from contify.website_tracking.models import (
    WebSource, WebClientSource, WebUpdate
)
from contify.website_tracking.web_snapshot.models import (
    WebSnapshot, DiffContent
)
from contify.website_tracking.utils import (
    get_diff_info_html, get_std_bucket_name
)


logger = logging.getLogger(__name__)

DEFAULT_VIEWPORT_WIDTH = 1920
DEFAULT_VIEWPORT_HEIGHT = 1080


def fetch_web_url(web_url, headless=True, devtools=False, timeout=None,
                  gen_screenshot=True, use_original_height=False,
                  close_browser=True):

    if timeout is None:
        timeout = 3000

    async def main():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                devtools=devtools,
                executable_path=wt_constant.PUPPETEER_EXECUTABLE_PATH,
                args=[
                    '--window-size=1920,1080',
                    "--no-sandbox",
                ]
            )

            context = await browser.new_context(
                viewport={
                    "width": DEFAULT_VIEWPORT_WIDTH,
                    "height": DEFAULT_VIEWPORT_HEIGHT
                },
                user_agent=wt_constant.USER_AGENT,
            )
            page = await context.new_page()
            await page.route("**/*", lambda route: route.continue_(
                headers={"Cache-Control": "no-cache"}
            ))

            await page.set_viewport_size({"width": 1920, "height": 1080})
            await page.goto(
                web_url, timeout=timeout, wait_until="domcontentloaded"
            )

            html_content = await page.content()
            height = await page.evaluate(
                "document.documentElement.scrollHeight")

            screenshot = ""

            if gen_screenshot:
                if height > 1080 and use_original_height:
                    await page.close()

                    page = await browser.new_page()
                    await page.set_viewport_size(
                        {"width": 1920, "height": height - 10}
                    )
                    await page.goto(
                        web_url, timeout=timeout, wait_until="domcontentloaded"
                    )
                    await page.evaluate(
                        'window.scrollBy(0, window.innerHeight)'
                    )

                screenshot = await page.screenshot(
                    type="jpeg",
                    full_page=True,
                    quality=100
                )
            if close_browser:
                await page.close()
                await browser.close()

            return {"html": html_content, "screenshot": screenshot}

    # Creating explicit event loop as IOLoop.current() needs it
    event_loop = asyncio.new_event_loop()
    # asyncio.set_event_loop(event_loop)

    result = event_loop.run_until_complete(main())
    return result


def get_screenshot_from_str_html(html_string, **kwargs):
    logger.info(
        "Generation Screenshot from String HTML with kwargs: {}".format(kwargs)
    )

    async def main():
        async with async_playwright() as p:
            browser = None
            context = None
            page = None

            try:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-http2",
                        "--disable-setuid-sandbox",
                        f"--window-size={DEFAULT_VIEWPORT_WIDTH},{DEFAULT_VIEWPORT_HEIGHT}",
                    ]
                )
                context = await browser.new_context(
                    viewport={
                        "width": DEFAULT_VIEWPORT_WIDTH,
                        "height": DEFAULT_VIEWPORT_HEIGHT
                    },
                    user_agent=wt_constant.USER_AGENT,
                )
                page = await context.new_page()
                await page.route("**/*", lambda route: route.continue_(
                    headers={"Cache-Control": "no-cache"}
                ))
                await page.set_content(html_string)

                height = await page.evaluate(
                    "document.documentElement.offsetHeight"
                )
                if height > DEFAULT_VIEWPORT_HEIGHT:
                    await page.set_viewport_size(
                        {"width": DEFAULT_VIEWPORT_WIDTH, "height": height - 10}
                    )
                    await page.evaluate(
                        'window.scrollBy(0, window.innerHeight)'
                    )

                screenshot = await page.screenshot(
                    type="jpeg",
                    full_page=True,
                    quality=100
                )

                return screenshot

            except Exception as e:
                logger.exception(
                    "StringGenScreenshot!, Unable to fetch web URL for"
                    " kwargs: {},"
                    " traceback: {}".format(kwargs, traceback.format_exc())
                )
                return e, traceback.format_exc()

            finally:
                # Close page/context/browser safely
                if page:
                    await page.close()
                if context:
                    await context.close()
                if browser:
                    await browser.close()

    # Explicit event loop
    event_loop = asyncio.new_event_loop()
    result = event_loop.run_until_complete(main())

    if isinstance(result, tuple):
        raise FetchWebSourceError(str(result[1]))

    return result


def get_md5_hash_of_string(html):
    return hashlib.md5(html.encode()).hexdigest()


@sync_to_async
def is_web_snapshot_exists(ws_id, new_md5_hash):
    return (
        WebSnapshot.objects
        .filter(
            web_source_id=ws_id, hash_html=new_md5_hash
        )
        .exists()
    )


def get_diff_html(new_wss_obj, old_wss_obj, base_url, ratio_mode="accurate",
                  threshold=0.5, fast_match=True, junk_xpaths=None):
    """
        This is used to find the differences in the old and new HTMl content.
    """
    logger.info(
        "ProcessWebSnapshot!, Generating DiffHtml for OldWebSnapshotID: {}, "
        "NewWebSnapshotID: {} and BaseURL: {}".format(
            new_wss_obj.id, old_wss_obj.id, base_url
        )
    )
    diff_options = {
        'ratio_mode': ratio_mode,
        'F': threshold,  # 0.6
        'fast_match': fast_match,
        'uniqueattrs': ['{http://www.w3.org/XML/1998/namespace}id']
    }

    html_parser = etree.HTMLParser(
        encoding="utf-8", remove_comments=True, compact=False,
        # default_doctype=False
    )

    old_html_str = old_wss_obj.raw_html or ""
    new_html_str = new_wss_obj.raw_html or ""

    raw_old_tree = etree.parse(StringIO(old_html_str), html_parser)

    patch_base_tag(raw_old_tree, base_url)

    o_start, o_body, o_end = split_html(
        etree.tounicode(raw_old_tree, method="html")
    )
    _, n_body, _ = split_html(
        etree.tounicode(
            etree.parse(StringIO(new_html_str), html_parser), method="html"
        )
    )

    old_tree = etree.parse(StringIO(o_body), html_parser)
    new_tree = etree.parse(StringIO(n_body), html_parser)
    if junk_xpaths:
        old_tree, new_tree = remove_junk(junk_xpaths, old_tree, new_tree)

    formatter = HTMLFormatter(normalize=True, pretty_print=True)
    formatter.prepare(old_tree, new_tree)

    differ = CFYDiffer(**diff_options)
    diffs = differ.diff(old_tree, new_tree)

    diffed_tree = formatter.format(diffs, old_tree)

    old_diff_html, new_diff_html = formatter.cfy_gen_separate_old_n_new(
        diffed_tree, o_start, o_end
    )

    logger.info(
        "ProcessWebSnapshot!, Done Generating DiffHtml for OldWebSnapshotID:"
        " {}, NewWebSnapshotID: {} and BaseURL: {}".format(
            new_wss_obj.id, old_wss_obj.id, base_url
        )
    )
    cxt = {
        "old_html": old_diff_html,
        "new_html": new_diff_html,
        "added_diff_info": formatter.cfy_added_diff_info,
        "removed_diff_info": formatter.cfy_removed_diff_info
    }
    return cxt


def remove_junk(junk_xpaths, old_tree, new_tree):
    """
    Remove junk parts from the trees before processing the difference. This
    helps to avoid unnecessary updates to be recorded.

    junk_xpaths(list): contains xpaths of tags to be removed
    old_tree: Element object for old html tree.
    new_tree: Element object for new html tree.
    """
    attr = None
    attr_pattern = '^.*@(?P<attr>[a-z]+)$'
    for junk_xpath in junk_xpaths:
        m = re.match(attr_pattern, junk_xpath)
        if m:
            attr = m.group('attr')
            junk_xpath = junk_xpath.replace(f'/@{attr}', '')
        try:
            for tree in [old_tree, new_tree]:
                tags = tree.xpath(junk_xpath)
                if tags:
                    for tag in tags:
                        if attr and attr in tag.attrib:
                            tag.set(attr, '')
                        else:
                            tag.getparent().remove(tag)
                else:
                    logger.info('Did not find any tag in tree.')
        except (XPathError, AttributeError):
            logger.info(
                f'Error while applying the {junk_xpath} xpath. '
                f'Traceback: {traceback.format_exc()}'
            )
    return old_tree, new_tree


def get_diff_cxt(diff_id):
    """
        This is used for curating the DiffContent to create a webUpdate
        from the DiffContent info.
    """
    tags_map = dict()
    bucket_map = dict()
    client_map = dict()

    def prepare_m2m_tags_map(wcs_obj, field_name):
        m2m_wcs_tags = getattr(wcs_obj, field_name)
        if m2m_wcs_tags.exists():
            b_id = m2m_wcs_tags.model.bucket_id
            tags_map[wcs.client_id][b_id] = {
                t.id: t.name for t in m2m_wcs_tags.all()
            }
            bucket_map[b_id] = get_std_bucket_name(b_id)

    dc = DiffContent.objects.get(id=diff_id)

    web_client_sources = WebClientSource.objects.filter(
        source_id=dc.new_snapshot.web_source_id,
        state=wt_enum.State.ACTIVE.value
    )
    ws = WebSource.objects.get(id=dc.new_snapshot.web_source_id)
    for wcs in web_client_sources:
        client_map[wcs.client_id] = wcs.client.company.name

        tags_map[wcs.client_id] = {}

        for slf in wt_constant.TAGS_SINGLE_FIELDS:
            if slf in ["published_by_company", "source"]:
                slf = "content_source" if slf == "source" else slf
                tag = getattr(ws, slf, None)
            else:
                tag = getattr(wcs, slf, None)

            if not tag:
                continue

            if slf == "language":
                tag_name = wt_constant.SUPPORTED_LANGUAGES.get(tag)
                bid = "language"
            else:
                if not tag.active:
                    continue

                tag_name = tag.name
                bid = tag.bucket_id

            if tag_name:
                tags_map[wcs.client_id][bid] = {
                    getattr(tag, "id", tag): tag_name,
                }
                bucket_map[bid] = get_std_bucket_name(bid)

        for fl in [
            "locations", "companies", "industries", "topics", "business_events",
            "themes"
        ]:
            prepare_m2m_tags_map(wcs, fl)

        if wcs.custom_tags.exists():
            ct_map = defaultdict(dict)
            for ct in wcs.custom_tags.all():
                ct_map[ct.bucket_id][ct.id] = ct.name
                bucket_map[ct.bucket_id] = ct.bucket.name

            for cb_id, cb_tags_map in ct_map.items():
                tags_map[wcs.client_id][cb_id] = cb_tags_map

    cxt = {
        "diffContentID": dc.id,
        "oldSnapshotId": dc.old_snapshot_id,
        "newSnapshotId": dc.new_snapshot_id,
        # "newDiffScreenshotUrl": dc.new_diff_image.url,
        "wsWebUrl": ws.web_url,

        "diffStatus": dc.status,
        "addedDiffHtml": get_diff_info_html(dc, "added", ws.base_url),
        "removedDiffHtml": get_diff_info_html(dc, "removed", ws.base_url),
        "diffCreatedOn": dc.created_on,

        "clientMap": client_map,
        "bucketMap": bucket_map,
        "tagsMap": json.dumps(tags_map),

        "snippetInfo": {},
        "pubDate": datetime.now()  # setting default pubDate (approved_on)
    }

    no_diff_screenshot = True
    if dc.new_diff_image and hasattr(dc.new_diff_image, "url"):
        no_diff_screenshot = False
        cxt["newDiffScreenshotUrl"] = dc.new_diff_image.url
    else:
        cxt["newDiffScreenshotUrl"] = wt_constant.FF_OLD_DIFF_IMAGE_URL

    if dc.old_diff_image and hasattr(dc.old_diff_image, "url"):
        no_diff_screenshot = False
        cxt["oldDiffScreenshotUrl"] = dc.old_diff_image.url
    else:
        cxt["oldDiffScreenshotUrl"] = wt_constant.FF_OLD_DIFF_IMAGE_URL

    cxt["noDiffScreenshot"] = no_diff_screenshot
    return cxt


def get_web_update_cxt(wu_id, include_diff_content=False):
    """
        This is used the details page of webUpdate and for updating the
        existing the webUpdate as well.
    """
    tags_map = {}
    bucket_map = {}
    entity_info = {}

    def prepare_m2m_tags_map(field_name):
        m2m_wcs_tags = getattr(wu_obj, field_name)
        if m2m_wcs_tags.exists():
            b_id = m2m_wcs_tags.model.bucket_id
            tags_map[b_id] = {
                t.id: t.name for t in m2m_wcs_tags.all()
            }
            bucket_map[b_id] = get_std_bucket_name(b_id)

    wu_obj = WebUpdate.objects.get(id=wu_id)

    for slf in wt_constant.TAGS_SINGLE_FIELDS:
        tag = getattr(wu_obj, slf, None)
        if not tag:
            continue

        if slf == "language":
            tag_name = wt_constant.SUPPORTED_LANGUAGES.get(tag)
            bid = "language"
        else:
            if not tag.active:
                continue

            tag_name = tag.name
            bid = tag.bucket_id

        if tag_name:
            tags_map[bid] = {getattr(tag, "id", tag): tag_name}
            bucket_map[bid] = get_std_bucket_name(bid)

            if slf == "published_by_company":
                entity_info["name"] = tag_name

    for fl in [
        "locations", "companies", "industries", "topics", "business_events",
        "themes", "source_locations", "companies_hq_locations"
    ]:
        prepare_m2m_tags_map(fl)

    if wu_obj.custom_tags.exists():
        ct_map = defaultdict(dict)
        for ct in wu_obj.custom_tags.all():
            ct_map[ct.bucket_id][ct.id] = ct.name
            bucket_map[ct.bucket_id] = ct.bucket.name

        for cb_id, cb_tags_map in ct_map.items():
            tags_map[cb_id] = cb_tags_map

    if not entity_info and "company" in tags_map:
        entity_info["name"] = list(tags_map["company"].values())[0]

    client_name = wu_obj.client.company.name

    cxt = {
        "WebUpdateID": wu_obj.id,

        "title": wu_obj.title,
        "summary": wu_obj.description,
        "pubDate": wu_obj.approved_on,

        "snippetInfo": wu_obj.snippet_info or {},
        "entityInfo": entity_info,

        "clientID": wu_obj.client_id,

        "WebUpdateCreatedBy": wu_obj.created_by,
        "webUpdateCreatedOn": wu_obj.created_on,

        "contentSourceName": wu_obj.source.name,
        "clientName": client_name,

        "diffContentID": wu_obj.diff_content_id,
        "bucketMap": bucket_map,
    }

    try:
        ws = WebSource.objects.get(id=wu_obj.web_source_id)
        web_url = ws.web_url
        base_url = ws.base_url
        cxt['wsWebUrl'] = web_url
    except WebSource.DoesNotExist as e:
        web_url = None
        base_url = None

    old_image_url = None
    new_image_url = None
    if wu_obj.new_image and hasattr(wu_obj.new_image, "url"):
        new_image_url = wu_obj.new_image.url

    if wu_obj.old_image and hasattr(wu_obj.old_image, "url"):
        old_image_url = wu_obj.old_image.url

    if include_diff_content:
        cxt["tagsMap"] = {wu_obj.client_id: tags_map}
        cxt["clientMap"] = {wu_obj.client_id: client_name}

        try:
            dc = DiffContent.objects.get(id=wu_obj.diff_content_id)
            if not web_url:
                try:
                    ws = WebSource.objects.get(
                        id=dc.new_snapshot.web_source_id
                    )
                    web_url = ws.web_url
                    base_url = ws.base_url
                except WebSource.DoesNotExist as e:
                    web_url = "WebSource does not exist"
                    base_url = None

            if (not old_image_url and
                    dc.old_diff_image and hasattr(dc.old_diff_image, "url")):
                old_image_url = dc.old_diff_image.url

            if (not new_image_url and
                    dc.new_diff_image and hasattr(dc.new_diff_image, "url")):
                new_image_url = dc.new_diff_image.url

            cxt["oldSnapshotId"] = dc.old_snapshot_id
            cxt["newSnapshotId"] = dc.new_snapshot_id

            cxt["addedDiffHtml"] = get_diff_info_html(dc, "added", base_url)
            cxt["removedDiffHtml"] = get_diff_info_html(dc, "removed", base_url)
            cxt["diffCreatedOn"] = dc.created_on
            cxt["diffStatus"] = dc.status
        except DiffContent.DoesNotExist as e:
            cxt.pop("diffContentID")

        cxt['wsWebUrl'] = web_url

        # this is the case that webUpdate is created without the DiffContent,
        # so considering snippet's URL as newDiffScreenshotUrl else setting
        # old and snapshot URLs blank.
        if not new_image_url and "url" in cxt["snippetInfo"]:
            new_image_url = cxt["snippetInfo"]["url"]

        if not new_image_url:
            new_image_url = wt_constant.FF_OLD_DIFF_IMAGE_URL

        if not old_image_url:
            old_image_url = wt_constant.FF_OLD_DIFF_IMAGE_URL

        cxt["oldDiffScreenshotUrl"] = old_image_url
        cxt["newDiffScreenshotUrl"] = new_image_url

        if "url" not in cxt["snippetInfo"]:
            cxt["snippetInfo"] = {}
    else:
        cxt["tagsMap"] = tags_map

    return cxt


@transaction.atomic
def create_or_update_web_update(post_data, user):
    """
        This creates a new WebUpdate or updates the existing WebUpdate.
    """
    client_id = int(post_data["clientID"])
    title = post_data["title"]
    summary = post_data["summary"]
    created = False
    old_wu_copy = None
    is_update_exists = "webUpdateID" in post_data
    make_copy_for_si = post_data["makeCopyForSI"]

    if is_update_exists:  # An ID of the existing WebUpdate record
        wu_obj = WebUpdate.objects.get(id=post_data["webUpdateID"])
        # TODO: keep the deep copy of webUpdate and its tags as well
        old_wu_copy = copy.deepcopy(wu_obj)
    else:
        new_hash = get_md5_hash_of_string(title + summary)
        wu_qs = WebUpdate.objects.filter(hash=new_hash, client_id=client_id)
        if wu_qs.exists():
            wu_obj = wu_qs[0]
        else:
            created = True
            wu_obj = WebUpdate()
            wu_obj.hash = new_hash
            wu_obj.approved_on = datetime.now()
            wu_obj.created_by_id = user.id
            wu_obj.client_id = client_id

    wu_obj.updated_by_id = user.id

    wu_obj.status = (
        post_data.get("status") or StoryStatus.PUBLISHED.value
    )

    wu_obj.approved_by_id = user.id
    wu_obj.user_updated_on = datetime.now()
    wu_obj.title = title
    wu_obj.description = summary
    # wu_obj.approved_on = post_data.get("pubDate", datetime.now())
    # TODO: email_priority tp
    wu_obj.email_priority = (
        post_data.get("emailPriority") or
        EmailPriority.KEEP_IN_EMAIL_ALERT.value
    )

    snippet_info = {}
    if "snippetInfo" in post_data and "url" in post_data["snippetInfo"]:
        wu_obj.snippet_info = post_data["snippetInfo"]
        snippet_info = post_data["snippetInfo"]

    # ----------------- Updating singled field value Tags ---------------------
    for tsf in wt_constant.TAGS_SINGLE_FIELDS:
        setattr(wu_obj, tsf, None)

    for bid, tags_map in post_data.get("tagsMap", {}).items():
        tags_id = list(tags_map.keys())
        if not tags_id:
            continue

        field_name = wt_constant.STD_BUCKET_ID_FIELDS_MAP.get(bid)
        if field_name in wt_constant.TAGS_SINGLE_FIELDS:
            if field_name in ["language"]:
                setattr(wu_obj, field_name, tags_id[0])
            else:
                setattr(wu_obj, f"{field_name}_id", int(tags_id[0]))
    ########################################################################
    # Assign any other webupdate field value here as save gets called while
    # image gets uploaded
    # ######################################################################
    # TODO: populate language using detect_language if it is empty

    # TODO: we do not get DiffContent ID from newsfeed
    is_image_uploaded = False
    try:
        dc_obj = DiffContent.objects.get(id=post_data.get("diffContentID"))
    except DiffContent.DoesNotExist as e:
        dc_obj = None

    if dc_obj:
        wu_obj.diff_content_id = dc_obj.id
        wu_obj.web_source_id = dc_obj.new_snapshot.web_source_id

        # Uploading image from DiffContent only if WebUpdate has not, as there
        # is possibility that manually uploaded image would change. So
        # considering that if the WebUpdate has the image it might be manually.
        f_name = "{}.jpeg".format(datetime.now().strftime("%d-%m-%Y-%H-%M-%S"))
        if dc_obj.new_diff_image and not wu_obj.new_image:
            dc_obj.new_diff_image.seek(0)
            wu_obj.new_image.save(
                f_name, ContentFile(dc_obj.new_diff_image.read())
            )
            is_image_uploaded = True
            logger.info(
                "CreateUpdateWebUpdate!, new image uploaded from DiffContent-"
                "ID: {}".format(dc_obj.id)
            )
            if snippet_info.get("url") == dc_obj.new_diff_image.url:
                wu_obj.snippet_info["url"] = wu_obj.new_image.url

        if dc_obj.old_diff_image and not wu_obj.old_image:
            dc_obj.old_diff_image.seek(0)
            wu_obj.old_image.save(
                f_name, ContentFile(dc_obj.old_diff_image.read())
            )
            is_image_uploaded = True
            logger.info(
                "CreateUpdateWebUpdate!, old image uploaded from DiffContent-"
                "ID: {}".format(dc_obj.id)
            )
            if snippet_info.get("url") == dc_obj.old_diff_image.url:
                wu_obj.snippet_info["url"] = wu_obj.old_image.url

    if not is_image_uploaded:
        wu_obj.save()

    # -------------------- Updating multi field value Tags --------------------
    for tmf in wt_constant.TAGS_MULTI_FIELDS:
        m2m_wu_tags = getattr(wu_obj, tmf)
        # if tmf == "custom_tags":
        #     m2m_wu_tags.filter(bucket__company__company_preferences__id=client_id).clear()
        # else:
        m2m_wu_tags.clear()

    custom_tags_id = []
    for bid, tags_map in post_data.get("tagsMap", {}).items():
        tags_id = list(tags_map.keys())
        if not tags_id:
            continue

        if str(bid).isdigit():
            custom_tags_id.extend(tags_id)
            continue

        field_name = wt_constant.STD_BUCKET_ID_FIELDS_MAP.get(bid)
        if not field_name or field_name in wt_constant.TAGS_SINGLE_FIELDS:
            continue

        if field_name in wt_constant.TAGS_MULTI_FIELDS:
            m2m_wu_tags = getattr(wu_obj, field_name)
            m2m_wu_tags.add(*tags_id)

    if custom_tags_id:
        wu_obj.custom_tags.add(*list(
            CustomTag.objects.filter(
                id__in=custom_tags_id, active=True,
                bucket__company__company_preferences__id=client_id
            ).values_list("id", flat=True)
        ))

    logger.info(
        f"CreateUpdateWebUpdate view called for WebUpdateId: {wu_obj.id} and "
        f"language: {wu_obj.language}"
    )
    #####################################################
    # save records of add and edits in journal table and
    # send mail of the changes to concerned person.
    # ###################################################
    copied_wu = None
    if is_update_exists:
        old_article, old_buckets_map = get_article(old_wu_copy)
        new_article, new_buckets_map = get_article(wu_obj)
        if new_article == old_article and old_buckets_map == new_buckets_map:
            raise NoChangeInWebUpdateEdit
        articles = [dict(old_article, **old_buckets_map), dict(new_article, **new_buckets_map)]
        articles_to_mail = [(old_article, old_buckets_map), (new_article, new_buckets_map)]
    else:
        # ToDo: Create a job for this which will trigger at certain interval
        #       and will create o copies for the published webupdates.
        if make_copy_for_si:
            copied_wu = copy.deepcopy(wu_obj)
            copied_wu.id = None
            copied_wu.client_id = CONTIFY_FOR_SALES_COMPANY_PREFERENCE_ID
            copied_wu.save()
            # Setting m2m field values
            for bucket, info in STANDARD_BUCKETS.items():
                try:
                    wu_field_map = info["story_field_map"]
                    if wu_field_map and wu_field_map["type"] == list:
                        (
                            getattr(copied_wu, wu_field_map["name"])
                            .add(*getattr(wu_obj, wu_field_map["name"]).all())
                        )
                except AttributeError:
                    continue

        new_article, buckets_map = get_article(wu_obj)
        articles = [dict(new_article, **buckets_map)]
        articles_to_mail = [(new_article, buckets_map)]
    if dc_obj:
        dc_obj.status = wt_enum.DiffStatus.PUBLISHED.value
        dc_obj.save()
    # Send email to Ops team and create a version for the update
    send_mail_analyst(articles_to_mail, wu_obj.id, user.subscriber)
    newsfeed_create_webupdate_journal(
        user, articles, wu_obj.id
    )

    return wu_obj, copied_wu, created


@transaction.atomic
def edit_web_update(post_data, subscriber):  # This is called from newsFeed
    """
        This is used for editing of WebUpdate from newsFeed
    """
    user = subscriber.user
    wu_obj = WebUpdate.objects.get(id=post_data["uuid"])

    old_wu_copy = copy.deepcopy(wu_obj)
    old_article, old_buckets_map = wu_mails.get_article(old_wu_copy)

    wu_obj.title = post_data["title"]
    wu_obj.description = post_data["summary"]
    wu_obj.status = post_data.get("status") or StoryStatus.PUBLISHED.value
    wu_obj.email_priority = post_data.get("emailPriority")

    wu_obj.approved_by_id = user.id
    wu_obj.updated_by_id = user.id

    wu_obj.user_updated_on = datetime.now()
    wu_obj.approved_on = datetime.fromtimestamp(int(post_data["pubDate"]))

    # ----------------- Updating singled field value Tags ---------------------
    for tsf in wt_constant.TAGS_SINGLE_FIELDS:
        setattr(wu_obj, tsf, None)

    for bid, tags_map in post_data.get("tagsMap", {}).items():
        tags_id = tags_map
        # We were told that publishedByCompany will not be incorrect so not
        # changing it and also we using it to show the logo in change-log
        # detail page.
        if not tags_id or bid == "published_by_company":
            continue

        field_name = wt_constant.STD_BUCKET_ID_FIELDS_MAP.get(bid)

        if field_name in wt_constant.TAGS_SINGLE_FIELDS:
            # tags_id is non empty list passed from newsfeed frontend.
            if field_name in ["language"]:
                setattr(wu_obj, field_name, tags_id[0])
            elif tags_id[0]:
                setattr(wu_obj, f"{field_name}_id", int(tags_id[0]))

    new_article, new_buckets_map = wu_mails.get_article(
        wu_obj, post_data.get("tagsMap", {})
    )
    if old_article == new_article and old_buckets_map == new_buckets_map:
        raise NoChangeInWebUpdateEdit(
            f"No change in the WebUpdate {post_data['uuid']}"
        )

    # wu_obj.save()

    # -------------------- Updating multi field value Tags --------------------
    for tmf in wt_constant.TAGS_MULTI_FIELDS:
        m2m_wu_tags = getattr(wu_obj, tmf)
        m2m_wu_tags.clear()

    custom_tags_id = []
    for bid, tags_map in post_data.get("tagsMap", {}).items():
        if bid in ["language"]:
            continue

        tags_id = [int(t) for t in tags_map if t]
        if not tags_id:
            continue

        if str(bid).isdigit():
            custom_tags_id.extend(tags_id)
            continue

        field_name = wt_constant.STD_BUCKET_ID_FIELDS_MAP.get(bid)
        if field_name in wt_constant.TAGS_MULTI_FIELDS:
            m2m_wu_tags = getattr(wu_obj, field_name)
            m2m_wu_tags.add(*tags_id)

    if custom_tags_id:
        wu_obj.custom_tags.add(*list(
            CustomTag.objects.filter(
                id__in=custom_tags_id, active=True,
                bucket__company__company_preferences__id=wu_obj.client_id
            ).values_list("id", flat=True)
        ))

    wu_obj.save()

    logger.info(
        f"EditWebUpdate view called for WebUpdateId: {wu_obj.id} and "
        f"language: {wu_obj.language}"
    )

    #####################################################
    # save records of edits in journal table and send mail
    # of the changes to concerned person.
    # ###################################################
    articles_to_mail = [(old_article, old_buckets_map), (new_article, new_buckets_map)]
    articles = [dict(old_article, **old_buckets_map), dict(new_article, **new_buckets_map)]
    wu_mails.send_mail_analyst(articles_to_mail, wu_obj.id, subscriber)
    newsfeed_create_webupdate_journal(
        user, articles, wu_obj.id
    )

    cxt = {
        "currentDoc": {
            "id": wu_obj.id,
            "isPaidContentSource": wu_obj.source.is_paid,
            "status": wu_obj.status,
            "approvedBy": user.username,
            "source": [{
                "id": wu_obj.source_id,
                "name": wu_obj.source.name,
                "cs_domain_url": wu_obj.source.domain_url,
                "url": wu_obj.get_redirecting_url()
            }],
        },
        "rootID": wu_obj.id,
        "childDocIds": []  # we are not using child ids in front-end, so
        # sending always empty
    }
    return cxt


def mark_complete_new_change_html(html, base_url):
    parser = etree.HTMLParser(
        encoding="utf-8", remove_comments=True, compact=False
    )
    html_tree = etree.parse(StringIO(html), parser)
    body_tag = html_tree.xpath("//body")
    if body_tag:
        body_tag = body_tag[0]
        old_style = body_tag.attrib.get("style", "")
        body_tag.attrib["style"] = (
            old_style + "border: 4px solid green !important;"
        )

    patch_base_tag(html_tree, base_url)
    return etree.tounicode(html_tree, method="html")


def update_diff_screenshot(model_cls, item_id, raw_image, fl_name):
    model_name = model_cls.__class__.__name__
    valid_fields = [
        "old_image", "new_image", "old_diff_image", "new_diff_image"
    ]
    if fl_name not in valid_fields:
        return ValueError(f"Invalid target field to {model_name}")

    item_obj = model_cls.objects.get(id=item_id)

    f_name = "{}.jpeg".format(datetime.now().strftime("%d-%m-%Y-%H-%M-%S"))

    if raw_image:
        item_img_obj = getattr(item_obj, fl_name)
        item_img_obj.save(f_name, ContentFile(raw_image))
        logger.info(
            f"ChangeImage{model_name}!, {fl_name} uploaded from {model_name}-"
            f"ID: {item_id}"
        )
        return getattr(item_obj, fl_name).url

    raise ValueError(
        f"Empty Image ({fl_name}) given for {model_name}ID: {item_id}"
    )


def get_change_log_detail_cxt(web_update_id):
    def _get_display_name(cache_map, language):
        return cache_map.get(language)
    # TODO: Remove below query when indexing of WebUpdate is at a separate core
    wu_obj = WebUpdate.objects.get(id=web_update_id)
    client_id = wu_obj.client_id

    es_server_name = es_utils.get_search_es_server_name(client_id=client_id)
    _es = get_es(es_server_name)
    _cache = BucketCacheReader()

    exclude_source = {
        "excludes": [
            "title_*", "lead_*", "body_*", "fl_*", "fc_*", "word_cloud"
        ]
    }

    # Getting request doc first and few change logs for the same WebSource
    root_qd = {
        "_source": exclude_source,
        "query": {
            "ids": {
                "values": [f"wu.{web_update_id}"]
            }
        }
    }

    dt = datetime.now()
    root_res = _es.search(**root_qd)
    logger.info(
        "ESChangeLogDetailCxt! response time: {} (seconds) for index: {},"
        " web_update_id: {} and query: {}".format(
            (datetime.now() - dt).total_seconds(), es_server_name,
            web_update_id, root_qd
        )
    )

    root_hits = root_res.raw_content["hits"]["hits"]
    if not root_hits:
        # Considering WebUpdate is not indexed ES, so reindexing it
        logger.info(
            f"ChangeLogService!, Indexing WebUpdate id: {web_update_id} as "
            f"this is not available in ES"
        )
        index_web_updates([web_update_id], True)

        # Getting request doc first and few change logs for the same WebSource
        dt = datetime.now()
        root_res = _es.search(**root_qd)
        logger.info(
            "ESChangeLogDetailCxt! response time: {} (seconds) for index: {},"
            " web_update_id: {} and query: {}".format(
                (datetime.now() - dt).total_seconds(), es_server_name,
                web_update_id, root_qd
            )
        )

        root_hits = root_res.raw_content["hits"]["hits"]
        if not root_hits:
            return {}

    root_doc = root_hits[0]["_source"]
    raw_doc_list = [root_doc]
    archive_docs = fetch_change_log(
        client_id=client_id,
        post_data={
            "solrUUID": root_doc["uuid"],
            "pubDate": root_doc["pub_date"],
            "webSourceId": root_doc["web_source_id"],
        },
        source=exclude_source,
        doc_converter=lambda hit: hit["_source"],
    ) if "web_source_id" in root_doc else []

    raw_doc_list.extend(archive_docs)

    accessible_buckets = get_client_buckets_info(client_id, "en")
    display_tags_formatters = {}
    language_key = "name.en"

    pbc_b_id = "published_by_company"

    for bucket in accessible_buckets:
        bucket_id = str(bucket['id'])
        if bucket_id in ["rating"]:
            continue

        display_tags_formatters[bucket_id] = [
            language_key, "twitter", "scope_note"
        ]

    if pbc_b_id not in accessible_buckets:
        display_tags_formatters[pbc_b_id] = [language_key, "twitter", "logo"]

    # Prepare cache with buckets of docs' display tags
    for bucketId, f_prop in display_tags_formatters.items():
        _cache.queue_get_category(bucketId, language_key)

    # walk docs to extract items and prepare cache
    for doc in raw_doc_list:
        display_tags_map = doc.get("display_tags") or {}
        if not display_tags_map:
            continue

        for b_id, f_prop in display_tags_formatters.items():
            d_tags = display_tags_map.get(f"d_{b_id}")
            if d_tags is not None:
                if not isinstance(d_tags, list):
                    d_tags = [d_tags]

                for t_id in d_tags:
                    _cache.queue_get_value(b_id, t_id, f_prop)

    _cache.execute()

    docs = []
    pbc_info = {}

    for doc in raw_doc_list:
        out = {
            "id": doc["uuid"],
            "title": doc.get("title", ""),
            "body": doc.get("body", ""),
            "pub_date": doc.get('pub_date'),
            "web_source_web_url": doc.get("web_source_web_url", ""),
            "diff_content_id": doc.get("diff_content_id"),
            "old_image": doc.get("old_image", ""),
            "new_image": doc.get("new_image", ""),
            "pbc_info": {}
        }

        tags = defaultdict(list)
        display_tags_map = doc.get("display_tags") or {}
        # As now, bucket name can be Type of Content & Type of Source and must
        # be displayed as TC & TS, so using below regex to exclude 'of'.
        stopword_regex = re.compile(r'of')
        for bucket in accessible_buckets:
            bucket_id = bucket["id"]
            d_tags = display_tags_map.get(f"d_{bucket_id}")

            if d_tags is not None:
                if not isinstance(d_tags, list):
                    d_tags = [d_tags]

                b_info = _cache.get_category(bucket_id)

                for tag_id in d_tags:
                    tag_info = _cache.get_value(bucket_id, tag_id)

                    tag_name = _get_display_name(tag_info, language_key)
                    bucket_name = _get_display_name(b_info, language_key)

                    if not bucket_name:
                        bucket_name = _get_display_name(b_info, 'n')

                    if not tag_name:
                        continue

                    tag_details = {
                        "id": tag_id,
                        "name": tag_name,
                        # ToDO: use this formatting in frontend using bucket_color.js
                        "bucket_name": ''.join(
                            [
                                s[0] for s in bucket_name.split()
                                if not stopword_regex.search(s)
                            ]
                        )
                    }
                    if tag_info.get("twitter"):
                        tag_details["twitter"] = tag_info.get("twitter")

                    tags[bucket_id].append(tag_details)

        out["tags"] = dict(tags)

        # Getting PublishedByCompany information
        if not pbc_info and f"d_{pbc_b_id}" in display_tags_map:
            d_tags = display_tags_map[f"d_{pbc_b_id}"]
            if d_tags is not None:
                pbc_id = d_tags[0]
                tag_info = _cache.get_value(pbc_b_id, pbc_id)

                if not tag_info.get('logo'):
                    pbc_logo = DEFAULT_COMPANY_LOGO
                else:
                    media_rel_url = settings.MEDIA_URL_RELATIVE
                    if settings.DEBUG:
                        media_rel_url = "//112233.contify.com/"
                    pbc_logo = media_rel_url + tag_info['logo']

                pbc_name = _get_display_name(tag_info, language_key)

                pbc_info = {
                    "id": pbc_id,
                    "name": pbc_name,
                    "logo": pbc_logo
                }

        docs.append(out)

    if not pbc_info:
        pbc_info["logo"] = DEFAULT_COMPANY_LOGO

    cxt = {
        "numFound": len(docs),
        "docs": docs,
        "targetWebUpdateID": story_entity_id_to_solr_id(web_update_id, "wu"),
        "publishedByCompany": pbc_info
    }
    return cxt


def fetch_change_log(client_id, post_data, offset=0, rows=20, source=None,
                     doc_converter=None):
    def _doc_converter(hit):
        # hit is a ES doc similar to solr
        h_source = hit["_source"] or {}
        e_n, e_id = solr_uuid_to_story_entity_info(h_source["uuid"])
        doc = {
            "uuid": e_id,
            "solrUUID": h_source["uuid"],
            "docType": DocType.WEB_UPDATE.value,
            "title": h_source.get("title", ""),
            "status": h_source.get("status"),
            "pubDate": h_source.get("pub_date"),
            "webSourceId": h_source["web_source_id"]
        }
        return doc

    if not post_data:
        return []

    web_source_id = post_data.get("webSourceId")
    if not web_source_id:
        return []

    wu_solr_id = post_data["solrUUID"]
    pub_date = post_data["pubDate"]

    status = [2]

    source = source or [
        "uuid", "title", "status", "pub_date", "web_source_id"
    ]
    doc_converter = doc_converter or _doc_converter

    timeline = Timeline(pub_date)
    start, end = timeline.get_timeline_as_iso_strings(tz='UTC')

    qd = {
        "track_total_hits": False,
        "size": rows,
        "from": offset,
        "_source": source,
        "sort": [
            {
                "pub_date": {
                    "order": "desc"
                }
            }
        ],
        "query": {
            "bool": {
                "filter": [
                    {
                        "term": {
                            "doc_type": DocType.WEB_UPDATE.value
                        }
                    },
                    {
                        "terms": {
                            "status": status
                        }
                    },
                    {
                        "term": {
                            "web_source_id": web_source_id
                        }
                    },
                    {
                        "range": {
                            "pub_date": {
                                "lte": start + "||/s"
                            }
                        }
                    }
                ]
            }
        }
    }
    if wu_solr_id:
        qd["query"]["bool"]["must_not"] = [
            {
                "terms": {
                    "uuid": [wu_solr_id]
                }
            }
        ]

    es_server_name = es_utils.get_search_es_server_name(client_id=client_id)
    es = get_es(es_server_name)

    dt = datetime.now()
    response = es.search(**qd)
    hits = response.raw_content["hits"]["hits"]

    logger.info(
        "ESFetchChangeLog! response time: {} (seconds) for index: {},"
        " web_source_id: {} and query: {}".format(
            (datetime.now() - dt).total_seconds(), es_server_name,
            web_source_id, qd
        )
    )

    tmp_doc_list = [doc_converter(h) for h in hits]
    return tmp_doc_list


def get_cxt_diff_html(diff_obj):
    model_name = diff_obj.__class__.__name__
    cxt = {
        "old_html": diff_obj.old_diff_html or wt_constant.FF_OLD_DIFF_HTML,
        "new_html": diff_obj.new_diff_html,
        "obj_id": diff_obj.id,
        "regen_screenshot_old": (
            f"http://{WST_SCREENSHOT_IP}:{WST_SCREENSHOT_PORT}/"
            f"website-tracking/regenerate-screenshot/?obj_id={diff_obj.id}"
            f"&obj_type={model_name}&fl=old_diff_html"
        ),
        "regen_screenshot_new": (
            f"http://{WST_SCREENSHOT_IP}:{WST_SCREENSHOT_PORT}/"
            f"website-tracking/regenerate-screenshot/?obj_id={diff_obj.id}"
            f"&obj_type={model_name}&fl=new_diff_html"
        )
    }
    return cxt
