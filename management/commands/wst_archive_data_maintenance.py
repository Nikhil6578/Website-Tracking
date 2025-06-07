# Python Imports
import boto3
import fcntl
import logging
import traceback
from datetime import datetime, timedelta

# Django Imports
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.utils import IntegrityError

# Project Imports
from contify.cutils.utils import get_db_connection
from contify.website_tracking import cfy_enum as wt_enum
from contify.website_tracking.web_snapshot.models import DiffContent, DiffHtml

logger = logging.getLogger(__name__)
LATEST_SNAPSHOTS_TO_KEEP_COUNT = 2

class Command(BaseCommand):
    """
        Cleans up website tracking data by deleting old junk DiffContent,
        DiffHtml, and WebSnapshots data.

        Usage:
        python manage.py wst_archive_data_maintenance [For Testing]
        python manage.py wst_archive_data_maintenance --delete [To Delete, **USE CAREFULLY**]

        Note:
          1. If an S3 file is deleted, then only we can delete the related
             object or else it'll be hard to figure out which S3 file is
             related to archive data.
          2. We are not to delete the 'Published' Diff-Content and related
             objects in case we need to edit a WebUpdate.

        Reference doc:
            1. https://docs.google.com/document/d/1sylw0PEcre-T_hMRCYBp5TDY8FCHKRXy-H5flARQq5o/edit
               (About website-tracking and data cleanup)
            2. https://docs.google.com/document/d/173WpLI2CxBszzmDxH7Wea4yIIxr5r8jqV_ue6t4rWhE/edit
               (About management command)
    """
    help = "Archives and deletes old and rejected data from web tracking."
    command_name = 'wst_archive_data_maintenance'


    def __init__(self):
        super().__init__()
        self.start_time = datetime.now()
        self.deletion_threshold = None
        self.can_delete = False
        self.max_items = 200
        self.duration = 9
        self.web_snapshots_to_delete = []
        self.do_not_delete_web_snapshot_ids = set()


    def add_arguments(self, parser):
        parser.add_argument(
            "--delete", action='store_true', dest='can_delete',
            help="Pass for deletion of data, default: False.",
        )
        parser.add_argument(
            "-d", "--duration", type=int, default=9,
            help="Duration (in months) to determine old data for cleanup.",
        )
        parser.add_argument(
            '-m', '--max', type=int, default=200, dest='max',
            help="Max number of items to process.",
        )


    def handle(self, *args, **options):
        logger.info(f'Initiated with Args: {args} | Options: {options}')
        extra_str = ''
        if options:
            for key, value in list(options.items()):
                extra_str += f'{key}_{value}'
        try:
            lock_file_name = f"/tmp/{self.command_name}_{extra_str}"
            with open(lock_file_name, 'w') as lock_fp:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            logger.warning(
                f"{self.command_name}!, job is probably already running."
            )
            return

        self.max_items = options['max']
        self.duration = options['duration']
        self.can_delete = options.get('can_delete', False)
        self.deletion_threshold = (
                self.start_time.date() - timedelta(days=self.duration*30)
        )

        # Fetching the latest two Processed WebSnapshot IDs per web source id
        # ALSO WebSnapshot IDs present in Published DiffContent(s).
        raw_query = ("""
            SELECT
                ws_id
            FROM (
                SELECT
                    ws.id AS ws_id,
                    ws.web_source_id,
                    ws.created_on,
                    dcnws.id AS dc_nws_id,
                    dcows.id AS dc_ows_id,
                    ROW_NUMBER() OVER(
                        PARTITION BY ws.web_source_id
                        ORDER BY ws.created_on DESC
                    ) AS row_no
                FROM
                    web_snapshot_websnapshot As ws
                    LEFT JOIN web_snapshot_diffcontent AS dcnws
                        ON dcnws.new_snapshot_id = ws.id AND dcnws.status = %s
                    LEFT JOIN web_snapshot_diffcontent AS dcows
                        ON dcows.old_snapshot_id = ws.id AND dcows.status = %s
                WHERE
                    ws.status = %s
            ) AS ranked_snapshots
            WHERE
                row_no <= %s OR dc_nws_id IS NOT NULL OR dc_ows_id IS NOT NULL;
        """)
        params = [wt_enum.DiffStatus.PUBLISHED.value,
                  wt_enum.DiffStatus.PUBLISHED.value,
                  wt_enum.SnapshotStatus.PROCESSED.value,
                  LATEST_SNAPSHOTS_TO_KEEP_COUNT]
        try:
            with get_db_connection(db_alias="web_snapshot") as connection:
                with connection.cursor() as cursor:
                    cursor.execute(raw_query, params)
                    self.do_not_delete_web_snapshot_ids = {
                        ws_id[0] for ws_id in cursor.fetchall()
                    }
        except Exception as err:
            logger.info(
                f"ERROR occurred while executing SQL query: {raw_query} | "
                f"Error: {err}.\nTraceback: {traceback.format_exc()}"
            )
            return

        # All DiffContent(s) objects to delete
        fields_to_fetch = [
            'old_snapshot', 'new_snapshot', 'old_diff_image', 'new_diff_image'
        ]
        diff_contents = DiffContent.objects.filter(
            created_on__lt=self.deletion_threshold,
            status__in=[wt_enum.DiffStatus.PENDING.value,
                        wt_enum.DiffStatus.REJECT.value]
        ).only(*fields_to_fetch).order_by('created_on')[:self.max_items]

        total_diff_contents = len(diff_contents)
        diff_content_objs_deleted = 0
        for diff_content in diff_contents:
            try:
                self.process_and_delete_diff_content(diff_content)
                diff_content_objs_deleted += 1
            except Exception as err:
                logger.info(
                    f'While deleting DiffContent ID: {diff_content.id} | '
                    f'Error: {err}.\nTraceback: {traceback.format_exc()}'
                )
        # DiffContent might already be deleted in the previous process
        # but not the WebSnapshot object itself.
        for ws in self.web_snapshots_to_delete:
            try:
                ws.delete()
            except IntegrityError:
                pass
            except Exception as err:
                logger.info(f"WebSnapshot ID: {ws.id} | Unexpected Error: {err}")

        end_time = datetime.now()
        execution_time = (end_time - self.start_time).seconds
        logger.info(
            f'{self.command_name} Completed! | Total DiffContent objects '
            f'deleted: {diff_content_objs_deleted}/{total_diff_contents}. '
            f'Started at: {self.start_time.strftime("%b %d %H:%M:%S")} | '
            f'Finished at: {end_time.strftime("%b %d %H:%M:%S")} | '
            f'Time Taken: {execution_time//60} mins {execution_time%60} secs.'
        )


    @transaction.atomic
    def process_and_delete_diff_content(self, diff_content):
        """
        To processes and deletes a single `DiffContent` object and its related
        data while Ensuring atomic deletion of all related data before deleting
        the `DiffContent` itself and also their S3 images.

        Args:
            diff_content: The `DiffContent` object to process and delete.
        """
        diff_content_id = diff_content.id
        old_web_snapshot_obj = diff_content.old_snapshot
        new_web_snapshot_obj = diff_content.new_snapshot

        # Delete DiffContent object and S3 image.
        if self.can_delete:
            if diff_content.old_diff_image:
                diff_content.old_diff_image.delete()
            if diff_content.new_diff_image:
                diff_content.new_diff_image.delete()
            diff_content.delete()

        # Delete related DiffHtml object.
        old_ws_id = old_web_snapshot_obj.id if old_web_snapshot_obj else None
        new_ws_id = new_web_snapshot_obj.id if new_web_snapshot_obj else None
        related_diff_html = DiffHtml.objects.filter(
            old_web_snapshot_id=old_ws_id,
            new_web_snapshot_id=new_ws_id
        )
        if self.can_delete:
            related_diff_html.delete()

        # Delete related WebSnapshot objects and S3 image.
        for ws_obj in [old_web_snapshot_obj, new_web_snapshot_obj]:
            if not ws_obj or ws_obj.id in self.do_not_delete_web_snapshot_ids:
                continue

            if self.can_delete:

                if ws_obj.raw_snapshot:
                    ws_obj.raw_snapshot.delete()

                try:
                    ws_obj.delete()
                except IntegrityError:
                    # WS referenced in other DiffContent (ForeignKey).
                    self.web_snapshots_to_delete.append(ws_obj)

        logger.info(f'Deletion of DiffContent, DiffHtml and WebSnapshot '
                    f'completed for DiffContent ID: {diff_content_id}.')
