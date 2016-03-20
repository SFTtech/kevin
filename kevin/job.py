"""
Job processing code
"""

from abc import abstractmethod
import asyncio
import json
import logging
import shutil
import traceback

from .chantal import Chantal
from .config import CFG
from .falk import FalkSSH, FalkSocket
from .falkvm import VMError
from .process import (ProcTimeoutError, LineReadError, ProcessFailed,
                      ProcessError)
from .project import Project
from .update import (BuildSource, Update, JobState, StepState,
                     StdOut, OutputItem, Enqueued, JobCreated,
                     GeneratedUpdate, ActionsAttached)
from .util import recvcoroutine
from .watcher import Watcher, Watchable
from .service import Action



class JobTimeoutError(Exception):
    """
    Indicates a job execution timeout.
    """
    def __init__(self, timeout, was_global):
        super().__init__(cmd, timeout)
        self.was_global = was_global


class JobAction(Action):
    """
    This action attaches a new job to a build.
    """

    @classmethod
    def name(cls):
        return "job"

    def __init__(self, cfg, project):
        super().__init__(cfg, project)
        self.job_name = cfg["name"]
        self.descripton = cfg.get("description")
        self.vm_name = cfg["machine"]

    def get_watcher(self, build):
        return Job(build, self.project, self.job_name, self.vm_name)


class Job(Watcher, Watchable):
    """
    Holds all info for one job, which runs a commit SHA of a project
    in a falk machine.

    TODO: when "restarting" the job, the reconstruction from fs must
          not happen. for that, a "reset" must be implemented.
    """
    def __init__(self, build, project, name, vm_name):
        super().__init__()

        # the project and build this job is invoked by
        self.build = build
        self.project = project

        # name of this job within a build.
        self.name = name

        # name of the vm where the job shall run.
        self.vm_name = vm_name

        # No more tasks to perform for this job?
        self.completed = False

        # the tasks required to run for this job.
        self.tasks = set()

        # List of job status update JSON objects.
        self.updates = list()

        # Internal cache, used when erroring all remaining pending states.
        self.pending_steps = set()

        # {step name: step number}, used for step name prefixes
        self.step_numbers = dict()

        # all commited output items
        self.output_items = set()

        # current uncommited output item
        self.current_output_item = None

        # current step name of the job
        self.current_step = None

        # remaining size limit
        self.remaining_output_size = self.project.cfg.job_max_output

        # receive files from chantal
        self.raw_file = None
        self.raw_remaining = 0

        # storage folder for this job.
        self.path = build.path.joinpath(self.name)

        # tell the our watchers that we enqueued ourselves
        self.send_update(JobCreated(self.build.commit_hash, self.name))

        # try to reconstruct from the persistent storage.
        self.load_from_fs()

        # create the output directory structure
        if not CFG.args.volatile:
            if not self.path.is_dir():
                self.path.mkdir(parents=True)

    def load_from_fs(self):
        """
        reconstruct the job from the filesystem.
        TODO: currently, the old job attempt is deleted if not finished.
        maybe we wanna keep it.
        """

        # only reconstruct if we wanna use the local storage
        if CFG.args.volatile:
            return

        # Check the current status of the job.
        self.completed = self.path.joinpath("_completed").is_file()
        if self.completed:
            # load update list from file
            with self.path.joinpath("_updates").open() as updates_file:
                for json_line in updates_file:
                    self.send_update(Update.construct(json_line),
                                     save=True, fs_store=False,
                                     forbid_completed=False)

        else:
            # make sure that there are no remains
            # of previous aborted jobs.
            try:
                shutil.rmtree(str(self.path))
            except FileNotFoundError:
                pass

    def __str__(self):
        return "%s.%s [\x1b[33m%s\x1b[m]" % (
            self.build.project.name,
            self.name,
            self.build.commit_hash)

    async def get_falk_vm(self, vm_name):
        """
        return a suitable vm instance for this job from a falk.
        """

        vm = None

        # try each falk to find the machine
        for falkname, falkcfg in CFG.falks.items():

            # TODO: better selection if this falk is suitable, e.g. has
            #       machine for this job. either via cache or direct query.
            #
            # TODO: allow falk bypass by launching VM locally without a
            #       falk daemon!

            falk = None
            if falkcfg["connection"] == "ssh":
                host, port = falkcfg["location"]
                falk = FalkSSH(host, port, falkcfg["user"],
                               falkcfg["key"])

            elif falkcfg["connection"] == "unix":
                falk = FalkSocket(falkcfg["location"], falkcfg["user"])

            else:
                raise Exception("unknown falk connection type: %s -> %s" % (
                    falkname, falkcfg["connection"]))

            try:
                # connect
                await falk.create()

                # create container
                vm = await falk.create_vm(vm_name)

            except (LineReadError, ProcessError) as exc:
                # TODO: connection rejections, auth problems, ...
                logging.warn("failed communicating "
                             "with falk '%s'" % falkname)
                logging.warn("\x1b[31merror\x1b[m: $ %s" % (
                    " ".join(exc.cmd)))
                logging.warn("       -> %s" % (exc))
                logging.warn("  are you sure %s = '%s' "
                             "is a valid falk entry?" % (
                                 falkname, falk))

            if vm is not None:
                # we found the machine
                return vm

        if vm is None:
            raise VMError("VM '%s' could not be provided by any falk" % (
                vm_name))

    def on_send_update(self, update, save=True, fs_store=True,
                       forbid_completed=True):
        """
        When an update is to be sent to all watchers
        """

        if update == StopIteration:
            return

        if forbid_completed and self.completed:
            raise Exception("job sending update after being completed.")

        # when it's a StepUpdate, this manages the pending_steps set.
        update.apply_to(self)

        if isinstance(update, GeneratedUpdate):
            save = False

        if save:
            self.updates.append(update)

        if not save or not fs_store or CFG.args.volatile:
            # don't write the update to the job storage
            return

        # append this update to the build updates file
        with self.path.joinpath("_updates").open("a") as ufile:
            ufile.write(update.json() + "\n")

    def on_update(self, update):
        """
        When this job receives updates from any of its watched
        watchables, the update is processed here.
        """

        if isinstance(update, ActionsAttached):
            # tell the build that we're a assigned job.
            self.build.register_job(self)

            # we are already attached to receive updates from a build
            # now, we subscribe the build to us so it gets our updates.
            # when we reconstructed the job from filesystem,
            # this step feeds all the data into the build.
            self.watch(self.build)

        elif isinstance(update, Enqueued):
            if not self.completed:
                # add the job to the processing queue
                update.queue.add_job(self)

    def step_update(self, update):
        """ apply a step update to this job. """

        if not isinstance(update, StepState):
            raise Exception("tried to use non-StepState to step_update")

        if update.state == "pending":
            self.pending_steps.add(update.step_name)
        else:
            try:
                self.pending_steps.remove(update.step_name)
            except KeyError:
                pass

        if update.step_number is None:
            if update.step_name not in self.step_numbers:
                self.step_numbers[update.step_name] = len(self.step_numbers)
            update.step_number = self.step_numbers[update.step_name]

    def set_state(self, state, text, time=None):
        """ set the job state information """
        self.send_update(JobState(self.project.name, self.build.commit_hash,
                                  self.name, state, text, time))


    def set_step_state(self, step_name, state, text, time=None):
        """ send a StepState update. """
        self.send_update(StepState(self.project.name, self.build.commit_hash,
                                   self.name, step_name, state, text,
                                   time=None))

    def on_watch(self, watcher):
        # send all previous job updates to the watcher
        for update in self.updates:
            watcher.on_update(update)

        # and send stop if this job is finished
        if self.completed:
            watcher.on_update(StopIteration)


    async def run(self):
        """ Attempts to build the job. """

        try:
            if self.completed:
                raise Exception("tried to run a completed job!")

            logging.info(
                "running: "
                "\x1b[1mcurl -N %s?project=%s&hash=%s&job=%s\x1b[m" % (
                    CFG.dyn_url, self.build.project.name,
                    self.build.commit_hash, self.name))

            # falk contact
            self.set_state("pending", "requesting VM")

            vm = await asyncio.wait_for(self.get_falk_vm(self.vm_name),
                                        timeout=10)

            # vm was acquired, now boot it.
            self.set_state("pending", "booting VM")

            async with Chantal(vm) as chantal:

                # wait for the VM and install chantal
                # TODO: allow already existing chantal installation?
                await chantal.wait_for_connection(timeout=60, try_timeout=20)
                await chantal.install(timeout=20)

                control_handler = self.control_handler()

                try:
                    # run chantal process in the vm
                    async with chantal.run(self) as run:

                        # and fetch all the output
                        async for stream_id, data in run.output():
                            # stdout message:
                            if stream_id == 1:
                                self.send_update(
                                    StdOut(self.project.name,
                                           self.build.commit_hash,
                                           self.name,
                                           data.decode("utf-8",
                                                       errors="replace")))

                            # control message stream chunk:
                            elif stream_id == 2:
                                control_handler.send(data)

                        # wait for chantal termination
                        await run.wait()

                except ProcTimeoutError as exc:
                    raise JobTimeoutError(exc.timeout, exc.was_global)

        except asyncio.CancelledError as exc:
            logging.info("\x1b[31;1mJob aborted:\x1b[m %s" % (self))
            self.error("Job cancelled")
            raise

        except (ProcessFailed, asyncio.TimeoutError) as exc:
            if isinstance(exc, ProcessFailed):
                error = ": %s" % exc.cmd[0]
                what = "failed"
            else:
                error = ""
                what = "timed out"

            logging.error("[job] \x1b[31;1mProcess %s:\x1b[m %s:\n%s" % (
                what, self, exc))

            self.error("Process failed%s" % error)

        except (LineReadError, ProcessError) as exc:
            logging.error("[job] \x1b[31;1mCommunication failure:"
                          "\x1b[m %s" % self)

            logging.error(" $ %s: %s" % (" ".join(exc.cmd), exc))

            self.error("Process communication error: %s" % (exc.cmd[0]))

        except ProcTimeoutError as exc:
            if exc.was_global:
                silence = "Took"
            else:
                silence = "Silence time"

            logging.error("[job] \x1b[31;1mTimeout:\x1b[m %s" % self)
            logging.error(" $ %s", " ".join(exc.cmd))
            logging.error("\x1b[31;1m%s longer than limit "
                          "of %.2fs.\x1b[m" % (
                              silence , exc.timeout))
            traceback.print_exc()

            self.error("Process %s took > %.02fs." % (exc.cmd[0],
                                                      exc.timeout))

        except JobTimeoutError as exc:

            # did it take too long to finish?
            if exc.was_global:
                logging.error("[job] \x1b[31;1mTimeout:\x1b[m %s" % (
                    self))
                logging.error("\x1b[31;1mTook longer than limit "
                              "of %.2fs.\x1b[m" % (
                                  exc.timeout))

                if self.current_step:
                    self.set_step_state(self.current_step, "error",
                                        "Timeout!")

                self.error("Job took > %.02fs." % (exc.timeout))

            # or too long to provide a message?
            else:
                logging.error("[job] \x1b[31;1mSilence timeout!\x1b[m "
                              "%s\n\x1b[31mQuiet for > %.2fs.\x1b[m" % (
                                  self, exc.timeout))

                # a specific step is responsible:
                if self.current_step:
                    self.set_step_state(self.current_step, "error",
                                        "Silence for > %.02fs." % (
                                            exc.timeout))
                    self.error("Silence Timeout!")
                else:
                    # bad step is unknown:
                    self.error("Silence for > %.2fs!" % (exc.timeout))

        except VMError as exc:
            logging.error("\x1b[31;1mMachine action failed\x1b[m "
                          "%s.%s [\x1b[33m%s\x1b[m]" % (
                              self.build.project.name,
                              self.name, self.build.commit_hash))
            traceback.print_exc()

            self.error("VM Error: %s" % (exc))

        except Exception as exc:
            logging.error("\x1b[31;1mexception in Job.run()\x1b[m "
                          "%s.%s [\x1b[33m%s\x1b[m]" % (
                              self.build.project.name,
                              self.name,
                              self.build.commit_hash))
            traceback.print_exc()

            try:
                self.error("Job.run(): %r" % (exc))
            except Exception as exc:
                logging.error("\x1b[31;1mfailed to notify service "
                              "about error\x1b[m")
                traceback.print_exc()

        finally:
            # error the leftover steps
            for step in self.pending_steps:
                self.set_step_state(step, 'error',
                                    'step result was not reported')

            # the job is completed!
            self.completed = True

            if not CFG.args.volatile:
                # the job is now officially completed
                self.path.joinpath("_completed").touch()

            self.send_update(StopIteration)

    def error(self, text):
        """
        Produces an 'error' JobState and an 'error' StepState for all
        steps that are currently pending.
        """
        self.set_state("error", text)
        while self.pending_steps:
            step = self.pending_steps.pop()
            self.set_step_state(step, "error", "build has errored")

    @recvcoroutine
    def control_handler(self):
        """
        Coroutine that receives control data chunks via yield, and
        interprets them.
        Note: The data chunks are entirely untrusted.
        """
        data = bytearray()
        while True:
            if self.raw_file is not None:
                # control stream is in raw binary mode
                if not data:
                    # wait for more data
                    data += yield

                if len(data) < raw_remaining:
                    self.raw_file.write(data[:self.raw_remaining])
                    data = data[self.raw_remaining:]
                    self.raw_file.close()
                    self.raw_file = None
                else:
                    self.raw_file.write(data)
                    self.raw_remaining -= len(data)
                    data = bytearray()
                continue

            # control stream is in regular JSON+'\n' mode
            newline = data.rfind(b"\n")
            if newline < 0:
                if len(data) > (8 * 1024 * 1024):
                    # chantal is trying to crash us with a >= 8MiB msg
                    raise ValueError("Control message too long")
                # wait for more data
                data += yield
                continue

            msgs, data = bytes(data[:newline]), data[newline + 1:]

            for msg in msgs.split(b"\n"):
                self.control_message(msg.decode().strip())

    def control_message(self, msg):
        """
        control message parser, chantal sends state through this channel.
        """

        msg = json.loads(msg)

        cmd = msg["cmd"]

        if cmd == 'job-state':
            self.set_state(msg["state"], msg["text"])

        elif cmd == 'step-state':
            self.current_step = msg["step"]
            self.set_step_state(msg["step"], msg["state"], msg["text"])

        elif cmd == 'output-item':
            name = msg["name"]

            if self.current_output_item is None:
                raise ValueError("no data received for " + name)
            if self.current_output_item.name != name:
                raise ValueError(
                    "wrong output item name: " + name + ", "
                    "expected: " + self.current_output_item.name
                )

            self.send_update(self.current_output_item)
            self.current_output_item = None

        elif cmd in {'output-dir', 'output-file'}:
            path = msg["path"]
            if '/' in path:
                if self.current_output_item is None:
                    raise ValueError("no current output item")
                self.current_output_item.validate_path(path)
            else:
                if self.current_output_item is not None:
                    raise ValueError("an output item is already present")

                self.current_output_item = OutputItem(
                    path,
                    isdir=(cmd == 'output-dir')
                )

            if cmd == 'output-file':
                # prevent attackers from using negative integers/floats
                size = abs(int(msg["size"]))
            else:
                size = 0

            # also account for metadata size
            # (prevent DOSers from creating billions of empty files)
            self.current_output_item.size += (size + 512)
            if self.current_output_item.size > self.remaining_output_size:
                raise ValueError("output size limit exceeded")

            pathobj = self.path.joinpath(path)
            if pathobj.exists():
                raise ValueError("duplicate output path: " + path)

            if CFG.args.volatile:
                logging.warn("'%s' ignored because of "
                             "volatile mode active." % cmd)
            elif cmd == 'output-file':
                self.raw_file = pathobj.open('wb')
                self.raw_remaining = size
            else:
                pathobj.mkdir(parents=True, exist_ok=True)

        else:
            raise ValueError("unknown build control command: %r" % (cmd))
