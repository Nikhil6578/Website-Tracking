# Imports
import collections
import fcntl
import logging
import time
import traceback
from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction, IntegrityError
from contify.website_tracking import cfy_enum as wt_enum
from contify.website_tracking.management.commands import format_error_msg
from contify.website_tracking.models import WebSource
from contify.website_tracking.web_snapshot.models import WebSnapshot, DiffHtml
from contify.website_tracking.service import get_diff_html
from contify.website_tracking.utils import prepare_error_report, set_values


logger = logging.getLogger(__name__)
ERROR_DICT = collections.defaultdict(list)


class Command(BaseCommand):
    LOCK_FILE = '/tmp/process_web_snapshot'

    help = "This processes only one draft WebSnapshot for each WebSource"

    def add_arguments(self, parser):
        parser.add_argument(
            "-r", "--ratio_mode", action="store", dest="ratio_mode",
            default="accurate", choices={"accurate", "fast", "faster"},
            help="Use accurate mode instead of risky mode"
        )
        parser.add_argument(
            "-F", type=float, action='store', dest='threshold', default=None,
            help=(
                "A value between 0 and 1 that determines how similar nodes "
                "must be to match."
            ),
        )
        parser.add_argument(
            "--ws_ids", action="store", dest="ws_ids", default=None,
            type=set_values, help=(
                "Process WebSnapshot and create DiffContent for the given ids "
                "of WebSource"
            )
        )
        parser.add_argument(
            "-b", "--batchSize", action="store", dest="batch_size",
            type=int, default=300, help=(
                "Batch size used for processing the WebSnapshot"
            )
        )
        parser.add_argument(
            "--shardNo", action="store", dest="shard_no", type=int,
            default=None, help="Used for sharding in job by web_source_id"
        )
        parser.add_argument(
            "--maxShard", dest="max_shard", type=int, default=4,
            help="The maximum cluster size to process"
        )

    def __init__(self, *args, **kwargs):
        self.start_date = datetime.now()
        super().__init__(*args, **kwargs)

    def handle(self, *args, **options):
        t = time.time()
        logger.info(
            "ProcessWebSnapshot!, handle function initiated with args: {} and "
            "options: {}".format(args, options)
        )

        ratio_mode = options["ratio_mode"]
        threshold = options["threshold"]

        ws_ids = options["ws_ids"]
        batch_size = options["batch_size"]
        shard_no = options["shard_no"]
        max_shard = options["max_shard"]

        try:
            lock_file_name = "{}_{}".format(self.LOCK_FILE, shard_no)
            lock_fp = open(lock_file_name, 'w')
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            logger.warning(
                "ProcessWebSnapshot!, job is probably already running for and "
                "shard {}".format(shard_no)
            )
            return

        wss_draft_qs = (
            WebSnapshot.objects
            .filter(
                status=wt_enum.SnapshotStatus.DRAFT.value,
                state=wt_enum.State.ACTIVE.value
            )
            .order_by('web_source_id', 'created_on')
            .distinct('web_source_id')
        )

        if ws_ids:
            wss_draft_qs = wss_draft_qs.filter(web_source_id__in=ws_ids)

        if shard_no:
            wss_draft_qs = wss_draft_qs.extra(
                where=[
                    '{}.web_source_id %% {} = %s'.format(
                        WebSnapshot._meta.db_table, max_shard
                    )
                ],
                params=[shard_no]
            )

        wss_draft_qs = wss_draft_qs[:batch_size]

        if wss_draft_qs.exists():
            web_source_ids = [s.web_source_id for s in wss_draft_qs]

            logger.info(
                "ProcessWebSnapshot!, {} WebSnapshot found to fetch".format(
                    len(web_source_ids)
                )
            )

            wss_processed_qs = (
                WebSnapshot.objects
                .filter(
                    status=wt_enum.SnapshotStatus.PROCESSED.value,
                    web_source_id__in=web_source_ids
                )
                .order_by('web_source_id', '-created_on')
                .distinct('web_source_id')
            )

            wss_processed_obj_id_map = {
                s.web_source_id: s for s in wss_processed_qs
            }
            first_fetch_web_snapshots = []
            old_n_new_ws_map = {}
            web_sources_id = []
            process_web_snapshot = 0
            for new_wss_obj in wss_draft_qs:
                process_web_snapshot += 1
                tmp_ws_id = new_wss_obj.web_source_id
                web_sources_id.append(tmp_ws_id)
                if tmp_ws_id not in wss_processed_obj_id_map:
                    first_fetch_web_snapshots.append(new_wss_obj)
                    continue

                old_wss_obj = wss_processed_obj_id_map[tmp_ws_id]
                old_n_new_ws_map[tmp_ws_id] = {
                    "o": old_wss_obj, "n": new_wss_obj
                }

            ws_data_list = list(
                WebSource.objects.filter(id__in=web_sources_id)
                .values_list("id", "base_url", "junk_xpaths")
            )
            ws_base_url_map = dict([list(tup)[:-1] for tup in ws_data_list])
            ws_junk_xpaths_map = dict([list(tup)[::2] for tup in ws_data_list])
            logger.info(
                "ProcessWebSnapshot!, old and new WebSnapshot map: {}".format(
                    old_n_new_ws_map
                )
            )

            # --------- Get the diff HTML for the fetched WebSnapshot ---------
            subsequent_fetch_diff_html(
                ws_base_url_map, ws_junk_xpaths_map, old_n_new_ws_map, ratio_mode, threshold
            )
            logger.info(
                "ProcessWebSnapshot!, Done generation DiffHtml of both old and"
                " new WebSnapshots"
            )

            # ------------- Generating diff for the first fetch ---------------
            if len(first_fetch_web_snapshots) > 0:
                first_fetch_diff_html(first_fetch_web_snapshots)
                logger.info(
                    "ProcessWebSnapshot!, Done DiffHtml generation for the "
                    "first fetch of WebSnapshot"
                )
            if ERROR_DICT:
                prepare_error_report(ERROR_DICT, process_web_snapshot, "process_web_snapshot")

        else:
            web_source_ids = []
            logger.info(
                "ProcessWebSnapshot!, no WebSnapshot found to process"
            )

        logger.info(
            f"ProcessWebSnapshot!, Took {time.time() - t:0.4f} seconds in "
            f"processing the {len(web_source_ids)} WebSnapshot items."
        )


def first_fetch_diff_html(first_fetch_wss_map):
    """
        Sagar told us to not create DiffHtml for first fetch, so just
        updating the status of WebSnapshot.
    """
    for new_wss_obj in first_fetch_wss_map:
        try:
            new_wss_obj.status = wt_enum.SnapshotStatus.PROCESSED.value
            new_wss_obj.save()
        except Exception as e:
            transaction.rollback("web_snapshot")
            logger.info(
                "ProcessWebSnapshot!, Unable to update the status of "
                "WebSnapshot-ID: {}, traceback: {}".format(
                    new_wss_obj.id, traceback.format_exc()
                )
            )
            ERROR_DICT[str(e)].append({new_wss_obj.id: traceback.format_exc()[:1000]})

            try:
                new_wss_obj.last_error = format_error_msg(
                    traceback.format_exc()
                )
                new_wss_obj.status = wt_enum.SnapshotStatus.FAILED.value
                new_wss_obj.save()
            except Exception as e:
                transaction.rollback("web_snapshot")
                logger.exception(
                    "ProcessWebSnapshot!, Got error while updating the status "
                    "of WebSnapshot-id: {} for first fetch with the last_error"
                    ", traceback: {}".format(
                        new_wss_obj.id, traceback.format_exc()
                    )
                )
                ERROR_DICT[str(e)].append({new_wss_obj.id: traceback.format_exc()[:1000]})


def subsequent_fetch_diff_html(ws_base_url_map, ws_junk_xpaths_map, old_n_new_ws_map, ratio_mode,
                               threshold):
    """
        It generates Diff HTML for the old and new WebSnapshots.
    """
    for ws_id, data in old_n_new_ws_map.items():
        old_wss_obj = data["o"]
        new_wss_obj = data["n"]
        ws_base_url = ws_base_url_map.get(ws_id)
        ws_junk_xpaths = ws_junk_xpaths_map.get(ws_id, [])

        logger.info(
            "ProcessWebSnapshot!, Compare and Get the DiffHtml for base_url: "
            "{}, old_wss_id: {} and new_wss_id: {}".format(
                ws_base_url, old_wss_obj.id, new_wss_obj.id
            )
        )

        try:
            cxt = get_diff_html(
                new_wss_obj, old_wss_obj, ws_base_url, ratio_mode, threshold, junk_xpaths=ws_junk_xpaths
            )

            added_diff_text = cxt.get("added_diff_info", {})
            a_t = added_diff_text.get("T")
            a_i = added_diff_text.get("I")
            a_l = added_diff_text.get("L")

            removed_diff_info = cxt.get("removed_diff_info", {})
            r_t = removed_diff_info.get("T")
            r_i = removed_diff_info.get("I")
            r_l = removed_diff_info.get("L")

            n_a_t = set(a_t) - set(r_t)
            n_a_i = set(a_i) - set(r_i)
            n_a_l = set(a_l) - set(r_l)

            n_r_t = set(r_t) - set(a_t)
            n_r_i = set(r_i) - set(a_i)
            n_r_l = set(r_l) - set(a_l)

            if n_a_t or n_a_i or n_a_l or n_r_t or n_r_i or n_r_l:
                if n_a_t:
                    cxt["added_diff_info"]["T"] = list(n_a_t)

                if n_a_i:
                    cxt["added_diff_info"]["I"] = list(n_a_i)

                if n_a_l:
                    cxt["added_diff_info"]["L"] = list(n_a_l)

                if n_r_t:
                    cxt["removed_diff_info"]["T"] = list(n_r_t)

                if n_r_i:
                    cxt["removed_diff_info"]["I"] = list(n_r_i)

                if n_r_l:
                    cxt["removed_diff_info"]["L"] = list(n_r_l)

                # diff_cxt.update({ws_id: cxt})
                create_diff_html_n_update_snapshot(
                    new_wss_obj, old_wss_obj.id, cxt
                )
            else:
                logger.info(
                    "ProcessWebSnapshot!, No diff_text found for old_wss_id: "
                    "{} and new_wss_id: {}".format(
                        old_wss_obj.id, new_wss_obj.id
                    )
                )
                new_wss_obj.status = wt_enum.SnapshotStatus.PROCESSED.value
                new_wss_obj.save()
        except TimeoutError as e:
            logger.info(
                "ProcessWebSnapshot TimeoutError!, Unable to get diff html for old_wss_id: "
                "{} and new_wss_id: {}, traceback: {}".format(
                    old_wss_obj.id, new_wss_obj.id, traceback.format_exc()
                )
            )
            ERROR_DICT[str(e)].append({new_wss_obj.id: traceback.format_exc()[:1000]})

            try:
                new_wss_obj.last_error = format_error_msg(
                    traceback.format_exc()
                )
                new_wss_obj.status = wt_enum.SnapshotStatus.DIFF_TIMEOUT.value
                new_wss_obj.save(update_fields=['status', 'last_error'])
            except Exception as e:
                logger.info(
                    "ProcessWebSnapshot!, Got error while updating the "
                    "WebSnapshot-id: {} with the last_error, traceback: {}"
                    .format(new_wss_obj.id, traceback.format_exc())
                )
                ERROR_DICT[str(e)].append({new_wss_obj.id: traceback.format_exc()[:1000]})


        except Exception as e:
            logger.info(
                "ProcessWebSnapshot!, Unable to get diff html for old_wss_id: "
                "{} and new_wss_id: {}, traceback: {}".format(
                    old_wss_obj.id, new_wss_obj.id, traceback.format_exc()
                )
            )
            ERROR_DICT[str(e)].append({new_wss_obj.id: traceback.format_exc()[:1000]})

            try:
                new_wss_obj.last_error = format_error_msg(
                    traceback.format_exc()
                )
                new_wss_obj.status = wt_enum.SnapshotStatus.FAILED.value
                new_wss_obj.save()
            except Exception as e:
                logger.info(
                    "ProcessWebSnapshot!, Got error while updating the "
                    "WebSnapshot-id: {} with the last_error, traceback: {}"
                    .format(new_wss_obj.id, traceback.format_exc())
                )
                ERROR_DICT[str(e)].append({new_wss_obj.id: traceback.format_exc()[:1000]})


# @transaction.atomic
def create_diff_html_n_update_snapshot(new_wss_obj, old_wss_id, diff_data):
    logger.info(
        "ProcessWebSnapshot!, Creating DiffHtml for old_wss_id: {} "
        "and new_wss_id: {}, and updating WebSnapshot Status".format(
            old_wss_id, new_wss_obj.id
        )
    )
    dh_id = None
    try:
        dh = DiffHtml()

        dh.old_web_snapshot_id = old_wss_id
        dh.old_diff_html = diff_data["old_html"]
        dh.removed_diff_info = diff_data["removed_diff_info"]

        dh.new_web_snapshot_id = new_wss_obj.id
        dh.new_diff_html = diff_data["new_html"]
        dh.added_diff_info = diff_data["added_diff_info"]

        dh.status = wt_enum.DiffHtmlStatus.DRAFT.value
        dh.state = wt_enum.State.ACTIVE.value

        dh.save()

        new_wss_obj.status = wt_enum.SnapshotStatus.PROCESSED.value
        dh_id = dh.id
    except IntegrityError as e:
        # A DiffContent is already created with the new WebSnapshot
        logger.info(
            "ProcessWebSnapshot!, A DiffHtml is already created for "
            "the new WebSnapshot-ID:: {}".format(new_wss_obj.id)
        )
        ERROR_DICT[str(e)].append({new_wss_obj.id: traceback.format_exc()[:1000]})
        new_wss_obj.status = wt_enum.SnapshotStatus.PROCESSED.value

    new_wss_obj.save()

    logger.info(
        "ProcessWebSnapshot!, DiffHtml created with ID: {} and Snapshot "
        "(ID: {}) status updated".format(dh_id, new_wss_obj.id)
    )
