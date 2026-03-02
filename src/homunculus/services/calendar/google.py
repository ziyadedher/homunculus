import asyncio
import uuid
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from homunculus.services.calendar.models import Event, FreeBusyResult, TimePeriod

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]


def get_credentials(
    credentials_path: Path,
    token_path: Path,
) -> Credentials:
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        new_creds = flow.run_local_server(port=0)
        if not isinstance(new_creds, Credentials):
            msg = f"Unexpected credential type: {type(new_creds)}"
            raise TypeError(msg)
        creds = new_creds
    token_path.write_text(creds.to_json())
    return creds


def _build_service(creds: Credentials) -> Resource:
    return build("calendar", "v3", credentials=creds)


def _parse_event_datetime(dt_dict: dict[str, str]) -> datetime:
    raw = dt_dict.get("dateTime") or dt_dict.get("date")
    if raw is None:
        msg = f"Event datetime missing both 'dateTime' and 'date': {dt_dict}"
        raise ValueError(msg)
    return datetime.fromisoformat(raw)


async def get_freebusy(
    creds: Credentials, time_min: datetime, time_max: datetime, calendar_id: str = "primary"
) -> FreeBusyResult:
    def _query() -> list[TimePeriod]:
        # Resource methods are dynamically generated; stubs don't expose them
        service = _build_service(creds)
        body = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "items": [{"id": calendar_id}],
        }
        result = service.freebusy().query(body=body).execute()
        busy = result["calendars"][calendar_id]["busy"]
        return [
            TimePeriod(
                start=datetime.fromisoformat(p["start"]),
                end=datetime.fromisoformat(p["end"]),
            )
            for p in busy
        ]

    busy_periods = await asyncio.to_thread(_query)
    return FreeBusyResult(busy_periods=busy_periods, time_min=time_min, time_max=time_max)


async def list_events(
    creds: Credentials, time_min: datetime, time_max: datetime, calendar_id: str = "primary"
) -> list[Event]:
    def _query() -> list[Event]:
        service = _build_service(creds)
        result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return [
            Event(
                id=e["id"],
                summary=e.get("summary", "(No title)"),
                start=_parse_event_datetime(e["start"]),
                end=_parse_event_datetime(e["end"]),
                description=e.get("description"),
                location=e.get("location"),
            )
            for e in result.get("items", [])
        ]

    return await asyncio.to_thread(_query)


async def create_event(
    creds: Credentials,
    summary: str,
    start: datetime,
    end: datetime,
    calendar_id: str = "primary",
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    recurrence: list[str] | None = None,
    reminders_minutes: list[int] | None = None,
    conference: bool = False,
) -> Event:
    def _create() -> Event:
        service = _build_service(creds)
        body: dict[str, object] = {
            "summary": summary,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": email} for email in attendees]
        if recurrence:
            body["recurrence"] = recurrence
        if reminders_minutes:
            body["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": m} for m in reminders_minutes],
            }
        if conference:
            body["conferenceData"] = {
                "createRequest": {"requestId": uuid.uuid4().hex},
            }

        kwargs: dict[str, object] = {"calendarId": calendar_id, "body": body}
        if conference:
            kwargs["conferenceDataVersion"] = 1

        result = service.events().insert(**kwargs).execute()
        return Event(
            id=result["id"],
            summary=result.get("summary", summary),
            start=_parse_event_datetime(result["start"]),
            end=_parse_event_datetime(result["end"]),
            description=result.get("description"),
            location=result.get("location"),
        )

    return await asyncio.to_thread(_create)


async def update_event(
    creds: Credentials,
    event_id: str,
    calendar_id: str = "primary",
    summary: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
) -> Event:
    def _update() -> Event:
        service = _build_service(creds)
        # Fetch existing event first
        existing = service.events().get(calendarId=calendar_id, eventId=event_id).execute()

        if summary is not None:
            existing["summary"] = summary
        if start is not None:
            existing["start"] = {"dateTime": start.isoformat()}
        if end is not None:
            existing["end"] = {"dateTime": end.isoformat()}
        if description is not None:
            existing["description"] = description
        if location is not None:
            existing["location"] = location
        if attendees is not None:
            existing["attendees"] = [{"email": email} for email in attendees]

        result = (
            service.events()
            .update(calendarId=calendar_id, eventId=event_id, body=existing)
            .execute()
        )
        return Event(
            id=result["id"],
            summary=result.get("summary", "(No title)"),
            start=_parse_event_datetime(result["start"]),
            end=_parse_event_datetime(result["end"]),
            description=result.get("description"),
            location=result.get("location"),
        )

    return await asyncio.to_thread(_update)


async def delete_event(
    creds: Credentials,
    event_id: str,
    calendar_id: str = "primary",
) -> None:
    def _delete() -> None:
        service = _build_service(creds)
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()

    await asyncio.to_thread(_delete)
