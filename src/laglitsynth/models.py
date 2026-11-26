from pydantic import BaseModel, ConfigDict


class RunMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    tool_version: str = "alpha"
    run_at: str  # ISO-8601 UTC
    validation_skipped: int  # records dropped by read_jsonl on ValidationError


class LlmMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    base_url: str  # OpenAI-compatible base, including /v1
    decisions_url: str | None  # None for chat stages
    reasoning_effort: str | None  # stage 8 only; None means the provider default
    prompt_sha256: str  # sha256(prompt or question JSON + "\n" + USER_TEMPLATE), full hex
