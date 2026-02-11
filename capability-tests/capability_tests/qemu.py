import socket


class TestRunnerCommands:
    """
    Interface to executing actions like pressing keys in the test runner
    context
    """

    def __init__(self, socket_factory):
        self.socket_factory = socket_factory

    @classmethod
    def build_tcp(cls, port, host):
        """
        Build an instance that connects to a TCP port
        """

        def factory():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((host, port))
            return s

        return cls(factory)

    def send_key(self, key: str):
        self._monitor_send_command("sendkey " + key)

    def _monitor_send_command(self, command: str):
        monitor_socket = self.socket_factory()
        monitor_socket.send((command + "\n").encode())
        monitor_socket.recv(1024)
        monitor_socket.close()
