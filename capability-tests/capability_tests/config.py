import pathlib
import tomllib


class TestConfig:
    def __init__(self, data):
        self.data = data

    def list_tests(self):
        yield from self.data["tests"]

    @classmethod
    def build_from_default_filepath(cls) -> "TestConfig":
        file = pathlib.Path(__file__).parent.parent.resolve() / "capability_tests.toml"
        with open(file, "rb") as fh:
            return TestConfig(tomllib.load(fh))
