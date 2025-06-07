
import copy
import logging
import threading
from collections import defaultdict

from django.conf import settings
from django.template import loader

import difflib
from bs4 import BeautifulSoup as bsoup

from config.utils import get_model
from contify.cutils.cfy_enum import StoryStatus
from contify.cutils.utils import send_mail_via_sendgrid
from contify.subscriber.models import CompanyPreferences
from contify.subscriber.utils import get_accessible_buckets_map
from contify.website_tracking import constants as wu_constants


logger = logging.getLogger(__name__)


STYLE_ADD = "font-weight: bold; color: #00FF00;"
STYLE_DEL = "font-weight: bold; color: #FF0000; text-decoration: line-through;"
STYLE_REP = "font-weight: bold; color: #0000FF;"


def send_bulk_action_mail_analyst(subscriber, web_update_list, new_val, action):
    try:
        user = subscriber.user
        if not user.is_staff or user.id in settings.DEBUG_USER_IDS:
            thread = threading.Thread(
                target=web_update_bulk_action_notification, args=(
                    subscriber, web_update_list, new_val, action
                )
            )
            thread.start()
    except Exception as e:
        logger.info("Unable to WebUpdate send bulk action mail")


def web_update_bulk_action_notification(subscriber, web_update_list, new_val,
                                        action):
    from contify.story.change_log import transform_value_to_name
    logger.info(
        "SendBulkActionWebUpdate!, Sending Mail to Analyst for Bulk WebUpdate"
        " Action: {}".format(action)
    )
    try:
        try:
            cp = CompanyPreferences.objects.get(id=subscriber.client_id)
            if not cp.edit_notify_email and not cp.secondary_emails:
                return

        except Exception as e:
            logger.exception(
                "SendBulkActionWebUpdate!, CompanyPreference does not exist "
                "for subscriber: {}".format(subscriber)
            )
            return

        to = cp.edit_notify_email
        if cp.secondary_emails:
            to = [cp.edit_notify_email] + cp.secondary_emails

        if action in ["approved_by_id"]:
            subject = "WebUpdate approved by {}".format(subscriber.user.email)
        else:
            subject = "WebUpdate marked as {}".format(
                transform_value_to_name()[action][new_val]
            )

        context = {
            "MEDIA_URL": settings.MEDIA_URL,
            "subject": subject,
            "editor": "{} ({})".format(
                subscriber.name, subscriber.email
            ),
            "action": action.replace('_', ' '),
            "new_value": new_val,
            "web_update_list": web_update_list
        }
        template = loader.get_template(
            "website_tracking/email/bulk_action_notification.html"
        )
        html = template.render(context)

        send_mail_via_sendgrid(
            "admin@contify.com", to, subject, html,
            reply_to="admin@contify.com",
        )

        logger.info(
            "SendBulkActionWebUpdate!, Sent Mail to Analyst for Bulk WebUpdate"
            " Action: {}, to: {}".format(action, to)
        )
    except Exception as e:
        logger.exception(
            "SendBulkActionWebUpdate!, Exception in sending mail for Bulk "
            "WebUpdate Action: {}, error: {}".format(action, e)
        )


def make_rel_tags_value_iterable(bucket_map):
    """
    Convert single related tags value into list

    :param bucket_map (dict) mapping of WebUpdate tags field and their values
    :return: field (tag) with their value (list) for a WebUpdate
    """
    new_bucket_map = {}
    for wu_field, wu_field_value in bucket_map.items():
        if wu_field == "language":
            continue

        if wu_field in wu_constants.TAGS_SINGLE_FIELDS:
            if wu_field_value is None:
                new_bucket_map[wu_field] = []
            if not isinstance(wu_field_value, list):
                new_bucket_map[wu_field] = [wu_field_value]
            else:
                new_bucket_map[wu_field] = wu_field_value

        elif wu_field in wu_constants.TAGS_MULTI_FIELDS:
            new_bucket_map[wu_field] = wu_field_value
        else:
            logger.info(
                f"WebUpdateMail, Unknown field {wu_field} found which is meant"
                f" for tags to WebUpdate"
            )
    return new_bucket_map


def get_tags_info(web_update):
    """
    Get WebUpdate tags info

    :param web_update:
    :return: buckets_map
    """
    bucket_map = dict()
    for f in wu_constants.TAGS_SINGLE_FIELDS:
        ff = f if f in ["language"] else f"{f}_id"
        tag_id = getattr(web_update, ff)
        if tag_id:
            bucket_map[f] = tag_id

    for f in wu_constants.TAGS_MULTI_FIELDS:
        tags_id = list(getattr(web_update, f).values_list("id", flat=True))
        if tags_id:
            bucket_map[f] = tags_id
    return bucket_map


def get_tags_info_from_dict(tags_map):
    bucket_map = {}
    custom_tags = []
    for b_id, tags_id in tags_map.items():
        if not tags_id:
            continue

        if isinstance(b_id, int) or b_id.isdigit():
            custom_tags.extend(tags_id)
            continue

        wu_field = wu_constants.STD_BUCKET_ID_FIELDS_MAP.get(b_id)
        if not wu_field:
            continue

        if wu_field in wu_constants.TAGS_SINGLE_FIELDS:
            bucket_map[wu_field] = tags_id[0]
        else:
            bucket_map[wu_field] = tags_id

    if custom_tags:
        bucket_map["custom_tags"] = custom_tags

    return bucket_map


def get_tags_details_map(tags_map):
    """
    tags_map is a dict:
        key is field name of WebUpdate which are meant for tags (relations)
        value is the list of tags id

    example:
        tags_map = {
            "companies": [12, 34],
            "locations: [3],
            "custom_tags: [233, 545],
            source: [3, 4],
            ...........
            ...........
        }

    output is a dict like below:
        bucket_tags_map = {
            "company: {
                12: 'Contify',
                34: 'Test Contify'
            },
            521: {
                233: 'India'
            },
            231: {
                545: 'Tcs'
            }
            ....
        }
    """
    bucket_tags_map = defaultdict(dict)
    for wu_tag_f, tags_id in tags_map.items():
        if not tags_id:
            continue

        if wu_tag_f not in wu_constants.WU_TAG_INFO:
            logger.info(
                "WebUpdateMail!, Unknown '{}' field found which is not the "
                "part of tags in WebUpdate".format(wu_tag_f)
            )
            continue

        tag_model = get_model(*wu_constants.WU_TAG_INFO[wu_tag_f]["model_info"])
        if wu_tag_f == "custom_tags":
            ct_tags = tag_model.objects.only("id", "name", "bucket_id").filter(
                id__in=tags_id
            )
            for tag in ct_tags:
                bucket_tags_map[tag.bucket.id][tag.id] = tag.name
        else:
            st_tags = dict(
                tag_model.objects
                .filter(id__in=tags_id)
                .values_list("id", "name")
            )
            for t_id, t_name in st_tags.items():
                bucket_tags_map[tag_model.bucket_id][t_id] = t_name
    return bucket_tags_map


def new_tag_with_style(tag, style_string):
    new_tag = '<span style="{style}">{name}</span>'.format(
        style=style_string, name=tag
    )
    return new_tag


def generate_diff(new_item, old_item):
    if not (old_item and isinstance(old_item, str)):
        old_item = ""

    if not (new_item and isinstance(new_item, str)):
        new_item = ""

    if new_item == old_item:
        return new_item

    sm = difflib.SequenceMatcher(None, old_item, new_item)
    output = []

    for opcode, i1, i2, j1, j2 in sm.get_opcodes():
        if opcode == "equal":
            output.append(sm.a[i1:i2])
        elif opcode == "insert":
            output.append(
                '<span style="{style}">{text}</span>'.format(
                    style=STYLE_ADD, text=sm.b[j1:j2]
                )
            )
        elif opcode == 'delete':
            output.append(
                '<span style="{style}">{text}</span>'.format(
                    style=STYLE_DEL, text=sm.a[i1:i2]
                )
            )
        elif opcode == 'replace':
            output.append(
                '<span style="{style}">{text}</span>'.format(
                    style=STYLE_REP, text=sm.b[j1:j2]
                )
            )
        else:
            raise RuntimeError("unexpected opcode")
    return "".join(output)


def get_article(web_update, tags_map=None, exc_bucket_map=False):
    """
    Get defined attributes only from WebUpdate object

    :param web_update: (orm object)
    :param tags_map: (None or a python dict)
    :param exc_bucket_map: (bool), useful to exclude the bucket_map info

    :return: key-value pair of story object
    """
    data_dict = {
        "title": web_update.title,
        "status": web_update.status,
        "created_on": web_update.created_on,
        "email_priority": web_update.email_priority,
        "description": web_update.description,
        "approved_on": web_update.approved_on,
        "approved_by_id": web_update.approved_by_id
    }
    if exc_bucket_map:
        return data_dict

    # data_dict.update(bucket_map)
    if not tags_map:
        bucket_map = get_tags_info(web_update)
    else:
        bucket_map = get_tags_info_from_dict(tags_map)

    return data_dict, bucket_map


def get_tags_formatted_context(wu_tags_map, bucket_map, accessible_buckets_list):
    tags_cxt = defaultdict(list)

    for b_id in accessible_buckets_list:
        if b_id in ["company", "combined_company"]:
            b_id = "company"
        elif b_id in ["person", "combined_person"]:
            b_id = "person"

        if isinstance(b_id, int):
            if "custom_tags" in wu_tags_map:
                for tag_id in wu_tags_map["custom_tags"]:
                    if tag_id in bucket_map[b_id]:
                        tags_cxt[b_id].append(bucket_map[b_id][tag_id])

        elif b_id in wu_constants.STD_BUCKET_ID_FIELDS_MAP:
            wu_field = wu_constants.STD_BUCKET_ID_FIELDS_MAP[b_id]

            if wu_field in wu_tags_map:
                tags_val = wu_tags_map[wu_field]

                if b_id == "language":
                    if tags_val in wu_constants.SUPPORTED_LANGUAGES:
                        tags_cxt[b_id].append(
                            wu_constants.SUPPORTED_LANGUAGES[tags_val]
                        )
                else:
                    if isinstance(tags_val, int):
                        tags_val = [tags_val]

                    for tag_id in tags_val:
                        if tag_id in bucket_map[b_id]:
                            tags_cxt[b_id].append(bucket_map[b_id][tag_id])

    return dict(tags_cxt)


def get_article_context(articles, web_update_id, subscriber):
    """
    Get mail context with updated or added style article with subject line

    :param articles: (dict) story articles
    :param web_update_id: (int) WebUpdate id
    :param subscriber: request subscriber object
    :return: formatted article, subject line
    """
    accessible_buckets_list = list(
        get_accessible_buckets_map(subscriber).keys()
    )
    if web_update_id:
        old_web_update, old_bucket_map = articles[0]
        new_web_update, new_bucket_map = articles[1]

        tmp_bucket_map = [
            make_rel_tags_value_iterable(old_bucket_map),
            make_rel_tags_value_iterable(new_bucket_map)
        ]

        tmp_tags_map = defaultdict(set)
        for tags_values_map in tmp_bucket_map:
            for wu_field, tags_id_list in tags_values_map.items():
                tmp_tags_map[wu_field].update(tags_id_list)

        bucket_map = get_tags_details_map(tmp_tags_map)

        old_article = {
            "articleTitle": "Original WebUpdate",
            "webUpdate": old_web_update,
            "bucketMap": get_tags_formatted_context(
                old_bucket_map, bucket_map, accessible_buckets_list
            )
        }
        new_article = {
            "articleTitle": "Edited WebUpdate",
            "webUpdate": new_web_update,
            "bucketMap": get_tags_formatted_context(
                new_bucket_map, bucket_map, accessible_buckets_list
            )
        }

        # Add style for diff
        diff_title = generate_diff(
            new_article["webUpdate"]["title"],
            old_article["webUpdate"]["title"]
        )
        new_article["webUpdate"]["title"] = diff_title

        diff_description = generate_diff(
            new_article["webUpdate"]["description"],
            old_article["webUpdate"]["description"]
        )
        new_article["webUpdate"]["description"] = diff_description

        # Add style for diff tags
        for b_id, new_t_info in new_article["bucketMap"].items():
            if b_id in old_article["bucketMap"]:
                old_t_info = old_article["bucketMap"][b_id]

                for index, new_tag in enumerate(new_t_info):
                    if new_tag not in old_t_info:
                        new_t_info[index] = new_tag_with_style(
                            new_tag, STYLE_ADD
                        )

                for index, old_tag in enumerate(old_t_info):
                    if old_tag not in new_t_info:
                        new_t_info.append(
                            new_tag_with_style(old_tag, STYLE_DEL)
                        )
            else:
                for index, tag in enumerate(new_t_info):
                    new_t_info[index] = new_tag_with_style(tag, STYLE_ADD)

        for b_id, old_t_info in old_article["bucketMap"].items():
            if b_id not in new_article["bucketMap"]:
                new_article["bucketMap"][b_id] = copy.deepcopy(old_t_info)

                new_t_info = new_article["bucketMap"][b_id]
                for index, tag in enumerate(new_t_info):
                    new_t_info[index] = new_tag_with_style(tag, STYLE_DEL)

        action_status = "Updated"
        if (old_web_update["status"] != new_web_update["status"] and
                new_web_update["status"] == StoryStatus.REJECTED.value):
            action_status = "Rejected"

        subject = "{} | {} | WebUpdate Content {}".format(
            subscriber.client.company.name, subscriber.user.username,
            action_status
        )
        return [old_article, new_article], subject

    else:
        tmp_tags_map = defaultdict(set)

        new_web_update, new_bucket_map = articles[0]

        tmp_bucket_map = make_rel_tags_value_iterable(new_bucket_map)
        for wu_field, tags_id_list in tmp_bucket_map.items():
            tmp_tags_map[wu_field].update(tags_id_list)

        bucket_map = get_tags_details_map(tmp_tags_map)

        new_article = {
            "articleTitle": "New WebUpdate",
            "webUpdate": new_web_update,
            "bucketMap": get_tags_formatted_context(
                new_bucket_map, bucket_map, accessible_buckets_list
            )
        }

        subject = "{} | {} | New Content (WebUpdate) Uploaded".format(
            subscriber.client.company.name, subscriber.user.username
        )
        return [new_article], subject


def send_mail_async(articles, web_update_id, subscriber, bulk_update):
    subject = ""
    if not bulk_update:
        articles_to_send, subject = get_article_context(
            articles, web_update_id, subscriber
        )
    else:
        articles_to_send = []
        for article, wu_id in zip(articles, web_update_id):
            article_to_send, subject = get_article_context(
                article, wu_id, subscriber
            )
            articles_to_send.extend(article_to_send)

    logger.info(f"WebUpdateMail!, Sending Mail to Analyst: {subject}")

    try:
        cp = CompanyPreferences.objects.get(id=subscriber.client_id)
        if not cp.edit_notify_email:
            return
    except Exception as e:
        logger.exception(
            "WebUpdateMail!, CompanyPreference does not exist for subscriber: "
            "{}".format(subscriber)
        )
        return

    try:
        to = cp.edit_notify_email
        if cp.secondary_emails:
            to = [cp.edit_notify_email] + cp.secondary_emails

        cxt = {
            "MEDIA_URL": settings.MEDIA_URL,
            "subject": subject,
            "editor": "{} ({})".format(subscriber.name, subscriber.email),
            "articles": articles_to_send
        }
        rendered_string = loader.render_to_string(
            "website_tracking/email/content_uploaded.html", context=cxt
        )

        soup = bsoup(rendered_string, "html.parser")
        text = "".join(soup.findAll(text=True))

        send_mail_via_sendgrid(
            "admin@contify.com", to, subject, rendered_string,
            reply_to="admin@contify.com", plain_text=text
        )

        logger.info(
            f"WebUpdateMail!, Sent mail to analyst: {to} for WebUpdateID: "
            f"{web_update_id}"
        )
    except Exception as e:
        logger.exception(
            "WebUpdateMail!, Exception in sending mail: {}".format(e)
        )


def send_mail_analyst(articles, web_update_id, subscriber, bulk_update=False):
    user = subscriber.user
    if not user.is_staff or user.id in settings.DEBUG_USER_IDS:
        thread = threading.Thread(
            target=send_mail_async, args=(
                articles, web_update_id, subscriber, bulk_update
            )
        )
        thread.start()
