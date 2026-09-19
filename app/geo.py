# Small geo math helpers: distance between points, bounding box checks, and coordinate validation.

import math

# Average radius of the earth in meters, used for distance math.
EARTH_M = 6371008.8


# Distance in meters between two lat/lon points, using the haversine formula.
def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(a)))


# Check if a point is inside a bounding box given as (west, south, east, north).
def in_bbox(lat, lon, bbox):
    w, s, e, n = bbox
    return s <= lat <= n and w <= lon <= e


# Check that lat and lon are real numbers within valid earth coordinate ranges.
def valid_coord(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (math.isnan(lat) or math.isnan(lon))
