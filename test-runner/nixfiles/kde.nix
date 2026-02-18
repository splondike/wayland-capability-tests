{ ssh_key, host_user_id, user_password }:
let
  result = import ./common.nix {
    ssh_key = ssh_key;
    user_password = user_password;
    user_id = builtins.fromJSON host_user_id;
    overrides = {
      services.desktopManager.plasma6.enable = true;
      services.displayManager.defaultSession = "plasma";
      # The default 1GB RAM isn't enough
      virtualisation.vmVariant = {
        virtualisation.memorySize = 2048;
      };
    };
  };
in result
