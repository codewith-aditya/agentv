from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from .config import Settings
from .db import ProviderRow
from .render import split_think

log = logging.getLogger(__name__)

DeltaFn = Callable[[str], Awaitable[None]]


class LLMError(RuntimeError):
    pass


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def parsed_arguments(self) -> dict[str, Any]:
        raw = (self.arguments or "").strip() or "{}"
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {"value": value}
        except json.JSONDecodeError:
            return {}


@dataclass(slots=True)
class LLMResult:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    """OpenAI-compatible client with SSE streaming, tool calls and retries."""

    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.llm_timeout, connect=20.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _headers(provider: ProviderRow) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare blocks urllib's default UA on modelrouter-style gateways.
            "User-Agent": "AgentV/2.0",
        }
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        extra_headers = (provider.extra or {}).get("headers") or {}
        if isinstance(extra_headers, dict):
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        return headers

    @staticmethod
    def _base(provider: ProviderRow) -> str:
        return provider.normalized_base

    def _payload(
        self,
        provider: ProviderRow,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        stream: bool,
        temperature: float,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        extra = provider.extra or {}
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": float(extra.get("temperature", temperature)),
        }
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = extra.get("tool_choice", "auto")
        max_tokens = extra.get("max_tokens")
        if max_tokens:
            # reasoning models need headroom or content comes back empty
            payload["max_tokens"] = max(512, int(max_tokens))
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    # ------------------------------------------------------------------ public API
    async def list_models(self, provider: ProviderRow) -> list[str]:
        url = f"{self._base(provider)}/models"
        resp = await self._http.get(url, headers=self._headers(provider))
        if resp.status_code >= 400:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else data
        out: list[str] = []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("id"):
                    out.append(str(item["id"]))
                elif isinstance(item, str):
                    out.append(item)
        return out

    async def chat(
        self,
        provider: ProviderRow,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.4,
        on_delta: DeltaFn | None = None,
        on_reasoning: DeltaFn | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResult:
        model = model or provider.model
        if not model:
            raise LLMError(f"provider '{provider.name}' has no model configured")
        if not self._base(provider):
            raise LLMError(f"provider '{provider.name}' has no base_url configured")

        extra = provider.extra or {}
        want_stream = on_delta is not None and not extra.get("no_stream")
        last_err: Exception | None = None
        attempts = max(1, int(self.cfg.llm_max_retries))

        for attempt in range(1, attempts + 1):
            emitted = {"any": False}

            async def delta(text: str) -> None:
                emitted["any"] = True
                if on_delta:
                    await on_delta(text)

            try:
                if want_stream:
                    return await self._stream(
                        provider, messages, tools, model, temperature, delta, on_reasoning, reasoning_effort
                    )
                return await self._once(provider, messages, tools, model, temperature, reasoning_effort)
            except _RetryableError as exc:
                last_err = exc
                if emitted["any"]:
                    # partial output already shown: finish rather than duplicate
                    raise LLMError(str(exc)) from exc
                delay = min(8.0, 1.5 * (2 ** (attempt - 1)))
                log.warning("LLM retry %s/%s in %.1fs: %s", attempt, attempts, delay, exc)
                await asyncio.sleep(delay)
            except httpx.HTTPError as exc:
                last_err = exc
                delay = min(8.0, 1.5 * (2 ** (attempt - 1)))
                log.warning("LLM transport error %s/%s in %.1fs: %s", attempt, attempts, delay, exc)
                await asyncio.sleep(delay)

        raise LLMError(str(last_err) if last_err else "LLM request failed")

    # ------------------------------------------------------------------ requests
    async def _once(
        self,
        provider: ProviderRow,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        temperature: float,
        reasoning_effort: str | None = None,
    ) -> LLMResult:
        url = f"{self._base(provider)}/chat/completions"
        payload = self._payload(
            provider, messages, tools, model, stream=False, temperature=temperature, reasoning_effort=reasoning_effort
        )
        resp = await self._http.post(url, headers=self._headers(provider), json=payload)
        self._raise_for_status(resp)
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
        calls: list[ToolCall] = []
        for idx, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            calls.append(
                ToolCall(
                    id=str(tc.get("id") or f"call_{idx}"),
                    name=str(fn.get("name") or ""),
                    arguments=fn.get("arguments") or "{}",
                )
            )
        usage = data.get("usage") or {}
        inline_reasoning, clean_content = split_think(str(content or ""))
        return LLMResult(
            content=clean_content,
            reasoning="\n".join(p for p in (str(reasoning or ""), inline_reasoning) if p).strip(),
            tool_calls=calls,
            finish_reason=str(choice.get("finish_reason") or ""),
            usage={
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
            },
            raw=data,
        )

    async def _stream(
        self,
        provider: ProviderRow,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        temperature: float,
        on_delta: DeltaFn,
        on_reasoning: DeltaFn | None,
        reasoning_effort: str | None = None,
    ) -> LLMResult:
        url = f"{self._base(provider)}/chat/completions"
        payload = self._payload(
            provider, messages, tools, model, stream=True, temperature=temperature, reasoning_effort=reasoning_effort
        )
        content = ""
        reasoning = ""
        finish = ""
        usage: dict[str, int] = {}
        calls: dict[int, dict[str, str]] = {}
        started = False

        async with self._http.stream("POST", url, headers=self._headers(provider), json=payload) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")
                self._raise_for_status_code(resp.status_code, body)
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise LLMError(str(chunk["error"])[:500])
                if chunk.get("usage"):
                    usage = {
                        "prompt_tokens": int(chunk["usage"].get("prompt_tokens") or 0),
                        "completion_tokens": int(chunk["usage"].get("completion_tokens") or 0),
                    }
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                finish = choice.get("finish_reason") or finish
                delta = choice.get("delta") or choice.get("message") or {}
                text = delta.get("content")
                if isinstance(text, list):
                    text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
                if text:
                    content += text
                    started = True
                    inline_reasoning, visible = split_think(content)
                    if visible:
                        await on_delta(visible)
                    if inline_reasoning and on_reasoning:
                        await on_reasoning(inline_reasoning)
                rc = delta.get("reasoning_content") or delta.get("reasoning")
                if rc:
                    reasoning += rc
                    if on_reasoning:
                        await on_reasoning(reasoning)
                for tc in delta.get("tool_calls") or []:
                    idx = int(tc.get("index") or 0)
                    slot = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = str(tc["id"])
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += str(fn["name"])
                    if fn.get("arguments"):
                        slot["arguments"] += str(fn["arguments"])

        tool_calls = [
            ToolCall(
                id=slot["id"] or f"call_{idx}",
                name=slot["name"],
                arguments=slot["arguments"] or "{}",
            )
            for idx, slot in sorted(calls.items())
            if slot["name"]
        ]
        inline_reasoning, clean_content = split_think(content)
        if not started and not clean_content and not tool_calls and finish not in ("stop", "tool_calls"):
            # gateway may not implement streaming; caller can fall back
            raise _RetryableError("empty streamed response")
        return LLMResult(
            content=clean_content,
            reasoning="\n".join(p for p in (reasoning, inline_reasoning) if p).strip(),
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=usage,
        )

    # ------------------------------------------------------------------ errors
    @staticmethod
    def _raise_for_status_code(status: int, body: str) -> None:
        if status in (408, 409, 425, 429) or status >= 500:
            raise _RetryableError(f"HTTP {status}: {body[:300]}")
        raise LLMError(f"HTTP {status}: {body[:500]}")

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            self._raise_for_status_code(resp.status_code, resp.text)


class _RetryableError(RuntimeError):
    pass
