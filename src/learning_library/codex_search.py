"""Discovery Adapter using the same subscription executor, with live search enabled."""

import json

from pydantic import BaseModel, ConfigDict, Field

from .model_config import codex_settings
from .search_types import SearchHit
from .storage import now
from .structured_model import call_json

PROMPT_VERSION = "practice-source-search-v1"


class FoundSources(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[SearchHit] = Field(max_length=5)
    limitations: str = Field(min_length=1, max_length=2000)


def search(query, objective, *, root, trace_name, as_of=None, settings=None):
    # Discovery requires a browsing harness; analysis-provider selection is separate.
    model, effort = settings or codex_settings()
    request = {"provider": "codex", "model": model, "effort": effort, "web_search": "live",
               "messages": [
                   {"role": "system", "content":
                    "Search first-hand developer AI practices using live web search. Inspect originals. "
                    "Exclude product announcements, generic tips and reposts. Respect requested dates; "
                    "do not infer publication from URL or crawl date. Return at most five original URLs; "
                    "unknown author/date stays null. snippet explains concrete practices and unverified "
                    "limits in concise Chinese. Do not invent quotations. A returned candidate is not "
                    "an approved article. At most four searches is a soft instruction, not a hard budget. "
                    "No shell, local files, agents or MCP. Return only the supplied JSON structure."},
                   {"role": "user", "content": json.dumps({"query": query, "objective": objective,
                                                              "as_of": as_of or now()[:10]}, ensure_ascii=False)},
               ]}
    result = call_json(request, FoundSources, root=root, trace_name=trace_name)
    raw = result["response"]
    # Full events remain in model-runs; the search observation stays bounded.
    trace = {k: v for k, v in raw.items() if k != "events"}
    output = result["output"] or {"results": [], "limitations": result["error"]}
    return {"provider": "codex_sdk_chatgpt", "observed_at": now(),
            "status": "failed" if result["error"] else "succeeded",
            "results": output["results"], "limitations": output["limitations"],
            "provider_output": json.dumps({"response": trace}, ensure_ascii=False),
            "reported_cost_usd": None}
