"""
GitHub backend for Kevin.

All github interaction originates from this module.
"""

import  json
import hmac
import traceback
from hashlib import sha1

import requests

from . import (HookHandler, HookTrigger, Action)
from ..build import new_build
from ..config import CFG
from ..update import (Update, BuildState, JobState,
                      StepState, GeneratedUpdate)
from ..watcher import Watcher


class GitHubStatusURL(GeneratedUpdate):
    """ transmit the github status url to be set """

    def __init__(self, destination):
        self.destination = destination


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
        print("[github] \x1b[34mGot webhook from %s\x1b[m" % (
            self.request.remote_ip))
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
            tried_repos = set()

            for trigger in self.triggers:
                if project_name in trigger.repos:

                    if self.verify_secret(blob, headers, trigger.hooksecret):
                        project = trigger.get_project()
                        break
                    else:
                        # the trigger has an entry for the originating repo,
                        # but the signature was wrong,
                        # so try with the next trigger which may contain
                        # the project again, but with different secret.
                        pass

                tried_repos |= set(trigger.repos)

            if project is None:
                if project_name in tried_repos:
                    # we found the project but the signature was invalid
                    print("[github] \x1b[31minvalid signature\x1b[m "
                          "for %s hook, sure you use the same keys?" % (
                        project_name))
                    raise ValueError("invalid message signature")

                else:
                    # the project could not be found by repo name
                    print("[github] \x1b[31mcould not find project\x1b[m "
                          "for hook from '%s'. I tried: %s" % (
                        project_name, tried_repos))
                    raise ValueError("invalid project source")

            # dispatch by event type
            if event == "pull_request":
                self.handle_pull_request(project, json_data)

            elif event == "push":
                self.handle_push(project, json_data)

            elif event == "fork":
                user = json_data["sender"]["login"]
                forklocation = json_data["forkee"]["full_name"]
                forkurl = json_data["forkee"]["html_url"]
                print("[github] %s forked %s to %s at %s" % (
                    user, project_name, forklocation, forkurl
                ))

            elif event == "watch":
                action = json_data["action"]
                user = json_data["sender"]["login"]
                watchlocation = json_data["repository"]["full_name"]
                print("[github] %s %s watching %s" % (
                    user, action, project_name
                ))
            else:
                raise ValueError("unhandled hook event '%s'" % event)

        except (ValueError, KeyError) as exc:
            print("[github] bad request: " + repr(exc))
            traceback.print_exc()

            self.write(repr(exc).encode())
            self.set_status(400, "Bad request")

        except (BaseException) as exc:
            print("[github] \x1b[31;1mexception in post hook\x1b[m")
            traceback.print_exc()

            self.set_status(500, "Internal error")
        else:
            self.write(b"OK")

        self.finish()

    def verify_secret(self, blob, headers, secret):
        """
        verify the hmac signature with our shared secret.
        """

        localsignature = hmac.new(secret, blob, sha1)
        goodsig = 'sha1=' + localsignature.hexdigest()
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

        self.new_build(project, commit_sha, clone_url, repo_url, user,
                       branch, status_url=None)

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

        self.new_build(project, commit_sha, clone_url, repo_url, user,
                       branch, status_update_url)

    def new_build(self, project, commit_sha, clone_url, repo_url, user,
                  branch, status_url=None):
        """
        Create a new build for this commit hash.
        This commit may already exist, so a existing Build is retrieved.
        """

        # this creates a new build, or, if the commit hash is already known,
        # reuses a known build
        build = new_build(project, commit_sha)

        # the github push is a source for the build
        build.add_source(clone_url, repo_url, user, branch)

        if status_url:
            # notify actions that this status url would like to have updates.
            build.send_update(GitHubStatusURL(status_url))

        # add the build to the queue
        self.application.queue.add_build(build)


class GitHubStatus(Action):
    """
    GitHub status updater action, enable in a project to
    allow real-time build updates via the github api.
    """
    @classmethod
    def name(cls):
        return "github_status"

    def __init__(self, cfg, project):
        super().__init__(project)
        self.authtoken = (cfg["user"], cfg["token"])

    def get_watcher(self, build):
        return GitHubBuildStatusUpdater(build, self)


class GitHubBuildStatusUpdater(Watcher):
    """
    Sets the GitHub build/build step statuses from job updates.

    Constructed for each job to update.

    remember all updates.
    remember which urls are known.
    on new url, send all previous updates to that new url.
    new updates are sent to all urls known.
    """
    def __init__(self, build, config):
        # TODO: add urls from the config file?
        self.status_update_urls = set()
        self.cfg = config
        self.build = build

        # updates that we received.
        self.known_updates = list()

    def on_update(self, update):
        """
        Translates the update to a JSON GitHub status update request

        This method sends certain job updates to github,
        to allow near real-time status information.
        """

        if update == StopIteration:
            return

        if isinstance(update, GitHubStatusURL):
            newurl = update.destination

            # we got some status update url
            self.status_update_urls.add(newurl)

            # send all previous updates to that url.
            for old_update in self.known_updates:
                self.github_notify(old_update, url=newurl)

            return

        # store the update so we can send it a new client later
        self.known_updates.append(update)

        # then actually notify github.
        self.github_notify(update)

    def github_notify(self, update, url=None):
        """ prepare sending an update to github. """

        if not (url or self.status_update_urls):
            # no status update url known
            # we discard the update as we have nowhere to send it.
            # once somebody wants to known them, we stored it already.
            return

        # craft the update message
        # TODO: update the urls for mandy!
        if isinstance(update, BuildState):
            state, description = update.state, update.text
            context = CFG.ci_name
            target_url = CFG.web_url + "/" + str(self.build.relpath)

        elif isinstance(update, JobState):
            state, description = update.state, update.text
            context = "%s: %s" % (CFG.ci_name,
                                  update.job_name)
            target_url = self.build.target_url + "&job=" + update.job_name

        elif isinstance(update, StepState):
            state, description = update.state, update.text
            context = "%s: %s-%02d %s" % (CFG.ci_name,
                                          update.job_name,
                                          update.step_number,
                                          update.step_name)

            target_url = None

        else:
            # unhandled update
            return

        if len(description) > 140:
            print("[github] description too long, truncating")
            description = description[:140]

        data = json.dumps({
            "context": context,
            "state": state,
            "description": description,
            "target_url": target_url
        })

        if not url:
            for destination in self.status_update_urls:
                self.github_send_status(data, destination)
        else:
            self.github_send_status(data, url)

    def github_send_status(self, data, url):
        """ send a single github status update """

        try:
            # TODO: perform this post asynchronously!
            # TODO: select authtoken based on url!
            reply = requests.post(url, data, auth=self.cfg.authtoken)

        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                "Failed status connection to '%s': %s" % (url, exc)
            ) from None

        if not reply.ok:
            if "status" in reply.headers:
                replytext = reply.headers["status"] + '\n' + reply.text
                print("[github] status update request rejected "
                      "by github: %s" % (replytext))
            else:
                print("[github] reply status: no data given.")
