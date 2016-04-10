"""
Web API over WebSocket
"""

import json
from tornado import websocket

from ..config import CFG
from ..watcher import Watcher


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

        if(build == None):
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
        except json.JSONDecodeError:
            self.write_error("Cannot parse request " + msg)
            return
            
        if not isinstance(request, dict):
            self.write_error("Request is not an object")
            return
        
        if "method" not in request:
            self.write_error("Request has no method")
            return
    
        method = request["method"]
        
        if "collection" not in request:
            self.write_error("Request has no collection")
            return
        
        collection = request["collection"]
        
        # list projects
        if (method == "list" and collection == "projects"):
            self.write_projects()
        
        # subscribe to build
        elif (method == "subscribe" and collection == "build"):
            if "commit_hash" not in request:
                self.write_error("Request has no commit_hash")
                return
            
            commit_hash = request["commit_hash"]
            
            if "project" not in request:
                self.write_error("Request has no project")
                return
            
            project = request["project"]
            
            self.subscribe_to_build(project, commit_hash)
        else:
            self.write_error("Malformed request")

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
