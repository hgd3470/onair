"""Small provider boundary: fixed endpoints, no key/body/exception logging."""
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from dotenv import dotenv_values
from pydantic import BaseModel


class ProviderError(RuntimeError):
    def __init__(self, provider, model, code, status=None, diagnostics=None):
        self.provider, self.model, self.code, self.status = provider, model, code, status
        self.diagnostics = diagnostics or {}
        super().__init__(f"{provider}:{model}:{code}" + (f":HTTP_{status}" if status else ""))


def credentials(env_file: Path | None = None, *, root: Path | None = None) -> dict[str, str]:
    # Only application runtime opens the user's .env. Never print values, length,
    # prefixes, repr, environment dictionaries, request headers or raw exceptions.
    # Resolve only the selected project .env; never search parent directories.
    env_file = env_file if env_file is not None else (root or Path.cwd()) / '.env'
    from_file = dotenv_values(env_file, interpolate=False)
    keys = {}
    for name in (
        "openai_api_key",
        "gemini_api_key",
        "google_application_credentials",
        "gcp_project_id",
        "gcp_location",
    ):
        value = os.environ.get(name) or os.environ.get(name.upper()) or from_file.get(name)
        if value:
            keys[name] = value
    return keys


@dataclass
class Completion:
    value: BaseModel
    usage: dict
    elapsed_sec: float
    response_model: str


class Provider(Protocol):
    def complete(self, system: str, payload: dict, schema: type[BaseModel]) -> Completion: ...


def strict_schema(schema: dict) -> dict:
    """OpenAI strict schemas require every object property in required."""
    if isinstance(schema, dict):
        schema = {k: strict_schema(v) for k, v in schema.items() if k != "default"}
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
            schema["required"] = list(schema.get("properties", {}))
    elif isinstance(schema, list):
        schema = [strict_schema(v) for v in schema]
    return schema


def clean_gemini_schema(schema: dict) -> dict:
    """Inline all $defs / $ref and strip OpenAPI-incompatible fields for Gemini / Vertex API."""
    defs = schema.get("$defs", {})

    def resolve(node, parent_key=None):
        if isinstance(node, dict):
            if "$ref" in node:
                ref_key = node["$ref"].split("/")[-1]
                if ref_key in defs:
                    resolved = resolve(defs[ref_key], parent_key)
                    extra = {k: resolve(v, k) for k, v in node.items() if k != "$ref"}
                    return {**resolved, **extra}
            res = {}
            for k, v in node.items():
                if k in {"$defs", "additionalProperties"}:
                    continue
                if k == "title" and isinstance(v, str) and parent_key != "properties":
                    continue
                res[k] = resolve(v, k)
            return res
        elif isinstance(node, list):
            return [resolve(v, parent_key) for v in node]
        return node

    return resolve(schema)


def _resolve_sa_path(path_str: str, root: Path | None = None) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    if root and (root / p).exists():
        return (root / p).resolve()
    if (Path.cwd() / p).exists():
        return (Path.cwd() / p).resolve()
    return (root / p).resolve() if root else p.resolve()


class GoogleAuthHelper:
    def __init__(self, key_path: str | Path | None = None, project_id: str | None = None, root: Path | None = None):
        self.raw_path = key_path
        self.root = root
        self._explicit_project_id = project_id
        self._creds = None

    def get_credentials(self):
        if self._creds is None:
            scopes = ["https://www.googleapis.com/auth/cloud-platform"]
            if self.raw_path:
                sa_path = _resolve_sa_path(str(self.raw_path), self.root)
                if not sa_path.exists():
                    raise FileNotFoundError(f"service_account_file_not_found: {sa_path.name}")
                from google.oauth2 import service_account
                self._creds = service_account.Credentials.from_service_account_file(
                    str(sa_path),
                    scopes=scopes,
                )
            else:
                import google.auth
                self._creds, _ = google.auth.default(scopes=scopes)
        return self._creds

    @property
    def project_id(self) -> str | None:
        if self._explicit_project_id:
            return self._explicit_project_id
        creds = self.get_credentials()
        return getattr(creds, "project_id", None)

    def get_token(self) -> str:
        from google.auth.transport.requests import Request
        creds = self.get_credentials()
        if not creds.valid or creds.expired:
            creds.refresh(Request())
        return creds.token


class APIProvider:
    def __init__(self, settings: dict, keys: dict, timeout: float = 180, root: Path | None = None):
        self.settings, self.keys, self.timeout = settings, keys, timeout
        self.root = root
        self._auth_helper: GoogleAuthHelper | None = None

    def _get_auth_helper(self) -> GoogleAuthHelper:
        if self._auth_helper is None:
            sa_path = self.keys.get("google_application_credentials")
            proj_id = self.keys.get("gcp_project_id")
            self._auth_helper = GoogleAuthHelper(key_path=sa_path, project_id=proj_id, root=self.root)
        return self._auth_helper

    def complete(self, system, payload, schema):
        s = self.settings
        provider, model = s["provider"], s["model"]
        if provider not in {"gemini", "openai", "vertex"}:
            raise ProviderError(provider, model, "unsupported_provider")

        has_gemini = "gemini_api_key" in self.keys or "google_application_credentials" in self.keys
        if provider in {"gemini", "vertex"} and not has_gemini:
            raise ProviderError(provider, model, "missing_key")
        if provider == "openai" and "openai_api_key" not in self.keys:
            raise ProviderError(provider, model, "missing_key")

        # Credentials remain in HTTP headers, never URL query parameters.
        if provider in {"gemini", "vertex"}:
            sa_cred = self.keys.get("google_application_credentials")
            # Explicit Gemini API-key use must not be shadowed by host SA settings.
            # Keep service-account-only Gemini compatibility and explicit Vertex auth.
            if provider == "vertex" or (sa_cred and not self.keys.get("gemini_api_key")):
                try:
                    auth = self._get_auth_helper()
                    token = auth.get_token()
                    project_id = self.keys.get("gcp_project_id") or auth.project_id
                    location = self.keys.get("gcp_location") or s.get("location") or "us-central1"
                except Exception as exc:
                    raise ProviderError(provider, model, "auth_failed", diagnostics={"auth_error": type(exc).__name__}) from None

                if project_id:
                    endpoint = f"{location}-aiplatform.googleapis.com" if location != "global" else "aiplatform.googleapis.com"
                    url = f"https://{endpoint}/v1/projects/{project_id}/locations/{location}/publishers/google/models/{model}:generateContent"
                    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                    gen_cfg = {
                        "maxOutputTokens": s["max_output_tokens"],
                        "responseMimeType": "application/json",
                        "responseSchema": clean_gemini_schema(schema.model_json_schema()),
                    }
                    if "effort" in s:
                        gen_cfg["thinkingConfig"] = {"thinkingLevel": s["effort"]}
                    body = {
                        "systemInstruction": {"parts": [{"text": system}]},
                        "contents": [{"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
                        "generationConfig": gen_cfg,
                    }
                else:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                    body = {
                        "systemInstruction": {"parts": [{"text": system}]},
                        "contents": [{"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
                        "generationConfig": {
                            "thinkingConfig": {"thinkingLevel": s["effort"]},
                            "maxOutputTokens": s["max_output_tokens"],
                            "responseMimeType": "application/json",
                            "responseJsonSchema": schema.model_json_schema(),
                        },
                    }
            else:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                headers = {"x-goog-api-key": self.keys["gemini_api_key"]}
                body = {
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
                    "generationConfig": {
                        "thinkingConfig": {"thinkingLevel": s["effort"]},
                        "maxOutputTokens": s["max_output_tokens"],
                        "responseMimeType": "application/json",
                        "responseJsonSchema": schema.model_json_schema(),
                    },
                }
        else:
            url = "https://api.openai.com/v1/responses"
            headers = {"Authorization": f"Bearer {self.keys['openai_api_key']}"}
            body = {"model": model, "instructions": system,
                    "input": json.dumps(payload, ensure_ascii=False), "store": False,
                    "reasoning": {"effort": s["effort"]}, "max_output_tokens": s["max_output_tokens"],
                    "text": {"format": {"type": "json_schema", "name": schema.__name__,
                        "strict": True, "schema": strict_schema(schema.model_json_schema())}}}
        start = time.monotonic()
        max_attempts = 4
        for attempt in range(max_attempts):
            try:
                response = httpx.post(url, headers=headers, json=body, timeout=self.timeout)
            except httpx.TimeoutException:
                # No retry after ambiguous transport failure: request may be billed.
                raise ProviderError(provider, model, "transport_or_timeout",
                                    diagnostics={"transport_failure": "timeout"}) from None
            except httpx.NetworkError:
                raise ProviderError(provider, model, "transport_or_timeout",
                                    diagnostics={"transport_failure": "network_error"}) from None
            if response.status_code == 429 and attempt < max_attempts - 1:
                # 429 RESOURCE_EXHAUSTED: rate limit or quota burst.
                # Back off exponentially (5s, 10s, 20s) to clear rolling window.
                retry_sec = 5.0 * (2 ** attempt)
                try:
                    err = response.json().get('error', {})
                    for detail in err.get('details', []):
                        if detail.get('@type', '').endswith('RetryInfo'):
                            delay_str = str(detail.get('retryDelay', ''))
                            if delay_str.endswith('s'):
                                retry_sec = max(retry_sec, float(delay_str[:-1]))
                except Exception:
                    pass
                time.sleep(retry_sec)
                if provider == "vertex" and self._auth_helper:
                    try:
                        headers["Authorization"] = f"Bearer {self._auth_helper.get_token()}"
                    except Exception:
                        pass
                continue
            if response.status_code in {500, 502, 503, 504} and attempt < max_attempts - 1:
                time.sleep(2 ** attempt)
                continue
            if response.is_error:
                # Allowlist quota diagnostics; never save response bodies/messages.
                diagnostics = {}
                try:
                    err = response.json().get('error', {})
                    diagnostics['status'] = err.get('status')
                    for detail in err.get('details', []):
                        if detail.get('@type', '').endswith('QuotaFailure'):
                            diagnostics['quotas'] = [{k: v for k, v in item.items()
                                if k in {'quotaMetric', 'quotaId', 'quotaValue'}}
                                for item in detail.get('violations', [])]
                        if detail.get('@type', '').endswith('RetryInfo'):
                            diagnostics['retryDelay'] = detail.get('retryDelay')
                except Exception:
                    pass
                raise ProviderError(provider, model, "api_error", response.status_code, diagnostics) from None
            break
        try:
            data = response.json()
            if provider in {"gemini", "vertex"}:
                candidate = (data.get("candidates") or [{}])[0]
                if candidate.get("finishReason") != "STOP":
                    # Only enums and counts: never response text, reasoning, or headers.
                    reason = candidate.get("finishReason")
                    allowed = {"MAX_TOKENS", "SAFETY", "RECITATION", "OTHER", "BLOCKLIST",
                               "PROHIBITED_CONTENT", "SPII", "MALFORMED_FUNCTION_CALL",
                               "LANGUAGE", "FINISH_REASON_UNSPECIFIED"}
                    diagnostics = {"finish_reason": reason if reason in allowed else "UNKNOWN"}
                    block = data.get("promptFeedback", {}).get("blockReason")
                    if block in {"SAFETY", "OTHER", "BLOCKLIST", "PROHIBITED_CONTENT"}:
                        diagnostics["block_reason"] = block
                    usage = data.get("usageMetadata", {})
                    diagnostics["token_counts"] = {
                        k: v for k, v in usage.items()
                        if k in {"promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount"}
                        and type(v) is int and v >= 0}
                    raise ProviderError(provider, model, "incomplete_or_blocked", diagnostics=diagnostics)
                raw = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought"))
                usage = data.get("usageMetadata", {})
                returned_model = data.get("modelVersion", model)
            else:
                refusal = any(c.get("type") == "refusal" for item in data.get("output", [])
                              for c in item.get("content", []))
                if data.get("status") != "completed" or refusal:
                    status = data.get("status")
                    reason = (data.get("incomplete_details") or {}).get("reason")
                    diagnostics = {
                        "response_status": status if status in {
                            "completed", "failed", "in_progress", "cancelled", "queued", "incomplete"
                        } else "UNKNOWN",
                        "incomplete_reason": reason if reason in {
                            "max_output_tokens", "content_filter", "steered"
                        } else "UNKNOWN",
                        "refusal_present": refusal,
                    }
                    usage = data.get("usage") or {}
                    diagnostics["token_counts"] = {
                        k: v for k, v in usage.items()
                        if k in {"input_tokens", "output_tokens", "total_tokens"}
                        and type(v) is int and v >= 0}
                    reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
                    if type(reasoning) is int and reasoning >= 0:
                        diagnostics["token_counts"]["reasoning_tokens"] = reasoning
                    raise ProviderError(provider, model, "incomplete_or_refused", diagnostics=diagnostics)
                raw = "".join(c.get("text", "") for item in data.get("output", [])
                              for c in item.get("content", []) if c.get("type") == "output_text")
                usage = data.get("usage", {})
                returned_model = data.get("model", model)
            parsed = schema.model_validate_json(raw)
        except ProviderError:
            raise
        except Exception:
            # Validation errors can embed entire model output. Keep diagnostics bounded.
            raise ProviderError(provider, model, "invalid_structured_output") from None
        return Completion(parsed, usage, round(time.monotonic() - start, 3), returned_model)
