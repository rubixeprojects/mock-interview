"""
Cache utilities for Internship Portal with comprehensive logging.

Provides:
- Cached queries with hit/miss tracking
- Automatic cache invalidation helpers
- Performance monitoring
- Error handling with fallbacks
"""

import logging
import functools
import time
from django.core.cache import cache
from django.conf import settings

try:
    from django_redis import get_redis_connection
except ImportError:
    get_redis_connection = None

# Get logger for cache operations
cache_logger = logging.getLogger('cache')
django_logger = logging.getLogger('django')

# Cache key prefixes for organization
CACHE_KEYS = {
    'TEAMS': 'teams:',
    'COURSES': 'courses:',
    'STUDENTS': 'students:',
    'DASHBOARD': 'dashboard:',
    'EVALUATIONS': 'evaluations:',
}

# Signal-based cache invalidation rules
# Maps model name → list of cache prefixes to invalidate
INVALIDATION_RULES = {
    'Team': ['teams:', 'dashboard:'],
    'TeamProject': ['teams:', 'dashboard:'],
    'Submission': ['dashboard:', 'evaluations:'],
    'Evaluation': ['dashboard:', 'evaluations:'],
    'Student': ['students:', 'teams:', 'dashboard:'],
    'Course': ['courses:'],
    'ProjectRegistry': ['projects:'],
}


class CacheMetrics:
    """Track cache performance metrics"""
    hits = 0
    misses = 0
    
    @classmethod
    def record_hit(cls):
        cls.hits += 1
    
    @classmethod
    def record_miss(cls):
        cls.misses += 1
    
    @classmethod
    def hit_rate(cls):
        total = cls.hits + cls.misses
        return (cls.hits / total * 100) if total > 0 else 0
    
    @classmethod
    def reset(cls):
        cls.hits = 0
        cls.misses = 0


def cached_query(timeout=300, key_prefix='', key_suffix=''):
    """
    Decorator to cache function results with comprehensive logging.
    
    Args:
        timeout: Cache TTL in seconds (default: 5 minutes)
        key_prefix: Prefix for cache key (e.g., 'teams:')
        key_suffix: Suffix for cache key (e.g., ':all')
    
    Example:
        @cached_query(timeout=600, key_prefix=CACHE_KEYS['TEAMS'])
        def get_all_teams():
            return Team.objects.all()
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Build cache key
            cache_key = f"{key_prefix}{func.__name__}{key_suffix}"
            
            # Try to get from cache
            cached_value = cache.get(cache_key)
            
            if cached_value is not None:
                CacheMetrics.record_hit()
                cache_logger.debug(
                    f"✅ CACHE HIT: {cache_key} | "
                    f"Hit Rate: {CacheMetrics.hit_rate():.1f}% "
                    f"({CacheMetrics.hits}H/{CacheMetrics.misses}M)"
                )
                return cached_value
            
            # Cache miss - fetch from source
            CacheMetrics.record_miss()
            start_time = time.time()
            
            try:
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time
                
                # Store in cache
                cache.set(cache_key, result, timeout)
                
                cache_logger.info(
                    f"❌ CACHE MISS & SET: {cache_key} | "
                    f"TTL: {timeout}s | "
                    f"Execution: {execution_time:.3f}s | "
                    f"Hit Rate: {CacheMetrics.hit_rate():.1f}%"
                )
                
                return result
                
            except Exception as e:
                execution_time = time.time() - start_time
                django_logger.error(
                    f"⚠️  CACHE ERROR in {cache_key}: {str(e)} | "
                    f"Execution: {execution_time:.3f}s",
                    exc_info=True
                )
                # Return uncached result on error
                return func(*args, **kwargs)
        
        return wrapper
    return decorator


def invalidate_cache(pattern=None, specific_key=None):
    """
    Invalidate cache by pattern or specific key with logging.
    
    Args:
        pattern: Wildcard pattern (e.g., 'teams:*') - uses SCAN
        specific_key: Exact cache key to delete
    
    Example:
        # Delete specific key
        invalidate_cache(specific_key='teams:get_all_teams')
        
        # Delete by pattern
        invalidate_cache(pattern='dashboard:*')
    """
    try:
        if specific_key:
            cache.delete(specific_key)
            cache_logger.info(f"🗑️  CACHE DELETED: {specific_key}")
            
        elif pattern:
            # Delete by pattern using redis connection
            if get_redis_connection:
                try:
                    redis_conn = get_redis_connection('default')
                    cursor = 0
                    count = 0
                    while True:
                        cursor, keys = redis_conn.scan(cursor, match=pattern, count=100)
                        if keys:
                            count += redis_conn.delete(*keys)
                        if cursor == 0:
                            break
                    cache_logger.info(
                        f"🗑️  CACHE INVALIDATED PATTERN: {pattern} | "
                        f"Keys deleted: {count}"
                    )
                except Exception as inner_e:
                    cache_logger.warning(
                        f"⚠️  Pattern deletion failed: {str(inner_e)}, "
                        f"falling back to cache.clear()"
                    )
            else:
                cache_logger.warning(
                    "⚠️  django-redis not available, cannot delete by pattern"
                )
        else:
            cache_logger.warning("⚠️  invalidate_cache called with no pattern or key")
            
    except Exception as e:
        django_logger.error(
            f"❌ CACHE INVALIDATION ERROR: {str(e)}",
            exc_info=True
        )


def invalidate_on_save(sender, instance, created, **kwargs):
    """
    Signal handler to invalidate cache when model is saved.
    
    Example:
        from django.db.models.signals import post_save
        from .models import Team
        
        post_save.connect(invalidate_on_save, sender=Team)
    """
    model_name = sender.__name__.lower()
    pattern = f"{model_name}:*"
    
    action = "created" if created else "updated"
    cache_logger.info(
        f"📝 Model {action}: {sender.__name__} (id={instance.id})"
    )
    
    invalidate_cache(pattern=pattern)


class CacheStats:
    """Get current cache statistics"""
    
    @staticmethod
    def get_stats():
        """Return cache performance statistics"""
        return {
            'hits': CacheMetrics.hits,
            'misses': CacheMetrics.misses,
            'hit_rate': f"{CacheMetrics.hit_rate():.2f}%",
            'total_requests': CacheMetrics.hits + CacheMetrics.misses,
        }
    
    @staticmethod
    def log_stats():
        """Log current statistics"""
        stats = CacheStats.get_stats()
        cache_logger.info(
            f"📊 CACHE STATS: {stats['hits']} hits, "
            f"{stats['misses']} misses, "
            f"Hit Rate: {stats['hit_rate']}"
        )


def get_cache_health():
    """Check if Redis is healthy"""
    try:
        cache.set('health_check', 'ok', 1)
        value = cache.get('health_check')
        if value == 'ok':
            cache_logger.debug("✅ Redis health check passed")
            return True
    except Exception as e:
        cache_logger.error(f"❌ Redis health check failed: {str(e)}")
        return False
    
    return False


def register_cache_invalidation_signals():
    """
    Register signal handlers for automatic cache invalidation.
    Call this in apps.py ready() method.
    
    When models are saved/deleted, affected cache keys are automatically invalidated.
    """
    from django.db.models.signals import post_save, post_delete
    
    try:
        # Import models
        from Submissions.models import (
            Team, TeamProject, Submission, Evaluation,
            Course, ProjectRegistry, Student
        )
        
        # Register signal for each model
        for Model in [Team, TeamProject, Submission, Evaluation, Student, Course, ProjectRegistry]:
            post_save.connect(
                _cache_invalidation_handler,
                sender=Model,
                dispatch_uid=f"cache_invalidate_save_{Model.__name__}"
            )
            post_delete.connect(
                _cache_invalidation_handler,
                sender=Model,
                dispatch_uid=f"cache_invalidate_delete_{Model.__name__}"
            )
        
        cache_logger.info("✅ Cache invalidation signals registered")
        
    except Exception as e:
        cache_logger.error(f"⚠️  Error registering cache signals: {str(e)}")


def _cache_invalidation_handler(sender, instance, created=None, **kwargs):
    """
    Internal signal handler for automatic cache invalidation.
    Triggered on model save or delete.
    """
    model_name = sender.__name__
    
    if model_name not in INVALIDATION_RULES:
        return  # No invalidation rules for this model
    
    # Determine action for logging
    action = "created" if (created and 'created' in kwargs) else \
             ("deleted" if 'signal' in str(kwargs) and 'post_delete' in str(kwargs) else "updated")
    
    # Invalidate all related cache prefixes
    for prefix in INVALIDATION_RULES[model_name]:
        try:
            invalidate_cache(pattern=f"{prefix}*")
            cache_logger.debug(
                f"📝 {action.upper()}: {model_name} → Invalidated {prefix}*"
            )
        except Exception as e:
            cache_logger.warning(
                f"⚠️  Failed to invalidate {prefix}* on {model_name} {action}: {str(e)}"
            )

