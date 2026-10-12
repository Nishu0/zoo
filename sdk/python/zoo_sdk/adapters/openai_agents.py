"""Zoo for the OpenAI Agents SDK: function tools for the shell and files, and a computer for ComputerTool.

from agents import Agent, ComputerTool, Runner
from zoo_sdk.adapters.openai_agents import ZooComputer, zoo_tools

agent = Agent(
    name="operator",
    model="computer-use-preview",
    tools=[*zoo_tools(box), ComputerTool(ZooComputer(box))],
    model_settings=ModelSettings(truncation="auto"),
)
await Runner.run(agent, "...")
"""
# pyright: reportMissingImports=false

import asyncio
from typing import Any, Literal

from agents import AsyncComputer, FunctionTool, RunContextWrapper

from zoo_sdk.adapters import TOOLS, AnySandbox, ToolSpec, call, run, text

# the Linux desktop's screen; pass dimensions= for other sizes
SCREEN = (1280, 720)
KEYS = {"enter": "Return", "return": "Return", "esc": "Escape", "space": "space", "backspace": "BackSpace"}


def zoo_tools(box: AnySandbox, names: list[str] | None = None) -> list[FunctionTool]:
    """The sandbox's tools as FunctionTools (all of them, or those `names`)."""

    def make(spec: ToolSpec) -> FunctionTool:
        async def invoke(_: RunContextWrapper[Any], arguments: str) -> str:
            import json

            try:
                return text(await run(box, spec, json.loads(arguments or "{}")))
            except Exception as e:
                return f"error: {e}"

        return FunctionTool(
            name=spec.name,
            description=spec.description,
            params_json_schema=spec.parameters,
            on_invoke_tool=invoke,
            strict_json_schema=False,
        )

    return [make(spec) for spec in TOOLS if names is None or spec.name in names]


class ZooComputer(AsyncComputer):
    """A Zoo desktop for ComputerTool."""

    def __init__(self, box: AnySandbox, dimensions: tuple[int, int] = SCREEN):
        self.box = box
        self._dimensions = dimensions

    @property
    def environment(self) -> Literal["linux"]:
        return "linux"

    @property
    def dimensions(self) -> tuple[int, int]:
        return self._dimensions

    async def screenshot(self) -> str:
        return await call(self.box, "screenshot", {})

    async def click(self, x: int, y: int, button: str = "left") -> None:
        button = {"wheel": "middle", "back": "left", "forward": "left"}.get(button, button)
        await call(self.box, "click", {"x": x, "y": y, "button": button})

    async def double_click(self, x: int, y: int) -> None:
        await call(self.box, "double_click", {"x": x, "y": y})

    async def scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
        if scroll_y:
            direction, amount = ("down" if scroll_y > 0 else "up"), abs(scroll_y) // 100 or 1
        else:
            direction, amount = ("right" if scroll_x > 0 else "left"), abs(scroll_x) // 100 or 1
        await call(self.box, "scroll", {"direction": direction, "amount": amount, "x": x, "y": y})

    async def type(self, text: str) -> None:
        await call(self.box, "type_text", {"text": text})

    async def wait(self) -> None:
        await asyncio.sleep(1)

    async def move(self, x: int, y: int) -> None:
        await call(self.box, "move_mouse", {"x": x, "y": y})

    async def keypress(self, keys: list[str]) -> None:
        mapped = [KEYS.get(k.lower(), k.lower() if len(k) > 1 else k) for k in keys]
        if len(mapped) == 1:
            await call(self.box, "press_key", {"key": mapped[0]})
        else:
            await call(self.box, "hotkey", {"keys": mapped})

    async def drag(self, path: list[tuple[int, int]]) -> None:
        if len(path) >= 2:
            (sx, sy), (ex, ey) = path[0], path[-1]
            await call(self.box, "drag", {"start_x": sx, "start_y": sy, "end_x": ex, "end_y": ey})
