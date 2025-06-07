
import asyncio
import logging
import os
from datetime import datetime

import playwright
from os.path import abspath

from django.conf import settings

import cv2
import imutils
import tldextract
from playwright.async_api import async_playwright
from skimage.measure import compare_ssim

from contify.website_tracking.constants import CONTEXT_DICT

try:
    SCREEN_SHOT_DIR = os.path.join(settings.MEDIA_ROOT, "website_screenshot")
    LOCAL_SCREEN_SHOT_DIR = os.path.join(settings.MEDIA_ROOT, "local_file_screenshot")
    SUB_TREE_HTML_DIR = os.path.join(settings.MEDIA_ROOT, "sub_tree")
except Exception:
    pass


logger = logging.getLogger(__name__)


EXECUTABLE_PATH = "/home/sanjeet/work/ContifyWebTracking/pyppeteer_chromium/local-chromium/588429/chrome-linux/chrome"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.97 Safari/537.36"


def fetch_web_url(web_url, devtools=False, headless=True, pu='adb5925b628547c6b17135ff6237f87f'):
    async def main():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=[
                    '--window-size=1920,1080',
                    '--ignore-certificate-errors',
                    "--no-sandbox",
                    "--incognito",
                ],
                executable_path="/usr/bin/google-chrome",
                devtools=devtools
            )

            context = await browser.new_context(**CONTEXT_DICT)

            page = await context.new_page()
            if page.is_closed():
                logger.info("Page is already closed, reopening a new page.")
                page = await context.new_page()

        # await page.authenticate({'username': pu, 'password': ''})

        # await page.setExtraHTTPHeaders({
        #     'Proxy-Authorization': 'Basic YWRiNTkyNWI2Mjg1NDdjNmIxNzEzNWZmNjIzN2Y4N2Y6',
        #     # 'Authorization': 'Basic YWRiNTkyNWI2Mjg1NDdjNmIxNzEzNWZmNjIzN2Y4N2Y6'
        # })

            await page.goto(
                web_url,
                wait_until='load', timeout=60000
            )
            try:
                # Navigate to the page
                await page.goto(web_url, wait_until='load',
                                timeout=60000)  # 60 seconds timeout
                html_content = await page.content()
                return html_content
            except playwright._impl._errors.TargetClosedError as e:
                logger.info(f"Error navigating to {web_url}: {e}")
                html_content = "Error: Page closed prematurely."
                return html_content

            finally:
                if page:
                    try:
                        await page.close()
                    except Exception as e:
                        logger.info(f"Page already closed or error: {e}")

                if browser:
                    try:
                        await browser.close()
                    except Exception as e:
                        logger.info(f"Browser already closed or error: {e}")

        # await page.waitForNavigation()
        # await page.type('.v78BW', "wst.render@contify.com")
        # await page.type('.mZaaF', "Cq7jP4fZJWugrhoeTb")
        # await page.click('.plx8s')

        # async def intercept(req):
        #     # if req.url.endswith('.png') or req.url.endswith('.jpg'):
        #     #     await req.abort()
        #
        #     if req.url == "http://127.0.0.1:8000/SVEx2XKZQi/90e5f31db7b59dedec63f2464dee5ff0ccd8e5/6050h6jz2UFnWzTIWp/e1pore0Hbq/":
        #         print(req.headers)
        #         print(req.url)
        #         overrides = {
        #             # "url": "",
        #             "method": "POST",
        #             "postData": json.dumps({
        #                 "username": "wst.render@contify.com",
        #                 "password": "Cq7jP4fZJWugrhoeTb"
        #             }),
        #             # "headers": ""
        #         }
        #         await req.continue_(overrides)
        #
        # await page.setRequestInterception(True)
        # page.on('request', lambda req: asyncio.ensure_future(intercept(req)))
        # await page.goto(
        #     "http://127.0.0.1:8000/SVEx2XKZQi/90e5f31db7b59dedec63f2464dee5ff0ccd8e5/6050h6jz2UFnWzTIWp/e1pore0Hbq/", {
        #         #"timeout": 0,  # Disable timeout
        #         # "waitUntil": ["load", "domcontentloaded", "networkidle0"]
        #     }
        # )

        # cookies = await page.cookies()
        # print(cookies)
        # page2 = await browser.newPage()
        # await page2.setCookie(cookies[0])
        # # Setting user agent
        # await page2.setUserAgent(USER_AGENT)
        # # Manually Set Viewport. As default (Width: 800 and Height: 600) is
        # # not capturing the perfect screenshot
        # await page2.setViewport({"width": 1920, "height": 1080})
        # await page2.goto(web_url, {
        #     "timeout": 0,  # Disable timeout
        #     # "waitUntil": ["load", "domcontentloaded", "networkidle0"]
        # })

        # await page.goto(web_url, {
        #     "timeout": 0,  # Disable timeout
        #     # "waitUntil": ["load", "domcontentloaded", "networkidle0"]
        # })

        html_content = await page.content()
        return html_content
        td = tldextract.extract(web_url)
        file_name = "{}_{}_{}_{}.JPEG".format(
            td.subdomain, td.domain, td.suffix, int(datetime.now().timestamp())
        )
        # file_name = "abc.JPEG"
        # file_path = f"{SCREEN_SHOT_DIR}/{file_name}"

        file_prefix = "contify/website_tracking/dist/raw_screenshot"
        file_path = abspath(f'{file_prefix}/{file_name}')

        await page.screenshot({
            "path": file_path,
            "fullPage": True,
            "quality": 100,
            # "clip": {
            #     "width": 1500,
            #     "height": 2000
            # }
            # "omitBackground": True
        })

        await page.close()
        await browser.close()

        cxt = {
            "path": file_path,
            "screenshot_url": f"/media/website_screenshot/{file_name}",
            "html": html_content
        }

        # with open("contify/website_tracking/dist/tmp_home.html", "w") as f:
        #     f.seek(0)
        #     f.truncate()
        #     f.write(html_content)

        return cxt

    return asyncio.run(main())


def get_image_diff(first_file, second_file):
    # load the two input images
    image_a = cv2.imread(f"{SCREEN_SHOT_DIR}/{first_file}")
    image_b = cv2.imread(f"{SCREEN_SHOT_DIR}/{second_file}")

    # Resizing the image as ssim takes the same dimension
    (ha, wa, _) = image_a.shape
    (hb, wb, _) = image_b.shape
    h = max(ha, hb)
    w = max(wa, wb)
    print(ha, wa)
    print(hb, wb)
    print(w, h)

    # image_a = image_a[372*0: 372*2, 950:1920]
    # image_b = image_b[372*0: 372*2, 950:1920]

    # image_a = image_a[0:1200, 0:1920]
    # image_b = image_b[0:1200, 0:1920]

    # image_a = image_a[0:1490, 0:1920]
    # image_b = image_b[0:1490, 0:1920]

    # cv2.imwrite(os.path.join(SCREEN_SHOT_DIR, "image_diff/crop_image_a.JPEG"), image_a)
    # cv2.imwrite(os.path.join(SCREEN_SHOT_DIR, "image_diff/crop_image_b.JPEG"), image_b)

    image_a = cv2.resize(image_a, (w, h))
    image_b = cv2.resize(image_b, (w, h))

    # resized_orig = cv2.resize(imageA, (700, 800))
    # resized_mod = cv2.resize(imageB, (700, 800))

    # https://www.geeksforgeeks.org/python-grayscaling-of-images-using-opencv/
    # convert the images to grayscale
    gray_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2GRAY)

    # gray_a = cv2.imread(f"{SCREEN_SHOT_DIR}/{first_file}", 0)
    # gray_b = cv2.imread(f"{SCREEN_SHOT_DIR}/{second_file}", 0)

    # compute the Structural Similarity Index (SSIM) between the two
    # images, ensuring that the difference image is returned
    (score, diff) = compare_ssim(gray_a, gray_b, full=True, gaussian_weights=True, data_range=1.5, win_size=15)
    # diff = cv2.imread(f"{SCREEN_SHOT_DIR}/diff.JPEG", 0)
    cv2.imwrite(os.path.join(SCREEN_SHOT_DIR, "image_diff/ssim_diff.JPEG"), diff)

    diff = (diff * 255).astype("uint8")
    print("Structural Similarity Index: {}".format(score))

    cv2.imwrite(os.path.join(SCREEN_SHOT_DIR, "image_diff/diff.JPEG"), diff)

    # threshold the difference image, followed by finding contours to
    # obtain the regions of the two input images that differ
    thresh = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    cv2.imwrite(os.path.join(SCREEN_SHOT_DIR, "image_diff/threshold.JPEG"), diff)

    cnts = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # print(cnts, "before grab")
    cnts = imutils.grab_contours(cnts)
    # print(cnts, "after grab")

    # loop over the contours
    for c in cnts:
        # compute the bounding box of the contour and then draw the
        # bounding box on both input images to represent where the two
        # images differ
        (x, y, w, h) = cv2.boundingRect(c)
        cv2.rectangle(image_a, (x, y), (x + w, y + h), (0, 0, 255), 2)
        cv2.rectangle(image_b, (x, y), (x + w, y + h), (0, 0, 255), 2)

    # show the output images
    # cv2.imshow("Original", imageA)
    # cv2.imshow("Modified", imageB)
    # cv2.imshow("Diff", diff)
    # cv2.imshow("Thresh", thresh)
    # cv2.waitKey(0)

    first_file_name, _ = os.path.splitext(first_file)
    second_file_name, _ = os.path.splitext(second_file)
    f_f_pp = f"image_diff/{first_file_name}.JPEG"
    s_f_pp = f"image_diff/{second_file_name}.JPEG"

    cv2.imwrite(os.path.join(SCREEN_SHOT_DIR, f_f_pp), image_a)
    cv2.imwrite(os.path.join(SCREEN_SHOT_DIR, s_f_pp), image_b)
    return {
        "f_f_pp": f_f_pp,
        "s_f_pp": s_f_pp
    }


def render_local_file_html(file_name):
    async def main():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--window-size=1920,1080',
                    '--no-sandbox',
                    '--disable-setuid-sandbox'
                ]
            )

            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.97 Safari/537.36"
            )

            page = await context.new_page()

            file_path = os.path.join(os.getcwd(), file_name)
            await page.goto(f"file://{file_path}", timeout=0)

            fn, _ = os.path.splitext(file_name)
            file_path = f"{LOCAL_SCREEN_SHOT_DIR}/{fn}.jpeg"

            await page.screenshot(
                path=file_path,
                full_page=True,
            )

            await page.close()
            await browser.close()

            return file_path

    event_loop = asyncio.new_event_loop()

    result = event_loop.run_until_complete(main())
    return result


def render_string_html(html_string, output_file_name):
    async def main():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--window-size=1920,1080',
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            )

            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.97 Safari/537.36" #noqa
            )

            page = await context.new_page()

            await page.set_viewport_size({"width": 1920, "height": 1080})
            await page.goto(f"data:text/html,{html_string}", timeout=0)
            file_path = f"{LOCAL_SCREEN_SHOT_DIR}/{output_file_name}.jpeg"
            await page.screenshot(path=file_path, full_page=True)

            await page.close()
            await browser.close()

            return f"/media/local_file_screenshot/{output_file_name}.jpeg"

    event_loop = asyncio.new_event_loop()

    result = event_loop.run_until_complete(main())
    return result


def fetch_site_url(web_url):
    async def fetch():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--window-size=1920,1080',
                    '--disable-features=site-per-process',
                    '--mute-audio',
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ],
            )

            page = await browser.new_page()
            await page.set_viewport_size({"width": 1920, "height": 1080})
            await page.goto(web_url, timeout=0)
            html = await page.content()
            screenshot = await page.screenshot(
                type="jpeg",
                full_page=True,
                quality=100,
            )

            await page.close()
            await browser.close()

            return {
                "html": html,
                "screenshot": screenshot
            }

    event_loop = asyncio.new_event_loop()
    result = event_loop.run_until_complete(fetch())
    return result
