from django import template
from django.utils import timezone
from datetime import timedelta
import pytz

register = template.Library()


@register.filter
def to_ist(dt):
    """
    Convert a UTC datetime to IST (Indian Standard Time, UTC+5:30) for display.
    Handles None, aware, and naive datetime objects.
    """
    if not dt:
        return ""
    
    # If naive datetime, assume UTC
    if dt.tzinfo is None:
        dt = timezone.make_aware(dt, pytz.utc)
    
    # Convert to IST (UTC+5:30)
    ist_offset = timedelta(hours=5, minutes=30)
    
    # Convert to IST
    ist_time = dt.astimezone(pytz.utc).replace(tzinfo=None) + ist_offset
    
    # Format: "2024-03-26 18:45" or "26 Mar 2024, 18:45"
    return ist_time.strftime("%Y-%m-%d %H:%M")
