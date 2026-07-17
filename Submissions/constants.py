# Submissions/constants.py

CAPSTONE_REQUIRED = {
    "CDS": 4,
    "AIE": 4,
    "CDE": 2,
    "CDA": 2,
}

# AIE two-phase statuses — the CDS leg reuses the standard statuses,
# these track the AIE leg after the CDS cycle is complete.
AIE_PHASE2_STATUSES = {
    "CDSCycleComplete",
    "AIECapstoneAssigned",
    "AIEReadyForClientPick",
    "AIEClientAssigned",
}

COURSE_CODES = {"CDS", "CDE", "CDA", "AIE"}

COURSE_ALIASES = {
    "AI": "AIE",
}
