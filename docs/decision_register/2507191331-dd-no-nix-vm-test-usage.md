I've decided not to use the [runNixOSTest test system](https://nixos.wiki/wiki/NixOS_Testing_library) for this project.

That system looks good for running simple smoke tests in fresh VMs. The issue is every time you run a test you basically have to rebuild the VM. This works fine for simpler scenarios, but booting something like GNOME takes around 1 minute on my machine. Add to that the issue that I haven't yet worked out how to include Poetry packages as part of the VM so those need to be installed as well. This all adds up to quite a slow cycle time for then change code -> rerun test loop which I want to avoid for development.

The second issue is some of my tests will require coordination between code running within the VM and code outside it generating simulated mouse moves or key presses. With runNixOSTest you define tests as a string included in your `.nix` file. This doesn't lend itself well to including common code via imports, and the code needed to coordinate in and outside VM code would be substantial. That code may have to be copy pasted into all the strings somehow, which is kind of messy.

The things I think I'm giving up by not using runNixOSTest are:

- Ease of understanding by Nix users, and documentation around how to run it.
- Potentially a harder time [running tests in Github CI](https://nixcademy.com/posts/nixos-integration-test-on-github/) if that became useful at some point.
- Duplication of functionality from their `Machine()` Python class that allows key press event simulation.
