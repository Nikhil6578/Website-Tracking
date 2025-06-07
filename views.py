
import ast
import json
import logging
import traceback
import urllib
from datetime import datetime
from io import StringIO
from lxml import etree
from urllib.parse import urlparse

from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.http import (
    JsonResponse, HttpResponseForbidden, HttpResponseBadRequest
)
from django.shortcuts import render, Http404, HttpResponse
from django.utils.safestring import mark_safe
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import tldextract

from config.constants import WST_SCREENSHOT_IP, WST_SCREENSHOT_PORT
from config.custom_response import UJsonResponse
from config.utils import decrypt_string
from contify.cutils.utils import can_modify_content, int_or_none
from contify.story.constants import STORY_UPDATE_ERROR_MSG
from contify.story.service import is_bulk_updating
from contify.website_tracking import constants as ws_constants
from contify.website_tracking import dummy_data as ws_d_data
from contify.website_tracking import service as ws_service
from contify.website_tracking import utils as ws_utils
from contify.website_tracking.diff_html.utils import patch_base_tag
from contify.website_tracking.execptions import NoChangeInWebUpdateEdit
from contify.website_tracking.indexer_raw import index_web_updates
from contify.website_tracking.models import WebUpdate, WebSource
from contify.website_tracking.web_snapshot.models import (
    DiffContent, DiffHtml, WebSnapshot
)

logger = logging.getLogger(__name__)


@xframe_options_exempt
@login_required
def test_web_url(request):
    cxt = dict()
    cxt["WST_PATH"] = ws_constants.WST_PATH
    return render(request, 'test_web_url.html', cxt)


@login_required
@require_POST
@csrf_exempt
def test_fetch_web_url(request):
    cxt = dict()
    try:
        web_url = request.POST["webUrl"]
        time_out = request.POST["timeOut"]
        head_less = request.POST["headLess"]
        dev_tools = request.POST["devTools"]
        use_original_height = request.POST["useOriginalHeight"]
        close_browser = request.POST["closeBrowser"]
        gen_screenshot = request.POST["genScreenshot"]
        use_base_url = request.POST["patchBaseUrl"]
        cxt = ws_service.fetch_web_url(
            web_url,
            headless=bool(int(head_less)),
            devtools=bool(int(dev_tools)),
            timeout=int(time_out),
            gen_screenshot=bool(int(gen_screenshot)),
            use_original_height=bool(int(use_original_height)),
            close_browser=bool(int(close_browser))
        )
        html_str = cxt["html"]
        if html_str and use_base_url and bool(int(use_base_url)):
            td = tldextract.extract(web_url)
            up = urlparse(web_url)
            base_url = "{}://{}".format(up.scheme, td.fqdn)

            html_parser = etree.HTMLParser(encoding="utf-8", compact=False)
            raw_old_tree = etree.parse(StringIO(html_str), html_parser)
            patch_base_tag(raw_old_tree, base_url)
            html_str = etree.tounicode(raw_old_tree, method="html")
            cxt["html"] = html_str
    except Exception as e:
        cxt["error"] = str(traceback.format_exc())

    return UJsonResponse(cxt)


@xframe_options_exempt
@login_required
def view_dh_diff_html(request, dh_id):
    if request.GET.get("dummy"):
        return render(request, 'iframe_comp.html', ws_d_data.DIFF_HTML)

    dh = DiffHtml.objects.get(id=dh_id)
    cxt = ws_service.get_cxt_diff_html(dh)
    return render(request, 'iframe_comp.html', cxt)


@csrf_exempt
def auth_render_direct_diff_html(request):
    try:
        encrypted_token = request.headers.get("WST-Auth-Key")
        logger.info(
            "auth_render_direct_diff_html! Authenticating..., "
            f"Auth-key received: {True if encrypted_token else False}"
        )
        if encrypted_token:
            try:
                expiration_time = ws_utils.decrypt_token(encrypted_token)
                if not (
                        expiration_time
                        or isinstance(ast.literal_eval(expiration_time), int)
                ):
                    logger.info(
                        f"auth_render_direct_diff_html! Invalid WST-Auth-Key: "
                        f"{expiration_time} ('{type(expiration_time)}') passed."
                    )
                    return HttpResponse(status=401)  # Unauthorized
            except Exception as err:
                logger.info(
                    "auth_render_direct_diff_html! Error decrypting the "
                    f"token: {encrypted_token}, Error: {err}"
                )
                return HttpResponse(status=401)  # Unauthorized

            current_time = int(datetime.now().timestamp())
            if current_time < int(expiration_time) and request.method == "GET":

                email = ws_constants.WST_AUTH_PASS_KEY_MAP.get("EMAIL")
                password = ws_constants.WST_AUTH_PASS_KEY_MAP.get("PASS")
                if email and password:

                    user = authenticate(request, username=email, password=password)
                    if user and user.is_active:
                        login(request, user)
                        return HttpResponse(status=200)  # authenticated

                    else:
                        logger.info(
                            "auth_render_direct_diff_html! Authentication "
                            f"failed! USER with Username: '{email}' and "
                            f"Password: '{password}' either "
                            f"NOT FOUND: {False if user else True} OR "
                            f"INACTIVE: {user.is_active if user else True}."
                        )
                else:
                    logger.info(
                        f"auth_render_direct_diff_html! Authentication failed: "
                        f"Invalid USERNAME: '{email}' or PASSWORD: '{password}'"
                    )

            else:
                logger.info(
                    f"auth_render_direct_diff_html! UnAuthorized Access. "
                    f"Authentication Key Expired: {expiration_time<current_time}, "
                    f"Invalid Request-Method: {request.method != 'GET'}. "
                    f"Expiration-Time: {expiration_time}, "
                    f"Current-Time: {current_time}, "
                    f"Request-Headers: {request.headers}"
                )

        else:
            logger.info(
                "auth_render_direct_diff_html! Unauthorized Access "
                f"Authentication Key Not Passed. Request Headers: {request.headers}"
            )
        return HttpResponse(status=401)  # Unauthorized

    except Exception as err:
        logger.error(
            f"auth_render_direct_diff_html! wWhile authenticating an "
            f"Error Occurred: {err}, Traceback: {traceback.format_exc()}."
        )
        return HttpResponse(status=500)  # Internal Server Error


# @sensitive_post_parameters('fsx5k')
# @csrf_exempt
# def render_direct_diff_html(request):
#     if hasattr(request, 'user') and request.user.is_authenticated:
#         if request.user.username != "wst.render@contify.com":
#             raise Http404()
#         return HttpResponse()
#
#     if request.method == "POST":
#         post_data = request.POST
#         email = post_data.get('VCJ4i')
#         if email != "wst.render@contify.com":
#             raise Http404()
#
#         password = post_data.get('fsx5k')
#         if email and password:
#             email = email.strip()
#             user = authenticate(request, username=email, password=password)
#             if user and user.is_active:
#                 login(request, user)
#                 return HttpResponse()
#
#         raise Http404()
#     return render(request, "internal_login.html", {})


def view_direct_diff_html_content(request, dh_id_enc, dh_fl_enc):
    """
        This is used by a job only which render the external website content
        and this is accessible by a user because someone can report for the
        website phishing that is why restricted to a user.
    """
    if request.user.is_authenticated:
        try:
            (dh_id,) = int_or_none(decrypt_string(dh_id_enc))
        except ValueError:
            logger.info(f'Value error while decrypting {dh_id_enc}')
            raise Http404()

        try:
            dh_fl = ws_constants.REV_ENC_DIFF_FIELD_MAP[dh_fl_enc]
        except ValueError:
            raise Http404()

        dh = DiffHtml.objects.get(id=dh_id)
        dh_html = getattr(dh, dh_fl)
        return HttpResponse(dh_html)
    logger.info(
        f'User: {request.user} was not logged. '
        f'dh_id_enc: {dh_id_enc}, '
        f'dh_fl_enc: {dh_fl_enc}'
    )
    raise Http404


@login_required
def view_direct_dh_diff_content(request, dc_id, dc_fl):
    dh = DiffContent.objects.get(id=dc_id)
    dh_html = getattr(dh, dc_fl)
    return HttpResponse(dh_html)


@xframe_options_exempt
@login_required
def view_diff_html(request, diff_id):
    if request.GET.get("dummy"):
        return render(request, 'iframe_comp.html', ws_d_data.DIFF_HTML)

    dc = DiffContent.objects.get(id=diff_id)
    cxt = ws_service.get_cxt_diff_html(dc)
    return render(request, 'iframe_comp.html', cxt)


@login_required
def view_diff_screenshot(request, diff_id):
    if request.GET.get("dummy"):
        return HttpResponse(ws_d_data.DIFF_SCREENSHOT)

    dc = DiffContent.objects.get(id=diff_id)

    if dc.old_diff_image and hasattr(dc.old_diff_image, "url"):
        old_diff_image_url = dc.old_diff_image.url
    else:
        old_diff_image_url = ws_constants.FF_OLD_DIFF_IMAGE_URL

    if dc.new_diff_image and hasattr(dc.new_diff_image, "url"):
        new_diff_image_url = dc.new_diff_image.url
    else:
        new_diff_image_url = ws_constants.FF_OLD_DIFF_IMAGE_URL

    cxt = {
        "old_diff_image_url": old_diff_image_url,
        "new_diff_image_url": new_diff_image_url,
        "diffContentID": diff_id
    }
    return render(request, 'view_diff_screenshot.html', cxt)


@xframe_options_exempt
@login_required
def view_raw_html(request, diff_id):
    if request.GET.get("dummy"):
        return render(request, 'iframe_comp.html', ws_d_data.DIIF_RAW_HTML)

    dc = DiffContent.objects.get(id=diff_id)
    ws = WebSource.objects.get(id=dc.new_snapshot.web_source_id)

    base_url = ws.base_url

    parser = etree.HTMLParser(
        encoding="utf-8", remove_comments=True, compact=False,
        default_doctype=False
    )

    new_tree = etree.parse(StringIO(dc.new_snapshot.raw_html or ""), parser)
    patch_base_tag(new_tree, base_url)

    cxt = {
        "new_html": etree.tounicode(new_tree, method="html"),
        "obj_id": dc.id,
        "regen_screenshot_new": (
            f"http://{WST_SCREENSHOT_IP}:{WST_SCREENSHOT_PORT}/website-tracking/"
            f"regenerate-screenshot/?obj_id={dc.new_snapshot.id}&"
            f"obj_type=WebSnapshot&fl=raw_html"
        )
    }
    if dc.old_snapshot:
        old_tree = etree.parse(StringIO(dc.old_snapshot.raw_html or ""), parser)
        patch_base_tag(old_tree, base_url)

        cxt["old_html"] = etree.tounicode(old_tree, method="html")
        cxt["regen_screenshot_old"] = (
            f"http://{WST_SCREENSHOT_IP}:{WST_SCREENSHOT_PORT}/website-tracking/"
            f"regenerate-screenshot/?obj_id={dc.old_snapshot.id}&"
            f"obj_type=WebSnapshot&fl=raw_html"
        )
    else:
        cxt["old_html"] = ws_constants.FF_OLD_DIFF_HTML

    return render(request, 'iframe_comp.html', cxt)


@login_required
def view_raw_screenshot(request, diff_id):
    if request.GET.get("dummy"):
        return HttpResponse(ws_d_data.DIFF_RAW_SCREENSHOT)

    dc = DiffContent.objects.get(id=diff_id)

    old_ele = """<div style="width:50%; background: darkgrey;"></div>"""
    new_img_url = ws_constants.FF_OLD_DIFF_IMAGE_URL

    if (dc.new_snapshot and dc.new_snapshot.raw_snapshot and
            hasattr(dc.new_snapshot.raw_snapshot, "url")):
        new_img_url = dc.new_snapshot.raw_snapshot.url

    if (dc.old_snapshot and dc.old_snapshot.raw_snapshot and
            hasattr(dc.old_snapshot.raw_snapshot, "url")):
        old_ele = (
            f"""<img src={dc.old_snapshot.raw_snapshot.url} style="width:50%;
            height:100%;" />"""
        )

    new_ele = f"""<img src={new_img_url} style="width:50%; height:100%;" />"""

    return HttpResponse(
        mark_safe(f'<div style="display:flex;">{old_ele}\n{new_ele}</div>')
    )


@login_required
def diff_curation(request, diff_id):
    try:
        if request.GET.get("dummy"):
            cxt = ws_d_data.DIFF_CXT[diff_id]
            cxt["WST_PATH"] = ws_constants.WST_PATH
            return render(request, "curation_page.html", cxt)

        cxt = ws_service.get_diff_cxt(diff_id)
    except DiffContent.DoesNotExist as e:
        cxt = ws_d_data.DIFF_CXT[diff_id]

    except Exception as e:
        logger.exception(
            "Unable to render DiffCuration screen for diff_id: {}, traceback: "
            "{}".format(diff_id, traceback.format_exc())
        )
        raise Http404("Diff record does not exist")

    cxt["WST_PATH"] = ws_constants.WST_PATH
    return render(request, "curation_page.html", cxt)


@login_required
def edit_card_in_curation(request, wu_id):
    try:
        cxt = ws_service.get_web_update_cxt(wu_id, True)
        # cxt = ws_d_data.EDIT_CARD_IN_CURATION[wu_id]
    except Exception as e:
        logger.exception(
            "EditCardInCuration, Unknown error: {}, traceback: {}".format(
                e, traceback.format_exc()
            )
        )
        raise Http404("The resource you are looking for does not exist")
    cxt["WST_PATH"] = ws_constants.WST_PATH
    return render(request, "curation_page.html", cxt)


@login_required
def preview_card(request):
    # In some cases, we were getting incomplete information using
    # request.GET.get("cxt") that is why doing below 2 lines.
    decoded_full_path = urllib.parse.unquote(request.get_full_path())
    cxt_str = decoded_full_path.replace(
        f"{ws_constants.WST_ADMIN_PREFIX_URL}/preview-card/?cxt=", ""
    )
    cxt = json.loads(cxt_str)
    cxt["WST_PATH"] = ws_constants.WST_PATH
    return render(request, "preview-card.html", cxt)


@login_required
def web_update_detail(request, wu_id):
    try:
        if request.GET.get("dummy"):
            cxt = ws_d_data.WEB_UPDATE_CXT[str(wu_id)]
            cxt["WST_PATH"] = ws_constants.WST_PATH
            return render(request, "preview-card.html", cxt)

        cxt = ws_service.get_web_update_cxt(wu_id)
    except WebUpdate.DoesNotExist as e:
        cxt = ws_d_data.WEB_UPDATE_CXT[str(wu_id)]

    except Exception as e:
        logger.exception(f"WebUpdateDetailView, Unknown error: {e}")
        raise Http404("The resource you are looking for does not exist")

    cxt["WST_PATH"] = ws_constants.WST_PATH
    return render(request, "preview-card.html", cxt)


@login_required
def web_update_change_log(request, hash_id):
    return change_log_direct(request, hash_id)


def change_log_direct(request, hash_id):
    try:
        (wu_id,) = int_or_none(decrypt_string(hash_id))
    except ValueError:
        raise Http404("The resource you are looking for does not exist")

    try:
        if request.GET.get("dummy"):
            cxt = ws_d_data.CHANGE_LOG_DETAILS[str(wu_id)]
            cxt["WST_PATH"] = ws_constants.WST_PATH
            cxt["isDummy"] = True
            return render(request, "change_log.html", cxt)

        cxt = ws_service.get_change_log_detail_cxt(wu_id)
    except WebUpdate.DoesNotExist as e:
        cxt = ws_d_data.CHANGE_LOG_DETAILS[str(wu_id)]

    except Exception as e:
        logger.exception(f"ChangeLogView, Unknown error: {e}")
        raise Http404("The web update you are looking for does not exist")

    if not cxt:
        logger.error(
            f"ChangeLogView!, Unable to generate change log for WebUpdate ID: "
            f"{wu_id}"
        )
        raise Http404("The web update you are looking for does not exist")

    cxt["WST_PATH"] = ws_constants.WST_PATH
    cxt["isDummy"] = False
    return render(request, "change_log.html", cxt)


@login_required
def new_change_log(request, wu_id):
    cxt = ws_d_data.CHANGE_LOG_DETAILS[str(wu_id)]
    cxt["WST_PATH"] = ws_constants.WST_PATH
    cxt["isDummy"] = True
    return render(request, "new_change_log.html", cxt)


@login_required
@require_POST
@csrf_exempt
def fetch_change_log(request):
    post_data = json.loads(request.body)
    cxt = ws_service.fetch_change_log(request.subscriber.client_id, post_data)
    # cxt = ws_d_data.CHANGE_LOG
    return UJsonResponse(cxt)


@login_required
@require_POST
@csrf_exempt
def create_or_update_web_update(request):
    post_data = json.loads(request.POST.get("postData"))
    try:
        wu_obj, copied_wu, created = ws_service.create_or_update_web_update(
            post_data, request.user
        )
        msg = (
            "<span class='success-msg'>The web update “<label for='title' "
            "class='msg-title'>{} ....</label>” was <label class='msg-title'>"
            "{}</label> successfully. click <a href='{}/{}/web-update/' "
            "target='_blank'>here</a> to view it</span>".format(
                wu_obj.title[:31], "created" if created else "updated",
                ws_constants.WST_ADMIN_PREFIX_URL, wu_obj.id
            )
        )
        if copied_wu:
            index_web_updates(copied_wu.id, soft_commit=True)
            msg += (
                "<span class='success-msg'>A copy for SI also created as "
                "per request.click"
                " <a href='{}/{}/web-update/' target='_blank'>here</a> "
                "to view it.</span>".format(
                    ws_constants.WST_ADMIN_PREFIX_URL, copied_wu.id
                )
            )
        return JsonResponse({"msg": msg, "webUpdateID": wu_obj.id})
    except Exception as e:
        msg = (
            "<span class='error-msg'>Something went wrong while "
            "<label class='msg-title'>{}</label> the web update “<label "
            "for='title' class='msg-title'>{} ....</label>”</span>"
            "<label class='trace' style='display: none;'>{}</label>".format(
                "updating" if "wuID" in post_data else "creating",
                post_data.get("title", "")[:40], traceback.format_exc()
            )
        )
        logger.exception(
            "Error while creating a WebUpdate by {}, with data: {}:\n\n "
            "traceback: {}".format(
                request.user, post_data, traceback.format_exc()
            )
        )
        return JsonResponse({"msg": msg})


@login_required
@require_POST
@csrf_exempt
def web_update_upload_image(request):
    image_blob = request.FILES["imgBlob"]
    field_name = request.POST["fieldName"]
    try:
        web_update_id = int(request.POST["webUpdateID"])
        img_url = ws_service.update_diff_screenshot(
            WebUpdate, web_update_id, image_blob.read(), field_name
        )
        msg = (
            "<span class='success-msg'>The Image “<label for='title' "
            "class='msg-title'>{0}</label>” was <label class='msg-title'>"
            "{1}</label> uploaded successfully. click "
            "<a href='{1}/{2}/web-update/' target='_blank'>here</a> to view "
            "it</span>".format(
                image_blob.name, ws_constants.WST_ADMIN_PREFIX_URL,
                web_update_id
            )
        )

        cxt = {
            "msg": msg,
            "webUpdateID": web_update_id,
            "imgURL": img_url
        }
        return JsonResponse(cxt)
    except Exception as e:
        msg = (
            "<span class='error-msg'>Something went wrong while "
            "<label class='msg-title'>Uploading</label> the {} image “<label "
            "for='title' class='msg-title'>{} ....</label>”</span>"
            "<label class='trace' style='display: none;'>{}</label>".format(
                field_name, image_blob.name, traceback.format_exc()
            )
        )
        logger.exception(
            "Error while creating a WebUpdate by {}, with data: {}:\n\n "
            "traceback: {}".format(
                request.user, request.POST, traceback.format_exc()
            )
        )
        return JsonResponse({"msg": msg})


@login_required
@require_POST
@csrf_exempt
def diff_content_upload_image(request):
    image_blob = request.FILES["imgBlob"]
    field_name = request.POST["fieldName"]
    try:
        diff_content_id = int(request.POST["diffContentID"])
        img_url = ws_service.update_diff_screenshot(
            DiffContent, diff_content_id, image_blob.read(), field_name
        )
        cxt = {
            "diff_content_id": diff_content_id, "imgURL": img_url
        }
        return JsonResponse(cxt)
    except Exception as e:
        logger.exception(
            "Error while Uploading DiffContent screenshot by {}, with data: {}"
            ":\n\n traceback: {}".format(
                request.user, request.POST, traceback.format_exc()
            )
        )
        cxt = {
            "error": True, "traceback": str(traceback.format_exc())
        }
        return JsonResponse(cxt)


@login_required
@require_POST
@csrf_exempt
def edit_card_in_news_feed(request):
    subscriber = request.subscriber
    try:
        if not can_modify_content(subscriber):
            return HttpResponseForbidden()

        try:
            post_data = json.loads(request.POST["form-data"])
            _id = post_data['uuid']
            if is_bulk_updating(f'wu.{_id}'):
                return HttpResponseBadRequest(
                    STORY_UPDATE_ERROR_MSG,
                    reason='cfyInternalServerError'
                )

            cxt = ws_service.edit_web_update(post_data, subscriber)

        except NoChangeInWebUpdateEdit:
            return UJsonResponse({"error": "noChange"})

        except (ValueError, KeyError) as e:
            logger.info(
                "EditWebUpdate, ValueError/KeyError, traceback: {}".format(
                    traceback.format_exc()
                )
            )
            return UJsonResponse({"error": "internalError"})

        logger.info("Finished Inserting WebUpdate")
        return UJsonResponse(cxt)

    except Exception as e:
        logger.exception(
            "Failed in Inserting WebUpdate request error: {}".format(traceback.format_exc())
        )
        raise Http404


@login_required
@csrf_exempt
def regen_n_export_screenshot(request):
    obj_id = request.GET["obj_id"]
    obj_type = request.GET["obj_type"]
    field_name = request.GET["fl"]

    try:
        if obj_type.lower() == "websnapshot":
            dc = WebSnapshot.objects.get(id=obj_id)
        elif obj_type.lower() == "diffcontent":
            dc = DiffContent.objects.get(id=obj_id)
        elif obj_type.lower() == "diffhtml":
            dc = DiffHtml.objects.get(id=obj_id)
        else:
            raise Http404("Invalid object type")
    except ObjectDoesNotExist as e:
        raise Http404("The Object you are looking for does not exits")

    try:
        tmp_html = getattr(dc, field_name) or ws_constants.FF_OLD_DIFF_HTML
        binary_screenshot = ws_service.get_screenshot_from_str_html(tmp_html)

        now = datetime.strftime(datetime.now(), '%d-%b-%Y-%H-%M-%S')
        file_name = f"{field_name}_diff_content_{dc.id}_{now}.jpeg"

        response = HttpResponse(binary_screenshot, content_type="image/jpeg")
        response['Content-Disposition'] = 'attachment; filename=%s' % file_name
        return response
    except Exception as e:
        logger.exception(
            "Unable to regenerate and export screenshot for request.Get: {}, "
            "traceback: {}".format(
                request.GET, traceback.format_exc()
            )
        )
        raise Http404('No Content Found')
