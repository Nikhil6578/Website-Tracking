
import copy
import logging
import os
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.postgres.fields import ArrayField, JSONField
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

import tldextract

from config.constants import SYSTEM_ADMIN_USER_ID, EXPLICIT_SITE_URL
from contify.cutils.cfy_enum import EmailPriority, StoryStatus
from contify.cutils.constants import SUPPORTED_LANGUAGES
from contify.cutils.utils import get_choices, detect_language
from contify.subscriber.models import CompanyPreferences
from contify.website_tracking import cfy_enum as wt_enum
from contify.website_tracking import constants as ws_constant
from contify.website_tracking.utils import get_storage, encode_change_log_url


logger = logging.getLogger(__name__)


class WebSource(models.Model):
    """
    This is the table where URLs will be added for tracking and their run
    configuration. It does not hold the client information as the URls may be
    common for the multiple clients, so clients related information will be in
     a separate table ie. it only holds the URLs and their run configuration.
    """
    created_by = models.ForeignKey(
        User, related_name='created_wt_source_set',
        default=SYSTEM_ADMIN_USER_ID, on_delete=models.SET_DEFAULT
    )
    updated_by = models.ForeignKey(
        User, related_name='updated_wt_source_set',
        default=SYSTEM_ADMIN_USER_ID, on_delete=models.SET_DEFAULT
    )

    title = models.CharField(
        max_length=150, help_text='Name of the source', db_index=True
    )
    web_url = models.URLField(
        db_index=True, max_length=2000, unique=True,
        help_text="An URL of a web-site"
    )
    base_url = models.URLField(
        db_index=True, max_length=2000, null=True, blank=True, help_text=(
            "Base URL of the given web_url, it is useful to resolve the "
            "relative resourcing loading from the web_url. For example, if the"
            " web_url are 'https://www.pwc.com/us/en/services/alliances.html' "
            "and 'https://comintelli.com/ir/' then the base_url will be "
            "'https://www.pwc.com' and 'https://comintelli.com'."
        )
    )
    domain = models.CharField(
        max_length=250, null=True, blank=True, db_index=True
    )
    state = models.PositiveSmallIntegerField(
        choices=get_choices(wt_enum.State), db_index=True,
        default=wt_enum.State.ACTIVE.value
    )
    comment = models.TextField(blank=True, null=True)

    frequency = models.PositiveSmallIntegerField(
        choices=get_choices(wt_enum.RunTimeFrequency), db_index=True,
        default=wt_enum.RunTimeFrequency.EVERY_24_HOURS.value
    )
    junk_xpaths = ArrayField(models.CharField(max_length=250), null=True, blank=True)
    accept_cookie_xpaths = ArrayField(
        models.TextField(),
        null=True,
        blank=True,
    )
    pyppeteer_networkidle = models.IntegerField(
        null=True, blank=True,
        help_text="Used to store count for how many network calls can be "
                 "ignored to "
             "assume that page has been loaded fully in pyppeteer. If negative "
                  "then dont"
             "pass this param while requesting"
    )
    last_run = models.DateTimeField(db_index=True, null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)

    created_on = models.DateTimeField(db_index=True, auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)

    content_source = models.ForeignKey(
        'penseive.ContentSource', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='ws_cs'
    )
    published_by_company = models.ForeignKey(
        'penseive.PublishedByCompany', null=True, blank=True,
        related_name="ws_pbc", on_delete=models.SET_NULL,
    )
    screenshot_sleep_time = models.PositiveIntegerField(blank=True, null=True, db_index=True)

    class Meta:
        pass
        # app_label = "contify.website_tracking"

    def __str__(self):
        return '{}'.format(self.title)

    def save(self, *args, **kwargs):
        if not self.domain:
            td = tldextract.extract(self.base_url or self.web_url)
            self.domain = td.domain
        if not self.base_url:
            td = tldextract.extract(self.web_url)
            up = urlparse(self.web_url)
            self.base_url = "{}://{}".format(up.scheme, td.fqdn)
        super().save(*args, **kwargs)


class WebClientSource(models.Model):
    """
    This holds the extra information of source w.r.t client ie. the
    information of source which is dependent on the client, like source tags.
    """
    client = models.ForeignKey(CompanyPreferences, on_delete=models.DO_NOTHING)
    source = models.ForeignKey("WebSource", on_delete=models.CASCADE)

    created_by = models.ForeignKey(
        User, related_name='created_wt_client_source_set',
        default=SYSTEM_ADMIN_USER_ID, on_delete=models.SET_DEFAULT
    )
    updated_by = models.ForeignKey(
        User, related_name='updated_wt_client_source_set',
        default=SYSTEM_ADMIN_USER_ID, on_delete=models.SET_DEFAULT
    )

    state = models.PositiveSmallIntegerField(
        choices=get_choices(wt_enum.State), db_index=True,
        default=wt_enum.State.ACTIVE.value
    )
    # Tags:
    content_type = models.ForeignKey(
        'penseive.ContentType', blank=True, null=True,
        on_delete=models.SET_NULL
    )

    locations = models.ManyToManyField('penseive.Location', blank=True)
    companies = models.ManyToManyField('penseive.Company', blank=True)
    industries = models.ManyToManyField('penseive.Industry', blank=True)
    topics = models.ManyToManyField('penseive.Topic', blank=True)
    business_events = models.ManyToManyField('penseive.BusinessEvent', blank=True)
    themes = models.ManyToManyField('penseive.Theme', blank=True)
    custom_tags = models.ManyToManyField('penseive.CustomTag', blank=True)
    language = models.CharField(
        max_length=50, choices=SUPPORTED_LANGUAGES, null=True, blank=True
    )

    created_on = models.DateTimeField(db_index=True, auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('client', 'source')

    def __str__(self):
        return f"{self.client}-{self.source}"


class WebUpdate(models.Model):
    """
    It will have final web-content updates, just like the story.
    """
    def old_image_upload_path(instance, filename):
        fn, ext = os.path.splitext(filename)
        return "web_track/images/web_update/old_image/{}-{}{}".format(
            fn, instance.pk, ext
        )

    def new_image_upload_path(instance, filename):
        fn, ext = os.path.splitext(filename)
        return "web_track/images/web_update/new_image/{}-{}{}".format(
            fn, instance.pk, ext
        )

    client = models.ForeignKey(CompanyPreferences, on_delete=models.DO_NOTHING)
    web_source = models.ForeignKey(
        WebSource, on_delete=models.SET_NULL, null=True, blank=True
    )
    manual_copy_of = models.ForeignKey(
        "self", blank=True, null=True, on_delete=models.SET_NULL
    )

    # Users interaction with WebUpdate
    created_by = models.ForeignKey(
        User, related_name='created_wt_update_set',
        default=SYSTEM_ADMIN_USER_ID, on_delete=models.SET_DEFAULT
    )
    updated_by = models.ForeignKey(
        User, related_name='updated_wt_update_set',
        default=SYSTEM_ADMIN_USER_ID, on_delete=models.SET_DEFAULT
    )
    approved_by = models.ForeignKey(
        User, related_name='approved_wt_update_set', null=True, blank=True,
        on_delete=models.SET_NULL
    )

    # Tags
    source = models.ForeignKey(
        'penseive.ContentSource', null=True, blank=True,
        on_delete=models.SET_NULL
    )
    rating = models.ForeignKey(
        'penseive.Rating', blank=True, null=True, on_delete=models.SET_NULL
    )
    content_type = models.ForeignKey(
        'penseive.ContentType', blank=True, null=True,
        on_delete=models.SET_NULL
    )
    published_by_company = models.ForeignKey(
        'penseive.PublishedByCompany', null=True, blank=True,
        related_name="wt_update_pbc", on_delete=models.SET_NULL
    )
    locations = models.ManyToManyField('penseive.Location', blank=True, related_name='web_update_by_location')
    source_locations = models.ManyToManyField('penseive.Location', blank=True, related_name='web_update_by_source_location')
    companies_hq_locations = models.ManyToManyField('penseive.Location', blank=True, related_name='web_update_by_companies_hq_location')
    companies = models.ManyToManyField('penseive.Company', blank=True)
    persons = models.ManyToManyField("penseive.Person", blank=True)
    industries = models.ManyToManyField('penseive.Industry', blank=True)
    topics = models.ManyToManyField('penseive.Topic', blank=True)
    business_events = models.ManyToManyField('penseive.BusinessEvent', blank=True)
    themes = models.ManyToManyField('penseive.Theme', blank=True)
    custom_tags = models.ManyToManyField('penseive.CustomTag', blank=True)
    language = models.CharField(
        db_index=True, max_length=50, choices=settings.LANGUAGES,
        null=True, blank=True,
    )

    diff_content_id = models.PositiveIntegerField(
        db_index=True, null=True, blank=True
    )

    # Regular Fields
    hash = models.TextField(
        db_index=True, help_text="hash value of the title and description",
    )
    status = models.IntegerField(
        db_index=True, choices=get_choices(StoryStatus),
        default=StoryStatus.DRAFT.value
    )
    title = models.TextField(
        db_index=True, help_text="Tile of the Story in English Language"
    )
    description = models.TextField(
        blank=True, help_text="Body of the Story in English Language"
    )
    old_image = models.ImageField(
        storage=get_storage(), upload_to=old_image_upload_path,
        blank=True, null=True
    )
    new_image = models.ImageField(
        storage=get_storage(), upload_to=new_image_upload_path,
        blank=True, null=True
    )
    email_priority = models.IntegerField(
        db_index=True, choices=get_choices(EmailPriority),
        default=EmailPriority.KEEP_IN_EMAIL_ALERT.value
    )
    snippet_info = JSONField(
        null=True, blank=True, encoder=DjangoJSONEncoder,
        help_text=(
            "It holds information about the screenshot snippet that will be "
            "visible in the newsFeed list page."
        )
    )

    generic_data_list = ArrayField(
        models.IntegerField(), null=True, blank=True
    )
    generic_data_json = JSONField(
        null=True, blank=True, encoder=DjangoJSONEncoder,
        help_text="miscellaneous data point"
    )

    updated_on = models.DateTimeField(auto_now=True)
    approved_on = models.DateTimeField(db_index=True, null=True, blank=True)
    created_on = models.DateTimeField(db_index=True, auto_now_add=True)
    user_updated_on = models.DateTimeField(db_index=True, null=True, blank=True)

    class Meta:
        unique_together = ("client", "hash")

    def __str__(self):
        return f"{self.title}"

    def save(self, *args, **kwargs):
        if kwargs.get('direct_save'):
            kwargs.pop("direct_save")
            temp_kwargs = copy.deepcopy(kwargs)
            super(WebUpdate, self).save(**temp_kwargs)
            return

        if not self.language and (self.description or self.title):
            try:
                self.language = (
                    detect_language(self.description) or
                    detect_language(self.title)
                )
            except Exception as e:
                logger.warning(
                    'Failed to detect language. Setting English: en. error: '
                    '{}, title: {}'.format(
                        e, getattr(self, 'title', 'Unknown')
                    )
                )
                self.language = 'en'
        super(WebUpdate, self).save()

    def get_redirecting_url(self, subscriber_id=''):
        return encode_change_log_url(self.id)

    def get_lead(self, gen_lead=False, gen_display_lead=True):
        return self.description

