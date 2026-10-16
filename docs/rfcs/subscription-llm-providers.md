# RFC: ChatGPT plan-backed LLM provider (SIWC)

**Status:** Experimental implementation in PR #13 (draft pending a consented live-account smoke test)
**Scope:** ChatGPT plan usage in self-hosted ArXiv Daily Researcher only
**Related:** [Issue #1](https://github.com/yzr278892/arxiv-daily-researcher/issues/1);
[PR #12 — generation parameter compatibility](https://github.com/yzr278892/arxiv-daily-researcher/pull/12)
**Reviewed:** October 2026

## Decision and limits

ADR supports two independent billing/authentication modes for each of its
CHEAP_LLM and SMART_LLM roles:

- **API Key** (default): retains the existing OpenAI-compatible model/provider
  configuration, endpoints, temperature settings and independently billed API
  usage. Existing installations upgrade without new required variables.
- **ChatGPT subscription** (opt-in): uses the official OpenAI Sign in with
  ChatGPT (SIWC) authorization and the user's eligible subscription allowance.
  It never forwards the OAuth token to a custom API base URL or switches
  silently to API-key billing. Only the \`openai_siwc\` identifier is accepted.

**Other subscription providers are explicitly out of scope.** This PR does
not include Copilot, Gemini, Claude, or credential extraction from proprietary
CLIs. Any later provider requires a new, independently approved proposal.

## Architecture (implemented)

\`\`\`text
CHEAP_LLM / SMART_LLM (auth_mode)
   |-- api_key          -> existing OpenAI-compatible SDK calls
   '-- subscription     -> OpenAI SIWC adapter only
                            |
                            +-- Local owner-initiated OAuth (CLI or local-only WebUI)
                            +-- Per-issued-client account registry
                            +-- Protected local credential store (0600/0700)
                            +-- Exclusive refresh-token rotation lock
                            +-- Account model catalog via GET /v1/models
                            '-- Restricted POST /v1/responses with SSE stream

Existing AnalysisAgent / KeywordAgent / TrendAgent
               -> shared LLMRequestPool
               -> SIWC chat-shaped adapter
               -> normalized content, JSON and usage
\`\`\`

The ChatGPT route uses \`https://auth.openai.com\` only for authorization and
\`https://api.openai.com/v1\` for inference and account-specific model listings.

## OAuth and credential lifecycle

1. An owner chooses a saved issued registration or adds a new one. The
   authorization request generates fresh random state, nonce and PKCE-S256
   verifier, with \`ext_agent_host_id\`, official scopes and exact loopback URI.
2. Initial registration starts with \`dynamic_agent_client\`; the callback's
   \`oaiapp_...\` client ID is used in the authorization-code exchange. Returning
   sign-in retains the issued client ID and verified identity.
3. Verify signed OIDC ID tokens using OpenAI JWKS, issuer, audience, nonce and
   lifetime. Keep an account registration separate even if emails match.
4. Check the returned \`chatgpt.tokens.use.direct\` scope: identity login alone
   is **not** permission to use a ChatGPT plan for inference.
5. Persist credentials atomically in the owner-controlled runtime directory;
   rotate refresh tokens under a cross-process lock; preserve tokens on a
   temporary upstream failure; invalidate confirmed terminal grants.
6. Use OIDC discovery for refresh-token revocation. Disconnect always clears
   local tokens and identifies when remote revocation could not be confirmed.

A worker instance pins the selected issued client ID on its first inference.
Changing the active account in the WebUI cannot redirect an already running
worker's later inferences to another person's subscription.

## Authorization UI topology

The **standalone CLI** supports local loopback authorization and is the
supported path for remote VPS/Docker deployment (via secure, owner-controlled
credential transfer). A direct WebUI "Continue with ChatGPT" action is available
only when all of these are true:

- The owner explicitly set \`ADR_SIWC_LOCAL_WEBUI_ENABLED=true\`.
- The WebUI authentication feature is enabled and the session is its owner.
- Both browser request and WebUI host use direct HTTP \`127.0.0.1\`;
  reverse-proxy headers, LAN addresses and remote Docker bridges are rejected.

The WebUI holds pending PKCE state only in memory, consumes it once on callback
and never returns access/refresh/ID tokens. The cross-site callback does not
depend on the SameSite=Strict cookie; it requires the unguessable owner-started
state and immediately redirects to the UI without OAuth query parameters.

The WebUI also provides a protected account switcher, optional disconnect,
owner-only model listing and a model selector for each LLM role. User must
save and restart the worker after changing configured model or auth mode.

## Restricted inference

- Send only an available model slug from the selected ChatGPT account.
- Use the public Responses endpoint, \`input\` as an array,
  \`store=False\`, \`stream=True\`; consume until \`response.completed\`.
- Include required system/developer instructions through \`instructions\`.
- Never include unsupported parameters such as \`temperature\`, \`user\`,
  \`background\`, \`max_output_tokens\` or \`previous_response_id\`.
- Map existing JSON-output requests into Responses \`text.format\` and return
  a chat-shaped result so all existing Agents keep their contracts.
- A structured subscription-limit error stops inference without retrying or
  falling back to paid API calls. A process-local circuit breaker stops later
  requests for the same account in the current worker process.
- Distinguish quota/eligibility/policy errors from temporary routing failures.
  Token accounting is diagnostic only and does not imply remaining monetary
  balance or subscription quota.

## Security and compatibility

- Never store OAuth tokens in \`.env\`, GitHub Actions secrets, WebUI JSON,
  diagnostic logs, source control, container images, backups or URLs.
- Only a direct local owner or the explicitly authorized self-hosted worker
  may touch credential storage.
- Credentials transferred to a VM must be protected via SSH, preserving the
  destination VM host ID. Shared hosted runners and multi-tenant SaaS use are
  not supported.
- The legacy API key path remains the default and is not replaced.
- A different model, account or billing mode must invalidate relevant cached
  generation-stage results; protected credential values must never appear in
  audit metadata.

## Tests and acceptance

Automated, secret-free CI exercises:

1. PKCE authorization URL, State/client-ID validation, OIDC identity handling
   through mocked cryptographic verifier, one-time local WebUI callback and
   owner/local-only access guard.
2. Owner-protected file permissions, refresh-token rotation, account
   separation, selection, remote revocation discovery and quota stops.
3. Model availability filtering, requested ChatGPT-only mode, stream
   completion/failure, forbidden sampling-field omission and agent dispatch.
4. Existing Python regression suite, WebUI syntax and Chromium smoke, plus
   both Docker target builds/entrypoints.

**The remaining human acceptance gate** is real OpenAI consent on an eligible
ChatGPT account, model listing, a live streamed request, restart/refresh and
optional revoke on a local or self-hosted machine. A successful mocked CI run
is not proof of real plan eligibility.

Run \`PYTHONPATH=src python -m utils.subscription_siwc smoke --model <slug>\`
after login, and check the account's ChatGPT Settings → Usage. Do not
post credentials, callback codes or account-specific tokens in review comments.

Detailed steps: [ChatGPT subscription operator guide](../guides/subscription-providers.md).

## Official sources

- [OpenAI registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [OpenAI account and token lifecycle](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [OpenAI model listing and streaming](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [OpenAI errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
- [OpenAI preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [OpenAI self-hosted VM guidance](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)
