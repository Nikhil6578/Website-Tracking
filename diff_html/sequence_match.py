
import logging
from copy import copy
from difflib import SequenceMatcher
from io import BytesIO

from contify.website_tracking.diff_html import constants
from contify.website_tracking.diff_html.utils import (
    get_spacing, utf8_encode, utf8_decode, strip_tags, split_html,
    whitespacegen
)


logger = logging.getLogger(__name__)


def is_junk(x):
    """
    Used for the faster but less accurate mode.

    :type x: string
    :param x: string to match against
    :returns: regex matched or lowercased x
    """
    return constants.WS_RE.match(x) or x.lower() in constants.STOPWORDS


class TagIter(object):
    """Iterable that returns tags in sequence."""

    def __init__(self, html_string):
        self.html_string = html_string
        self.pos = 0
        self.end_reached = False
        self.buffer = []

    def __iter__(self):
        return self

    def __next__(self):
        if self.buffer:
            return self.buffer.pop(0)

        if self.end_reached:
            raise StopIteration

        match = constants.TAG_RE.search(
            utf8_decode(self.html_string), pos=self.pos
        )
        if not match:
            self.end_reached = True
            return self.html_string[self.pos:]

        self.buffer.append(match.group(0))
        val = self.html_string[self.pos:match.start()]
        self.pos = match.end()
        return val

    def next(self):
        return self.__next__()


class HTMLMatcher(SequenceMatcher):
    """SequenceMatcher for HTML data."""

    start_insert_text = '<span class="insert">'
    end_span_text = '</span>'
    start_delete_text = '<span class="delete">'
    stylesheet = (
        '.insert {\n\tbackground-color: #AFA\n}\n'
        '.delete {\n'
        '\tbackground-color: #F88;\n'
        '\ttext-decoration: line-through;\n'
        '}\n'
        '.tagInsert {\n\tbackground-color: #070;\n\tcolor: #FFF\n}\n'
        '.tagDelete {\n\tbackground-color: #700;\n\tcolor: #FFF\n}\n'
        '.delete img {\n\tborder: 3px solid red !important;\n}\n'
        '.delete input {\n\tborder: 3px solid red !important;\n}\n'
        '.delete embed {\n\tborder: 3px solid red !important;\n}\n'
        '.delete textarea {\n\tborder: 3px solid red !important;\n}\n'
        '.insert img {\n\tborder: 3px solid green !important;\n}\n'
        '.insert input {\n\tborder: 3px solid green !important;\n}\n'
        '.insert embed {\n\tborder: 3px solid green !important;\n}\n'
        '.insert textarea {\n\tborder: 3px solid green !important;\n}\n'
    )

    # stylesheet = (
    #     '.insert {\n\tborder: 2px solid green !important\n}\n'
    #     '.delete {\n''\tborder: 2px solid red !important;\n}\n'
    #     '.tagInsert {\n\tbackground-color: #070;\n\tcolor: #FFF\n}\n'
    #     '.tagDelete {\n\tbackground-color: #700;\n\tcolor: #FFF\n}\n'
    # )

    def __init__(self, source1, source2, accurate_mode):
        logger.debug('Initializing HTMLMatcher...')
        if accurate_mode:
            logger.debug('Using accurate mode')
            super().__init__(lambda x: False, source1, source2, False)
        else:
            logger.debug('Using fast mode')
            super().__init__(is_junk, source1, source2, False)

        self.cfy_added_diff_info = {
            "T": [],
            "I": [],
            "L": []
        }
        self.cfy_removed_diff_info = {
            "T": [],
            "I": [],
            "L": []
        }

    def set_seqs(self, a, b):
        super().set_seqs(self.split_html(a), self.split_html(b))

    def split_html(self, t):
        logger.debug('Splitting html into tag pieces and words')
        result = []
        t = utf8_decode(t)
        for item in TagIter(t):
            if item.startswith('<'):
                result.append(item)
            else:
                result.extend(constants.WORD_RE.findall(item))
        return result

    def diff_html(self, insert_stylesheet=True):
        opcodes = self.get_opcodes()
        a = self.a
        b = self.b
        out = BytesIO()
        for tag, i1, i2, j1, j2 in opcodes:
            logger.debug('Processing opcodes for tag %s', tag)
            if tag == 'equal':
                for item in a[i1:i2]:
                    out.write(utf8_encode(item))

            if tag == 'delete':
                self.text_delete(a[i1:i2], out)

            if tag == 'insert':
                self.text_insert(b[j1:j2], out)

            if tag == 'replace':
                if self.is_invisible_change(a[i1:i2], b[j1:j2]):
                    for item in b[j1:j2]:
                        out.write(utf8_encode(item))
                else:
                    self.text_delete(a[i1:i2], out)
                    self.text_insert(b[j1:j2], out)

        html = out.getvalue()
        out.close()
        if insert_stylesheet:
            html = self.insert_stylesheet(html)
        return html

    @staticmethod
    def is_invisible_change(seq1, seq2):
        logger.debug('Checking if change is visible...')
        if len(seq1) != len(seq2):
            return False

        for i in range(0, len(seq1)):
            if (constants.VOID_TAG_RE.match(seq1[i]) or
                    constants.VOID_TAG_RE.match(seq2[i])):
                return False

            if seq1[i][0] == '<' and seq2[i][0] == '<':
                continue

            if all((
                constants.WS_RE.match(seq1[i]), constants.WS_RE.match(seq2[i])
            )):
                continue

            if seq1[i] != seq2[i]:
                return False
        return True

    def text_delete(self, lst, out):
        text = []
        for item in lst:
            if item.startswith('<'):
                self.out_delete(''.join(text), out)

                if constants.VOID_TAG_RE.match(item):
                    self.out_delete(item, out)

                text = []
            else:
                text.append(item)
        self.out_delete(''.join(text), out)

    def text_insert(self, lst, out):
        text = []
        for item in lst:
            if item.startswith('<'):
                self.out_insert(''.join(text), out)

                if constants.VOID_TAG_RE.match(item):
                    self.out_insert(item, out)
                else:
                    out.write(utf8_encode(item))

                text = []
            else:
                text.append(item)
        self.out_insert(''.join(text), out)

    def out_delete(self, s, out):
        if not s.strip():
            val = s
        else:
            val = ''.join((self.start_delete_text, s, self.end_span_text))

        self.cfy_removed_diff_info["T"].append(val)
        out.write(utf8_encode(val))

    def out_insert(self, s, out):
        if not s.strip():
            val = s
        else:
            val = ''.join((self.start_insert_text, s, self.end_span_text))

        self.cfy_added_diff_info["T"].append(val)
        out.write(utf8_encode(val))

    def insert_stylesheet(self, html, stylesheet=None):
        """
        Add the stylesheet to the given html strings header. Attempt to find
        the head tag and insert it after it, but if it doesn't exist then
        insert at the beginning of the string.

        :type html: str
        :param html: string of html text to add the stylesheet to
        :type stylesheet: str
        :param stylesheet: css stylesheet to include in document header
        :returns: modified html string with stylesheet added to the header
        """
        if not stylesheet:
            stylesheet = self.stylesheet
        logger.debug('Inserting stylesheet...')
        match = constants.HEAD_RE.search(utf8_decode(html))
        pos = match.end() if match else 0
        return ''.join((
            utf8_decode(html[:pos]),
            '\n<style type="text/css">\n',
            stylesheet,
            '</style>',
            utf8_decode(html[pos:]),
        ))


def diff_strings(orig, new, accurate_mode):
    """
    Given two strings of html, return a diffed string.

    :type orig: string
    :param orig: original string for comparison
    :type new: string
    :param new: new string for comparision against original string
    :type accurate_moode: boolean
    :param accurate_moode: use accurate mode or not
    :returns: string containing diffed html
    """
    # Make sure we are dealing with bytes...
    orig = utf8_encode(orig)
    new = utf8_encode(new)
    logger.debug('Beginning to diff strings...')
    h = HTMLMatcher(orig, new, accurate_mode)
    return h.diff_html(True)


def diff_files(initial_path, new_path, accurate_mode):
    """
    Given two files, open them to variables and pass them to diff_strings
    for diffing.

    :type initial_path: object
    :param initial_path: initial file to diff against
    :type new_path: object
    :param new_path: new file to compare to f1
    :type accurate_mode: boolean
    :param accurate_mode: use accurate mode or not
    :returns: string containing diffed html from initial_path and new_path
    """
    # Open the files
    with open(initial_path) as f:
        logger.debug('Reading file: {0}'.format(initial_path))
        source1 = constants.COMMENT_RE.sub('', f.read())

    with open(new_path) as f:
        logger.debug('Reading file: {0}'.format(new_path))
        source2 = constants.COMMENT_RE.sub('', f.read())

    return diff_strings(source1, source2, accurate_mode)


def span_to_whitespace(html_string, span):
    """
    Given an html string and a span tag name, parse the html and find
    the document areas containing those pieces and then replace them
    with nonbreaking whitespace html entities.

    :type html_string: string
    :param html_string: string of html to parse
    :type span: string
    :param string: the span class to parse for
    :returns: html string with specified span replaced with whitespace
    """
    logger.debug('Converting span to whitespace...')
    start = '<span class="{0}">'.format(span)
    stop = '</span>'
    while True:
        logger.debug('Iterating html processing whitespace spans...')
        try:
            s = html_string.index(start)
            f = html_string.index(stop, s) + 7
        except ValueError:
            # No more occurances of this span exist in the file.
            break

        strip = html_string[s:f]
        stripped = strip_tags(strip)
        chars = '<span style="white-space: pre-wrap;">{0}</span>'.format(
            whitespacegen(get_spacing(stripped, 'times new roman'))
        )
        html_string = html_string.replace(strip, chars)
    return html_string


def gen_side_by_side(file_string):
    """
    Given an html file as a string, return a new html file with side by
    side differences displayed in a single html file.

    :type file_string: string
    :param file_string: string of html to convert
    :returns: string of html with side-by-side diffs
    """
    logger.debug('Attempting to generate side-by-side diff from text.')
    container_div = """<div id="container" style="width: 100%;">"""

    orig_div_start = ('<div id="left" style="clear: left; display: inline; '
                      'float: left; width: 47%; border-right: 1px solid black;'
                      ' padding: 10px;">')

    new_div_start = ('<div id="right" style="float: right; width: 47%; '
                     'display: inline; padding: 10px;">')
    div_end = '</div>'
    start, body, ending = split_html(file_string)
    left_side = copy(body)
    right_side = copy(body)
    logger.debug('Converting insert spans to whitespace...')
    left = span_to_whitespace(left_side, 'insert')
    logger.debug('Converting delete spans to whitespace...')
    right = span_to_whitespace(right_side, 'delete')

    # Create side-by-side diff
    sbs_diff = (
        '%(start)s%(container)s%(orig_start)s%(left)s%(div_end)s%(new_start)s'
        '%(right)s%(div_end)s%(ending)s' % {
            'start': start,
            'container': container_div,
            'orig_start': orig_div_start,
            'left': left,
            'div_end': div_end,
            'new_start': new_div_start,
            'right': right,
            'ending': ending

        }
    )
    return sbs_diff


def gen_left_and_right_html(file_string):
    start, body, ending = split_html(file_string)
    left_side = copy(body)
    right_side = copy(body)

    left = span_to_whitespace(left_side, 'insert')
    right = span_to_whitespace(right_side, 'delete')

    left_html = (
        '%(start)s%(left)s%(ending)s' % {
            'start': start,
            'left': left,
            'ending': ending

        }
    )

    right_html = (
            '%(start)s%(right)s%(ending)s' % {
            'start': start,
            'right': right,
            'ending': ending

        }
    )
    return left_html, right_html
