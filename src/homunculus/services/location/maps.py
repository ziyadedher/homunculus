import httpx


async def get_travel_time(
    api_key: str,
    origin: str,
    destination: str,
    mode: str = "driving",
) -> dict[str, object]:
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "key": api_key,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        data = resp.json()

    if data.get("status") != "OK" or not data.get("routes"):
        return {"error": data.get("status", "NO_RESULTS"), "duration": None, "distance": None}

    leg = data["routes"][0]["legs"][0]
    return {
        "duration": leg["duration"]["text"],
        "duration_seconds": leg["duration"]["value"],
        "distance": leg["distance"]["text"],
        "distance_meters": leg["distance"]["value"],
    }


async def search_places(
    api_key: str,
    query: str,
    location: str | None = None,
    radius: int = 5000,
) -> list[dict[str, object]]:
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params: dict[str, str | int] = {
        "query": query,
        "key": api_key,
    }
    if location:
        params["location"] = location
        params["radius"] = radius

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        data = resp.json()

    results = data.get("results", [])
    return [
        {
            "name": place.get("name"),
            "address": place.get("formatted_address"),
            "rating": place.get("rating"),
            "location": place.get("geometry", {}).get("location"),
        }
        for place in results[:10]
    ]
