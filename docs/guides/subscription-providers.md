# ChatGPT subscription (official Sign in with ChatGPT, experimental)

This guide covers **only ChatGPT plan-backed inference** for eligible self-hosted
ArXiv Daily Researcher (ADR) installations. No other consumer subscription
login mechanisms are integrated.

API keys remain the default. When enabled explicitly per CHEAP_LLM or
SMART_LLM role, ChatGPT SIWC uses the user's own authorized subscription
allowance; no OpenAI API key is consumed or billed for those requests.

> Experimental until a user completes a real account authorization and
> consented inference check. Passing the mocked CI tests does not establish
> that your plan, workspace, country or requested model is eligible.

## Supported setups and authentication choices

| Environment | Sign-in flow | Status |
| --- | --- | --- |
| Locally installed Python application | Built-in CLI loopback OAuth | Supported |
| Local native WebUI on `127.0.0.1` | Optional **Continue with ChatGPT** button | Supported with explicit opt-in and owner authentication |
| Self-hosted remote Docker / VPS | Sign in on the browser's computer, transfer protected credential session over SSH | Supported with provider's known limitations |
| GitHub-hosted Actions runner or shared SaaS | No persistent owner-controlled credential session | Not supported |

The OAuth callback address is deliberately always an HTTP
`127.0.0.1:<port>/auth/callback` URL. The browser computer and callback
listener must be the **same** computer. Do not expose that callback through
a public IP or remote reverse proxy.

## A. Local CLI sign-in (also used for remote Docker)

From a source checkout on the computer running your browser:

```sh
python -m pip install -r requirements-core.txt
PYTHONPATH=src python -m utils.subscription_siwc login
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m utils.subscription_siwc login
```

The helper opens OpenAI's real consent screen, starts a temporary
`127.0.0.1` listener, validates State/PKCE/nonce and verifies the signed
ID token. Credentials are saved only after identity validation.
The authorization URL contains no access or refresh token.

The authorization request asks for `chatgpt.tokens.use.direct`; if this
permission is declined, a successful identity login **does not enable**
subscription inference. A new, user-initiated sign-in with the saved registration
requests `prompt=consent` for this declined grant, without prompting on every
normal sign-in.

List the selected account's currently available models (live catalog request,
not a text-generation call):

```sh
PYTHONPATH=src python -m utils.subscription_siwc status
PYTHONPATH=src python -m utils.subscription_siwc models
```

To exercise actual entitlement, perform **one** intentionally
quota-consuming test with an available `slug`:

```sh
PYTHONPATH=src python -m utils.subscription_siwc smoke --model <visible-model-slug>
```

The command must receive a completed SSE response, not only a partial stream.

## B. Optional direct sign-in from a native localhost WebUI

For a **local source-code install, not remote Docker**, with WebUI account
authentication enabled, start the WebUI on the browser computer using:

```sh
ADR_SIWC_LOCAL_WEBUI_ENABLED=true PYTHONPATH=src \
  uvicorn modern_webui.app:app --host 127.0.0.1 --port 8501
```

Open `http://127.0.0.1:8501`, sign in as the **WebUI owner**, go to
**配置 → API → ChatGPT 订阅授权**, and choose
**Continue with ChatGPT（本机）**. Use **添加 ChatGPT 账户** to add a separate
registration. The button is disabled unless all requirements are satisfied:
explicit environment opt-in, authenticated owner, localhost request and
direct, non-proxied HTTP connection.

The start endpoint creates a one-time PKCE authorization attempt in server
memory. The local WebUI accepts the returning `/auth/callback` only with a
matching, unconsumed State. It does not return tokens in browser JSON,
URL fragments or settings exports. If the WebUI restarts during consent,
restart the attempt. Cross-site callbacks do not create or grant a WebUI
login session.

After authorization, the owner can select the active account, refresh the
available model list and disconnect the active authorization. A connected
account does not imply that every model is available.

Remote Docker installations **must not** enable the local WebUI login option:
the browser's `127.0.0.1` callback cannot reach the remote container.

## C. Select LLM billing mode and model

In WebUI **配置 → API**, choose the following independently for the low-cost
(CHEAP_LLM) and high-performance (SMART_LLM) model roles:

- **API Key** — unchanged, the default.
- **订阅授权** — official ChatGPT SIWC, authorized by the selected account.

Use the account-aware **订阅可用模型** dropdown for each role. It displays
the user's actual `display_name` but saves the provider's `slug` as
`MODEL_NAME`. Save configuration and restart the worker after switching
accounts/models or billing mode.

Equivalent optional environment values:

```dotenv
CHEAP_LLM__AUTH_MODE=subscription
CHEAP_LLM__SUBSCRIPTION_PROVIDER=openai_siwc
CHEAP_LLM__MODEL_NAME=<visible-model-slug>
SMART_LLM__AUTH_MODE=subscription
SMART_LLM__SUBSCRIPTION_PROVIDER=openai_siwc
SMART_LLM__MODEL_NAME=<visible-model-slug>
```

Either role may stay on API-key billing. There is **no automatic fallback**
to paid API requests on subscription expiration, policy failure or quota exhaustion.

The WebUI connection test checks the selected ChatGPT model's availability
in the authenticated model catalog; it is **not** a paid or quota-consuming
inference test. The CLI `smoke` command is the explicit live test.

## D. Remote Docker / VPS deployment

The browser and its `127.0.0.1` callback must remain local. Complete step A
on a machine you control, then copy only the already protected credential
record to the VM over an authenticated encrypted channel.

1. On the VM, create **its own** persistent host identifier before importing
   credentials (example container name from the standard Compose deployment):

   ```sh
   docker exec -u 1000:1000 -e PYTHONPATH=/app/src \
     arxiv-daily-researcher python -c \
     'from utils.subscription_state import host_id; print(host_id())'
   ```

   Replace `1000:1000` with your configured Docker `PUID:PGID`.
2. Transfer your local `runtime/subscriptions/openai.json` using SSH/SCP
   into the VM's **own** `runtime/subscriptions/openai.json` (a mounted
   directory shared by Worker and WebUI in the standard Compose setup).
3. Retain the VM's own `host.json`; **do not** overwrite it with the
   laptop's host identifier. Require directory mode `0700` and file mode
   `0600`, owned by the mapped runtime user. Do not commit these files,
   attach them to GitHub issues, or put them in your usual backup/export.
4. In the remote WebUI, use owner authentication to select a model, enable
   subscription mode, save settings and restart the worker.

OpenAI documents that transferred sessions currently have limitations for
host-specific attribution and revocation. The VM refreshes the imported
credentials thereafter. Multi-tenant sharing is not supported.

## Local credentials and account lifecycle

ADR stores the current and past issued `oaiapp_...` account registrations
under `runtime/subscriptions/openai.json`, with an independent persistent
host ID in `runtime/subscriptions/host.json`. Files are atomically replaced
with mode `0600` under a `0700` directory on Unix. The directory is
excluded by the repository's `.gitignore`.

Access tokens are short-lived. The worker refreshes using the newest rotating
refresh token and serializes refreshes across processes; account switching
does not change the identity pinned by an already-running research task.
Confirmed terminal grant failures require sign-in again; temporary failures
retain usable credentials.

To manage accounts via CLI:

```sh
PYTHONPATH=src python -m utils.subscription_siwc login --new-account
PYTHONPATH=src python -m utils.subscription_siwc select <issued-client-id>
PYTHONPATH=src python -m utils.subscription_siwc disconnect
```

Disconnect attempts revocation at the endpoint advertised in OpenAI's
OpenID Connect discovery document, then always clears the selected local
tokens. If remote revocation cannot be confirmed, also disconnect the app
in ChatGPT Settings.

## Quota, errors and troubleshooting

- ChatGPT plan availability, eligibility and model catalog are determined by
  OpenAI, not ADR. App quotas are shared as documented for your subscription.
- A `subscription_sharing_usage_limit_exceeded` response stops further
  subscription requests for the same registered account in the current worker
  process. Check **ChatGPT Settings → Usage**; do not guess the reset time.
- `subscription_sharing_usage_unavailable` is a temporary availability
  failure, not evidence of an exhausted plan. Bounded retries may apply.
- `401/403` may mean invalid auth, insufficient scope, ineligible user,
  region restriction or unsupported route, and should not silently use the
  paid API path.
- Worker token counts are diagnostic and **not** a remaining subscription
  credit balance.
- An expired/revoked login needs reauthorization. If the WebUI's
  unauthenticated mode is active, personal subscription operations are
  intentionally blocked; use the CLI or enable owner authentication.

## Manual acceptance before enabling production use

Verify all five on a self-hosted machine and **never share the tokens**:

1. Initial and returning ChatGPT sign-in and scope acceptance.
2. A nonempty model catalog for the selected ChatGPT account.
3. A real `smoke` request with `response.completed`.
4. Worker restart plus token refresh after access-token expiry.
5. Safe disconnect and local credential invalidation.

CI mocks the login and requests; it cannot substitute for these checks.

## Official documentation

- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts, refresh and disconnect](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Self-hosted VM transfer](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
