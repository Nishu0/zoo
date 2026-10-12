import sys

from dotenv import load_dotenv

load_dotenv()

from server import requirements
from server.server import Server

server = Server()
app = server.app


def main():
    failed = requirements.failures()
    if failed:
        print("\n".join(failed), file=sys.stderr)
        sys.exit(1)
    server.start(import_string="main:app")


if __name__ == "__main__":
    main()
