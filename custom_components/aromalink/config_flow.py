import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN
from .aromalink_api import AromaLinkClient

_LOGGER = logging.getLogger(__name__)

class AromaLinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Aroma-Link Diffuser integration."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}

        if user_input is not None:
            username = user_input["username"]
            password = user_input["password"]

            client = AromaLinkClient(username, password)

            try:
                login_success = await client.login()  # Remove async_add_executor_job
                if login_success:
                    devices = await client.get_devices()  # Remove async_add_executor_job

                    if not devices:
                        errors["base"] = "no_devices"
                    else:
                        # Convert user_id to string for unique_id
                        await self.async_set_unique_id(str(client.user_id))
                        self._abort_if_unique_id_configured()

                        return self.async_create_entry(
                            title=username,
                            data={
                                "username": username,
                                "user_id": client.user_id,
                                "access_token": client.access_token,
                                # Do not store device_id here
                            },
                        )
                else:
                    errors["base"] = "auth_failed"
            except Exception as e:
                _LOGGER.exception("Unexpected error during login: %s", e)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("username"): str,
                vol.Required("password"): str,
            }),
            errors=errors,
        )