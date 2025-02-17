"""
Message updates sent due to GitHub stuff.
"""

from __future__ import annotations

from ...update import GeneratedUpdate


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
