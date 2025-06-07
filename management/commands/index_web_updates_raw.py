# coding=utf-8

import dateutil
import fcntl
import logging
import signal

from django.core.management.base import BaseCommand

from config.constants import (
    STORY_CORE, CONTIFY_FOR_SALES_COMPANY_PREFERENCE_ID,
    DEUTSCHE_TELEKOM_CLIENT_ID
)
from config.solr.utils import commit_solr, delete_by_query
from contify.cutils.cfy_enum import DocType
from contify.subscriber.models import CompanyPreferences
from contify.website_tracking.indexer_raw import raw_index


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Index WebUpdates in Solr (stories core) for given options"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--core", type=str, action="store", dest="core",
            default=STORY_CORE,
            help="In which core data should be indexed. Default is story."
        )

        parser.add_argument(
            "-m", "--minutes", action="store", dest="minutes", type=int, help=(
                "Index WebUpdates that have been updated these many minutes "
                "ago. This is ignored if --start-date is provided."
            )
        )

        parser.add_argument(
            "-d", "--days", action="store", dest="days", type=int, help=(
                "Index WebUpdates that have been updated these many days ago. "
                "This is ignored if --start-date is provided."
            )
        )

        parser.add_argument(
            "-s", "--start-date", action="store", dest="start_date", type=str,
            help="Start updated_on date to process WebUpdates."
        )

        parser.add_argument(
            "-e", "--end-date", action="store", dest="end_date", type=str,
            help=(
                "End updated_on date to process WebUpdates. Current date "
                "assumed if not provided."
            )
        )

        parser.add_argument(
            "-i", "--client-id", action="store", dest="client_ids",
            default=None, type=str, help=(
                "Specify the client ids of WebUpdates to be indexed."
            )
        )

        parser.add_argument(
            "--dont_use_redis_queue", action="store_false",
            dest="use_redis_queue", default=True, help=(
                "Dont use redis queue to send index file to solr"
            )
        )

        parser.add_argument(
            "-C", "--clearall", action="store_true", dest="clear_all",
            default=False, help=(
                "Delete all WebUpdates in Solr before indexing starts."
            )
        )

        parser.add_argument(
            "-M", "--usemultiproc", action="store_true", dest="usemultiproc",
            default=False, help="Use MultiProcessing to speed up indexing."
        )

        parser.add_argument(
            "--statuses", action="store", dest="statuses", type=str,
            default=None, help="for eg. [2, 1]"
        )

        parser.add_argument(
            "-sharedOnly", action="store", dest="sharedOnly", type=bool,
            default=False
        )

        parser.add_argument(
            "--pg_batch_size", action="store", dest="pg_batch_size", type=int,
            default=50000
        )

        parser.add_argument(
            "--es_server_name", action="store", dest="es_server_name",
            type=str, default=None, help=(
                "This can be useful to index data at some other server"
            )
        )

    LOCK_FILE = "/tmp/index_web_updates_raw_django_contify"

    def handle(self, *args, **options):
        lock_fp = open(self.LOCK_FILE, "w")
        try:
            # This acts as a system wide mutex
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            logger.warning(
                "Index WebUpdates raw could not acquire a lock. The job is "
                "probably already running!"
            )
            return

        def signal_handler(signum, frame):
            logger.error("Index WebUpdates Timed out.")
            raise Exception("Timed out!")

        # Hard Timeout for 6 hour
        signal.signal(signal.SIGALRM, signal_handler)
        signal.alarm(60 * 60 * 6)

        try:
            minutes = options.get("minutes")
            days = options.get("days")
            start = options.get("start_date")
            end = options.get("end_date")
            client_ids = options.get("client_ids")
            use_redis_queue = options.get("use_redis_queue")
            clear_all_web_updates = options.get("clear_all")
            usemultiproc = options.get("usemultiproc")
            statuses = options.get("statuses")
            sharedOnly = options.get("sharedOnly", False)
            pg_batch_size = options.get('pg_batch_size')
            core = options["core"]
            es_server_name = options.get("es_server_name")

            if statuses:
                statuses = eval(options.get("statuses"))

            if client_ids:
                client_ids = [int(x) for x in client_ids.split(",")]

            if core not in [STORY_CORE]:
                input_string = (
                    "Given core %s is not listed in our system. Are you sure "
                    "want to proceed. Press 'y' or 'Y' to continue " % core
                )
                response = input(input_string)
                if response == 'y' or response == 'Y':
                    pass
                else:
                    return

            if es_server_name == 'mi_story':
                if not client_ids:
                    client_ids = list(
                        CompanyPreferences.objects
                        .filter(active=True)
                        .exclude(id__in=[
                            CONTIFY_FOR_SALES_COMPANY_PREFERENCE_ID,
                            DEUTSCHE_TELEKOM_CLIENT_ID
                        ])
                        .values_list("id", flat=True)
                    )

            elif es_server_name == 'dt_story':
                if not client_ids:
                    client_ids = [DEUTSCHE_TELEKOM_CLIENT_ID]

            elif core == STORY_CORE:
                if not client_ids:
                    client_ids = list(
                        CompanyPreferences.objects
                        .filter(active=True)
                        .exclude(id__in=[DEUTSCHE_TELEKOM_CLIENT_ID])
                        .values_list("id", flat=True)
                    )

            else:
                if not client_ids:
                    input_string = (
                        "You have not given the client id, so do you want to "
                        "run the indexing for ContifyForSales?"
                        " Press 'y' or 'Y' to continue: "
                    )
                    response = input(input_string)
                    if response == 'y' or response == 'Y':
                        client_ids = [CONTIFY_FOR_SALES_COMPANY_PREFERENCE_ID]
                    else:
                        return

                if not es_server_name:
                    es_server_name = "si_story_cluster"

            if (start or end) and (not(start and end)):
                raise Exception(
                    "Both start and end should be provided. "
                    "Not just start or end."
                )

            if start:
                start = dateutil.parser.parse(start)
                end = dateutil.parser.parse(end)
                minutes = None
                days = None

            if minutes:
                days = None

            if clear_all_web_updates:
                if client_ids:
                    client_ids_str = [str(x) for x in client_ids]
                    delete_by_query(
                        core,
                        "(show_for_client:({}) AND doc_type:({}))".format(
                            " OR ".join(client_ids_str),
                            DocType.WEB_UPDATE.value
                        )
                    )
                else:
                    delete_by_query(
                        core,
                        "(doc_type:({}))".format(DocType.WEB_UPDATE.value)
                    )

                commit_solr(
                    core, wait_searcher=True, soft_commit=False
                )

            raw_index(
                days_ago=days,
                minutes_ago=minutes,
                start_date=start,
                end_date=end,
                client_ids=client_ids,
                use_redis_queue=use_redis_queue,
                use_multiprocessing=usemultiproc,
                statuses=statuses,
                sharedOnly=sharedOnly,
                pg_batch_size=pg_batch_size,
                core=core,
                es_server_name=es_server_name
            )
        except Exception as e:
            logger.exception(f"Error in index web_update command : {e}")
