class MockTokeninfoResponse:
    """Mock aiohttp response for Google tokeninfo, usable as async context manager."""

    def __init__(self, status: int, email: str | None = None):
        self.status = status
        self._data = {"email": email} if email else {"error": "invalid_token"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: object):
        pass

    async def json(self):
        return self._data


class MockHttpSession:
    """Mock aiohttp.ClientSession that returns tokeninfo responses based on access token."""

    def __init__(self, tokeninfo_responses: dict[str, tuple[int, str | None]]):
        self._responses = tokeninfo_responses

    def get(self, url: str, params: dict[str, str] | None = None) -> MockTokeninfoResponse:
        token = params.get("access_token", "") if params else ""
        if token in self._responses:
            status, email = self._responses[token]
            return MockTokeninfoResponse(status, email)
        return MockTokeninfoResponse(400)
