
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, re_path

from contify.website_tracking import views
from contify.website_tracking import data_migrate as dm_views
from contify.website_tracking.cfy_admin_conf import cfy_admin_site

# initiating a post WebUpdate signal to index the WebUpdate after committing
# to the DB
from contify.website_tracking.signals import post_web_update_save_handler


urlpatterns = [
    re_path(r'^admin/', cfy_admin_site.urls),
    path("preview-card/", views.preview_card),
    path("create-or-update-web-update/", views.create_or_update_web_update),
    path("web-update-upload-img", views.web_update_upload_image),
    path("diff-content-upload-img/", views.diff_content_upload_image),
    path("fetch-change-log/", views.fetch_change_log),
    path("edit-card/", views.edit_card_in_news_feed),
    path("regenerate-screenshot/", views.regen_n_export_screenshot),
    path("test-web-url/", views.test_web_url),
    path("test-fetch-web-url/", views.test_fetch_web_url),
    re_path(r"^(?P<wu_id>\d+)/web-update/$", views.web_update_detail),
    re_path(r'^(?P<hash_id>\w+)/change-log/$', views.web_update_change_log),
    re_path(r'^(?P<hash_id>\w+)/change-log-direct/$', views.change_log_direct),
    re_path(r"^(?P<wu_id>\d+)/new-change-log/$", views.new_change_log),
    re_path(r"^(?P<wu_id>\d+)/edit-wu-in-cm/$", views.edit_card_in_curation),
    re_path(r"^(?P<diff_id>\d+)/diff-html/$", views.view_diff_html),
    re_path(r"^(?P<dh_id>\d+)/dh-diff-html/$", views.view_dh_diff_html),
    re_path(r"^(?P<dc_id>\d+)/(?P<dc_fl>\w+)/direct-dh-diff-content/$",
            views.view_direct_dh_diff_content),
    re_path(r"^(?P<diff_id>\d+)/diff-screenshot/$", views.view_diff_screenshot),
    re_path(r"^(?P<diff_id>\d+)/raw-html/$", views.view_raw_html),
    re_path(r"^(?P<diff_id>\d+)/raw-screenshot/$", views.view_raw_screenshot),
    re_path(r"^(?P<diff_id>\d+)/curation/$", views.diff_curation),

    path("web-source-raw-data", dm_views.get_web_source_data),
    path("web-client-source-raw-data", dm_views.get_web_client_source),
    path("web-update-raw-data", dm_views.get_web_update),
    path("web-snapshot-raw-data", dm_views.get_web_snapshot),
    path("diff-html-raw-data", dm_views.get_diff_html),
    path("diff-content-raw-data", dm_views.get_diff_content)
]


if settings.DEBUG:
    #  urlpatterns += static("static/", document_root="contify/website_tracking/static/")
    urlpatterns += static(
        settings.STATIC_URL, document_root=settings.STATIC_ROOT
    )


