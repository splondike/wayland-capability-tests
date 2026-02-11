"""
Implementations that rely on wlroots specific Wayland protocols.
"""

import time

from capability_tests.wayland_client import WaylandClient, Window


def mouse_move_absolute(wayland_client: WaylandClient, window_factory: callable):
    with window_factory() as window:
        pointer = wayland_client.binding(
            "zwlr_virtual_pointer_manager_v1"
        ).create_virtual_pointer(wayland_client.binding("wl_seat"))
        # The last two arguments are the max value for the x and y coords.
        # So we're treating the screen like it's 100 units in both directions.
        for pos in range(10, 50, 10):
            pointer.motion_absolute(1, pos, pos, 100, 100)
            time.sleep(0.1)
        pointer.destroy()

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


def mouse_click(wayland_client: WaylandClient, window_factory: callable):
    with window_factory() as window:
        pointer = wayland_client.binding(
            "zwlr_virtual_pointer_manager_v1"
        ).create_virtual_pointer(wayland_client.binding("wl_seat"))
        # Make sure we're on top of the window
        pointer.motion_absolute(1, 50, 50, 100, 100)
        for button in (272, 273, 274):
            for button_state in (1, 0):
                pointer.button(1, button, button_state)
        pointer.destroy()

    events = [
        e["button"] + "." + e["state"]
        for e in window.events if e["type"] == "wl_pointer.button"
    ]
    expected = [
        b + "." + s
        for b in ("left", "right", "middle")
        for s in ("pressed", "released")
    ]
    assert events == expected


def mouse_scroll(wayland_client: WaylandClient, window_factory: callable):
    with window_factory() as window:
        pointer = wayland_client.binding(
            "zwlr_virtual_pointer_manager_v1"
        ).create_virtual_pointer(wayland_client.binding("wl_seat"))
        # Make sure we're on top of the window
        pointer.motion_absolute(1, 50, 50, 100, 100)

        # Send scroll event (vertical scroll by 10 units in 1ms)
        pointer.axis(
            1,
            0,
            15.0
        )
        pointer.destroy()

    # mouse scroll
    events = window.events_of_type("wl_pointer.axis")
    assert len(events) > 0
