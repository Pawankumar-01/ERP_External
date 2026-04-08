"""
LiveKit Client
Wraps the LiveKit Python SDK for room creation and JWT generation.
All LiveKit-specific logic is isolated here so the rest of the app
never imports the SDK directly.
"""

from livekit import api as livekit_api
from livekit.api import AccessToken, VideoGrants
import aiohttp
import logging
from app.config.settings import settings

logger = logging.getLogger(__name__)


class LiveKitClient:
    """
    Thin async wrapper around LiveKit REST API and token generation.
    Uses the official livekit-server-sdk-python package.
    """

    def __init__(self):
        self.api_key = settings.LIVEKIT_API_KEY
        self.api_secret = settings.LIVEKIT_API_SECRET
        self.url = settings.LIVEKIT_URL

    def generate_token(
        self,
        room_name: str,
        identity: str,
        display_name: str,
        is_host: bool = False,
    ) -> str:
        """
        Generate a signed LiveKit JWT.

        - Hosts receive canPublish=True, canSubscribe=True, roomAdmin=True
        - Patients receive canPublish=True (they can speak/show video), canSubscribe=True
        """
        grants = VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,          # Both host and patient can publish
            can_subscribe=True,
            can_publish_data=True,
            room_admin=is_host,        # Only host can manage room
        )

        token = (
            AccessToken(self.api_key, self.api_secret)
            .with_identity(identity)
            .with_name(display_name)
            .with_grants(grants)
            .to_jwt()
        )
        logger.debug(f"Token generated for {identity} in room {room_name}")
        return token

    async def create_room(self, room_name: str, empty_timeout: int = 7200) -> dict:
        """
        Create a LiveKit room via REST API.
        empty_timeout: seconds before empty room is automatically deleted (default 2h).
        """
        lkapi = livekit_api.LiveKitAPI(
            url=self.url.replace("wss://", "https://"),
            api_key=self.api_key,
            api_secret=self.api_secret,
        )
        try:
            room = await lkapi.room.create_room(
                livekit_api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=empty_timeout,
                    max_participants=100,
                )
            )
            logger.info(f"LiveKit room created: {room_name}")
            return {"room_name": room_name, "sid": room.sid}
        except Exception as e:
            logger.error(f"Failed to create LiveKit room {room_name}: {e}")
            raise
        finally:
            await lkapi.aclose()

    async def delete_room(self, room_name: str) -> None:
        """Delete a LiveKit room after session ends."""
        lkapi = livekit_api.LiveKitAPI(
            url=self.url.replace("wss://", "https://"),
            api_key=self.api_key,
            api_secret=self.api_secret,
        )
        try:
            await lkapi.room.delete_room(
                livekit_api.DeleteRoomRequest(room=room_name)
            )
            logger.info(f"LiveKit room deleted: {room_name}")
        except Exception as e:
            logger.warning(f"Failed to delete room {room_name}: {e}")
        finally:
            await lkapi.aclose()

    def verify_webhook(self, body: bytes, auth_header: str) -> dict:
        try:
            from livekit.api import TokenVerifier, WebhookReceiver
            verifier = TokenVerifier(self.api_key, self.api_secret)
            receiver = WebhookReceiver(verifier)
            event = receiver.receive(body.decode(), auth_header)
            return event
        except Exception as e:
            logger.error(f"Webhook verification failed: {e}")
            raise ValueError(f"Invalid webhook signature: {e}")


livekit_client = LiveKitClient()
