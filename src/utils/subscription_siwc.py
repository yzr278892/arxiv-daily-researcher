"""OpenAI SIWC public-client OAuth and subscription-only Responses adapter.

No browser token interception, CLI-cookie reuse, or fallback to paid API billing.
All inference uses the official public Responses endpoint with store=False,
stream=True and explicit completion verification.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
from types import SimpleNamespace
import time
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

import requests

from utils.subscription_state import (
    SubscriptionError,
    SubscriptionLimitError,
    SubscriptionTransientError,
    host_id,
    locked_store,
    public_status,
    read_accounts,
    selected_account,
    write_accounts,
)

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
REVOKE_URL = ISSUER + "/api/accounts/oauth/revoke"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
AGENT_NAME = "ArXiv Daily Researcher"
LIMIT_CODES = {"subscription_sharing_usage_limit_exceeded"}
FATAL_CODES = {
    "subscription_sharing_user_not_eligible",
    "subscription_sharing_route_not_supported",
    "subscription_sharing_invalid_user",
    "subscription_sharing_unsupported_capability",
    "chatpass_v2_scope_not_authorized",
    "chatpass_v2_invalid_authorization_context",
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_authorize_url(*, client_id: str, host: str, callback: str,
                        state: str, nonce: str, challenge: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": callback,
        "response_type": "code",
        "scope": SCOPES,
        "resource": RESOURCE,
        "ext_agent_host_id": host,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    if client_id == "dynamic_agent_client":
        params["agent_name_hint"] = AGENT_NAME
    return AUTHORIZE_URL + "?" + urlencode(params)


def validate_callback(query: dict, *, expected_state: str,
                      expected_client_id: str) -> tuple[str, str]:
    """Fail closed on state mismatch, unexpected client or cancelled consent."""
    state = str(query.get("state", ""))
    if not state or not secrets.compare_digest(state, expected_state):
        raise SubscriptionError("OAuth callback state mismatch.")
    if query.get("error"):
        raise SubscriptionError("ChatGPT authorization was declined or unavailable.")
    code = query.get("code")
    if not isinstance(code, str) or not code:
        raise SubscriptionError("OAuth callback is missing authorization code.")
    returned = str(query.get("client_id", ""))
    if expected_client_id == "dynamic_agent_client":
        if not returned.startswith("oaiapp_"):
            raise SubscriptionError("Dynamic OAuth registration did not return an issued client ID.")
        return code, returned
    if returned and returned != expected_client_id:
        raise SubscriptionError("OAuth client ID changed during reauthorization.")
    return code, expected_client_id


def _post_form(url: str, payload: dict) -> dict:
    try:
        response = requests.post(
            url, data=payload, timeout=30, allow_redirects=False
        )
    except requests.RequestException as exc:
        raise SubscriptionTransientError("OpenAI OAuth endpoint is unavailable.") from exc
    if response.status_code != 200:
        # Do not include response.text: providers might echo request secrets.
        error_code = ""
        try:
            parsed = response.json()
            raw = parsed.get("error", "")
            error_code = raw if isinstance(raw, str) else raw.get("code", "")
        except (ValueError, AttributeError):
            pass
        if url == TOKEN_URL and error_code in {
            "invalid_grant", "invalid_refresh_token", "refresh_token_expired",
            "refresh_token_invalidated", "refresh_token_reused",
        }:
            raise SubscriptionError("ChatGPT authorization expired; sign in again.")
        if response.status_code >= 500:
            raise SubscriptionTransientError(f"OpenAI authorization unavailable (HTTP {response.status_code}).")
        raise SubscriptionError(f"OpenAI authorization denied (HTTP {response.status_code}, code={str(error_code)[:64]}).")
    try:
        body = response.json()
    except ValueError as exc:
        raise SubscriptionError("OpenAI returned an invalid token response.") from exc
    if not isinstance(body, dict):
        raise SubscriptionError("OpenAI returned an invalid token response.")
    return body


def _validate_id_token(raw: str, *, client_id: str, nonce: str | None) -> dict:
    """Verify signature, issuer, audience, lifetime and (for sign-in) nonce."""
    try:
        import jwt

        jwks = jwt.PyJWKClient(JWKS_URL)
        signing_key = jwks.get_signing_key_from_jwt(raw)
        claims = jwt.decode(
            raw, signing_key.key, algorithms=["RS256"],
            issuer=ISSUER, audience=client_id,
            options={"require": ["iss", "aud", "exp", "iat", "sub"]},
            leeway=5,
        )
    except Exception as exc:
        raise SubscriptionError("OpenAI ID-token validation failed.") from exc
    if nonce is not None and (
        not isinstance(claims.get("nonce"), str)
        or not secrets.compare_digest(claims["nonce"], nonce)
    ):
        raise SubscriptionError("OpenAI identity nonce mismatch.")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise SubscriptionError("OpenAI identity has no valid subject.")
    return claims


def _make_record(body: dict, client_id: str, claims: dict, *, previous: dict | None = None) -> dict:
    previous = previous or {}
    token = body.get("access_token")
    refresh = body.get("refresh_token")
    if not isinstance(token, str) or not token or not isinstance(refresh, str) or not refresh:
        raise SubscriptionError("Token response missing access or replacement refresh token.")
    if body.get("token_type", "Bearer").lower() != "bearer":
        raise SubscriptionError("Unsupported OAuth token type.")
    scopes = body.get("scope")
    if isinstance(scopes, str):
        scopes = scopes.split()
    elif not isinstance(scopes, list):
        scopes = previous.get("scopes", [])
    if not isinstance(scopes, list):
        raise SubscriptionError("Invalid OAuth scope response.")
    expires = int(body.get("expires_in", 3600))
    if not 0 < expires <= 86400:
        raise SubscriptionError("Invalid OAuth token lifetime.")
    return {
        "client_id": client_id,
        "subject": claims["sub"],
        "issuer": ISSUER,
        "email": claims.get("email") or previous.get("email") or "",
        "id_token": body.get("id_token") or previous.get("id_token") or "",
        "access_token": token,
        "refresh_token": refresh,
        "scopes": scopes,
        "saved_at": time.time(),
        "expires_at": time.time() + expires,
        "earliest_refresh_at": body.get("earliest_refresh_at") or 0,
    }


def _receive_callback(server: HTTPServer, *, state: str) -> dict:
    """Serve one loopback redirect; callback content never enters the browser."""
    output: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # authorization codes and state MUST NOT reach access logs

        def do_GET(self):
            host = self.headers.get("Host", "")
            if host != f"127.0.0.1:{server.server_port}" or urlparse(self.path).path != "/auth/callback":
                self.send_error(404)
                return
            pairs = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            query = {k: v[0] for k, v in pairs.items() if len(v) == 1}
            valid = query.get("state") == state
            if valid:
                output.update(query)
            message = (b"Authorization received. Return to your terminal."
                       if valid else b"Invalid authorization state.")
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)

    server.RequestHandlerClass = Handler
    server.timeout = 180
    server.handle_request()
    return output


def login(*, new_account: bool = False, no_browser: bool = False) -> dict:
    """Run official dynamic registration on the machine with the user's browser."""
    data = read_accounts()
    previous = None
    if not new_account and data.get("active") in data["accounts"]:
        previous = data["accounts"][data["active"]]
    client_id = previous["client_id"] if previous else "dynamic_agent_client"
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    with HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
        callback = f"http://127.0.0.1:{server.server_port}/auth/callback"
        url = build_authorize_url(
            client_id=client_id, host=host_id(), callback=callback,
            state=state, nonce=nonce, challenge=challenge,
        )
        # Never print an id_token_hint or any OAuth tokens.
        print("Open the following official authorization URL in this machine's browser:")
        print(url)
        if not no_browser:
            webbrowser.open(url)
        query = _receive_callback(server, state=state)
    code, issued_id = validate_callback(
        query, expected_state=state, expected_client_id=client_id
    )
    body = _post_form(TOKEN_URL, {
        "grant_type": "authorization_code",
        "client_id": issued_id,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": callback,
        "resource": RESOURCE,
    })
    raw_id = body.get("id_token")
    if not isinstance(raw_id, str):
        raise SubscriptionError("OpenAI did not return an ID token.")
    claims = _validate_id_token(raw_id, client_id=issued_id, nonce=nonce)
    if previous is not None and claims["sub"] != previous["subject"]:
        raise SubscriptionError("Reauthorization changed the selected ChatGPT identity.")
    with locked_store():
        data = read_accounts()
        # Do not overwrite a different user's registration or consent state.
        existing = data["accounts"].get(issued_id)
        if existing and existing.get("subject") != claims["sub"]:
            raise SubscriptionError("OAuth registration identity mismatch.")
        record = _make_record(body, issued_id, claims, previous=previous)
        data["accounts"][issued_id] = record
        data["active"] = issued_id
        write_accounts(data)
    return public_status()


def get_access_token() -> str:
    """Refresh atomically across processes when near expiry, fail closed."""
    with locked_store():
        data = read_accounts()
        account = selected_account(data)
        if "chatgpt.tokens.use.direct" not in account.get("scopes", []):
            raise SubscriptionError("ChatGPT plan usage consent was not granted; reauthorize.")
        if not account.get("refresh_token"):
            raise SubscriptionError("ChatGPT login is required.")
        if account.get("expires_at", 0) > time.time() + 120 and account.get("access_token"):
            return account["access_token"]
        earliest = account.get("earliest_refresh_at")
        # The field may be an ISO timestamp or a numeric epoch; do not
        # make speculative early-refresh requests when OpenAI defers them.
        if isinstance(earliest, (int, float)) and earliest > time.time():
            if account.get("expires_at", 0) > time.time():
                return account["access_token"]
            raise SubscriptionTransientError("OpenAI refresh is not yet available.")
        try:
            body = _post_form(TOKEN_URL, {
                "grant_type": "refresh_token",
                "client_id": account["client_id"],
                "refresh_token": account["refresh_token"],
                "resource": RESOURCE,
            })
        except SubscriptionError as exc:
            if isinstance(exc, SubscriptionTransientError):
                raise
            if "expired; sign in again" in str(exc):
                # Preserve the issued ID and verified account identity, but
                # prevent additional use of a confirmed invalid credential.
                account.update(access_token="", refresh_token="", expires_at=0)
                write_accounts(data)
            raise
        if body.get("id_token"):
            claims = _validate_id_token(body["id_token"], client_id=account["client_id"], nonce=None)
            if claims["sub"] != account["subject"]:
                raise SubscriptionError("Refresh changed the selected ChatGPT identity.")
        else:
            claims = {"sub": account["subject"], "email": account.get("email", "")}
        updated = _make_record(body, account["client_id"], claims, previous=account)
        data["accounts"][data["active"]] = updated
        write_accounts(data)
        if "chatgpt.tokens.use.direct" not in updated["scopes"]:
            raise SubscriptionError("ChatGPT plan permission was removed.")
        return updated["access_token"]


def list_models() -> list[dict]:
    """Discover models for the currently authenticated ChatGPT identity."""
    token = get_access_token()
    try:
        response = requests.get(
            RESOURCE + "/models",
            headers={"Authorization": "Bearer " + token},
            timeout=30, allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise SubscriptionTransientError("OpenAI model catalog is unavailable.") from exc
    if response.status_code != 200:
        raise SubscriptionError(f"OpenAI model list request failed (HTTP {response.status_code}).")
    try:
        body = response.json()
    except ValueError as exc:
        raise SubscriptionError("OpenAI model list was not JSON.") from exc
    models = body.get("models", []) if isinstance(body, dict) else []
    if not isinstance(models, list):
        raise SubscriptionError("OpenAI model catalog has an invalid format.")
    return [
        {"slug": row["slug"], "display_name": row.get("display_name", row["slug"])}
        for row in models if isinstance(row, dict)
        and row.get("visibility") == "list"
        and isinstance(row.get("slug"), str)
    ]


def _event_attr(obj, attr, default=None):
    return obj.get(attr, default) if isinstance(obj, dict) else getattr(obj, attr, default)


def _infer_error(code: str) -> SubscriptionError:
    if code in LIMIT_CODES:
        return SubscriptionLimitError(
            "ChatGPT subscription app quota reached. View ChatGPT Settings > Usage."
        )
    if code in {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"}:
        return SubscriptionTransientError("ChatGPT subscription usage service is temporarily unavailable.")
    return SubscriptionError(f"ChatGPT subscription inference rejected ({code[:80]}).")


class SIWCClient:
    """Chat-shaped adapter backed exclusively by OAuth Responses streaming."""

    subscription_provider = "openai_siwc"
    base_url = RESOURCE

    def __init__(self, *, timeout: float = 300.0):
        self.timeout = timeout

    def generate_chat(self, **kwargs):
        from openai import OpenAI

        model = kwargs.get("model")
        if not isinstance(model, str) or not model:
            raise SubscriptionError("A ChatGPT subscription model must be selected.")
        messages = kwargs.get("messages", [])
        instructions = []
        context = []
        for item in messages:
            role = item.get("role")
            if role in {"system", "developer"}:
                instructions.append(str(item.get("content") or ""))
            elif role in {"user", "assistant"}:
                context.append({"role": role, "content": str(item.get("content") or "")})
            else:
                raise SubscriptionError("Unsupported SIWC message role.")
        if not context:
            raise SubscriptionError("SIWC request must contain a user message.")
        payload = {"model": model, "input": context, "store": False, "stream": True}
        if instructions:
            payload["instructions"] = "\n\n".join(instructions)
        if kwargs.get("response_format"):
            request_format = kwargs["response_format"]
            if request_format.get("type") == "json_object":
                payload["text"] = {"format": {"type": "json_object"}}
        if kwargs.get("reasoning_effort"):
            payload["reasoning"] = {"effort": kwargs["reasoning_effort"]}
        # Never send temperature, top_p, background, user or other unsupported
        # Chat parameters in the subscription route.
        token = get_access_token()
        client = OpenAI(
            api_key=token, base_url=RESOURCE,
            max_retries=0, timeout=self.timeout,
        )
        del token
        chunks = []
        total = 0
        completed = False
        usage = None
        try:
            with client.responses.create(**payload) as stream:
                for event in stream:
                    event_type = _event_attr(event, "type")
                    if event_type == "response.output_text.delta":
                        delta = _event_attr(event, "delta", "")
                        if isinstance(delta, str):
                            total += len(delta)
                            if total > 4_000_000:
                                raise SubscriptionError("SIWC response exceeded safety size limit.")
                            chunks.append(delta)
                    elif event_type == "response.failed":
                        error = _event_attr(_event_attr(event, "response"), "error")
                        code = _event_attr(error, "code", "unknown_error")
                        raise _infer_error(str(code))
                    elif event_type == "response.incomplete":
                        raise SubscriptionError("SIWC inference was incomplete.")
                    elif event_type == "response.completed":
                        completed = True
                        usage = _event_attr(_event_attr(event, "response"), "usage")
        except SubscriptionError:
            raise
        except Exception as exc:
            # Preserve failure metadata without echoing response bodies or
            # bearer credentials into logs or user-visible diagnostics.
            status = getattr(exc, "status_code", 0)
            code = getattr(exc, "code", None)
            if not code:
                body = getattr(exc, "body", None)
                if isinstance(body, dict):
                    detail = body.get("error", {})
                    code = detail.get("code") if isinstance(detail, dict) else None
            if code:
                raise _infer_error(str(code)) from exc
            if status in {401, 403, 400}:
                raise SubscriptionError(f"ChatGPT authorization/inference rejected (HTTP {status}).") from exc
            if status == 429:
                raise SubscriptionLimitError("ChatGPT subscription quota is unavailable; see Settings > Usage.") from exc
            from openai import APIConnectionError, APITimeoutError
            if status >= 500 or isinstance(exc, (TimeoutError, ConnectionError, APIConnectionError, APITimeoutError)):
                raise SubscriptionTransientError("ChatGPT inference temporarily unavailable.") from exc
            raise SubscriptionError("ChatGPT subscription inference failed; check provider status.") from exc
        if not completed:
            raise SubscriptionTransientError("SIWC stream ended before response.completed.")
        text = "".join(chunks)
        if not text.strip():
            raise SubscriptionError("SIWC completed without output text.")
        return SimpleNamespace(
            model=model,
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=usage,
        )

    def generate_responses(self, **kwargs):
        messages = []
        if kwargs.get("instructions"):
            messages.append({"role": "system", "content": kwargs["instructions"]})
        supplied = kwargs.get("input", "")
        if isinstance(supplied, list):
            messages.extend(supplied)
        else:
            messages.append({"role": "user", "content": supplied})
        request = {
            "model": kwargs["model"], "messages": messages,
        }
        if isinstance(kwargs.get("text"), dict):
            request["response_format"] = kwargs["text"].get("format")
        if isinstance(kwargs.get("reasoning"), dict):
            request["reasoning_effort"] = kwargs["reasoning"].get("effort")
        chat = self.generate_chat(**request)
        return SimpleNamespace(output_text=chat.choices[0].message.content, usage=chat.usage)


def disconnect() -> bool:
    """Best-effort remote revocation, always clear the selected local tokens."""
    with locked_store():
        data = read_accounts()
        account = selected_account(data)
        token = account.get("refresh_token")
        revoked = False
        if token:
            try:
                response = requests.post(
                    REVOKE_URL,
                    data={"token": token, "token_type_hint": "refresh_token",
                          "client_id": account["client_id"]},
                    timeout=15, allow_redirects=False,
                )
                revoked = response.status_code == 200
            except requests.RequestException:
                pass
        account.update(access_token="", refresh_token="", id_token="", expires_at=0)
        write_accounts(data)
    return revoked


def select_account(client_id: str) -> dict:
    with locked_store():
        data = read_accounts()
        if client_id not in data["accounts"]:
            raise SubscriptionError("Unknown ChatGPT account registration.")
        data["active"] = client_id
        write_accounts(data)
    return public_status()


def main() -> int:
    parser = argparse.ArgumentParser(description="Local OpenAI SIWC authorization for ArXiv Daily Researcher")
    sub = parser.add_subparsers(dest="command", required=True)
    auth = sub.add_parser("login", help="Authorize in a local system browser")
    auth.add_argument("--new-account", action="store_true")
    auth.add_argument("--no-browser", action="store_true")
    sub.add_parser("status")
    sub.add_parser("models")
    smoke = sub.add_parser("smoke", help="Run one consented live subscription Responses request")
    smoke.add_argument("--model", required=True, help="Visible model slug from the models command")
    sub.add_parser("disconnect")
    choose = sub.add_parser("select")
    choose.add_argument("client_id")
    args = parser.parse_args()
    try:
        if args.command == "login":
            result = login(new_account=args.new_account, no_browser=args.no_browser)
            print(json.dumps(result, indent=2))
        elif args.command == "status":
            print(json.dumps(public_status(), indent=2))
        elif args.command == "models":
            print(json.dumps(list_models(), indent=2))
        elif args.command == "smoke":
            models = [item["slug"] for item in list_models()]
            if args.model not in models:
                raise SubscriptionError("Model is not visible to the selected ChatGPT account.")
            print("Sending one stream request using your ChatGPT subscription allowance.")
            response = SIWCClient().generate_chat(
                model=args.model,
                messages=[{"role": "user", "content": "Reply with exactly: SIWC OK"}],
            )
            print("Completed subscription response: " + response.choices[0].message.content)
        elif args.command == "select":
            print(json.dumps(select_account(args.client_id), indent=2))
        elif args.command == "disconnect":
            confirmed = disconnect()
            print("Disconnected locally." + ("" if confirmed else " Remote revocation unconfirmed; disconnect in ChatGPT settings."))
        return 0
    except SubscriptionError as exc:
        print("Subscription authorization error: " + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
