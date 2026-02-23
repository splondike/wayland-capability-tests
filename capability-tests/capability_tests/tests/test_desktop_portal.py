"""
Implementations that use the XDG Desktop Portals

https://flatpak.github.io/xdg-desktop-portal/docs/
"""
import asyncio
import random
import time
from typing import Tuple

import dbus_fast.introspection
from dbus_fast.service import Variant
from dbus_fast.aio import MessageBus

from capability_tests.qemu import TestRunnerCommands


async def mouse_move_absolute(
    dbus_client: MessageBus,
    window_factory: callable,
    runner_commands: TestRunnerCommands
):
    remote_desktop, monitor_stream_id, session_handle = await _build_remote_desktop_connection(
        dbus_client
    )

    with window_factory() as window:
        for pos in range(100, 150, 10):
            await remote_desktop.call_notify_pointer_motion_absolute(
                session_handle,
                {},
                monitor_stream_id,
                pos,
                pos
            )
            time.sleep(0.1)

    # mouse move events
    events = [
        e
        for e in window.events if e["type"] == "wl_pointer.motion"
    ][:3]
    assert len(events) >= 3

    xdiff1 = events[-2]["x"] - events[-3]["x"]
    xdiff2 = events[-1]["x"] - events[-2]["x"]
    ydiff1 = events[-2]["y"] - events[-3]["y"]
    ydiff2 = events[-1]["y"] - events[-2]["y"]

    assert xdiff1 == xdiff2
    assert ydiff1 == ydiff2


async def _build_remote_desktop_connection(dbus_client: MessageBus):
    # Normally you have to click a button to authorize an application to
    # use the RemoteDesktop protocols. Having to click this auth dialog is
    # inconvenient for testing however, so instead we edit the
    # authorization data store to include the following fixed token
    # with all permissions granted.
    REMOTE_DESKTOP_RESTORE_TOKEN = "9c437d05-3e48-4f8e-81d6-cea0001564ff"

    # First pre-authorize our remote desktop session
    compositor_name = await _detect_compositor_name(dbus_client)
    obj = await get_proxy_object(
        dbus_client,
        "org.freedesktop.impl.portal.PermissionStore",
        "/org/freedesktop/impl/portal/PermissionStore"
    )
    ps = obj.get_interface("org.freedesktop.impl.portal.PermissionStore")
    table = "remote-desktop"
    if REMOTE_DESKTOP_RESTORE_TOKEN not in (await ps.call_list(table)):
        # I worked out this value by first allowing the auth dialog to pop up,
        # then looking at what value was saved in the PermissionStore. See
        # the debug_dbus_permission_store command in app.py
        token_value = None
        if compositor_name == "gnome":
            token_value = ("GNOME", 1, Variant("(xxuba(uuv))", (0, 1, 7, False, [
                (0, 1, Variant("s", "RHT:QEMU Monitor:0x00000000"))
            ])))
        else:
            token_value = ("KDE", 1, Variant("ay", b"\x00\x00\x00\x02\x00\x00\x00\x0e\x00d\x00e\x00v\x00i\x00c\x00e\x00s\x00\x00\x00\x03\x00\x00\x00\x00\x03\x00\x00\x00$\x00s\x00c\x00r\x00e\x00e\x00n\x00S\x00h\x00a\x00r\x00e\x00E\x00n\x00a\x00b\x00l\x00e\x00d\x00\x00\x00\x01\x00\x01"))
        await ps.call_set(
            table,
            True,
            REMOTE_DESKTOP_RESTORE_TOKEN,
            {"": ["yes"]},
            Variant(
                "(suv)",
                token_value
            )
        )

    # Then set up a remove desktop XDG desktop portal session
    obj = await get_proxy_object(
        dbus_client,
        "org.freedesktop.portal.Desktop",
        "/org/freedesktop/portal/desktop"
    )
    rd = obj.get_interface("org.freedesktop.portal.RemoteDesktop")
    session_request_token, session_future = await create_request_future(
        dbus_client
    )
    await rd.call_create_session({
        "handle_token": Variant("s", session_request_token),
        "session_handle_token": Variant("s", "testsession"),
    })
    session_handle = (await session_future)["session_handle"].value

    sc = obj.get_interface("org.freedesktop.portal.ScreenCast")
    await sc.call_select_sources(session_handle, {})

    select_devices_request_token, select_devices_future = await create_request_future(
        dbus_client
    )
    options = {
        "persist_mode": Variant("u", 2),
        "restore_token": Variant("s", REMOTE_DESKTOP_RESTORE_TOKEN),
        "handle_token": Variant("s", select_devices_request_token),
        # TODO: Types should not be required according to the spec,
        # but KDE's portal implementation currently requires it
        "types": Variant("u", 2 | 1),
    }
    await rd.call_select_devices(session_handle, options)
    await select_devices_future

    start_request_token, start_future = await create_request_future(
        dbus_client
    )
    await rd.call_start(session_handle, "", {
        "handle_token": Variant("s", start_request_token),
    })
    start_result = await start_future
    monitor_stream_id, _ = start_result["streams"].value[0]

    return rd, monitor_stream_id, session_handle

async def _detect_compositor_name(dbus_client: MessageBus):
    obj = await get_proxy_object(
        dbus_client,
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus"
    )
    interface = obj.get_interface("org.freedesktop.DBus")
    for namespace in sorted(await interface.call_list_names()):
        if namespace == "org.kde.plasmashell":
            return "kde"
        elif namespace == "org.gnome.Shell":
            return "gnome"

    raise RuntimeError(
        "Couldn't find known compositor dbus namespace"
    )


async def create_request_future(
    dbus_client: MessageBus
) -> Tuple[str, asyncio.Future]:
    """
    XDG Desktop Portals often return values via a 'Result' object that
    gives you the value asyncronously via a signal. This method
    creates a Result object and returns a token/id you can use in method
    calls to refer to it and a future that can be awaited to get the
    value.

    See https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Request.html#org-freedesktop-portal-request-response
    """

    sender = dbus_client.unique_name[1:].replace(".", "_")
    token = str(random.random()).replace(".", "")
    request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    # The request_path object doesn't exist yet, but we want to register
    # to listen to its signals ahead of time to avoid a race condition.
    # This means we have to manually construct the introspection object
    # rather than deriving it automatically like usual.
    introspection = dbus_fast.introspection.Node(
        request_path,
        is_root=True,
        interfaces=[
            dbus_fast.introspection.Interface(
                "org.freedesktop.portal.Request",
                methods=[
                    dbus_fast.introspection.Method("Close",),
                ],
                signals=[
                    dbus_fast.introspection.Signal(
                        "Response",
                        args=[
                            dbus_fast.introspection.Arg(
                                "u",
                                dbus_fast.introspection.ArgDirection.OUT,
                                "response"
                            ),
                            dbus_fast.introspection.Arg(
                                "a{sv}",
                                dbus_fast.introspection.ArgDirection.OUT,
                                "results"
                            )
                        ],
                    )
                ],
            ),
        ]
    )

    request = dbus_client.get_proxy_object(
        "org.freedesktop.portal.Desktop",
        request_path,
        introspection
    )
    future = asyncio.get_running_loop().create_future()

    def handler(status, value):
        future.set_result(value)

    interface = request.get_interface("org.freedesktop.portal.Request")
    interface.on_response(handler)

    return token, future


async def get_proxy_object(bus, namespace, path):
    """
    Build a DBus object, automatically deriving the interfaces
    available on it.
    """

    definition = await bus.introspect(
        namespace,
        path
    )
    return bus.get_proxy_object(
        namespace,
        path,
        definition
    )
