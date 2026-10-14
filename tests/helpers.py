"""Execute Python fixtures without inheriting the test module's future flags."""


def execute(source: str) -> dict[str, object]:
    """Execute source in a fresh namespace and return its bindings."""
    namespace: dict[str, object] = {}
    exec(compile(source, "<fixture>", "exec", dont_inherit=True), namespace)
    return namespace


def run(source: str) -> object:
    """Execute a fixture and return its result."""
    return execute(source)["result"]
