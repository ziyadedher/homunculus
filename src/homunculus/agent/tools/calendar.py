import json
from datetime import datetime

from google.oauth2.credentials import Credentials

from homunculus.agent.tools.registry import ToolDef
from homunculus.calendar import google as gcal


def make_calendar_tools(creds: Credentials, calendar_id: str) -> list[ToolDef]:
    async def check_availability(time_min: str, time_max: str) -> str:
        result = await gcal.get_freebusy(
            creds,
            time_min=datetime.fromisoformat(time_min),
            time_max=datetime.fromisoformat(time_max),
            calendar_id=calendar_id,
        )
        if not result.busy_periods:
            return json.dumps({"available": True, "busy_periods": []})
        busy = [
            {"start": p.start.isoformat(), "end": p.end.isoformat()} for p in result.busy_periods
        ]
        return json.dumps({"available": False, "busy_periods": busy})

    async def list_events(time_min: str, time_max: str) -> str:
        events = await gcal.list_events(
            creds,
            time_min=datetime.fromisoformat(time_min),
            time_max=datetime.fromisoformat(time_max),
            calendar_id=calendar_id,
        )
        return json.dumps(
            [
                {
                    "id": e.id,
                    "summary": e.summary,
                    "start": e.start.isoformat(),
                    "end": e.end.isoformat(),
                }
                for e in events
            ]
        )

    async def create_event(
        summary: str,
        start: str,
        end: str,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        recurrence: list[str] | None = None,
        reminders_minutes: list[int] | None = None,
        conference: bool = False,
    ) -> str:
        event = await gcal.create_event(
            creds,
            summary=summary,
            start=datetime.fromisoformat(start),
            end=datetime.fromisoformat(end),
            calendar_id=calendar_id,
            description=description,
            location=location,
            attendees=attendees,
            recurrence=recurrence,
            reminders_minutes=reminders_minutes,
            conference=conference,
        )
        return json.dumps(
            {
                "id": event.id,
                "summary": event.summary,
                "start": event.start.isoformat(),
                "end": event.end.isoformat(),
            }
        )

    async def update_event(
        event_id: str,
        summary: str | None = None,
        start: str | None = None,
        end: str | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
    ) -> str:
        event = await gcal.update_event(
            creds,
            event_id=event_id,
            calendar_id=calendar_id,
            summary=summary,
            start=datetime.fromisoformat(start) if start else None,
            end=datetime.fromisoformat(end) if end else None,
            description=description,
            location=location,
            attendees=attendees,
        )
        return json.dumps(
            {
                "id": event.id,
                "summary": event.summary,
                "start": event.start.isoformat(),
                "end": event.end.isoformat(),
            }
        )

    async def delete_event(event_id: str) -> str:
        await gcal.delete_event(creds, event_id=event_id, calendar_id=calendar_id)
        return json.dumps({"status": "deleted", "event_id": event_id})

    async def find_free_slots(time_min: str, time_max: str, duration_minutes: int) -> str:
        result = await gcal.get_freebusy(
            creds,
            time_min=datetime.fromisoformat(time_min),
            time_max=datetime.fromisoformat(time_max),
            calendar_id=calendar_id,
        )
        range_start = datetime.fromisoformat(time_min)
        range_end = datetime.fromisoformat(time_max)
        busy = sorted(result.busy_periods, key=lambda p: p.start)

        free_slots = []
        current = range_start
        for period in busy:
            if period.start > current:
                gap_minutes = (period.start - current).total_seconds() / 60
                if gap_minutes >= duration_minutes:
                    free_slots.append(
                        {"start": current.isoformat(), "end": period.start.isoformat()}
                    )
            if period.end > current:
                current = period.end
        if current < range_end:
            gap_minutes = (range_end - current).total_seconds() / 60
            if gap_minutes >= duration_minutes:
                free_slots.append({"start": current.isoformat(), "end": range_end.isoformat()})

        return json.dumps({"free_slots": free_slots})

    return [
        ToolDef(
            name="check_availability",
            description="Check if the owner is free during a time range. Returns busy periods if any.",
            input_schema={
                "type": "object",
                "properties": {
                    "time_min": {
                        "type": "string",
                        "description": "Start of time range in ISO 8601 format",
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End of time range in ISO 8601 format",
                    },
                },
                "required": ["time_min", "time_max"],
            },
            handler=check_availability,
        ),
        ToolDef(
            name="list_events",
            description="List upcoming calendar events in a time range. Returns event summaries and times.",
            input_schema={
                "type": "object",
                "properties": {
                    "time_min": {
                        "type": "string",
                        "description": "Start of time range in ISO 8601 format",
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End of time range in ISO 8601 format",
                    },
                },
                "required": ["time_min", "time_max"],
            },
            handler=list_events,
        ),
        ToolDef(
            name="create_event",
            description="Create a new calendar event. Requires owner approval (handled automatically).",
            input_schema={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Event title",
                    },
                    "start": {
                        "type": "string",
                        "description": "Event start time in ISO 8601 format",
                    },
                    "end": {
                        "type": "string",
                        "description": "Event end time in ISO 8601 format",
                    },
                    "description": {
                        "type": "string",
                        "description": "Event description (optional)",
                    },
                    "location": {
                        "type": "string",
                        "description": "Event location (optional)",
                    },
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Email addresses of attendees (optional)",
                    },
                    "recurrence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "RRULE recurrence rules (optional)",
                    },
                    "reminders_minutes": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Reminder times in minutes before event (optional)",
                    },
                    "conference": {
                        "type": "boolean",
                        "description": "Whether to create a Google Meet link (optional)",
                    },
                },
                "required": ["summary", "start", "end"],
            },
            handler=create_event,
            requires_approval=True,
        ),
        ToolDef(
            name="update_event",
            description="Update an existing calendar event. Requires owner approval (handled automatically).",
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "ID of the event to update",
                    },
                    "summary": {
                        "type": "string",
                        "description": "New event title (optional)",
                    },
                    "start": {
                        "type": "string",
                        "description": "New start time in ISO 8601 format (optional)",
                    },
                    "end": {
                        "type": "string",
                        "description": "New end time in ISO 8601 format (optional)",
                    },
                    "description": {
                        "type": "string",
                        "description": "New event description (optional)",
                    },
                    "location": {
                        "type": "string",
                        "description": "New event location (optional)",
                    },
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "New attendee email addresses (optional)",
                    },
                },
                "required": ["event_id"],
            },
            handler=update_event,
            requires_approval=True,
        ),
        ToolDef(
            name="delete_event",
            description="Delete a calendar event. Requires owner approval (handled automatically).",
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "ID of the event to delete",
                    },
                },
                "required": ["event_id"],
            },
            handler=delete_event,
            requires_approval=True,
        ),
        ToolDef(
            name="find_free_slots",
            description="Find available time slots in a time range given a minimum duration.",
            input_schema={
                "type": "object",
                "properties": {
                    "time_min": {
                        "type": "string",
                        "description": "Start of time range in ISO 8601 format",
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End of time range in ISO 8601 format",
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Minimum duration of free slots in minutes",
                    },
                },
                "required": ["time_min", "time_max", "duration_minutes"],
            },
            handler=find_free_slots,
        ),
    ]
