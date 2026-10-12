"""A LangGraph ReAct agent with a Zoo code sandbox. pip install 'zoo-sdk[langgraph]' langgraph langchain-anthropic"""

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from zoo_sdk import Zoo
from zoo_sdk.adapters.langgraph import zoo_tools

zoo = Zoo()
with zoo.sandbox(kind="code") as box:
    agent = create_react_agent(ChatAnthropic(model="claude-sonnet-5-5"), zoo_tools(box, ["exec", "read_file", "write_file"]))
    result = agent.invoke({"messages": [("user", "Write fizzbuzz.py in the home folder, run it, show the output.")]})
    print(result["messages"][-1].content)
