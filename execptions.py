

class MissingSource(Exception):
    """Raised when the ContentSource is missing or inactive in a story"""
    pass


class MissingApprovedDate(Exception):
    """Raised when there is not pub_date in a story object"""
    pass


class NoChangeInWebUpdateEdit(Exception):
    """Raised when there is not any changes in story"""
    pass


class FetchWebSourceError(Exception):
    """Raised when there is any issue in fetching web URL using puppeteer"""
    pass


class WSTTimeoutException(Exception):
    """
    Custom Exception class to help signal based timeout exceptions to propagate.
    """
    pass
