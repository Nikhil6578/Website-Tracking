from enum import unique, IntEnum


@unique
class State(IntEnum):
    ACTIVE = 0
    INACTIVE = 1
    BROKEN = 2


@unique
class RunTimeFrequency(IntEnum):
    EVERY_6_HOURS = 0
    EVERY_12_HOURS = 1
    EVERY_24_HOURS = 2


@unique
class SnapshotStatus(IntEnum):
    DRAFT = 0
    # RECENT = 1
    # ARCHIVE = 2
    PROCESSED = 1
    FAILED = 2
    DIFF_TIMEOUT = 3


@unique
class DiffHtmlStatus(IntEnum):
    DRAFT = 0
    PROCESSED = 1
    FAILED = 2


@unique
class DiffKeyMap(IntEnum):
    ADD = 0
    REMOVE = 1
    UPDATE = 2


@unique
class DiffStatus(IntEnum):
    PENDING = 0
    REJECT = 1
    PUBLISHED = 2
