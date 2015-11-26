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

from . import service
from ..config import CFG
from .. import jobs
from ..jobupdate import JobSource, BuildState, StepState


class HookHandler(service.HookHandler):
    """
    Listens for GitHub WebHook POST requests
    """

    def get(self):
        self.write(b"Expected a JSON-formatted POST request.\n")
        self.set_status(400)
        self.finish()

    def post(self):
        print("\x1b[34mGot webhook from %s\x1b[m" % self.request.remote_ip)
        blob = self.request.body
        try:
            # verify signature
            signature = hmac.new(CFG.github_hooksecret, blob, sha1)
            signature = 'sha1=' + signature.hexdigest()
            if self.request.headers["X-Hub-Signature"] != signature:
                raise ValueError(
                    "signature invalid",
                    self.request.headers["X-Hub-Signature"],
                    signature
                )
            self.handle_json_blob(blob)

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

    def handle_json_blob(self, blob):
        """
        Called for each JSON-formatted POST request.
        blob is a bytes object.
        If the JSON blob describes a valid build job,
        a job object is instantiated and appended to server.job_queue.
        """
        json_data = json.loads(blob.decode())

        clone_url = json_data["pull_request"]["head"]["repo"]["clone_url"]
        commit_sha = json_data["pull_request"]["head"]["sha"]
        statuses_update_url = json_data["pull_request"]["statuses_url"]

        # brand_new says whether the job has not prevoiusly existed
        # and should be enqueued.
        # TODO: implement brand-new-detection
        job, brand_new = jobs.get(commit_sha, True)

        # TODO: may have multiple sources (e.g. branch, pullreq, ...)
        # each webhook adds this source again!
        # -> check if it's already in there
        job.update(JobSource(
            clone_url=clone_url,
            repo_url=None,  # TODO get those from json_data
            author=None,
            branch=None,
            comment=None
        ))

        # install the github job state updater callback
        job.watch(
            GitHubBuildStatusUpdater(
                statuses_update_url,
                CFG.github_authtok,
                job.target_url
            )
        )

        if brand_new:
            print("enqueueing job: \x1b[2m[%s]\x1b[m @ %s" % (job.job_id,
                                                              job.clone_url))
            print("\x1b[1mcurl -N %s?job=%s\x1b[m" % (CFG.dyn_url, job.job_id))

            try:
                self.application.job_queue.put(job, timeout=0)
                job.update(BuildState("pending", "waiting for VM"))
            except queue.Full:
                job.error("overloaded; job was dropped.")


class GitHubBuildStatusUpdater:
    """
    Sets the GitHub build/build step statuses from job updates.

    The constructor takes the
     - statuses update url
     - auth token
     - job info url
    """
    def __init__(self, statuses_update_url, authtok, job_info_url):
        self.statuses_update_url = statuses_update_url
        self.authtok = authtok
        self.job_info_url = job_info_url

    def new_update(self, update):
        """ Translates the update to a JSON GitHub status update request """
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

        data = json.dumps(dict(
            context=context,
            state=state,
            description=description,
            target_url=target_url
        ))

        try:
            reply = requests.post(self.statuses_update_url, data,
                                  auth=self.authtok)
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError("Failed status connection to '%s' "
                               "%s" % (
                                   self.statuses_update_url,
                                   exc
                               )
            ) from None

        if not reply.ok:
            if "status" in reply.headers:
                replytext = reply.headers["status"] + '\n' + reply.text
                print("status update request rejected by github: " + replytext)
            else:
                print("reply status: no data given.")
