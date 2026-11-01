"""Integration constants."""

DOMAIN = "sonoff_l2_ble"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_APIKEY = "device_apikey"
CONF_ACCOUNT_APIKEY = "account_apikey"
# Preserve the stored option key so existing explicit choices survive upgrades.
CONF_EFFECTS_ENABLED = "experimental_effects"
CONF_POLL_INTERVAL = "poll_interval"
DEFAULT_EFFECTS_ENABLED = True
DEFAULT_POLL_INTERVAL = 60
CONF_MTU_WRITES = "mtu_writes"
