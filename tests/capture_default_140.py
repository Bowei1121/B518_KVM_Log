"""Loaded via pytest.ini addopts = -p capture_default_140; this tryfirst wrapper of
pytest_load_initial_conftests runs before _pytest.capture starts global capturing.
Explicit -s/--capture (including PYTEST_ADDOPTS) wins; conftest still rejects fd on win32.
"""

import argparse
import sys

import pytest


def default_capture(platform, explicit_capture):
    return "sys" if platform == "win32" and explicit_capture is None else None


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args):
    if not early_config.pluginmanager.is_blocked("capture"):
        ns = parser.parse_known_args(args, namespace=argparse.Namespace(capture=None))
        capture = default_capture(sys.platform, ns.capture)
        if capture is not None:
            early_config.known_args_namespace.capture = capture
            args[:0] = [f"--capture={capture}"]
    return (yield)
