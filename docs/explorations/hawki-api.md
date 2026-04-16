# Using the HAWKI API for LLM inference

Exploration of using the [HAWKI](https://github.com/HAWK-Digital-Environments/HAWKI)
API as an LLM backend for screening and validation stages.  HAWKI is a
self-hosted gateway that provides access to hosted LLMs (GPT, Qwen, Llama,
etc.) via a simple REST API.  Tested March 2026.

## Why HAWKI

HAWKI gives access to large hosted models (e.g. GPT-5.2, Qwen3 235B) that
are too big to run locally or on a single HPC GPU node.  For the literature
screening pipeline, these models are useful for spot-checking and validating
results from smaller local models — not for bulk processing (rate limits,
cost).

## Authentication

HAWKI requires a Bearer token obtained from your instance's admin.  Set it
as an environment variable:

```bash
export HAWKI_API_KEY="your-api-key"
export HAWKI_BASE_URL="https://your-hawki-instance.example.com"
```

Or place these in a `.env` file (already in `.gitignore`).

## API endpoint

HAWKI exposes a single inference endpoint:

```
POST {HAWKI_BASE_URL}/api/ai-req
```

### Request format

```json
{
  "payload": {
    "model": "gpt-5.2",
    "messages": [
      {
        "role": "system",
        "content": { "text": "You are a helpful assistant." }
      },
      {
        "role": "user",
        "content": { "text": "Hello!" }
      }
    ]
  }
}
```

Headers:

```
Authorization: Bearer {HAWKI_API_KEY}
Content-Type: application/json
Accept: application/json
```

### Response format

```json
{
  "content": {
    "text": "Hello! How can I help you today?"
  }
}
```

The response text is at `response["content"]["text"]`.

### Note on the `stream` field

HAWKI's external API only supports non-streaming responses.  Older versions
(2.0.0) required `"stream": false` in the payload; current versions should
not need it.  If you get 500 errors, try adding `"stream": false` to the
payload as a workaround — see the [HAWKI issue
history](https://github.com/HAWK-Digital-Environments/HAWKI/issues) for
details.

## curl example

```bash
curl -X POST "${HAWKI_BASE_URL}/api/ai-req" \
  -H "Authorization: Bearer $HAWKI_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{
    "payload": {
      "model": "gpt-5.2",
      "messages": [
        {
          "role": "user",
          "content": { "text": "Summarize the key features of quantum computing." }
        }
      ]
    }
  }'
```

## Python example

```python
"""Minimal HAWKI API client."""

import os

from dotenv import load_dotenv
import requests

load_dotenv()

HAWKI_BASE_URL = os.environ["HAWKI_BASE_URL"]
HAWKI_API_KEY = os.environ["HAWKI_API_KEY"]


def chat(message: str, model: str = "gpt-5.2") -> str:
    """Send a message to the HAWKI API and return the response text."""
    resp = requests.post(
        f"{HAWKI_BASE_URL}/api/ai-req",
        headers={
            "Authorization": f"Bearer {HAWKI_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "payload": {
                "model": model,
                "messages": [
                    {"role": "user", "content": {"text": message}},
                ],
            }
        },
    )
    resp.raise_for_status()
    return resp.json()["content"]["text"]
```

## Available models (as of March 2026)

The model list depends on the HAWKI instance configuration.  Models seen
during testing:

| Model ID | Description |
|----------|-------------|
| `gpt-5.2` | OpenAI GPT-5.2 |
| `gpt-5` | OpenAI GPT-5 |
| `glm-4.7` | GLM 4.7 |
| `qwen3-235b-a22b` | Qwen3 235B (MoE, 22B active) |
| `qwen3-coder-30b-a3b-instruct` | Qwen3 Coder 30B |
| `meta-llama-3.1-8b-instruct` | Meta Llama 3.1 8B |
| `teuken-7b-instruct-research` | Teuken 7B (research) |

Use `gpt-5.2` or `qwen3-235b-a22b` for best quality on validation tasks.

## Differences from the Ollama API

HAWKI does **not** implement the OpenAI-compatible `/v1/chat/completions`
endpoint.  The request and response formats differ from what the current
`filter-abstracts` code expects:

- Request: messages use `{"content": {"text": "..."}}` instead of
  `{"content": "..."}`
- Response: result is at `["content"]["text"]` instead of
  `["choices"][0]["message"]["content"]`
- No structured output / `response_format` support

Integrating HAWKI as a backend for `filter-abstracts` would require either
an adapter in `classify_abstract()` or a thin proxy that translates between
the two formats.

## Open questions

- What are the rate limits on the HAWKI API?
- Does HAWKI support `response_format` or JSON mode for structured output?
- Is there a model listing endpoint (`GET /api/models` or similar)?
- Would a lightweight adapter (HAWKI-to-OpenAI translation) be worth
  building, or is it simpler to use HAWKI only for manual spot checks?
