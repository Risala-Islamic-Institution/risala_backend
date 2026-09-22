"""
High-performance authentication classes with lightweight in-memory caching.
"""

import logging
from django.core.cache import caches
from django.db.models.signals import post_delete
from django.dispatch import receiver
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token

logger = logging.getLogger(__name__)


def get_auth_cache():
    """Retrieve the dedicated in-memory cache for auth tokens, falling back to default."""
    try:
        return caches["auth_tokens"]
    except Exception:
        return caches["default"]


class CachedTokenAuthentication(TokenAuthentication):
    """
    Extends DRF TokenAuthentication by caching validated user instances for 3 minutes
    in worker-local RAM (LocMemCache). This completely eliminates remote Redis hops and
    database round-trips to the `authtoken_token` table on every single API request.
    """

    CACHE_TTL_SECONDS = 180

    def authenticate_credentials(self, key):
        cache_key = f"token_auth_user:{key}"
        auth_cache = get_auth_cache()
        cached_user = auth_cache.get(cache_key)
        if cached_user is not None:
            token = self.get_model()(key=key, user=cached_user)
            return (cached_user, token)

        user, token = super().authenticate_credentials(key)
        try:
            auth_cache.set(cache_key, user, timeout=self.CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.debug("Could not cache user token credentials: %s", exc)
        return (user, token)


@receiver(post_delete, sender=Token)
def invalidate_token_cache_on_logout(sender, instance, **kwargs):
    """Instantly remove token from cache upon logout so revocation is immediate."""
    try:
        get_auth_cache().delete(f"token_auth_user:{instance.key}")
    except Exception as exc:
        logger.debug("Could not invalidate token cache: %s", exc)
