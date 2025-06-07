
from datetime import datetime


def format_error_msg(error_msg):
    pre_fix = "Error caught on {}".format(datetime.now())
    return f"{pre_fix}\n\n{error_msg}"
