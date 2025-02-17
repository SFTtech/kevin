"""
Message updates sent due to GitHub stuff.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ...update import GeneratedUpdate


class GitHubStatusURL(ABC, GeneratedUpdate):
    """ Where GitHub status updates are to be sent to """

    def __init__(self, destination: str, repo: str) -> None:
        self.destination = destination
        self.repo = repo

    @abstractmethod
    def target_id(self) -> str:
        raise NotImplementedError()


class GitHubPullRequestStatusURL(GitHubStatusURL):
    """ Where status updates for a GitHub pull request are sent to """
    def __init__(self, destination: str, repo: str, pull_id: int) -> None:
        super().__init__(destination, repo)
        self.pull_id: int = pull_id

    def target_id(self) -> str:
        return f"{self.repo}/pull/{self.pull_id}"


class GitHubBranchStatusURL(GitHubStatusURL):
    """ Where status updates for GitHub branch update are sent to """
    def __init__(self, destination: str, repo: str, branch_name: str) -> None:
        super().__init__(destination, repo)
        self.branch_name: str = branch_name

    def target_id(self) -> str:
        return f"{self.repo}/branch/{self.branch_name}"


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

    def __init__(self, project_name, repo, pull_id, issue_url,
                 action, label):
        self.project_name = project_name
        self.repo = repo
        self.pull_id = pull_id
        self.issue_url = issue_url
        self.action = action
        self.label = label
