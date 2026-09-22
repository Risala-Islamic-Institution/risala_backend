"""
High-performance rate-limiting throttles using local memory caching.
"""

from django.core.cache import caches
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


def get_throttling_cache():
    """Retrieve the dedicated in-memory throttle cache, falling back to default."""
    try:
        return caches["throttles"]
    except Exception:
        return caches["default"]


class FastAnonRateThrottle(AnonRateThrottle):
    """
    AnonRateThrottle backed by local worker memory to eliminate 200ms+ remote Redis network hops.
    """

    @property
    def cache(self):
        return get_throttling_cache()


class FastUserRateThrottle(UserRateThrottle):
    """
    UserRateThrottle backed by local worker memory to eliminate 200ms+ remote Redis network hops.
    """

    @property
    def cache(self):
        return get_throttling_cache()
