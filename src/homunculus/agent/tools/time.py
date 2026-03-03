import json
from datetime import datetime
from zoneinfo import ZoneInfo

from homunculus.agent.tools.registry import ToolDef


def make_time_tools(owner_timezone: str) -> list[ToolDef]:
    async def get_current_time(timezone: str = "") -> str:
        tz_name = timezone or owner_timezone
        try:
            tz = ZoneInfo(tz_name)
        except KeyError:
            return json.dumps({"error": f"Unknown timezone: {tz_name}"})

        now = datetime.now(tz)
        return json.dumps({
            "timezone": tz_name,
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%A, %B %d, %Y"),
            "time": now.strftime("%I:%M %p"),
            "utc_offset": now.strftime("%z"),
        })

    async def convert_time(time_str: str, from_timezone: str, to_timezone: str) -> str:
        try:
            from_tz = ZoneInfo(from_timezone)
        except KeyError:
            return json.dumps({"error": f"Unknown timezone: {from_timezone}"})
        try:
            to_tz = ZoneInfo(to_timezone)
        except KeyError:
            return json.dumps({"error": f"Unknown timezone: {to_timezone}"})

        # Try common time formats
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%H:%M", "%I:%M %p"):
            try:
                parsed = datetime.strptime(time_str, fmt)
                break
            except ValueError:
                continue
        else:
            return json.dumps({"error": f"Could not parse time: {time_str}"})

        # If only time was given (no date), use today's date in the source timezone
        if parsed.year == 1900:
            today = datetime.now(from_tz).date()
            parsed = parsed.replace(year=today.year, month=today.month, day=today.day)

        localized = parsed.replace(tzinfo=from_tz)
        converted = localized.astimezone(to_tz)

        return json.dumps({
            "original": {
                "timezone": from_timezone,
                "datetime": localized.strftime("%Y-%m-%d %H:%M:%S"),
                "time": localized.strftime("%I:%M %p"),
            },
            "converted": {
                "timezone": to_timezone,
                "datetime": converted.strftime("%Y-%m-%d %H:%M:%S"),
                "time": converted.strftime("%I:%M %p"),
            },
        })

    return [
        ToolDef(
            name="get_current_time",
            description=(
                "Get the current date and time. Defaults to the owner's timezone."
                " Use this whenever you need to know the current time or date."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": (
                            "IANA timezone name (e.g. 'America/New_York')."
                            " Defaults to owner's timezone if not specified."
                        ),
                    },
                },
                "required": [],
            },
            handler=get_current_time,
        ),
        ToolDef(
            name="convert_time",
            description="Convert a time from one timezone to another.",
            input_schema={
                "type": "object",
                "properties": {
                    "time_str": {
                        "type": "string",
                        "description": (
                            "Time to convert (e.g. '2025-03-02 14:00', '14:00', '2:00 PM')."
                        ),
                    },
                    "from_timezone": {
                        "type": "string",
                        "description": "Source IANA timezone (e.g. 'America/New_York').",
                    },
                    "to_timezone": {
                        "type": "string",
                        "description": "Target IANA timezone (e.g. 'Europe/London').",
                    },
                },
                "required": ["time_str", "from_timezone", "to_timezone"],
            },
            handler=convert_time,
        ),
    ]
