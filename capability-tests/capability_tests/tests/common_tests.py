"""
If we have different ways of implementing test functionality we might otherwise be duplicating all the assertions involved. For example the wlroots and xdg_desktop_portal mouse click test would look substantially the same aside from how to actually click the mouse.

This file contains the common test assertion code.
"""
import time


async def mouse_move_absolute(window_factory: callable, mouse_move: callable):
    with window_factory() as window:
        for pos in range(50, 100, 10):
            await mouse_move(pos, pos)

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


async def mouse_click(window_factory: callable, mouse_move: callable, mouse_click: callable):
    with window_factory() as window:
        # Make sure we're on top of the window
        await mouse_move(50, 50)
        for button in ("left", "right", "middle"):
            for button_state in ("pressed", "released"):
                await mouse_click(button, button_state)
        # GNOME needs a little sleep here
        time.sleep(0.1)

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


async def mouse_scroll(window_factory: callable, mouse_move: callable, mouse_scroll: callable):
    with window_factory() as window:
        # Make sure we're on top of the window
        await mouse_move(50, 50)

        # Send scroll event
        await mouse_scroll()

    events = window.events_of_type("wl_pointer.axis")
    assert len(events) > 0


async def keyboard_press(window_factory: callable, keypress: callable):
    with window_factory() as window:
        await keypress("pressed", 17) # ctrl
        await keypress("pressed", 65) # a
        await keypress("released", 65) # a
        await keypress("released", 17) # ctrl
        # GNOME needs a little sleep here
        time.sleep(0.1)

    events = [
        f"{event['key']}.{event['state']}"

        for event in window.events_of_type("wl_keyboard.key")
    ]
    assert events == ["17.pressed", "65.pressed", "65.released", "17.released"]
