"""
GitHub backend for Kevin.

All github interaction originates from this module.
"""

import  json
import hmac
import logging
from hashlib import sha1

import aiohttp

from ..action import Action
from ..config import CFG
from ..httpd import HookHandler, HookTrigger
from ..update import (BuildState, JobState,
                      StepState, GeneratedUpdate, QueueActions)
from ..watcher import Watcher


# translation lookup-table for kevin states -> github states
STATE_TRANSLATION = {
    "waiting": "pending",
    "running": "pending",
    "success": "success",
    "failure": "failure",
    "error": "error",
    "skipped": "failure"
}


def verify_secret(blob, headers, secret):
    """
    verify the github hmac signature with our shared secret.
    """

    localsignature = hmac.new(secret, blob, sha1)
    goodsig = 'sha1=' + localsignature.hexdigest()
    msgsig = headers.get("X-Hub-Signature")
    if not msgsig:
        raise ValueError("message doesn't have a signature.")

    if hmac.compare_digest(msgsig, goodsig):
        return True

    return False


class GitHubStatusURL(GeneratedUpdate):
    """ transmit the github status url to be set """

    def __init__(self, destination):
        self.destination = destination


class GitHubPullRequest(GeneratedUpdate):
    """ Sent when a github pull request was created or updated """

    def __init__(self, project_name, repo, pull_id, commit_hash):
        self.project_name = project_name
        self.repo = repo
        self.pull_id = pull_id
        self.commit_hash = commit_hash


class GitHubPullManager(Watcher):
    """
    Tracks running pull requests and aborts a running build
    if the same pull request gets an update.

    Subscribes to all builds.
    """

    def __init__(self, repos):
        # repos this pullmanager is responsible for
        self.repos = repos

        # all the pulls that we triggered
        # (project, repo, pull_id) -> (build_id, queue)
        self.running_pull_builds = dict()

    async def on_update(self, update):
        if isinstance(update, GitHubPullRequest):
            # new pull request information that may cause an abort.
            key = (update.project_name, update.repo, update.pull_id)

            if update.repo not in self.repos:
                # repo is not handled by this pull manager,
                # don't do anything.
                return

            # get the running build id for this pull request
            entry = self.running_pull_builds.get(key)

            if entry is not None:
                build_id, queue = entry

            else:
                # that pull is not running currently, so
                # store that it's running.
                # the queue is unknown, set it to None.
                self.running_pull_builds[key] = (update.commit_hash, None)
                return

            if build_id == update.commit_hash:
                # the same build is running currently, just ignore it
                pass

            else:
                # the pull request is running already,
                # now abort the previous build for it.

                if not queue:
                    # we didn't get the "Enqueued" update for the build
                    logging.warning("[github] wanted to abort build "
                                    "in unknown queue")

                else:
                    # abort it
                    queue.abort_build(build_id)

                # and store the new build id for that pull request
                self.running_pull_builds[key] = (update.commit_hash, None)

        elif isinstance(update, QueueActions):
            # catch the queue of the build actions
            # only if we track that build, we store the queue

            # select the tracked build and store the learned queue
            for key, (build_id, _) in self.running_pull_builds.items():
                if update.build_id == build_id:
                    self.running_pull_builds[key] = (build_id, update.queue)

        elif isinstance(update, BuildState):
            # build state to remove a running pull request
            if update.is_finished():
                for key, (build_id, queue) in self.running_pull_builds.items():
                    if update.build_id == build_id:
                        # remove the build from the run list
                        del self.running_pull_builds[key]
                        return


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
        super().__init__(cfg, project)

        # shared secret
        self.hooksecret = cfg["hooksecret"].encode()

        # allowed github repos
        self.repos = list()
        for repo in cfg["repos"].split(","):
            self.repos.append(repo.strip())

        # pull request manager to detect build aborts
        self.pull_manager = GitHubPullManager(self.repos)

    def get_watchers(self):
        return [self.pull_manager]

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

    def initialize(self, queue, build_manager, triggers):
        self.queue = queue
        self.build_manager = build_manager

        # list of GitHubHooks that are can invoke this hook handler
        self.triggers = triggers

    async def get(self):
        self.write(b"Expected a JSON-formatted POST request.\n")
        self.set_status(400)
        self.finish()

    async def post(self):
        logging.info("[github] \x1b[34mGot webhook from %s\x1b[m",
                     self.request.remote_ip)
        blob = self.request.body

        try:
            headers = self.request.headers

            # fetch github event type first,
            # stray clients will already fail here.
            event = headers["X-GitHub-Event"]

            # decode the blob
            json_data = json.loads(blob.decode())

            # select the repo the update came from
            repo_name = json_data["repository"]["full_name"]

            # verify the shared secret.
            # at least one of the triggers must have it.
            # triggers is a list of GitHubHooks.
            project = None
            tried_repos = set()

            # find the trigger responsible for the pull request.
            for trigger in self.triggers:
                if repo_name in trigger.repos:

                    if verify_secret(blob, headers, trigger.hooksecret):
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
                if repo_name in tried_repos:
                    # we found the project but the signature was invalid
                    logging.error("[github] \x1b[31minvalid signature\x1b[m "
                                  "for %s hook, sure you use the same keys?",
                                  repo_name)
                    raise ValueError("invalid message signature")

                else:
                    # the project could not be found by repo name
                    logging.error(
                        "[github] \x1b[31mcould not find project\x1b[m "
                        "for hook from '%s'. I tried: %s",
                        repo_name, tried_repos)
                    raise ValueError("invalid project source")

            # dispatch by event type
            if event == "pull_request":
                await self.handle_pull_request(project, json_data)

            elif event == "push":
                await self.handle_push(project, json_data)

            elif event == "fork":
                user = json_data["sender"]["login"]
                forklocation = json_data["forkee"]["full_name"]
                forkurl = json_data["forkee"]["html_url"]
                logging.info("[github] %s forked %s to %s at %s",
                             user, repo_name, forklocation, forkurl)

            elif event == "watch":
                # the "watch" event actually means "star"
                action = json_data["action"]
                user = json_data["sender"]["login"]
                logging.info("[github] %s %s starring %s",
                             user, action, repo_name)

            else:
                raise ValueError("unhandled hook event '%s'" % event)

        except (ValueError, KeyError) as exc:
            logging.exception("[github] bad request")

            self.write(repr(exc).encode())
            self.set_status(400, "Bad request")

        except Exception as exc:
            logging.exception("[github] \x1b[31;1mexception in post hook\x1b[m")

            self.set_status(500, "Internal error")
        else:
            self.write(b"OK")

        self.finish()

    async def handle_push(self, project, json_data):
        """
        github push webhook parser.
        """

        commit_sha = json_data["after"]
        clone_url = json_data["repository"]["clone_url"]
        repo_url = json_data["repository"]["html_url"]
        user = json_data["pusher"]["name"]
        branch = json_data["ref"]        # e.g. "refs/heads/master"

        await self.create_build(project, commit_sha, clone_url, repo_url, user,
                                branch, status_url=None)

    async def handle_pull_request(self, project, json_data):
        """
        github pull_request webhook parser.
        """

        # first, see if the hook contains commit updates
        action = json_data["action"]
        if action in {"opened", "synchronize"}:
            # needs a build, let's continue
            pass
        elif action in {"labeled", "unlabeled", "assigned",
                        "unassigned", "reopened", "closed"}:
            # ignore those.
            return
        else:
            logging.warning("unknown pull_request action '%s'" % action)
            return

        # select all kinds of metadata.
        user = json_data["sender"]["login"]

        pull_id = int(json_data["number"])
        repo_name = json_data["repository"]["full_name"]

        pull = json_data["pull_request"]
        clone_url = pull["head"]["repo"]["clone_url"]
        repo_url = pull["html_url"]
        commit_sha = pull["head"]["sha"]
        branch = pull["head"]["label"]

        status_update_url = pull["statuses_url"]

        updates = [
            GitHubPullRequest(project.name, repo_name, pull_id, commit_sha),
        ]

        await self.create_build(project, commit_sha, clone_url, repo_url, user,
                                branch, status_update_url, updates)

    async def create_build(self, project, commit_sha, clone_url, repo_url, user,
                           branch, status_url=None, initial_updates=None):
        """
        Create a new build for this commit hash.
        This commit may already exist, so a existing Build is retrieved.
        """

        # this creates a new build, or, if the commit hash is already known,
        # reuses a known build
        build = await self.build_manager.new_build(project, commit_sha)

        # the github push is a source for the build
        await build.add_source(clone_url, repo_url, user, branch)

        if initial_updates:
            for update in initial_updates:
                await build.send_update(update)

        if status_url:
            # notify actions that this status url would like to have updates.
            await build.send_update(GitHubStatusURL(status_url))

        # add the build to the queue
        await self.queue.add_build(build)


class GitHubStatus(Action):
    """
    GitHub status updater action, enable in a project to
    allow real-time build updates via the github api.
    """
    @classmethod
    def name(cls):
        return "github_status"

    def __init__(self, cfg, project):
        super().__init__(cfg, project)
        self.auth_user = cfg["user"]
        self.auth_pass = cfg["token"]

    async def get_watcher(self, build, completed):
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

    async def on_update(self, update):
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
                await self.github_notify(old_update, url=newurl)

            return

        # store the update so we can send it a new client later
        self.known_updates.append(update)

        # then actually notify github.
        await self.github_notify(update)

    async def github_notify(self, update, url=None):
        """ prepare sending an update to github. """

        if not (url or self.status_update_urls):
            # no status update url known
            # we discard the update as we have nowhere to send it.
            # once somebody wants to known them, we stored it already.
            return

        # craft the update message
        if isinstance(update, BuildState):
            state, description = update.state, update.text
            context = CFG.ci_name
            target_url = None

        elif isinstance(update, JobState):
            state, description = update.state, update.text
            context = "%s: %s" % (CFG.ci_name,
                                  update.job_name)
            target_url = "%s&job=%s" % (
                self.build.target_url,
                update.job_name
            )

        elif isinstance(update, StepState):
            state, description = update.state, update.text
            context = "%s: %s-%02d %s" % (CFG.ci_name,
                                          update.job_name,
                                          update.step_number,
                                          update.step_name)

            target_url = "%s&job=%s#%s" % (
                self.build.target_url,
                update.job_name,
                update.step_name
            )

        else:
            # unhandled update
            return

        if len(description) > 140:
            logging.warning("[github] update description too long, truncating")
            description = description[:140]

        data = json.dumps({
            "context": context,
            "state": STATE_TRANSLATION[state],
            "description": description,
            "target_url": target_url
        })

        if not url:
            for destination in self.status_update_urls:
                await self.github_send_status(data, destination)
        else:
            await self.github_send_status(data, url)

    async def github_send_status(self, data, url):
        """ send a single github status update """

        try:
            # TODO: select authtoken based on url!
            authinfo = aiohttp.BasicAuth(self.cfg.auth_user,
                                         self.cfg.auth_pass)

            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data, auth=authinfo) as reply:
                    if 200 <= reply.status < 300:
                        logging.debug("[github] update delivered successfully:"
                                      " %s" % reply.status)
                    elif "status" in reply.headers:
                        logging.warning("[github] status update request "
                                        "rejected by github: %s\n%s",
                                        reply.headers["status"],
                                        await reply.text())
                    else:
                        logging.warning("[github] update failed but "
                                        "no reason given")

        except aiohttp.ClientConnectionError as exc:
            # TODO: schedule this request for resubmission
            logging.warning("[github] Failed status connection to '%s': %s",
                            url, exc)
