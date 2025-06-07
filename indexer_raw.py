# coding=utf-8

import copy
import json
import logging
import os.path
import time
from collections import defaultdict, namedtuple
from datetime import datetime, timedelta

from django.conf import settings

from nltk.corpus import stopwords
from treelib import Tree

from config import ROOT_DIR
from config.constants import (
    STORY_CORE, CONTIFY_FOR_SALES_COMPANY_PREFERENCE_ID,
    DEUTSCHE_TELEKOM_CLIENT_ID,CURATED_COPILOTS_CLIENTS
)
from config.elastic_search import utils as es_utils
from config.redis.queue import enqueue_job
from config.solr.utils import (
    commit_solr, update_solr_from_data, update_solr_from_file
)
from config.utils import story_entity_id_to_solr_id
from contify.cutils.cfy_enum import (
    GenericDataBooleanKey, StoryStatus, DocType, EntityModelType
)
from contify.cutils.utils import format_exc, get_db_connection
from contify.cutils.timeline import ISO_8601_FORMAT
from contify.story.cfy_enum import ESStoryUploadType
from contify.website_tracking.execptions import (
    MissingSource, MissingApprovedDate
)
from contify.website_tracking.utils import encode_change_log_url


logger = logging.getLogger(__name__)


__ALLOWED_LANG_SET = set(lang[0] for lang in settings.LANGUAGES)

REVERSE_GENERIC_DATA_BOOL_MAP = {
    v.value: k
    for k, v in GenericDataBooleanKey.__dict__["_member_map_"].items()
}

BATCH_SIZE = 10000

PARALLEL_REQUESTS = 8

WEB_SNAPSHOT_DB = "web_snapshot"

STOP_DICT = defaultdict(bool)
for item in stopwords.words():
    STOP_DICT[item] = True


def get_source_info(cs_id):
    from contify.penseive.models import ContentSource
    try:
        cs = ContentSource.objects.get(id=cs_id)
        cs_info = {
            "domain_url": cs.domain_url,
            "global_rank": cs.global_rank or 2147483647,
            "name": cs.name,
            "country_id": cs.country_id
        }
    except ContentSource.DoesNotExist:
        cs_info = {
            "domain_url": "",
            "global_rank": 2147483647,
            "name": "",
            "country_id": None
        }
    return cs_info


WebUpdateData = namedtuple(
    "WUData", (
        "id",
        "client_id",
        "manual_copy_of_id",

        "created_by_id",
        "updated_by_id",
        "approved_by_user_name",

        "language",

        "content_source_id",
        "content_source_name",
        "content_source_global_rank",
        "content_source_domain_url",
        "content_source_channel_id",

        "rating_id",
        "rating_numeric_value",

        "published_by_company_id",
        "pbc_referenced_company_ids",

        "location_tree",
        "source_location_list",
        "explicit_hq_location_tree",
        "inferred_hq_location_tree",
        "companies_list_info",
        "persons_list",
        "industry_tree",
        "topic_tree",
        "business_event_tree",
        "theme_tree",
        "contenttype_tree",
        "custom_tag_tree_map",

        "diff_content_id",
        "web_source_id",
        "web_source_web_url",

        "hash",
        "status",
        "title",
        "description",
        "old_image_url",
        "new_image_url",
        "email_priority",
        "snippet_info",

        "generic_data_list",
        "generic_data_json",

        "created_on",
        "updated_on",
        "approved_on",

        "commented_by_ids"
    ))


def story_cluster_web_update_index_finder(es_doc):
    return "web-update"


def update_solr_and_commit(core, doc, soft_commit, wait_searcher):
    update_solr_from_data(core, doc)
    if soft_commit:
        commit_solr(core, wait_searcher=wait_searcher)


def index_web_updates(web_updates_id, table_name=None, soft_commit=False, wait_searcher=False,
                      dry_run=False, comment_doc=None, es_server_name=None):
    """
        dry_run option does not perform indexing but will give you the exact
        docs that will be pushed into Solr.
    """
    if isinstance(web_updates_id, int):
        web_updates_id = [web_updates_id]

    if not isinstance(web_updates_id, (list, set)):
        raise Exception(
            "Please pass a list of integers or integer as web_updates_id "
            "instead of: {}".format(web_updates_id)
        )

    if not web_updates_id:
        logger.warning(
            "Empty web_updates id list passed: {}".format(web_updates_id)
        )
        return

    solr_doc_list = []

    es_si_doc_list = []
    es_15_days_doc_list = []
    es_mi_doc_list = []
    es_dt_doc_list = []
    es_curated_copilot_doc_list = []

    connection = get_db_connection("default")
    cursor = connection.cursor("index_web_updates_id_in_solr")
    cursor.itersize = 1000

    try:
        main_query = get_main_query()

        if len(web_updates_id) == 1:
            main_query += " WHERE (wu.id = {})".format(web_updates_id[0])
        else:
            main_query += " WHERE (wu.id IN {})".format(tuple(web_updates_id))

        cursor.execute(main_query)

        for row in cursor:
            wu_data = WebUpdateData(*row)
            try:
                if not wu_data.content_source_id:
                    logger.info(
                        f"Inactive ContentSource in WebUpdate ID: {wu_data.id}"
                    )
                    continue

                solr_doc = transform_into_solr_doc(wu_data)

            except (MissingSource, MissingApprovedDate) as e:
                logger.error(
                    f"Error in generating solr doc for WebUpdate ID: "
                    f"{wu_data.id} e: {e}"
                )
                continue

            except Exception as e:
                logger.exception(
                    f"Error in generating solr doc for WebUpdate ID: "
                    f"{wu_data.id} e: {e}"
                )
                continue

            if solr_doc:
                number_of_days = (
                    datetime.now() - datetime.strptime(
                        wu_data.approved_on or wu_data.updated_on,
                        ISO_8601_FORMAT
                    )
                ).days

                # Add doc for ElasticSearch
                if solr_doc["show_for_client"] == CONTIFY_FOR_SALES_COMPANY_PREFERENCE_ID:
                    tmp_doc = transform_into_es_doc(wu_data, solr_doc)
                    es_si_doc_list.append(tmp_doc)
                    if comment_doc:
                        es_si_doc_list.append(comment_doc)
                elif solr_doc["show_for_client"] in CURATED_COPILOTS_CLIENTS:
                    tmp_doc = transform_into_es_doc(wu_data, solr_doc)
                    es_curated_copilot_doc_list.append(tmp_doc)
                    if comment_doc:
                        es_curated_copilot_doc_list.append(comment_doc)

                elif solr_doc["show_for_client"] == DEUTSCHE_TELEKOM_CLIENT_ID:
                    es_dt_doc_list.append(
                        transform_into_es_doc(wu_data, solr_doc)
                    )
                    if comment_doc:
                        es_dt_doc_list.append(comment_doc)

                else:
                    tmp_doc = transform_into_es_doc(wu_data, solr_doc)
                    es_mi_doc_list.append(tmp_doc)

                    if comment_doc:
                        es_mi_doc_list.append(comment_doc)

                # index comment in 15 days server for both clients
                if number_of_days <= 15 and solr_doc["show_for_client"] != DEUTSCHE_TELEKOM_CLIENT_ID:
                    es_15_days_doc_list.append(tmp_doc)

                    solr_doc_list.append(solr_doc)

                    if comment_doc:
                        es_15_days_doc_list.append(comment_doc)
                        # removing in case of solr as id field has been
                        # removed from solr and 'uuid' has added, not doing
                        # the same for ES as we kept the "id" field in ES
                        comment_doc.pop('id')
                        solr_doc_list.append(comment_doc)

        if not dry_run:
            if es_si_doc_list:
                tmp_es_server_name = es_server_name
                if not es_server_name:
                    tmp_es_server_name = es_utils.get_index_es_server_name(
                        CONTIFY_FOR_SALES_COMPANY_PREFERENCE_ID
                    )

                if tmp_es_server_name != "si_story_cluster":
                    es_utils.bulk_index(
                        tmp_es_server_name, es_si_doc_list, refresh=soft_commit
                    )

                _index_finder = None
                if settings.PRODUCTION:
                    _index_finder = story_cluster_web_update_index_finder

                es_utils.bulk_index(
                    "si_story_cluster", es_si_doc_list, refresh=soft_commit,
                    index_finder=_index_finder
                )

            if es_15_days_doc_list:
                tmp_es_server_name = es_server_name
                if not es_server_name:
                    tmp_es_server_name = es_utils.get_index_es_server_name(
                        None, days=15
                    )
                es_utils.bulk_index(
                    tmp_es_server_name, es_15_days_doc_list,
                    refresh=soft_commit
                )

            if es_mi_doc_list:
                tmp_es_server_name = es_server_name
                if not es_server_name:
                    tmp_es_server_name = es_utils.get_index_es_server_name("mi")

                es_utils.bulk_index(
                    tmp_es_server_name, es_mi_doc_list, refresh=soft_commit
                )

            if es_dt_doc_list:
                tmp_es_server_name = es_server_name
                if not es_server_name:
                    tmp_es_server_name = es_utils.get_index_es_server_name(
                        DEUTSCHE_TELEKOM_CLIENT_ID
                    )

                es_utils.bulk_index(
                    tmp_es_server_name, es_dt_doc_list, refresh=soft_commit
                )

            if es_curated_copilot_doc_list:
                tmp_es_server_name = es_server_name
                if not es_server_name:
                    tmp_es_server_name = es_utils.get_index_es_server_name(
                        client_id=CURATED_COPILOTS_CLIENTS[0] if CURATED_COPILOTS_CLIENTS else None
                    )

                es_utils.bulk_index(
                    tmp_es_server_name, es_curated_copilot_doc_list, refresh=soft_commit
                )

            if solr_doc_list:
                update_solr_and_commit(
                    STORY_CORE, solr_doc_list, soft_commit, wait_searcher
                )

            logger.info(
                "{} number of WebUpdates indexed in solr {} core".format(
                    len(solr_doc_list), STORY_CORE
                )
            )
    finally:
        cursor.close()

    if dry_run:
        return (solr_doc_list, ), (
            es_si_doc_list, es_15_days_doc_list, es_mi_doc_list,
            es_dt_doc_list, es_curated_copilot_doc_list
        )


def raw_index(*args, **kwargs):
    def get_main_filters():
        filters = []

        if kwargs.get("minutes_ago"):
            filters.append(
                "wu.updated_on > now() - INTERVAL '{} minute'".format(
                    kwargs["minutes_ago"]
                )
            )

        if kwargs.get("days_ago"):
            filters.append(
                "wu.updated_on > now() - INTERVAL '{} day'".format(
                    kwargs["days_ago"]
                )
            )

        if kwargs.get("start_date"):
            filters.append(
                "wu.updated_on >= '{}'".format(kwargs["start_date"].date())
            )

        if kwargs.get("end_date"):
            filters.append(
                "wu.updated_on < '{}'".format(kwargs["end_date"].date())
            )

        if kwargs.get("client_ids"):
            client_ids = ", ".join(str(i) for i in kwargs['client_ids'])
            filters.append("wu.client_id IN ({})".format(client_ids))

        if kwargs.get("statuses"):
            filters.append(
                "wu.status IN {}".format(tuple(kwargs["statuses"]))
            )

        if kwargs.get('web_update_ids'):
            wu_ids = ", ".join(str(i) for i in kwargs['web_update_ids'])
            filters.append(f"wu.id in ({wu_ids})")

        return filters

    start_time = datetime.now()
    use_redis_queue = kwargs.get("use_redis_queue", True)
    use_multiprocessing = kwargs.get("use_multiprocessing", True)
    pg_batch_size = kwargs.get('pg_batch_size')
    solr_core = kwargs.get('core') or STORY_CORE
    es_server_name = kwargs.get('es_server_name')

    if use_multiprocessing:
        def get_divided_query(shard_no):
            # SQL injection? Really? In a CRON JOB?
            filters = get_main_filters()
            filters.append(
                u"wu.id % {} = {}".format(PARALLEL_REQUESTS, shard_no)
            )
            return get_main_query_filtered(filters)

        main_query_divided = [
            get_divided_query(sn) for sn in range(PARALLEL_REQUESTS)
        ]

        from multiprocessing import Pool
        pool = Pool(processes=PARALLEL_REQUESTS)

        args = []
        i = 0
        for q in main_query_divided:
            i += 1
            args.append((
                q, i, use_redis_queue, pg_batch_size, solr_core, es_server_name
            ))

        try:
            total_fetched_list = pool.map(index_with_query, args, chunksize=1)
        except Exception:
            logger.info("args: {}".format(args))
            raise

        total_fetched = sum(total_fetched_list)
    else:
        main_query = get_main_query_filtered(get_main_filters())
        total_fetched = index_with_query(
            (
                main_query, 1, use_redis_queue, pg_batch_size, solr_core,
                es_server_name
             )
        )

    end_time = datetime.now()
    total_time_taken = end_time - start_time
    total_seconds = total_time_taken.total_seconds()

    if total_fetched:
        index_speed = 1000000 * ((total_seconds or 1) // total_fetched)
    else:
        index_speed = 0

    logger.info(
        "Total time taken: {}, total_rows_fetched: {}, index_speed: {} for "
        "WebUpdate".format(
            total_time_taken, total_fetched,
            str(timedelta(seconds=index_speed))
        )
    )


def index_with_query(input_args):
    main_query, parallel_index, use_redis_queue, pg_batch_size, core, es_server_name = (
        input_args
    )
    connection = get_db_connection("default")
    cursor = connection.cursor(f"web_update_data_indexing_{parallel_index}")
    total_rows_fetched = 0

    try:
        cursor.itersize = pg_batch_size
        cursor.execute(main_query)

        def create_file():
            if create_file.index_file and not create_file.index_file.closed:
                raise Exception("Invalid state")

            create_file.index_file_name = (
                ROOT_DIR + "logs/web_update_solr_json_doc_{}__{}".format(
                    int(time.time() * 1000), parallel_index
                )
            )
            logger.info(
                "Opening Solr index file for WebUpdate: {}".format(
                    create_file.index_file_name
                )
            )
            create_file.index_file = open(create_file.index_file_name, "wb+")

        create_file.index_file = None
        create_file.index_file_name = None
        total_added_in_file = 0
        exception_count = 0

        def new_file():
            create_file()
            create_file.index_file.write(b"[")

        def close_file_and_start_index():
            create_file.index_file.write(b"]")
            create_file.index_file.close()
            if total_added_in_file:
                logger.info(
                    "Indexing WebUpdate file with {} records".format(
                        total_added_in_file
                    )
                )
                if use_redis_queue:
                    enqueue_job(
                        upload_file_to_solr, queue_type="low", kwargs={
                            "file_path": create_file.index_file_name,
                            "core": core
                        }
                    )
                else:
                    upload_file_to_solr(
                        file_path=create_file.index_file_name, core=core
                    )

        new_file()

        es_docs = []
        es_15_days_doc_list = []
        es_copilot_doc_list = []
        es_15_d_server_name = es_utils.get_index_es_server_name(
            client_id=None, days=15
        )
        es_dt_server_name = es_utils.get_index_es_server_name(
            client_id=DEUTSCHE_TELEKOM_CLIENT_ID
        )
        es_copilot_curated_server_name = es_utils.get_index_es_server_name(
            client_id=CURATED_COPILOTS_CLIENTS[0] if CURATED_COPILOTS_CLIENTS else None
        )

        for row in cursor:
            if exception_count > 30:
                raise Exception(
                    "Too many exceptions while indexing WebUpdate!"
                )

            total_rows_fetched += 1

            wu_data = WebUpdateData(*row)

            if not wu_data.web_source_id:
                logger.info(
                    "Inactive ContentSource found to WebUpdate-ID: {}".format(
                        wu_data.id
                    )
                )
                continue

            try:
                solr_doc = transform_into_solr_doc(wu_data)
            except (MissingSource, MissingApprovedDate) as e:
                logger.exception(
                    "Error in generating the solr doc for WebUpdate-ID: {} e: "
                    "{}".format(wu_data.id, e)
                )
                continue

            except Exception as e:
                exception_count += 1
                logger.exception(
                    "Error in generating the WebUpdate raw data for ID: {} e: "
                    "{}".format(wu_data.id, e)
                )
                continue

            try:
                solr_doc_json = json.dumps(solr_doc)

                # Add doc for ElasticSearch
                tmp_es_doc = transform_into_es_doc(wu_data, solr_doc)
                es_docs.append(tmp_es_doc)

                number_of_days = (
                    datetime.now() - datetime.strptime(
                        wu_data.approved_on or wu_data.updated_on,
                        ISO_8601_FORMAT
                    )
                ).days
                if (number_of_days <= 15
                        and es_server_name not in [
                            es_15_d_server_name, es_dt_server_name]
                ):
                    if es_server_name == es_copilot_curated_server_name:
                        es_copilot_doc_list.append(tmp_es_doc)
                    else:
                        es_15_days_doc_list.append(tmp_es_doc)

            except Exception as e:
                logger.exception(
                    f"Exception in getting WebUpdate json.dumps: {e}"
                )

                error_file_name = (
                    ROOT_DIR + (
                        "logs/json_dump_web_update_error_{}_{}.json".format(
                            int(time.time() * 1000), parallel_index
                        )
                    )
                )

                try:
                    import pickle
                    with open(error_file_name, "w+") as efile:
                        efile.write(pickle.dumps(solr_doc))

                except Exception as e:
                    logger.exception(
                        f"Exception in writing web_update error_file: {e}"
                    )

                logger.info(f"Error file writer in {error_file_name}")

                continue

            # TODO: Do not index to Solr if the doc is older than 15 days
            create_file.index_file.write(
                bytes(solr_doc_json, encoding="utf-8")
            )
            total_added_in_file += 1

            if (total_rows_fetched - 10) % 20000 == 0:
                logger.info(f"Total WebUpdate Indexed: {total_rows_fetched}")

            if total_added_in_file > BATCH_SIZE:
                close_file_and_start_index()
                new_file()
                total_added_in_file = 0

                # Initiate bulk indexing to ElasticSearch
                es_utils.bulk_index(es_server_name, es_docs)
                es_docs = []

                if len(es_15_days_doc_list) > 0:
                    es_utils.bulk_index(
                        es_15_d_server_name, es_15_days_doc_list
                    )
                    es_15_days_doc_list = []
                elif len(es_copilot_doc_list) > 0:
                    es_utils.bulk_index(
                        es_copilot_curated_server_name, es_copilot_doc_list
                    )
            else:
                create_file.index_file.write(b"\n,")

        # Remove the newline and comma
        create_file.index_file.flush()

        if total_added_in_file > 0:
            create_file.index_file.seek(-1, os.SEEK_CUR)

        close_file_and_start_index()

        if len(es_docs) > 0:
            # Initiate bulk indexing to ElasticSearch
            es_utils.bulk_index(es_server_name, es_docs)
            es_docs = []

            if len(es_15_days_doc_list) > 0:
                es_utils.bulk_index(
                    es_15_d_server_name, es_15_days_doc_list
                )
                es_15_days_doc_list = []
            elif len(es_copilot_doc_list) > 0:
                es_utils.bulk_index(
                    es_copilot_curated_server_name, es_copilot_doc_list
                )
                es_15_days_doc_list = []


    except Exception as e:
        logger.exception(f"Exception in the index_with_query: {e}")
        return 0

    finally:
        connection.close()

    logger.info(
        f"Returning Total rows fetched: {total_rows_fetched} for WebUpdate"
    )
    return total_rows_fetched


def upload_file_to_solr(file_path, core):
    if not os.path.isfile(file_path):
        logger.warning(
            f"Failed uploading the file {file_path}, as it does not exist"
        )
        return

    try:
        for x in range(6):
            try:
                update_solr_from_file(core, file_path, use_curl=False)
                return

            except RuntimeError:
                # Sometimes Solr in busy loading a core
                if x == 5:
                    raise

                logger.warning(
                    "Failed uploading file {}. Retrying {}. Error: {}".format(
                        file_path, x, format_exc()
                    )
                )
                time.sleep(60)
    finally:
        os.remove(file_path)


def transform_into_solr_doc(wu_data):
    """
    TODO: Rebuild all the tags database
        CustomTag.tree.rebuild()
        ContentSource.tree.rebuild()
        Location.tree.rebuild()
        Industry.tree.rebuild()
        Company.tree.rebuild()

    """

    def add_fk_tag(bucket_id, attr_name=None, value=None, ref_entity_ids=None):
        ref_entity_ids = ref_entity_ids or []
        fk_tag_id = value or getattr(wu_data, attr_name, "")

        if fk_tag_id and bucket_id:
            solr_fk_attr_value = [int(fk_tag_id)]
            doc[f"d_{bucket_id}"] = solr_fk_attr_value
            doc[f"fl_{bucket_id}"] = solr_fk_attr_value + ref_entity_ids
            doc[f"fc_{bucket_id}"] = ["|" + str(fk_tag_id)]

    def add_tree_tag(bucket_id, attr_name=None, value=None, ref_entity_ids=None):
        ref_entity_ids = ref_entity_ids or []
        raw_tree_val = value or getattr(wu_data, attr_name, "")

        if raw_tree_val:
            d_tags, fl_tags, fc_tags = process_tree(raw_tree_val)
            doc[f"d_{bucket_id}"] = d_tags
            doc[f"fl_{bucket_id}"] = fl_tags + ref_entity_ids
            doc[f"fc_{bucket_id}"] = fc_tags

    def add_list_tag(bucket_id, attr_name=None, str_tags_val=None,
                     ref_entity_ids=None):
        ref_entity_ids = ref_entity_ids or []
        str_tag_list_val = str_tags_val or getattr(wu_data, attr_name, "")

        if str_tag_list_val:
            tag_list_val = list({int(x) for x in str_tag_list_val.split("|")})
            doc[f"d_{bucket_id}"] = tag_list_val
            # in case of company_list we are adding referenced companies in fl
            # field to support the referenced company query
            doc[f"fl_{bucket_id}"] = tag_list_val + ref_entity_ids
            doc[f"fc_{bucket_id}"] = ["|" + str(t) for t in tag_list_val]

    story_solr_id = story_entity_id_to_solr_id(wu_data.id, "wu")

    if not wu_data.content_source_id:
        raise MissingSource("Either Missing OR Inactive ContentSource")

    if not wu_data.approved_on and wu_data.status == 2:
        raise MissingApprovedDate(
            "Missing approved_on to the published WebUpdate-ID: {}".format(
                wu_data.id
            )
        )

    change_log_url = encode_change_log_url(wu_data.id, is_rel=True)

    language = wu_data.language
    language = "en" if language not in __ALLOWED_LANG_SET else language
    language_suffix = language.replace("-", "_")

    pub_date = wu_data.approved_on or wu_data.created_on

    doc = {
        "uuid": story_solr_id,
        "show_for_client": wu_data.client_id,

        "entity_ref_model": EntityModelType.WEB_UPDATE.value,
        "doc_type": DocType.WEB_UPDATE.value,
        "upload_type": ESStoryUploadType.PUBLIC_URL.value,

        "is_root": 1,
        "deduplication_uuid": story_solr_id,

        "status": wu_data.status,
        "pub_date": pub_date,
        # "approved_on": wu_data.approved_on,
        "created_on": wu_data.created_on,
        "email_priority": wu_data.email_priority,

        "title": wu_data.title,
        "title_" + language_suffix: wu_data.title,
        "title_exact": "startswith {} endswith".format(wu_data.title),
        "title_alpha_numeric": wu_data.title, # used only to find exact title
        # with same source duplicate and lower() as ist_title_cs is a string
        # field so ignoring the case while indexing
        "title_word_count": (
            len(
                [
                    item for item in wu_data.title.split(" ") if item and not (
                        STOP_DICT[item.encode("ascii", "ignore").decode()] or
                        item[0] in ["#", "@"]
                    )
                ]
            ) or 0
        ),

        "body": wu_data.description,
        "body_" + language_suffix: wu_data.description,
        "body_exact": "startswith {} endswith".format(wu_data.description),
        "word_count": (
            wu_data.description.count(" ") + 1 if wu_data.description else 0
        ),

        "lead": wu_data.description,
        "lead_" + language_suffix: wu_data.description,
        "lead_exact": "startswith {} endswith".format(wu_data.description),

        "word_cloud": (
            (wu_data.description + " " + wu_data.title)
            .encode("ascii", "ignore").decode()
        ),

        "duplicate_tree_root_published_on": pub_date,
        "url_contains": [change_log_url],
        "content_source_urls": [
            "{}|{}<~>>{}<~csd>>{}".format(
                wu_data.content_source_id, wu_data.content_source_name,
                change_log_url, wu_data.content_source_domain_url
            )
        ]
    }

    # -------------------------------------------------------------------------
    if wu_data.diff_content_id:
        doc["diff_content_id"] = wu_data.diff_content_id

    if wu_data.old_image_url:
        doc["old_image"] = wu_data.old_image_url

    if wu_data.new_image_url:
        doc["new_image"] = wu_data.new_image_url

    if wu_data.web_source_id:
        doc["web_source_id"] = wu_data.web_source_id

    if wu_data.web_source_web_url:
        doc["web_source_web_url"] = wu_data.web_source_web_url

    if wu_data.snippet_info:
        try:
            doc["snippet_info"] = json.dumps(wu_data.snippet_info)
        except Exception as e:
            logger.exception(
                "Error: {} in parsing snippet_info of WebUpdate-ID: {}".format(
                    e, wu_data.id
                )
            )

    # -------------------------------------------------------------------------

    if wu_data.generic_data_list:
        generic_attributes_list = [
            REVERSE_GENERIC_DATA_BOOL_MAP[v] for v in wu_data.generic_data_list
        ]
        if generic_attributes_list:
            doc["generic_attributes"] = generic_attributes_list

    if wu_data.status != StoryStatus.PENDING.value:
        # Approved On
        doc["approved_on"] = wu_data.approved_on
        doc["duplicate_tree_root_approved_on"] = wu_data.approved_on

    if wu_data.created_by_id is not None:
        doc["created_by"] = wu_data.created_by_id

    if wu_data.approved_by_user_name is not None:
        doc["approved_by"] = wu_data.approved_by_user_name

    # ------------------------ Add Single Valued Tag --------------------------
    if wu_data.content_source_channel_id:
        add_fk_tag(
            "monitoring_channel", value=wu_data.content_source_channel_id
        )

    add_fk_tag("content_source", value=wu_data.content_source_id)

    doc["fl_language"] = language
    doc["d_language"] = language
    doc["fc_language"] = ["|" + language]

    if wu_data.rating_id:
        add_fk_tag("rating", value=wu_data.rating_id)

    # parse published by company id and related referenced company ids
    if wu_data.published_by_company_id:
        pbc_ref_ids = []
        pbc_str = str(wu_data.published_by_company_id)
        if wu_data.pbc_referenced_company_ids:
            pbc_ref_ids = list(
                map(int, wu_data.pbc_referenced_company_ids.split('|'))
            )

        add_fk_tag(
            "published_by_company", value=pbc_str, ref_entity_ids=pbc_ref_ids
        )

    # ----------------------- Add Multi Valued Tag ----------------------------
    if wu_data.location_tree:
        process_location_tree(wu_data.location_tree, doc)

    if wu_data.source_location_list:
        add_list_tag('source_location', 'source_location_list')
    else:
        cs_id = wu_data.content_source_id
        cs_info = get_source_info(cs_id)
        if cs_info["country_id"]:
            add_list_tag('source_location', 'source_location_list', str_tags_val=str(cs_info["country_id"]))

    if wu_data.explicit_hq_location_tree:
        add_tree_tag('company_hq_location', 'explicit_hq_location_tree')
    elif wu_data.inferred_hq_location_tree:
        add_tree_tag('company_hq_location', 'inferred_hq_location_tree')

    # get the company_list and related referenced_company_ids map
    if wu_data.companies_list_info:
        referenced_company_id_set = set()
        cleaned_entities = []

        for elem in wu_data.companies_list_info.split(
                '|'):
            tree_id, comp_info = elem.split('=')
            comp_id, referenced_company_id_str = comp_info.split('~*~')
            if referenced_company_id_str:
                referenced_company_id_set.update(
                    map(int, referenced_company_id_str.split(','))
                )
            cleaned_entities.append(f"{tree_id}={comp_id}")

        company_tree = '|'.join(cleaned_entities)

        for comp_bucket_id in ["company", "all_company"]:
            add_tree_tag(
                comp_bucket_id,
                'company_tree',
                company_tree,
                ref_entity_ids=list(referenced_company_id_set)
            )

    add_list_tag("person", "persons_list")

    add_tree_tag("industry", "industry_tree")
    add_tree_tag("topic", "topic_tree")
    add_tree_tag("business_event", "business_event_tree")
    add_tree_tag("theme", "theme_tree")
    add_tree_tag("content_type", "contenttype_tree")

    if wu_data.custom_tag_tree_map:
        process_custom_tag_tree(wu_data.custom_tag_tree_map, doc)

    ###########################################################################
    # combined_company is the combination of published_by_company and company,
    # it used only for SI "ALL UPDATES" tab to the newsfeed (just for UI)
    for p_attr in ["d", "fl", "fc"]:
        combined_company_val = (
            doc.get(f"{p_attr}_published_by_company", []) +
            doc.get(f"{p_attr}_company", [])
        )
        if combined_company_val:
            doc[f"{p_attr}_combined_company"] = list(set(combined_company_val))

    add_list_tag("combined_person", "persons_list")

    # Update Commented By field to do faceting of WebUpdate w.r.t commented_by
    doc["fc_commented_by"] = wu_data.commented_by_ids
    return doc


def transform_into_es_doc(wu_data, solr_doc=None):
    if not solr_doc:
        solr_doc = transform_into_solr_doc(wu_data)

    tmp_doc = copy.deepcopy(solr_doc)

    tmp_doc['id'] = tmp_doc['uuid']

    # Remove the attr which is not required for ES
    display_tags_map = dict()
    for k in tmp_doc.copy():
        # We are not creating fc_custom_trigger_ w.r.t user_id
        if k.startswith("fc_custom_trigger_"):
            tmp_doc.pop(k)

        # We are flattened data type for d_*
        if k.startswith("d_"):
            display_tags_map[k] = tmp_doc[k]
            tmp_doc.pop(k)

    tmp_doc["display_tags"] = display_tags_map

    if "snippet_info" in tmp_doc:
        tmp_doc["snippet_info"] = json.loads(tmp_doc["snippet_info"])

    tmp_doc["source_info"] = {
        "id": wu_data.content_source_id,
        "url": solr_doc["url_contains"][0],
        "cs_domain_url": wu_data.content_source_domain_url,
        "global_rank": wu_data.content_source_global_rank
    }
    return tmp_doc


def cfy_bpointer(node):
    """Using this as we do not want to check initial_tree_id of the node each
     time
    """
    if node._initial_tree_id not in node._predecessor.keys():
        return None
    return node._predecessor[node._initial_tree_id]


def generate_tree_map(tree_string):
    path_strings = tree_string.split("|")
    tree_map = {}

    def add_node(t, x, parent=None):
        if x not in t:
            t.create_node(identifier=x, parent=parent)

    def add_nodes_to_tree(t, nodes):
        previous_node = None
        for n in nodes:
            add_node(t, n, parent=previous_node)
            previous_node = n

    for path_string in path_strings:
        tree_id, new_nodes = path_string.split("=")
        new_nodes = new_nodes.split(",")

        existing_tree = tree_map.get(tree_id)
        if existing_tree:
            add_nodes_to_tree(existing_tree, new_nodes)
        else:
            tree = Tree()
            add_nodes_to_tree(tree, new_nodes)
            tree_map[tree_id] = tree

    return tree_map


def process_tree(tree_string):
    index_terms = []
    all_ids = []
    leaf_tags = []

    tree_map = generate_tree_map(tree_string)

    for tree_id, tree in tree_map.items():
        for node in tree.all_nodes():
            tmp_parent_id = cfy_bpointer(node)
            node_identifier = node.identifier
            node_id_int = int(node_identifier)
            node_parent = tmp_parent_id

            all_ids.append(node_id_int)

            index_terms.append(
                (node_parent if node_parent else "") + "|" + node_identifier
            )

            if node.is_leaf():
                leaf_tags.append(node_id_int)

    return leaf_tags, all_ids, index_terms


def process_location_tree(tree_string, doc):

    index_terms = []
    all_ids = []
    leaf_tags = []

    region_ids = []
    country_ids = []
    city_ids = []

    tree_map = generate_tree_map(tree_string)

    for tree_id, tree in tree_map.items():
        for node in tree.all_nodes():
            tmp_parent_id = cfy_bpointer(node)

            location_type, node_identifier = node.identifier.split(">")

            node_id_int = int(node_identifier)
            node_parent = (
                tmp_parent_id.split(">")[1] if tmp_parent_id else None
            )

            if location_type == "Country":
                country_ids.append(node_id_int)

            elif location_type == "City":
                city_ids.append(node_id_int)

            elif location_type == "Region":
                region_ids.append(node_id_int)

            all_ids.append(node_id_int)

            index_terms.append(
                (node_parent if node_parent else "") + "|" + node_identifier
            )

            if node.is_leaf():
                leaf_tags.append(node_id_int)

    doc["fl_location"] = all_ids
    doc["fc_location"] = index_terms

    all_display_locations = country_ids + region_ids  # Mohit told us to show only countries and regions in display tags

    if country_ids:
        doc["fc_countries"] = country_ids
        doc["d_location"] = country_ids

    if city_ids:
        doc["fc_cities"] = city_ids

    if region_ids:
        doc["d_location"] = all_display_locations

    # return leaf_tags, all_ids, index_terms, country_ids, city_ids


def process_custom_tag_tree(tree_string, doc):
    tree_map = generate_tree_map(tree_string)

    def extend_bucket(bucket_id, tag_list):
        bucket = doc.get(bucket_id)
        if bucket:
            bucket.extend(tag_list)
        else:
            doc[bucket_id] = tag_list

    for tree_bucket_id, tree in tree_map.items():
        leaf_tags = []
        all_ids = []
        index_terms = []

        for node in tree.all_nodes():
            tmp_parent_id = cfy_bpointer(node)

            node_id_int = int(node.identifier)

            all_ids.append(node_id_int)

            if tmp_parent_id:
                index_terms.append(tmp_parent_id + "|" + node.identifier)
            else:
                index_terms.append("|" + node.identifier)

            if node.is_leaf():
                leaf_tags.append(node_id_int)

        bucket_id, tree_id = tree_bucket_id.split(";")

        extend_bucket("d_" + bucket_id, leaf_tags)
        extend_bucket("fl_" + bucket_id, all_ids)
        extend_bucket("fc_" + bucket_id, index_terms)


def get_main_query_filtered(filters):
    main_query = get_main_query()
    if filters:
        main_query += " WHERE {} ".format(" AND ".join(filters))

    return main_query


def get_main_query():
    if not hasattr(get_main_query, "query"):
        get_main_query.query = """
        SELECT
          wu.id                                                      AS id,
          wu.client_id                                        AS client_id,
          wu.manual_copy_of_id                        AS manual_copy_of_id,

          wu.created_by_id                                AS created_by_id,
          wu.updated_by_id                                AS updated_by_id,
          approved_by_user.username               AS approved_by_user_name,

          wu.language                                          AS language,

          bcs.id                                      AS content_source_id,
          bcs.name                                  AS content_source_name,
          (
            COALESCE(bcs.global_rank, 2147483647)
          )                                  As content_source_global_rank,
          bcs.domain_url                      AS content_source_domain_url,
          bcs.channel_id                      AS content_source_channel_id,

          pen_rating.id                                       AS rating_id,
          pen_rating.numeric_value                 AS rating_numeric_value,


          pbc.id                                AS published_by_company_id,
          (
            COALESCE(array_to_string(pbc.referenced_company_ids, '|'), '')
          )                                  AS pbc_referenced_company_ids,

          (
            SELECT
              string_agg(
                (
                  SELECT
                    pl.tree_id
                      || '=' ||
                    string_agg(
                      COALESCE(type, 'unknown')
                        || '>' ||
                      CAST(id AS TEXT), ',' ORDER BY lft ASC
                    )
                  FROM
                    penseive_location
                  WHERE
                    tree_id = pl.tree_id AND
                    lft <= pl.lft AND
                    rght >= pl.rght
                ),
                '|' ORDER BY pl.lft DESC
                )
            FROM
              website_tracking_webupdate_locations AS wtwl
                LEFT JOIN penseive_location pl
                  ON wtwl.location_id = pl.id
            WHERE
              wtwl.webupdate_id = wu.id AND pl.active = TRUE
          )                                               As location_tree,

          (
            SELECT
              string_agg(
                CAST(pl.id AS TEXT), '|'
              )
            FROM
              website_tracking_webupdate_source_locations AS wtwsl
                LEFT JOIN penseive_location pl
                  ON wtwsl.location_id = pl.id
            WHERE
              wtwsl.webupdate_id = wu.id AND pl.active = TRUE
          )                                            AS source_location_list,
              
          (
            SELECT
                string_agg(
                    (
                    SELECT
                      pl.tree_id || '=' || string_agg(
                        CAST(id AS TEXT), ',' ORDER BY lft ASC
                      )
                    FROM
                      penseive_location
                    WHERE
                      tree_id = pl.tree_id AND
                      lft <= pl.lft AND
                      rght >= pl.rght
                    ),
                    '|' ORDER BY pl.lft DESC
                )
            FROM
                website_tracking_webupdate_companies_hq_locations AS wtwchl
                  LEFT JOIN penseive_location pl
                  ON wtwchl.location_id = pl.id AND wtwchl.webupdate_id = wu.id
            WHERE
                pl.active = true
          )                                       AS explicit_hq_location_tree,
          
          (
            SELECT
                string_agg(
                  (
                    SELECT
                      pl.tree_id || '=' || string_agg(
                        CAST(id AS TEXT), ',' ORDER BY lft ASC
                      )
                    FROM
                      penseive_location
                    WHERE
                      tree_id = pl.tree_id AND
                      lft <= pl.lft AND
                      rght >= pl.rght
                  ),
                  '|' ORDER BY pl.lft DESC
                )
            FROM 
                penseive_location AS pl
            WHERE
                pl.id in (
                    SELECT
                        CASE
                          WHEN pb.city_id IS NOT NULL
                            THEN pb.city_id
                          WHEN pb.state_id IS NOT NULL
                            THEN pb.state_id
                          WHEN pb.country_id IS NOT NULL
                            THEN pb.country_id
                        END
                    FROM
                        website_tracking_webupdate_companies    AS wuc
                        INNER JOIN penseive_company    AS    pc
                            ON (pc.id = wuc.company_id AND wuc.webupdate_id = wu.id)
                        INNER JOIN penseive_branch    AS pb
                            ON pb.company_id = pc.id
                    WHERE
                        pc.active = true AND
                        pb.type = 1
                ) AND pl.active=true
          )                                       AS inferred_hq_location_tree,
            
          (
            SELECT
              string_agg(
              (
                  SELECT
                    pc.tree_id 
                      || '=' || 
                    string_agg(
                      CAST(id AS TEXT), ',' ORDER BY lft ASC
                    )
                      || '~*~' ||
                    COALESCE(array_to_string(
                      pc.referenced_company_ids, ','), ''
                    )
                  FROM
                    penseive_company
                  WHERE
                    tree_id = pc.tree_id AND
                    lft <= pc.lft AND
                    rght >= pc.rght
              ),
                '|' ORDER BY pc.lft DESC
              )
            FROM
              website_tracking_webupdate_companies  AS wtwc
                LEFT JOIN penseive_company  AS pc
                  ON wtwc.company_id = pc.id
            WHERE
              wtwc.webupdate_id = wu.id AND pc.active = TRUE
          )                                        AS companies_list_info,

          (
            SELECT
              string_agg(CAST(pp.id AS TEXT), '|')
            FROM
              website_tracking_webupdate_persons  AS wtwp
                LEFT JOIN penseive_person pp
                  ON wtwp.person_id = pp.id
            WHERE
              wtwp.webupdate_id = wu.id AND pp.active = TRUE
          )                                                AS persons_list,

          (
            SELECT
              string_agg(
                (
                  SELECT
                    pi.tree_id
                      || '=' ||
                    string_agg(
                      CAST(id AS TEXT), ',' ORDER BY lft ASC
                    )
                  FROM
                    penseive_industry
                  WHERE
                    tree_id = pi.tree_id AND
                    lft <= pi.lft AND
                    rght >= pi.rght
                ),
                '|' ORDER BY pi.lft DESC, ''
              )
            FROM
              website_tracking_webupdate_industries AS wtwi
                LEFT JOIN penseive_industry pi
                  ON wtwi.industry_id = pi.id
            WHERE
              wtwi.webupdate_id = wu.id AND pi.active = TRUE
          )                                               AS industry_tree,

          (
            SELECT
              string_agg(
                (
                  SELECT
                    pt.tree_id
                      || '=' ||
                    string_agg(
                      CAST(id AS TEXT), ',' ORDER BY lft ASC
                    )
                  FROM
                    penseive_topic
                  WHERE
                    tree_id = pt.tree_id AND
                    lft <= pt.lft AND
                    rght >= pt.rght
                ),
                '|' ORDER BY pt.lft DESC
              )
            FROM
              website_tracking_webupdate_topics AS wtwt
                LEFT JOIN penseive_topic pt
                  ON wtwt.topic_id = pt.id
            WHERE
              wtwt.webupdate_id = wu.id AND pt.active = TRUE
          )                                                  AS topic_tree,

          (
            SELECT
              string_agg(
                (
                  SELECT
                    pbe.tree_id
                      || '=' ||
                    string_agg(
                      CAST(id AS TEXT), ',' ORDER BY lft ASC
                    )
                  FROM
                    penseive_businessevent
                  WHERE
                    tree_id = pbe.tree_id AND
                    lft <= pbe.lft AND
                    rght >= pbe.rght
                ),
                '|' ORDER BY pbe.lft DESC
              )
            FROM
              website_tracking_webupdate_business_events AS wtwbe
                LEFT JOIN penseive_businessevent pbe
                  ON wtwbe.businessevent_id = pbe.id
            WHERE
              wtwbe.webupdate_id = wu.id AND pbe.active = TRUE
          )                                             AS business_event_tree,
          
          (
            SELECT
              string_agg(
                (
                  SELECT
                    pth.tree_id
                      || '=' ||
                    string_agg(
                      CAST(id AS TEXT), ',' ORDER BY lft ASC
                    )
                  FROM
                    penseive_theme
                  WHERE
                    tree_id = pth.tree_id AND
                    lft <= pth.lft AND
                    rght >= pth.rght
                ),
                '|' ORDER BY pth.lft DESC
              )
            FROM
              website_tracking_webupdate_themes AS wtwth
                LEFT JOIN penseive_theme pth
                  ON wtwth.theme_id = pth.id
            WHERE
              wtwth.webupdate_id = wu.id AND pth.active = TRUE
          )                                                  AS theme_tree,
          
          (
             CASE
      WHEN pen_ct.id IS NOT NULL AND pen_ct.parent_id IS NOT NULL
        THEN
          (
            SELECT
              pen_ct.tree_id
                || '=' ||
              string_agg(CAST(id AS TEXT), ',' ORDER BY lft ASC)
            FROM
              penseive_contenttype
            WHERE
              tree_id = pen_ct.tree_id AND
              lft <= pen_ct.lft AND
              rght >= pen_ct.rght
          )
      WHEN pen_ct.id IS NOT NULL
        THEN
          CAST(pen_ct.id AS TEXT) || '=' || CAST(pen_ct.id AS TEXT)
      ELSE
        NULL
      END
  )                                                     AS contenttype_tree,
          (
            SELECT
              string_agg(
                (
                  SELECT
                    CAST(pct.bucket_id AS TEXT)
                      || ';' ||
                    CAST(pct.tree_id AS TEXT)
                      || '=' ||
                    string_agg(
                      CAST(id AS TEXT), ',' ORDER BY lft ASC
                    )
                  FROM
                    penseive_customtag
                  WHERE
                    tree_id = pct.tree_id AND
                    lft <= pct.lft AND
                    rght >= pct.rght
                ),
                '|' ORDER BY pct.lft ASC
              )
            FROM
              website_tracking_webupdate_custom_tags  AS wtwct
                LEFT JOIN penseive_customtag pct
                  ON wtwct.customtag_id = pct.id
            WHERE
              wtwct.webupdate_id = wu.id AND pct.active = TRUE
          )                                         AS custom_tag_tree_map,

          wu.diff_content_id                            AS diff_content_id,
          wtws.id                                         AS web_source_id,
          wtws.web_url                               AS web_source_web_url,

          wu.hash                                                  AS hash,
          wu.status                                              As status,
          wu.title                                                AS title,
          wu.description                                    As description,
          wu.old_image                                    AS old_image_url,
          wu.new_image                                    AS new_image_url,
          wu.email_priority                              AS email_priority,
          wu.snippet_info                                  As snippet_info,

          wu.generic_data_list                        AS generic_data_list,
          wu.generic_data_json                        AS generic_data_json,

          TO_CHAR(
            (wu.created_on AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'
          )                                                   AS created_on,
          TO_CHAR(
            (wu.updated_on AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'
          )                                                  AS updated_on,
          TO_CHAR(
            (wu.approved_on AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'
          )                                                 AS approved_on,

          (
            SELECT
              array_agg(DISTINCT c.commented_by_id)
            FROM
              comment_comment As c
            WHERE
              c.entity_id = wu.id AND
              wu.status != -1 AND
              c.parent_id IS NULL AND
              c.entity_ref_model = 1
          )                                            As commented_by_ids

        FROM
          website_tracking_webupdate AS wu
            LEFT JOIN brief_contentsource AS bcs
              ON (bcs.id = wu.source_id AND bcs.active = TRUE)

            LEFT JOIN penseive_rating AS pen_rating
              ON pen_rating.id = wu.rating_id

            LEFT JOIN penseive_contenttype AS pen_ct
              ON (pen_ct.id = wu.content_type_id AND pen_ct.active = TRUE)

            LEFT JOIN penseive_company  AS pbc
              ON pbc.id = wu.published_by_company_id

            LEFT JOIN auth_user AS approved_by_user
              ON approved_by_user.id = wu.approved_by_id

            LEFT JOIN website_tracking_websource As wtws
              ON wtws.id = wu.web_source_id
        """
    return get_main_query.query
