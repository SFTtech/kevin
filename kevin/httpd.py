"""
Web server to receive WebHook notifications from GitHub,
and provide them in a job queue.
"""

import asyncio
from abc import abstractmethod
import logging

from tornado import websocket, web, httpserver

from .config import CFG
from .trigger import Trigger
from .update import (
    JobCreated, JobUpdate, JobState,
    StdOut, BuildState, BuildSource, RequestError
)
from .watcher import Watcher


class HookTrigger(Trigger):
    """
    Base class for a webhook trigger (e.g. the "github notifies us" thingy).
    """

    @abstractmethod
    def get_handler(self):
        """
        Return the (url, HookHandler class) to register at tornado for webhooks
        """
        pass

    def merge_cfg(self, urlhandlers):
        # when e.g. GitHubHookHandler is instanciated,
        # the list of all Triggers that use it
        # will be passed as configuration.

        # create an entry in the defaultdict for this
        # hook handler class, e.g. GitHubHookHandler.
        handlerkwargs = urlhandlers[self.get_handler()]

        # and add the config which requested it to the list
        # this step creates the mandatory "triggers" constructor
        # argument for all HookHandlers.
        handlerkwargs["triggers"].append(self)

        # additional custom keyword arguments for this
        urlhandlers[self.get_handler()] = handlerkwargs


class HookHandler(web.RequestHandler):
    """
    Base class for web hook handlers.
    A web hook is a http request made by e.g. github, gitlab, ...
    and notify kevin that there's a job to do.
    """

    def initialize(self, triggers):
        """
        triggers: a list of HookTriggers which requested to instanciate
                  this HookHandler
        """
        raise NotImplementedError()

    async def get(self):
        raise NotImplementedError()

    async def post(self):
        raise NotImplementedError()

    def data_received(self, chunk):
        del chunk


class HTTPD:
    """
    This class contains a server that listens for WebHook
    notifications to spawn triggered actions, e.g. new Builds.
    It also provides the websocket API and plain log streams for curl.
    """

    def __init__(self, external_handlers, queue, build_manager):
        """
        external_handlers: (url, handlercls) -> [cfg, cfg, ...]
        queue: the jobqueue.Queue where new builds/jobs are put in
        build_manager: build_manager.BuildManager for caching
                       and restoring buils from disk.
        """

        # this dict will be the kwargs of each urlhandler
        handler_args = {
            "queue": queue,
            "build_manager": build_manager,
        }

        # url handler configuration dict
        # (urlmatch, requesthandlerclass) => custom_args
        urlhandlers = dict()
        urlhandlers[("/", PlainStreamHandler)] = None
        urlhandlers[("/ws", WebSocketHandler)] = None
        urlhandlers[("/robots.txt", RobotsHandler)] = None

        urlhandlers.update(external_handlers)

        # create the tornado application
        # that serves assigned urls to handlers:
        # [web.URLSpec(match, handler, kwargs)]
        handlers = list()
        for (url, handler), custom_args in urlhandlers.items():

            # custom handlers may have custom kwargs
            if custom_args is not None:
                kwargs = handler_args.copy()
                kwargs.update(custom_args)
                handler = web.URLSpec(url, handler, kwargs)

            else:
                handler = web.URLSpec(url, handler, handler_args)

            handlers.append(handler)

        app = web.Application(handlers)

        # bind http server to tcp port
        self.server = httpserver.HTTPServer(app)
        self.server.listen(port=CFG.dyn_port, address=str(CFG.dyn_address))

    async def stop(self):
        """
        Stop listening for http requests
        """
        # TODO: when tornado's HTTPServer::stop is async, await it
        self.server.stop()


class WebSocketHandler(websocket.WebSocketHandler, Watcher):
    """ Provides a job description stream via WebSocket """

    def initialize(self, queue, build_manager):
        del queue  # unused

        self.build_manager = build_manager
        self.build = None
        self.filter_ = None

    def select_subprotocol(self, subprotocols):
        # sync this with mandy.js and other websocket clients
        preferred = "mandy_v0"
        if preferred not in subprotocols:
            return None
        return preferred

    async def open(self):
        project = CFG.projects[self.get_parameter("project")]
        build_id = self.get_parameter("hash")
        try:
            self.build = await self.build_manager.get_build(project, build_id)

            if not self.build:
                logging.warning(f"unknown build {build_id} "
                                f"of {project} requested.")

                self.send_error("Unknown build requested.")
                return

            def get_filter(filter_def):
                """
                Returns a filter function from the filter definition string.
                """
                if not filter_def:
                    return lambda _: True

                job_names = filter_def.split(",")
                return lambda job_name: job_name in job_names

            # state_filter specifies which JobState updates to forward.
            self.state_filter = get_filter(self.get_parameter("state_filter"))
            # filter_ specifies which JobUpdate updates to forward.
            # (except for JobState updates, which are treated by the filter above).
            self.filter_ = get_filter(self.get_parameter("filter"))

            await self.build.register_watcher(self)

            # trigger the disk-load, if necessary
            await self.build.reconstruct_jobs(self.get_parameter("filter"))

        except Exception as exc:
            self.send_error(f"Error: {exc}")
            return

    def get_parameter(self, name, default=None):
        """
        Returns the string value of the URL parameter with the given name.
        """
        try:
            parameter, = self.request.query_arguments[name]
        except (KeyError, ValueError):
            return default
        else:
            return parameter.decode()

    def send_error(self, msg):
        """
        Send an error message to the websocket client.
        """
        msg = RequestError(msg)
        self.write_message(msg.json())

    def on_close(self):
        if self.build is not None:
            self.build.deregister_watcher(self)

    async def on_message(self, message):
        # TODO: handle user messages
        pass

    async def on_update(self, update):
        """
        Called by the watched build when an update arrives.
        """
        if update is StopIteration:
            self.close()
            return

        if isinstance(update, JobUpdate):
            if isinstance(update, JobCreated):
                # those are not interesting for the webinterface.
                return
            if isinstance(update, JobState):
                filter_ = self.state_filter
            else:
                filter_ = self.filter_

            if filter_(update.job_name):
                self.write_message(update.json())

        elif isinstance(update, (BuildState, BuildSource)):
            # these build-specific updates are never filtered.
            self.write_message(update.json())

    def check_origin(self, origin):
        # Allow connections from anywhere.
        return True


class PlainStreamHandler(web.RequestHandler, Watcher):
    """ Provides the job stdout stream via plain HTTP GET """

    def initialize(self, queue, build_manager):
        del queue  # unused

        self.build_manager = build_manager
        self.job = None

    async def get(self):
        try:
            project_name = self.request.query_arguments["project"][0]
        except (KeyError, IndexError):
            self.write(b"no project given\n")
            return

        try:
            build_id = self.request.query_arguments["hash"][0]
        except (KeyError, IndexError):
            self.write(b"no build hash given\n")
            return

        try:
            job_name = self.request.query_arguments["job"][0]
        except (KeyError, IndexError):
            self.write(b"no job given\n")
            return

        project_name = project_name.decode(errors='replace')
        build_id = build_id.decode(errors='replace')
        job_name = job_name.decode(errors='replace')

        try:
            project = CFG.projects[project_name]

        except KeyError:
            self.write(b"unknown project requested\n")
            return

        build = await self.build_manager.get_build(project, build_id)
        if not build:
            self.write(("no such build: project %s [%s]\n" % (
                project_name, build_id)).encode())
            return

        self.job = build.jobs.get(job_name)
        if not self.job:
            self.write(("unknown job in project %s [%s]: %s\n" % (
                project_name, build_id, job_name)).encode())
            return

        # the message queue to be sent to the http client
        self.queue = asyncio.Queue()

        # request the updates from the watched jobs
        await self.job.register_watcher(self)

        # load the job from the filesystem and
        # emit the updates until no more are coming
        await asyncio.gather(
            self.job.load_from_fs(),
            self.watch_job()
        )

    async def watch_job(self):
        """ Process updates and send them to the client """

        self.set_header("Content-Type", "text/plain")

        while True:
            update = await self.queue.get()

            if update is StopIteration:
                break

            if isinstance(update, StdOut):
                self.write(update.data.encode())

            elif isinstance(update, JobState):
                if update.is_errored():
                    self.write(
                        ("\x1b[31merror:\x1b[m %s\n" %
                         (update.text)).encode()
                    )
                elif update.is_succeeded():
                    self.write(
                        ("\x1b[32msuccess:\x1b[m %s\n" %
                         (update.text)).encode()
                    )
                elif update.is_finished():
                    # if finished but not errored or succeeded,
                    # this must be a failure.
                    # TODO: is this a good way to implement this?
                    #       certainly caused me a WTF moment...
                    self.write(
                        ("\x1b[31mfailed:\x1b[m %s\n" %
                         (update.text)).encode()
                    )

            await self.flush()

        return self.finish()

    async def on_update(self, update):
        """ Put a message to the stream queue """
        await self.queue.put(update)

    def on_connection_close(self):
        """ Add a connection-end marker to the queue """
        self.queue.put_nowait(StopIteration)

    def on_finish(self):
        if self.job is not None:
            self.job.deregister_watcher(self)

    def data_received(self, chunk):
        del chunk


class RobotsHandler(web.RequestHandler):
    """ Serves a robots.txt that disallows all indexing. """

    def initialize(self, queue, build_manager):
        del queue
        del build_manager

    def get(self):
        self.set_header("Content-Type", "text/plain")
        self.write("User-agent: *\nDisallow: /\n")

    def data_received(self, chunk):
        del chunk
