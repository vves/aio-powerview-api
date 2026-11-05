"""Class containing the async http methods."""

import asyncio
import logging
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from aiopvapi.helpers.position_batcher import PositionBatcher

_LOGGER = logging.getLogger(__name__)


class PvApiError(Exception):
    """General Api error."""


class PvApiResponseStatusError(PvApiError):
    """Wrong http response error."""


class PvApiMaintenance(PvApiError):
    """Hub is undergoing maintenance."""


class PvApiConnectionError(PvApiError):
    """Problem connecting to PowerView Hub."""


class PvApiEmptyData(PvApiError):
    """PowerView Hub returned empty data."""


class PvApiCommandError(PvApiError):
    """A gateway rejected a position command for a shade."""


class AioRequest:
    """Request class managing Powerview Hub connection."""

    def __init__(
        self,
        hub_ip,
        loop=None,
        websession=None,
        timeout: int = 15,
        api_version: int | None = None,
    ) -> None:
        """Initialize request class."""
        self.hub_ip = hub_ip
        self._timeout = timeout
        if loop:
            self.loop = loop
        else:
            self.loop = asyncio.new_event_loop()
        if websession:
            self.websession = websession
        else:
            self.websession = aiohttp.ClientSession(loop=self.loop)
        self.api_version: int | None = api_version
        self._last_request_status: int = 0
        self._position_batcher: PositionBatcher | None = None
        _LOGGER.debug("Powerview api version: %s", self.api_version)

    @property
    def api_path(self) -> str:
        """Return the initial api call path."""
        if self.api_version and self.api_version >= 3:
            return "home"
        return "api"

    async def check_response(self, response, valid_response_codes):
        """Check the response for correctness."""
        _val = None
        if response.status == 403 and self._last_request_status == 423:
            # if last status was hub undergoing maint then it is common
            # on reboot for a 403 response. Generally this should raise
            # PvApiResponseStatusError but as this is unavoidable we
            # class this situation as still undergoing maintenance
            _val = False
        elif response.status in [204, 423]:
            # 423 hub under maintenance, returns data, but not shade
            _val = True
        elif response.status in valid_response_codes:
            _val = await response.json()

        # store the status for next check
        self._last_request_status = response.status

        # raise a maintenance error
        if isinstance(_val, bool):
            raise PvApiMaintenance("Powerview Hub is undergoing maintenance")

        # if none of the above checks passed, raise a response error
        if _val is None:
            raise PvApiResponseStatusError(response.status)

        # finally, return the result
        return _val

    async def get(
        self,
        url: str,
        params: str | None = None,
        suppress_timeout: bool = False,
        **kwargs,
    ) -> dict:
        """Get a resource.

        :param url: The URL to fetch.
        :param params: Dictionary or bytes to be sent in the query string of the new request
                    (optional).
        :param suppress_timeout: Stermine if timeouts will return an error
        :param kwargs: Keyword arguments to be passed to aiohttp ClientSession get method.
                    For example, timeout can be passed as kwargs.
        :return: A dictionary representing the JSON response.
        """
        response = None
        try:
            timeout = kwargs.pop("timeout", None) or self._timeout
            _LOGGER.debug(
                "Sending GET request to: %s params: %s timeout: %s kwargs: %s",
                url,
                params,
                timeout,
                kwargs,
            )
            response = await self.websession.get(
                url, params=params, timeout=timeout, **kwargs
            )
            return await self.check_response(response, [200, 204])
        except TimeoutError as error:
            if suppress_timeout:
                _LOGGER.debug("Timeout occurred but was suppressed: %s", error)
                return None
            raise PvApiConnectionError(
                "Timeout in communicating with PowerView Hub"
            ) from error
        except aiohttp.ClientError as error:
            raise PvApiConnectionError(
                "Failed to communicate with PowerView Hub"
            ) from error
        finally:
            if response is not None:
                await response.release()

    async def post(
        self,
        url: str,
        data: dict | None = None,
        suppress_timeout: bool = False,
        **kwargs,
    ):
        """Post a resource update.

        :param url: The URL to fetch.
        :param data: Dictionary later converted to json. Sent in the request
        :param suppress_timeout: Stermine if timeouts will return an error
        :param kwargs: Keyword arguments to be passed to aiohttp ClientSession get method.
                    For example, timeout can be passed as kwargs.
        :return: A dictionary representing the JSON response.
        """
        response = None
        try:
            timeout = kwargs.pop("timeout", None) or self._timeout
            _LOGGER.debug(
                "Sending POST request to: %s data: %s timeout: %s kwargs: %s",
                url,
                data,
                timeout,
                kwargs,
            )
            response = await self.websession.post(
                url, json=data, timeout=timeout, **kwargs
            )
            return await self.check_response(response, [200, 201])
        except TimeoutError as error:
            if suppress_timeout:
                _LOGGER.debug("Timeout occurred but was suppressed: %s", error)
                return None
            raise PvApiConnectionError(
                "Timeout in communicating with PowerView Hub"
            ) from error
        except aiohttp.ClientError as error:
            raise PvApiConnectionError(
                "Failed to communicate with PowerView Hub"
            ) from error
        finally:
            if response is not None:
                await response.release()

    def enable_position_batching(self, lock: asyncio.Lock | None = None) -> None:
        """Coalesce simultaneous Gen3 position writes using a shared gateway lock."""
        if self.api_version != 3:
            raise ValueError("Position batching requires a Gen3 gateway")
        if self._position_batcher is None:
            from aiopvapi.helpers.position_batcher import PositionBatcher

            self._position_batcher = PositionBatcher(self._put, lock)

    def disable_position_batching(self) -> None:
        """Release queued writes when the gateway connection is unloaded."""
        if self._position_batcher is not None:
            self._position_batcher.close()
            self._position_batcher = None

    async def put(
        self,
        url: str,
        data: dict | None = None,
        params=None,
        suppress_timeout: bool = False,
        **kwargs,
    ):
        """Queue compatible shade commands, or send other writes directly."""
        if (
            self._position_batcher is not None
            and isinstance(params, dict)
            and set(params) == {"ids"}
            and isinstance(params["ids"], int)
            and url.endswith(("/home/shades/positions", "/home/shades/stop"))
        ):
            return await self._position_batcher.submit(
                url,
                params["ids"],
                data,
                {"suppress_timeout": suppress_timeout, **kwargs},
                combine=url.endswith("/positions"),
            )
        return await self._put(
            url, data=data, params=params, suppress_timeout=suppress_timeout, **kwargs
        )

    async def _put(
        self,
        url: str,
        data: dict | None = None,
        params=None,
        suppress_timeout: bool = False,
        expect_no_content: bool = False,
        **kwargs,
    ):
        """Do a put request.

        :param url: The URL to fetch.
        :param data: Dictionary later converted to json. Sent in the request
        :param params: Dictionary or bytes to be sent in the query string of the new request
                    (optional).
        :param suppress_timeout: Stermine if timeouts will return an error
        :param kwargs: Keyword arguments to be passed to aiohttp ClientSession get method.
                    For example, timeout can be passed as kwargs.
        :param expect_no_content: Accept HTTP 204 as success for this operation.
        :return: A dictionary representing the JSON response.
        """
        response = None
        try:
            timeout = kwargs.pop("timeout", None) or self._timeout
            _LOGGER.debug(
                "Sending PUT request to: %s params: %s data: %s timeout: %s kwargs: %s",
                url,
                params,
                data,
                timeout,
                kwargs,
            )
            response = await self.websession.put(
                url, json=data, params=params, timeout=timeout, **kwargs
            )
            if response.status == 204 and expect_no_content:
                self._last_request_status = response.status
                return None
            return await self.check_response(response, [200, 204])
        except TimeoutError as error:
            if suppress_timeout:
                _LOGGER.debug("Timeout occurred but was suppressed: %s", error)
                return None
            raise PvApiConnectionError(
                "Timeout in communicating with PowerView Hub"
            ) from error
        except aiohttp.ClientError as error:
            raise PvApiConnectionError(
                "Failed to communicate with PowerView Hub"
            ) from error
        finally:
            if response is not None:
                await response.release()

    async def delete(
        self,
        url: str,
        params: dict | None = None,
        suppress_timeout: bool = False,
        **kwargs,
    ):
        """Delete a resource.

        :param url: Endpoint
        :param params: parameters
        :return: Response body

        :raises PvApiError when something is wrong.
        """
        response = None
        try:
            timeout = kwargs.pop("timeout", None) or self._timeout
            _LOGGER.debug(
                "Sending DELETE request to: %s params: %s timeout: %s kwargs: %s",
                url,
                params,
                timeout,
                kwargs,
            )
            response = await self.websession.delete(
                url, params=params, timeout=timeout, **kwargs
            )
            return await self.check_response(response, [200, 204])
        except TimeoutError as error:
            if suppress_timeout:
                _LOGGER.debug("Timeout occurred but was suppressed: %s", error)
                return None
            raise PvApiConnectionError(
                "Timeout in communicating with PowerView Hub"
            ) from error
        except aiohttp.ClientError as error:
            raise PvApiConnectionError(
                "Failed to communicate with PowerView Hub"
            ) from error
        finally:
            if response is not None:
                await response.release()
