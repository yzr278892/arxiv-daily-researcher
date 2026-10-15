# RFC: subscription-backed LLM provider authentication

**Status:** research and design only — no implementation or implied provider support
**Scope:** self-hosted ArXiv Daily Researcher (ADR), worker and management WebUI
**Origin:** community feedback under [Issue #1](https://github.com/yzr278892/arxiv-daily-researcher/issues/1)
**Last reviewed:** October 2026

## Summary

ADR currently configures independent CHEAP_LLM and SMART_LLM instances
through OpenAI-compatible endpoints and API keys. Explore a separate,
opt-in **subscription authentication** provider category to accommodate
officially delegated consumer subscription usage *when explicitly
permitted by the provider*. Do not treat OAuth login, a CLI session,
an API key, and subscription-billed inference as interchangeable.

This PR intentionally adds no login button, token handler, reverse proxy,
undocumented endpoint, or production credential storage. A later implementation
PR must satisfy the gate below and deliver a tested vertical slice.

## Core distinction

1. **Identity login:** OAuth/OpenID authenticates a user. This by itself says
   nothing about whether a third-party application can bill inference against
   an existing subscription.
2. **Developer API access:** a normal API key generally bills to a developer
   account, independently of a consumer app subscription.
3. **Subscription-backed inference delegation:** a provider-specific, officially
   supported grant and inference interface must expressly permit the intended
   third-party application and automated workload. A login token is not a
   drop-in replacement for an OpenAI-compatible API key.
4. **CLI or agent SDK:** use only when its documentation and terms explicitly
   permit embedding this particular service, not by extracting private auth
   state from a command-line tool.

## Provider research matrix

| Provider/ecosystem | Public integration lead | Subscription-backed access in arbitrary ADR app? | Next action |
| --- | --- | --- | --- |
| **OpenAI / ChatGPT** | Official **Sign in with ChatGPT for open-source apps** documentation provides an opt-in delegated auth and restricted inference path | **Candidate — documented for eligible open-source apps**; grants, model list, request format, quota and eligible deployment types must be checked against current official docs | First proof of concept on a locally controlled host; check automatic daily workflows |
| **GitHub Copilot** | Public GitHub Copilot SDK and CLI/GitHub authorization patterns | **Candidate with conditions**: confirm GitHub's SDK license, billing/plan requirements, model policy and headless usage; SDK operations are not necessarily ordinary OpenAI-compatible inference | Test SDK as an isolated, consented integration; verify unattended job eligibility |
| **Google Gemini / Gemini CLI** | Gemini CLI supports Google sign-in for its own workflow; Google Gemini APIs have independent key / usage arrangements | **Not approved by this fact alone**. Logging into Gemini CLI does not authorize exporting or reusing its tokens in ADR. Google provider credentials and eligible official embedding mechanisms require separate review | Use API-key mode until an explicitly permitted third-party subscription access route is documented |
| **Anthropic / Claude Pro, Max, Claude Code** | Claude Code login and Claude Agent SDK documentation | **Not approved by this fact alone**. Claude Code subscription and session tokens should not be extracted or injected into other apps; review the latest official SDK terms for each exact workflow | Retain Anthropic API billing; do not implement Claude login-token replay |
| **Other vendors** (Microsoft, Alibaba/Qwen, Zhipu, etc.) | Official sign-in, IDE, SDK or model API products | **Unverified**; subscription entitlement is not proven by a named OAuth or chatbot product | Require provider-by-provider documentation, technical and legal review before enabling |

**Important:** “Supported by an open-source client” is not proof of authorization.
A community-developed OAuth plugin may rely on undocumented endpoints or
credentials issued only to a first-party CLI.

### Relevant official sources to re-check at implementation time

- OpenAI [Sign in with ChatGPT integration guide](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)
  and [SIWC limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
- OpenAI [SIWC authentication flow](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
  and [inference requirements](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).
- GitHub [Copilot SDK](https://github.com/github/copilot-sdk) and
  [Copilot documentation](https://docs.github.com/en/copilot).
- Google [Gemini CLI](https://github.com/google-gemini/gemini-cli) and
  [Gemini API documentation](https://ai.google.dev/gemini-api/docs).
- Anthropic [Claude Code documentation](https://code.claude.com/docs/en/overview),
  [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview),
  and [legal/compliance documentation](https://code.claude.com/docs/en/legal-and-compliance).

## Reference open-source projects (architectural patterns, not credential reuse)

| Project | Pattern worth examining | What ADR must NOT assume |
| --- | --- | --- |
| [OpenCode](https://github.com/anomalyco/opencode) | Provider-specific authentication plugins and model routing, with a shared generation interface | Presence of a community auth plugin implies provider permission |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Interactive authorization, callback and token refresh in a dedicated first-party CLI | CLI refresh tokens can be copied into unrelated products |
| [GitHub Copilot SDK](https://github.com/github/copilot-sdk) | Official SDK boundary, capability discovery, and tool-specific session semantics | An SDK is an unrestricted chat-completions endpoint |
| [Continue](https://github.com/continuedev/continue) | Separation between configurable model providers and editor agents | Every provider's consumer plan is an interchangeable subscription |
| [Cline](https://github.com/cline/cline) | Provider configuration UI, explicit credentials and quota/error feedback | Browser-session scraping is an acceptable auth flow |

## ADR design proposal

Keep legacy API-key providers unchanged, and add a separate,
**explicitly selectable** authentication mode per cheap/smart role:

```text
LLM role (CHEAP_LLM / SMART_LLM)
    |
    +-- api_key                  [existing OpenAI-compatible providers]
    +-- provider_subscription   [future, opt-in and provider-allowlisted]
           |
           +-- ProviderAdapter (auth + models + inference + quota/errors)
           +-- CredentialStore  (encrypted/OS-backed, local owner-controlled)
           +-- ConsentManager   (connect/revoke/reauthorize/status)
           +-- ModelCapability  (request shape, reasoning, structured output)
```

Suggested interface (design sketch; not a committed API):

```python
class SubscriptionProviderAdapter(Protocol):
    provider_id: str
    def authorization_capabilities(self) -> AuthorizationInfo: ...
    def begin_authorization(self) -> AuthorizationAttempt: ...
    def complete_authorization(self, callback: AuthorizedCallback) -> Identity: ...
    def refresh_if_needed(self) -> None: ...
    def list_models(self) -> list[ModelDescriptor]: ...
    def generate(self, request: GenerationRequest) -> GenerationResult: ...
    def disconnect(self) -> None: ...
```

The agent layer should consume a provider-neutral generation result with
text, structured output, token usage, finish status and retry classification.
Some subscription SDKs may expose *agent sessions*, rather than plain LLM
inference: do not silently force them into the standard request interface.
Treat such integrations as unsupported until semantics are clear.

### Required properties

1. **Explicit opt-in per model role.** No automatic switch from API billing to
   subscription usage. Show the provider identity, selected model and quota
   implications before use.
2. **Strict allowlist.** Enable a subscription mode only after verifying the
   exact grant type, endpoint, scopes, eligible applications and unattended usage
   in authoritative provider documentation.
3. **Security.** Prefer system keychain / secure owner-controlled storage;
   store refresh tokens encrypted at rest, with limited filesystem permissions.
   Never expose them to WebUI JSON, logs, telemetry, backups or GitHub artifacts.
   Use PKCE, state validation, strict redirect URIs, token rotation/revocation
   and safe concurrent refresh. Do not collect passwords, cookie jars or
   copy first-party app tokens.
4. **Self-hosted topology.** Browser loopback redirects require special care in
   Docker or remote VPS setups. Separate a local authorization helper from
   remote workers; do not assume a host browser redirect reaches a container.
   Verify provider approval for headless jobs before enabling cron.
5. **Provider-specific inference.** Keep current Chat Completions / Responses
   capability discovery for API-key providers. Subscription adapters must
   enforce their own model, endpoint, streaming, retention and parameter
   requirements rather than reusing an arbitrary base_url.
6. **Safe failure modes.** Explicitly label revoked grants, expired credentials,
   throttling, subscription limits and unsupported deployments. Do not
   automatically fail over to a paid API key without user consent.
7. **Multi-account boundaries.** Never pool subscription entitlements among
   WebUI users. Bind credentials to their owner and require authorization
   checks for connect/test/disconnect.
8. **Accounting.** Surface subscription usage separately from API-billed tokens;
   token counts are not equivalent to subscription balance or charges.

## Rollout

**Phase 0 — this PR:** research and design, no runtime changes.

**Phase 1 — proof of concept:** one officially documented provider (likely
OpenAI SIWC), locally controlled machine only; authorization, one generation,
token refresh and revoke. Record provider documentation/version and supported
model names. Test via mocks and live opt-in manual credentials.

**Phase 2 — integration:** isolate adapter and persist secrets securely;
wire through scoring, translation, keyword and trend agents; surface state in
WebUI; handle structured outputs, quotas, retries, observability and batch jobs.

**Phase 3 — additional providers:** admit each provider only after the same
compliance and integration gate. Do not claim cross-provider subscription
support until validated end to end.

## Acceptance criteria for a future implementation PR

- Current API-key deployment, WebUI settings and GitHub Actions remain fully
  backward compatible; no new required environment variables.
- Explicit consent, supported auth flow and owner-controlled credential
  lifecycle; no scraping, token replay or shared subscription accounts.
- Provider-specific endpoint/model/parameter tests, including structured JSON,
  streaming, token accounting, timeout/retry and refresh races.
- Clear treatment of unattended execution and remote Docker deployment.
- A clean disconnect path, automatic invalid-token detection and unambiguous
  billing-mode display.
- Tests cover all three agent call sites and retain existing regression suite.
- Documentation precisely names supported subscription plans, limitations and
  provider policies, with a review date.

## Open questions

1. Are all ADR recurring tasks permitted under each provider's third-party
   subscription usage terms, or only direct interactive calls?
2. How should a remote Docker host authorize without taking ownership of an
   end-user credential outside the approved flow?
3. Does any authorized provider SDK support the current JSON contracts and
   inference accounting for scoring/translation?
4. Should one WebUI account be allowed multiple independently licensed
   subscription identities, and how will quota exhaustion be reported?
5. What is the supported migration if a provider withdraws a preview feature?

**Decision requested:** accept the opt-in adapter architecture and provider
eligibility gate; do not authorize an implementation or market generalized
subscription support merely by merging this RFC.
