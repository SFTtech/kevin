"""
GitHub backend for Kevin.

All github interaction originates from this module.
"""

import asyncio
import hmac
import json
import logging
from hashlib import sha1
from urllib.parse import quote

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

    def __init__(self, destination, repo_name):
        self.destination = destination
        self.repo = repo_name


class GitHubPullRequest(GeneratedUpdate):
    """ Sent when a github pull request was created or updated """

    def __init__(self, project_name, repo, pull_id, commit_hash):
        self.project_name = project_name
        self.repo = repo
        self.pull_id = pull_id
        self.commit_hash = commit_hash


class GitHubBranchUpdate(GeneratedUpdate):
    """
    Sent when a branch on github is pushed to.
    e.g. refs/heads/master, which is not a pull request.

    This update can be consumed e.g. by badge generators
    or symlink handlers.

    TODO: base on a generic BranchUpdate event.
    """

    def __init__(self, project_name, repo, branch, commit_hash):
        self.project_name = project_name
        self.repo = repo
        self.branch = branch
        self.commit_hash = commit_hash


class GitHubLabelUpdate(GeneratedUpdate):
    """
    Send to perform changes to github issue/pull request labels.
    """

    def __init__(self, project_name, repo_name, pull_id, issue_url,
                 action, label):
        self.project_name = project_name
        self.repo_name = repo_name
        self.pull_id = pull_id
        self.issue_url = issue_url
        self.action = action
        self.label = label


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
                    await queue.abort_build(build_id)

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
        self.repos = set()
        for repo in cfg["repos"].split(","):
            repo = repo.strip()
            if repo:
                self.repos.add(repo)

        # assign labelname => action for custom control labels
        self.ctrl_labels = dict()
        for label in cfg.get("ctrl_labels_rebuild", "").split(','):
            label = label.strip()
            if label:
                self.ctrl_labels[label] = "rebuild"

        # pull request manager to detect build aborts
        self.pull_manager = GitHubPullManager(self.repos)

    def get_watchers(self):
        return [self.pull_manager]

    def get_handler(self):
        return ("/hooks/github", GitHubHookHandler)


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

                tried_repos |= trigger.repos

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

            logging.debug("[github] webhook event type: %s", event)

            # dispatch by event type
            if event == "pull_request":
                await self.handle_pull_request(trigger, project, json_data)

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

        repo_name = json_data["repository"]["full_name"]
        commit_sha = json_data["head_commit"]["id"]
        clone_url = json_data["repository"]["clone_url"]
        repo_url = json_data["repository"]["html_url"]
        user = json_data["pusher"]["name"]
        branch = json_data["ref"]        # e.g. "refs/heads/master"

        status_update_url = json_data["repository"]["statuses_url"].replace(
            "{sha}", commit_sha
        )
        updates = [
            GitHubBranchUpdate(project.name, repo_name, branch, commit_sha),
        ]

        await self.create_build(project, commit_sha, clone_url, repo_url, user,
                                repo_name, branch, status_update_url, updates)

    async def handle_pull_request(self, trigger, project, json_data):
        """
        github pull_request webhook parser.
        """

        create_build = False

        # first, see if the hook contains commit updates
        action = json_data["action"]
        logging.debug("[github] pull request hook action: %s", action)

        if action in {"opened", "synchronize"}:
            # needs a build, let's continue
            create_build = True

        elif action in {"labeled", "unlabeled"}:
            # control builds, let's continue
            pass

        elif action in {"assigned", "unassigned", "reopened", "edited"
                        "review_requested", "review_requested_removed",
                        "ready_for_review", "locked", "unlocked"}:
            # ignore those.
            return

        elif action in {"closed",}:
            # maybe helpful some day:
            # was_merged = json_data["merged"]
            # the pull request was merged (or not)
            return

        else:
            logging.warning("[github] unknown pull_request action '%s'", action)
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
        issue_url = pull["issue_url"]

        updates = [
            GitHubPullRequest(project.name, repo_name, pull_id, commit_sha),
        ]

        labels = pull["labels"]
        label_actions = list()
        force_rebuild = False

        for label in labels:
            label_action = trigger.ctrl_labels.get(label["name"])
            if label_action:
                logging.debug("[github] using label %s for action %s",
                              label["name"], label_action)
                label_actions.append((label_action, label["name"]))
            else:
                logging.debug("[github] ignoring label %s", label["name"])

        for label_action, label_value in label_actions:
            if label_action == "rebuild":
                force_rebuild = True
                create_build = True
                updates.append(
                    GitHubLabelUpdate(project.name, repo_name, pull_id,
                                      issue_url, "remove", label_value)
                )
            else:
                raise Exception("unknown build control action: %s" % label_action)

        if not create_build:
            return

        await self.create_build(project, commit_sha, clone_url, repo_url, user,
                                repo_name, branch, status_update_url, updates,
                                force_rebuild)

    async def create_build(self, project, commit_sha, clone_url,
                           repo_url, user, repo_name, branch,
                           status_url=None,
                           initial_updates=None,
                           force_rebuild=False):
        """
        Create a new build for this commit hash.
        This commit may already exist, so a existing Build is retrieved.
        """

        # this creates a new build, or, if the commit hash is already known,
        # reuses a known build
        build = await self.build_manager.new_build(project, commit_sha,
                                                   force_rebuild=force_rebuild)

        # the github push is a source for the build
        await build.add_source(clone_url, repo_url, user, branch)

        if initial_updates:
            for update in initial_updates:
                await build.send_update(update)

        if status_url:
            # notify actions that this status url would like to have updates.
            # this will most likely tell the GitHubBuildStatusUpdater
            # where to send updates to.
            await build.send_update(GitHubStatusURL(status_url, repo_name))

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
        self.repos = set()
        for repo in cfg.get("repos", "any").split(","):
            repo = repo.strip()
            if repo:
                self.repos.add(repo)

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

        self.update_send_queue = asyncio.Queue(maxsize=1000)

        self.send_task = asyncio.get_event_loop().create_task(
            self.github_status_worker()
        )

    async def on_update(self, update):
        """
        Translates the update to a JSON GitHub status update request

        This method sends certain job updates to github,
        to allow near real-time status information.
        """

        if update == StopIteration:
            return

        if isinstance(update, GitHubStatusURL):
            if ("any" not in self.cfg.repos
                and update.repo_name not in self.cfg.repos):

                # this status url is not handled by this status updater.
                # => we don't have the auth token to send valid updates
                #    to the github API.
                return

            newurl = update.destination

            # we got some status update url
            self.status_update_urls.add(newurl)

            # send all previous updates to that url.
            for old_update in self.known_updates:
                await self.github_notify(old_update, url=newurl)

            return

        elif isinstance(update, GitHubLabelUpdate):
            if ("any" not in self.cfg.repos
                and update.repo_name not in self.cfg.repos):
                return

            if update.action == "remove":
                # DELETE /repos/:owner/:repo/issues/:issue_number/labels/:name
                url = f"{update.issue_url}/labels/{quote(update.label)}"
                await self.github_send_status(None, url, "delete")
            return

        # store the update so we can send it to a new client later
        # even if we don't have an url yet (it may arrive later)
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
                await self.github_send_status(data, destination, "post")
        else:
            await self.github_send_status(data, url, "post")

    async def github_send_status(self, data, url, req_method):
        """
        send a single github status update

        data: the payload
        url: http url
        req_method: http method, e.g. get, post, ...

        this request is put into a queue, where it is
        processed by the worker task.
        """

        try:
            self.update_send_queue.put_nowait((data, url, req_method))

        except asyncio.QueueFull:
            logging.error("[github] request queue full! wtf!?")

    async def github_status_worker(self):
        """
        works through the update request queue
        """

        retries = 3
        retry_delay = 5

        while True:
            data, url, method = await self.update_send_queue.get()

            delivered = False
            for _ in range(retries):
                try:
                    delivered = await self.github_submit(data, url, method)
                    if delivered:
                        break
                except Exception:
                    logging.exception("[github] exception occured when sending"
                                      "%s API request to '%s'", method, url)

                await asyncio.sleep(retry_delay)

            if not delivered:
                logging.warning("[github] could not deliver %s request to %s "
                                "in %d tries with %s s retry delay, "
                                "skipping it...",
                                method, url, retries, retry_delay)

    async def github_submit(self, data, url, req_method, timeout=10.0):
        """ send a request to the github API """
        try:
            authinfo = aiohttp.BasicAuth(self.cfg.auth_user,
                                         self.cfg.auth_pass)

            timeout = aiohttp.ClientTimeout(total=timeout)

            async with aiohttp.ClientSession(timeout=timeout,
                                             trust_env=True) as session:
                assert req_method in {"post", "get", "delete", "put", "patch"}

                async with session.request(req_method.upper(), url,
                                           data=data, auth=authinfo) as reply:
                    if 200 <= reply.status < 300:
                        logging.debug("[github] update delivered successfully:"
                                      " %s", reply.status)
                        return True

                    elif "status" in reply.headers:
                        logging.warning("[github] status update request to %s "
                                        "rejected by github (%d): %s\n%s",
                                        url,
                                        reply.status,
                                        reply.headers["status"],
                                        await reply.text())
                        return False

                    logging.warning("[github] update to %s failed with"
                                    "http code %d, "
                                    "and no status report",
                                    url,
                                    reply.status)
                    return False

        except aiohttp.ClientConnectionError as exc:
            logging.warning("[github] Failed status connection to '%s': %s",
                            url, exc)
            return False
