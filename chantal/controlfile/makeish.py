"""
Parser code for the control flow file.
"""

import shlex
from pathlib import Path

from . import ControlFile, Step, ParseError
from ..error import FatalJobError


class MakeishControlFile(ControlFile):
    def __init__(self, path: Path, args) -> None:
        super().__init__()

        try:
            with path.open() as controlfile:
                self._steps = parse_control_file(controlfile.read(), args)

        except FileNotFoundError:
            raise FatalJobError(
                "no kevin config file named '%s' was found" % (args.filename))

        except ParseError as exc:
            raise FatalJobError("%s:%d: %s" % (args.filename, exc.args[0],
                                               exc.args[1]))


    def get_steps(self) -> list[Step]:
        return self._steps


def get_indent(line):
    """
    Given a line, returns the whitespace that the line was indented with.
    """
    return line[:-len(line.lstrip())]


def preprocess_lines(data, args):
    """
    Preprocesses the raw text in data,

     - splitting it in lines
     - discarding empty lines and comments
     - verifying that all lines are indented the same way

    Yields tuples of lineno, line, isindented
    """
    condition_env = args.__dict__.copy()

    file_indent = None
    for lineno, line in enumerate(data.split('\n')):
        indent = get_indent(line)

        # make sure that all lines are indented the same way.
        if indent:
            if file_indent is None:
                file_indent = indent
            elif indent != file_indent:
                raise ParseError(
                    lineno,
                    "Illegal indent; expected %r" % file_indent
                )

        # check the condition at the end of the line
        if line.endswith(" ?)"):
            try:
                line, condition = line[:-3].rsplit("(? if ", maxsplit=1)
            except ValueError:
                raise ParseError(
                    lineno,
                    "Expected '(? if ' to match ' ?)' at end of line"
                ) from None

            try:
                condition_env["lineno"] = lineno
                # yes, we eval the condition expression.
                # the user has code execution anyway.
                # pylint: disable=eval-used
                condition_result = eval(condition, condition_env)
            except Exception as exc:
                raise ParseError(
                    lineno,
                    "Could not eval() condition: %r" % exc
                ) from None

            if not condition_result:
                # the line is disabled.
                continue

        # remove comments at the end of the line
        line = line.partition('#')[0].strip()
        # skip empty lines
        if not line:
            continue

        yield lineno, line, bool(indent)


def parse_control_file(data, args) -> list[Step]:
    """
    Parses control file contents, yields a list of steps.
    """
    # names of steps
    stepnames: set[str] = set()
    # names of output files
    all_outputs: set[str] = set()

    results: list[MakeishStep] = []

    for lineno, line, isindented in preprocess_lines(data, args):
        if isindented:
            # step content
            try:
                results[-1].parse_line(line, lineno, all_outputs)
            except IndexError:
                raise ParseError(lineno, "expected step header") from None
        else:
            # step header/name
            results.append(MakeishStep(line, lineno, stepnames))

    steps: list[Step] = list()
    for result in results:
        steps.append(result.get_step())
    return steps


class MakeishStep:
    """
    One job step (consisting of flags, params and a list of commands)

    The constructor takes the declaration line and set of existing names.
    """
    def __init__(self, line: str, lineno: int, existing_steps: set[str]) -> None:
        # split line into name and depends
        try:
            name, depends = line.split(':', maxsplit=1)
            self.name = name.strip()

        except ValueError:
            raise ParseError(lineno, "Expected ':'") from None

        # check name
        if not self.name.isidentifier():
            raise ParseError(lineno, "Invalid step name: " + self.name)
        if self.name in existing_steps:
            raise ParseError(lineno, "Duplicate step name: " + self.name)
        existing_steps.add(self.name)

        # check depends
        self.depends = []
        for depend in depends.split(' '):
            depend = depend.strip()
            if not depend:
                continue
            if depend not in existing_steps:
                raise ParseError(lineno, "Undefined depend step: " + depend)

            self.depends.append(depend)

        self._in_header = True
        self._commands: list[str] = []

        # the various attributes that may be set in header lines.
        self._hidden = False  # hide the step from step list
        self._skip = False    # don't perform the step
        self._outputs: list[tuple[str, str]] = []    # (inpath, outpath) for files to store
        self._env: dict[str, str] = {}        # key-value for step environment variables
        self._cwd: str | None = None      # directory to cd to for the whole step

    def get_step(self) -> Step:
        return Step(
            name = self.name,
            depends = self.depends,
            cwd = self._cwd,
            commands = self._commands,
            env = self._env,
            outputs = self._outputs,
            skip = self._skip,
            hidden = self._hidden,
        )

    def parse_line(self, line, lineno, all_outputs) -> None:
        """ Parses one line that was written as part of the Step section. """
        header_marker = '-'
        header_cmdstart = ':'

        if line.startswith(header_marker):
            if not line.startswith(header_marker + ' '):
                raise ParseError(lineno, "Expected ' ' after '%s'" % (
                    header_marker))

            if not self._in_header:
                raise ParseError(lineno, "Expected shell command, "
                                         "got '%s '" % (header_marker))

            key, sep, val = line[2:].partition(header_cmdstart)
            key = key.strip()
            if not sep:
                val = None
            else:
                val = val.strip()
            self._process_header(key, val, lineno, all_outputs)
        else:
            self._in_header = False
            self._commands.append(line)

    def _process_header(self, key, val, lineno, all_outputs) -> None:
        """
        Parses a section header line

        key: what does this header do?
        val: definition of the specific header action
        """
        if key in {"hidden", "skip"}:
            # boolean flags
            if val is not None:
                raise ParseError(lineno, key + " is just a flag, nothing more.")
            if key == "hidden":
                self._hidden = True
            elif key == "skip":
                self._skip = True
            else:
                raise Exception("unhandled flag type")

        elif key == "output":
            # val is something like "input/file/name" as "outputname"
            if not val:
                raise ParseError(lineno, "empty output definition.")

            try:
                output_cfg = shlex.split(val)
            except ValueError as exc:
                raise ParseError(lineno, "failed to parse: %s" % exc)

            if len(output_cfg) not in (1, 3):
                raise ParseError(
                    lineno,
                    "output definition must be one of "
                    "'filename' or 'filename as outputname'"
                )

            if len(output_cfg) == 3:
                alias_hint = ""
                output_source, _, output_dest = output_cfg
            else:
                alias_hint = ", use the 'as' statement?"
                output_source = output_dest = output_cfg[0]

            if output_dest[0] in ('_', '.'):
                raise ParseError(lineno,
                                 "output destination filename "
                                 "starts with . or _: %s" % output_dest)

            if '/' in output_dest:
                raise ParseError(lineno,
                                 "'/' in output destination%s: "
                                 "%s" % (alias_hint, output_dest))

            if (not output_dest or
                    not output_dest.isprintable() or
                    not output_dest[0].isalpha()):

                raise ParseError(lineno,
                                 "Illegal output destination "
                                 "filename: %s" % output_dest)

            if output_dest in all_outputs:
                raise ParseError(lineno, "Duplicate filename: %s" % val)

            all_outputs.add(output_dest)
            self._outputs.append((output_source, output_dest))

        elif key == "env":
            # env: variable=value mom="really fat"
            for var in shlex.split(val):
                name, sep, val = var.partition('=')
                if not sep:
                    raise ParseError(lineno, "Expected 'var=' in "
                                             "env assignment '%s'" % var)
                if not name.isidentifier():
                    raise ParseError(lineno, "Illegal env "
                                             "var name '%s'" % name)
                self._env[name] = val

        elif key == "trigger":
            raise NotImplementedError("TODO: success triggers (e.g. badge)")

        elif key == "cwd":
            custom_cwd = shlex.split(val)
            if not custom_cwd or len(custom_cwd) != 1:
                raise ParseError(lineno,
                                 "invalid workdir definition: "
                                 "%s" % (val,))

            self._cwd = custom_cwd[0]

        else:
            raise ParseError(lineno, "Unknown key: " + key)
