from httpx import AsyncClient
from urllib.parse import urlparse


def asgi_proxy(backend, log=None):
    backend_host = urlparse(backend).netloc

    async def asgi_proxy(scope, receive, send):
        assert scope["type"] == "http"

        method = scope["method"]
        path = scope["path"]
        query_string = scope["query_string"].decode()

        # Forward headers
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        # Replace host header
        headers["host"] = backend_host.encode()

        url_bits = [backend, path]
        if query_string:
            url_bits.extend(("?", query_string))
        url = "".join(url_bits)

        # Receive request body if any
        more_body = True
        body = b""
        while more_body:
            message = await receive()
            body += message.get("body", b"")
            more_body = message.get("more_body", False)

        async with AsyncClient() as client:
            try:
                # Stream it, in case of long streaming responses
                async with client.stream(
                    method, url, data=body or None, headers=headers
                ) as resp:
                    if log:
                        log.info(f"Request: {method} {url}")
                        log.info(f"Response: {resp.status_code} {resp.reason_phrase}")
                    # Start the response
                    await send(
                        {
                            "type": "http.response.start",
                            "status": resp.status_code,
                            "headers": [
                                (k.encode(), v.encode())
                                for k, v in resp.headers.items()
                            ],
                        }
                    )
                    # Stream the content
                    try:
                        # aiter_raw not aiter_bytes because we don't want
                        # content decoding to have been applied
                        async for chunk in resp.aiter_raw():
                            await send(
                                {
                                    "type": "http.response.body",
                                    "body": chunk,
                                    "more_body": True,
                                }
                            )
                    except Exception as e:
                        # The client has disconnected
                        if log:
                            log.info(
                                f"Client disconnected: {e.__class__.__name__}: {e}"
                            )
                        await send({"type": "http.response.body", "more_body": False})
                        return

                    await send({"type": "http.response.body", "more_body": False})
            except Exception as e:
                # Handle any errors during the request
                if log:
                    log.error(f"An error occurred: {e.__class__.__name__}: {e}")
                return

    return asgi_proxy
