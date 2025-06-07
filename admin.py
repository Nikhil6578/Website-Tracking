
import json
import logging
import traceback
from datetime import datetime

from django.db import models
from django.contrib import admin
from django.contrib.postgres.fields import ArrayField, JSONField
from django.core.files.base import ContentFile
from django.forms import TextInput, Textarea
from django.utils.safestring import mark_safe
from django.urls import re_path

from config.constants import WEB_UPDATE_ADMIN_URL, WEBSITE_TRACKING_GROUP_ID
from contify.cutils.cfy_enum import StoryStatus
from contify.story.utils import get_content_source
from contify.website_tracking import admin_filters
from contify.website_tracking import constants as ws_constants
from contify.website_tracking.autocomplete import (
    AutocompleteStackedInline, AutocompleteModelAdmin
)
from contify.website_tracking.cfy_admin_conf import cfy_admin_site, EXTRA_HEAD
from contify.website_tracking.cfy_enum import DiffStatus
from contify.website_tracking.change_log import (
    single_story_admin_update_journal
)
from contify.website_tracking.forms import WebSourceAdminForm
from contify.website_tracking.models import (
    WebSource, WebClientSource, WebUpdate
)
from contify.website_tracking.web_snapshot.models import (
    WebSnapshot, DiffContent, DiffHtml
)
from contify.website_tracking.constants import (
    WST_ADMIN_PREFIX_URL, FF_OLD_DIFF_IMAGE_URL, WST_PATH, TAGS_MULTI_FIELDS
)
from contify.website_tracking.service import get_md5_hash_of_string
from contify.website_tracking.utils import get_diff_info_html


logger = logging.getLogger(__name__)


def has_website_tracking_access(request):
    user_has_wst_access = (
        request.user.groups.filter(id=WEBSITE_TRACKING_GROUP_ID).exists()
    )
    if not user_has_wst_access:
        logger.info(
            f'User: {request.user.username}, do not have access to website tracking group. '
            f'Group Id: {WEBSITE_TRACKING_GROUP_ID}'
        )
    return user_has_wst_access


class Media:
    js = (
        'jquery-ui/jquery-ui.min.js',
    )
    css = {
        'all': (
            'jquery-ui/jquery-ui.min.css',
            'jquery-ui/jquery-ui.structure.min.css',
            'jquery-ui/jquery-ui.theme.min.css',
        )
    }


class DiffContentAdmin(admin.ModelAdmin):
    class Media:
        js = EXTRA_HEAD["js"]
        css = {'all': EXTRA_HEAD["css"]}

    list_select_related = True

    list_display_links = None

    # change_list_template = "wst_admin/diff_context_change_list.html"

    search_fields = ["added_diff_info", "removed_diff_info"]

    actions = ['make_rejected']

    list_display = (
        "_source_info", "_added_diff_info", "_removed_diff_info", "status",
        "created_on"
    )

    list_filter = (
        "status", "created_on", "updated_on",
        admin_filters.DiffContentClientFilter,
        admin_filters.DiffContentWebSourceFilter
    )

    fieldsets = (
        (
            None, {
                "fields": (
                    ("old_snapshot", "new_snapshot", "state"),
                    ("_added_diff_info", "_removed_diff_info"),
                    # ("old_diff_html", "new_diff_html"),
                    ("created_on", "updated_on"),
                    ("_view_diff_html", "_view_raw_html"),
                    ("old_diff_image", "new_diff_image", ),
                    ("_diff_image", ),
                )
            }
        ),
    )

    readonly_fields = (
        "created_on", "updated_on", "_diff_image", "_added_diff_info",
        "_removed_diff_info", "_view_diff_html", "_view_raw_html", "_source_info"
    )


    def lookup_allowed(self, key, value):
        """
        To bypass the security check altogether,
        effectively stating that all lookups are allowed.
        """
        return True

    def has_view_or_change_permission(self, request, obj=None):
        return has_website_tracking_access(request)

    def make_rejected(self, request, queryset):
        queryset.update(status=DiffStatus.REJECT.value)
    make_rejected.short_description = "Mark selected DiffContent as Rejected"

    def _view_diff_html(self, obj):
        return mark_safe(
            f"<a href='{ws_constants.WST_ADMIN_PREFIX_URL}/{obj.id}/"
            f"diff-html'>View Diff HTML</a>"
        )
    _view_diff_html.allow_tags = True

    def _view_raw_html(self, obj):
        return mark_safe(
            f"<a href='{ws_constants.WST_ADMIN_PREFIX_URL}/{obj.id}/raw-html'>"
            f"View Raw HTML</a>"
        )
    _view_raw_html.allow_tags = True

    def _diff_image(self, obj):
        return mark_safe(f"""
            <div style="display:flex;">
                <img src={obj.old_diff_image.url} style="width:50%; height:100%;" />
                <img src={obj.new_diff_image.url} style="width:50%; height:100%;" />
            </div>
        """)
    _diff_image.allow_tags = True

    def _added_diff_info(self, obj):
        base_url = ""
        if obj.new_snapshot and obj.new_snapshot.web_source_id:
            try:
                ws = WebSource.objects.get(id=obj.new_snapshot.web_source_id)
                base_url = ws.base_url
            except WebSource.DoesNotExist as e:
                pass

        diff_added_html, m_l_btn = get_diff_info_html(
            obj, "added", base_url, True
        )
        tmp_tag_list = ["<div class='diff-info-container'>"]

        if diff_added_html.strip():
            # tmp_tag_list.append('<label for="added">Added:</label>')
            tmp_tag_list.append(
                "<p class='new-diff-p'>{}</p>".format(diff_added_html)
            )

        tmp_tag_list.append("</div>")
        tmp_tag_list.append(m_l_btn)
        return mark_safe(" ".join(tmp_tag_list))
    _added_diff_info.allow_tags = True

    def _removed_diff_info(self, obj):
        base_url = ""
        if obj.new_snapshot and obj.new_snapshot.web_source_id:
            try:
                ws = WebSource.objects.get(id=obj.new_snapshot.web_source_id)
                base_url = ws.base_url
            except WebSource.DoesNotExist as e:
                pass

        diff_removed_html, m_l_btn = get_diff_info_html(
            obj, "removed", base_url, True
        )
        tmp_tag_list = ["<div class='diff-info-container'>"]

        if diff_removed_html.strip():
            # tmp_tag_list.append('<label for="removed">Removed:</label>')
            tmp_tag_list.append(
                "<p class='old-diff-p'>{}</p>".format(diff_removed_html)
            )

        tmp_tag_list.append("</div>")
        tmp_tag_list.append(m_l_btn)
        return mark_safe(" ".join(tmp_tag_list))
    _removed_diff_info.allow_tags = True

    def _source_info(self, obj):
        client_list = list(
            WebClientSource.objects
            .filter(source_id=obj.new_snapshot.web_source_id)
            .values_list("id", "client__company__name")
        )
        tmp_c = []
        for i, c_info in enumerate(client_list):
            c_id, c_name = c_info
            prefix_str = ""

            if len(client_list) > 1 and len(client_list) - 1 == i:
                prefix_str = ", and "
            elif i != 0:
                prefix_str = ", "

            tmp_c.append("""
                {}<a href="{}/admin/website_tracking/webclientsource/{}"
                  class="client-name" target="_blank">
                    <span class="web-client-name" title="Client">{}</span>
                  </a>
            """.format(prefix_str, WST_ADMIN_PREFIX_URL, c_id, c_name))

        if len(client_list) == 0:
            client_html = "not any client"
        else:
            client_html = " ".join(tmp_c)
            client_html += " client" if len(client_list) == 1 else " clients"

        ws = WebSource.objects.get(id=obj.new_snapshot.web_source_id)

        tmp_html = """
            <div class="source-info-container">
              <p style="font-weight: 600;">
                An update from
                <span class="diff-source-name" title="WebSource">
                  <a href="{0}" target="_blank" class="web-url">{1}</a>
                </span>
                for {2}.
              </p>
              <p>
                <a title="WebSource Admin" href="{6}website_tracking/websource/{5}" target="_blank" class="web-source">
                  View WebSource
                </a>
              </p>
            </div>

            <div class="curation-link">
              <p>
                <a href="{4}/{3}/curation/" onclick="return popItUp('{4}/{3}/curation/')" class="preview_a_btn">
                  Create Web Update
                </a>
              </p>

              <div class="diff-other-links">
                <span class="diff-raw-svg">
                  <a href="{4}/{3}/raw-html" target="_blank" class="a-svg-link" title="Raw HTML">
                    <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" aria-hidden="true" focusable="false" width="3em" height="5em" style="-ms-transform: rotate(360deg); -webkit-transform: rotate(360deg); transform: rotate(360deg);" preserveAspectRatio="xMidYMid meet" viewBox="0 0 24 24">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 2l5 5h-5V4zM8.531 18h-.76v-1.411H6.515V18h-.767v-3.373h.767v1.296h1.257v-1.296h.76V18zm3-2.732h-.921V18h-.766v-2.732h-.905v-.641h2.592v.641zM14.818 18l-.05-1.291c-.017-.405-.03-.896-.03-1.387h-.016c-.104.431-.245.911-.375 1.307l-.41 1.316h-.597l-.359-1.307a15.154 15.154 0 0 1-.306-1.316h-.011c-.021.456-.034.976-.059 1.396L12.545 18h-.705l.216-3.373h1.015l.331 1.126c.104.391.21.811.284 1.206h.017c.095-.391.209-.836.32-1.211l.359-1.121h.996L15.563 18h-.745zm3.434 0h-2.108v-3.373h.767v2.732h1.342V18z" fill="#626262"/>
                    </svg>
                  </a>
                </span>

                <span class="diff-raw-svg">
                  <a href="{4}/{3}/raw-screenshot/" target="_blank" class="a-svg-link" title="Raw Screenshot">
                    <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" aria-hidden="true" focusable="false" width="3em" height="5em" style="-ms-transform: rotate(360deg); -webkit-transform: rotate(360deg); transform: rotate(360deg);" preserveAspectRatio="xMidYMid meet" viewBox="0 0 36 36">
                      <path d="M32.12 10H3.88A1.88 1.88 0 0 0 2 11.88v18.24A1.88 1.88 0 0 0 3.88 32h28.24A1.88 1.88 0 0 0 34 30.12V11.88A1.88 1.88 0 0 0 32.12 10zM32 30H4V12h28z" class="clr-i-outline clr-i-outline-path-1" fill="#626262"/><path d="M8.56 19.45a3 3 0 1 0-3-3a3 3 0 0 0 3 3zm0-4.6A1.6 1.6 0 1 1 7 16.45a1.6 1.6 0 0 1 1.56-1.6z" class="clr-i-outline clr-i-outline-path-2" fill="#626262"/>
                      <path d="M7.9 28l6-6l3.18 3.18L14.26 28h2l7.46-7.46L30 26.77v-2L24.2 19a.71.71 0 0 0-1 0l-5.16 5.16l-3.67-3.66a.71.71 0 0 0-1 0L5.92 28z" class="clr-i-outline clr-i-outline-path-3" fill="#626262"/><path d="M30.14 3a1 1 0 0 0-1-1h-22a1 1 0 0 0-1 1v1h24z" class="clr-i-outline clr-i-outline-path-4" fill="#626262"/><path d="M32.12 7a1 1 0 0 0-1-1h-26a1 1 0 0 0-1 1v1h28z" class="clr-i-outline clr-i-outline-path-5" fill="#626262"/>
                    </svg>
                  </a>
                </span>

                <span class="diff-raw-svg">
                  <a href="{4}/{3}/diff-html" target="_blank" class="a-svg-link" title="Diff HTML">
                    <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" aria-hidden="true" focusable="false" width="3em" height="5em" style="-ms-transform: rotate(360deg); -webkit-transform: rotate(360deg); transform: rotate(360deg);" preserveAspectRatio="xMidYMid meet" viewBox="0 0 32 32">
                      <path d="M16.02 16.945l-.529 1.64h-.788l1.317-4.107V7.945h7.344l-1.397 15.39l-5.947 1.602zM16 32C7.163 32 0 24.837 0 16S7.163 0 16 0s16 7.163 16 16s-7.163 16-16 16zm-7.364-7.531L15.98 26.5l7.384-2.031L25 6.5H7zm5.163-6.793l-3.526-1.432v-.592l3.526-1.433v.742l-2.469.99l2.47.984zm7.933-1.432v-.592l-3.527-1.433v.742l2.47.987l-2.47.987v.741zm-5.712.7l1.1-3.413h-.796l-.304.947z" fill-rule="evenodd" fill="#626262"/>
                    </svg>
                  </a>
                </span>

                <span class="diff-raw-svg">
                  <a href="{4}/{3}/diff-screenshot/" target="_blank" class="a-svg-link" title="Diff Screenshot">
                    <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" aria-hidden="true" focusable="false" width="3em" height="5em" style="-ms-transform: rotate(360deg); -webkit-transform: rotate(360deg); transform: rotate(360deg);" preserveAspectRatio="xMidYMid meet" viewBox="0 0 36 36">
                      <path d="M30.14 3a1 1 0 0 0-1-1h-22a1 1 0 0 0-1 1v1h24z" class="clr-i-solid clr-i-solid-path-1" fill="#626262"/><path d="M32.12 7a1 1 0 0 0-1-1h-26a1 1 0 0 0-1 1v1h28z" class="clr-i-solid clr-i-solid-path-2" fill="#626262"/><path d="M32.12 10H3.88A1.88 1.88 0 0 0 2 11.88v18.24A1.88 1.88 0 0 0 3.88 32h28.24A1.88 1.88 0 0 0 34 30.12V11.88A1.88 1.88 0 0 0 32.12 10zM8.56 13.45a3 3 0 1 1-3 3a3 3 0 0 1 3-3zM30 28H6l7.46-7.47a.71.71 0 0 1 1 0l3.68 3.68L23.21 19a.71.71 0 0 1 1 0L30 24.79z" class="clr-i-solid clr-i-solid-path-3" fill="#626262"/>
                    </svg>
                  </a>
                </span>
              </div>
            </div>
        """.format(
            ws.web_url, ws.title, client_html, obj.id,
            ws_constants.WST_ADMIN_PREFIX_URL, ws.id, WEB_UPDATE_ADMIN_URL
        )
        return mark_safe(tmp_html)
    _source_info.allow_tags = True

    def changelist_view(self, request, extra_context=None):
        """
        To apply pending status by default.
        """
        if "status__exact" not in request.GET:
            q = request.GET.copy()
            q['status__exact'] = '0'
            request.GET = q
            request.META['QUERY_STRING'] = request.GET.urlencode()
        return super(DiffContentAdmin, self).changelist_view(
            request, extra_context=extra_context
        )


class WebClientSourceAdmin(AutocompleteModelAdmin):
    """
    Admin Class for corresponding to RssIntegrationStatus Model
    """
    # change_form_template = "wst_admin/change_form.html"
    class Media:
        js = EXTRA_HEAD["js"]
        css = {'all': EXTRA_HEAD["css"]}

    model = WebClientSource

    list_select_related = True

    save_on_top = True

    search_fields = ("client", )

    list_display = ("client", "source", "state", "created_on", "updated_on")

    list_filter = ("state", "created_on", "updated_on")

    fieldsets = (
        (
            None, {
                "fields": (
                    ("client", "source", "state"),
                    ("created_by", "updated_by"),
                    ("created_on", "updated_on")
                )
            },
        ),
        (
            "Tags", {
                "fields": (
                    ("language", ),
                    ("content_type", ),
                    ("locations", ),
                    ("companies", ),
                    ("industries", ),
                    ("topics", ),
                    ("business_events",),
                    ("themes",),
                    ("custom_tags", )
                )
            }
        )
    )

    readonly_fields = ("created_on", "updated_on", "created_by", "updated_by")

    # exclude = ['created_by', ]

    related_search_fields = {
        "client": {
            "search_fields": ["company__name", ], "show_item_admin_link": True
        },
        "source": {
            "search_fields": ["title", ], "show_item_admin_link": True
        },
        "content_source": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "content_type": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "published_by_company": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "locations": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "companies": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "industries": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "topics": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "business_events": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "themes": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "custom_tags": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
    }

    def has_view_or_change_permission(self, request, obj=None):
        return has_website_tracking_access(request)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user

        obj.save()


class WebSnapshotAdmin(admin.ModelAdmin):
    class Media:
        js = EXTRA_HEAD["js"]
        css = {'all': EXTRA_HEAD["css"]}

    list_select_related = True

    save_on_top = True

    search_fields = ["hash_html"]

    list_display = (
        "id", "hash_html", "_web_source", "status", "state", "created_on"
    )

    list_filter = ("status", "created_on", "updated_on")

    fieldsets = (
        (
            None, {
                "fields": (
                    ("web_source_id", "hash_html"),
                    ("status", "state"),
                    ("raw_html", ),
                    ("created_on", "updated_on"),
                    ("last_error", ),
                    ("raw_snapshot", "_raw_snapshot", ),
                )
            }
        ),
    )

    readonly_fields = (
        "created_on", "updated_on", "_raw_snapshot", "_web_source",
        "last_error"
    )

    def has_view_or_change_permission(self, request, obj=None):
        return has_website_tracking_access(request)

    def _raw_snapshot(self, obj):
        return mark_safe('<img src="{}"/>'.format(obj.raw_snapshot.url))
    _raw_snapshot.allow_tags = True

    def _web_source(self, obj):
        try:
            ws = WebSource.objects.get(id=obj.web_source_id)
            return mark_safe("<p>{}</p>".format(ws.title))
        except Exception as e:
            return mark_safe("<p>WebSource Not Found</p>")
    _web_source.allow_tags = True


class DiffHtmlAdmin(admin.ModelAdmin):

    list_display = (
        "id", "old_web_snapshot_id", "new_web_snapshot_id", "status", "state",
        "created_on"
    )

    list_filter = ("state", "status", "created_on", "updated_on")

    fieldsets = (
        (
            None, {
                "fields": (
                    ("old_web_snapshot_id", "new_web_snapshot_id"),
                    ("status", "state"),
                    ("created_on", "updated_on"),
                    ("last_error", ),
                    ("old_diff_html", "new_diff_html"),
                    ("removed_diff_info", "added_diff_info")
                )
            }
        ),
    )

    readonly_fields = ("created_on", "updated_on", "last_error")

    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 35, 'cols': 190})},
        JSONField: {'widget': Textarea(attrs={'rows': 30, 'cols': 90})},
    }

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return False

    def has_view_or_change_permission(self, request, obj=None):
        return has_website_tracking_access(request)


class ClientSourceTagInline(AutocompleteStackedInline):
    extra = 1

    model = WebClientSource

    fieldsets = (
        (
            None, {
                "fields": (
                    ("client", "state"),
                    ("created_by", "updated_by", "created_on", "updated_on")
                )
            },
        ),
        (
            "Tags", {
                "fields": (
                    ("language", ),
                    ("content_type", ),
                    ("locations", ),
                    ("companies", ),
                    ("industries", ),
                    ("topics", ),
                    ("business_events",),
                    ("themes",),
                    ("custom_tags", )
                ),
                # 'classes': ('collapse',),
            }
        )
    )

    readonly_fields = ("created_on", "updated_on", "created_by", "updated_by")

    related_search_fields = {
        "client": {
            "search_fields": ["company__name", ], "show_item_admin_link": True
        },
        "content_type": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "locations": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "companies": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "industries": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "topics": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "business_events": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "themes": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "custom_tags": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
    }

    def has_view_or_change_permission(self, request, obj=None):
        return has_website_tracking_access(request)

    def get_queryset(self, request):
        queryset = super().get_queryset(request).prefetch_related(
            "locations", "companies", "industries", "topics", "business_events",
            "themes", "custom_tags"
        ).select_related(
            "client", "created_by",
            "updated_by"
        )
        return queryset


class WebSourceAdmin(AutocompleteModelAdmin):
    class Media:
        js = EXTRA_HEAD["js"]
        css = {'all': EXTRA_HEAD["css"]}

    change_form_template = "wst_admin/websource/change_form.html"
    form = WebSourceAdminForm
    save_on_top = True

    inlines = [ClientSourceTagInline]

    search_fields = ['title', 'web_url']

    list_display = (
        'title', 'web_url', 'state', 'frequency', 'created_by', "last_run",
        'created_on'
    )

    list_filter = (
        'state', 'frequency', 'last_run', admin_filters.SourceClientFilter
    )

    fieldsets = (
        (
            None, {
                'fields': (
                    ('title', 'state', 'frequency'),
                    ('web_url', 'base_url', 'domain'),
                    ('comment', ),
                    ('junk_xpaths', 'accept_cookie_xpaths'),
                    ('pyppeteer_networkidle', 'screenshot_sleep_time'),
                    ('created_by', 'updated_by', 'created_on', 'updated_on'),
                    ("last_run", "last_error"),
                    ("published_by_company", ),
                    ("content_source", ),
                )
            }
        ),
    )

    readonly_fields = (
        'created_on', 'updated_on', "created_by", "updated_by", "last_error"
    )

    related_search_fields = {
        "published_by_company": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "content_source": {
            "search_fields": ["name", ], "show_item_admin_link": True
        },
    }

    formfield_overrides = {
        ArrayField: {'widget': TextInput(attrs={'size': '80'})}
    }
    def has_view_or_change_permission(self, request, obj=None):
        return has_website_tracking_access(request)

    def get_urls(self):
        default_urls = super(WebSourceAdmin, self).get_urls()
        custom_urls = [
            re_path(
                r'^search/$', self.admin_site.admin_view(
                    AutocompleteModelAdmin.search
                )
            )
        ]
        return custom_urls + default_urls

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user

        cs_obj, _ = get_content_source(obj.web_url)
        obj.content_source = cs_obj
        obj.published_by_company = cs_obj.published_by_company

        obj.save()


class WebUpdateAdmin(AutocompleteModelAdmin):
    class Media:
        js = EXTRA_HEAD["js"]
        css = {'all': EXTRA_HEAD["css"]}

    change_form_template = "wst_admin/webupdate/change_form.html"
    change_list_template = "wst_admin/webupdate/change_list.html"

    model = WebUpdate

    list_select_related = True

    save_on_top = True

    search_fields = ["title", "description"]

    list_display = (
        "title", "_description", "status", "_client",
        "approved_by", "created_by", "approved_on", "created_on"
    )

    list_filter = (
        "status", "approved_on", "created_on", admin_filters.ClientFilter
    )

    fieldsets = (
        (
            None, {
                "fields": (
                    # ("hash", ),
                    ("title", "hash"),
                    ("description", )
                )
            }
        ),
        (
            "Advanced options", {
                # 'classes': ('collapse',),
                "fields": (
                    ("client", ),
                    ("source", ),
                    ("published_by_company", ),
                    ("content_type", ),

                    ("companies", ),
                    ("locations",),
                    ("source_locations",),
                    ("companies_hq_locations",),
                    ("industries",),
                    ("topics",),
                    ("business_events",),
                    ("themes",),
                    ("custom_tags",),
                    ("persons",),

                    ("rating", ),
                    ("language", ),
                    ("email_priority", ),
                    ("status",),

                    ("web_source", ),

                    ("approved_on", "created_on", "user_updated_on",
                     "updated_on"),
                    ("approved_by", "created_by", "updated_by"),

                    ("generic_data_list", "generic_data_json")
                )
            }
        ),
        (
            "Screenshot", {
                "fields": (
                    ("old_image", "new_image")
                )
            }
        )
    )

    radio_fields = {"status": admin.HORIZONTAL, }

    readonly_fields = (
        "approved_on", "created_on", "snippet_info", "generic_data_json",
        "generic_data_list", "hash", "updated_on", "user_updated_on",
        "approved_by", "created_by", "updated_by", "web_source"
    )

    related_search_fields = {
        "client": {
            "search_fields": ["company__name", ], "show_item_admin_link": True
        },
        "source": {
            "search_fields": ["name", ], "show_item_admin_link": True
        },
        # "content_source": {
        #     "search_fields": ['name', ], "show_item_admin_link": True
        # },
        "content_type": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "published_by_company": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "locations": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "source_locations": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "companies_hq_locations": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "companies": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "industries": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "topics": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "business_events": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "themes": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "custom_tags": {
            "search_fields": ['name', ], "show_item_admin_link": True
        },
        "persons": {
            "search_fields": ['name', ], "show_item_admin_link": True
        }
    }

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_or_change_permission(self, request, obj=None):
        return has_website_tracking_access(request)

    def render_change_form(self, request, context, add=False, change=False,
                           form_url='', obj=None):
        context["WST_PATH"] = WST_PATH

        if obj:
            if obj.diff_content_id:
                context["diffContentID"] = obj.diff_content_id

            if obj.snippet_info:
                context["previewSnippetInfo"] = obj.snippet_info

            if obj.old_image and hasattr(obj.old_image, "url"):
                context["oldImageUrl"] = obj.old_image.url

            if obj.new_image and hasattr(obj.new_image, "url"):
                context["newImageUrl"] = obj.new_image.url

        if "diffContentID" not in context:
            context["diffContentID"] = request.GET.get('diff_content_id')

        if "previewSnippetInfo" not in context:
            context["previewSnippetInfo"] = {}

        if "oldImageUrl" not in context:
            context["oldImageUrl"] = FF_OLD_DIFF_IMAGE_URL

        if "newImageUrl" not in context:
            context["newImageUrl"] = FF_OLD_DIFF_IMAGE_URL

        return super().render_change_form(
            request, context, add=add, change=change, form_url=form_url,
            obj=obj
        )

    def add_view(self, request, form_url='', extra_context=None):
        q = request.GET.copy()
        if 'approved_on' in q:
            q['approved_on'] = datetime.strptime(
                request.GET.get('approved_on'), "%Y-%m-%d %H:%M:%S"
            )

        # Below stuff is used if user has clicked on copy_web_update and the
        # url of copy WebUpdate will be like:
        # "/website-tracking/admin/website_tracking/webupdate/add/?copy_wu_id=15"
        if 'copy_wu_id' in q:
            params = self.get_clone_fields(q['copy_wu_id'])
            q.update(params)
            # q.pop('copy_wu_id')

            old_img = params.get("old_image")
            new_img = params.get("new_image")
            old_img_url = FF_OLD_DIFF_IMAGE_URL
            new_img_url = FF_OLD_DIFF_IMAGE_URL

            if old_img and hasattr(old_img, "url"):
                old_img_url = old_img.url

            if new_img and hasattr(new_img, "url"):
                new_img_url = new_img.url

            extra_context = extra_context or {}
            extra_context["previewSnippetInfo"] = (
                params.get("snippet_info") or {}
            )
            extra_context["oldImageUrl"] = old_img_url
            extra_context["newImageUrl"] = new_img_url

        request.GET = q
        return super().add_view(request, form_url, extra_context)

    @staticmethod
    def get_clone_fields(web_update_id):
        """
        Get cloneable fields value info for the given web_update_id, this info
        is be used to create a new WebUpdate (copy of) through admin interface.

        :param web_update_id: An Id of a WebUpdate
        :return: a dict (key will be the field of WebUpdate and value will be
         field value of the WebUpdate)
        """
        exclude_fields = [
            "id", "created_on", "approved_on", "updated_on", "created_by",
            "approved_by", "updated_by", "client", "generic_data_json",
            "generic_data_list", "status", "hash"
        ]

        try:
            wu_obj = WebUpdate.objects.get(id=web_update_id)

            kwargs = {
                f.name: getattr(wu_obj, f.name)
                for f in wu_obj._meta.fields if f.name not in exclude_fields
            }
            kwargs['status'] = StoryStatus.PUBLISHED.value
            kwargs['manual_copy_of_id'] = wu_obj.manual_copy_of_id or wu_obj.id

            for m2mf in TAGS_MULTI_FIELDS:
                if m2mf in ["custom_tags"]:
                    continue

                tag_id_list = list(
                    getattr(wu_obj, m2mf).values_list("id", flat=True)
                )
                if tag_id_list:
                    kwargs[m2mf] = ",".join(map(str, tag_id_list))

            return kwargs

        except WebUpdate.DoesNotExist:
            logger.exception(
                "Error while collecting the cloneable fields of WebUpdate for "
                "WebUpdateID: {} traceback: {}".format(
                    web_update_id, traceback.format_exc()
                )
            )
        return {}

    def save_model(self, request, obj, form, change):
        """
            populating approved_on and approved_by just once i.e, first time
            when WebUpdate is published.

            populating approved_by also when WebUpdate is rejected
        """
        if not change:
            obj.created_by = request.user
            obj.hash = get_md5_hash_of_string(obj.title + obj.description)
            obj.approved_on = datetime.now()

        if not obj.approved_on:
            obj.approved_on = datetime.now()

        obj.approved_by_id = request.user.id
        obj.update_by_id = request.user.id

        if request.POST.get("previewSnippetInfo"):
            try:
                snippet_info = json.loads(request.POST["previewSnippetInfo"])
                obj.snippet_info = snippet_info
            except Exception as e:
                logger.exception(
                    "WebUpdateAdminSave!, Unable to parse snippet_info, POST "
                    "data: {}".format(request.POST)
                )

        if not change:
            is_image_uploaded = self.decouple_copy_reference(request, obj)
            if not is_image_uploaded:
                obj.save()
        else:
            obj.save()
        single_story_admin_update_journal(request.user, obj.id, form)

    @staticmethod
    def decouple_copy_reference(request, obj):
        is_image_uploaded = False
        f_name = "{}.jpeg".format(datetime.now().strftime("%d-%m-%Y-%H-%M-%S"))

        manual_copy_of_id = request.GET.get("manual_copy_of_id")
        copy_wu_id = request.GET.get("copy_wu_id")

        if not copy_wu_id or not manual_copy_of_id:
            raise ValueError(
                "copy_wu_id and manual_copy_of_id are required to create a "
                "copy of WebUpdate"
            )

        # Reference of manual_copy_of_id and diff_content id
        obj.manual_copy_of_id = manual_copy_of_id
        obj.diff_content_id = request.GET.get("diff_content_id")

        old_image = request.GET.get("old_image")
        old_image_clear = request.POST.get("old_image-clear")

        # Upload a old image from cloned object if meets the below conditions
        if (old_image_clear != "on" and "old_image" not in request.FILES and
                old_image and hasattr(old_image, "url")):

            old_image.seek(0)
            obj.old_image.save(f_name, ContentFile(old_image.read()))

            is_image_uploaded = True

            if obj.snippet_info.get("url") == old_image.url:
                obj.snippet_info["url"] = obj.old_image.url

        new_image = request.GET.get("new_image")
        new_image_clear = request.POST.get("new_image-clear")

        # Upload a new image from cloned object if meets the below conditions
        if (new_image_clear != "on" and "new_image" not in request.FILES and
                new_image and hasattr(new_image, "url")):

            new_image.seek(0)
            obj.new_image.save(f_name, ContentFile(new_image.read()))

            is_image_uploaded = True

            if obj.snippet_info.get("url") == new_image.url:
                obj.snippet_info["url"] = obj.new_image.url

        return is_image_uploaded

    def _description(self, obj):
        change_log_link = f"""
          <p align="right">
            <a href="{obj.get_redirecting_url()}" target="_blank">
              Change Log ({obj.approved_on.strftime('%d %b %Y')})
            </a>
          </p>
        """
        return mark_safe(obj.description + change_log_link)
    _description.allow_tags = True

    def _client(self, obj):
        return mark_safe(
            "<p>{}</p>".format(
                admin_filters.get_client_map(obj.client_id), "None"
            )
        )
    _client.allow_tags = True


cfy_admin_site.register(WebSource, WebSourceAdmin)
cfy_admin_site.register(WebSnapshot, WebSnapshotAdmin)
cfy_admin_site.register(DiffHtml, DiffHtmlAdmin)
cfy_admin_site.register(DiffContent, DiffContentAdmin)
cfy_admin_site.register(WebClientSource, WebClientSourceAdmin)
cfy_admin_site.register(WebUpdate, WebUpdateAdmin)
