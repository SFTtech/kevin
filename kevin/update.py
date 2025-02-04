""" The various job update objects """

import json
import time as clock
from abc import ABCMeta

ALLOWED_BUILD_STATES = {
    "waiting", "running",
    "success", "failure", "error", "skipped"
}

FINISH_STATES = {"success", "failure", "error", "skipped"}
SUCCESS_STATES = {"success"}
ERROR_STATES = {"error"}

UPDATE_CLASSES = {}


UPDATE_COMPAT = {
    "JobCreated": "BuildJobCreated",
}


class _UpdateMeta(ABCMeta):
    """ Update metaclass. Adds the classes to UPDATE_CLASSES. """
    def __init__(cls, name, bases, classdict):
        super().__init__(name, bases, classdict)
        UPDATE_CLASSES[name] = cls


class Update(metaclass=_UpdateMeta):
    """
    Abstract base class for all JSON-serializable Update objects.
    __init__() should simply set the update's member variables.
    """

    # which of the member variables should not be sent via json?
    BLACKLIST: set[str] = set()

    def dump(self):
        """
        Dump members as a dict, except those in BLACKLIST.
        The dict shall be suitable for feeding back into __init__ as kwargs.
        """
        return {
            k: v for k, v in self.__dict__.items()
            if k not in self.BLACKLIST
        }

    def json(self):
        """
        Returns a JSON-serialized string of self (via self.dump()).
        This string will be broadcast via WebSocket and saved to disk.
        """
        result = {
            'class': type(self).__name__,
            **self.dump(),
        }
        return json.dumps(result)

    def __repr__(self):
        return f"<{type(self).__name__}>"

    @staticmethod
    def construct(jsonmsg: str):
        """
        Constructs an Update object from a JSON-serialized string.
        The 'class' member is used to determine the subclass that shall be
        built, the rest is passed on to the constructor as kwargs.
        """
        data = json.loads(jsonmsg)
        classname = data.pop('class')
        # mapping of old class names
        classname = UPDATE_COMPAT.get(classname, classname)

        try:
            return UPDATE_CLASSES[classname](**data)
        except (TypeError, KeyError) as err:
            raise Exception("Failed reconstructing %s: %r" % (
                classname, err)) from err

UpdateStep = Update | type[StopIteration]


class GeneratedUpdate(Update):
    """
    An update that is created by processing other updates,
    therefore should never be stored.

    When the other updates are replayed, this update is triggered again.
    This is why it must not be stored.
    """
    pass


class BuildJobCreated(Update):
    """
    Update that notifies the creation of a job.
    """
    def __init__(self, job_name, vm_name):
        self.job_name = job_name
        self.vm_name = vm_name


class BuildSource(Update):
    """
    A new source for the build.
    A source is a place from which the request to build this SHA1 has
    originated.
    A build must have at least one of these updates in order to be
    buildable (we must know a clone_url).
    """
    def __init__(self, clone_url, repo_url, author, branch, comment):
        self.clone_url = clone_url
        self.repo_url = repo_url
        self.author = author
        self.branch = branch
        self.comment = comment


class State(Update):
    """ Overall state change """
    def __init__(self, project_name, build_id, state, text, time=None):
        if state not in ALLOWED_BUILD_STATES:
            raise ValueError("Illegal state: " + repr(state))
        if not text.isprintable():
            raise ValueError("State.text not printable: " + repr(text))
        if time is None:
            time = clock.time()
        elif not (isinstance(time, int) or isinstance(time, float)):
            raise TypeError("State.time not a number, is %s: %s" % (
                type(time), repr(time)
            ))

        self.state = state
        self.text = text
        self.time = time

        self.project_name = project_name
        self.build_id = build_id

    def is_succeeded(self):
        """ return if the build succeeded """
        return self.state in SUCCESS_STATES

    def is_finished(self):
        """ return if the build is no longer running """
        return self.state in FINISH_STATES

    def is_errored(self):
        """ return if the build is no longer running """
        return self.state in ERROR_STATES


class BuildState(GeneratedUpdate, State):
    """ Build specific state changes """

    def __init__(self, project_name, build_id, state, text, time=None):
        State.__init__(self, project_name, build_id, state, text, time)


class JobUpdate(Update):
    """
    An update that is emitted from a job.
    """

    def __init__(self, job_name):
        self.job_name = job_name

    def apply_to(self, job):
        """
        Update-specific code to modify the thing object on job.update().
        No-op by default.
        Raise to report errors.
        """
        pass


class JobState(JobUpdate, State):
    """ Job specific state changes """

    def __init__(self, project_name, build_id, job_name,
                 state, text, time=None, updates_merged=False):
        JobUpdate.__init__(self, job_name)
        State.__init__(self, project_name, build_id, state, text, time)
        self.updates_merged = updates_merged

    def set_updates_merged(self):
        self.updates_merged = True


class JobEmergencyAbort(JobState):
    """ Special job state that for job-double-failures """

    def __init__(self, project_name, build_id, job_name, text, time=None, version=None):
        super().__init__(project_name, build_id, job_name,
                         "error", text, time, version)


class StepState(JobUpdate, State):
    """ Step build state change """

    # don't dump these
    BLACKLIST = {"step_number"}

    def __init__(self, project_name, build_id, job_name,
                 step_name, state, text, time=None,
                 step_number=None):

        JobUpdate.__init__(self, job_name)
        State.__init__(self, project_name, build_id, state, text, time)

        if not step_name.isidentifier():
            raise ValueError("StepState.step_name invalid: %r" % (step_name))
        if time is None:
            time = clock.time()

        self.step_name = step_name
        self.step_number = step_number

    def apply_to(self, job):
        job.step_update(self)


class OutputItem(JobUpdate):
    """ Job has produced an output item """
    def __init__(self, job_name, name, isdir, size=0):
        JobUpdate.__init__(self, job_name)

        if not name:
            raise ValueError("output item name must not be empty")
        if not name[0].isalpha():
            raise ValueError("output item name must start with a letter")
        if not name.isprintable() or (set("/\\'\"") & set(name)):
            raise ValueError("output item name contains illegal characters")

        self.name = name
        self.isdir = isdir
        self.size = size

    def validate_path(self, path):
        """
        Raises an exception if path is not a valid subdir of this.
        """
        components = path.split('/')
        if components[0] != self.name:
            raise ValueError("not a subdir of " + self.name + ": " + path)

        for component in components[1:]:
            if not component.isprintable():
                raise ValueError("non-printable character(s): " + repr(path))
            if component in {'.', '..'}:
                raise ValueError("invalid component name(s): " + path)

    def apply_to(self, job):
        job.output_items.add(self)
        job.remaining_output_size -= self.size


class StdOut(JobUpdate):
    """ Process has produced output on the TTY """
    def __init__(self, job_name: str, data: str, step_name: str | None = None):
        JobUpdate.__init__(self, job_name)

        if not isinstance(data, str):
            raise TypeError("StdOut.data not str: %r" % (data,))

        self.step_name = step_name
        self.data = data


class QueueActions(GeneratedUpdate):
    """ Actions of a build can now be enqueued. """

    def __init__(self, build_id, queue, project):
        self.build_id = build_id
        self.queue = queue
        self.project = project


class RegisterActions(GeneratedUpdate):
    """ Actions of a project shall now register at the build. """

    def __init__(self):
        pass


class RequestError(GeneratedUpdate):
    """ Sent to a client if an error occured. """

    def __init__(self, text):
        self.text = text
