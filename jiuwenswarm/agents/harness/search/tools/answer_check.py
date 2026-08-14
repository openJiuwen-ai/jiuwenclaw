import os

from jiuwenswarm.agents.harness.search.tools.tool_registry import tool

@tool(name="check_confidence_gate",
      description="A strict confidence-based final-answer gate. Before producing any final answer,"
                  " the agent must call this tool with its current confidence score. "
                  "The tool checks whether the confidence is higher than the required threshold."
                  " If the confidence passes the threshold, the agent is allowed to answer. "
                  "If the confidence does not pass the threshold, the agent is not allowed to output a final answer, exact answer, best guess, "
                  "ikely answer, confidence statement, or “unknown/insufficient evidence” answer, and must continue searching for stronger evidence. "
                  "he tool result is mandatory and cannot be ignored.")
def check_answer(confidence:int):
    confidence_threshold = int(os.getenv("SEARCH_CONFIDENCE_THRESHOLD", "90"))
    if confidence>=confidence_threshold:
        return "The evidence is sufficient. You may provide the final answer."
    else:
        return "The evidence is insufficient. You are not allowed to provide an answer and must continue searching for relevant evidence."