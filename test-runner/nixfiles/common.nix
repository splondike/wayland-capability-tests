{ ssh_key, user_password, user_id, overrides }:
let
  nixosVersion = "25.05";
  # use nix-build --tarball-ttl <lots of seconds> to use the cache for longer.
  nixpkgs = fetchTarball "https://github.com/NixOS/nixpkgs/tarball/nixos-${nixosVersion}";
  pkgs = import nixpkgs {
    config = {};
    overlays = [];
  };
  baseVmOptions = {
    system.stateVersion = nixosVersion;
    users.users.root.password = user_password;
    users.users.root.openssh.authorizedKeys.keys = [
      ssh_key
    ];
    users.users.alice = {
      isNormalUser = true;
      password = user_password;
      uid = user_id;
      # I need to use a SSH key, password auth doesn't work for some reason
      openssh.authorizedKeys.keys = [
        ssh_key
      ];
    };
    environment.systemPackages = with pkgs; [
      python313
      poetry
      git
    ];

    services.xserver.enable = true;

    services.xserver.displayManager.gdm = {
      enable = true;
      debug = true;
    };

    services.openssh = {
      enable = true;
      settings = {
        PermitRootLogin = "yes";
      };
    };
  };
  combinedVmOptions = pkgs.lib.recursiveUpdate baseVmOptions overrides;
  return = rec {
    # Use this target like nix-build -A default.vm to create a vm runner script
    default = pkgs.nixos vmOptions;
    vmOptions = combinedVmOptions;
  };
in return
