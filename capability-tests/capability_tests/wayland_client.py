"""
Methods to help test Wayland compositors. E.g. an easy to use interface for
sending and receiving events.
"""

import os
import logging
import mmap
import struct
import time
import tempfile
import threading
from typing import Tuple

from wayland.proxy import Proxy


logger = logging.getLogger(__name__)


class WaylandClient():
    """
    Wayland client for communicating with a compositor.

    Wrapper around the python-wayland library to give some convenience
    methods, including one to handle race conditions with
    setting up event listeners.

    python-wayland from version 1.0 uses a background thread to queue
    events. If an event listener isn't set up before the event fires
    then it may be dropped. It isn't always possible to do this, e.g.
    for the wl_display.sync() event where you register the event listener
    on the object returned from the sync() call and the compositor could
    send its response before you get a chance to do that. To handle this
    use:

    ```
    with client.event_callback_lock():
        obj = wl_display.sync()
        obj.events.done += my_handler
    ```
    """

    def __init__(self, protocols_dir: str):
        """
        protocols_dir must contain a prococols.json file with the protocols
        we want to support.
        """

        self.wl = Proxy()
        # Actual live instances of objects we call methods on
        self.bindings = {}
        # A list of Wayland interfaces the compositor advertises
        self.available_interfaces = {}
        # Proxy classes derived entirely from protocols_dir. The only
        # one of these you can call directly is wl_display
        self.protocol_classes = {}
        self.wl.initialise(self.protocol_classes)
        self.bindings["wl_display"] = self.protocol_classes["wl_display"]()

        # The xdg_wm_base object periodically emits ping events which you
        # must reply to or be deemed inactive. Centralise handling that here.
        self.xdg_wm_base_ping_handler = False

    def require_protocols(self, protocol_names: list) -> None:
        """
        Ensure this WaylandClient instance has a binding to all the given protocols.
        """

        if len(self.available_interfaces) == 0:
            # Can only call get_registry once, so load up all the information
            # we may need in the future here
            def load_protocols(name, interface, version):
                self.available_interfaces[interface] = (name, interface, version)
            self.bindings["wl_registry"] = self.bindings["wl_display"].get_registry()
            self.bindings["wl_registry"].events.global_ += load_protocols
            self.sync()
            self.bindings["wl_registry"].events.global_ -= load_protocols
            # The Wayland server can send us error responses back. Log those
            # centrally to aid debugging.
            self.bindings["wl_display"].events.error += self._log_compositor_error

        for name in protocol_names:
            if name not in self.protocol_classes:
                raise RuntimeError(
                    f"Your protocols_dir/protocols.json file doesn't have this protocol: {name}"
                )

            if name not in self.bindings:
                self.bindings[name] = self.bindings["wl_registry"].bind(*self.available_interfaces[name])

    def sync(self, timeout=2000) -> None:
        """
        Will block until any pending compositor events have been flushed using
        the sync/done request/event pair designed for this purpose.
        """

        synced = False
        def sync_done(callback_data):
            nonlocal synced
            synced = True
        # Apparently the compositor will take care of garbage collecting this sync object on its end
        with self.event_callback_lock():
            sync_obj = self.bindings["wl_display"].sync()
            sync_obj.events.done += sync_done

        poll_interval = 10
        for _ in range(timeout // poll_interval):
            self.process_messages()
            if synced:
                break
            time.sleep(poll_interval / 1000)

        if not synced:
            raise RuntimeError(f"Didn't receive sync event after {timeout}ms")

    def binding(self, name) -> Proxy.DynamicObject:
        """
        Get an instance of a global object (e.g. wl_seat).
        """

        self.require_protocols([name])
        if name == "xdg_wm_base" and not self.xdg_wm_base_ping_handler:
            xdg_wm_base = self.bindings["xdg_wm_base"]
            def ping_handler(serial):
                xdg_wm_base.pong(serial)
            # If we're using xdg_wm_base then we will start receiving ping
            # events. If we don't respond to them we'll be considered inactive,
            # so handle those events centrally here.
            xdg_wm_base.events.ping += ping_handler
        return self.bindings[name]

    def event_callback_lock(self) -> threading.Lock:
        """
        Returns a lock which you can use to stall processing of
        messages from the compositor. Useful when you need to
        set up an event handler for an object you've just created.
        """

        wl_lib_state = self.bindings["wl_display"]._DynamicObject__state
        wl_lib_state.connect()
        return wl_lib_state._socket.buffer_lock

    def process_messages(self):
        """
        Flush all queued messages from the compositor.
        """

        self.bindings["wl_display"].dispatch_timeout(0.05)

    def make_shared_memory(self, size) -> Tuple[int, mmap.mmap]:
        """
        Makes a shared memory region of the given size.

        Returns a file descriptor and a mmap instance pointing
        to the memory. File descriptor can be sent over the Wayland
        socket.
        """

        fd, path = tempfile.mkstemp(
            prefix="wayland_tests",
            dir=os.environ["XDG_RUNTIME_DIR"]
        )
        os.write(fd, b"\0"*size)
        os.unlink(path)
        obj = mmap.mmap(
            fd,
            size,
            prot=mmap.PROT_READ | mmap.PROT_WRITE,
            flags=mmap.MAP_SHARED
        )

        return fd, obj

    def _log_compositor_error(self, object_id, code, message):
        ref = self.wl.state.object_id_to_object_reference(object_id)
        logger.error("Compositor error on '%s' object: '%s'", ref._name, message)


class WaylandEventWatcher:
    """
    Helper for capturing and waiting on Wayland events. Allows code that
    needs events to be written without callbacks.
    """

    def __init__(self, client: WaylandClient):
        self.client = client
        self.invocations = []

    def __call__(self, **kwargs):
        self.invocations.append(kwargs)

    def await_first_event(self, timeout=2000):
        if len(self.invocations) > 0:
            return

        poll_interval = 10
        for _ in range(timeout // poll_interval):
            self.client.process_messages()
            if len(self.invocations) > 0:
                break
            time.sleep(poll_interval / 1000)

        if len(self.invocations) == 0:
            raise RuntimeError(f"Didn't receive first event after {timeout}ms")


class Window():
    """
    Makes a window in the Wayland compositor that captures events.
    Acts as a context manager to handle garbage collection of objects
    created on the compositor.
    """

    def __init__(self, client: WaylandClient, fullscreen=False):
        self.client = client
        self.fullscreen = fullscreen
        self.events = []
        self.garbage_collection_calls = []

    def __enter__(self):
        self.show()
        return self

    def __exit__(self, *args):
        self.client.sync()
        self.destroy()

    def sync(self):
        self.client.sync()

    def destroy(self):
        for call in reversed(self.garbage_collection_calls):
            call()
        self.garbage_collection_calls = []
        self.sync()

    def show(self):
        # If called twice, destroy the old window first
        if len(self.garbage_collection_calls) > 0:
            self.destroy()

        wl_surface = self.client.binding("wl_compositor").create_surface()
        self.garbage_collection_calls.append(wl_surface.destroy)

        with self.client.event_callback_lock():
            xdg_surface = self.client.binding("xdg_wm_base").get_xdg_surface(wl_surface)
            # Automatically acknowledge any xdg_surface.configure events
            def _ack_xdg_surface_configure_events(serial):
                xdg_surface.ack_configure(serial)
            xdg_surface.events.configure += _ack_xdg_surface_configure_events
            # We also need to wait for the first one
            configure_listener = WaylandEventWatcher(self.client)
            xdg_surface.events.configure += configure_listener
            self.garbage_collection_calls.append(xdg_surface.destroy)
            xdg_toplevel = xdg_surface.get_toplevel()
            self.garbage_collection_calls.append(xdg_toplevel.destroy)

        xdg_toplevel.set_title("wayland_capability_tests_title")
        xdg_toplevel.set_app_id("wayland_capability_tests_title_app_id")
        wl_surface.commit()

        # Wait for compositor to acknowledge everything
        configure_listener.await_first_event()
        xdg_surface.events.configure -= configure_listener

        # Don't bother with working out the actual window size
        # and updating the buffer size. Just make a big enough
        # black background single shot.
        width = 2*1920
        height = 2*1080
        fd, obj = self.client.make_shared_memory(4*width*height)
        self.garbage_collection_calls.append(lambda: os.close(fd))
        self.garbage_collection_calls.append(obj.close)
        wl_shm = self.client.binding("wl_shm")
        pool = wl_shm.create_pool(fd, 4*width*height)
        self.garbage_collection_calls.append(pool.destroy)
        buffer = pool.create_buffer(
            0,
            width,
            height,
            4*width,
            wl_shm.format.xrgb8888
        )
        self.garbage_collection_calls.append(buffer.destroy)
        wl_surface.attach(buffer, 0, 0)
        wl_surface.commit()

        if self.fullscreen:
            xdg_toplevel.set_fullscreen(self.client.binding("wl_output"))

        # Track any window resise/maximise/fullscreen etc. events
        self._bind_event_tracker(
            xdg_toplevel.events.configure,
            self._track_xdg_toplevel_configure_events
        )
        # And capture mouse events
        wl_seat = self.client.binding("wl_seat")
        wl_pointer = wl_seat.get_pointer()
        self._bind_event_tracker(
            wl_pointer.events.motion,
            self._track_wl_pointer_motion_events
        )
        self._bind_event_tracker(
            wl_pointer.events.axis,
            self._track_wl_pointer_axis_events
        )
        self._bind_event_tracker(
            wl_pointer.events.button,
            self._track_wl_pointer_button_events
        )
        wl_keyboard = wl_seat.get_keyboard()
        self._bind_event_tracker(
            wl_keyboard.events.key,
            self._track_wl_keyboard_key_events
        )

        # At this point under Sway at least the window isn't visible, so
        # just wait a bit to let that happen. I don't think we get a
        # wayland event unfortunately.
        time.sleep(0.5)

        # Sync to give a chance for any compositor errors to get logged
        # before continuing
        self.sync()

    def events_of_type(self, event_type: str) -> list:
        """
        Returns all captured events of the given type
        """

        return [e for e in self.events if e["type"] == event_type]

    def _bind_event_tracker(self, event_binding_point, handler):
        event_binding_point += handler
        def unset_binding_point():
            nonlocal event_binding_point
            event_binding_point -= handler
        self.garbage_collection_calls.append(
            unset_binding_point
        )

    def _track_wl_pointer_motion_events(self, time, surface_x, surface_y):
        self.events.append({
            "type": "wl_pointer.motion",
            "x": surface_x,
            "y": surface_y
        })

    def _track_wl_pointer_axis_events(self, time, axis, value):
        self.events.append({
            "type": "wl_pointer.axis",
            "axis": {
                0: "vertical_scroll",
                1: "horizontal_scroll",
            }.get(axis, f"unknown:{axis}"),
            "value": value
        })

    def _track_wl_pointer_button_events(self, serial, time, button, state):
        self.events.append({
            "type": "wl_pointer.button",
            "button": {
                272: "left",
                273: "right",
                274: "middle",
            }.get(button, f"unknown:{button}"),
            "state": {
                0: "released",
                1: "pressed"
            }.get(state, f"unknown:{state}")
        })

    def _track_wl_keyboard_key_events(self, serial, time, key, state):
        self.events.append({
            "type": "wl_keyboard.key",
            "key": key,
            "state": {
                0: "released",
                1: "pressed",
                2: "repeated"
            }.get(state, f"unknown:{state}")
        })

    def _track_xdg_toplevel_configure_events(self, width, height, states):
        properties = {
            1: "maximised",
            2: "fullscreen",
            4: "activated",
            9: "suspended"
        }
        result = {
            "type": "xdg_toplevel.configure",
            "width": width,
            "height": height,
            **{
                property: False
                for property in properties.values()
            }
        }

        for value in states:
            if value in properties:
                result[properties[value]] = True
        self.events.append(result)
