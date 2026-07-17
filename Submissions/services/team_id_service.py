from django.utils import timezone
from Submissions.models import Team

def _get_last_counter(base_pattern):
    """
    Returns the highest numeric counter for team IDs starting with base_pattern,
    or 11110 if none exist. Must be called inside a transaction with
    select_for_update to be safe under concurrency.

    Handles suffixes like '-B' by extracting the first digit block after the
    base_pattern (e.g. 'PTID-AIE-APR-26-11894-B' → counter 11894).
    """
    import re

    existing_ids = list(
        Team.objects
        .filter(team_id__startswith=base_pattern)
        .select_for_update()
        .values_list('team_id', flat=True)
    )
    if not existing_ids:
        return 11110

    max_counter = 11110
    prefix_len = len(base_pattern)
    for tid in existing_ids:
        suffix = tid[prefix_len:]               # e.g. "11894" or "11894-B"
        match = re.match(r'^(\d+)', suffix)     # take leading digits only
        if match:
            counter = int(match.group(1))
            if counter > max_counter:
                max_counter = counter
    return max_counter


def generate_unified_team_id(course_code, prefix="", offset=0):
    """
    Generate a single team ID (the (offset+1)-th next one after what's in the DB).
    Use generate_team_id_block() when creating multiple IDs in one transaction.

    Format: PTID-{COURSE}-{MONTH_SHORT}-{YEAR_SHORT}-{5DIGIT_COUNTER}
    Example: PTID-AIE-APR-26-11111
    """
    now = timezone.now()
    base_pattern = f"PTID-{course_code}-{now.strftime('%b').upper()}-{now.strftime('%y')}-"
    if prefix:
        base_pattern = f"{prefix}-{base_pattern}"

    last_counter = _get_last_counter(base_pattern)
    return f"{base_pattern}{last_counter + 1 + offset}"


def generate_team_id_block(course_code, count, prefix=""):
    """
    Generate `count` unique team IDs in one DB read, guaranteed non-colliding
    with existing IDs. Must be called inside an atomic transaction.

    Returns a list of `count` IDs.
    """
    if count <= 0:
        return []

    now = timezone.now()
    base_pattern = f"PTID-{course_code}-{now.strftime('%b').upper()}-{now.strftime('%y')}-"
    if prefix:
        base_pattern = f"{prefix}-{base_pattern}"

    last_counter = _get_last_counter(base_pattern)
    return [f"{base_pattern}{last_counter + i + 1}" for i in range(count)]
