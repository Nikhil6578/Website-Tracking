
import logging
import six
from html.parser import HTMLParser

from contify.website_tracking.diff_html.constants import fonts


logger = logging.getLogger(__name__)


def get_spacing(string, font_type):
    """
    Given a string & font, return approximate spacing for making more
    appropriate whitespace than would be normally generated from just
    counting characters and replacing with spaces. Currently only has a
    lookup table for Times New Roman. Possible to add support for others
    at a later point just by adding them to the font dictionary. The
    font sizes are an arbitrary unit and merely approximates.

    This will get things closer, but outside of test rendering each and
    manually calculating space and doing the conversion with that, this
    at least works to get you close.

    :type string: string
    :param string: a string to calculate whitespace for
    :type font_tye: string
    :param font_type: type of font to calculate space for
    :returns: space characters to use
    :rtype: int
    """

    if font_type not in fonts.keys():
        raise Exception('Unsupported font type specified')

    lookup_table = fonts[font_type]
    whitespace = 0
    for character in lookup_table.keys():
        occurs = string.count(character)
        ws = occurs * lookup_table[character]
        whitespace = whitespace + ws

    spaces = whitespace / lookup_table['non-breaking-space']
    spaces = round(spaces, 0)
    return int(spaces)


def utf8_encode(val):
    """Return a string in bytes; use utf-8 for unicode strings."""
    if isinstance(val, six.text_type):
        return val.encode('utf-8')
    elif isinstance(val, six.binary_type):
        return val
    else:
        raise TypeError('{} is not a unicode or str object'.format(val))


def utf8_decode(val):
    """Return a string."""
    if isinstance(val, six.text_type):
        return val
    elif isinstance(val, six.binary_type):
        return val.decode('utf-8')
    else:
        raise TypeError('{} is not a unicode or str object'.format(val))


def whitespacegen(spaces):
    """
    From a certain number of spaces, provide an html entity for non breaking
    spaces in an html document.

    :type spaces: integer
    :param spaces: Number of html space entities to return as string
    :returns: string containing html space entities (&nbsp;) wrapped in
              a html span that properly wraps the whitespace.
    """
    # The average length of a word is 5 letters.. I guess
    words = spaces / 5
    s = '&nbsp;&nbsp;&nbsp;&nbsp; ' * int(words)

    # s = " " * spaces
    return '{0}'.format(s)


class TagStrip(HTMLParser):
    """
    Subclass of HTMLParser used to strip html tags from strings
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.reset()
        self.fed = []
        self.convert_charrefs = False

    def handle_data(self, s):
        self.fed.append(s)

    def get_stripped_string(self):
        return ''.join(self.fed)


def strip_tags(html_string):
    """
    Remove all HTML tags from a given string of html

    :type html_string: string
    :param html_string: string of html
    :return: intial string stripped of html tags
    """
    logger.debug('Stripping tags from "%s"', html_string)
    st = TagStrip()
    st.feed(html_string)
    stripped = st.get_stripped_string()
    return stripped


def split_html(html_string):
    """
    Divides an html document into three seperate strings and returns
    each of these. The first part is everything up to and including the
    <body> tag. The next is everything inside of the body tags. The
    third is everything outside of and including the </body> tag.

    :type html_string: string
    :param html_string: html document in string form
    :returns: three strings start, body, and ending
    """
    logger.debug('Splitting html into start, body, end...')
    try:
        i = html_string.index('<body')
        # j = html_string.index('>', i) + 1
        k = html_string.index('</body')
        k += 7  # len(<body>)
    except ValueError:
        #print(html_string)
        raise ValueError('This is not a full html document.')
    # start = html_string[:j]
    # body = html_string[j:k]
    # ending = html_string[k:]

    start = html_string[:i]
    body = html_string[i:k]
    ending = html_string[k:]
    return start, body, ending


def patch_base_tag(html_tree, base_url):
    if html_tree.find("head") is None:
        html_tree_root = html_tree.getroot()
        html_tree_head = html_tree_root.makeelement("head")
        html_tree_head.text = "\n"
        html_tree_root.insert(0, html_tree_head)
    else:
        html_tree_head = html_tree.find("head")

    if html_tree_head.find("base") is None:
        base_tag = html_tree_head.makeelement("base")
        base_tag.attrib["href"] = base_url
        base_tag.attrib["target"] = "_blank"
        html_tree_head.insert(0, base_tag)
