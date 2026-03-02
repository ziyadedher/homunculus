import json

from homunculus.agent.tools.registry import ToolDef
from homunculus.services.location import maps


def make_location_tools(api_key: str) -> list[ToolDef]:
    async def get_travel_time(origin: str, destination: str, mode: str = "driving") -> str:
        result = await maps.get_travel_time(api_key, origin, destination, mode)
        return json.dumps(result)

    async def search_places(query: str, location: str | None = None, radius: int = 5000) -> str:
        results = await maps.search_places(api_key, query, location, radius)
        return json.dumps(results)

    return [
        ToolDef(
            name="get_travel_time",
            description="Get travel time and distance between two locations.",
            input_schema={
                "type": "object",
                "properties": {
                    "origin": {
                        "type": "string",
                        "description": "Starting location (address or place name)",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination location (address or place name)",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["driving", "walking", "bicycling", "transit"],
                        "description": "Travel mode (default: driving)",
                    },
                },
                "required": ["origin", "destination"],
            },
            handler=get_travel_time,
        ),
        ToolDef(
            name="search_places",
            description="Search for places (restaurants, venues, etc.) near a location.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g., 'coffee shop', 'Italian restaurant')",
                    },
                    "location": {
                        "type": "string",
                        "description": "Center location as 'lat,lng' (optional)",
                    },
                    "radius": {
                        "type": "integer",
                        "description": "Search radius in meters (default: 5000)",
                    },
                },
                "required": ["query"],
            },
            handler=search_places,
        ),
    ]
