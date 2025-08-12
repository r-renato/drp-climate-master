from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries

from custom_components.drp_climate_master.utils.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# @config_entries.HANDLERS.register(DOMAIN)
class HomeClimateMasterConfigFlow( config_entries.ConfigFlow, domain=DOMAIN ):

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="DRP Home Climate Master", data={})