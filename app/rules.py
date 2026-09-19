"""Game and safety rules in one place so the numbers are easy to tune and to test."""

FLAG_TYPES = {
    # points: base reward; radius_m: arrival radius before the accuracy allowance
    "tree_water": {"points": 12, "radius_m": 35, "purpose": "complete", "cooldown_days": 5},
    "drain_clear": {"points": 15, "radius_m": 30, "purpose": "complete", "cooldown_days": 3},
    "cooling_check": {"points": 20, "radius_m": 60, "purpose": "complete", "cooldown_days": 60},
    "flood_report": {"points": 12, "radius_m": 45, "purpose": "confirm", "expire_hours": 12},
    "problem_report": {"points": 10, "radius_m": 45, "purpose": "confirm", "expire_hours": 24 * 7},
}
REPORT_TYPES = ("flood_report", "problem_report")

URGENCY_MULTIPLIER = {1: 1.0, 2: 1.25, 3: 1.5}

CLAIM_MINUTES = 45
ARRIVAL_VALID_MINUTES = 60
MAX_ACTIVE_MISSIONS = 3
MAX_ATTEMPTS = 3
REPEAT_LOCKOUT_HOURS = 24
UNUSABLE_RECHECK_DAYS = 14  # a cooling space found closed or blocked is checked again after this long
DAILY_POINT_MISSIONS = 12

ACCURACY_ALLOWANCE_CAP_M = 60
MANUAL_ARRIVAL_MAX_M = 150
REPORT_DEDUPE_M = 25
REPORT_DEDUPE_HOURS = 24
CONFIRM_RADIUS_M = 60

# Confidence thresholds used when the AI validator checks a photo.
VERIFIED_MIN = 0.70
REVIEW_MIN = 0.40
REPORT_AUTO_CONFIRM_MIN = 0.85

# Point values for reports, confirmations, and daily streak bonuses.
BEFORE_PHOTO_BONUS = 5
REPORT_POINTS = 10
CONFIRM_POINTS = 3
STREAK_STEP = 0.10
STREAK_MAX_BONUS = 0.50

# What it takes to unlock each badge.
BADGES = {
    "first_mission": {"verified": 1},
    "ten_missions": {"verified": 10},
    "tree_friend": {"type": "tree_water", "count": 5},
    "storm_ready": {"type": "drain_clear", "count": 3},
    "cool_scout": {"type": "cooling_check", "count": 2},
    "eyes_on_street": {"types": ("flood_report", "problem_report"), "count": 3},
    "streak_3": {"streak": 3},
    "streak_7": {"streak": 7},
}

NICKNAME_MIN, NICKNAME_MAX = 2, 20
LANGS = ("en",)


# Work out how many points a completed mission is worth, including the before-photo and streak bonuses.
def mission_points(flag_type, urgency, streak, has_before):
    base = FLAG_TYPES[flag_type]["points"] * URGENCY_MULTIPLIER.get(urgency, 1.0)
    if has_before:
        base += BEFORE_PHOTO_BONUS
    bonus = min(max(streak - 1, 0) * STREAK_STEP, STREAK_MAX_BONUS)
    return int(round(base * (1 + bonus)))
