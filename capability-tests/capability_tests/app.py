import ast
import asyncio
import os
import importlib
import inspect
import logging
import pathlib
import sys
import subprocess
import traceback
from typing import List, Optional

import typer
from dbus_fast.aio import MessageBus

from wayland.parser import WaylandParser
from capability_tests import (
    config,
    test_utils,
    qemu,
    wayland_client as wayland_client_module
)


app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

PROTOCOLS_DIR = "wayland-protocols"


@app.command()
def debug_show_window(seconds_open: int = 2):
    """
    Pops up a wayland window for a few seconds and prints out all the events
    captured by our client libraries
    """

    # Want to show the debug logs from the underlying library too
    # (normally you use the WAYLAND_DEBUG=1 env var for this)
    from wayland import log
    logger = logging.getLogger("wayland")
    logger.enable(log.PROTOCOL_LEVEL)

    wayland_client = wayland_client_module.WaylandClient(
        PROTOCOLS_DIR
    )
    with wayland_client_module.Window(wayland_client) as window:
        import time
        time.sleep(seconds_open)
    print("Window events: ", window.events)


@app.command()
def debug_dbus_list(path_segment: List[str] = typer.Argument(default=None)):
    """
    Prints out available DBus services. These form a tree: namespace,
    object path, interface, methods/properties . If you supply a single
    argument for each of those levels it will print the next level down.

    e.g. dbug-dbus-list org.freedesktop.systemd1 /org/freedesktop/LogControl1
    would print all the interfaces in that namespace + object path
    """
    path_segment = path_segment or []

    async def get_proxy_object(bus, namespace, path):
        definition = await bus.introspect(
            namespace,
            path
        )
        return bus.get_proxy_object(
            namespace,
            path,
            definition
        )

    async def inner():
        bus = await MessageBus().connect()
        if len(path_segment) == 0:
            # List out all the top level namespaces
            obj = await get_proxy_object(
                bus,
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus"
            )
            interface = obj.get_interface("org.freedesktop.DBus")
            for namespace in sorted(await interface.call_list_names()):
                if namespace.startswith(":"):
                    # These ones aren't useful to show
                    continue
                print(namespace)
        elif len(path_segment) == 1:
            # List out the object paths within the namespace
            all_paths = []
            path_stack = [""]
            while True:
                if len(path_stack) > 0:
                    path = path_stack.pop()
                else:
                    break
                obj = await get_proxy_object(
                    bus,
                    path_segment[0],
                    path or "/"
                )
                pending_paths = []
                for node in obj.introspection.nodes:
                    pending_paths.append(path + "/" + node.name)

                if len(pending_paths) > 0:
                    path_stack += pending_paths
                else:
                    all_paths += [path]

            for path in sorted(all_paths):
                print(path)
        elif len(path_segment) == 2:
            # List out the interfaces within the object path
            obj = await get_proxy_object(
                bus,
                path_segment[0],
                path_segment[1]
            )
            for name in sorted([
                interface.name
                for interface in obj.introspection.interfaces
            ]):
                print(name)
        elif len(path_segment) == 3:
            # List out the methods and properties within the interface
            obj = await get_proxy_object(
                bus,
                path_segment[0],
                path_segment[1]
            )
            for interface in obj.introspection.interfaces:
                if interface.name == path_segment[2]:
                    # The type signature notation is defined here
                    # https://dbus.freedesktop.org/doc/dbus-specification.html#type-system
                    # Happy for somebody to decode that to something more
                    # readable if desired.

                    print("The letters after the names below specify the type of the name:\n")
                    print(
                        "\n".join([
                            "b = boolean",
                            "u = unsigned number",
                            "s = string",
                            "o = dbus object path (string)",
                            "v = dynamic type",
                            "ax = array of x",
                            "{xy} = map from x to y",
                            "(xyz) = tuple with xyz as members"
                        ])
                    )
                    print("\nSee https://dbus.freedesktop.org/doc/dbus-specification.html#type-system for a full explanation.\n")

                    print("# Methods:\n")
                    for method in interface.methods:
                        print(method.name)
                        for arg in method.in_args:
                            print("  input", arg.name, arg.type.signature)
                        for arg in method.out_args:
                            print("  output", arg.name, arg.type.signature)
                    print("\n# Properties:\n")
                    for property in interface.properties:
                        print(property.name, property.signature)

    asyncio.run(inner())


@app.command()
def debug_wayland_list():
    """
    Lists the Wayland protocols supported by the current Wayland compositor.
    """

    wayland_client = wayland_client_module.WaylandClient(
        PROTOCOLS_DIR
    )
    wayland_client.require_protocols([])
    for row in _format_table(
        sorted([
            [i, str(v)]
            for _, i, v in wayland_client.available_interfaces.values()
        ], key=lambda x: x[0]),
        header=["interface", "version"]
    ):
        print(row)


@app.command()
def tests_run(
    ids: List[str] = typer.Argument(default=None),
    compositor: Optional[str] = None,
    compositor_skip_failing: bool = False
):
    """
    Run the tests matching the given filters. ids should
    be the 'implementation' or path to the code.
    """
    wayland_client = wayland_client_module.WaylandClient(
        PROTOCOLS_DIR
    )

    def window_factory() -> wayland_client_module.Window:
        return wayland_client_module.Window(
            wayland_client
        )

    async def inner():
        dbus_client = await MessageBus().connect()
        # We have the monitor socket listening on this port
        runner_commands = qemu.TestRunnerCommands.build_tcp(
            2134,
            "localhost"
        )

        conf = config.TestConfig.build_from_default_filepath()
        tests = [
            test
            for test in conf.list_tests()
            # Filter the tests by compositor, ids, or by failure state
            if (
                (compositor is None or compositor in test["compositors"]) and
                (
                    compositor is None or not compositor_skip_failing or
                    compositor not in test.get("failing_compositors", [])
                ) and
                (ids is None or test["implementation"] in ids)
            )
        ]
        name_prefixes = _format_table([
            [test["implementation"] + " ..."]
            for test in tests
        ], pad_chars={1: "."})

        failed_tests = []
        for test, name_prefix in zip(tests, name_prefixes):
            module, function_name = test["implementation"].rsplit(
                ".",
                maxsplit=1
            )
            test_module = importlib.import_module(
                f"capability_tests.tests.{module}"
            )
            print(name_prefix, end="")

            passed = True
            try:
                func = getattr(test_module, function_name)
                args = []
                for name in inspect.signature(func).parameters.keys():
                    if name == "wayland_client":
                        args.append(wayland_client)
                    elif name == "dbus_client":
                        args.append(dbus_client)
                    elif name == "runner_commands":
                        args.append(runner_commands)
                    elif name == "window_factory":
                        args.append(window_factory)
                    else:
                        msg = f" Unhandled implementation arg name: {name}"
                        assert False, msg
                result = func(*args)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                failed_tests.append((test["implementation"], e))
                passed = False

            print(" PASS" if passed else " FAIL")

        if failed_tests:
            print()
            for test_name, exception in failed_tests:
                print(f"failed test: {test_name}")
                traceback.print_exception(exception)
                if isinstance(exception, AssertionError):
                    test_utils.print_assertion_values(exception)
                print("\n----\n")

        print()
        print(
            f"Passed: {len(tests) - len(failed_tests)} Failed: {len(failed_tests)}"
        )
        if len(failed_tests) > 0:
            sys.exit(2)

    asyncio.run(inner())


@app.command()
def tests_list():
    """
    Output all the tests with their autogenerated id.
    """

    conf = config.TestConfig.build_from_default_filepath()
    header = ["implementation", "feature", "compositors"]
    out = _format_table([
        [
            test["implementation"],
            test["feature"],
            "|".join(test["compositors"])
        ]
        for test in conf.list_tests()
    ], header=header)

    print("\n".join(out))


@app.command()
def wayland_fetch_protocols(update_repos: bool = False):
    """
    Download the various Wayland protocol definition files we need and
    build the protocols.json file we'll use from them.
    """

    base_dir = pathlib.Path(__file__).parent.parent / PROTOCOLS_DIR
    repos = [
        ("wayland-explorer", "https://github.com/vially/wayland-explorer.git"),
    ]
    for name, url in repos:
        path = base_dir / name
        if path.exists():
            if update_repos:
                subprocess.run(
                    ["git", "pull", "--recurse-submodules"],
                    cwd=path,
                    check=True
                )
        else:
            subprocess.run(
                ["git", "clone", "--recurse-submodules", url, path],
                check=True
            )

    protocols_dir = base_dir / "wayland-explorer" / "protocols"
    with open(base_dir / "protocols.json", "w") as fh:
        _wayland_build_json(fh, [
            str(protocols_dir / "libwayland" / "protocol"),
            str(protocols_dir / "wayland" / "stable"),
            str(protocols_dir / "wlr"),
        ])


def _wayland_build_json(protocol_file, protocols_dirs: List[str]):
    """
    Parse the Wayland protocol definitions in protocols_dirs and writes
    them out as a JSON document suitable for usage by this library
    to protocol_file
    """

    parser = WaylandParser()

    for dir in protocols_dirs:
        if not os.path.exists(dir):
            raise RuntimeWarning(f"Input dir doesn't exist: {dir}")

        for root, _, files in os.walk(dir):
            for file in files:
                full_file = os.path.join(root, file)
                if file.endswith(".xml"):
                    parser.parse(full_file)

    duplicate = False
    for interface_name, definition in parser.interfaces.items():
        for category in ("events", "requests", "enums"):
            existing = set()
            for item in definition[category]:
                name = item["name"]
                if name in existing:
                    print(
                        f"Duplicate definition: {interface_name}.{category}.{name}",
                        file=sys.stderr
                    )
                    duplicate = True
                else:
                    existing.add(name)

    if duplicate:
        # Wayland protocol doesn't allow more than one thing with the same name
        # I think. Having duplicate definitions caused me a few hours of
        # debugging at one point, hence this check!
        print("Error: Duplicate definitions. You're probably including multiple files that define the same interface.", file=sys.stderr)
        sys.exit(1)

    protocol_file.write(parser.to_json())


def _format_table(data, header=None, pad_chars=None) -> List[str]:
    """
    Helper to convert a list of lists into a nicely formatted left
    aligned table. Outputs a list of rows.
    """

    pad_chars = pad_chars or {}
    col_lengths = {}
    aug_data = data
    if header:
        aug_data = [header] + aug_data
    for row in aug_data:
        for idx, col in enumerate(row):
            length = len(col)
            if (idx not in col_lengths) or (length > col_lengths[idx]):
                col_lengths[idx] = length

    rtn = []
    for row in aug_data:
        out = []
        for idx, col in enumerate(row):
            sep = pad_chars.get(idx, " ")
            length = len(col)
            out.append(col + (sep * (col_lengths[idx] - length)))
        rtn.append(" ".join(out))

    return rtn
