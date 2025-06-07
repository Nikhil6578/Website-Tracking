"""
This command automates the process of scraping and storing snapshots of web
pages defined in the WebSource model.

Key Points:
-   Retrieves active WebSource records, which define the URLs to be scraped.
-   Uses Playwright to:
    -   Launch a headless browser.
    -   Navigate to each web page.
    -   Optionally handle cookie dialogs to ensure proper page loading.
    -   Scroll the page to load dynamically loaded content.
    -   Capture the full HTML content of the page.
    -   Capture a full-page screenshot of the page.
-  Calculates an MD5 hash of the cleaned HTML content to detect changes.
-  Stores the HTML and screenshot as a WebSnapshot in the database *only if*
   the content has changed since the last snapshot. (unique MD5 hash)
-  Implements retry logic to handle transient network errors or website issues.

Usage:
python manage.py scrape_web_sources --frequency 24 --batchSize 100
"""

# Python Imports
import os
import copy
import asyncio
import fcntl
import logging
import traceback
from collections import defaultdict
from asgiref.sync import sync_to_async
from datetime import datetime, timedelta

# Django Imports
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db.utils import DataError
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Q
from playwright.async_api import (
    async_playwright, TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError
)

# Project Imports
from contify.website_tracking.cfy_enum import (
    RunTimeFrequency, State, SnapshotStatus
)
from contify.website_tracking.constants import (
    DEFAULT_VIEWPORT_WIDTH, DEFAULT_VIEWPORT_HEIGHT, CONTEXT_DICT
)
from contify.website_tracking.models import WebSource
from contify.website_tracking.web_snapshot.models import WebSnapshot
from contify.website_tracking.utils import (
    set_values, clean_invisible_element, prepare_error_report,
    handle_cookie_dialog, close_all_popups
)
from contify.website_tracking.service import (
    get_md5_hash_of_string, is_web_snapshot_exists
)

# Limits and configurations constants
MAX_RETRY = 3  # Maximum number of retries for fetching a webpage
BATCH_GROUP = 2  # Number of pages processed together in a batch
CONTEXT_OPEN_DELAY = 1  # Delay before opening a new browser context
PROCESS_TIMEOUT = 1 * 60 * 60
ERROR_DICT = defaultdict(list)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    LOCK_FILE = '/tmp/fetch_web_source'

    FREQUENCY_RUN_TIME_MAP = {
        RunTimeFrequency.EVERY_6_HOURS.value: 6,
        RunTimeFrequency.EVERY_12_HOURS.value: 12,
        RunTimeFrequency.EVERY_24_HOURS.value: 24
    }  # {0: 6, 1: 12, 2: 24}

    def __init__(self):
        super().__init__()
        self.total_snapshots_created = 0  # Counter to track created snapshots
        self.no_change_detected = 0  # Counter to track ws with no change
        self.failed_ws = 0  # Counter to track failed ws

    def add_arguments(self, parser):
        parser.add_argument(
            "-b", "--batchSize",  dest="batch_size", type=int, default=100,
            help="Batch size"
        )
        parser.add_argument(
            "-i", "--ids", type=set_values, dest="ws_ids",
            help="Comma-separated WebSource IDs"
        )
        parser.add_argument(
            "-f", "--frequency", dest="frequency", type=int,
            default=RunTimeFrequency.EVERY_24_HOURS.value
        )
        parser.add_argument(
            "-u", "--web_url", type=set_values, dest="web_urls",
            help="Comma-separated WebSource URLs"
        )
        parser.add_argument(
            "--shardNo", action="store", dest="shard_no", type=int,
            default=None, help="Shard number to process"
        )
        parser.add_argument(
            "--maxShard", dest="max_shards", type=int, default=4,
            help="The maximum number of shards to process"
        )
        parser.add_argument(
            "--in_client_ids", action="store", dest="include_client_ids",
            default=None, type=set_values, help="Client IDs to process"
        )
        parser.add_argument(
            "--ex_client_ids", action="store", dest="exclude_client_ids",
            default=None, type=set_values, help="Client IDs not to process"
        )

    def handle(self, *args, **options):
        """Handles the command execution."""
        start_time = datetime.now()
        frequency = options["frequency"]
        shard_no = options.get('shard_no')

        try:
            lock_file_name = f"{self.LOCK_FILE}_{frequency}_{shard_no}"
            lock_fp = open(lock_file_name, 'w')
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            logger.warning(
                f"FetchWebSource!, job is probably already running for "
                f"frequency: {frequency} and shard: {shard_no}"
            )
            return

        # Query WebSource records that are active and need processing
        ws_qs = (
            WebSource.objects
            .filter(
                state=State.ACTIVE.value, frequency=frequency,
                webclientsource__state=State.ACTIVE.value
            )
            .filter(
                Q(last_run__isnull=True) |
                Q(last_run__lte=datetime.now() - timedelta(
                    hours=self.FREQUENCY_RUN_TIME_MAP[frequency]
                )))
            .order_by('last_run')
        ).distinct()

        if options.get("ws_ids"):
            ws_qs = ws_qs.filter(id__in=options["ws_ids"])
        elif options.get("web_urls"):
            ws_qs = ws_qs.filter(web_url__in=options["web_urls"])

        include_client_ids = options.get("include_client_ids")
        exclude_client_ids = options.get("exclude_client_ids")
        if include_client_ids:
            ws_qs = ws_qs.filter(
                webclientsource__client_id__in=include_client_ids
            )
        if exclude_client_ids:
            ws_qs = ws_qs.exclude(
                webclientsource__client_id__in=exclude_client_ids
            )

        # Sharding logic to distribute load across multiple processes
        max_shards = options['max_shards']
        if shard_no:
            ws_qs = ws_qs.extra(
                where=[f"{WebSource._meta.db_table}.id %% {max_shards} = {shard_no}"]
            )

        if not ws_qs.exists():
            logger.info(
                f"No WebSources to process | Frequency: {frequency} "
                f"({self.FREQUENCY_RUN_TIME_MAP[frequency]} hours) | "
                f"Shard No.: {shard_no} | Max Shards: {max_shards}."
            )
            return

        err_msg = ""
        ws_list = list(ws_qs[:options["batch_size"]])
        try:
            asyncio.run(
                asyncio.wait_for(
                    self.process_batches(ws_list), timeout=PROCESS_TIMEOUT
                )
            )
        except asyncio.TimeoutError:
            # kill the process group.[Parent + child processes]
            os.killpg(os.getpgid(os.getpid()), 15)
            logger.info(
                "asyncio.TimeoutError | Command execution exceeded 1 hour."
            )
        except Exception as err:
            err_msg = f" |\nError: {err} |\nTraceback: {traceback.format_exc()}"

        end_time = datetime.now()
        execution_time = (end_time - start_time).seconds
        total_ws_qs_items = len(ws_qs)
        end_log = (
            f'WebSource fetch job '
            f'Started At: {start_time.strftime("%b %d %H:%M:%S")} | '
            f'Finished At: {end_time.strftime("%b %d %H:%M:%S")} | '
            f'Time Taken: {execution_time // 60} mins {execution_time % 60} secs | '
            f'Frequency: {frequency} ({self.FREQUENCY_RUN_TIME_MAP[frequency]} hours) | '
            f'Shard No.: {shard_no} | Max Shards: {max_shards} | '
            f'Total WebSource(s) Processed: {total_ws_qs_items} | '
            f'Total WebSnapshot(s) Created: {self.total_snapshots_created} | '
            f'WebSource(s) with no change detected: {self.no_change_detected}'
        ) + err_msg

        logger.info(end_log)
        if ERROR_DICT:
            prepare_error_report(
                ERROR_DICT, total_ws_qs_items, "fetch_web_source"
            )

    async def process_batches(self, ws_qs):
        """
        Processes WebSource objects in pairs, deciding processing strategy
        based on domain match.
        """
        async with async_playwright() as p:
            browser = None
            context = None
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
                for i in range(0, len(ws_qs), BATCH_GROUP):
                    # Rotate context every 10 batches
                    if i % (BATCH_GROUP * 10) == 0:
                        if context:
                            await context.close()
                        context = await browser.new_context(**CONTEXT_DICT)
                        await asyncio.sleep(CONTEXT_OPEN_DELAY)

                    batch = ws_qs[i:i + BATCH_GROUP]

                    # Different domain, process them parallely
                    if len(batch) == 2 and batch[0].domain == batch[1].domain:
                        await self.process_single_source(context, batch[0])
                        await asyncio.sleep(3)
                        await self.process_single_source(context, batch[1])
                    else:
                        await asyncio.gather(
                            *[self.process_single_source(context, ws) for ws in batch]
                        )

            finally:
                if context:
                    await context.close()
                if browser:
                    await browser.close()

    async def process_single_source(self, context, ws_obj, max_timeout=180):
        """Fetches a single WebSource and creates a snapshot if needed."""
        ws_obj_id = ws_obj.id
        web_url = ws_obj.web_url
        response = None

        page = await context.new_page()
        await page.set_viewport_size({
            "width": DEFAULT_VIEWPORT_WIDTH,
            "height": DEFAULT_VIEWPORT_HEIGHT,
        })

        for attempt in range(1, MAX_RETRY + 1):
            last_error = None
            logger.info(
                f"Initiated fetching WebSource ID: {ws_obj_id} | "
                f"Web URL: {web_url} | Attempt: {attempt}/{MAX_RETRY}"
            )
            try:
                response = await page.goto(
                    web_url, timeout=max_timeout * 1000,
                    wait_until="domcontentloaded"
                )

                accept_cookie_xpaths = ws_obj.accept_cookie_xpaths
                if accept_cookie_xpaths:
                    for accept_cookie_xpath in accept_cookie_xpaths:
                        await handle_cookie_dialog(
                            page, xpath=accept_cookie_xpath
                        )
                else:
                    await close_all_popups(page)
                await self.auto_scroll(page)

                # Remove animations and transitions to avoid flickering in
                # screenshot
                await page.add_style_tag(content="""
                    *, *::before, *::after {
                        animation: none !important;
                        transition: none !important;
                    }
                """)

                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(1000)
                height = await page.evaluate(
                    "document.documentElement.offsetHeight"
                )
                if height > DEFAULT_VIEWPORT_HEIGHT:
                    await page.set_viewport_size({
                        "width": DEFAULT_VIEWPORT_WIDTH,
                        "height": height - 10
                    })

                raw_html = await page.content()
                new_md5_hash = get_md5_hash_of_string(
                    clean_invisible_element(copy.deepcopy(raw_html.replace(
                        "\n", ""
                    )))
                )

                if not await is_web_snapshot_exists(ws_obj_id, new_md5_hash):
                    screenshot = None
                    try:
                        await page.wait_for_timeout(1000)
                        screenshot = await page.screenshot(
                            type="jpeg", full_page=True, quality=100
                        )
                    except PlaywrightError as e:
                        last_error = f"{e}\n{traceback.format_exc()}"
                        ERROR_DICT[str(e)].append(
                            {ws_obj_id: traceback.format_exc()[:1000]}
                        )
                    if screenshot:
                        await self.create_web_snapshot(
                            ws_obj, new_md5_hash, screenshot, raw_html
                        )
                        self.total_snapshots_created += 1
                else:
                    self.no_change_detected += 1
                    logger.info(
                        f"No changes detected for WebSource-ID: {ws_obj_id}, "
                        f"URL: {web_url}."
                    )
                break

            except (PlaywrightTimeoutError, PlaywrightError) as e:
                error_message = str(e)
                last_error = f"{e}\n{traceback.format_exc()}"
                ERROR_DICT[str(e)].append(
                    {ws_obj.id: traceback.format_exc()[:1000]}
                )
                logger.info(
                    f"Playwright Internal Error | Attempt {attempt}/{MAX_RETRY} "
                    f"| Fetching WebSource-ID: {ws_obj.id} | "
                    f"URL: {ws_obj.web_url} |\n"
                    f"Exception: {e} |\n Traceback:{traceback.format_exc()}"
                )
                # If it's a DNS resolution error, exit retry loop early
                if "ERR_NAME_NOT_RESOLVED" in error_message:
                    ws_obj.state = State.BROKEN.value
                    logger.info(
                        f"Domain resolution failed for WebSource-ID: "
                        f"{ws_obj.id}, URL: {ws_obj.web_url}. "
                        "Skipping further retries as domain is not reachable."
                    )
                    break

            except Exception as err:
                last_error = f"{err}\n{traceback.format_exc()}"
                ERROR_DICT[str(err)].append({ws_obj.id: traceback.format_exc()[:1000]})
                logger.info(
                    f"UnExpectedError | Fetching WebSource-ID: {ws_obj.id} | "
                    f"Web URL: {ws_obj.web_url} |\nError {err} |\n"
                    f"Traceback: {traceback.format_exc()}"
                )

            finally:
                await self.update_web_source(ws_obj, last_error)
        else:
            self.failed_ws += 1
            last_error = (
                f"Failed to fetch after {MAX_RETRY} attempts. Error: "
                f"{ws_obj.last_error}"
            )

            # If all retries failed then mark state as BROKEN
            if response and response.status > 399:
                ws_obj.state = State.BROKEN.value
                await self.update_web_source(ws_obj, last_error)

        if page:
            await page.close()

    @staticmethod
    async def update_web_source(ws_obj, error):
        """Updates WebSource fields asynchronously using sync_to_async."""
        ws_obj.last_error = str(error)
        now = datetime.now()
        state = ws_obj.state
        # Using update to prevent race conditions
        await sync_to_async(WebSource.objects.filter(pk=ws_obj.pk).update)(
            last_run=now, last_error=error, updated_on=now, state=state
        )

    @staticmethod
    async def auto_scroll(page, scroll_limit=5):
        """
        Scrolls until the page stops loading new content or reaches the scroll
        limit.
        """
        prev_height = -1
        for _ in range(scroll_limit):
            try:
                curr_height = await page.evaluate("document.body.scrollHeight")
                if curr_height == prev_height:
                    break
                prev_height = curr_height
                await page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                await asyncio.sleep(1)
            except Exception:
                break

    async def create_web_snapshot(
            self, ws_obj, new_hash, screenshot, raw_html
    ):
        """
        Creates a WebSnapshot and saves it asynchronously using sync_to_async.
        """

        @sync_to_async
        @transaction.atomic
        def _create_snapshot_sync():
            try:
                snapshot = WebSnapshot.objects.create(
                    web_source_id=ws_obj.id,
                    status=SnapshotStatus.DRAFT.value,
                    raw_html=raw_html,
                    hash_html=new_hash
                )

                snapshot.raw_snapshot.save(
                    f"{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.jpeg",
                    ContentFile(screenshot)
                )
                return snapshot.id
            except IntegrityError as e:
                logger.info(
                    f'Error occur during creation websnapshot {ws_obj}'
                    f'Error : {e}'
                )

        snapshot_id = await _create_snapshot_sync()
        logger.info(
            f"Created WebSnapshot ID: {snapshot_id} for WebSource {ws_obj.id}."
        )


@sync_to_async
def save_ws_obj(ws_obj):
    try:
        ws_obj.save()
    except DataError as e:
        logger.warning(
            f"DataError: WS ID: {ws_obj.id}, Error: {e}, Traceback: "
            f"{traceback.format_exc()}"
        )
        pass
    except Exception as err:
        logger.error(
            f'Error: WS ID: {ws_obj.id}, '
            f'Unexpected Error: {err}, Traceback: {traceback.format_exc()}'
        )
        pass
