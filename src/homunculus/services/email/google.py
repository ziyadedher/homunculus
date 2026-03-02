# pyright: reportAttributeAccessIssue=false
# Resource methods (.users()) are generated dynamically by googleapiclient.
import asyncio
import base64

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from homunculus.services.email.models import EmailDetail, EmailSummary


def _build_service(creds: Credentials) -> Resource:
    return build("gmail", "v1", credentials=creds)


def _get_header(headers: list[dict[str, str]], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode_body(payload: dict[str, object]) -> str:
    """Extract plain text body from a Gmail message payload."""
    # Simple single-part message
    body = payload.get("body", {})
    if isinstance(body, dict) and body.get("data"):
        return base64.urlsafe_b64decode(body["data"]).decode("utf-8", errors="replace")

    # Multipart: look for text/plain
    parts = payload.get("parts", [])
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            mime = part.get("mimeType", "")
            if mime == "text/plain":
                part_body = part.get("body", {})
                if isinstance(part_body, dict) and part_body.get("data"):
                    return base64.urlsafe_b64decode(part_body["data"]).decode(
                        "utf-8", errors="replace"
                    )
            # Recurse into nested multipart
            nested = _decode_body(part)
            if nested:
                return nested

    return ""


async def search_messages(
    creds: Credentials, query: str, max_results: int = 10
) -> list[EmailSummary]:
    def _search() -> list[EmailSummary]:
        service = _build_service(creds)
        result = (
            service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        )
        messages = result.get("messages", [])

        summaries: list[EmailSummary] = []
        for msg_ref in messages:
            msg = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_ref["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            headers = msg.get("payload", {}).get("headers", [])
            summaries.append(
                EmailSummary(
                    id=msg["id"],
                    thread_id=msg["threadId"],
                    subject=_get_header(headers, "Subject"),
                    sender=_get_header(headers, "From"),
                    date=_get_header(headers, "Date"),
                    snippet=msg.get("snippet", ""),
                )
            )
        return summaries

    return await asyncio.to_thread(_search)


async def get_message(creds: Credentials, message_id: str) -> EmailDetail:
    def _get() -> EmailDetail:
        service = _build_service(creds)
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = msg.get("payload", {}).get("headers", [])
        to_raw = _get_header(headers, "To")
        to_list = [addr.strip() for addr in to_raw.split(",") if addr.strip()] if to_raw else []
        return EmailDetail(
            id=msg["id"],
            thread_id=msg["threadId"],
            subject=_get_header(headers, "Subject"),
            sender=_get_header(headers, "From"),
            to=to_list,
            date=_get_header(headers, "Date"),
            body_text=_decode_body(msg.get("payload", {})),
        )

    return await asyncio.to_thread(_get)
