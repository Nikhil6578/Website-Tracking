"""
Manages processing DiffHtml objects to create DiffContent.

This command processes DiffHtml objects, which represent the differences between
two web page snapshots, to generate DiffContent objects. DiffContent stores
the actual visual and textual differences, along with screenshots, making it
easier to review changes between web page versions.

Key Points:
-   Uses Playwright to capture screenshots of the old and new HTML content,
    using same context [for single DiffHtml].
-   Each context is authenticated so we do not need to do so for the pages.
-   Stores the processed data, including screenshots, in DiffContent objects.
-   Handles errors gracefully, logging them and marking DiffHtml objects as failed.
    If DiffHtml fails, we create a failed DiffContent so client don't miss an
    important update.
-   Only saves relevant changed fields to optimize DB operations.

Example Usage:
-   Process DiffHtml objects (24 hour category):
    `python manage.py process_diff_html`
-   Process failed DiffHtml objects:
    `python manage.py process_diff_html --failed`
-   Process DiffHtml objects in shard 1 of 4:
    `python manage.py process_diff_html --shard_no 1 --max_shard 4`
"""
# Python Imports
import os
import asyncio
import fcntl
import logging
import traceback
import dateparser
from typing import Dict
from collections import defaultdict
from asgiref.sync import sync_to_async
from datetime import datetime, timedelta

# Django Imports
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.base import ContentFile
from django.core.management import BaseCommand
from django.db import IntegrityError, transaction
from playwright.async_api import async_playwright, Error as PlaywrightError

# Project Imports
from config.constants import EXPLICIT_WSTR_SITE_URL
from config.utils import encrypt_string
from contify.website_tracking.utils import (
    prepare_error_report, authenticate_context, set_values,
    handle_cookie_dialog, close_all_popups
)
from contify.website_tracking.models import WebSource
from contify.website_tracking import cfy_enum as wt_enum
from contify.website_tracking import constants as wt_constant
from contify.website_tracking.web_snapshot.models import (
    DiffContent, DiffHtml, WebSnapshot
)

logger = logging.getLogger(__name__)

# Constants
MAX_RETRY = 3
PROCESS_TIMEOUT = 2 * 60 * 60  # 2 hours in seconds
ERROR_DICT = defaultdict(list)
# Need this for async DB operations(IMPORTANT)
_diff_html_save = sync_to_async(
    lambda instance, update_fields: instance.save(update_fields=update_fields)
)


class Command(BaseCommand):
    LOCK_FILE = '/tmp/process_diff_html'
    """Manages processing DiffHtml objects to create DiffContent."""

    def __init__(self):
        super().__init__()
        self.start_date = datetime.now()
        self.processed_count = 0
        self.failed_count = 0

    def add_arguments(self, parser):
        """Define command-line arguments."""
        parser.add_argument(
            "--new_wss_ids", action="store", dest="new_wss_ids", default=None,
            type=set_values,
            help="Process DiffHtml based on provided new WebSnapshots IDs"
        )
        parser.add_argument(
            "--failed", action="store_true", dest="process_failed",
            default=False,
            help="Process failed DiffHtml objects."
        )
        parser.add_argument(
            "-d", "--duration", dest="duration", default=24, type=int,
            help="Process DiffHtml for last n hours. Default=24 Hours"
        )
        parser.add_argument(
            "--from_date", dest='from_date', type=str,
            help="Start date (YYYY-MM-DD)."
        )
        parser.add_argument(
            "--to_date", dest='to_date', type=str,
            help="End date (YYYY-MM-DD)."
        )
        parser.add_argument(
            "-b", "--batchSize", action="store", dest="batch_size",
            type=int, default=100, help=(
                "Batch size used for processing the WebSnapshot"
            )
        )
        parser.add_argument(
            "--shardNo", action="store", dest="shard_no", type=int,
            default=None, help="Used for sharding in job"
        )
        parser.add_argument(
            "--maxShard", dest="max_shard", type=int, default=4,
            help="The maximum cluster size to process"
        )

    def handle(self, *args, **options):
        logger.info(
            f"ProcessDiffHtml!, handle function initiated with args: {args} "
            f"and options: {options}"
        )

        from_date = options.get('from_date')
        to_date = options.get('to_date')
        date_24_hours_back = self.start_date - timedelta(
            hours=options["duration"]
        )
        shard_no = options.get('shard_no')
        try:
            from_date = dateparser.parse(from_date) if from_date else date_24_hours_back
            to_date = dateparser.parse(to_date) if to_date else self.start_date
            if not from_date or not to_date:
                raise ValueError('Invalid Date!')
        except Exception as err:
            raise ValueError(f'Invalid Date Format! Error: {err}')

        if from_date >= to_date:
            raise ValueError(
                'from_date cannot be greater than or equal to to_date.'
            )

        status = wt_enum.DiffHtmlStatus.DRAFT.value
        process_failed = options.get("process_failed")
        if process_failed:
            status = wt_enum.DiffHtmlStatus.FAILED.value

        extra_str = ''
        if options:
            for k, v in list(options.items()):
                extra_str += '{}_{}'.format(k, v)
        try:
            lock_file_name = f"{self.LOCK_FILE}_{extra_str}"
            lock_fp = open(lock_file_name, 'w')
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            logger.warning(
                "ProcessDiffHtml!, job is probably already running for and "
                "shard {}".format(shard_no)
            )
            return

        dh_queryset = DiffHtml.objects.filter(
            status=status,
            state=wt_enum.State.ACTIVE.value,
            created_on__gte=from_date,
            created_on__lte=to_date,
        ).order_by("created_on")

        new_wss_ids = options["new_wss_ids"]
        if new_wss_ids:
            dh_queryset = dh_queryset.filter(
                new_web_snapshot_id__in=new_wss_ids
            )

        # Sharding logic to distribute load across multiple processes
        max_shards = options['max_shard']
        if shard_no:
            dh_queryset = dh_queryset.extra(
                where=[f"{DiffHtml._meta.db_table}.id %% {max_shards} = %s"],
                params=[shard_no]
            )

        err_msg = ''
        if dh_queryset.exists():
            try:
                # Important [List conversion is necessary]
                dh_queryset = list(dh_queryset[:options["batch_size"]])
                asyncio.run(
                    asyncio.wait_for(self.process_diff_htmls(dh_queryset), timeout=PROCESS_TIMEOUT)
                )
            except asyncio.TimeoutError:
                # kill the process group.[Parent + child processes]
                os.killpg(os.getpgid(os.getpid()), 15)
                logger.info(
                    "asyncio.TimeoutError | Command timed out (2 hours)."
                )
            except Exception as err:
                err_msg = f" |\nError: {err} |\nTraceback: {traceback.format_exc()}"
        else:
            logger.info("No DiffHtml to process found!")

        processed_count = len(dh_queryset)
        if ERROR_DICT:
            prepare_error_report(ERROR_DICT, processed_count,"process_diff_html")

        end_time = datetime.now()
        execution_time = (end_time - self.start_date).seconds
        logger.info(
            f'DiffHtml fetch job '
            f'Started At: {self.start_date.strftime("%b %d %H:%M:%S")} | '
            f'Finished At: {end_time.strftime("%b %d %H:%M:%S")} | '
            f'Time Taken: {execution_time // 60} mins {execution_time % 60} secs | '
            f'Shard No.: {shard_no} | Max Shards: {max_shards} | '
            f'Process Failed DiffHtml: {process_failed} | '
            f'Total DiffHtml(s) Processed: {self.processed_count} | '
            f'Failed: {self.failed_count}' + err_msg
        )

    async def process_diff_htmls(self, diff_html_queryset):
        """Processes DiffHtml objects in batches."""
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-http2",
                    "--disable-setuid-sandbox",
                    f"--window-size={wt_constant.DEFAULT_VIEWPORT_WIDTH},{wt_constant.DEFAULT_VIEWPORT_HEIGHT}",
                ],
            )

            for diff_html in diff_html_queryset:
                last_error = None
                diff_status = wt_enum.DiffHtmlStatus.PROCESSED.value
                update_fields = ["status", "updated_on", "last_error"]
                context = None
                try:
                    try:
                        context = await browser.new_context(**wt_constant.CONTEXT_DICT)
                        context = await authenticate_context(context, diff_html.id)
                    except Exception as err:
                        diff_html.last_error = f"{err}\n{traceback.format_exc()}"
                        diff_html.status = wt_enum.DiffHtmlStatus.FAILED.value
                        diff_html.updated_on = datetime.now()
                        await _diff_html_save(diff_html, update_fields)
                        if context:
                            await context.close()
                        await browser.close()
                        return

                    if context:
                        diff_data = await self.get_diff_content_info(diff_html, context)
                        await _create_diff_content(diff_html, diff_data)
                        self.processed_count += 1
                except IntegrityError as e:
                    self.failed_count += 1
                    logger.info(f"DiffContent exists for DiffHtml ID: {diff_html.id}")
                    ERROR_DICT[str(e)].append({diff_html.id: traceback.format_exc()[:1000]})

                except Exception as e:
                    fail_e = e
                    # Creating a DiffContent for failed diff-html, we are doing
                    # this so because the DiffHtml might be an important
                    # update for a client.
                    try:
                        await _create_diff_on_fail(diff_html)
                    except Exception as err:
                        fail_e = err
                    finally:
                        self.failed_count += 1
                        diff_status = wt_enum.DiffHtmlStatus.FAILED.value
                        last_error = f"{fail_e}\n\n{traceback.format_exc()}"
                        ERROR_DICT[str(fail_e)].append({diff_html.id: traceback.format_exc()[:1000]})
                        logger.info(f"Error processing DiffHtml ID {diff_html.id}: {fail_e}")

                finally:
                    diff_html.status = diff_status
                    diff_html.last_error = last_error
                    diff_html.updated_on = datetime.now()
                    await _diff_html_save(diff_html, update_fields)
                    if context:
                        await context.close()

            await browser.close()

    async def get_diff_content_info(self, diff_html, context):
        """Gets diff content info and screenshots."""

        async def fetch_screenshot(field_name):
            screenshot = None
            if getattr(diff_html, field_name):
                page = await context.new_page()
                screenshot = await self.get_diff_html_screenshot(
                    page, diff_html, field_name
                )
                await page.close()
            return screenshot

        old_screenshot_task = fetch_screenshot("old_diff_html")
        new_screenshot_task = fetch_screenshot("new_diff_html")

        # Await the tasks concurrently
        old_screenshot, new_screenshot = await asyncio.gather(
            old_screenshot_task, new_screenshot_task
        )
        return {
            "old_snapshot_id": diff_html.old_web_snapshot_id,
            "old_diff_html": diff_html.old_diff_html,
            "old_diff_screenshot": old_screenshot,
            "new_snapshot_id": diff_html.new_web_snapshot_id,
            "new_diff_html": diff_html.new_diff_html,
            "new_diff_screenshot": new_screenshot,
            "added_diff_info": diff_html.added_diff_info,
            "removed_diff_info": diff_html.removed_diff_info,
        }

    @staticmethod
    async def get_diff_html_screenshot(page, diff_html, field_name):
        """Fetches screenshot of the diff HTML."""
        diff_html_id = diff_html.id
        encoded_id = encrypt_string(diff_html_id)
        encoded_field = wt_constant.ENC_DIFF_FIELD_MAP[field_name]
        url = f"{EXPLICIT_WSTR_SITE_URL}kvJaZdkFH3pZKwwLIp/{encoded_id}/{encoded_field}/nxxUNHlc20Aix1O4ir/"
        logger.info(f"DiffHtml ID: {diff_html_id} | Field: {field_name} | URL: {url}")
        last_error = ""
        for attempt in range(1, MAX_RETRY+1):
            try:
                logger.info(f"Attempt {attempt + 1} for URL: {url}")
                response = await page.goto(url, wait_until="domcontentloaded", timeout=300000)
                await asyncio.sleep(2)
                if response and response.status and response.status == 200:
                    try:
                        w_snap_obj = await sync_to_async(
                            lambda: WebSnapshot.objects.only(
                                'web_source_id'
                            ).get(id=diff_html.new_web_snapshot_id))()
                        ws_obj = await sync_to_async(
                            lambda: WebSource.objects.only(
                                'accept_cookie_xpaths'
                            ).get(id=w_snap_obj.web_source_id))()
                        accept_cookie_xpaths = ws_obj.accept_cookie_xpaths
                        if accept_cookie_xpaths:
                            for accept_cookie_xpath in accept_cookie_xpaths:
                                await handle_cookie_dialog(
                                    page, xpath=accept_cookie_xpath
                                )
                        else:
                            await close_all_popups(page)
                    except ObjectDoesNotExist as e:
                        logger.info(f"WebSnapShot object is missing: {e}")
                    except Exception as e:
                        logger.exception(
                            f"Error occur during cookie handling: {e}"
                        )
                    await page.evaluate("window.scrollTo(0, 0)")
                    await page.wait_for_timeout(1000)

                    await page.add_style_tag(content="""
                        *, *::before, *::after {
                            animation: none !important;
                            transition: none !important;
                        }
                    """)
                    await page.evaluate("document.body.offsetHeight")
                    screenshot = await page.screenshot(
                        type="jpeg", full_page=True, quality=100
                    )
                    if screenshot:
                        return screenshot
                else:
                    logger.info(
                        f'Error while capturing diff html screenshot '
                        f'diff_html_id: {diff_html_id}.'
                    )
                    raise

            except PlaywrightError as e:
                last_error = f"Error: {e} | TraceBack: {traceback.format_exc()}"
                logger.info(f"PlaywrightError: {e} | URL: {url} | Attempt: {attempt}")
                ERROR_DICT[str(e)].append({diff_html_id: traceback.format_exc()[:1000]})

        else:
            logger.info(
                f"DiffHtml ID: {diff_html_id} | Field: {field_name} | Failed to "
                f"fetch after {MAX_RETRY} attempts | Error: {last_error}")


@transaction.atomic
def create_diff_content(diff_html: DiffHtml, diff_data: Dict) -> None:
    """Creates a DiffContent object."""
    logger.info(f"Creating DiffContent for DiffHtml ID: {diff_html.id}")
    dc = DiffContent(
        old_snapshot_id=diff_data["old_snapshot_id"],
        old_diff_html=diff_data["old_diff_html"],
        new_snapshot_id=diff_data["new_snapshot_id"],
        new_diff_html=diff_data["new_diff_html"],
        state=wt_enum.State.ACTIVE.value,
        added_diff_info=diff_data["added_diff_info"],
        removed_diff_info=diff_data["removed_diff_info"],
    )

    def save_image(field_name, image_data):
        if image_data:
            filename = f"{datetime.now().strftime('%d-%m-%Y-%H-%M-%S')}.jpeg"
            getattr(dc, field_name).save(filename, ContentFile(image_data))
            logger.info(f"Saved {field_name} for DiffContent ID: {dc.id}")

    if diff_data.get("old_diff_screenshot"):
        save_image("old_diff_image", diff_data["old_diff_screenshot"])
    if diff_data.get("new_diff_screenshot"):
        save_image("new_diff_image", diff_data["new_diff_screenshot"])

    logger.info(
        f"Created DiffContent ID: {dc.id} for DiffHtml ID: {diff_html.id} | "
        f"Old image: {bool(diff_data.get('old_diff_screenshot'))} | "
        f"New image: {bool(diff_data.get('new_diff_screenshot'))}"
    )


_create_diff_content = sync_to_async(create_diff_content)


def create_diff_on_fail(diff_html: DiffHtml) -> None:
    """Creates a DiffContent object for a failed DiffHtml processing."""
    if not diff_html.added_diff_info and not diff_html.removed_diff_info:
        logger.info(
            f"No diff info in DiffHtml ID: {diff_html.id}. "
            f"Skipping DiffContent creation."
        )
        return

    dc = DiffContent(
        old_snapshot_id=diff_html.old_web_snapshot_id,
        old_diff_html=diff_html.old_diff_html,
        new_snapshot_id=diff_html.new_web_snapshot_id,
        new_diff_html=diff_html.new_diff_html,
        state=wt_enum.State.ACTIVE.value,
        added_diff_info=diff_html.added_diff_info,
        removed_diff_info=diff_html.removed_diff_info,
    )
    dc.save()
    logger.info(
        f"DiffHtml ID: {diff_html.id} Failed | Created DiffContent ID: {dc.id}"
    )


_create_diff_on_fail = sync_to_async(create_diff_on_fail)
