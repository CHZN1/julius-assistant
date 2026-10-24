"""Shared, lazy backend adapter; importing does not load models or devices."""
import os, time, re, json as _json
from types import SimpleNamespace
import julius_tools as jt
LLM_BACKEND = os.environ.get("JULIUS_BACKEND", "ollama")
LLM = os.environ.get("JULIUS_MODEL", "qwen3:8b" if LLM_BACKEND == "ollama" else "mlx-community/Qwen3-8B-4bit")
KEEP_ALIVE = "5m"
_BACKEND_USED = False
_PROMPT_CACHE = None
_MLX_MODEL = None
_MLX_TOKENIZER = None
_RELEASE_METRICS = {}
_GENERATION_METRICS = {}

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def _load_mlx_brain():
    """Load the quantized Qwen3 brain once, in-process."""
    global _MLX_MODEL, _MLX_TOKENIZER
    if _MLX_MODEL is None:
        print(f"loading MLX brain: {LLM}...", flush=True)
        _t = time.time()
        from mlx_lm import load as mlx_load
        import mlx.core as mx
        mx.set_cache_limit(256 * 1024 * 1024)
        if os.environ.get('JULIUS_MLX_BRAIN_MEMORY_GB'):
            limit=float(os.environ['JULIUS_MLX_BRAIN_MEMORY_GB'])
            if not 2<=limit<=8:raise ValueError('MLX brain memory guidance must be between 2 and 8 GiB')
            mx.set_memory_limit(int(limit*1024**3))
        _MLX_MODEL, _MLX_TOKENIZER = mlx_load(LLM)
        print(f"  MLX brain loaded in {time.time()-_t:.1f}s", flush=True)
    return _MLX_MODEL, _MLX_TOKENIZER


def _normalize_mlx_messages(messages):
    """Normalize Julius/Ollama-style tool-call history for Qwen's chat template."""
    out = []
    for msg in messages:
        m = dict(msg)

        if m.get("role") == "assistant" and m.get("tool_calls"):
            normalized = []
            for call in m["tool_calls"]:
                fn = call.get("function", call)
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = _json.loads(args)
                    except Exception:
                        args = {}
                normalized.append({
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": args,
                    },
                })
            m["tool_calls"] = normalized

        out.append(m)
    return out


def _render_mlx_prompt(messages, tools=None):
    """Apply Qwen3's native chat template with thinking explicitly disabled."""
    _, tok = _load_mlx_brain()
    msgs = _normalize_mlx_messages(messages)

    # A prior tool call/result still needs the tool-aware Qwen template even when
    # the caller is now asking for the final natural-language response.
    has_tool_history = any(
        m.get("role") == "tool"
        or (m.get("role") == "assistant" and m.get("tool_calls"))
        for m in msgs
    )
    active_tools = tools if tools is not None else (jt.SCHEMAS if has_tool_history else None)

    kwargs = dict(
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    if active_tools is not None:
        kwargs["tools"] = active_tools

    return tok.apply_chat_template(msgs, **kwargs)


def _parse_mlx_tool_calls(text):
    """Convert Qwen3 <tool_call> blocks into the object shape Julius already uses."""
    calls = []
    if "<tool_call>" in (text or "") and not _TOOL_CALL_RE.search(text):
        raise ValueError("Truncated MLX tool call")
    for raw in _TOOL_CALL_RE.findall(text or ""):
        obj = _json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("Tool call must be an object")
        name = obj.get("name")
        args = obj.get("arguments", {})
        if isinstance(args, str):
            args = _json.loads(args)
        if not isinstance(args, dict) or name not in jt.REGISTRY:
            raise ValueError("Invalid tool name or arguments")
        calls.append(SimpleNamespace(function=SimpleNamespace(name=name, arguments=args)))
    return calls


def _mlx_generation_kwargs(options=None):
    """Translate the small subset of Ollama options Julius actually uses."""
    options = options or {}
    max_tokens = int(options.get("num_predict", 256))
    temperature = float(options.get("temperature", 0.0))

    kwargs = {"max_tokens": max_tokens}
    from mlx_lm.sample_utils import make_sampler
    kwargs["sampler"] = make_sampler(temp=temperature, top_p=0.9)
    return kwargs


def llm_chat(*, messages, tools=None, think=False, stream=False,
             keep_alive=KEEP_ALIVE, options=None):
    """MLX-backed drop-in adapter for the subset of ollama.chat Julius uses.

    The rest of Julius can keep using:
      response.message.content
      response.message.tool_calls
      for part in response: part.message.content
    """
    global _BACKEND_USED
    _BACKEND_USED = True
    _GENERATION_METRICS.clear()
    if LLM_BACKEND == "ollama":
        import ollama
        defaults={"num_ctx":4096}
        if os.environ.get('JULIUS_LLM_BATCH_SIZE'):
            batch=int(os.environ['JULIUS_LLM_BATCH_SIZE'])
            if not 32<=batch<=512:raise ValueError('JULIUS_LLM_BATCH_SIZE must be between 32 and 512')
            defaults['num_batch']=batch
        options = {**defaults, **(options or {})}
        return ollama.chat(model=LLM, messages=messages, tools=tools, think=False,
                           stream=stream, keep_alive=keep_alive, options=options)
    from mlx_lm import generate as mlx_generate, stream_generate as mlx_stream_generate
    if LLM_BACKEND != "mlx":
        raise RuntimeError(f"Unsupported LLM backend: {LLM_BACKEND}")

    model, tok = _load_mlx_brain()
    prompt = _render_mlx_prompt(messages, tools=tools)
    gen_kwargs = _mlx_generation_kwargs(options)
    prefill=int(os.environ.get('JULIUS_MLX_PREFILL_STEP_SIZE','256'))
    if not 64<=prefill<=512:raise ValueError('MLX prefill step must be between 64 and 512 tokens')
    gen_kwargs["prefill_step_size"] = prefill

    from mlx_lm.models.cache import LRUPromptCache, make_prompt_cache
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = LRUPromptCache(max_size=1, max_bytes=768 * 1024 * 1024)
    tokens = tok.encode(prompt, add_special_tokens=False)
    use_cache = os.environ.get("JULIUS_MLX_CACHE", "1") == "1"
    cache, rest = _PROMPT_CACHE.fetch_nearest_cache(LLM, tokens) if use_cache else (None, tokens)
    if cache is None:
        cache = make_prompt_cache(model)
    if not rest:
        # Keep one prompt token to compute the next-token distribution.
        from mlx_lm.models.cache import trim_prompt_cache
        trim_prompt_cache(cache, 1)
        rest = tokens[-1:]

    def _parts():
        key = tokens[:]
        completed = False
        try:
            for part in mlx_stream_generate(model, tok, prompt=rest,
                                            prompt_cache=cache, **gen_kwargs):
                key.append(part.token)
                for name in ('prompt_tokens','prompt_tps','generation_tokens','generation_tps','peak_memory','finish_reason'):
                    value=getattr(part,name,None)
                    if value is not None:_GENERATION_METRICS[name]=value
                yield SimpleNamespace(message=SimpleNamespace(content=part.text))
            completed = True
        finally:
            if use_cache and completed:
                _PROMPT_CACHE.insert_cache(LLM, key, cache)

    if stream:
        return _parts()
    raw = "".join(part.message.content for part in _parts())
    calls = _parse_mlx_tool_calls(raw)
    content = _TOOL_CALL_RE.sub("", raw or "").strip()
    return SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))


def prepare_backend():
    """Warm native imports/kernels before readiness, then release all weights."""
    if LLM_BACKEND!='mlx':return
    from julius_conversation import conversation_messages
    try:
        llm_chat(messages=conversation_messages([{'role':'user','content':'Reply only Ready.'}]),
                 options={'temperature':0.4,'num_predict':2})
    finally:close_backend()


def generation_metrics():
    return dict(_GENERATION_METRICS)


def release_metrics():
    return dict(_RELEASE_METRICS)


def close_backend():
    """Release model weights and caches before another model phase starts."""
    global _BACKEND_USED, _MLX_MODEL, _MLX_TOKENIZER, _PROMPT_CACHE
    if _BACKEND_USED and LLM_BACKEND == "ollama":
        import ollama
        ollama.generate(model=LLM, prompt="", keep_alive=0)
        _BACKEND_USED = False
        _RELEASE_METRICS.clear()
        _RELEASE_METRICS.update(backend='ollama',released=True)
    elif LLM_BACKEND == "mlx" and (_BACKEND_USED or _MLX_MODEL is not None):
        import gc
        import mlx.core as mx
        mx.synchronize()
        _PROMPT_CACHE=None
        _MLX_MODEL=None
        _MLX_TOKENIZER=None
        gc.collect();mx.clear_cache()
        _BACKEND_USED=False
        _RELEASE_METRICS.clear()
        _RELEASE_METRICS.update(backend='mlx',released=True,active_memory_bytes=mx.get_active_memory())
