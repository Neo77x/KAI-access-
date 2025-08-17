# planner.py
import json
from llm_adapter import call_llm
def propose_candidates(repo_summary, n=3):
    prompt = f"""
You are an assistant that proposes small, SAFE, reversible code edits to improve code quality.
Repo summary:
{repo_summary}

Produce up to {n} candidates as a JSON array. Each candidate should be an object:
{{"file": "<relative path>", "new_content": "<full file content or patch>", "reason":"<why this helps>"}}
If you cannot propose safe edits, return [].
"""
    resp = call_llm(prompt, max_tokens=1024, temperature=0.3)
    try:
        candidates = json.loads(resp)
        return candidates
    except Exception:
        # best-effort parsing: return empty if LLM not cooperative
        return []
