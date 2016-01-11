"""
GitHub backend for Kevin.

All github interaction originates from this module.
"""

import hmac
import json
import requests
import traceback
import queue

from hashlib import sha1

from . import (HookHandler, HookTrigger, Action)
from ..job import new_job
from ..config import CFG
from ..jobupdate import BuildState, StepState
from ..watcher import Watcher


class GitHubHook(HookTrigger):
    """
    A trigger from a GitHub webhook.
    This class is instanced multiple times, maybe even more
    for one project.

    Having one of those for each project is the normal case.
    """

    @classmethod
    def name(cls):
        return "github_webhook"

    def __init__(self, cfg, project):
        super().__init__(project)

        # shared secret
        self.hooksecret = cfg["hooksecret"].encode()

        # allowed github repos
        self.repos = list()
        for repo in cfg["repos"].split(","):
            self.repos.append(repo.strip())

    def get_handler(self):
        return ("/hook-github", GitHubHookHandler)


class GitHubHookHandler(HookHandler):
    """
    Listens for GitHub WebHook POST requests

    Detects which project the WebHook came from and attaches
    the registered actions to the job.

    This class is only instanced once normally:
    for the url where the hook will be delivered to.
    The configuration takes place in many GitHubHook instances.
    """

    def initialize(self, triggers):
        # list of GitHubHooks that are can invoke this hook handler
        self.triggers = triggers

    def get(self):
        self.write(b"Expected a JSON-formatted POST request.\n")
        self.set_status(400)
        self.finish()

    def post(self):
        print("\x1b[34mGot webhook from %s\x1b[m" % self.request.remote_ip)
        blob = self.request.body

        try:
            headers = self.request.headers

            # fetch github event type first,
            # stray clients will already fail here.
            event = headers["X-GitHub-Event"]

            # decode the blob
            json_data = json.loads(blob.decode())

            # select the repo the update came from
            project_name = json_data["repository"]["full_name"]

            # verify the shared secret.
            # at least one of the triggers must have it.
            # triggers is a list of GitHubHooks.
            project = None
            for trigger in self.triggers:
                if project_name in trigger.repos:
                    if self.verify_secret(blob, headers, trigger.hooksecret):
                        project = trigger.project
                        break
                    else:
                        # the trigger has an entry for the originating repo,
                        # but the signature was wrong,
                        # so try with the next trigger.
                        pass

            if project is None:
                # no project could be assigned to the hook
                raise ValueError("invalid message signature")

            # dispatch by event type
            if event == "pull_request":
                self.handle_pull_request(project, json_data)
            elif event == "push":
                self.handle_push(project, json_data)
            else:
                raise ValueError("unhandled hook event '%s'" % event)

        except (ValueError, KeyError) as exc:
            print("bad request: " + repr(exc))
            traceback.print_exc()

            self.write(repr(exc).encode())
            self.set_status(400, "Bad request")

        except (BaseException) as exc:
            print("\x1b[31;1mexception in post hook\x1b[m")
            traceback.print_exc()

            self.set_status(500, "Internal error")
        else:
            self.write(b"OK")

        self.finish()

    def verify_secret(self, blob, headers, secret):
        """
        verify the hmac signature with our shared secret.
        """

        goodsig = hmac.new(secret, blob, sha1)
        goodsig = 'sha1=' + goodsig.hexdigest()
        msgsig = headers.get("X-Hub-Signature")
        if not msgsig:
            raise ValueError("message doesn't have a signature.")

        if not hmac.compare_digest(msgsig, goodsig):
            return False
        else:
            return True

    def handle_push(self, project, json_data):
        """
        github push webhook parser.
        """

        commit_sha = json_data["head"]
        clone_url = json_data["repository"]["clone_url"]
        repo_url = json_data["repository"]["html_url"]
        user = json_data["pusher"]["name"]
        branch = json_data["ref"]        # e.g. "refs/heads/master"

        job, was_new = new_job(project, commit_sha, clone_url, repo_url,
                               user, branch)
        if was_new:
            self.application.enqueue_job(job)

    def handle_pull_request(self, project, json_data):
        """
        github pull_request webhook parser.
        """

        # first, see if the hook contains commit updates
        action = json_data["action"]
        if action in {"labeled", "unlabeled", "assigned",
                      "unassigned", "reopened", "closed"}:
            # ignore those.
            return
        elif action in {"opened", "synchronize"}:
            # needs a build, let's continue
            pass
        else:
            raise ValueError("unknown pull_request action '%s'" % action)

        # select all kinds of metadata.
        user = json_data["sender"]["login"]

        pull = json_data["pull_request"]
        clone_url = pull["head"]["repo"]["clone_url"]
        repo_url = pull["head"]["repo"]["html_url"]
        commit_sha = pull["head"]["sha"]
        branch = pull["head"]["label"]
        status_update_url = pull["statuses_url"]

        job, was_new = new_job(project, commit_sha, clone_url, repo_url,
                               user, branch, status_update_url)

        if was_new:
            self.application.enqueue_job(job)


class GitHubStatus(Action):
    @classmethod
    def name(cls):
        return "github_status"

    def __init__(self, cfg, project):
        super().__init__(project)
        self.authtoken = (cfg["user"], cfg["token"])

    def get_watcher(self, job):
        return GitHubBuildStatusUpdater(job, self)


class GitHubBuildStatusUpdater(Watcher):
    """
    Sets the GitHub build/build step statuses from job updates.

    Constructed for each job to update.
    """
    def __init__(self, job, config):
        self.status_update_url = job.get_info("status_update_url")
        if not self.status_update_url:
            raise Exception("no status update url known. "
                            "did you forget to use a github trigger?")

        self.cfg = config

        # "Details" link base url
        self.job_info_url = job.target_url

    def new_update(self, update):
        """
        Translates the update to a JSON GitHub status update request

        This method sends certain job updates to github,
        to allow near real-time status information.
        """

        if isinstance(update, BuildState):
            state, description = update.state, update.text
            context = CFG.ci_name
            target_url = self.job_info_url

        elif isinstance(update, StepState):
            state, description = update.state, update.text
            context = "%s: %02d %s" % (CFG.ci_name,
                                       update.step_number,
                                       update.step_name)
            target_url = self.job_info_url + "&step=" + update.step_name

        else:
            # e.g. StopIteration
            return

        if len(description) > 140:
            print("description too long for github, truncating")
            description = description[:140]

        data = json.dumps({
            "context": context,
            "state": state,
            "description": description,
            "target_url": target_url
        })

        # TODO: we may wanna do this asynchronously.
        try:
            reply = requests.post(self.status_update_url, data,
                                  auth=self.cfg.authtoken)
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError("Failed status connection to '%s': "
                               "%s" % (self.statuses_update_url, exc)
            ) from None

        if not reply.ok:
            if "status" in reply.headers:
                replytext = reply.headers["status"] + '\n' + reply.text
                print("status update request rejected by github: " + replytext)
            else:
                print("reply status: no data given.")
