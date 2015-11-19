""" The various job update objects """

import json
import time as clock
from abc import ABCMeta, abstractmethod

ALLOWED_BUILD_STATES = {"pending", "success", "failure", "error"}

JOB_UPDATE_CLASSES = {}


class JobUpdateMeta(ABCMeta):
    """ JobUpdate metaclass. Adds the classes to JOB_UPDATE_CLASSES. """
    def __init__(cls, name, bases, classdict):
        super().__init__(name, bases, classdict)
        JOB_UPDATE_CLASSES[name] = cls


class JobUpdate(metaclass=JobUpdateMeta):
    """
    Abstract base class for all JSON-serializable JobUpdate objects.
    __init__() should simply set the update's member variables.
    """
    @abstractmethod
    def dump(self):
        """
        Shall dump the members as a dict.
        The dict shall be suitable for feeding back into __init__ as kwargs.
        """
        pass

    def apply_to(self, job):
        """
        Update-specific code to modify the job object on job.update().
        No-op by default.
        This, and __init__, are where validations should happen.
        Raise to report errors.
        """
        pass

    def json(self):
        """
        Returns a JSON-serialized string of self (via self.dump()).
        This string will be broadcast via WebSocket and saved to disk.
        """
        result = self.dump()
        result['class'] = type(self).__name__
        return json.dumps(result)

    def __repr__(self):
        return self.json()

    @staticmethod
    def construct(jsonmsg):
        """
        Constructs an JobUpdate object from a JSON-serialized string.
        The 'class' member is used to determine the subclass that shall be
        built, the rest is passed on to the constructor as kwargs.
        """
        data = json.loads(jsonmsg)
        classname = data['class']
        del data['class']
        return JOB_UPDATE_CLASSES[classname](**data)


class JobSource(JobUpdate):
    """
    A new source for the job.
    A source is a place from which the request to build this SHA1 has
    originated.
    A job must have at least one of these updates in order to be
    buildable (we must know a clone_url).
    """
    def __init__(self, clone_url, repo_url, author, branch, comment):
        self.clone_url = clone_url
        self.repo_url = repo_url
        self.author = author
        self.branch = branch
        self.comment = comment

    def dump(self):
        return dict(
            clone_url=self.clone_url,
            repo_url=self.repo_url,
            author=self.author,
            branch=self.branch,
            comment=self.comment
        )

    def apply_to(self, job):
        job.clone_url = self.clone_url


class BuildState(JobUpdate):
    """ Overall build state change """
    def __init__(self, state, text, time=None):
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

    def dump(self):
        return dict(
            state=self.state,
            text=self.text,
            time=self.time
        )


class StepState(JobUpdate):
    """ Step build state change """
    def __init__(self, step_name, state, text, time=None, step_number=None):
        if not step_name.isidentifier():
            raise ValueError("StepState.step_name invalid: " + repr(step_name))
        if time is None:
            time = clock.time()

        self.step_name = step_name
        self.step_number = step_number
        self.state = state
        self.text = text
        self.time = time

    def dump(self):
        return dict(
            step_name=self.step_name,
            state=self.state,
            text=self.text,
            time=self.time
        )

    def apply_to(self, job):
        if self.state == "pending":
            job.pending_steps.add(self.step_name)
        else:
            try:
                job.pending_steps.remove(self.step_name)
            except KeyError:
                pass

        if self.step_number is None:
            if self.step_name not in job.step_numbers:
                job.step_numbers[self.step_name] = len(job.step_numbers)
            self.step_number = job.step_numbers[self.step_name]


class OutputItem(JobUpdate):
    """ Job has produced an output item """
    def __init__(self, name, isdir, size=0):
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
        components = path.split('\n')
        if not components[0] == self.name:
            raise ValueError("not a subdir of " + self.name + ": " + path)

        for component in components[1:]:
            if not component.isprintable():
                raise ValueError("non-printable character(s): " + repr(path))
            if component in {'.', '..'}:
                raise ValueError("invalid component name(s): " + path)

    def dump(self):
        return dict(
            name=self.name,
            isdir=self.isdir,
            size=self.size
        )

    def apply_to(self, job):
        job.output_items.add(self)
        job.remaining_output_size -= self.size


class StdOut(JobUpdate):
    """ Process has produced output on the TTY """
    def __init__(self, data):
        if not isinstance(data, str):
            raise TypeError("StdOut.data not str: " + repr(data))

        self.data = data

    def dump(self):
        return dict(data=self.data)
