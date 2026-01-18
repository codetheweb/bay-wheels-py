"""Authentication handling for Bay Wheels API."""

from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from .exceptions import AuthenticationError
from .models import TokenInfo

if TYPE_CHECKING:
    pass

# Client credentials for the Bay Wheels iOS app (public, embedded in app)
CLIENT_ID = "kN2nqhox8ySO"
CLIENT_SECRET = "K04achF9yqAh1KO6qCihWIARbfdrEJLU"

BASE_URL = "https://api.lyft.com"
USER_AGENT = "com.motivateco.gobike:iOS:26.2:2025.52.3.27469952"
USER_DEVICE = "iPhone14,3"

DEFAULT_TOKEN_PATH = Path.home() / ".bay_wheels_token"


class AuthManager:
    """Manages authentication with the Bay Wheels API."""

    def __init__(
        self,
        session: AsyncSession,
        token_path: Path | None = DEFAULT_TOKEN_PATH,
    ) -> None:
        """Initialize the auth manager.

        Args:
            session: The HTTP session to use for requests.
            token_path: Path to store/load tokens. Set to None to disable persistence.
        """
        self._session = session
        self._token_path = token_path
        self._token_info: TokenInfo | None = None

        # Generate persistent IDs for this session (app uses same IDs across requests)
        self._device_id = str(uuid.uuid4()).upper()
        self._bundle_id = str(uuid.uuid4()).upper()
        self._client_session_id = str(uuid.uuid4()).upper()

    @property
    def access_token(self) -> str | None:
        """Get the current access token, if available."""
        if self._token_info is None:
            return None
        return self._token_info.access_token

    @property
    def token_info(self) -> TokenInfo | None:
        """Get the current token info."""
        return self._token_info

    def set_token(self, token_info: TokenInfo) -> None:
        """Set the current token info."""
        self._token_info = token_info

    def load_token(self) -> TokenInfo | None:
        """Load token from the configured path.

        Returns:
            The loaded token info, or None if not found or invalid.
        """
        if self._token_path is None or not self._token_path.exists():
            return None

        try:
            data = json.loads(self._token_path.read_text())
            self._token_info = TokenInfo(**data)
            return self._token_info
        except (json.JSONDecodeError, ValueError):
            return None

    def save_token(self) -> None:
        """Save the current token to the configured path."""
        if self._token_path is None or self._token_info is None:
            return

        self._token_path.write_text(
            json.dumps(self._token_info.model_dump(), indent=2)
        )

    def clear_token(self) -> None:
        """Clear the saved token file."""
        if self._token_path is not None and self._token_path.exists():
            self._token_path.unlink()
        self._token_info = None

    def _get_basic_auth(self) -> str:
        """Get Basic auth header from client credentials."""
        credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
        return base64.b64encode(credentials.encode()).decode()

    def _get_session_header(self) -> str:
        """Generate the x-session header value."""
        session_data = {
            "j": self._device_id,  # device ID
            "i": False,  # is authenticated
            "b": self._bundle_id,  # bundle ID
            "e": "00000000-0000-0000-0000-000000000000",  # experiment ID
        }
        # Use separators to avoid spaces in the JSON (matches iOS app format)
        return base64.b64encode(
            json.dumps(session_data, separators=(",", ":")).encode()
        ).decode()

    def _get_common_headers(self) -> dict[str, str]:
        """Get common headers used in API requests."""
        return {
            "Host": "api.lyft.com",
            "Cookie": "",
            "upload-draft-interop-version": "6",
            "x-session": self._get_session_header(),
            "user-agent": USER_AGENT,
            "upload-complete": "?1",
            "user-device": USER_DEVICE,
            "x-design-id": "x",
            "x-timestamp-ms": str(int(time.time() * 1000)),
            "x-locale-language": "en",
            "priority": "u=3",
            "x-client-session-id": self._client_session_id,
            "x-device-density": "3.0",
            "accept-language": "en_US",
            "x-locale-region": "US",
            "x-distance-unit": "miles",
            "accept": "application/json",
            "x-timestamp-source": "system",
        }

    async def _get_anonymous_token(self) -> str:
        """Get an anonymous token using client credentials grant.

        Returns:
            The anonymous access token.

        Raises:
            AuthenticationError: If the request fails.
        """
        headers = self._get_common_headers()
        headers.update({
            "authorization": f"Basic {self._get_basic_auth()}",
            "content-type": "application/x-www-form-urlencoded; charset=utf-8",
        })

        response = await self._session.post(
            f"{BASE_URL}/oauth2/access_token",
            data="grant_type=client_credentials",
            headers=headers,
        )

        if response.status_code != 200:
            raise AuthenticationError(
                f"Failed to get anonymous token: {response.status_code}\n"
                f"Headers: {dict(response.headers)}\n"
                f"Body: {response.text}"
            )

        try:
            data = response.json()
            return data["access_token"]
        except (json.JSONDecodeError, KeyError) as e:
            raise AuthenticationError(f"Invalid anonymous token response: {e}")

    async def request_code(self, phone_number: str) -> None:
        """Request an SMS verification code.

        Args:
            phone_number: Phone number in E.164 format (e.g., +14155551234).

        Raises:
            AuthenticationError: If the request fails.
        """
        # Get anonymous token first (required for phoneauth)
        anon_token = await self._get_anonymous_token()

        headers = self._get_common_headers()
        headers.update({
            "authorization": f"Bearer {anon_token}",
            "content-type": "application/json",
            "x-idl-source": "pb.api.endpoints.v1.phone_auth.CreatePhoneAuthRequest",
            "accept": "application/x-protobuf,application/json",
        })

        response = await self._session.post(
            f"{BASE_URL}/v1/phoneauth",
            json={
                "phone_number": phone_number,
                "voice_verification": False,
            },
            headers=headers,
        )

        # API returns 202 Accepted on success
        if response.status_code != 202:
            raise AuthenticationError(
                f"Failed to request verification code: {response.status_code}\n"
                f"Headers: {dict(response.headers)}\n"
                f"Body: {response.text}"
            )

    async def login(
        self,
        phone_number: str,
        code: str,
        email: str | None = None,
    ) -> TokenInfo:
        """Exchange a verification code for an access token.

        Args:
            phone_number: Phone number in E.164 format.
            code: The SMS verification code.
            email: Email address for account verification challenge (if required).

        Returns:
            The token info containing the access token.

        Raises:
            AuthenticationError: If login fails. If email verification is required,
                the error message will contain "challenge_required" and the masked
                email hint.
        """
        headers = self._get_common_headers()
        headers.update({
            "authorization": f"Basic {self._get_basic_auth()}",
            "content-type": "application/x-www-form-urlencoded; charset=utf-8",
        })

        # URL-encode the form data (phone number has + that needs encoding)
        form_params: dict[str, str] = {
            "grant_type": "urn:lyft:oauth2:grant_type:phone",
            "phone_number": phone_number,
            "phone_code": code,
        }

        # Add email challenge response if provided
        if email is not None:
            form_params["challenge"] = "email_match"
            form_params["email"] = email

        form_data = urlencode(form_params)

        response = await self._session.post(
            f"{BASE_URL}/oauth2/access_token",
            data=form_data,
            headers=headers,
        )

        if response.status_code != 200:
            # Check if this is a challenge requirement
            try:
                error_data = response.json()
                if error_data.get("error") == "challenge_required":
                    challenges = error_data.get("challenges", [])
                    for challenge in challenges:
                        if challenge.get("identifier") == "email_match":
                            email_hint = challenge.get("data", "")
                            raise AuthenticationError(
                                f"Email verification required. "
                                f"Please provide the email matching: {email_hint}"
                            )
                    raise AuthenticationError(
                        f"Challenge required: {error_data.get('error_description', 'Unknown challenge')}"
                    )
            except json.JSONDecodeError:
                pass

            raise AuthenticationError(
                f"Login failed: {response.status_code}\n"
                f"Body: {response.text}"
            )

        try:
            data = response.json()
        except json.JSONDecodeError:
            raise AuthenticationError("Invalid response from auth server")

        # Calculate expiration time
        expires_in = data.get("expires_in")
        expires_at = time.time() + expires_in if expires_in else None

        self._token_info = TokenInfo(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            token_type=data.get("token_type", "Bearer"),
        )

        self.save_token()
        return self._token_info
