import os

# The node endpoint (server/nodes.py) runs gRPC threads while the API spawns subprocesses (ssh for Docker over
# SSH, docker CLI calls); those children never use gRPC, so its fork handlers only add noise to the logs.
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "false")
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
