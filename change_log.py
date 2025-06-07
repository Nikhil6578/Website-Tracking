
import copy
import logging
from datetime import datetime

from django.db import transaction
from django.db.models import QuerySet

from contify.entity.cfy_enum import (
    JournalActionType, JournalContentType, JournalSource
)
from contify.entity.constants import NEW_V, OLD_V
from contify.entity.models import Journal
from contify.entity.utils import get_journal_format

logger = logging.getLogger(__name__)


_JOURNAL_SOURCE = JournalSource.WEBAPP.value
_JOURNAL_CONTENT_TYPE = JournalContentType.WEB_UPDATE.value


def clean_serialized_data(serialized_data):
    """
    compare each field in new and old article and pop field if they are equal
    and both must not be none.

    :param serialized_data: formatted journal serialized data
    :return: differences
    """
    serialized_data_copy = copy.deepcopy(serialized_data)
    old_journal = serialized_data_copy.get(OLD_V, {})
    new_journal = serialized_data_copy.get(NEW_V, {})
    keys_set = set(old_journal.keys()).union(set(new_journal.keys()))

    for key in keys_set:
        is_same_val = (
            old_journal.get(key) == new_journal.get(key) and
            key in old_journal and
            key in new_journal
        )
        if is_same_val:
            old_journal.pop(key)
            new_journal.pop(key)
    return serialized_data_copy


def bulk_update_journal(user, articles, object_ids, comment="",
                        action_type=None):
    """
    Creates multiple records for any manual manipulations
    in webupdate from newsfeed or admin.
    Params: user(User Orm): the user by
            articles(dict): journal format
            object_ids(list): row ids
            comment(string): optional comment
            action_type(enum): add/edit
    Return: None
    """
    journal_object_list = []
    action_type = action_type or JournalActionType.UPDATE.value

    for article, object_id in zip(articles, object_ids):
        cleaned_serialized_data = clean_serialized_data(article)
        is_not_clean_data = (
            not cleaned_serialized_data.get(OLD_V) and
            not cleaned_serialized_data.get(NEW_V)
        )
        if is_not_clean_data:
            continue

        journal_object_list.append(
            Journal(
                action_by=user,
                object_uuid=object_id,
                content_type=_JOURNAL_CONTENT_TYPE,
                action_type=action_type,
                source=_JOURNAL_SOURCE,
                action_on=datetime.now(),
                comment=comment,
                serialized_data=cleaned_serialized_data
            )
        )

    Journal.objects.bulk_create(journal_object_list)

    logger.info(
        "Successfully bulk created with web_update_ids: {}".format(object_ids)
    )


def update_journal(article, user, web_update_id, comment="", action_type=None):
    action_type = action_type or JournalActionType.UPDATE.value
    cleaned_serialized_data = clean_serialized_data(article)

    is_not_clean_data = (
        not cleaned_serialized_data.get(OLD_V) and
        not cleaned_serialized_data.get(NEW_V)
    )
    if is_not_clean_data:
        return

    journal = Journal()
    journal.action_by = user
    journal.object_uuid = web_update_id
    journal.content_type = _JOURNAL_CONTENT_TYPE
    journal.action_type = action_type
    journal.source = _JOURNAL_SOURCE
    journal.action_on = datetime.now()
    journal.comment = comment
    journal.serialized_data = cleaned_serialized_data
    journal.save()
    logger.info(
        "Successfully saved web_update_id: {} with journal version {}".format(
            web_update_id, journal.id
        )
    )


def _newsfeed_change_status_update_journal(user, old_web_updates,
                                           new_web_updates):
    try:
        articles = []
        ids_list = []
        web_updates_count = len(old_web_updates)

        for index in range(web_updates_count):
            ids_list.append(old_web_updates[index].id)

            old_story = {
                "status": old_web_updates[index].status,
                "generic_data_json": old_web_updates[index].generic_data_json
            }

            articles.append({
                OLD_V: old_story,
                NEW_V: {
                    "status": new_web_updates[index].status,
                    "generic_data_json": new_web_updates[index].generic_data_json
                }
            })

        bulk_update_journal(user, articles, ids_list, comment="newsfeed")
    except Exception as e:
        logger.exception(
            "unable to save WebUpdate version with exception {}.".format(e)
        )


def _newsfeed_change_approved_by_update_journal(user, old_web_updates,
                                                current_datetime):
    try:
        old_article = []
        web_updates_id = []
        articles = []
        web_updates_count = len(old_web_updates)

        for index in range(web_updates_count):
            web_updates_id.append(old_web_updates[index].id)
            old_article.append({
                "approved_by_id": old_web_updates[index].approved_by_id,
                "approved_on": old_web_updates[index].approved_on
            })

        for index in range(web_updates_count):
            articles.append({
                OLD_V: old_article[index],
                NEW_V: {
                    "approved_by_id": user.id,
                    "approved_on": current_datetime
                }
            })

        bulk_update_journal(user, articles, web_updates_id, comment="newsfeed")
    except Exception as e:
        logger.exception(
            "unable to save webupdate version with exception {}.".format(e)
        )


def _newsfeed_change_priority_update_journal(user, old_web_update,
                                             email_priority):
    try:
        article = {
            OLD_V: {
                "email_priority": old_web_update.email_priority
            },
            NEW_V: {
                "email_priority": email_priority
            }
        }

        update_journal(article, user, old_web_update.id, comment="newsfeed")
    except Exception as e:
        logger.exception(
            "unable to save webupdate version with exception {}.".format(e)
        )


def _newsfeed_change_rating_update_journal(user, old_web_update, rating_id):
    try:
        article = {
            OLD_V: {
                "rating_id": old_web_update.rating_id
            },
            NEW_V: {
                "rating_id": None if rating_id == 0 else rating_id
            }
        }
        update_journal(article, user, old_web_update.id, comment="newsfeed")
    except Exception as e:
        logger.exception(
            "unable to save webupdate version with exception {}.".format(e)
        )


def _newsfeed_create_webupdate_journal(user, articles, webupdate_id):
    """
    creates a journal for add/edit by a user from newsfeed.
    Params: user(User orm): by user
            articles(dict): journal format
            webupdate_id(int): object id
    Return None
    """
    try:
        article, action_type = get_journal_format(articles)
        update_journal(
            article, user, webupdate_id, comment="newsfeed",
            action_type=action_type
        )
    except Exception as e:
        logger.exception(
            "unable to save webupdate version with exception {}.".format(e)
        )


@transaction.atomic
def _single_story_admin_update_journal(user, webupdate_id, form):
    """
    creates a journal for add/edit by a user from admin.
    Params: user(User orm): by user
            articles(dict): journal format
            webupdate_id(int): object id
    Return None
    """
    try:
        article = {}
        new_story = {}
        action_type = JournalActionType.UPDATE
        is_pub_date_changed = 'pub_date' in form.changed_data
        if form.initial:
            if is_pub_date_changed:
                form.initial['pub_date'] = form.initial['pub_date'].isoformat()
            old_story = {}
            for field in form.changed_data:
                field_value = form.initial[field]
                if isinstance(field_value, list):
                    new_field_value = []
                    for value in field_value:
                        # getting orm object from admin save
                        if not isinstance(value, int):
                            new_field_value.append(value.id)
                else:
                    new_field_value = field_value
                old_story[field] = new_field_value
            article[OLD_V] = old_story
        else:
            action_type = JournalActionType.ADD
        if is_pub_date_changed:
            form.cleaned_data['pub_date'] = form.cleaned_data['pub_date'].isoformat()

        for field in form.changed_data:
            field_value = form.cleaned_data[field]
            # getting orm queryset from admin save
            if isinstance(field_value, QuerySet):
                field_value = list(field_value.values_list("id", flat=True))
            else:
                field_value = field_value.id
            new_story[field] = field_value
        article[NEW_V] = new_story
        update_journal(
            article, user, webupdate_id, comment="admin", action_type=action_type
        )
    except Exception as e:
        logger.error(
            u"unable to save webupdate version with exception {}.".format(e)
        )


newsfeed_change_status_update_journal = _newsfeed_change_status_update_journal

newsfeed_change_approved_by_update_journal = (
    _newsfeed_change_approved_by_update_journal
)

newsfeed_change_priority_update_journal = (
    _newsfeed_change_priority_update_journal
)

newsfeed_change_rating_update_journal = _newsfeed_change_rating_update_journal

newsfeed_create_webupdate_journal = _newsfeed_create_webupdate_journal

single_story_admin_update_journal = _single_story_admin_update_journal
