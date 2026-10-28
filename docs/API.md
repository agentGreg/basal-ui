# basal-ui API server

One port for every model loaded in basal-ui. The API server inside basal-ui checks the API keys and CORS, stamps
request ids, translates OpenAI Decisions and forwards each request to the private `basal-serve` of the model it
names; the request and response bodies are the engine's own, so the full request body schema, endpoint list and
question-type reference live in the [engine's API reference](https://github.com/rkinas/basal/blob/main/docs/API.md).
Turning the server on, creating keys and the settings table: [USAGE.md](USAGE.md#api-server).

## Several models behind basal-ui

basal-ui's API server ([USAGE.md](USAGE.md#api-server)) serves every model loaded in the app on one port and
routes by the request's `model`, which is therefore **required** there, on `/v1/systemone`, `/v1/basal`,
`/v1/decisions` and `/decisions`. Use a model id from `GET /v1/models` (the variant repository id, for example
`Remek/basal-2.0-mini-MLX-8bit`). The body is otherwise the engine's own System One body; the answer is the model's
response.

| Case | Status | `code` |
|---|---|---|
| no `model` (or not a string) | 400 | `MODEL_REQUIRED` — the message lists the loaded models |
| `model` not loaded or not ready | 404 | `MODEL_NOT_FOUND` — the message lists the loaded models |
| the model was unloaded while the request arrived | 409 | `MODEL_NOT_READY` |
| its `basal-serve` cannot be reached | 502 | `UPSTREAM_ERROR` |

Errors use the basal envelope (`{"error", "code"}`) on the System One paths and the OpenAI envelope on the Decisions
paths, like basal-serve's own errors.

`GET /v1/models` lists the ready models in both shapes: `models` holds each model's basal-serve entry (as above) and
`data` the OpenAI list (`{"id", "object": "model", "created", "owned_by": "basal"}`), with `"object": "list"`.
`GET /health` answers `{"status": "ok", "models": [ids]}`.

`GET /v1/stats` returns basal-ui's statistics: `{"models": {id: {"requests", "questions", "errors", "input_tokens",
"avg_latency_ms", "p50_ms", "p95_ms", "avg_best_prob", "first_used", "last_used", "by_type": {type: {"questions",
"avg_best_prob"}}}}, "benchmarks": {id: [results]}}`. With **Require an API key** it needs an admin key (only
basal-ui's internal key is one; user keys get 403), otherwise it is open like `/v1/keys`.

MCP on this port: `decide` and `decide_many` serve the model named in the tool's `model` argument, else the first
loaded model. The engine's MCP server lists no models (`list_models` and `basal-mcp --model` arrive with the
model-argument proposal upstream); until then the first loaded model answers every tool call.

## Errors and request IDs

System One and `/v1/basal` use an envelope with a string `error` and machine-readable `code`. Validation failures also
include `detail`:

```json
{"error":"question 'q': 1 options (supported: 2..255)","code":"INVALID_REQUEST",
 "detail":[{"loc":["body"],"msg":"question 'q': 1 options (supported: 2..255)","type":"value_error"}]}
```

| Status | Code | Route / meaning |
|---|---|---|
| 400 | `INVALID_JSON` | System One or Decisions: malformed JSON or body is not an object |
| 400 | `INVALID_REQUEST` | Decisions: invalid input or question shape / limits |
| 400 | `unsupported_parameter` | Decisions: unsupported top-level parameter (`param` names it) |
| 401 | `UNAUTHORIZED` | Any protected route: missing or invalid API key |
| 403 | `FORBIDDEN` | `/v1/keys`: a non-admin key requested key usage |
| 503 | `OUT_OF_MEMORY` | CUDA, MPS, or Metal GPU memory exhaustion |
| 422 | `INVALID_REQUEST` | System One validation / media failure, or translated Decisions inference validation |
| 500 | `INTERNAL_ERROR` | Unexpected server failure; details are logged to stderr |

A malformed body example is `400 {"error":"malformed JSON","code":"INVALID_JSON"}`. On `/v1/decisions` and
`/decisions`, errors use the OpenAI envelope:

```json
{"error":{"message":"unsupported parameter: temperature","type":"invalid_request_error",
 "param":"temperature","code":"unsupported_parameter"}}
```

Decisions input/parameter errors return 400; translated inference validation returns 422; authentication failures
return 401; unexpected server failures are 500. Auth errors use `authentication_error`, server errors use
`server_error`, and request errors use `invalid_request_error`. The `error` message remains useful for display. Before
this API change every failure returned 422 with `{"error":"..."}`. Now malformed JSON is 400, GPU memory exhaustion is 503, unexpected internal
failures are 500, and responses include `code`; the existing `error` string is unchanged for the same failure.

Every HTTP response, including errors and `/health`, has `x-request-id` and `x-typesafe-request-id` with the same
value. A valid incoming `x-request-id` (1–128 ASCII letters, digits, `.`, `_`, `:`, or `-`) is echoed in both; an
invalid or absent value is replaced with `req_<uuid4 hex>`. CORS exposes both headers.

## Authentication and CORS

By default the API has no key requirement. Keys are created in the UI (API server view, **Require an API key**;
[USAGE.md](USAGE.md#api-server)); the API server reads them from the key file next to its settings, in this JSON format:

```json
{"keys":[{"name":"laptop","sha256":"<64 lowercase hexadecimal SHA-256 characters>","admin":false}]}
```

Create a random secret and its stored digest without basal-ui:

```bash
python -c 'import hashlib,secrets; s="bsl_"+secrets.token_urlsafe(32); print("secret:",s); print("sha256:",hashlib.sha256(s.encode()).hexdigest())'
```

Put the printed secret in your secret store and only the digest in the key file. Send the secret as
`Authorization: Bearer <secret>` or `X-API-Key: <secret>`. `/health` is always open. The key file is checked again
when its modification time or size changes, so removing a key revokes it without restarting the API server.
`GET /v1/keys` requires an `admin: true` key and reports each key's name and in-memory `last_used` timestamp (null
until used); it does not return secrets or hashes.

The **CORS origins** setting (API server view) takes browser origins, one per line, or `*`. CORS permits GET, POST and OPTIONS and the headers
`Authorization`, `Content-Type`, `X-API-Key`, `X-Request-Id`, `Mcp-Session-Id` and `Mcp-Protocol-Version`. It exposes `Mcp-Session-Id`, `x-request-id` and `x-typesafe-request-id`. Preflight
requests are handled before authentication; allowed origins receive CORS headers, and a foreign origin is rejected
with HTTP 400 and no allow-origin header.

## OpenAI Decisions compatibility

`POST /v1/decisions` and `POST /decisions` translate the OpenAI Decisions request to System One and translate the
answer back. The served model name is used in the response.

| Decisions request | System One translation | Decisions answer |
|---|---|---|
| `input` string or user messages | `state`; `input_text` parts joined with two newlines | — |
| `input_image` data URL | Image item in `media` | — |
| `predicate` | `noul` | `{type, name, probability}` |
| `choice` | `choice` with criteria keyed by `str(value)` | `{type, name, choice, probabilities, confidence}` |
| `score` | `score` with ordered levels | `{type, name, score, probabilities, confidence}` |

The request accepts `model`, `input`, `questions`, `safety_identifier`, `metadata` and `store`. The last three are
accepted but do not affect inference. Other top-level parameters and unknown question fields are rejected as
`unsupported_parameter` or invalid request. Messages must have role `user` or omit role. Content may be a string or an
array of `input_text` and `input_image` parts. Images must be base64 `data:` URLs; `detail` is ignored and remote URLs
are not fetched. Empty text with image input is accepted if the underlying model accepts it.

There must be 1–200 questions. Each requires `type` and non-empty `instructions`; `name` is optional and must be
unique when provided. Omitted names appear as JSON `null`. A `choice` needs 2–255 values, each a string or boolean;
values must be unique after converting to strings, and original values (including booleans) are restored in answers.
A `score` needs 2–10 levels. These Decisions limits may exceed basal's own per-prompt-format option limit: prompt format
1 supports 2–10 options and format 2 supports 2–255 (the `ollama` mode limit is 20). The serving format must still
accept the translated question.

Full request and verified response:

```json
{
  "model":"basal",
  "input":[{"role":"user","content":[{"type":"input_text","text":"Customer wrote: I was charged twice for order #4411 and want one charge refunded today."}]}],
  "questions":[
    {"type":"predicate","name":"is_refund_request","instructions":"Is the customer asking for a refund?"},
    {"type":"choice","name":"needs_human","instructions":"Should a human agent take over?","choices":[{"value":true,"description":"billing dispute that needs a person"},{"value":false}]},
    {"type":"score","instructions":"How frustrated is the customer?","levels":[{"label":"calm"},{"label":"mildly annoyed"},{"label":"angry","description":"uses hostile language"}]}
  ]
}
```

```json
{
  "id":"dec_e7849d58…","object":"decision","created":1792523317,"model":"Remek/basal-2.0-mini-MLX-8bit",
  "answers":[
    {"type":"predicate","name":"is_refund_request","probability":0.8828},
    {"type":"choice","name":"needs_human","choice":true,"probabilities":[{"value":true,"probability":0.9199},{"value":false,"probability":0.0801}],"confidence":0.9199},
    {"type":"score","name":null,"score":1.0627,"probabilities":[{"value":0,"label":"calm","probability":0.1620},{"value":1,"label":"mildly annoyed","probability":0.6132},{"value":2,"label":"angry","probability":0.2248}],"confidence":0.6132}
  ],
  "usage":{"input_tokens":395,"input_tokens_details":{"cached_tokens":0},"output_tokens":0,"output_tokens_details":{"reasoning_tokens":0},"total_tokens":395}
}
```

## Client setups

### TypeSafe Python SDK

Install `typesafe-sdk` and import `typesafe_sdk`. The verified run used version 0.7.5:

```bash
python -m pip install typesafe-sdk==0.7.5
export TYPESAFE_BASE_URL=http://127.0.0.1:8765
export TYPESAFE_API_KEY=<secret>
export TYPESAFE_DEFAULT_MODEL=Remek/basal-2.0-mini-MLX-8bit
export NO_PROXY=localhost,127.0.0.1
```

`TypeSafeClient().system_one(...)` returned `SystemOneResponse` with typed Choice/Noul/Score answers; the response
request id matched `x-typesafe-request-id`. `client.models.list()` returned the served model. Wrong keys raised
`TypeSafeAuthenticationError` (401); an invalid one-option choice raised `TypeSafeUnprocessableEntityError` (422)
with the server message and request id. When basal is open, the SDK still requires a non-empty `TYPESAFE_API_KEY`;
use a placeholder such as `local`.

### OpenAI Python SDK

Verified with OpenAI Python SDK 3.35.1 (the Decisions API is available in SDK versions >= 3.26):

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8765/v1", api_key="<secret>")
result = client.decisions.create(
    model="Remek/basal-2.0-mini-MLX-8bit",
    input="Export fails in Safari but works in Chrome.",
    questions=[
        {
            "type": "score",
            "name": "severity",
            "instructions": "How severe is the issue?",
            "levels": [{"label": "minor"}, {"label": "major"}, {"label": "blocking"}],
        },
        {
            "type": "choice",
            "name": "team",
            "instructions": "Which team should investigate?",
            "choices": [{"value": "frontend"}, {"value": "backend"}],
        },
    ],
)
```

This returned score and choice answers; a wrong key raised `openai.AuthenticationError` (401).

### LiteLLM

LiteLLM's self-hosted vLLM System One provider reaches the API server as `hosted_vllm/<served name>` with `api_base` (the
server root or `/v1`; both work). Verified with LiteLLM 1.106.1 against basal-ui's API server with a required key.

Python SDK, both request formats (LiteLLM translates the OpenAI shape to System One for this provider):

```python
import litellm

r = litellm.systemone(
    model="hosted_vllm/basal-2.0-mini",
    api_base="http://127.0.0.1:8000",
    api_key="<secret>",
    state="Customer: charged twice, wants a refund today.",
    questions={"refund": {"type": "noul", "instructions": "Is this a refund request?"}},
)
print(r.answers["refund"].noul)

d = litellm.decisions(
    model="hosted_vllm/basal-2.0-mini",
    api_base="http://127.0.0.1:8000",
    api_key="<secret>",
    input="Customer: charged twice, wants a refund today.",
    questions=[{"type": "predicate", "name": "refund", "instructions": "Is this a refund request?"}],
)
print(d.answers[0].probability)
```

A wrong key surfaces as `litellm.AuthenticationError` carrying the API server's `{"error": ..., "code": "UNAUTHORIZED"}`.

Proxy: the entry below (the same one the basal-ui Connect panel shows) served both `/v1/systemone` and
`/v1/decisions` through `litellm --config config.yaml`. The proxy refuses to start without its own master key
(`LITELLM_MASTER_KEY`); clients then authenticate to the proxy with that key, and the proxy uses `api_key` below
towards basal.

```yaml
model_list:
  - model_name: basal-2.0-mini
    litellm_params:
      model: hosted_vllm/basal-2.0-mini
      api_base: http://127.0.0.1:8000
      api_key: <secret>          # any non-empty value when the API server runs without keys
```

### `basal.client.Basal`

```python
from basal.client import Basal

client = Basal("http://127.0.0.1:8000", api_key="<secret>")
answer = client.choice("A user asks for a refund.", "Which team?", {"billing": "Billing", "support": "Support"})
```

### curl

```bash
curl http://127.0.0.1:8000/v1/systemone -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <secret>' \
  -d '{"state":"Choose a team","questions":{"route":{"type":"choice","instructions":"Which team?","criteria":{"billing":"Billing","support":"Support"}}}}'

curl http://127.0.0.1:8000/v1/decisions -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <secret>' \
  -d '{"input":"Choose a team","questions":[{"type":"choice","instructions":"Which team?","choices":[{"value":"billing"},{"value":"support"}]}]}'
```

### Verified compatibility

The transcript-backed checks used a single-model basal-serve 2.3.0 with basal-2.0-mini MLX 8-bit on Apple M4 Max;
on basal-ui's API server every request also names its `model`. TypeSafe Python
SDK `typesafe-sdk` 0.7.5, OpenAI SDK 3.35.1 and LiteLLM 1.106.1 (SDK and proxy) worked against that server.

| Ecosystem | Matches | Differences and notes |
|---|---|---|
| TypeSafe System One | `/v1/systemone`, Bearer auth, named questions and answers, request-id header | `model` is optional and not validated; basal reports the served name. Basal adds question types, usage fields and calibration metadata. |
| vLLM 0.32 | System One route and vLLM-style `images`/`audio` data URL aliases | Remote media URLs are rejected; basal limits requests to 8 items and 20 MiB each; the model/backend must support media. |
| Ollama / llama.cpp | System One request and answer shape through basal's server integrations | Basal-specific question types and extensions may not be supported by those servers; option limits depend on the selected backend and prompt format. |
| Ollaya | System One question/answer wire shape | Basal-specific types/fields and response usage may differ. |
| OpenAI Decisions | `/v1/decisions` and `/decisions` request/answer translation | Only predicate, choice and score are translated; see limits above. Safety/metadata/store fields are accepted but ignored. Errors use route-specific OpenAI envelopes. |
