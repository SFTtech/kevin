"""
Parser code for the control flow file.
"""

import shlex


class ParseError(ValueError):
    """ Control file parsing error """
    def __init__(self, lineno, text):
        super().__init__(lineno + 1, text)


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


def parse_control_file(data, args):
    """
    Parses control file contents, yields a list of steps.
    """
    stepnames = set()
    all_outputs = set()

    result = []

    for lineno, line, isindented in preprocess_lines(data, args):
        if isindented:
            # step content
            try:
                result[-1].parse_line(line, lineno, all_outputs)
            except IndexError:
                raise ParseError(lineno, "expected step header") from None
        else:
            # step header/name
            result.append(Step(line, lineno, stepnames))

    return result


class Step:
    """
    One build step (consisting of flags, params and a list of commands)

    The constructor takes the declaration line and set of existing names.
    """
    def __init__(self, line, lineno, existing):
        # split line into name and depends
        try:
            name, depends = line.split(':', maxsplit=1)
            self.name = name.strip()

        except ValueError:
            raise ParseError(lineno, "Expected ':'") from None

        # check name
        if not self.name.isidentifier():
            raise ParseError(lineno, "Invalid step name: " + self.name)
        if self.name in existing:
            raise ParseError(lineno, "Duplicate step name: " + self.name)
        existing.add(self.name)

        # check depends
        self.depends = []
        for depend in depends.split(' '):
            depend = depend.strip()
            if not depend:
                continue
            if depend not in existing:
                raise ParseError(lineno, "Undefined depend step: " + depend)

            self.depends.append(depend)

        self.in_header = True
        self.commands = []

        # the various attributes that may be set in header lines.
        self.hidden = False  # hide the step from step list
        self.skip = False    # don't perform the step
        self.outputs = []    # (inpath, outpath) for files to store
        self.env = {}        # key-value for step environment variables
        self.cwd = None      # directory to cd to for the whole step

    def parse_line(self, line, lineno, all_outputs):
        """ Parses one line that was written as part of the section. """
        header_marker = '-'
        header_cmdstart = ':'

        if line.startswith(header_marker):
            if not line.startswith(header_marker + ' '):
                raise ParseError(lineno, "Expected ' ' after '%s'" % (
                    header_marker))

            if not self.in_header:
                raise ParseError(lineno, "Expected shell command, "
                                         "got '%s '" % (header_marker))

            key, sep, val = line[2:].partition(header_cmdstart)
            key = key.strip()
            if not sep:
                val = None
            else:
                val = val.strip()
            self.process_header(key, val, lineno, all_outputs)
        else:
            self.in_header = False
            self.commands.append(line)

    def process_header(self, key, val, lineno, all_outputs):
        """
        Parses a section header line

        key: what does this header do?
        val: definition of the specific header action
        """
        if key in {"hidden", "skip"}:
            # boolean flags
            if val is not None:
                raise ParseError(lineno, key + " is just a flag, nothing more.")
            setattr(self, key, True)

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
            self.outputs.append((output_source, output_dest))

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
                self.env[name] = val

        elif key == "trigger":
            raise NotImplementedError("TODO: success triggers (e.g. badge)")

        elif key == "cwd":
            custom_cwd = shlex.split(val)
            if not custom_cwd or len(custom_cwd) != 1:
                raise ParseError(lineno,
                                 "invalid workdir definition: "
                                 "%s" % (val,))

            self.cwd = custom_cwd[0]

        else:
            raise ParseError(lineno, "Unknown key: " + key)
