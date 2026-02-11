#!/bin/sh
# Script run for every Wayland compositor after logging in

# The wayland socket can be different on each boot, so need to dynamically
# determine what it should be. Save the value to bashrc so SSH sessions
# can use it. May take a little while for the socket to appear, so try
# a few times
for _ in $(seq 1 5);do
    if [ z"$(find $XDG_RUNTIME_DIR -maxdepth 1 -type s -name "wayland-*" | head -n 1)" != z"" ];then
        wl_display=$(basename $(find $XDG_RUNTIME_DIR -maxdepth 1 -type s -name "wayland-*" | head -n 1))
        echo "export WAYLAND_DISPLAY=$wl_display" >> ~/.bashrc
        break
    fi
    sleep 3
done

poetry install -C /mnt/code

# Stick in an aliase to make using the shell nicer
echo "alias ct='poetry run capability-tests'" >> ~/.bashrc
