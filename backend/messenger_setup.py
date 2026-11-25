"""Check a Page token/subscription; use --subscribe to explicitly subscribe the Page.

Run: python -m backend.messenger_setup [--subscribe]
Credentials come only from the environment; Graph errors are redacted.
"""
import argparse

import httpx

from backend.messenger import (APP_SECRET, FACEBOOK_PAGE_ID, GRAPH_API_BASE,
                               MessengerConfigurationError, page_authorization_headers)


class SetupError(RuntimeError):
    pass


def graph_request(client, method, path, **kwargs):
    try:
        response = client.request(method, f"{GRAPH_API_BASE}/{path}", **kwargs)
    except httpx.LocalProtocolError:
        raise SetupError("Local HTTP protocol error. Check Page token formatting and HTTP/proxy configuration.") from None
    except httpx.RequestError:
        raise SetupError("Cannot reach Meta Graph API. Check network connectivity.") from None
    try:
        payload = response.json()
    except ValueError:
        raise SetupError(f"Meta returned an invalid response (HTTP {response.status_code}).") from None
    if not response.is_success or not isinstance(payload, dict) or payload.get("error"):
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        code = error.get("code") if isinstance(error, dict) else None
        code = code if isinstance(code, int) else "unknown"
        hint = (" Check Page access and permissions; Page metadata lookups may require pages_read_engagement."
                if code == 100 else " Check token expiry, Page access, and permissions.")
        raise SetupError(f"Meta rejected the operation (HTTP {response.status_code}, code {code})." + hint)
    return payload


def check_page(client, subscribe=False):
    page = graph_request(client, "GET", "me", params={"fields": "id"})
    page_id = page.get("id")
    if not isinstance(page_id, str) or not page_id.isascii() or not page_id.isdigit():
        raise SetupError("Meta did not return a valid Page ID.")
    if FACEBOOK_PAGE_ID and page_id != FACEBOOK_PAGE_ID:
        raise SetupError("The Page token does not match FACEBOOK_PAGE_ID.")
    print(f"Token accepted. Page ID: {page_id}")
    if subscribe:
        result = graph_request(client, "POST", f"{page_id}/subscribed_apps",
                               data={"subscribed_fields": "messages,messaging_postbacks"})
        if result.get("success") is not True:
            raise SetupError("Meta did not confirm Page subscription.")
        print("Page subscription accepted by Meta.")
    subscriptions = graph_request(client, "GET", f"{page_id}/subscribed_apps",
                                  params={"fields": "id,subscribed_fields"})
    apps = subscriptions.get("data", [])
    if not isinstance(apps, list):
        raise SetupError("Meta returned an invalid subscription list.")
    print(f"Subscribed apps returned: {len(apps)}")
    for app in apps:
        if isinstance(app, dict):
            print(f"  App {app.get('id')}: {app.get('subscribed_fields', [])}")
    print("Match the app ID above with your Meta App dashboard; callback verification is a separate step.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscribe", action="store_true", help="Subscribe the token's Page to messages and messaging_postbacks")
    args = parser.parse_args()
    try:
        headers = page_authorization_headers()
    except MessengerConfigurationError as exc:
        parser.error(str(exc))
    if args.subscribe and not APP_SECRET:
        parser.error("Configure APP_SECRET before subscribing; POST events require signature verification.")
    if not APP_SECRET:
        print("Warning: APP_SECRET is missing. GET verification works, but POST events will return 503.")
    try:
        with httpx.Client(headers=headers, http2=False, timeout=httpx.Timeout(15, connect=5)) as client:
            check_page(client, subscribe=args.subscribe)
    except SetupError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
