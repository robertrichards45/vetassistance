from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from urllib.parse import quote

# -------- Distance helpers --------

def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Earth radius miles
    r = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return r * c

def zip_to_latlon(zip_code: str) -> Optional[Tuple[float, float]]:
    """Geocode a US ZIP using the free Zippopotam.us endpoint.
    Returns (lat, lon) or None.
    """
    z = (zip_code or "").strip()
    if not z or len(z) < 5:
        return None
    z = z[:5]
    try:
        resp = requests.get(f"https://api.zippopotam.us/us/{z}", timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        places = data.get("places") or []
        if not places:
            return None
        lat = float(places[0].get("latitude"))
        lon = float(places[0].get("longitude"))
        return lat, lon
    except Exception:
        return None

def zip_to_place(zip_code: str) -> Optional[dict]:
    """Lookup a ZIP and return a dict with city/state."""
    z = (zip_code or "").strip()
    if not z or len(z) < 5:
        return None
    z = z[:5]
    try:
        resp = requests.get(f"https://api.zippopotam.us/us/{z}", timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        places = data.get("places") or []
        if not places:
            return None
        return {
            "city": places[0].get("place name") or "",
            "state": places[0].get("state abbreviation") or "",
        }
    except Exception:
        return None

def city_state_to_latlon(city: str, state: str) -> Optional[Tuple[float, float]]:
    """Geocode a US city/state using Zippopotam.us.
    Returns (lat, lon) or None.
    """
    c = (city or "").strip()
    s = (state or "").strip()
    if not c or not s:
        return None
    try:
        safe_city = quote(c)
        safe_state = quote(s.upper())
        resp = requests.get(f"https://api.zippopotam.us/us/{safe_state}/{safe_city}", timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        places = data.get("places") or []
        if not places:
            return None
        lat = float(places[0].get("latitude"))
        lon = float(places[0].get("longitude"))
        return lat, lon
    except Exception:
        return None

# -------- External sources --------

def fetch_va_facilities(lat: float, lon: float, radius_miles: float = 25, facility_type: str = "", radius: float | None = None):
    """Fetch nearby VA facilities (VHA/VBA/NCA/VC) and filter to radius.

    Uses VA Facilities API. If the API is unavailable, returns empty list.
    """
    # Backwards-compatible alias: some callers pass radius= instead of radius_miles=
    if radius is not None:
        try:
            radius_miles = float(radius)
        except Exception:
            pass
    out: List[Dict[str, Any]] = []
    try:
        # The VA Facilities API supports bbox and other filters; we keep it simple:
        # pull active facilities and filter locally by distance (works, but not as efficient).
        # In production, we'd use bbox query params to reduce payload.
        base = "https://api.va.gov/services/va_facilities/v1/facilities"
        params = {"page": 1, "per_page": 200}
        if facility_type:
            params["type"] = facility_type  # e.g., 'health', 'benefits', 'cemetery', 'vet_center'
        # NOTE: If you have a VA API key in future, add auth headers here.
        while True:
            r = requests.get(base, params=params, timeout=20)
            if r.status_code != 200:
                break
            payload = r.json()
            data = payload.get("data") or []
            for item in data:
                attr = item.get("attributes") or {}
                loc = attr.get("lat") or attr.get("latitude")
                lng = attr.get("long") or attr.get("longitude")
                try:
                    flt = float(loc)
                    fln = float(lng)
                except Exception:
                    continue
                d = haversine_miles(lat, lon, flt, fln)
                if d <= radius_miles:
                    out.append({
                        "source": "VA",
                        "category": "VA_FACILITY",
                        "name": attr.get("name") or "VA Facility",
                        "description": (attr.get("facility_type") or "").strip() or None,
                        "phone": (attr.get("phone") or {}).get("main") if isinstance(attr.get("phone"), dict) else attr.get("phone"),
                        "website": attr.get("website") or None,
                        "address": _format_address(attr.get("address")),
                        "lat": flt,
                        "lon": fln,
                        "distance_miles": round(d, 2),
                    })
            meta = payload.get("meta") or {}
            pagination = meta.get("pagination") or {}
            current = pagination.get("current_page") or params["page"]
            total = pagination.get("total_pages") or current
            if current >= total or current >= 10:  # safety cap
                break
            params["page"] = current + 1
        # sort by distance
        out.sort(key=lambda x: x.get("distance_miles", 9e9))
        return out
    except Exception:
        return []

def _format_address(address_obj: Any) -> Optional[str]:
    if not isinstance(address_obj, dict):
        return None
    street = address_obj.get("physical") or address_obj.get("address_1") or address_obj.get("address1")
    city = address_obj.get("city")
    state = address_obj.get("state")
    zipc = address_obj.get("zip") or address_obj.get("postal_code")
    parts = [p for p in [street, city, state, zipc] if p]
    return ", ".join(parts) if parts else None
