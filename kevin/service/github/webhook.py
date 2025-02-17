"""
handles webhooks from GitHub.
"""

from __future__ import annotations

import json
import logging
import typing

from .update import GitHubBranchUpdate, GitHubStatusURL, GitHubPullRequest, GitHubLabelUpdate
from .util import verify_secret
from ...httpd import HookHandler

if typing.TYPE_CHECKING:
    from ...update import Update
    from ...build_manager import BuildManager
    from ...project import Project


class GitHubHookHandler(HookHandler):
    """
    Listens for GitHub WebHook POST requests

    Detects which project the WebHook came from and attaches
    the registered actions to the job.

    This class is only instanced once normally:
    for the url where the hook will be delivered to.
    The configuration takes place in many GitHubHook instances.
    """

    def initialize(self, queue, build_manager: BuildManager, triggers):
        self.queue = queue
        self.build_manager = build_manager

        # list of GitHubHooks that are can invoke this hook handler
        self.triggers = triggers

    async def get(self):
        self.write(b"Expected a JSON-formatted POST request.\n")
        self.set_status(400)
        self.finish()

    async def post(self) -> None:
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
            project: Project | None = None
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

        except Exception:
            logging.exception("[github] \x1b[31;1mexception in post hook\x1b[m")

            self.set_status(500, "Internal error")
        else:
            self.write(b"OK")

        self.finish()

    async def handle_push(self, project: Project, json_data) -> None:
        """
        github push webhook parser.
        """

        repo_name = json_data["repository"]["full_name"]
        commit_sha = json_data["head_commit"]["id"]
        clone_url = json_data["repository"]["clone_url"]
        repo_url = json_data["repository"]["html_url"]
        user = json_data["pusher"]["name"]
        branch = json_data["ref"]
        if branch is not None:
            branch = branch.lstrip("refs/heads/")  # e.g. "refs/heads/master"

        status_update_url = json_data["repository"]["statuses_url"].replace(
            "{sha}", commit_sha
        )
        updates: list[Update] = [
            GitHubBranchUpdate(project.name, repo_name, branch, commit_sha),
        ]

        await self.create_build(project, commit_sha, clone_url, repo_url, user,
                                repo_name, branch, status_update_url, updates)

    async def handle_pull_request(self, trigger, project: Project, json_data) -> None:
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
        branch = pull["head"]["ref"]

        status_update_url = pull["statuses_url"]
        issue_url = pull["issue_url"]

        updates: list[Update] = [
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

    async def create_build(self, project: Project, commit_sha: str, clone_url: str,
                           repo_url: str, user: str, repo_name: str, branch: str | None,
                           status_url: str | None = None,
                           initial_updates: list[Update] | None = None,
                           force_rebuild: bool = False):
        """
        Create a new build for this commit hash.
        This commit may already exist, so a existing Build is retrieved.
        """

        # this creates a new build, or, if the commit hash is already known,
        # reuses a known build
        build = await self.build_manager.new_build(project, commit_sha,
                                                   force_rebuild=force_rebuild)

        # register source for the build
        await build.add_source(
            clone_url=clone_url,
            repo_id=f"github/{repo_name}",
            branch=branch,
            repo_url=repo_url,
            author=f"github/{user}",
        )

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
