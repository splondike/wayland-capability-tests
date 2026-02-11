#!/bin/sh

# # hyprland-update-screen shows a popup, so get rid of it. It may take
# # a few seconds for it to show up though, so try a few times.
# for _ in $(seq 1 10);do
#     # No killall on this machine
#     pid=$(pgrep hyprland-update-screen)
#     if [ $? = 0 ];then
#         kill $pid
#         break
#     else
#         sleep 3
#     fi
# done
