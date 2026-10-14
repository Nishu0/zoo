from dotenv import load_dotenv

load_dotenv()

from server.server import Server


def main():
    server = Server()
    server.start(import_string="main:app")


if __name__ == "__main__":
    main()
else:
    server = Server()
    app = server.app
