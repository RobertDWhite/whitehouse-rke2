"""App Store Connect API client.

Auth is a short-lived ES256 JWT signed with the team's .p8 private key (App Store
Connect -> Users and Access -> Integrations -> App Store Connect API key). Tokens
are valid for up to 20 minutes; we mint one good for 15 and reuse it until it nears
expiry. The signing inputs come from the SOPS secret (see 20-secret.sops.yaml):
ASC_ISSUER_ID, ASC_KEY_ID, ASC_PRIVATE_KEY (the full -----BEGIN PRIVATE KEY----- PEM).
"""
import os
import time

import jwt
import requests

BASE_URL = "https://api.appstoreconnect.apple.com"
AUDIENCE = "appstoreconnect-v1"
TOKEN_TTL = 15 * 60  # seconds; Apple caps at 20 minutes


class ASCError(Exception):
    """Surface an App Store Connect API error to the caller with status + detail."""

    def __init__(self, status, detail):
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")


class ASCClient:
    def __init__(self, issuer_id, key_id, private_key):
        self._issuer_id = issuer_id
        self._key_id = key_id
        self._private_key = private_key
        self._token = None
        self._token_exp = 0

    def _jwt(self):
        now = int(time.time())
        if self._token and now < self._token_exp - 60:
            return self._token
        exp = now + TOKEN_TTL
        self._token = jwt.encode(
            {"iss": self._issuer_id, "iat": now, "exp": exp, "aud": AUDIENCE},
            self._private_key,
            algorithm="ES256",
            headers={"kid": self._key_id, "typ": "JWT"},
        )
        self._token_exp = exp
        return self._token

    def _request(self, method, path_or_url, **kwargs):
        url = path_or_url if path_or_url.startswith("http") else f"{BASE_URL}{path_or_url}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._jwt()}"
        resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code >= 400:
            detail = resp.text
            try:
                errors = resp.json().get("errors", [])
                if errors:
                    detail = "; ".join(e.get("detail", e.get("title", "")) for e in errors)
            except ValueError:
                pass
            raise ASCError(resp.status_code, detail)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def list_apps(self):
        """All apps on the account, name + bundle id, sorted by name."""
        data = self._request(
            "GET",
            "/v1/apps",
            params={"limit": 200, "fields[apps]": "name,bundleId", "sort": "name"},
        )
        return [
            {"id": a["id"], "name": a["attributes"].get("name"), "bundleId": a["attributes"].get("bundleId")}
            for a in data.get("data", [])
        ]

    def list_reviews(self, app_id, cursor=None):
        """Customer reviews for an app (newest first), each with its existing
        developer response (if any) resolved from the included payload."""
        if cursor:
            data = self._request("GET", cursor)
        else:
            data = self._request(
                "GET",
                f"/v1/apps/{app_id}/customerReviews",
                params={
                    "limit": 50,
                    "sort": "-createdDate",
                    "include": "response",
                    "fields[customerReviews]": "rating,title,body,reviewerNickname,createdDate,territory,response",
                    "fields[customerReviewResponses]": "responseBody,lastModifiedDate,state",
                },
            )
        responses = {
            inc["id"]: inc["attributes"]
            for inc in data.get("included", [])
            if inc["type"] == "customerReviewResponses"
        }
        reviews = []
        for r in data.get("data", []):
            attrs = r["attributes"]
            resp_rel = r.get("relationships", {}).get("response", {}).get("data")
            existing = None
            if resp_rel and resp_rel["id"] in responses:
                ra = responses[resp_rel["id"]]
                existing = {
                    "id": resp_rel["id"],
                    "body": ra.get("responseBody"),
                    "lastModifiedDate": ra.get("lastModifiedDate"),
                    "state": ra.get("state"),
                }
            reviews.append(
                {
                    "id": r["id"],
                    "rating": attrs.get("rating"),
                    "title": attrs.get("title"),
                    "body": attrs.get("body"),
                    "reviewer": attrs.get("reviewerNickname"),
                    "createdDate": attrs.get("createdDate"),
                    "territory": attrs.get("territory"),
                    "response": existing,
                }
            )
        next_cursor = data.get("links", {}).get("next")
        return {"reviews": reviews, "next": next_cursor}

    def put_response(self, review_id, body):
        """Create or update the developer response for a review.

        Apple allows one response per review; POSTing again with the same review
        relationship updates the existing one, so a single call covers both cases.
        """
        payload = {
            "data": {
                "type": "customerReviewResponses",
                "attributes": {"responseBody": body},
                "relationships": {
                    "review": {"data": {"type": "customerReviews", "id": review_id}}
                },
            }
        }
        return self._request("POST", "/v1/customerReviewResponses", json=payload)

    def get_response_id(self, review_id):
        """Resolve the response id for a review, or None if there is no response."""
        data = self._request(
            "GET", f"/v1/customerReviews/{review_id}/response", params={"fields[customerReviewResponses]": "state"}
        )
        node = data.get("data")
        return node["id"] if node else None

    def delete_response(self, response_id):
        self._request("DELETE", f"/v1/customerReviewResponses/{response_id}")


def client_from_env():
    issuer_id = os.environ["ASC_ISSUER_ID"]
    key_id = os.environ["ASC_KEY_ID"]
    private_key = os.environ["ASC_PRIVATE_KEY"]
    return ASCClient(issuer_id, key_id, private_key)
