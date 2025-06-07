
import os
import sys
import time
import traceback
from lxml import etree
from os.path import abspath

from django.core.management.base import BaseCommand

from contify.website_tracking.diff_html.sequence_match import (
    diff_files, gen_side_by_side, gen_left_and_right_html, HTMLMatcher,
    utf8_encode, constants
)


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
            '-a', '--accurate-mode', action='store_true', dest='accurate_mode',
            default=False, help='Use accurate mode instead of risky mode'
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
        accurate_mode = options["accurate_mode"]
        output_container = options["output_container"]

        if not os.path.exists(old_file):
            print('Could not find old-file: {0}'.format(old_file))
            sys.exit(1)

        if not os.path.exists(new_file):
            print('Could not find new-file: {0}'.format(new_file))
            sys.exit(1)

        print(f"Using '{accurate_mode}' mode")

        try:
            with open(old_file) as f:
                raw_old_tree = constants.COMMENT_RE.sub('', f.read())

            with open(new_file) as f:
                new_html_str = constants.COMMENT_RE.sub('', f.read())

            matcher = HTMLMatcher(
                utf8_encode(raw_old_tree), utf8_encode(new_html_str),
                accurate_mode
            )
            diffed_html = matcher.diff_html(True)

            # diffed_html = diff_files(old_file, new_file, accurate_mode)
            tmp_out_prefix = f"{file_prefix}/sequence"

            if output_container == "side_by_side":
                diffed_html = gen_side_by_side(diffed_html)
                with open(f"{tmp_out_prefix}/{output_file}", 'w') as f:
                    f.seek(0)
                    f.truncate()
                    f.write(diffed_html)
            else:
                left_html, right_html = gen_left_and_right_html(diffed_html)
                with open(f"{tmp_out_prefix}/left_{output_file}", 'w') as f:
                    f.seek(0)
                    f.truncate()
                    f.write(left_html)

                with open(f"{tmp_out_prefix}/right_{output_file}", 'w') as f:
                    f.seek(0)
                    f.truncate()
                    f.write(right_html)

            print("Added:------: \n", matcher.cfy_added_diff_info, "\n\n")
            print("Removed:-----:\n", matcher.cfy_removed_diff_info)
        except Exception as e:
            print(
                "Diff process exited with an error, traceback: {}".format(
                    traceback.format_exc()
                )
            )
            sys.exit(1)

        sys.stderr.write('Took {0:0.4f} seconds\n'.format(time.time() - t))
