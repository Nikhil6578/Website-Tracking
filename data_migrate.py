
import copy
import os
import requests
import time
import traceback
from io import StringIO
from lxml import etree

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.http import JsonResponse, Http404

from config import ROOT_DIR
from contify.website_tracking.diff_html.utils import patch_base_tag
from contify.website_tracking.models import (
    WebSource, WebClientSource, WebUpdate
)
from contify.website_tracking.service import get_screenshot_from_str_html
from contify.website_tracking.web_snapshot.models import (
    WebSnapshot, DiffHtml, DiffContent
)


def get_items_ids(request):
    if "uid" not in request.GET:
        raise Http404({"msg": "Unauthorized User"})

    user = User.objects.get(id=request.GET["uid"])
    if not user.is_staff:
        raise Http404({"msg1": "Unauthorized User"})

    if "item_ids" in request.GET:
        return list(
            map(int, map(str.strip, request.GET["item_ids"].split(",")))
        )
    return []


def get_web_source_data(request):
    cxt = list()
    web_source_qs = WebSource.objects.all()

    item_ids = get_items_ids(request)
    if item_ids:
        web_source_qs = web_source_qs.filter(id__in=item_ids)

    for ws in web_source_qs:
        cxt.append({
            "id": ws.id,
            "created_by_id": ws.created_by_id,
            "updated_by_id": ws.updated_by_id,
            "title": ws.title,
            "web_url": ws.web_url,
            "base_url": ws.base_url,
            "state": ws.state,
            "comment": ws.comment,
            "frequency": ws.frequency,
            "last_run": ws.last_run,
            "last_error": ws.last_error,
            "created_on": ws.created_on,
            "updated_on": ws.updated_on
        })
    return JsonResponse(cxt, safe=False)


def get_web_client_source(request):
    cxt = list()
    client_source_qs = WebClientSource.objects.all()

    item_ids = get_items_ids(request)
    if item_ids:
        client_source_qs = client_source_qs.filter(id__in=item_ids)

    for sc in client_source_qs:
        cxt.append({
            "direct": {
                "id": sc.id,
                "client_id": sc.client_id,
                "source_id": sc.source_id,
                "created_by_id": sc.created_by_id,
                "updated_by_id": sc.updated_by_id,
                "state": sc.state,
                "created_on": sc.created_on,
                "updated_on": sc.updated_on,
                "language": sc.language
            },
            "through": {
                "locations": list(sc.locations.values_list("id", flat=True)),
                "source_locations": list(sc.source_locations.values_list("id", flat=True)),
                "companies_hq_locations": list(sc.companies_hq_locations.values_list("id", flat=True)),
                "companies": list(sc.companies.values_list("id", flat=True)),
                "industries": list(sc.industries.values_list("id", flat=True)),
                "topics": list(sc.topics.values_list("id", flat=True)),
                "business_events": list(sc.business_events.values_list("id", flat=True)),
                "themes": list(sc.themes.values_list("id", flat=True)),
                "custom_tags": list(sc.custom_tags.values_list("id", flat=True))
            }
        })
    return JsonResponse(cxt, safe=False)


def get_web_update(request):
    cxt = list()
    web_update_qs = WebUpdate.objects.all()

    item_ids = get_items_ids(request)
    if item_ids:
        web_update_qs = web_update_qs.filter(id__in=item_ids)

    for wu in web_update_qs:
        cxt.append({
            "direct": {
                "id": wu.id,
                "client_id": wu.client_id,
                "manual_copy_of_id": wu.manual_copy_of_id,

                "created_by_id": wu.created_by_id,
                "updated_by_id": wu.updated_by_id,
                "approved_by_id": wu.approved_by_id,

                "source_id": wu.source_id,
                "rating_id": wu.rating_id,
                "published_by_company_id": wu.published_by_company_id,

                "language": wu.language,

                "diff_content_id": wu.diff_content_id,

                "hash": wu.hash,
                "status": wu.status,
                "title": wu.title,
                "description": wu.description,
                # "old_image": wu.old_image,
                # "new_image": wu.new_image,
                "email_priority": wu.email_priority,
                "snippet_info": wu.snippet_info,

                "generic_data_list": wu.generic_data_list,
                "generic_data_json": wu.generic_data_json,

                "created_on": wu.created_on,
                "updated_on": wu.updated_on,
                "approved_on": wu.approved_on

            },
            "through": {
                "locations": list(wu.locations.values_list("id", flat=True)),
                "source_locations": list(wu.source_locations.values_list("id", flat=True)),
                "companies_hq_locations": list(wu.companies_hq_locations.values_list("id", flat=True)),
                "companies": list(wu.companies.values_list("id", flat=True)),
                "persons": list(wu.persons.values_list("id", flat=True)),
                "industries": list(wu.industries.values_list("id", flat=True)),
                "topics": list(wu.topics.values_list("id", flat=True)),
                "business_events": list(wu.business_events.values_list("id", flat=True)),
                "themes": list(wu.themes.values_list("id", flat=True)),
                "custom_tags": list(wu.custom_tags.values_list("id", flat=True))
            }
        })
    return JsonResponse(cxt, safe=False)


def get_web_snapshot(request):
    cxt = list()
    web_snapshot_qs = WebSnapshot.objects.all()

    item_ids = get_items_ids(request)
    if item_ids:
        web_snapshot_qs = web_snapshot_qs.filter(id__in=item_ids)

    for wss in web_snapshot_qs:
        cxt.append({
            "web_source_id": wss.web_source_id,
            "state": wss.state,
            "status": wss.status,
            "hash_html": wss.hash_html,
            "raw_html": wss.raw_html,
            # Not getting old_diff_image and new_diff_image as the url of
            # image will production so web generate it in local if needed.
            "last_error": wss.last_error,
            "created_on": wss.created_on,
            "updated_on": wss.updated_on
        })
    return JsonResponse(cxt, safe=False)


def get_diff_html(request):
    cxt = list()
    diff_html_qs = DiffHtml.objects.all()

    item_ids = get_items_ids(request)
    if item_ids:
        diff_html_qs = diff_html_qs.filter(id__in=item_ids)

    for dh in diff_html_qs:
        cxt.append({
            "old_web_snapshot_id": dh.old_web_snapshot_id,
            "old_diff_html": dh.old_diff_html,
            "removed_diff_info": dh.removed_diff_info,

            "new_web_snapshot_id": dh.new_web_snapshot_id,
            "new_diff_html": dh.new_diff_html,
            "added_diff_info": dh.added_diff_info,

            "state": dh.state,
            "status": dh.status,
            "last_error": dh.last_error,

            "created_on": dh.created_on,
            "updated_on": dh.updated_on
        })
    return JsonResponse(cxt, safe=False)


def get_diff_content(request):
    cxt = list()
    diff_content_qs = DiffContent.objects.all()

    item_ids = get_items_ids(request)
    if item_ids:
        diff_content_qs = diff_content_qs.filter(id__in=item_ids)

    for dc in diff_content_qs:
        cxt.append({
            "old_snapshot_id": dc.old_snapshot_id,
            "old_diff_html": dc.old_diff_html,

            "new_snapshot_id": dc.new_snapshot_id,
            "new_diff_html": dc.new_diff_html,

            "state": dc.state,
            "status": dc.status,
            "added_diff_info": dc.added_diff_info,
            "removed_diff_info": dc.removed_diff_info,
            # Not getting old_diff_image and new_diff_image as the url of
            # image will production so web generate it in local if needed.

            "created_on": dc.created_on,
            "updated_on": dc.updated_on
        })
    return JsonResponse(cxt, safe=False)


def create_web_source(raw_data_list):
    for rd in raw_data_list:
        ws = WebSource(**rd)
        ws.save()


def create_client_source(raw_data_list):
    for rd in raw_data_list:
        dd = rd["direct"]
        cs = WebClientSource(**dd)

        try:
            cs.save()
        except Exception as e:
            print(traceback.format_exc())
            continue

        for fl, t_ids in rd["through"].items():
            if t_ids:
                m2m = getattr(cs, fl)
                try:
                    m2m.add(*t_ids)
                except Exception as e:
                    print(
                        "Unable to add tags to WebClientSource-ID: {}, error: "
                        "{}".format(cs.id, e)
                    )


def create_web_update(raw_data_list):
    for rd in raw_data_list:
        dd = rd["direct"]
        cs = WebUpdate(**dd)
        try:
            cs.save()
        except Exception as e:
            print(traceback.format_exc())
            continue

        for fl, t_ids in rd["through"].items():
            if t_ids:
                m2m = getattr(cs, fl)
                try:
                    m2m.add(*t_ids)
                except Exception as e:
                    print(
                        "Unable to add tags to WebUpdate-ID: {}, error: "
                        "{}".format(cs.id, e)
                    )


def create_web_snapshot(raw_data_list):
    for rd in raw_data_list:
        wss = WebSnapshot(**rd)
        wss.save()


def create_diff_html(raw_data_list):
    for rd in raw_data_list:
        dh = DiffHtml(**rd)
        dh.save()


def create_diff_content(raw_data_list):
    for rd in raw_data_list:
        dc = DiffContent(**rd)
        try:
            dc.save()
        except Exception as e:
            print(traceback.format_exc())
            continue


def re_upload_img(w_obj, field_name):
    try:
        img_obj = getattr(w_obj, field_name, None)
        old_url = img_obj.url

        if not img_obj or not hasattr(img_obj, "url"):
            print("No image found")
            return

        f_name = os.path.basename(img_obj.name)

        res = requests.get("http://112233.contify.com/{}".format(img_obj.name), stream=True)

        # img_obj.seek(0)
        img_obj.save(
            f_name, ContentFile(res.raw.read())
        )
        print(
            "uploaded successfully, old URL: {} and new URL: {}".format(
                old_url, img_obj.url
            )
        )
    except Exception as e:
        print("{} ID traceback: {}".format(w_obj.id, traceback.format_exc()))


def generate_image_to_local_file_system(web_obj, html_field_name, base_url=None):
    model_name = web_obj.__class__.__name__

    root_dir_path = ROOT_DIR.path(
        "contify/website_tracking/dist/{}/".format(model_name)
    )
    if not os.path.exists(str(root_dir_path)):
        os.makedirs(str(root_dir_path))

    dir_path = str(
        ROOT_DIR.path(
            f"contify/website_tracking/dist/{model_name}/{html_field_name}"
        )
    )
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)

    tmp_html = copy.deepcopy(getattr(web_obj, html_field_name))

    try:
        if base_url:
            html_parser = etree.HTMLParser(
                encoding="utf-8", remove_comments=True, compact=False,
                # default_doctype=False
            )
            raw_old_tree = etree.parse(StringIO(tmp_html), html_parser)
            patch_base_tag(raw_old_tree, base_url)
            tmp_html = etree.tounicode(raw_old_tree, method="html")

        binary_screenshot = get_screenshot_from_str_html(tmp_html)

        file_path = "{}/{}_{}.jpeg".format(dir_path, web_obj.id, time.time())
        with open(file_path, 'wb') as f:
            f.write(binary_screenshot)

        print(
            "Screenshot created successfully for {}-ID : {}".format(
                model_name, web_obj.id
            )
        )
    except Exception as e:
        print(
            "Unable to create screenshot to {} for for {}-ID : {}, traceback: "
            "{}".format(
                dir_path, model_name, web_obj.id, traceback.format_exc()
            )
        )
