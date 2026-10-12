"""Writes the API's OpenAPI schema (the /v1 paths) to a file, without a database: `uv run python scripts/openapi.py
sdk/typescript/openapi.json`. The TypeScript SDK's types are generated from it."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET", "openapi")
os.environ.setdefault("ZOO_SECRETS_KEY", "openapi")
os.environ.setdefault("DATABASE_URL", "postgresql://openapi:openapi@127.0.0.1:1/openapi")
os.environ.setdefault("ZOO_OBJECT_STORE", "s3://openapi")

from main import app  # noqa: E402

with open(sys.argv[1], "w") as f:
    json.dump(app.openapi(), f, indent=1, sort_keys=True)
    f.write("\n")
