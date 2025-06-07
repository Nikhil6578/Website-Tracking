"""Constants."""
import re

COMMENT_RE = re.compile(r'<!--.*?-->', re.S)
TAG_RE = re.compile(r'<script.*?>.*?</script>|<.*?>', re.S)
HEAD_RE = re.compile(r'<\s*head\s*>', re.S | re.I)
WS_RE = re.compile(r'^([ \n\r\t]|&nbsp;)+$')
WORD_RE = re.compile(
    r'([^ \n\r\t,.&;/#=<>()-]+|(?:[ \n\r\t]|&nbsp;)+|[,.&;/#=<>()-])'
)

VOID_TAG_RE = re.compile(r"^<(\s+)?(img|input|embed)(\s+).+>", re.I | re.M)

STOPWORDS = (
    'a',
    'about',
    'an',
    'and',
    'are',
    'as',
    'at',
    'be',
    'by',
    'for',
    'from',
    'have',
    'how',
    'I',
    'in',
    'is',
    'it',
    'of',
    'on',
    'or',
    'that',
    'the',
    'this',
    'to',
    'was',
    'what',
    'when',
    'where',
    'who',
    'will',
    'with',
)


#  Default
times_new_roman = {
    'a': 36,
    'b': 41,
    'c': 36,
    'd': 41,
    'e': 36,
    'f': 35,
    'g': 41,
    'h': 41,
    'i': 23,
    'j': 23,
    'k': 41,
    'l': 23,
    'm': 64,
    'n': 41,
    'o': 41,
    'p': 41,
    'q': 41,
    'r': 27,
    's': 32,
    't': 23,
    'u': 41,
    'v': 41,
    'w': 58,
    'x': 41,
    'y': 51,
    'z': 36,
    'A': 59,
    'B': 50,
    'C': 50,
    'D': 58,
    'E': 50,
    'F': 45,
    'G': 59,
    'H': 59,
    'I': 27,
    'J': 32,
    'K': 58,
    'L': 50,
    'M': 72,
    'N': 58,
    'O': 58,
    'P': 45,
    'Q': 58,
    'R': 55,
    'S': 45,
    'T': 50,
    'U': 58,
    'V': 58,
    'W': 76,
    'X': 58,
    'Y': 58,
    'Z': 50,
    '0': 41,
    '1': 41,
    '2': 41,
    '3': 41,
    '4': 41,
    '5': 41,
    '6': 41,
    '7': 41,
    '8': 41,
    '9': 41,
    ' ': 20,
    'non-breaking-space': 15,
}
fonts = {
    'times new roman': times_new_roman
}


IGNORE_DIFF_TAGS = [
    "style", "base", "link", "meta", "script", "noscript", "title",
    "head", "svg", "defs", "polygon", "rect", "path"
]


EXTRACT_TEXT_XPATH = (
    "descendant-or-self::*[not({})]/text()".format(
        " or ".join(map(lambda i: "self::" + i, IGNORE_DIFF_TAGS))
    )
)
