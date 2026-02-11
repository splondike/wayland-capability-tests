Implementations of functions (e.g. move mouse cursor) under various Wayland compositors (GNOME, KDE, Sway etc.). Contains tests that confirm the functions work.

# Usage

The tests need to run across multiple different desktop environments/wayland compositors like GNOME, KDE etc. To make this easier a virtual machine testing system has been set up that lives in the `test-runner/` sub directory. Use that to run the Sway tests as follows (currently only working on Linux):

1. Install Nix: https://nixos.org/download/
2. Change to the `test-runner` sub directory.
3. Start up a sway virtual machine: `nix-shell --run "python test_runner/app.py nixfiles/sway.nix"` . This will take several minutes the first time while downloading and compiling things. When finished booting it will display a command prompt `[alice@nixos...`.
4. You're now logged in to the VM. On first run you need to download the definitions of the Wayland protocols. Run `ct wayland-fetch-protocols` in the VM (`ct` is an alias for `poetry run capability-tests`). These will be saved on your host machine, so you don't need to run this each time you boot the VM.
5. Inside the VM run the Sway related tests using: `ct tests-run --compositor=sway`. The `capability-tests/capability_tests.toml` file defines the available tests; that compositor argument matched the 'tests' entries with 'sway' in their list of compositors.

## VNC for viewing VM desktop

The VMs automatically start up a VNC server which you can use to view or interract with the desktop. A simple VNC client has been installed via Nix. You can connect to it using the following commandline, substitute a different port number if the log messages from your VM indicate to do that.

```
cd test-runner
# Or remove the ViewOnly argument to be readwrite, or press f8->options->input while running to adjust this setting
nix-shell --run "vncviewer ViewOnly=true localhost:5900"
```

## Debugging

The Wayland client library we're using accepts the `WAYLAND_DEBUG=1` env var to print more output.

If that's not enough you can also make use of various [helper applications](https://wayland.freedesktop.org/extras.html) that print out all events received and requests sent.

I've been using [wlanalyzer](https://github.com/blessed/wlanalyzer), which was a little annoying to build, but supports Python unlike `wayland-tracer`. I haven't figured out how Nix works enough to install this in the VM yet.

# Why build this?

The move from X to a Wayland ecosystem has so far resulted in a reduction of functionality needed by accessibility technologies. E.g. software that wants to move the mouse around may now need to use one protocol on Wayland compositor A and a different one on compositor B. Some required functionality that was available under X may not be available at all now.

It is difficult to know what functionality is missing or broken just by reading documentation, you have to test it. Also if we want to work to add in and standardise features it's helpful to be able to easily start up a particular compositor to test a code update. This repository aims to facilitate both those things: figure out what's missing, and make it easier to run those tests against particular compositor versions.

# Contributing

Helping to fill out this system and writing more tests would be appreciated. At the moment the project is still at the stage where core framework features are being adjusted. As such please get in contact with me via a Github issue prior to creating a pull request. That way we can minimise rework. I'm going to try using Github issues to track my own work too so people can see hwo things are coming along.

Use of AI to write code on this project is OK. Just keep in mind the sise of your pull requests as I'll still have to read and understand them before merging.

# Architecture

There are two parts to this project, the test runner VM (in the `test-runner` folder) and the tests themselves (in `capability-tests`). The VM is a pretty integral part of the design; you can run the tests directly on your host, but some of the tests rely on injecting keypresses or mouse actions.

## The tests themselves

We have a large number of Wayland compositors which we could test, and [3 or 4 different protocols](docs/protocols_overview.md) that we can use to implement functionality. Different compositors will support different ways of doing things. We keep track of which tests work on which compositors in the `capability-tests/capability_tests.toml` file.

Tests are just python functions that perform logic and test assertions, very similar to `pytest`. The reason I didn't just use `pytest` is I wanted to use that centralised `.toml` file for registering and adding metadata about tests. The tests in this system have a dependency injection/fixtures feature; certain special named arguments will be populated with specific object instances. For example in `def mouse_move_absolute(wayland_client: WaylandClient)` the wayland_client variable gets populated by the testing framework.

The testing framework provides a number of fixtures including: wayland_client, dbus_client, window_factory, and runner_commands. Runner commands provides access to mouse movement and keypress injection functionality from the test runner VM layer. The ability to inject keypresses/mouse movements without relying on the protocols we're testing is useful to ensure the compositor is in a good state (e.g. to dismiss popups), and to enable testing of some features (e.g. a global shortcut hook). window_factory pops up an OS window that will capture events like the mouse moving across it. You can then perform assertions on those events.

For the wayland_client, the library we're using depends on having a file defining the available protocols. This includes the RPC method names and their arguments for example. There's a command `wayland-fetch-protocols` that downloads the XML files defining the protocols, and formats them into the JSON document needed by our library.

## The test runner VM

The main reason to use a VM for running the tests is it lets you easily swap between compositors (or run multiple simultaneously) without having to change the desktop environment you're familiar with. The second reason is it lets us inject keypress and mousemove events for some tests. The last reason is it gives a consistent test environment for everyone working on the project.

I chose Nix as the base OS, which has several advantages. Firstly, they had already done a lot of the work for setting up a nice VM environment with minimal fuss for the user. Though see [this decision](docs/decision_register/2507191331-dd-no-nix-vm-test-usage.md) for why I didn't use their existing VM test system. A second reason is they let you define a lot of system configuration via a single .nix file; which in the future would work well for reproducibility.

We set up the VM by first using Nix's VM scripts to build a shell script. That script will boot up a VM using a Nix managed Qemu binary. The VMs filesystem is the base NixOS image with all the system packages we need mounted in from the host computer's `/nix` directory. This is why things boot up quickly, we don't have to redownload/recompile every time we start a VM.

The test runner scripts receive a path to a .nix file. They use that to set up the relevant compositor on the VM and also run any shell scripts named after the .nix file to do other configuration. After all that we SSH in to the VM and leave the user in a bash shell.

The qemu 'monitor' system is made available within the VM over TCP. This allows injection of keypress/mousemove events by tests via some helper functions.
