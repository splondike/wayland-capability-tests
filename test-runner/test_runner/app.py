import argparse
import os
import logging
import pathlib
import socket
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Optional, Tuple


logging.basicConfig(
    level="INFO",
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


NIX_VM_RESULT_DIR = "nix-vm-script"
NIX_VM_CODE_DIR = "code"
NIX_VM_MONITOR_SOCK = "qemu-monitor.sock"
NIX_VM_RUN_SSH = "run-ssh"
NIX_VM_SETUP_COMPLETE_FILE = "setup-complete"
NIX_VM_MONITOR_PROXY_SOCK = "qemu-monitor-proxy.sock"
NIX_VM_DISK_IMAGE = "nixos.qcow2"


class SuppressedException(RuntimeError):
    """
    Internal exception used for flow control
    """


def startvm(
    nixfile: str,
    verbose: bool = False,
    vm_dir: Optional[str] = None,
    code_dir: Optional[str] = None
):
    """
    Start up a Nix VM based on the given nixfile. It will
    share our capabilities test directory
    """

    if not code_dir:
        project_root = pathlib.Path(__file__).parent.parent.parent
        code_dir = project_root / "capability-tests"
    code_dir = code_dir.absolute()

    vm_dir = pathlib.Path(vm_dir if vm_dir else tempfile.mkdtemp())
    vm_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Using VM dir %s", vm_dir)

    shutdown_vm = None
    try:
        shutdown_vm, ssh_scripts = _setup_vm(
            code_dir,
            nixfile,
            verbose,
            vm_dir
        )
        logger.info("SSH-ing in to VM. Exit SSH session to shut down VM.")
        ssh_scripts["alice"](
            ["-t", "cd /mnt/code && bash"],
            capture_output=False
        )
    except SuppressedException:
        pass
    finally:
        if shutdown_vm:
            shutdown_vm()
        if vm_dir is None:
            # Clean up temp directories if we're doing that, but if the user
            # specified a dir, let them clean up
            shutil.rmtree(vm_dir)


def _setup_vm(
    code_dir: str,
    nixfile: str,
    verbose: bool,
    vm_dir: pathlib.Path
) -> Tuple[Callable[[], None], dict]:
    """
    Do all the work necessary to setup and run our VM. Returns a
    function to shut down the VM and a dictionary of functions that
    can be used to SSH in as various users.
    """

    result_dir = vm_dir / NIX_VM_RESULT_DIR
    setup_complete_file = vm_dir / NIX_VM_SETUP_COMPLETE_FILE
    user_password = "password"

    assert nixfile.endswith(".nix")
    nixfile_basepath = nixfile[:-1*len(".nix")]

    already_setup = setup_complete_file.exists()
    if already_setup:
        logger.info("Already set up VM in this dir, skipping most steps")

    # Set up a SSH key to use with the VM
    ssh_private_key_path, ssh_public_key = _ssh_setup_keypair(vm_dir)

    # Build the given nixfile
    if not already_setup:
        _nix_build(
            ssh_public_key,
            result_dir,
            user_password,
            nixfile,
            verbose=verbose
        )

    logger.info("Starting VM")
    vm_proc, ssh_port, vnc_port, monitor_proxy_stop_event = _start_qemu(
        vm_dir,
        result_dir,
        code_dir,
        verbose=verbose
    )
    logger.info(f"VM accepting VNC connections to port {vnc_port}")
    ssh_scripts = _ssh_create_run_scripts(
        vm_dir,
        ssh_private_key_path,
        ssh_port
    )

    logger.info("Waiting for SSH")
    _wait_for_ssh(ssh_scripts)

    logger.info("Mounting code to VM")
    ssh_scripts["root"](["mkdir", "-p", "/mnt/code"])
    ssh_scripts["root"]([
        "mount",
        "-t",
        "9p",
        "-o",
        "trans=virtio",
        "code",
        "/mnt/code",
        "-oversion=9p2000.L"
    ])

    if not already_setup:
        _ssh_run_script_maybe(
            ssh_scripts["alice"],
            nixfile_basepath + ".sh",
            log_message="pre-login script"
        )

    logger.info("Waiting for login screen")
    login_wait_time = 24
    for counter in range(login_wait_time + 1):
        if _is_on_login_screen(vm_dir):
            break
        elif counter == login_wait_time:
            raise RuntimeError(
                f"Login screen not loaded after {5*login_wait_time} seconds"
            )
        time.sleep(5)

    logger.info("Logging in user")
    _log_in_user(vm_dir, user_password)

    if not already_setup:
        _ssh_run_script_maybe(
            ssh_scripts["alice"],
            nixfile_basepath + "_post.sh",
            log_message="post-login script"
        )
        _ssh_run_script_maybe(
            ssh_scripts["alice"],
            pathlib.Path(nixfile).parent / "common.sh",
            log_message="post-login script"
        )

    if not already_setup:
        with open(setup_complete_file, "w") as fh:
            fh.write("done")

    def shutdown_vm():
        vm_proc.kill()
        monitor_proxy_stop_event.set()

    return shutdown_vm, ssh_scripts


def _nix_build(
    ssh_public_key,
    result_dir,
    user_password,
    nixfile,
    verbose=False
):
    unix_user_id = subprocess.run(
        ["id", "--user"],
        capture_output=True,
        check=True
    ).stdout.decode()

    logger.info("Running nix-build")
    args = [
        "nix-build",
        "-A",
        "default.vm",
        "--argstr",
        "user_password",
        user_password,
        "--argstr",
        "ssh_key",
        ssh_public_key,
        "--argstr",
        "host_user_id",
        unix_user_id,
        "--out-link",
        str(result_dir),
        nixfile,
    ]
    proc = subprocess.run(
        args,
        capture_output=not verbose
    )
    if proc.returncode != 0:
        print(proc.stdout.decode())
        print(proc.stderr.decode())
        raise SuppressedException()


def _start_qemu(
    vm_dir,
    result_dir,
    code_dir,
    verbose=False
) -> Tuple[subprocess.Popen, str, str, threading.Event]:
    """
    Starts up qemu and returns a handle to its unix process and
    an event to use to signal the monitor proxy thread to quit.
    """

    monitor_socket_path = vm_dir / NIX_VM_MONITOR_SOCK
    ssh_port = str(_calculate_free_port())

    nix_disk_image = vm_dir / NIX_VM_DISK_IMAGE
    script_path = result_dir / "bin" / "run-nixos-vm"

    # Socket for the Qemu monitor to listen on
    monitor_socket_path.unlink(missing_ok=True)
    monitor_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    monitor_socket.bind(str(monitor_socket_path))
    monitor_socket.listen(1)

    vnc_arg = None
    vnc_port = None
    for offset in range(100):
        vnc_port = offset + 5900
        if not _is_port_in_use(vnc_port):
            # Qemu adds an offset of 5900 to the supplied port number
            vnc_arg = offset
            break

    assert vnc_arg is not None, "Couldn't find a VNC port to listen on"

    vm_proc = subprocess.Popen(
        [script_path],
        env={
            **os.environ,
            "QEMU_NET_OPTS": f"hostfwd=tcp:127.0.0.1:{ssh_port}-:22",
            "NIX_DISK_IMAGE": nix_disk_image,
            "QEMU_OPTS": " ".join([
                (
                    f"-virtfs local,path={code_dir},"
                    "security_model=mapped-xattr,mount_tag=code"
                ),
                "-device usb-mouse",
                f"-monitor unix:{monitor_socket_path}",
                f"-display vnc=0.0.0.0:{vnc_arg}"
            ])
        },
        # Without stdin=PIPE qemu will interfere with our later
        # SSH call
        stdin=subprocess.PIPE,
        stdout=None if verbose else subprocess.PIPE,
        stderr=None if verbose else subprocess.PIPE,
    )

    # Start the monitor proxy thread
    monitor_connection, _ = monitor_socket.accept()

    # This _wait_for_monitor_prompt() lets us know qemu is
    # booted and so the monitor proxy will work.
    _monitor_wait_for_prompt(monitor_connection)

    monitor_proxy_stop_event = threading.Event()
    monitor_proxy = threading.Thread(
        target=_monitor_start_proxy,
        args=[monitor_proxy_stop_event, monitor_connection, vm_dir]
    )
    monitor_proxy.start()

    return vm_proc, ssh_port, str(vnc_port), monitor_proxy_stop_event


def _wait_for_ssh(ssh_scripts):
    # After this point we might still need to wait a while for the
    # SSH daemon to boot up. This took 5 minutes on a cloud VM I tested.
    # If this never works, then for manual debugging, try checking
    # qemu is actually runing using ps and then try running the run-ssh
    # in the VM dir written to the log whenn running the startvm command.
    attempts = 20
    retry_timeout = 50
    success = False
    for i in range(attempts):
        start = time.time()
        try:
            ssh_scripts["root"](["true"], timeout=retry_timeout)
            success = True
            break
        except subprocess.TimeoutExpired:
            logger.info(
                f"SSH connection attempt timed out ({i+1}/{attempts})"
            )
        except subprocess.CalledProcessError:
            # We can get 'connection reset by peer' as an error, which
            # should also trigger a retry
            logger.info(
                f"SSH connection attempt failed ({i+1}/{attempts})"
            )
        diff = time.time() - start
        if diff < retry_timeout:
            time.sleep(retry_timeout - diff)

    if not success:
        raise RuntimeError(
            "Failed to set up SSH connection, exiting."
        )


def _log_in_user(vm_dir, user_password):
    """
    Waits for the login screen to show up and the logs in the default user
    (we should only have one).
    """

    monitor_proxy_path = vm_dir / NIX_VM_MONITOR_PROXY_SOCK
    # TODO: Wait for the screen background to swap from black,
    # indicating we're on the login screen
    _monitor_sendkey(monitor_proxy_path, "ret")
    time.sleep(0.5)
    for key in user_password:
        _monitor_sendkey(monitor_proxy_path, key)
        time.sleep(0.2)
    _monitor_sendkey(monitor_proxy_path, "ret")


def _ssh_create_run_scripts(vm_dir, ssh_private_key_path, ssh_port):
    rtn = {}
    for user in ("alice", "root"):
        ssh_script = vm_dir / f"run-ssh-{user}"
        with open(ssh_script, "w") as fh:
            fh.write(
                "#!/bin/sh\n"
            )
            fh.write(" ".join([
                # -F to suppress host machine config
                "ssh",
                "-F",
                "/dev/null",
                "-p",
                ssh_port,
                "-oStrictHostKeyChecking=no",
                "-oUserKnownHostsFile=/dev/null",
                "-i",
                str(ssh_private_key_path),
                "-R",
                "2134:" + str(vm_dir / NIX_VM_MONITOR_PROXY_SOCK),
                f"{user}@localhost",
                "$@"
            ]))

        subprocess.run(
            ["chmod", "+x", ssh_script],
            capture_output=True,
            check=True
        )

        def _run_script(
            args,
            timeout=None,
            input=None,
            capture_output=True,
            # Capture the current value of ssh_script
            ssh_script=ssh_script
        ):
            return subprocess.run(
                [ssh_script] + args,
                capture_output=capture_output,
                timeout=timeout,
                input=input
            )

        rtn[user] = _run_script

    return rtn


def _ssh_setup_keypair(vm_dir: pathlib.Path) -> Tuple[pathlib.Path, str]:
    """
    Make a SSH keypair in the give directory. Returns a path to the private
    key and the value of the public key.
    """

    private_key_name = "id_rsa"
    private_key = vm_dir / private_key_name
    if not private_key.exists():
        subprocess.run(
            ["ssh-keygen", "-N", "", "-f", private_key],
            check=True,
            capture_output=True
        )

    with open(vm_dir / (private_key_name + ".pub")) as fh:
        return private_key, fh.read()


def _ssh_run_script_maybe(
    ssh_script,
    script_path,
    log_message
):
    script_path_ = pathlib.Path(script_path)
    if script_path_.exists():
        logger.info(f"Running {log_message} %s", script_path)
        with open(script_path_, mode="rb") as fh:
            ssh_script(["sh"], input=fh.read())


def _calculate_free_port():
    """
    Get an available TCP port.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


def _is_port_in_use(port: int) -> bool:
    """
    Return true if the given TCP port is in use on localhost
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0


def _monitor_start_proxy(
    monitor_proxy_stop_event: threading.Event,
    monitor_connection: socket.socket,
    vm_dir: pathlib.Path
):
    """
    Create a unix socket that proxies to the Qemu monitor unix socket.
    This lets other processes run commands against the monitor easily.
    """

    proxy_path = vm_dir / NIX_VM_MONITOR_PROXY_SOCK
    proxy_path.unlink(missing_ok=True)
    monitor_proxy_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    monitor_proxy_socket.bind(str(proxy_path))
    monitor_proxy_socket.listen(1)
    monitor_proxy_socket.settimeout(5.0)
    while not monitor_proxy_stop_event.is_set():
        try:
            client_sock, _ = monitor_proxy_socket.accept()
            request = client_sock.recv(1024)
            if request:
                monitor_connection.send(request)
                answer = _monitor_wait_for_prompt(monitor_connection)
                client_sock.send(answer)
            client_sock.close()
        except (socket.timeout, BrokenPipeError):
            continue


def _monitor_wait_for_prompt(monitor_connection: socket.socket) -> bytes:
    """
    Wait for the Qemu monitor prompt, signaling it is ready for the next
    command. Returns the output emitted by Qemu excluding that prompt.
    """

    answer = b""
    suffix = b"(qemu) "
    while True:
        undecoded_answer = monitor_connection.recv(1024)
        if not undecoded_answer:
            break
        answer += undecoded_answer
        if answer.endswith(suffix):
            answer = answer[:len(suffix)]
            break
    return answer


def _monitor_send_command(socket_path: socket.socket, command: str):
    monitor_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    monitor_socket.connect(str(socket_path))
    monitor_socket.send((command + "\n").encode())
    monitor_socket.recv(1024)
    monitor_socket.close()


def _monitor_sendkey(socket_path, key):
    _monitor_send_command(socket_path, "sendkey " + key)


def _is_on_login_screen(vm_dir):
    monitor_proxy_path = vm_dir / NIX_VM_MONITOR_PROXY_SOCK
    with tempfile.NamedTemporaryFile() as fh:
        _monitor_send_command(monitor_proxy_path, "screendump " + fh.name)
        fh.seek(0)
        content = fh.read()
    return content.splitlines()[3].startswith(b'""&')


def main():
    parser = argparse.ArgumentParser(
        description="Starts up a VM running a wayland compositor"
    )

    parser.add_argument(
        "nixfile",
        help="Path to the .nix file that describes the VM"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print more log messages"
    )
    parser.add_argument("--vm-dir", help="Where to store generated nix files")
    parser.add_argument("--code-dir", help="Where the test case code lives")

    args = parser.parse_args()

    startvm(
        args.nixfile,
        args.verbose,
        args.vm_dir,
        args.code_dir
    )


if __name__ == "__main__":
    main()
