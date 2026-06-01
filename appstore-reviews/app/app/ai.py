"""Draft a developer reply to a review via the ollama-router OpenAI-compatible API.

Tries the configured model, then falls back through the list on failure (the 5090
backend on node-50 is flaky; node-10 is the backup). Same pattern as the congress
AI summary job.
"""
import requests

SYSTEM_PROMPT = (
    "You are an app developer writing a short, warm, professional public reply to a "
    "customer's App Store review. Address their specific points. Thank positive "
    "reviewers; for complaints, apologize briefly, acknowledge the issue, and where it "
    "makes sense invite them to contact support. Do not include links, promo codes, or "
    "personal data. Keep it under 90 words. Write only the reply text, no preamble."
)


def draft_reply(cfg, review):
    ai = cfg["ai"]
    stars = review.get("rating")
    parts = [f"Rating: {stars} out of 5 stars."]
    if review.get("title"):
        parts.append(f"Title: {review['title']}")
    if review.get("body"):
        parts.append(f"Review: {review['body']}")
    if review.get("reviewer"):
        parts.append(f"Reviewer nickname: {review['reviewer']}")
    user_prompt = "\n".join(parts)

    models = [ai["model"], *ai.get("fallback_models", [])]
    last_error = None
    for model in models:
        try:
            resp = requests.post(
                f"{ai['base_url'].rstrip('/')}/chat/completions",
                json={
                    "model": model,
                    "temperature": ai.get("temperature", 0.4),
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=ai.get("request_timeout", 120.0),
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            if content:
                return {"draft": content, "model": model}
        except (requests.RequestException, KeyError, IndexError) as exc:
            last_error = exc
    raise RuntimeError(f"all models failed; last error: {last_error}")
