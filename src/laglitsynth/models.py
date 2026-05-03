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
    temperature: float
    prompt_sha256: str  # sha256(SYSTEM_PROMPT + "\n" + user prompt), full hex digest
