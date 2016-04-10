"""
Web server to receive WebHook notifications from GitHub,
and provide them in a job queue.
"""

from abc import ABCMeta, abstractmethod

import json

from tornado import websocket, web, ioloop, gen
from tornado.platform.asyncio import AsyncIOMainLoop
from tornado.queues import Queue

from .build import get_build
from .config import CFG
from .service import Trigger
from .update import StdOut, JobState
from .watcher import Watcher


class HookTrigger(Trigger):
    """
    Base class for a webhook trigger (e.g. the github thingy).
    """

    def __init__(self, cfg, project):
        super().__init__(cfg, project)

    @abstractmethod
    def get_handler(self):
        """
        Return the (url, HookHandler class) to register at tornado for webhooks
        """
        pass

    def add_args(self, kwargdict):
        """
        Let the hook trigger add itself to the existing kwarg dict
        which will be passed to the class returned in `get_handler()`
        when instanciated.
        """
        return kwargdict

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
        urlhandlers[self.get_handler()] = self.add_args(handlerkwargs)


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

    def get(self):
        raise NotImplementedError()

    def post(self):
        raise NotImplementedError()


class HTTPD:
    """
    This class contains a server that listens for WebHook
    notifications to spawn triggered actions, e.g. new Builds.
    It also provides the websocket API and plain log streams for curl.

    handlers: (url, handlercls) -> [cfg, cfg, ...]
    queue: the jobqueue.Queue where new builds/jobs are put in
    """
    def __init__(self, handlers, queue):
        # use the main asyncio loop to run tornado
        AsyncIOMainLoop().install()

        urlhandlers = dict()
        urlhandlers[("/", PlainStreamHandler)] = None
        urlhandlers[("/ws", WebSocketHandler)] = None

        urlhandlers.update(handlers)

        # create the tornado application
        # that serves assigned urls to handlers.
        handlers = list()
        for (url, handler), cfgs in urlhandlers.items():
            if cfgs is not None:
                handlers.append((url, handler, cfgs))
            else:
                handlers.append((url, handler))

        self.app = web.Application(handlers)

        self.app.queue = queue

        # bind to tcp port
        self.app.listen(CFG.dyn_port, address=str(CFG.dyn_address))


class WebSocketHandler(websocket.WebSocketHandler, Watcher):
    """ Provides a job description stream via WebSocket """

    def initialize(self):
        self.job = None
        self.build = None

    def open(self):
        # TODO: activate nodelay mode just for realtime data?
        self.set_nodelay(True)

    def on_close(self):
        if self.build is not None:
            self.build.stop_sending_updates_to(self)

    def check_origin(self, origin):
        # TODO: check if came from correct site?
        # parsed_origin = urllib.parse.urlparse(origin)
        # allowed_origins = [urllib.parse.urlparse(CFG.web_url),
        #                    "localhost"]
        # return any(parsed_origin.netloc.endswith() for ori in origins)
        return True

    def write_projects(self):
        """ used in on_message to answer with a project """
        print("websocket: providing project list")
        
        projects = list()
        for id, project in enumerate(CFG.projects.values()):
            projects.append({
                "type": "project",
                "id": id,
                "attributes": {
                    "name": project.name,
                    "state": "dunno",
                },
            })

        self.write_message({
            "data": projects,
        })
    
    def subscribe_to_build(self, project, commit_hash):
        print("websocket: subscribe to build")
        build = get_build(project, commit_hash)

        if(build == None)
            self.write_error("No such build")
            return
        
        build.send_updates_to(self)
        self.write_message({
            "success": True,
        })
    
    def write_error(self, error_message):
        """ utility function for sending back error messages """
        self.write_message({
            "error": error_message,
        })
        
    def on_message(self, msg):
        # TODO: actual websocket protocol
        # TODO: periodic self.ping(data)
        
        try:
            request = json.loads(msg)
            
            if !isinstance(request, dir):
                self.write_error("Request is not an object")
                return
            
            # read projects
            if request.method == "list" and request.collection == "projects":
                self.write_projects()
            
            # subscribe to build
            elif (request.method == "subscribe"
                  and request.collection == "build"):
                
                if(request.commit_hash == None):
                    self.write_error("Missing commit_hash")
                    return
                
                if(request.project == None):
                    self.write_error("Missing project")
                    return
                
                self.subscribe_to_build(request.project, request.commit_hash)
            else:
                self.write_error("Malformed request")
            
        except:
            self.write_error("Cannot parse request")

    def on_pong(self, data):
        print("websocket pong: %s" % data)

    def on_update(self, msg):
        """
        Called by the watched build when an update arrives.
        """
        if msg is StopIteration:
            self.close()
        else:
            self.write_message(msg.json())


class PlainStreamHandler(web.RequestHandler, Watcher):
    """ Provides the job stdout stream via plain HTTP GET """
    @gen.coroutine
    def get(self):
        self.job = None

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

        build = get_build(project, build_id)
        if not build:
            self.write(("no such build: project %s [%s]\n" % (
                project_name, build_id)).encode())
            return
        else:
            self.job = build.jobs.get(job_name)
            if not self.job:
                self.write(("unknown job in project %s [%s]: %s\n" % (
                    project_name, build_id, job_name)).encode())
                return

            # the message queue to be sent to the http client
            self.queue = Queue()

            # request the updates from the watched jobs
            self.job.send_updates_to(self)

            # emit the updates and wait until no more are coming
            yield self.watch_job()

    @gen.coroutine
    def watch_job(self):
        """ Process updates and send them to the client """

        self.set_header("Content-Type", "text/plain")

        while True:
            update = yield self.queue.get()

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
                    self.write(
                        ("\x1b[31mfailed:\x1b[m %s\n" %
                         (update.text)).encode()
                    )

            yield self.flush()

        return self.finish()

    def on_update(self, update):
        """ Put a message to the stream queue """
        self.queue.put(update)

    def on_connection_close(self):
        """ Add a connection-end marker to the queue """
        self.on_update(StopIteration)

    def on_finish(self):
        # TODO: only do this if we got a GET request.
        if self.job is not None:
            self.job.stop_sending_updates_to(self)
