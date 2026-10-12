# zoo-sdk

Python SDK and CLI for [Zoo](https://github.com/chann44/zoo): self-hosted Linux, macOS and Windows sandboxes for AI
agents.

```sh
pip install zoo-sdk            # the SDK
pip install 'zoo-sdk[cli]'     # plus the `zoo` command
```

```python
from zoo_sdk import Zoo

zoo = Zoo()  # reads ZOO_API_KEY and ZOO_URL
with zoo.sandbox(kind="desktop") as box:  # deleted on exit, also on error
    for chunk in box.exec("pip install requests && python -V", stream=True):
        print(chunk.data, end="")
    png = box.screenshot()
```

Async:

```python
from zoo_sdk import AsyncZoo

async with AsyncZoo() as zoo:
    async with await zoo.sandbox(kind="code") as box:
        result = await box.exec("uname -a")
```

Every call has a timeout (30 s unless the call passes `timeout=`) and is retried with exponential backoff when that's
safe: connection failures and 429 always; 5xx and timeouts for reads and for creates, which the SDK sends with an
`Idempotency-Key`. Errors are `ZooError` subclasses (`NotFoundError`, `ConflictError`, `RateLimitError`, …) with the
API's `code`, `message`, `hint` and `docs_url`.
