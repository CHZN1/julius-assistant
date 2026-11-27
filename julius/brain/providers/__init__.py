"""Bring your own model: which provider and model each tier uses.

Tiers: front_end (every voice turn starts here), planner (escalate_to_planner), research (the research sub-agent),
vision (pictures and page photos), coding (the coding agent). Each can have its own block in julius.toml:

    [models.front_end]
    provider = "ollama"            # anthropic | openai_compat | ollama, or a preset: lmstudio, llamacpp, vllm, openrouter, ollama_v1
    model = "qwen3:4b-instruct-2507-q4_K_M"
    base_url = "http://localhost:11434"   # optional: each provider and preset has a default
    api_key_env = ""               # optional: the environment variable holding the key (never the key itself)
    price_in = 0.0                 # optional: USD per million input tokens, for the cost ledger (local models cost $0)
    price_out = 0.0
    timeout = 120                  # optional: seconds
    options = { num_ctx = 16384 }  # optional: passed to the provider (Ollama options, or extra chat-completions fields)

Without a block a tier keeps today's behaviour: Anthropic with [models] haiku (front_end, research) or sonnet (planner),
vision with whichever of the two the caller asks for, and coding with `claude -p` ([code] model).

For evals and one-off runs, JULIUS_TIER_<TIER>="provider:model" (and optionally JULIUS_TIER_<TIER>_BASE_URL) overrides the
file, e.g. JULIUS_TIER_FRONT_END="ollama:qwen3:4b-instruct-2507-q4_K_M"."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any

from julius.brain.providers.base import (
    Capabilities,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
)

TIERS = ("front_end", "planner", "research", "vision", "coding")
LEGACY_KEY = {"front_end": "haiku", "planner": "sonnet", "research": "haiku", "vision": None}
FROM_LEGACY = {"haiku": "front_end", "sonnet": "planner"}  # the ledger's and TIERS' old names

PRESETS = {  # name -> (adapter, default base_url, default api_key_env)
    "anthropic": ("anthropic", "", "ANTHROPIC_API_KEY"),
    "openai_compat": ("openai_compat", "", ""),
    "ollama": ("ollama", "http://localhost:11434", ""),
    "ollama_v1": ("openai_compat", "http://localhost:11434/v1", ""),
    "lmstudio": ("openai_compat", "http://localhost:1234/v1", ""),
    "llamacpp": ("openai_compat", "http://localhost:8080/v1", ""),
    "vllm": ("openai_compat", "http://localhost:8000/v1", ""),
    "openrouter": ("openai_compat", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "claude-cli": ("claude-cli", "", ""),
}

CAPABILITY_OPTIONS = (
    "vision",
    "forced_tool_choice",
    "max_output_tokens",
    "deadline_s",
)  # options that describe the model or its limits rather than go to it
# A local model can get stuck repeating itself until it fills its whole output budget (qwen3 4B did on about one call in
# three for one routing case: a minute of tokens, then an HTTP 500 from Ollama). Local tiers therefore get an output cap
# and a deadline by default; [models.<tier>] options max_output_tokens and deadline_s override them.
LOCAL_LIMITS = {
    "front_end": {"max_output_tokens": 512, "deadline_s": 90.0}
}  # 90 s: the first turn after a cold load (model plus a 13k-token prompt) took 54 s on an M4; the cap bounds a loop to ~27 s
LOCAL_DEFAULT = {"deadline_s": 180.0}
HOSTED_DEFAULT = {"deadline_s": 120.0}
TRANSPORT: Any = (
    None  # an httpx transport for every HTTP adapter (tests replay recorded responses through it)
)
STATS: dict[
    str, dict[str, int]
] = {}  # per tier: tool_calls, invalid_json (the tool-call JSON validity floor)
__all__ = [
    "Capabilities",
    "ProviderAuthError",
    "ProviderError",
    "ProviderUnavailable",
    "TierModel",
    "resolve",
    "tier_of",
]


@dataclass(frozen=True)
class TierModel:
    tier: str
    provider: str  # the name as configured (a preset or an adapter)
    adapter: str  # anthropic | openai_compat | ollama | claude-cli
    model: str
    base_url: str = ""
    api_key_env: str = ""
    price_in: float = 0.0
    price_out: float = 0.0
    timeout: float = 0.0
    options: dict[str, Any] = field(default_factory=dict)
    allow_insecure: bool = False  # [models.<tier>] allow_insecure = true: plain http:// to a non-localhost host, warned at start

    @property
    def is_anthropic(self) -> bool:
        return self.adapter == "anthropic"

    def describe(self) -> str:
        """For logs and eval reports: provider, model and endpoint, never a key."""
        where = f" at {self.base_url}" if self.base_url else ""
        return f"{self.provider}:{self.model}{where}"


def tier_of(name: str) -> str:
    """A logical tier for an old tier key ("haiku" -> front_end, "sonnet" -> planner)."""
    return FROM_LEGACY.get(name, name)


def _env_override(tier: str, env: dict[str, str]) -> dict[str, Any]:
    raw = env.get(f"JULIUS_TIER_{tier.upper()}", "").strip()
    if not raw:
        return {}
    provider, _, model = raw.partition(":")
    out: dict[str, Any] = {"provider": provider, "model": model}
    if url := env.get(f"JULIUS_TIER_{tier.upper()}_BASE_URL", "").strip():
        out["base_url"] = url
    if opts := env.get(f"JULIUS_TIER_{tier.upper()}_OPTIONS", "").strip():
        import json

        out["options"] = json.loads(opts)
    return out


def resolve(
    tier: str, config: dict[str, Any], legacy: str | None = None, env: dict[str, str] | None = None
) -> TierModel:
    """The provider and model for `tier`. `legacy` picks the old [models] key when the tier has no block (vision asks for haiku or sonnet)."""
    tier = tier_of(tier)
    if tier not in TIERS:
        raise ValueError(f"unknown model tier {tier!r} (tiers: {', '.join(TIERS)})")
    env = os.environ if env is None else env
    models = config.get("models") or {}
    block = dict(models.get(tier) or {}) if isinstance(models.get(tier), dict) else {}
    block.update(_env_override(tier, env))
    if tier == "coding" and not block:
        code = config.get("code") or {}
        return TierModel(tier, "claude-cli", "claude-cli", str(code.get("model", "")))
    provider = str(block.get("provider") or "anthropic")
    if provider not in PRESETS:
        raise ValueError(
            f"[models.{tier}] provider {provider!r} is not one of: {', '.join(sorted(PRESETS))}"
        )
    adapter, default_url, default_key = PRESETS[provider]
    if tier == "coding" and adapter not in ("claude-cli", "anthropic"):
        raise ValueError(
            "[models.coding] supports claude-cli for now: a non-Anthropic coding model needs Julius's own edit/test loop "
            "(planned in docs/PROVIDERS.md)"
        )
    key = legacy or LEGACY_KEY.get(tier) or "sonnet"
    model = str(block.get("model") or (models.get(key) if adapter == "anthropic" else "") or "")
    if not model:
        raise ValueError(f"[models.{tier}] needs a model for provider {provider!r}")
    base_url = str(block.get("base_url") or default_url)
    if adapter == "openai_compat" and not base_url:
        raise ValueError(f"[models.{tier}] provider openai_compat needs a base_url")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError(f"[models.{tier}] base_url must start with http:// or https://")
    allow_insecure = block.get("allow_insecure") is True
    if base_url.startswith("http://") and not is_localhost_url(base_url) and not allow_insecure:
        raise ValueError(
            f"[models.{tier}] base_url {base_url} is plain http:// to another machine: prompts, memory notes and any key "
            "would travel unencrypted. Use https://, or set allow_insecure = true for this tier if you accept that."
        )
    return TierModel(
        tier=tier,
        provider=provider,
        adapter=adapter,
        model=model,
        base_url=base_url,
        api_key_env=str(block.get("api_key_env") or default_key),
        price_in=float(block.get("price_in") or 0.0),
        price_out=float(block.get("price_out") or 0.0),
        timeout=float(block.get("timeout") or 0.0),
        options=dict(block.get("options") or {}),
        allow_insecure=allow_insecure,
    )


LOCALHOST = ("localhost", "127.0.0.1", "::1")


def is_localhost_url(url: str) -> bool:
    """True only for this Mac itself (a .local name is still the network)."""
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower() in LOCALHOST


def endpoint_warnings(config: dict[str, Any]) -> list[str]:
    """Start-up warnings for endpoints that leave this Mac without TLS (allowed only with allow_insecure = true), and for an
    Ollama server that isn't on this Mac and isn't behind https (Ollama itself has no authentication)."""
    out = []
    for tier in TIERS:
        try:
            tm = resolve(tier, config)
        except ValueError:
            continue  # load_config already refused it
        url = tm.base_url
        if not url or is_localhost_url(url):
            continue
        if url.startswith("http://"):
            out.append(
                f"WARNING: {tier} sends prompts, memory notes and any key unencrypted to {url} (allow_insecure = true)."
            )
        if (tm.adapter == "ollama" or tm.provider == "ollama_v1") and not url.startswith(
            "https://"
        ):
            out.append(
                f"WARNING: {tier} uses an Ollama server at {url}. Ollama has no authentication: anyone who can reach that "
                "port can use it. Keep Ollama bound to localhost, or put it behind https with authentication."
            )
    return out


def configured(config: dict[str, Any]) -> dict[str, TierModel]:
    """Every tier's resolved model (vision with the planner's legacy key)."""
    return {t: resolve(t, config) for t in TIERS}


_CLIENTS: dict[tuple, Any] = {}
_LOCK = threading.Lock()


def is_local(tm: TierModel) -> bool:
    from urllib.parse import urlparse

    host = urlparse(tm.base_url).hostname or ""
    return (
        tm.adapter == "ollama"
        or host in ("localhost", "127.0.0.1", "::1")
        or host.endswith(".local")
    )


def limits_for(tm: TierModel) -> dict[str, Any]:
    """The tier's output cap and deadline: local defaults (tighter for the front end), hosted defaults, then the config."""
    out = dict(LOCAL_LIMITS.get(tm.tier, LOCAL_DEFAULT)) if is_local(tm) else dict(HOSTED_DEFAULT)
    for key in ("max_output_tokens", "deadline_s"):
        if key in tm.options:
            out[key] = tm.options[key]
    return out


def build_provider(
    tm: TierModel, timeout: float, max_retries: int = 1, anthropic_client: Any = None
) -> Any:
    """The adapter for a resolved tier."""
    stats = STATS.setdefault(tm.tier, {})
    if tm.adapter == "anthropic":
        from julius.brain.providers.anthropic import AnthropicProvider

        return AnthropicProvider(tm.model, anthropic_client)
    if tm.adapter == "openai_compat":
        from julius import keys
        from julius.brain.providers.openai_compat import OpenAICompatProvider

        key = keys.get(tm.api_key_env) if tm.api_key_env else None
        if tm.api_key_env and not key:
            raise ProviderAuthError(f"{tm.api_key_env} is not set")
        return OpenAICompatProvider(
            tm.model,
            tm.base_url,
            api_key=key,
            timeout=tm.timeout or timeout,
            capabilities=Capabilities(
                forced_tool_choice=bool(tm.options.get("forced_tool_choice", True)),
                vision=bool(tm.options.get("vision", False)),
            ),
            extra={k: v for k, v in tm.options.items() if k not in CAPABILITY_OPTIONS},
            transport=TRANSPORT,
            limits=limits_for(tm),
            stats=stats,
        )
    if tm.adapter == "ollama":
        from julius.brain.providers.ollama import OllamaProvider

        return OllamaProvider(
            tm.model,
            tm.base_url,
            timeout=tm.timeout or max(timeout, 120.0),
            capabilities=Capabilities(
                forced_tool_choice=False, vision=bool(tm.options.get("vision", False))
            ),
            options={
                **{k: v for k, v in tm.options.items() if k not in CAPABILITY_OPTIONS},
                **limits_for(tm),
            },
            transport=TRANSPORT,
            stats=stats,
        )
    raise ProviderError(f"tier {tm.tier} uses {tm.adapter}, which is not a chat provider")


def client(tm: TierModel, timeout: float, max_retries: int, anthropic_factory: Any) -> Any:
    """A Messages-API-shaped client for the tier: the tagged SDK client for Anthropic, the shim for anything else. Cached."""
    key = (tm.tier, tm.describe(), repr(sorted(tm.options.items())), timeout, max_retries)
    with _LOCK:
        if key in _CLIENTS:
            return _CLIENTS[key]
    if tm.is_anthropic:
        from julius.brain.providers.anthropic import TaggingClient

        made = TaggingClient(anthropic_factory(timeout=timeout, max_retries=max_retries))
    else:
        from julius.brain.providers.shim import MessagesClient

        made = MessagesClient(
            build_provider(tm, timeout, max_retries),
            rates={"in": tm.price_in, "out": tm.price_out},
        )
    with _LOCK:
        _CLIENTS[key] = made
    return made


def reset() -> None:
    """Forget cached clients (a new key, or a config change in tests)."""
    with _LOCK:
        _CLIENTS.clear()


def client_for(brain: Any, tier: str, legacy: str | None = None) -> Any:
    """The client a call site should use: brain.client_for when the brain has it, else its slow client (test doubles)."""
    fn = getattr(brain, "client_for", None)
    if callable(fn):
        return fn(tier, legacy=legacy)
    return brain.slow_client()


def model_for(brain: Any, tier: str, legacy: str | None = None) -> str:
    """The model name a call site should send: the tier's, or the old [models] key on a brain without tiers."""
    fn = getattr(brain, "model_for", None)
    if callable(fn):
        return fn(tier, legacy=legacy)
    return brain.config["models"][legacy or LEGACY_KEY.get(tier_of(tier)) or "sonnet"]
