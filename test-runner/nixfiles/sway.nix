{ ssh_key, host_user_id, user_password }:
let
  result = import ./common.nix {
    ssh_key = ssh_key;
    user_password = user_password;
    user_id = builtins.fromJSON host_user_id;
    overrides = {
      programs.sway.enable = true;
      services.displayManager.defaultSession = "sway";
      # These enable the ScreenCast portal, but not the remote desktop
      # one. See https://github.com/emersion/xdg-desktop-portal-wlr
      # xdg.portal = {
      #   enable = true;
      #   wlr.enable = true;
      # };
    };
  };
in result
