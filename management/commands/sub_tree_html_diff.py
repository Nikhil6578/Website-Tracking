
import os
import sys
import time
import traceback
from io import StringIO
from lxml import etree
from os.path import abspath

from django.core.management.base import BaseCommand

from contify.website_tracking.diff_html.sub_tree_match import (
    CFYDiffer, HTMLFormatter
)
from contify.website_tracking.diff_html.utils import split_html, patch_base_tag


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            "-l", "--old-file", action="store", dest="old_file",
            help="Old HTML file that has to compare with the new Html file"
        )
        parser.add_argument(
            "-r", "--new-file", action="store", dest="new_file",
            help="New HTML file that has to be compare with old html file"
        )
        parser.add_argument(
            '-o', '--output-file', action='store', dest='out_fn', default=None,
            help='[OPTIONAL] Write to given output file instead of stdout'
        )
        parser.add_argument(
            '-b', '--base_url', action='store', dest='base_url',
            help='The base URL of the html content'
        )
        parser.add_argument(
            '-a', '--ratio_mode', action='store', dest='ratio_mode',
            default="accurate", choices={"accurate", "fast", "faster"},
            help='Use accurate mode instead of risky mode'
        )
        parser.add_argument(
            "-F", type=float, action='store', dest='threshold', default=None,
            help=(
                "A value between 0 and 1 that determines how similar nodes "
                "must be to match."
            ),
        )
        parser.add_argument(
            "--fast-match", action="store_true", default=True,
            dest="fast_match", help="A faster, less optimal match run."
        )
        parser.add_argument(
            '--oc', action='store', dest='output_container',
            default="side_by_side", choices={"side_by_side", "individuals"},
            help=(
                'generate the output html separate or side-by-side. '
                'individual generated 2 html files one ie. for left and right'
            )
        )

    def handle(self, *args, **options):
        t = time.time()
        file_prefix = "contify/website_tracking/dist"

        old_file = abspath(f'{file_prefix}/{options["old_file"]}')
        new_file = abspath(f'{file_prefix}/{options["new_file"]}')
        output_file = options["out_fn"] if options["out_fn"] else None

        base_url = options["base_url"]
        ratio_mode = options["ratio_mode"]
        threshold = options["threshold"]
        fast_match = options["fast_match"]
        output_container = options["output_container"]

        if not os.path.exists(old_file):
            print('Could not find old-file: {0}'.format(old_file))
            sys.exit(1)

        if not os.path.exists(new_file):
            print('Could not find new-file: {0}'.format(new_file))
            sys.exit(1)

        print(f"Using ratio_mode: '{ratio_mode}'")
        print(f"Using threshold: '{threshold}'")
        print(f"Using fast_match: '{fast_match}'")

        try:
            diff_options = {
                'ratio_mode': ratio_mode,
                'F': threshold,  # 0.6
                'fast_match': fast_match,
                'uniqueattrs': ['{http://www.w3.org/XML/1998/namespace}id']
            }
            print(f"diff_options: {diff_options}")

            parser = etree.HTMLParser(
                encoding="utf-8",
                remove_comments=True,
                default_doctype=False,
                compact=False,
                # remove_blank_text=True
            )

            with open(old_file) as f:
                old_html_str = f.read()

            with open(new_file) as f:
                new_html_str = f.read()

            raw_old_tree = etree.parse(StringIO(old_html_str), parser)
            patch_base_tag(raw_old_tree, base_url)

            o_start, o_body, o_end = split_html(
                etree.tounicode(raw_old_tree, method="html")
            )

            # _, n_body, _ = split_html(new_html_str)
            _, n_body, _ = split_html(
                etree.tounicode(
                    etree.parse(StringIO(new_html_str), parser), method="html"
                )
            )

            left = etree.parse(StringIO(o_body), parser)
            right = etree.parse(StringIO(n_body), parser)

            formatter = HTMLFormatter(
                normalize=True,
                pretty_print=True
            )  # text_tags=("span", "a", "li", "p", "h1", "h2", "h3", 'h4'))
            formatter.prepare(left, right)

            differ = CFYDiffer(**diff_options)
            diffs = differ.diff(left, right)

            diffed_tree = formatter.format(diffs, left)

            tmp_out_prefix = f"{file_prefix}/sub_tree"

            if output_container == "side_by_side":
                diff_html = formatter.cfy_gen_side_by_side(
                    diffed_tree, o_start, o_end
                )
                with open(f"{tmp_out_prefix}/{output_file}", 'w') as f:
                    f.seek(0)
                    f.truncate()
                    f.write(diff_html)
            else:
                left_html, right_html = formatter.cfy_gen_separate_old_n_new(
                    diffed_tree, o_start, o_end
                )
                with open(f"{tmp_out_prefix}/left_{output_file}", 'w') as f:
                    f.seek(0)
                    f.truncate()
                    f.write(left_html)

                with open(f"{tmp_out_prefix}/right_{output_file}", 'w') as f:
                    f.seek(0)
                    f.truncate()
                    f.write(right_html)
            print("Added:------: \n", formatter.cfy_added_diff_info, "\n\n")
            print("Removed:-----:\n", formatter.cfy_removed_diff_info)
        except Exception as e:
            print(
                "Diff process exited with an error, traceback: {}".format(
                    traceback.format_exc()
                )
            )
            sys.exit(1)

        sys.stderr.write('Took {0:0.4f} seconds\n'.format(time.time() - t))
