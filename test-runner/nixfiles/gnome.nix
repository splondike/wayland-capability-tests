{ ssh_key, host_user_id, user_password }:
let
  result = import ./common.nix {
    ssh_key = ssh_key;
    user_password = user_password;
    user_id = builtins.fromJSON host_user_id;
    overrides = {
      services.xserver.desktopManager.gnome.enable = true;
      services.xserver.desktopManager.gnome.debug = true;
      # Pick the wayland session version not gnome-xorg
      services.displayManager.defaultSession = "gnome";
    };
  };
in result
