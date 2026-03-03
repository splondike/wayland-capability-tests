#!/bin/sh

# Turn off screen locking and blanking
mkdir -p ~/.config/
cat << EOT > ~/.config/kscreenlockerrc
[Daemon]
Autolock=false
Timeout=0
EOT

cat << EOT > ~/.config/powerdevilrc
[AC][Display]
DimDisplayIdleTimeoutSec=-1
DimDisplayWhenIdle=false
TurnOffDisplayIdleTimeoutSec=-1
TurnOffDisplayWhenIdle=false
EOT
