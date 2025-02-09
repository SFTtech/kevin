"""
The kevin-ci component that runs inside the builder machine.
"""

import argparse
import typing

Args = typing.NewType('Args', argparse.Namespace)
