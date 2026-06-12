import os
import json
from typing import TypedDict, Literal
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import FAISS

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

llm = ChatOpenAI(model="gpt-5-mini", api_key=OPENAI_API_KEY)
embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY, model="text-embedding-3-small")


# ───────────────────────────────
# Shared State — passed between agents
# ───────────────────────────────
class AgentState(TypedDict):
    query: str
    strategy: str
    retrieved_chunks: list
    answer: str
    critique: str
    is_grounded: bool
    retry_count: int


# ───────────────────────────────
# Agent 1: Router — decides retrieval strategy
# ───────────────────────────────
def router_agent(state: AgentState) -> AgentState:
    query = state["query"]
    retry_count = state.get("retry_count", 0)

    if retry_count > 0:
        # On retry, escalate to a broader strategy
        strategy = "rerank"
    else:
        prompt = f"""Classify this question into one strategy:
- "naive": simple factual lookup
- "hyde": vague or broad question
- "rerank": complex/compliance/policy question needing precision

Question: {query}

Respond with ONLY one word: naive, hyde, or rerank"""
        response = llm.invoke(prompt)
        strategy = response.content.strip().lower()
        if strategy not in ["naive", "hyde", "rerank"]:
            strategy = "naive"

    return {**state, "strategy": strategy, "retry_count": retry_count}


# ───────────────────────────────
# Agent 2a/b/c: Retrieval Agents
# ───────────────────────────────
def naive_retrieval_agent(state: AgentState, vectorstore) -> AgentState:
    docs = vectorstore.similarity_search(state["query"], k=3)
    return {**state, "retrieved_chunks": [d.page_content for d in docs]}


def hyde_retrieval_agent(state: AgentState, vectorstore) -> AgentState:
    prompt = f"""Write a short hypothetical passage (2-3 sentences) that would answer this question,
as if it came from a company policy document.

Question: {state['query']}

Hypothetical passage:"""
    hypothetical = llm.invoke(prompt).content
    docs = vectorstore.similarity_search(hypothetical, k=3)
    return {**state, "retrieved_chunks": [d.page_content for d in docs]}


def rerank_retrieval_agent(state: AgentState, vectorstore) -> AgentState:
    candidates = vectorstore.similarity_search(state["query"], k=8)
    scored = []
    for d in candidates:
        prompt = f"""Rate relevance 0-10. Respond with ONLY a number.

Question: {state['query']}
Passage: {d.page_content}
Score:"""
        try:
            score = float(llm.invoke(prompt).content.strip())
        except ValueError:
            score = 0
        scored.append((score, d.page_content))
    scored.sort(key=lambda x: x[0], reverse=True)
    return {**state, "retrieved_chunks": [c for _, c in scored[:3]]}


# ───────────────────────────────
# Agent 3: Answer Agent
# ───────────────────────────────
def answer_agent(state: AgentState) -> AgentState:
    context_text = "\n\n".join(state["retrieved_chunks"])
    prompt = f"""Answer the question using only the context below.

Context:
{context_text}

Question: {state['query']}
Answer:"""
    answer = llm.invoke(prompt).content
    return {**state, "answer": answer}


# ───────────────────────────────
# Agent 4: Critic Agent (A2A feedback loop)
# ───────────────────────────────
def critic_agent(state: AgentState) -> AgentState:
    prompt = f"""You are a critic reviewing an AI's answer for groundedness.

Question: {state['query']}
Context provided: {chr(10).join(state['retrieved_chunks'])}
Answer given: {state['answer']}

Is this answer well-grounded in the context, and does it actually address the question?
Respond with ONLY "SUFFICIENT" or "INSUFFICIENT"."""

    critique = llm.invoke(prompt).content.strip().upper()
    is_grounded = "SUFFICIENT" in critique

    return {**state, "critique": critique, "is_grounded": is_grounded}


# ───────────────────────────────
# Routing logic — conditional edges
# ───────────────────────────────
def route_after_router(state: AgentState) -> Literal["naive", "hyde", "rerank"]:
    return state["strategy"]


def route_after_critic(state: AgentState) -> Literal["retry", "end"]:
    if state["is_grounded"]:
        return "end"
    if state.get("retry_count", 0) >= 1:
        # Already retried once, give up to avoid infinite loop
        return "end"
    return "retry"


def increment_retry(state: AgentState) -> AgentState:
    return {**state, "retry_count": state.get("retry_count", 0) + 1}


# ───────────────────────────────
# Build the Graph
# ───────────────────────────────
def build_graph(vectorstore):
    workflow = StateGraph(AgentState)

    workflow.add_node("router", router_agent)
    workflow.add_node("naive", lambda s: naive_retrieval_agent(s, vectorstore))
    workflow.add_node("hyde", lambda s: hyde_retrieval_agent(s, vectorstore))
    workflow.add_node("rerank", lambda s: rerank_retrieval_agent(s, vectorstore))
    workflow.add_node("answer", answer_agent)
    workflow.add_node("critic", critic_agent)
    workflow.add_node("increment_retry", increment_retry)

    workflow.set_entry_point("router")

    # Router → one of 3 retrieval strategies
    workflow.add_conditional_edges("router", route_after_router, {
        "naive": "naive",
        "hyde": "hyde",
        "rerank": "rerank"
    })

    # All retrieval strategies → answer
    workflow.add_edge("naive", "answer")
    workflow.add_edge("hyde", "answer")
    workflow.add_edge("rerank", "answer")

    # Answer → critic
    workflow.add_edge("answer", "critic")

    # Critic → end OR retry (A2A feedback loop)
    workflow.add_conditional_edges("critic", route_after_critic, {
        "end": END,
        "retry": "increment_retry"
    })

    # Retry loops back to router (with escalated strategy)
    workflow.add_edge("increment_retry", "router")

    return workflow.compile()