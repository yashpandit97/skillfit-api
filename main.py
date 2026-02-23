"""
Interactive chatbot using LangGraph and Ollama (ChatGPT-style).
"""

from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage


def create_graph():
    llm = ChatOllama(model="llama3.2", temperature=1)

    def chatbot_node(state: dict):
        messages = state["messages"]
        response = llm.invoke(messages)
        return {"messages": messages + [response]}

    graph = StateGraph(dict)
    graph.add_node("chatbot", chatbot_node)
    graph.set_entry_point("chatbot")
    graph.add_edge("chatbot", END)
    return graph.compile()


if __name__ == "__main__":
    app = create_graph()
    messages = []

    print("Chat with the assistant. Type 'quit' or 'exit' to stop.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break

        messages.append(HumanMessage(content=user_input))
        result = app.invoke({"messages": messages})
        messages = result["messages"]
        reply = messages[-1].content

        print(f"Assistant: {reply}\n")
