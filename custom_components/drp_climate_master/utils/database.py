
import sqlite3
from threading import Lock
from typing import Any
import logging

from .helpers import is_number

_LOGGER = logging.getLogger(__name__)

db_path = "/config/home-assistant_v2.db"
db_lock = Lock()

def enquiry_entity_seconds_in_state(entity_id: str, state) -> dict[str, Any] | None:
    """ ... """
    with db_lock:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        query = """
            WITH entities AS (
            SELECT metadata_id
                FROM states_meta sm
                WHERE entity_id = ?
            )
            SELECT 
                (strftime('%s', DATETIME('now')) - strftime('%s', MAX(DATETIME(a.last_reported_ts, 'unixepoch')))) AS time_r_difference_seconds,
                (strftime('%s', DATETIME('now')) - strftime('%s', MAX(DATETIME(a.last_updated_ts, 'unixepoch')))) AS time_u_difference_seconds
            FROM states a
            JOIN entities b ON a.metadata_id = b.metadata_id
            WHERE a.state = ?
        """
        cursor.execute( query, (entity_id, state) )
        result = cursor.fetchone()
        conn.close()
        # _LOGGER.debug( 'enquiry_entity_seconds_in_state - entity %s state %s result %s', entity_id, str(state), str(result) )
        obj = {}
        if is_number( result[0] ):
            obj['reported'] = result[0]
        if is_number( result[1] ):
            obj['updated'] = result[1]
        return (obj if obj else None)
    
def enquiry_entity_in_state_last_minutes(entity_id: str, state, minutes):
    """ ... """
    with db_lock:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        query = """
          WITH entities AS (
            SELECT metadata_id
              FROM states_meta sm
              WHERE entity_id = ?
          )
          SELECT count(*) as states
          FROM states a
          JOIN entities b ON a.metadata_id = b.metadata_id
          WHERE a.state = ?
          AND DATETIME(a.last_reported_ts, 'unixepoch') > DATETIME('now', ?)
        """
        cursor.execute( query, (entity_id, state, "-" + minutes + " minutes") )
        result = cursor.fetchone()
        conn.close()
        # _LOGGER.info( 'enquiry_entity_in_state_last_minutes %s is %s last %s minutes.', entity_id, state, minutes )
        return result[0]
    
def enquiry_entity_for_time_in_maxi_value(entity_id):
    with db_lock:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        query = """
WITH entities AS (
    SELECT metadata_id
    FROM states_meta
    WHERE entity_id = ?
),
filtered_states AS (
    SELECT
        DATETIME(last_reported_ts, 'unixepoch') AS last_reported_ts,
        CAST(state AS FLOAT) AS state,
        DATE(DATETIME(last_reported_ts, 'unixepoch')) AS day,
        (strftime('%H', last_reported_ts, 'unixepoch') * 3600 +
         strftime('%M', last_reported_ts, 'unixepoch') * 60 +
         strftime('%S', last_reported_ts, 'unixepoch')) AS seconds_since_midnight
    FROM
        states a
    JOIN entities b ON a.metadata_id = b.metadata_id
    WHERE
        state NOT LIKE 'unknown'
        AND state NOT LIKE 'unavailable'
        AND TIME(DATETIME(last_reported_ts, 'unixepoch')) BETWEEN '10:00:00' AND '16:00:00'
),
daily_max AS (
    SELECT
        day,
        MAX(state) AS max_value
    FROM
        filtered_states
    GROUP BY
        day
),
average_time AS (
    SELECT
        AVG(seconds_since_midnight) AS avg_seconds
    FROM
        filtered_states f
    JOIN
        daily_max d ON f.day = d.day AND f.state = d.max_value
)
SELECT
    CAST(avg_seconds / 3600 AS INT) AS avg_hours,
    CAST((avg_seconds % 3600) / 60 AS INT) AS avg_minutes,
    CAST(avg_seconds % 60 AS INT) AS avg_seconds
FROM
    average_time
        """
        cursor.execute( query, (entity_id,) )
        result = cursor.fetchone()
        conn.close()
        # _LOGGER.info( 'enquiry_entity_in_state_last_minutes %s is %s last %s minutes.', entity_id, state, minutes )
        return result

def enquiry_entity_state_transition_minutes(entity_id, from_state, to_state):
    with db_lock:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        query = """
        WITH entities AS (
            SELECT metadata_id
            FROM states_meta
            WHERE entity_id = ?
        ),
        state_changes AS (
            SELECT 
                a.metadata_id, 
                a.state, 
                DATETIME(a.last_reported_ts, 'unixepoch') AS timestamp,
                LAG(state) OVER (PARTITION BY a.metadata_id ORDER BY a.last_reported_ts) AS previous_state,
                LAG(DATETIME(a.last_reported_ts, 'unixepoch')) OVER (PARTITION BY a.metadata_id ORDER BY a.last_reported_ts) AS previous_timestamp
            FROM states a
            JOIN entities b ON a.metadata_id = b.metadata_id
            WHERE a.state IN ('on', 'off')
        )
        SELECT 
            (JULIANDAY('now') - JULIANDAY(previous_timestamp)) * 1440 AS minutes_since_last_on_to_off
        FROM state_changes
        WHERE previous_state = ? and state = ?
        ORDER BY previous_timestamp DESC
        LIMIT 1
        """
        cursor.execute( query, (entity_id, from_state, to_state) )
        result = cursor.fetchone()
        conn.close()
        # _LOGGER.info( 'enquiry_entity_in_state_last_minutes %s is %s last %s minutes.', entity_id, state, minutes )
        return result[0]
