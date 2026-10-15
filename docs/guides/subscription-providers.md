# Subscription-backed LLM providers (experimental)

This release introduces a **separate, optional** subscription billing mode
for CHEAP_LLM and SMART_LLM. The first working adapter is **OpenAI Sign in
with ChatGPT (SIWC)** for eligible open-source, locally controlled use.
Legacy API-key providers remain the default and are not changed.

**Not a universal OAuth-to-API gateway.** A Gemini/Claude CLI login is not
authorization to reuse its tokens in another app. The UI lists candidate
providers but intentionally refuses to run unimplemented adapters. The
GitHub Copilot SDK is an independently documented candidate for a later
adapter; its session semantics cannot be represented as a normal API key.

> Experimental: OAuth request and inference are implemented, with mocked
> tests. A real ChatGPT account and provider approval are required for a
> live end-to-end verification. Do not treat a passing CI test as evidence
> that a particular account/region/model is eligible.

## Setup on a computer you control

On the **same computer where your browser runs**, using the source checkout:

1. Install Python dependencies:
   \`\`\`sh
   python -m pip install -r requirements-core.txt
   \`\`\`
2. Start local authorization:
   \`\`\`sh
   PYTHONPATH=src python -m utils.subscription_siwc login
   \`\`\`
   Windows PowerShell:
   \`\`\`powershell
   $env:PYTHONPATH="src"
   python -m utils.subscription_siwc login
   \`\`\`
   The command opens the official OpenAI sign-in page and listens only on
   \`127.0.0.1\` with an ephemeral port under \`/auth/callback\`. The user
   authorizes access to their own eligible ChatGPT plan. The callback is
   checked against a one-time state, PKCE verifier and signed OIDC ID token
   before anything is persisted. Stop and review OpenAI's requested consent
   before accepting.
3. Query the exact available models, after sign-in:
   \`\`\`sh
   PYTHONPATH=src python -m utils.subscription_siwc models
   \`\`\`
   Use a listed **slug**, not a display name; each ChatGPT account may have
   different available models. Do not assume the default \`gpt-4o\` is eligible.
4. In WebUI **配置 → API** choose \`认证 / 计费模式 = 订阅授权\`
   for the required CHEAP_LLM and/or SMART_LLM role. Select the
   \`openai_siwc\` provider and set its model name to an available slug.
   Save the settings and restart the worker. Alternatively set:
   \`\`\`dotenv
   CHEAP_LLM__AUTH_MODE=subscription
   CHEAP_LLM__SUBSCRIPTION_PROVIDER=openai_siwc
   CHEAP_LLM__MODEL_NAME=<a slug from the models command>
   SMART_LLM__AUTH_MODE=subscription
   SMART_LLM__SUBSCRIPTION_PROVIDER=openai_siwc
   SMART_LLM__MODEL_NAME=<a slug from the models command>
   \`\`\`
   Each role is independent: either may stay on \`api_key\`.
5. Run an ordinary workflow. The worker uses the official public
   \`POST https://api.openai.com/v1/responses\` route, \`store=false\`,
   \`stream=true\`, user/developer context, and consumes the full stream
   through \`response.completed\`. It never sends \`temperature\` on this
   route. The OpenAI OAuth token is **not** an OpenAI API key and is never
   forwarded to custom \`BASE_URL\` hosts.

The WebUI subscription status checks **local credential presence and granted
permissions only**. The ordinary test button does not claim to validate a
live model request in this mode.

## Credentials, accounts, and disconnect

- Credentials are stored at \`runtime/subscriptions/openai.json\`, atomically
  replaced with mode \`0600\` inside a \`0700\` directory (on Unix).
  \`runtime/\` is excluded from Git. **Do not put this file in emails, bug
  reports, issue comments, Docker images, GitHub secrets or normal backups.**
- The stable host identifier is stored separately in
  \`runtime/subscriptions/host.json\`; it must not be copied from a laptop to
  an already-identified VM.
- On expiry, the worker refreshes tokens via the official OpenAI token
  endpoint; refresh is serialized across worker/CLI processes to prevent
  concurrent use of rotating refresh tokens. Confirmed invalid grants
  invalidate local tokens; temporary provider outages preserve credentials.
- Available local management commands:
  \`\`\`sh
  PYTHONPATH=src python -m utils.subscription_siwc status
  PYTHONPATH=src python -m utils.subscription_siwc login --new-account
  PYTHONPATH=src python -m utils.subscription_siwc select <issued-client-id>
  PYTHONPATH=src python -m utils.subscription_siwc disconnect
  \`\`\`
  Reauthorization of the selected registration reuses its issued client ID.
  A new account gets an independent ID/client mapping.
- The \`disconnect\` command attempts remote revocation, then clears active
  local tokens. If revocation cannot be confirmed, disconnect the app in
  ChatGPT Settings as well. Credentials are never returned by WebUI endpoints.

## Self-hosted VPS / Docker

The browser's \`127.0.0.1\` callback goes to **the browser's computer**,
not a remote server or arbitrary Docker container. Therefore, do not expose
the OAuth callback on a public IP or create a reverse-proxy route for it.

1. On the remote machine, create/preserve its own host ID via the container
   worker after upgrading:
   \`\`\`sh
   docker exec -u 1000:1000 -e PYTHONPATH=/app/src arxiv-daily-researcher python -c 'from utils.subscription_state import host_id; print(host_id())'
   \`\`\`
   Replace `1000:1000` with the actual configured Docker `PUID:PGID`.
2. Authorize the **same application and ChatGPT identity** locally, then
   transfer only \`runtime/subscriptions/openai.json\` over a secure
   authenticated channel such as SSH/SCP, to the VM's corresponding
   \`runtime/subscriptions/\` directory.
3. Preserve the remote VM's existing \`host.json\`. Ensure owner-only
   permissions and correct Docker PUID/PGID ownership.
4. Restart the worker after selecting subscription mode in the remote WebUI.
   Refresh occurs on the self-hosted VM, with the user's consent.

OpenAI's current documentation notes limitations for host-specific usage
attribution and revocation after moving credentials between machines.

**Unsupported deployment**: GitHub-hosted Actions runners do not constitute
the long-lived, owner-controlled storage assumed by this integration. The
existing API-key CI/Actions configuration remains the recommended path there.

## Policy and operational constraints

- Use only supported, officially documented OAuth scopes and endpoints.
  Authorization does not transfer ChatGPT conversations to ADR.
- ChatGPT plan access, regional permission, models and usage limits are
  determined by OpenAI. The app **never silently falls back to separately
  billed API usage** on auth or quota failure.
- When app quota is exhausted, stop requesting new subscription inferences.
  Use the account's ChatGPT Settings → Usage to check limits. Do not assume a
  numeric reset time from an error code alone.
- User-controlled local/self-hosted, **single operator** is the supported
  operational topology in this phase. Do not share one subscription grant
  across unrelated WebUI accounts, service tenants, or a hosted SaaS.
- Avoid logging request bodies or bearer tokens. Refrain from exposing
  \`runtime/subscriptions/\` via file browser or backup/export features.
- Token counts shown in existing diagnostics are **not a monetary balance**,
  and do not indicate subscription quota remaining.
- OpenAI may change the preview requirements. Recheck official documentation
  before enabling new endpoint parameters or models.

## Follow-up implementation steps

1. **Phase 1 (this PR):** officially supported OpenAI SIWC local OAuth, secure
   credential persistence/refresh, Responses inference adapter and config UI.
2. **Phase 2:** account-aware WebUI authorization UX, model picker with live
   per-account availability, better per-provider usage accounting and
   single-/multi-operator access control review.
3. **Phase 3:** GitHub Copilot SDK adapter after independent license,
   headless-runtime, OAuth and session testing. Keep unsupported Google and
   Anthropic consumer auth flows unavailable until official approval exists.

## Primary documentation

- [OpenAI OSS SIWC overview](https://developers.openai.com/siwc/token-sharing-open-source)
- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Accounts and refresh](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Self-hosted VMs](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [GitHub Copilot SDK authentication](https://docs.github.com/en/copilot/how-tos/copilot-sdk/auth/authenticate)
