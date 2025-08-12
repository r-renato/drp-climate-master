
import asyncio
from functools import partial

from influxdb_client import InfluxDBClient as InfluxDBClientV2 # type: ignore
from influxdb_client.client.write_api import ASYNCHRONOUS, SYNCHRONOUS
from influxdb_client.rest import ApiException

import logging

_LOGGER = logging.getLogger(__name__)

class InfluxClient:
    def __init__(
        self,
        organization: str,
        bucket: str,
        url: str,
        token: str,
    ):
        """ """
        self._bucket = bucket
        self._influx_client = InfluxDBClientV2(
            url=url,
            token=token,
            org=organization,
        )

    async def async_query_entity_bias(
        self,
        entity_id: str,
        date_start: str,
        date_stop: str,
        ideal_temp: float,
    ) -> float | None:
        """
        Query the average entity bias (difference from ideal) for a specific device 
        over a defined time range.

        :param entity_id: entity_id of the entity sensor (e.g., 'sensor.ambient_home_current_temperature')
        :param start: ISO 8601 formatted start timestamp (e.g., '2024-12-01T00:00:00Z')
        :param stop: ISO 8601 formatted stop timestamp (e.g., '2025-03-01T00:00:00Z')
        :param ideal_temp: The target ideal entity to compare against (default: 25.5)
        :return: Average entity bias (°C) or None if no data
        """
        query = f'''
            from(bucket: "{self._bucket}")
            |> range(start: {date_start}, stop: {date_stop})
            |> filter(fn: (r) => r["_measurement"] == "°C")
            |> filter(fn: (r) => r["entity_id"] !~ /outdoor(.*)/ and r["entity_id"] !~ /electric(.*)/)
            |> filter(fn: (r) => r["_field"] == "value")
            |> filter(fn: (r) => r["entity_id"] =~ /^{entity_id}(.*)/)
            |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
            |> map(fn: (r) => ({{ r with _value: r._value - {ideal_temp} }}))
            |> mean(column: "_value")
            |> yield(name: "winter_bias_temperature")
        '''

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, partial(self._influx_client.query_api().query, query))
            values = [record.get_value() for table in result for record in table.records]
            return values[0] if values else None
        except Exception as e:
            _LOGGER.warning(f"InfluxDB query failed: {e}")
            return None
