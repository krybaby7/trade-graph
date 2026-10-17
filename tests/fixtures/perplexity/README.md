# Perplexity fixtures (synthetic)

Every file here is **synthetic**: written by hand in the response shapes that
Perplexity documents for its Agent API (`POST /v1/agent`, OpenAI
Responses-style output items) and its Search API (`POST /search`). None was
recorded from a real Perplexity call, and no key was used. They prove how the
adapter reads documented shapes; they do not prove what Perplexity returns
today. The first real responses are recorded at local step L2 (D125 test key).

| File | Shape |
|---|---|
| `agent_tool_call.json` | Agent API turn asking the software-run `web_search` tool |
| `agent_final.json` | Agent API turn with structured JSON output and reported cost |
| `agent_refusal.json` | Agent API turn that declines |
| `agent_truncated.json` | Agent API turn cut off at `max_output_tokens` |
| `agent_missing_usage.json` | Agent API turn with output but no `usage` |
| `search_results.json` | Search API response with three results |
| `search_malformed.json` | Search API response whose `results` is not a list |
| `search_injection.json` | Search API response with one clean result and one written to steer the reader (T37 injection fixture) |
