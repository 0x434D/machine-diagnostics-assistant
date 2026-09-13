from asyncua import Server


def new_server() -> Server:
    """Binds an OS-assigned ephemeral loopback port instead of asyncua's 4840
    default. Once the plant container exists (Task 6), anything with that stack up
    already owns 4840, and every asyncua-backed test would fail on
    OSError: [Errno 98] Address already in use for a reason unrelated to whatever
    changed."""
    server = Server()
    server.set_endpoint("opc.tcp://127.0.0.1:0/plant")
    return server
