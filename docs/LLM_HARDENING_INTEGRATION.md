# LLM Hardening Integration Guide

## What Was Built

`agents/llm_hardening.py` — a production-grade resilience layer that wraps the existing `LLMRouter` with:

| Feature | What It Does | Config |
|---------|-------------|--------|
| **Health Tracking** | Tracks success/failure rate + latency per provider | In-memory, resets on restart |
| **Circuit Breaker** | Opens after 3 consecutive failures, cools down for 5 min | `failure_threshold=3`, `cooldown_seconds=300` |
| **Automatic Fallback** | Tries primary → secondary → tertiary provider | `fallback_chain=[(provider, model), ...]` |
| **Retry + Backoff** | Exponential backoff (1s, 2s, 4s) between retries | `max_retries=2`, `base_delay=1.0` |
| **Timeout Enforcement** | Every call capped at 180s (configurable) | `call_timeout=180.0` |
| **Cost-Aware Auto-Select** | Picks best healthy provider for quality tier | `quality_tier="high\|medium\|low\|json"` |
| **Graceful Errors** | `HardenedLLMError` with full context (health snapshot, attempt log) | Always included |

## Files Created

| File | Purpose |
|------|---------|
| `agents/llm_hardening.py` | The hardening layer (~450 lines) |
| `tests/test_llm_hardening.py` | 18 regression tests |

## Integration Pattern

### Option 1: Per-Agent Opt-In (Recommended — Gradual Rollout)

For each agent that needs resilience, replace `router.ask()` with `hardened.ask_with_fallback()`:

```python
# agents/g2_nodes.py — writer node (before)
from agents.llm_router import get_router
router = get_router()
result = await router.ask(
    provider="anthropic", model="claude-opus-4-5",
    system=WRITER_SYSTEM, messages=msgs,
    agent_name="g2.writer", cache_system=True,
)

# After — with fallback + retry + circuit breaker
from agents.llm_hardening import get_hardened_router
hardened = get_hardened_router()
result = await hardened.ask_with_fallback(
    primary_provider="anthropic", primary_model="claude-opus-4-5",
    fallback_chain=[("openai", "gpt-4.1"), ("google", "gemini-2.5-pro")],
    system=WRITER_SYSTEM, messages=msgs,
    agent_name="g2.writer", cache_system=True,
    max_retries=2, call_timeout=180.0,
)
```

### Option 2: BaseAgent Global Swap (All Agents at Once)

Modify `agents/base_agent.py` to use the hardened router transparently:

```python
# agents/base_agent.py — modify ask() method
async def ask(self, system, messages, **kwargs):
    from agents.llm_hardening import get_hardened_router
    hardened = get_hardened_router()
    
    # Determine fallback chain based on primary provider
    primary = self._provider or infer_provider(self._model)
    chain = self._fallback_chain_for(primary)
    
    return await hardened.ask_with_fallback(
        primary_provider=primary, primary_model=self._model,
        fallback_chain=chain,
        system=system, messages=messages,
        agent_name=self.name,
        **kwargs,
    )
```

### Option 3: Auto-Select (Let the System Decide)

```python
# For non-critical nodes where any quality tier is acceptable
result = await hardened.ask_auto(
    quality_tier="medium",  # or "high", "low", "json"
    system=system, messages=messages,
    agent_name="g6.cadence",
)
```

## Fallback Chains by Graph

| Graph | Primary | Fallback Chain | Why |
|-------|---------|----------------|-----|
| **G2** Writer | Anthropic Opus 4.5 | OpenAI GPT-4.1 → Google Gemini 2.5 Pro | Resume quality is critical |
| **G2** ATS Critic | DeepSeek R1 | Moonshot Kimi K2.5 | JSON-mode reliability |
| **G3** Interview | Anthropic Opus 4.5 | OpenAI GPT-4.1 | Interview prep quality |
| **G4** LinkedIn | Anthropic Sonnet 4.6 | OpenAI GPT-4.1 | Speed over quality |
| **G6** Follow-up | Anthropic Sonnet 4.6 | OpenAI GPT-4.1 | Daily cron, needs reliability |
| **G7** Application | Anthropic Opus 4.5 | OpenAI GPT-4.1 | Form answers must be correct |
| **G8** Offer Eval | Anthropic Opus 4.5 | OpenAI GPT-4.1 → Google Gemini 2.5 Pro | $50K-200K decision |
| **G9** Story Extract | Anthropic Opus 4.5 | OpenAI GPT-4.1 | One-time extraction |
| **G11** Voice Calib | Anthropic Opus 4.5 | OpenAI GPT-4.1 | One-time calibration |
| **Boss Agent** | Anthropic Opus 4.5 | OpenAI GPT-4.1 | Daily digest |

## Monitoring

```python
# GET /admin/provider-health (add to api/server.py)
@app.get("/admin/provider-health")
async def admin_provider_health(_auth=Depends(verify_secret)):
    hardened = get_hardened_router()
    return {
        "health": hardened.health_snapshot(),
        "circuits": hardened.circuit_snapshot(),
    }
```

Returns:
```json
{
  "health": {
    "anthropic": {"total_calls": 150, "success_rate": 0.98, "avg_latency_ms": 2340, "consecutive_failures": 0, "is_healthy": true},
    "openai": {"total_calls": 12, "success_rate": 1.0, "avg_latency_ms": 1200, "consecutive_failures": 0, "is_healthy": true},
    "google": {"total_calls": 3, "success_rate": 1.0, "avg_latency_ms": 3100, "consecutive_failures": 0, "is_healthy": true},
    "deepseek": {"total_calls": 45, "success_rate": 0.95, "avg_latency_ms": 4500, "consecutive_failures": 0, "is_healthy": true},
    "moonshot": {"total_calls": 22, "success_rate": 0.91, "avg_latency_ms": 5200, "consecutive_failures": 0, "is_healthy": true}
  },
  "circuits": {
    "anthropic": {"state": "closed", "failure_count": 0},
    "openai": {"state": "closed", "failure_count": 0},
    ...
  }
}
```

## Configuration

No new env vars needed. Uses existing keys from `.env`:
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `GOOGLE_API_KEY`
- `DEEPSEEK_API_KEY`
- `KIMI_API_KEY`

Optional tuning (in code):
- `failure_threshold`: 3 (consecutive failures before circuit opens)
- `cooldown_seconds`: 300 (5 min cooldown)
- `max_retries`: 2 (attempts after first failure)
- `base_delay`: 1.0 (initial retry delay, doubles each time)
- `call_timeout`: 180.0 (seconds per call)

## Test Results

Run: `pytest tests/test_llm_hardening.py -v`

Expected: 18 tests covering:
- Health tracking (success/failure counting)
- Circuit breaker (closed → open → half_open → closed)
- Fallback chains (primary succeeds, secondary succeeds, all fail)
- Retry with backoff (success on retry, all retries fail)
- Timeout handling
- Auto-selection of healthy providers
- Error context propagation
