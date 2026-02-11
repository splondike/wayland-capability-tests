{ ssh_key, host_user_id, user_password }:
let
  result = import ./common.nix {
    ssh_key = ssh_key;
    user_password = user_password;
    user_id = builtins.fromJSON host_user_id;
    overrides = {
      programs.hyprland.enable = true;
      services.displayManager.defaultSession = "hyprland";
    };
  };
in result
