

import os

from django.contrib.postgres.fields import JSONField
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from contify.cutils.utils import get_choices
from contify.website_tracking import cfy_enum as wt_enum
from contify.website_tracking.utils import get_storage


class WebSnapshot(models.Model):
    """
    It is just like the VCS for the web-content of an URL.

    It keeps the track of fetched raw html and the screenshot of the
    fetched raw html. Screenshot may be different if we generate it later
    from the downloaded raw html and the external link has been changed
    that is why generating raw screenshot just after downloading the raw html.
    """
    def image_upload_path(instance, filename):
        fn, ext = os.path.splitext(filename)
        return "web_track/images/raw_snapshot/{}_{}{}".format(
            fn, instance.web_source_id, ext
        )

    # web_source = models.ForeignKey(
    #     WebSource, related_name="wt_client_source_set",
    #     on_delete=models.CASCADE
    # )
    web_source_id = models.PositiveSmallIntegerField(db_index=True)

    state = models.PositiveSmallIntegerField(
        choices=get_choices(wt_enum.State), db_index=True,
        default=wt_enum.State.ACTIVE.value
    )
    status = models.PositiveSmallIntegerField(
        choices=get_choices(wt_enum.SnapshotStatus), db_index=True,
        default=wt_enum.SnapshotStatus.DRAFT.value
    )

    hash_html = models.TextField(
        db_index=True, unique=True,
        help_text="hash of the raw_html and used for uniqueness"
    )
    raw_html = models.TextField(help_text="The web content of the URL")
    raw_snapshot = models.ImageField(
        storage=get_storage(), upload_to=image_upload_path,
        blank=True, null=True
    )

    last_error = models.TextField(null=True, blank=True)

    created_on = models.DateTimeField(db_index=True, auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        # app_label = "web_snapshot"
        pass
        # constraints = [
        #     models.UniqueConstraint(
        #         fields=["web_source"],
        #         condition=models.Q(status=wt_enum.SnapshotStatus.RECENT.value),
        #         name="%(app_label)s_%(class)s_unique_recent_web_source"
        #     )
        # ]

    def __str__(self):
        return f"{self.hash_html}"


class DiffHtml(models.Model):
    """
    It holds the diff-html that is difference of html calculated from old
    WebSnapshot and new WebSnapshot. From here, we generate the screenshots of
    diff-html and create a record in the DiffContent table with  a DiffHtml
    and generated screenshot.

    We have created this table to optimize the process as generating diff-html,
    and taking their screenshots take much time that is why we have split the
    process in 2 parts, one generates the diff-html and other one generates
    the screenshots of generated diff-html.
    """
    old_web_snapshot_id = models.PositiveIntegerField(
        null=True, blank=True, db_index=True
    )
    old_diff_html = models.TextField(null=True, blank=True)
    removed_diff_info = JSONField(encoder=DjangoJSONEncoder, default=dict)

    new_web_snapshot_id = models.PositiveIntegerField(
        null=True, blank=True, db_index=True, unique=True
    )
    new_diff_html = models.TextField(null=True, blank=True)
    added_diff_info = JSONField(encoder=DjangoJSONEncoder, default=dict)

    state = models.PositiveSmallIntegerField(
        choices=get_choices(wt_enum.State), db_index=True,
        default=wt_enum.State.ACTIVE.value
    )
    status = models.PositiveSmallIntegerField(
        choices=get_choices(wt_enum.DiffHtmlStatus), db_index=True,
        default=wt_enum.DiffHtmlStatus.DRAFT.value
    )

    last_error = models.TextField(null=True, blank=True)

    created_on = models.DateTimeField(db_index=True, auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        #app_label = "web_snapshot"
        pass

    def __str__(self):
        return (
            f"Added: {self.added_diff_info}\nRemoved: {self.removed_diff_info}"
        )


class DiffContent(models.Model):
    """
    It keeps the track of diff html (old and new) and their screenshots and
    the final web update will be created from the diff content by the
    ops members.

    Keys of diff_info must be the members of DiffKeyMap enum, and value of key
    will be list type. An example of this will be like below:

        added_diff_info = {
            T: [
                "A new section has been added",
                "A new key members has been added",
            ],
            I: [
                "New Image source URL 1",
                "New Image source URL 2"
            ],
            L: [
                "A new href of a tag"
            ]
        }

        removed_diff_info = {
            T: [
                "A old section has been added",
                "A old key members has been added",
            ],
            I: [
                "Old Image source URL 1",
                "old Image source URL 2"
            ],
            L: [
                "A old href of a tag"
            ]
        }
    """
    def old_image_upload_path(instance, filename):
        fn, ext = os.path.splitext(filename)
        return "web_track/images/diff_content/old_image/{}_{}{}".format(
            fn, instance.old_snapshot, ext
        )

    def new_image_upload_path(instance, filename):
        fn, ext = os.path.splitext(filename)
        return "web_track/images/diff_content/new_image/{}_{}{}".format(
            fn, instance.new_snapshot, ext
        )

    # Left / Old diff details
    old_snapshot = models.ForeignKey(
        WebSnapshot, related_name="wt_left_snapshot_set",
        on_delete=models.DO_NOTHING, null=True, blank=True
    )
    old_diff_html = models.TextField(
        help_text="Left diff Image is generated by this",
        null=True, blank=True, # default=DEFAULT_OLD_HTML
    )
    old_diff_image = models.ImageField(
        storage=get_storage(), upload_to=old_image_upload_path,
        blank=True, null=True
    )

    # Right / New diff html
    new_snapshot = models.OneToOneField(
        WebSnapshot, related_name="wt_right_snapshot_set",
        on_delete=models.DO_NOTHING, unique=True
    )
    new_diff_html = models.TextField(
        help_text="Right diff image is generated from this"
    )
    new_diff_image = models.ImageField(
        storage=get_storage(), upload_to=new_image_upload_path,
        blank=True, null=True
    )

    state = models.PositiveSmallIntegerField(
        choices=get_choices(wt_enum.State), db_index=True,
        default=wt_enum.State.ACTIVE.value
    )
    status = models.PositiveSmallIntegerField(
        choices=get_choices(wt_enum.DiffStatus), db_index=True,
        default=wt_enum.DiffStatus.PENDING.value
    )
    added_diff_info = JSONField(encoder=DjangoJSONEncoder, default=dict)
    removed_diff_info = JSONField(encoder=DjangoJSONEncoder, default=dict)

    created_on = models.DateTimeField(db_index=True, auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        # app_label = "web_snapshot"
        constraints = [
            models.UniqueConstraint(
                fields=["old_snapshot"],
                condition=models.Q(old_snapshot__isnull=True),
                name="%(app_label)s_%(class)s_unique_null_old_snapshot"
            )
        ]

    def __str__(self):
        return f"{self.added_diff_info}\n{self.removed_diff_info}"

    def save(self, *args, **kwargs):
        # source_id of old_snapshot and new_snapshot must be same
        if self.old_snapshot_id and self.old_snapshot.web_source_id != self.new_snapshot.web_source_id:
            raise ValueError(
                "WebSource ID of old_snapshot and new_snapshot are different, "
                "it has to be same."
            )

        super().save(*args, **kwargs)
        return
