"""
Supported service base definitions.
"""


from tornado import web


class HookHandler(web.RequestHandler):
    """
    Base class for web hook handlers
    """

    def get(self):
        raise NotImplementedError()

    def post(self):
        raise NotImplementedError()
