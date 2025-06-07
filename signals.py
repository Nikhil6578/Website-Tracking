
import logging
import traceback

from django.conf import settings
from django.dispatch import receiver
from django.db import transaction
from django.db.models.signals import post_save

from config.constants import USE_WEB_UPDATE_POST_SIGNAL
from contify.website_tracking.models import WebUpdate
from contify.website_tracking.indexer_raw import index_web_updates


logger = logging.getLogger(__name__)


@receiver(post_save, sender=WebUpdate)
def post_web_update_save_handler(sender, instance, **kwargs):
    def tmp_index_story():
        if USE_WEB_UPDATE_POST_SIGNAL or settings.DEBUG:
            logger.info(
                "WebUpdate post signal initiated for id: {}".format(
                    instance.id
                )
            )
            index_web_updates(
                instance.id, soft_commit=True, wait_searcher=False
            )
        else:
            logger.info(
                "WebUpdate post signal Disabled for id: {}".format(instance.id)
            )

    # TODO: Deal with instance in index_stories rather making a DB query
    try:
        transaction.on_commit(tmp_index_story)
    except transaction.TransactionManagementError as e:
        logger.info("error while indexing WebUpdate id {}, traceback {}".format(
            instance.id, traceback.format_exc())
        )
