"""
Implementations that rely on wlroots specific Wayland protocols.
"""

import time
from functools import partial

from capability_tests.wayland_client import WaylandClient, Window
from capability_tests.tests import common_tests


async def mouse_move_absolute(wayland_client: WaylandClient, window_factory: callable):
    pointer = wayland_client.binding(
        "zwlr_virtual_pointer_manager_v1"
    ).create_virtual_pointer(wayland_client.binding("wl_seat"))

    await common_tests.mouse_move_absolute(
        window_factory,
        partial(_mouse_move_absolute, pointer)
    )
    pointer.destroy()


async def mouse_click(wayland_client: WaylandClient, window_factory: callable):
    pointer = wayland_client.binding(
        "zwlr_virtual_pointer_manager_v1"
    ).create_virtual_pointer(wayland_client.binding("wl_seat"))

    async def _mouse_click(button: str, state: str):
        button_code = {
            "left": 272,
            "right": 273,
            "middle": 274,
        }[button]
        state_code = {
            "pressed": 1,
            "released": 0,
        }[state]
        pointer.button(1, button_code, state_code)

    await common_tests.mouse_click(
        window_factory,
        partial(_mouse_move_absolute, pointer),
        _mouse_click
    )
    pointer.destroy()


async def mouse_scroll(wayland_client: WaylandClient, window_factory: callable):
    pointer = wayland_client.binding(
        "zwlr_virtual_pointer_manager_v1"
    ).create_virtual_pointer(wayland_client.binding("wl_seat"))

    async def _mouse_scroll():
        # Send scroll event (vertical scroll by 15 units in 1ms)
        pointer.axis(
            1,
            0,
            15.0
        )

    await common_tests.mouse_scroll(
        window_factory,
        partial(_mouse_move_absolute, pointer),
        _mouse_scroll
    )
    pointer.destroy()


async def _mouse_move_absolute(pointer, xpos: int, ypos: int):
    # The last two arguments are the max value for the x and y coords.
    # So we're treating the screen like it's 100 units in both directions.
    pointer.motion_absolute(1, xpos, ypos, 100, 100)
    time.sleep(0.1)
