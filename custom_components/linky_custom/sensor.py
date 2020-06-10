"""
Support for Linky.

For more details about this component, please refer to the documentation at
https://home-assistant.io/components/sensor.linky/
"""
import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_TIMEOUT,
    STATE_UNAVAILABLE,
    CONF_NAME,
    ENERGY_KILO_WATT_HOUR,
    ATTR_ATTRIBUTION,
)
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle
from pylinky.client import DAILY, MONTHLY, YEARLY
import homeassistant.helpers.config_validation as cv


KILOWATT_HOUR = "kWh"
DEFAULT_NAME = "Linky"
ATTRIBUTION = "Data provided by Enedis"

ICON_ENERGY = "mdi:flash"

PEAK_HOURS = "peak_hours"
PEAK_HOURS_COST = "peak_hours_cost"
OFFPEAK_HOURS_COST = "offpeak_hours_cost"

CONSUMPTION = "conso"
TIME = "time"

INDEX_CURRENT = -1
INDEX_LAST = -2

REQUIREMENTS = ["pylinky==0.3.3"]
_LOGGER = logging.getLogger(__name__)

MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=30)
SCAN_INTERVAL = timedelta(minutes=30)
DEFAULT_TIMEOUT = 10

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(PEAK_HOURS): vol.All(cv.ensure_list),
        vol.Required(PEAK_HOURS_COST): vol.Coerce(float),
        vol.Required(OFFPEAK_HOURS_COST): vol.Coerce(float),
    }
)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Linky sensor."""
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)
    timeout = config.get(CONF_TIMEOUT)
    name = config.get(CONF_NAME)
    peak_hours = config.get(PEAK_HOURS)
    peak_hours_cost = config.get(PEAK_HOURS_COST)
    offpeak_hours_cost = config.get(OFFPEAK_HOURS_COST)

    linky_data = LinkyData(username, password, timeout)
    linky_data.update()

    add_entities(
        [
            LinkySensor(name, linky_data, peak_hours, peak_hours_cost, offpeak_hours_cost),
            SimpleLinkySensor("Linky current month", linky_data, MONTHLY, INDEX_CURRENT),
            SimpleLinkySensor("Linky last month", linky_data, MONTHLY, INDEX_LAST),
            SimpleLinkySensor("Linky current year", linky_data, YEARLY, INDEX_CURRENT),
            SimpleLinkySensor("Linky last year", linky_data, YEARLY, INDEX_LAST),
        ]
    , True)
    return True


class LinkySensor(Entity):
    """Representation of a sensor entity for Linky."""

    def __init__(
        self, name, linky_data, peak_hours, peak_hours_cost, offpeak_hours_cost
    ):
        """Initialize the sensor."""
        self._name = name
        self._peak_hours = peak_hours
        self._peak_hours_cost = peak_hours_cost
        self._offpeak_hours_cost = offpeak_hours_cost
        self._unit = KILOWATT_HOUR
        self._icon = ICON_ENERGY
        self._lk = linky_data
        self._state = None
        self._attributes = {}
        self.update()

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def unit_of_measurement(self):
        """Return the unit the value is expressed in."""
        return self._unit

    @property
    def state_attributes(self):
        """Return the state attributes."""
        return self._attributes

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return self._icon

    @Throttle(SCAN_INTERVAL)
    def update(self):
        """Fetch new state data for the sensor."""
        _LOGGER.debug("Start of update of linky data")
        self._lk.update()
        if not self._lk.success:
            self._state = STATE_UNAVAILABLE
            self._attributes = []
            return

        self._state = self._lk.daily[1][CONSUMPTION]
        self._attributes["daily"] = [d[CONSUMPTION] for d in self._lk.daily]
        
        if len(self._lk.halfhourly) > 0:
            self._attributes["halfhourly"] = [d[CONSUMPTION] for d in self._lk.halfhourly]
            self._attributes["peak_hours"] = (
                sum(
                    [
                        d[CONSUMPTION]
                        if any([_between(h[0], h[1], d[TIME]) for h in self._peak_hours])
                        else 0
                        for d in self._lk.halfhourly
                    ]
                )
                / 2
            )
            # From kW for 30 minutes to kWh
            self._attributes["offpeak_hours"] = (
                sum(
                    [
                        0
                        if any([_between(h[0], h[1], d[TIME]) for h in self._peak_hours])
                        else d[CONSUMPTION]
                        for d in self._lk.halfhourly
                    ]
                )
                / 2
            )
            # From kW for 30 minutes to kWh
            self._attributes["peak_offpeak_percent"] = (
                self._attributes["peak_hours"] * 100
            ) / (self._attributes["peak_hours"] + self._attributes["offpeak_hours"])

            self._attributes["daily_cost"] = (
                self._peak_hours_cost * self._attributes["peak_hours"]
                + self._offpeak_hours_cost * self._attributes["offpeak_hours"]
            )
        else: 
            _LOGGER.warning('Hourly data not available. Please check hourly activation on enedis.fr')
            self._attributes["halfhourly"] = []
            self._attributes["peak_hours"] = 0
            self._attributes["offpeak_hours"] = 0
            self._attributes["peak_offpeak_percent"] = 0
            self._attributes["daily_cost"] = 0

        if self._lk.compare_month == 0:
            self._attributes["monthly_evolution"] = 0
        else:
            _LOGGER.debug(
                "monthly consumption:%s / last year monthly consumption:%s",
                self._lk.monthly[0][CONSUMPTION],
                self._lk.compare_month,
            )
            self._attributes["monthly_evolution"] = (
                self._lk.monthly[0][CONSUMPTION] / self._lk.compare_month - 1
            ) * 100
        _LOGGER.debug("Computed values: %s", str(self._attributes))

def _hour_to_min(hour):
    return sum(map(lambda x, y: int(x) * y, hour.split(":"), [60, 1]))


def _between(start, end, hour):
    min_start = _hour_to_min(start)
    min_end = _hour_to_min(end)
    min_hour = _hour_to_min(hour)
    return (
        min_start <= min_hour <= min_end
        if min_start < min_end
        else min_start >= min_hour >= min_end
    )

class SimpleLinkySensor(Entity):
    """Representation of a sensor entity for Linky."""

    def __init__(self, name, linkyData, scale, when):
        """Initialize the sensor."""
        self._name = name
        self._account = linkyData
        self._username = linkyData.username
        self._time = None
        self._consumption = None
        self._scale = scale
        self._when = when

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._consumption

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return ENERGY_KILO_WATT_HOUR

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return ICON_ENERGY

    @property
    def device_state_attributes(self):
        """Return the state attributes of the sensor."""
        return {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            'time': self._time,
            CONF_USERNAME: self._username
        }

    def update(self):
        """Retreive the new data for the sensor."""
        # Verify data exist !
        if self._account.data is None:
            return
        
        data = self._account.data[self._scale][self._when]
        self._consumption = data[CONSUMPTION]
        self._time = data[TIME]

        if self._scale is not YEARLY:
            year_index = INDEX_CURRENT
            if self._time.endswith("Dec"):
                year_index = INDEX_LAST
            self._time += ' ' + self._account.data[YEARLY][year_index][TIME]

class LinkyData:
    """The class for handling the data retrieval."""

    def __init__(self, username, password, timeout):
        """Initialize the data object."""
        self._username = username
        self._password = password
        self._timeout = timeout
        self.client = {}
        self.data = {}
        self.halfhourly = []
        self.daily = []
        self.monthly = []
        self.yearly = []
        self.compare_month = []
        self.success = False
    
    @property
    def username(self):
        """Return the username."""
        return self._username

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def _fetch_data(self):
        """Fetch latest data from Linky."""
        from pylinky.exceptions import PyLinkyAccessException, PyLinkyEnedisException, PyLinkyMaintenanceException, PyLinkyWrongLoginException
        from pylinky import LinkyClient
        from datetime import date
        from dateutil.relativedelta import relativedelta

        try:
            self.client = LinkyClient(
                self._username, self._password, None, self._timeout
            )
            self.client.login()
            self.client.fetch_data()
            _LOGGER.info("Connected to Enedis server successfully.")
            self.data = self.client.get_data()
            today = date.today()
            # Get partial CONSUMPTION of the same month last year
            self.compare_month = sum(
                [
                    d[CONSUMPTION]
                    for d in self.client.format_data(
                        self.client.get_data_per_period(
                            "monthly",
                            today.replace(day=1) - relativedelta(months=12),
                            today - relativedelta(months=12),
                        )
                    )
                ]
            )
            _LOGGER.info(
                "Same month last year (from 1st to same day): %s",
                str(self.compare_month),
            )
        except PyLinkyAccessException as accessExp:
            reason = "(verify your login password):"
            _LOGGER.warning("Unable to fetch Linky data %s %s", reason, accessExp)
            return False
        except PyLinkyEnedisException as enedisExp:
            reason = "(unknown exception):"
            _LOGGER.warning("Unable to fetch Linky data %s %s", reason, enedisExp)
            return False
        except PyLinkyMaintenanceException as maintenanceExp:
            reason = "(verify your login password):"
            _LOGGER.warning("Unable to fetch Linky data %s %s", reason, maintenanceExp)
            return False
        except PyLinkyWrongLoginException as accessExp:
            reason = "(your login is wrong ...):"
            _LOGGER.warning("Unable to fetch Linky data %s %s", reason, accessExp)
            return False
        return True

    def update(self):
        """Return the latest collected data from Linky."""
        self._fetch_data()
        if not self.data:
            return
        _LOGGER.debug("Linky data retrieved: %s", str(self.data))
        self.halfhourly = list(reversed(self.data["hourly"]))
        self.daily = list(reversed(self.data["daily"]))
        self.monthly = list(reversed(self.data["monthly"]))
        self.yearly = list(reversed(self.data["yearly"]))
        self.success = True