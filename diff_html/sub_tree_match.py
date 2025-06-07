import re
import signal
from copy import deepcopy
from lxml import etree

from xmldiff import utils, actions
from xmldiff.diff import Differ
from xmldiff.formatting import WS_NONE, WS_TEXT, DIFF_PREFIX, DIFF_NS
from xmldiff.formatting import PlaceholderMaker, PlaceholderEntry, XMLFormatter

from contify.website_tracking.diff_html.constants import (
    IGNORE_DIFF_TAGS, EXTRACT_TEXT_XPATH
)
from contify.website_tracking.diff_html.utils import utf8_decode, split_html

WS_RE = re.compile(r'^([ \n\r\t]|&nbsp;)+$')
HEAD_RE = re.compile(r'<\s*head\s*>', re.S | re.I)

DIFF_A = "{http://namespaces.shoobx.com/diff}"
DIFF_I_T = f"{DIFF_A}insert"
DIFF_D_T = f"{DIFF_A}delete"
DIFF_R_T = f"{DIFF_A}rename"
DIFF_U_A = f"{DIFF_A}update-attr"

STYLESHEET = """
    .cfy-ins-text-app, .cfy-ins-tag-app {
        background-color: #6FDC8C !important;
    }
    .cfy-update-tag-attr-app {
        border: 2px solid #6FDC8C !important;
    }
    .cfy-del-text-app, .cfy-del-tag-app {
        background-color: #ffb784 !important;
        text-decoration: line-through;
    }
    /* .cfy-rename-text-app, .cfy-rename-tag-app {
        background-color: #ffb784 !important;
    }
    .tagInsert {
        background-color: #6FDC8C !important;
        color: #6FDC8C !important;
    } */
    img.cfy-del-tag-app {
        border: 3px solid #ffb784 !important;
    }
    input.cfy-del-tag-app {
        border: 3px solid #ffb784 !important;
    }
    embed.cfy-del-tag-app {
        border: 3px solid #ffb784 !important;
    }
    textarea.cfy-del-tag-app {
        border: 3px solid #ffb784 !important;
    }
    img.cfy-ins-tag-app {
        border: 3px solid #6FDC8C !important;
    }
    input.cfy-ins-tag-app {
        border: 3px solid #6FDC8C !important;
    }
    embed.cfy-ins-tag-app {
        border: 3px solid #6FDC8C !important;
    }
    textarea.cfy-ins-tag-app {
        border: 3px solid #6FDC8C !important;
    }
"""


class HTMLPlaceholderMaker(PlaceholderMaker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_placeholder(self, element, ttype, close_ph):
        tag = etree.tounicode(element, method="html")
        ph = self.tag2placeholder.get((tag, ttype, close_ph))
        if ph is not None:
            return ph

        self.placeholder += 1
        ph = chr(self.placeholder)
        self.placeholder2tag[ph] = PlaceholderEntry(element, ttype, close_ph)
        self.tag2placeholder[tag, ttype, close_ph] = ph
        return ph


def remove_empty_chars(node):
    return list(
        filter(str.strip, map(str.strip, node.xpath(EXTRACT_TEXT_XPATH)))
    )


def remove_href_prefix(raw_a_href):
    a_split = raw_a_href.split("href:")
    return a_split[0] if len(a_split) == 1 else a_split[1]


def extract_img_scr_n_href(node):
    img_src_list = node.xpath("descendant-or-self::img/@src")
    a_href_list = [
        remove_href_prefix(href)
        for href in node.xpath("descendant-or-self::a/@href")
    ]
    return img_src_list, a_href_list


class HTMLFormatter(XMLFormatter):
    """
        HTMLFormatter overrides some methods of XMLFormatter
    """

    def __init__(self, normalize=WS_NONE, pretty_print=True, text_tags=(),
                 formatting_tags=()):
        # Mapping from placeholders -> structural content and vice versa.
        super().__init__(normalize=normalize, pretty_print=pretty_print,
                         text_tags=text_tags, formatting_tags=formatting_tags)

        self.normalize = WS_TEXT  # normalize
        self.placeholderer = HTMLPlaceholderMaker(
            text_tags=text_tags, formatting_tags=formatting_tags
        )

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
        # self.__ignore_diff_tags = [
        #     "style", "base", "link", "meta", "script", "noscript", "title",
        #     "head"
        # ]
        self.__ignore_diff_tags = IGNORE_DIFF_TAGS

    def format(self, diff, orig_tree, differ=None):
        # Make a new tree, both because we want to add the diff namespace
        # and also because we don't want to modify the original tree.
        result = deepcopy(orig_tree)

        if isinstance(result, etree._ElementTree):
            root = result.getroot()
        else:
            root = result

        etree.register_namespace(DIFF_PREFIX, DIFF_NS)

        for action in diff:
            if action:
                self.handle_action(action, root)

        self.finalize(root)

        etree.cleanup_namespaces(result, top_nsmap={DIFF_PREFIX: DIFF_NS})
        return result
        # return self.render(result)

    def render(self, result):
        return etree.tounicode(
            result, pretty_print=self.pretty_print, method="html"
        )

    # Cfy custom functions
    @staticmethod
    def cfy_remove_diff_attr(node, remove_all=False):
        if remove_all:
            attr_del = {
                k for k, v in node.attrib.items() if k.startswith(DIFF_A)
            }
        else:
            attr_del = {
                k for k, v in node.attrib.items() if (
                    k.startswith(DIFF_A) and not k.endswith("update-attr")
                )
            }
        for k in attr_del:
            del node.attrib[k]

        if node.tag not in ["a"]:
            for child in node.getchildren():
                HTMLFormatter.cfy_remove_diff_attr(child, remove_all)

    @staticmethod
    def cfy_remove_tag_but_keep_text_n_tail(node, tag_name):
        if node.tag == tag_name:
            tt = (node.text or "" + node.tail or "")
            if tt:
                previous = node.getprevious()
                parent = node.getparent()

                if previous is not None:
                    if previous.tail is None:
                        previous.tail = tt
                    else:
                        previous.tail = previous.tail + tt
                else:
                    if parent.text is None:
                        parent.text = tt
                    else:
                        parent.text = parent.text + tt

                parent.remove(node)

        for child in node.getchildren():
            HTMLFormatter.cfy_remove_tag_but_keep_text_n_tail(child, tag_name)

    @staticmethod
    def _cfy_extend_diff_attr(node, name, value, sep=" "):
        old_value = node.attrib.get(name, "")
        if old_value:
            value = old_value + sep + value
        node.attrib[name] = value

    @staticmethod
    def _cfy_preserve_tail_before_delete(node):
        if node.tail:  # preserve the tail
            previous = node.getprevious()
            if previous is not None:
                # if there is a previous sibling it will get the tail
                if previous.tail is None:
                    previous.tail = node.tail
                else:
                    previous.tail = previous.tail + node.tail
            else:
                # The parent get the tail as text
                parent = node.getparent()
                if parent.text is None:
                    parent.text = node.tail
                else:
                    parent.text = parent.text + node.tail

    # For old file
    def cfy_get_old_diff_tree(self, tree):
        tmp_tree = deepcopy(tree)
        root = tmp_tree.getroot()

        for node in utils.breadth_first_traverse(root):
            # Delete the node which is added or renamed
            if DIFF_I_T in node.attrib or DIFF_R_T in node.attrib or node.tag == DIFF_I_T:
                self._cfy_preserve_tail_before_delete(node)
                parent = node.getparent()
                parent.remove(node)

        for node in utils.breadth_first_traverse(root):
            # Change renamed or inserted element into span and delete its text
            if node.tag == DIFF_R_T:
                node.tag = "span"
                self._cfy_extend_diff_attr(
                    node, "style", "white-space: pre-wrap", ";"
                )
                if node.text:
                    node.text = ""

                self.cfy_remove_diff_attr(node)

        for node in utils.breadth_first_traverse(root):
            if node.tag in self.__ignore_diff_tags:
                continue

            # Keep the deleted tag and add related style
            if DIFF_D_T in node.attrib:
                if node.tag not in ["a", "img"]:
                    if node.tag == "option":
                        self._cfy_extend_diff_attr(
                            node.getparent(), "class", "cfy-update-tag-attr-app"
                        )

                    else:
                        n_tail = (node.tail or "").strip()
                        n_text = (node.text or "").strip()

                        if n_tail or n_text or node.getchildren():
                            self._cfy_extend_diff_attr(
                                node, "class", "cfy-del-tag-app"
                            )

                            dt = remove_empty_chars(node)
                            if dt:
                                self.cfy_removed_diff_info["T"].extend(
                                    ["\n".join(dt)]
                                )
                else:
                    self._cfy_extend_diff_attr(
                        node, "class", "cfy-del-tag-app"
                    )

                # TODO: Remove <diff:delete> from all descendant-or-self::
                img_src_list, a_href_list = extract_img_scr_n_href(node)
                self.cfy_removed_diff_info["I"].extend(img_src_list)
                self.cfy_removed_diff_info["L"].extend(a_href_list)

                self.cfy_remove_tag_but_keep_text_n_tail(node, DIFF_D_T)
                self.cfy_remove_diff_attr(node, True)

        for node in utils.breadth_first_traverse(root):
            if node.tag in self.__ignore_diff_tags:
                continue

            # Rename (for delete text) the deleted tag into span
            if node.tag == DIFF_D_T:
                node.tag = "span"
                if node.tag not in ["a", "img"]:
                    n_tail = (node.tail or "").strip()
                    n_text = (node.text or "").strip()

                    if n_tail or n_text or node.getchildren():
                        self._cfy_extend_diff_attr(
                            node, "class", "cfy-del-text-app"
                        )

                        dt = remove_empty_chars(node)
                        if dt:
                            self.cfy_removed_diff_info["T"].extend(
                                ["\n".join(dt)]
                            )

                        self.cfy_remove_diff_attr(node)
                else:
                    self._cfy_extend_diff_attr(
                        node, "class", "cfy-del-text-app"
                    )
                    self.cfy_remove_diff_attr(node)

                img_src_list, a_href_list = extract_img_scr_n_href(node)
                self.cfy_removed_diff_info["I"].extend(img_src_list)
                self.cfy_removed_diff_info["L"].extend(a_href_list)

            # self.cfy_remove_diff_attr(node)
        return tmp_tree

    # For new file
    def cfy_get_new_diff_tree(self, tree):
        tmp_tree = deepcopy(tree)
        root = tmp_tree.getroot()
        for node in utils.breadth_first_traverse(root):
            if DIFF_D_T in node.attrib or node.tag == DIFF_D_T:
                # Remove the node
                self._cfy_preserve_tail_before_delete(node)
                parent = node.getparent()
                parent.remove(node)

        for node in utils.breadth_first_traverse(root):
            if DIFF_R_T in node.attrib:
                self._cfy_extend_diff_attr(node, "class", "cfy-rename-tag-app")
                # self.cfy_diff_text["R"].extend(remove_empty_chars(node))
                self.cfy_remove_diff_attr(node)

            # Rename deleted tag into span and add the related style
            elif node.tag == DIFF_R_T:
                node.tag = "span"
                self._cfy_extend_diff_attr(
                    node, "class", "cfy-rename-text-app"
                )
                # self.cfy_diff_text["R"].extend(remove_empty_chars(node))
                self.cfy_remove_diff_attr(node)

        for node in utils.breadth_first_traverse(root):
            if node.tag in self.__ignore_diff_tags:
                continue

            if DIFF_U_A in node.attrib and "href:" in node.attrib[DIFF_U_A]:
                if node.getchildren():
                    self._cfy_extend_diff_attr(
                        node, "class", "cfy-ins-tag-app"
                    )
                else:
                    self._cfy_extend_diff_attr(
                        node, "class", "cfy-update-tag-attr-app"
                    )

                self.cfy_added_diff_info["L"].extend(
                    [remove_href_prefix(node.attrib[DIFF_U_A])]
                )
                # self.cfy_diff_text["U"].extend([node.attrib[DIFF_U_A]])
                self.cfy_remove_diff_attr(node)

            elif DIFF_U_A in node.attrib and "src:" in node.attrib[DIFF_U_A]:
                self._cfy_extend_diff_attr(
                    node, "class", "cfy-update-tag-attr-app"
                )
                self.cfy_added_diff_info["I"].extend([node.attrib[DIFF_U_A]])
                # self.cfy_diff_text["U"].extend([node.attrib[DIFF_U_A]])
                self.cfy_remove_diff_attr(node)

        for node in utils.breadth_first_traverse(root):
            if node.tag in self.__ignore_diff_tags:
                continue

            if DIFF_I_T in node.attrib:
                if node.tag not in ["a", "img"]:
                    if node.tag == "option":
                        self._cfy_extend_diff_attr(
                            node.getparent(), "class", "cfy-update-tag-attr-app"
                        )

                    else:
                        n_tail = (node.tail or "").strip()
                        n_text = (node.text or "").strip()

                        if n_tail or n_text or node.getchildren():
                            self._cfy_extend_diff_attr(
                                node, "class", "cfy-ins-tag-app"
                            )

                            dt = remove_empty_chars(node)
                            if dt:
                                self.cfy_added_diff_info["T"].extend(
                                    ["\n".join(dt)]
                                )
                else:
                    self._cfy_extend_diff_attr(
                        node, "class", "cfy-ins-tag-app"
                    )

                img_src_list, a_href_list = extract_img_scr_n_href(node)
                self.cfy_added_diff_info["I"].extend(img_src_list)
                self.cfy_added_diff_info["L"].extend(a_href_list)

                self.cfy_remove_tag_but_keep_text_n_tail(node, DIFF_I_T)
                self.cfy_remove_diff_attr(node, True)

        for node in utils.breadth_first_traverse(root):
            if node.tag in self.__ignore_diff_tags:
                continue

            # Keep the element which is for inserted text
            if node.tag == DIFF_I_T:
                n_tail = (node.tail or "").strip()
                n_text = (node.text or "").strip()

                if n_tail or n_text or node.getchildren():
                    node.tag = "span"

                    self._cfy_extend_diff_attr(
                        node, "class", "cfy-ins-text-app"
                    )

                    dt = remove_empty_chars(node)
                    if dt:
                        self.cfy_added_diff_info["T"].extend(
                            ["\n".join(dt)]
                        )

                img_src_list, a_href_list = extract_img_scr_n_href(node)
                self.cfy_added_diff_info["I"].extend(img_src_list)
                self.cfy_added_diff_info["L"].extend(a_href_list)

                self.cfy_remove_diff_attr(node)

            # self.cfy_remove_diff_attr(node)
        return tmp_tree

    @staticmethod
    def cfy_insert_stylesheet(html, stylesheet=None):
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
            stylesheet = STYLESHEET

        match = HEAD_RE.search(utf8_decode(html))
        pos = match.end() if match else 0

        return ''.join((
            utf8_decode(html[:pos]),
            '\n<style type="text/css">\n',
            stylesheet,
            '</style>',
            utf8_decode(html[pos:]),
        ))

    def cfy_gen_side_by_side(self, tree, h_start, h_end):
        container_div = """<div id="container" style="width: 100%;">"""

        orig_div_start = (
            '<div id="left" style="clear: left; display: inline; float: left; '
            'width: 47%; border-right: 1px solid black; padding: 10px;">'
        )

        new_div_start = (
            '<div id="right" style="float: right; width: 47%; display: inline;'
            ' padding: 10px;">'
        )
        div_end = '</div>'

        hlt = self.cfy_get_old_diff_tree(tree)
        _, left, _ = split_html(etree.tounicode(hlt, method="html"))
        left = left.replace("&amp;nbsp;", "&nbsp;")

        hrt = self.cfy_get_new_diff_tree(tree)
        _, right, _ = split_html(etree.tounicode(hrt, method="html"))
        right = right.replace("&amp;nbsp;", "&nbsp;")

        # Create side-by-side diff
        sbs_diff = (
            '%(start)s%(container)s%(orig_start)s%(left)s%(div_end)s'
            '%(new_start)s%(right)s%(div_end)s%(ending)s' % {
                'start': h_start,
                'container': container_div,
                'orig_start': orig_div_start,
                'left': left,
                'div_end': div_end,
                'new_start': new_div_start,
                'right': right,
                'ending': h_end
            }
        )
        return self.cfy_insert_stylesheet(sbs_diff)

    def cfy_gen_separate_old_n_new(self, tree, h_start, h_end):
        hlt = self.cfy_get_old_diff_tree(tree)
        _, left, _ = split_html(etree.tounicode(hlt, method="html"))
        left = left.replace("&amp;nbsp;", "&nbsp;")

        hrt = self.cfy_get_new_diff_tree(tree)
        _, right, _ = split_html(etree.tounicode(hrt, method="html"))
        right = right.replace("&amp;nbsp;", "&nbsp;")

        left_html = self.cfy_insert_stylesheet(
            '%(start)s%(left)s%(ending)s' % {
                'start': h_start,
                'left': left,
                'ending': h_end

            }
        )

        right_html = self.cfy_insert_stylesheet(
            '%(start)s%(right)s%(ending)s' % {
                'start': h_start,
                'right': right,
                'ending': h_end

            }
        )
        return left_html, right_html


def get_to_string(node):
    return node, etree.tounicode(node, method="html").strip()


class CFYDiffer(Differ):
    def __init__(self, *args, **kwargs):
        self.__ratio_mode = kwargs.get("ratio_mode") or args[2]
        super().__init__(*args, **kwargs)

        self.__tag_text = ["option", "label"]
        if self.__ratio_mode == "fast":
            self.__tag_text.extend(["p", "h1", "h2", "h3", "h4", "h5"])

        self.__ignore_diff_tags = IGNORE_DIFF_TAGS

    def timeout_handler(self, signum, frame):
        """Signal handler for timeout"""
        raise TimeoutError("Match operation timed out")

    def match(self, left=None, right=None):
        signal.signal(signal.SIGALRM, self.timeout_handler)
        signal.alarm(300)
        # This is not a generator, because the diff() functions needs
        # _l2rmap and _r2lmap, so if match() was a generator, then
        # diff() would have to first do list(self.match()) without storing
        # the result, and that would be silly.

        # Nothing in this library is actually using the resulting list of
        # matches match() returns, but it may be useful for somebody that
        # actually do not want a diff, but only a list of matches.
        # It also makes testing the match function easier.

        if left is not None or right is not None:
            self.set_trees(left, right)

        if self._matches is not None:
            # We already matched these sequences, use the cache
            return self._matches

        # Initialize the caches:
        self._matches = []
        self._l2rmap = {}
        self._r2lmap = {}
        self._inorder = set()
        self._text_cache = {}

        # Generate the node lists
        lnodes = list(utils.post_order_traverse(self.left))
        rnodes = list(utils.post_order_traverse(self.right))

        # TODO: If the roots do not match, we should create new roots, and
        # have the old roots be children of the new roots, but let's skip
        # that for now, we don't need it. That's strictly a part of the
        # insert phase, but hey, even the paper defining the phases
        # ignores the phases, so...
        # For now, just make sure the roots are matched, we do that by
        # removing them from the lists of nodes, so it can't match, and add
        # them back last.
        lnodes.remove(self.left)
        rnodes.remove(self.right)

        if self.fast_match:
            # First find matches with longest_common_subsequence:
            matches = list(utils.longest_common_subsequence(
                lnodes, rnodes, lambda x, y: self.node_ratio(x, y) >= 0.5))
            # Add the matches (I prefer this from start to finish):
            for left_match, right_match in matches:
                self.append_match(lnodes[left_match],
                                  rnodes[right_match],
                                  None)
            # Then remove the nodes (needs to be done backwards):
            for left_match, right_match in reversed(matches):
                lnode = lnodes.pop(left_match)
                rnode = rnodes.pop(right_match)

        for lnode in lnodes:
            if lnode.tag in self.__ignore_diff_tags:
                continue

            max_match = 0
            match_node = None

            for rnode in rnodes:
                if rnode.tag in self.__ignore_diff_tags:
                    continue

                match = self.node_ratio(lnode, rnode)
                if match > max_match:
                    match_node = rnode
                    max_match = match

                # Try to shortcut for nodes that are not only equal but also
                # in the same place in the tree
                if match == 1.0:
                    # This is a total match, break here
                    break

            if max_match >= self.F:
                self.append_match(lnode, match_node, max_match)

                # We don't want to check nodes that already are matched
                if match_node is not None:
                    rnodes.remove(match_node)

        # Match the roots
        self.append_match(self.left, self.right, 1.0)
        signal.alarm(0)
        return self._matches

    def node_ratio(self, left, right):
        if left.tag == "img" or right.tag == "img":
            # TODO: Do we need to check the alt text
            clean_left_src_url = (
                left.attrib.get('src').split('?')[0]
                if left.attrib.get('src') else None
            )
            clean_right_src_url = (
                right.attrib.get('src').split('?')[0]
                if right.attrib.get('src') else None
            )
            if clean_left_src_url != clean_right_src_url:
                return 0

        if left.tag == "a" or right.tag == "a":
            if left.attrib.get("href") != right.attrib.get("href") and left.text != right.text:
                return 0

        if left.tag == "option" or right.tag == "option":
            if left.text != right.text:
                return 0

        if left.tag == "label" or right.tag == "label":
            if left.text != right.text:
                return 0

        if (left.tag in ["input", "textarea"] or
                right.tag in ["input", "textarea"]):
            if left.tag != right.tag:
                return 0
            left_html = etree.tounicode(left, method="html").strip()
            right_html = etree.tounicode(right, method="html").strip()
            return left_html == right_html

        if left.tag is etree.Comment or right.tag is etree.Comment:
            if left.tag is etree.Comment and right.tag is etree.Comment:
                # comments
                self._sequencematcher.set_seqs(left.text, right.text)
                return self._sequence_ratio()
            # One is a comment the other is not:
            return 0

        for attr in self.uniqueattrs:
            if not isinstance(attr, str):
                # If it's actually a sequence of (tag, attr), the tags must
                # match first.
                tag, attr = attr
                if tag != left.tag or tag != right.tag:
                    continue

            if attr in left.attrib or attr in right.attrib:
                # One of the nodes have a unique attribute, we check only that.
                # If only one node has it, it means they are not the same.
                return int(left.attrib.get(attr) == right.attrib.get(attr))

        match = self.leaf_ratio(left, right)
        child_ratio = self.child_ratio(left, right)

        if child_ratio is not None:
            match = (match + child_ratio) / 2
        return match

    def node_text(self, node):
        if node in self._text_cache:
            return self._text_cache[node]

        if node.tag in self.__ignore_diff_tags:
            text = ""

        elif node.tag in self.__tag_text:
            text = node.text

        # elif node.tag == "img":
        #     text = "<img src={}/>".format(node.attrib.get("src"))
        #
        # elif node.tag == "a":
        #     text = "<a href={}>{}</a>".format(
        #         node.attrib.get("href"), node.text
        #     )
        else:
            # tmp_node = deepcopy(node)
            # for ra in ["id", "style", "class"]:
            #     for tn in tmp_node.xpath(f"descendant-or-self::*[@{ra}]"):
            #         tn.attrib.pop(ra)
            # text = etree.tounicode(tmp_node, method="html").strip()

            # Get the texts and the tag as a start
            # Then add attributes and values

            # if node.tag == "a" and node.getchildren():
            #     text = etree.tounicode(node, method="html").strip()
            # else:
            if node.tag == "img":
                texts = ["<img src={}/>".format(node.attrib.get("src"))]
            elif node.tag == "a":
                texts = ["<a href={}>{}</a>".format(node.attrib.get("href"), node.text)]
            else:
                texts = node.xpath('text()')

            for tag, value in sorted(node.attrib.items()):
                if tag[0] == '{':
                    tag = tag.split('}',)[-1]
                texts.append('%s:%s' % (tag, value))

            text = u' '.join(texts).strip()

        result = utils.cleanup_whitespace(text or "")
        self._text_cache[node] = result
        return result

    def diff(self, left=None, right=None):
        # Make sure the matching is done first, diff() needs the l2r/r2l maps.
        if not self._matches:
            self.match(left, right)

        # The paper talks about the five phases, and then does four of them
        # in one phase, in a different order that described. This
        # implementation in turn differs in order yet again.
        ltree = self.left.getroottree()

        ignored_node_descendants = []
        for rnode in utils.breadth_first_traverse(self.right):

            if self.is_junk_in_url_patterns(rnode):
                continue

            if rnode.tag in self.__ignore_diff_tags:
                ignored_node_descendants.extend(rnode.iterdescendants())
                continue

            # if ancestor of a node is already being ignored then no point to checking the difference for that node
            if rnode in ignored_node_descendants:
                continue

            # (a)
            rparent = rnode.getparent()

            ltarget = self._r2lmap.get(id(rparent)) # if None, means no match found for the parent of rnode in left tree

            # (b) Insert
            if id(rnode) not in self._r2lmap:  # means no match found for this node in the left tree
                # (i)
                pos = self.find_pos(rnode)
                # (ii)
                if rnode.tag is etree.Comment:
                    yield actions.InsertComment(
                        utils.getpath(ltarget, ltree), pos, rnode.text)
                    lnode = etree.Comment(rnode.text)
                else:
                    yield actions.InsertNode(utils.getpath(ltarget, ltree),
                                             rnode.tag, pos)
                    lnode = ltarget.makeelement(rnode.tag)

                    # (iii)
                self.append_match(lnode, rnode, 1.0)
                ltarget.insert(pos, lnode)
                self._inorder.add(lnode)
                self._inorder.add(rnode)
                # And then we update attributes. This is different from the
                # paper, because the paper assumes nodes only has labels and
                # values. Nodes also has texts, we do them later.
                for action in self.update_node_attr(lnode, rnode):
                    yield action

            # (c)
            else:
                # Normally there is a check that rnode isn't a root,
                # but that's perhaps only because comparing valueless
                # roots is pointless, but in an elementtree we have no such
                # thing as a valueless root anyway.
                # (i)
                lnode = self._r2lmap[id(rnode)]

                # (iii) Move
                lparent = lnode.getparent()
                if ltarget is not lparent:
                    pos = self.find_pos(rnode)
                    yield actions.MoveNode(
                        utils.getpath(lnode, ltree),
                        utils.getpath(ltarget, ltree),
                        pos)
                    # Move the node from current parent to target
                    lparent.remove(lnode)
                    ltarget.insert(pos, lnode)
                    self._inorder.add(lnode)
                    self._inorder.add(rnode)

                # Rename
                for action in self.update_node_tag(lnode, rnode):
                    yield action

                # (ii) Update
                # XXX If they are exactly equal, we can skip this,
                # maybe store match results in a cache?
                for action in self.update_node_attr(lnode, rnode):
                    yield action

            # (d) Align
            for action in self.align_children(lnode, rnode):
                yield action

            # And lastly, we update all node texts. We do this after
            # aligning children, because when you generate an XML diff
            # from this, that XML diff update generates more children,
            # confusing later inserts or deletes.
            lnode = self._r2lmap[id(rnode)]
            for action in self.update_node_text(lnode, rnode):
                yield action

        for lnode in utils.reverse_post_order_traverse(self.left):

            if self.is_junk_in_url_patterns(lnode):
                continue

            if lnode.tag in self.__ignore_diff_tags:
                continue

            lparent = lnode.getparent()
            if lparent is not None and lparent.tag in self.__ignore_diff_tags:
                continue

            if id(lnode) not in self._l2rmap:
                # No match
                yield actions.DeleteNode(utils.getpath(lnode, ltree))
                lnode.getparent().remove(lnode)


    def is_junk_in_url_patterns(self, node):
        """
        Determines whether a given HTML/XML node is considered "junk" based on known tracking or advertising domains and
        invisible or zero-dimension attributes.

        This method checks for common junk sources in attributes such as 'src' and 'href' for <script>, <link>, <iframe>, and
        <img> tags. It also examines style attributes and dimensions (width and height) that indicate invisible elements,
        which are typical of tracking beacons, analytics scripts, and other hidden resources.

        The logic includes:
        - Matching known junk domains (e.g., doubleclick.net, bing.net etc.).
        - Identifying invisible or zero-dimension iframes.
        - Checking for tracking pixels in <img> tags with invisible or zero-dimension styles or attributes.
        - Detecting invisible <div> or <span> elements that contain tracking <img> elements.

        Returns:
            bool: True if the node is considered junk (i.e., likely to be a tracking pixel, ad script, or invisible beacon),
            False otherwise.

        This is particularly useful for ignoring meaningless differences in HTML content when performing DOM diffing.
        """

        junk_sources = [
            "bat.bing.com",
            "bat.bing.net",
            "td.doubleclick.net",
            "doubleclick.net",
            "googleadservices.com",
            "pixel.wp.com",
            "googlesyndication.com",
            "analytics.twitter.com",
            "google-analytics.com",
            "images/blank.png",
            "bat.bing"
        ]

        def has_junk_src(attr):
            return any(domain in attr.lower() for domain in junk_sources)

        def has_invisible_style(style):
            style = style.lower()
            invisible_conditions = [
                "display:none", "display: none",
                "visibility:hidden", "visibility: hidden",
                "width:0", "width: 0", "width:0px", "width: 0px",
                "height:0", "height: 0", "height:0px", "height: 0px"
            ]
            return any(cond in style for cond in invisible_conditions)

        def has_zero_dimension(attr):
            attr = attr.strip()
            return attr == "0" or attr.startswith("0")

        tag = node.tag.lower()

        if tag in {"script", "link", "iframe", "a", "img"}:
            src = node.attrib.get("src", "")
            href = node.attrib.get("href", "")
            if has_junk_src(src) or has_junk_src(href):
                return True

        if tag in {"img", "iframe", "div", "span"}:
            style = node.attrib.get("style", "").lower()
            width = node.attrib.get("width", "").strip()
            height = node.attrib.get("height", "").strip()

            if has_invisible_style(style) or has_zero_dimension(width) or has_zero_dimension(height):
                if tag in {"div", "span"}:
                    for child in node.iterdescendants():
                        if child.tag.lower() == "img":
                            if has_junk_src(child.attrib.get("src", "")):
                                return True
                return True

        return False