"""
Tests using wayland core as the implementation
"""

from capability_tests.wayland_client import WaylandClient, WaylandEventWatcher, Window


async def clipboard(wayland_client: WaylandClient, window_factory: callable):
    # This protocol needs a focussed window to set or receive
    # clipboard content.

    """
    Send offer
    Create window
    Wait for keyboard focus
    wl_data_device_set_selection
    wl_display_roundtrip
    destroy window
    did_set_selection_callback

    Need to make our own file descriptor for the data
    """

    # Set up handler for the window getting keyboard focus
    seat = wayland_client.binding("wl_seat")
    keyboard = seat.get_keyboard()
    keyboard_enter_listener = WaylandEventWatcher(wayland_client)
    keyboard.events.enter += keyboard_enter_listener

    with window_factory() as window:
        # Wait for focus
        keyboard_enter_listener.await_first_event()
        focus_event_id = keyboard_enter_listener.invocations[0]["serial"]

        manager = wayland_client.binding("wl_data_device_manager")

        # Set up clipboard sender
        data_source = manager.create_data_source()
        data_source.offer("text/plain")

        # Set up clipboard receiver
        data_device = manager.get_data_device(
            wayland_client.binding("wl_seat")
        )
        data_offer_listener = WaylandEventWatcher(wayland_client)
        data_device.events.data_offer += data_offer_listener
        # Also say what kind of clipboard we're interested in
        data_device.set_selection(data_source, focus_event_id)

        # Receiver waits for offer event
        data_offer_listener.await_first_event()
