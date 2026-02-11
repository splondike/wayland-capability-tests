#!/bin/sh

# Don't show the tour popup
dconf write /org/gnome/shell/welcome-dialog-last-shown-version '"99.9"'

# Turn off the screensaver/lock screen timeout
gsettings set org.gnome.desktop.screensaver lock-enabled false
gsettings set org.gnome.desktop.session idle-delay 0

# # gnome-keyring-daemon also shows a popup, so get rid of it. It doesn't
# # start immediately though, so try a few times
# for _ in $(seq 1 10);do
#     # No killall on this machine
#     pid=$(pgrep gnome-keyring)
#     if [ $? = 0 ];then
#         kill $pid
#         break
#     else
#         sleep 3
#     fi
# done
