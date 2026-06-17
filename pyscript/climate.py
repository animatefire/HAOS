import math

# ─── Constants ────────────────────────────────────────────────────────────────

ROOMS = range(1, 7)
NOTIFY_TARGET = "notify.mobile_app_johns_iphone_3"

STATE_SEVERITY = {
    "uncomfortable_cold": -2,
    "less_comfortable_cold": -1,
    "comfortable": 0,
    "less_comfortable_warm": 1,
    "uncomfortable_warm": 2,
}

STATE_EMOJI = {
    "comfortable": "✅",
    "less_comfortable_warm": "🟡",
    "less_comfortable_cold": "🔵",
    "uncomfortable_warm": "🔴",
    "uncomfortable_cold": "❄️",
}

STATE_LABEL = {
    "comfortable": "Comfortable",
    "less_comfortable_warm": "Less Comfortable — Warm",
    "less_comfortable_cold": "Less Comfortable — Cool",
    "uncomfortable_warm": "Too Warm",
    "uncomfortable_cold": "Too Cold",
}

DIRECTION_MAP = {
    ("uncomfortable_warm", "less_comfortable_warm"): "📉 Cooling down — still warm but improving",
    ("uncomfortable_cold", "less_comfortable_cold"): "📈 Warming up — still cool but improving",
    ("less_comfortable_warm", "uncomfortable_warm"): "📈 Crossed into uncomfortable — too warm now",
    ("less_comfortable_cold", "uncomfortable_cold"): "📉 Crossed into uncomfortable — too cold now",
    ("less_comfortable_warm", "comfortable"): "📉 Cooled back to comfort zone",
    ("less_comfortable_cold", "comfortable"): "📈 Warmed back to comfort zone",
    ("comfortable", "less_comfortable_warm"): "📈 Trending warm — watch this",
    ("comfortable", "less_comfortable_cold"): "📉 Trending cool — watch this",
    ("comfortable", "uncomfortable_warm"): "📈 Jumped straight to uncomfortable warm",
    ("comfortable", "uncomfortable_cold"): "📉 Jumped straight to uncomfortable cold",
    ("uncomfortable_warm", "comfortable"): "📉 Big improvement — back to comfortable",
    ("uncomfortable_cold", "comfortable"): "📈 Big improvement — back to comfortable",
    ("uncomfortable_warm", "uncomfortable_cold"): "↔️ Overcorrected — now too cold",
    ("uncomfortable_cold", "uncomfortable_warm"): "↔️ Overcorrected — now too warm",
}

# ─── In-memory previous state tracking ───────────────────────────────────────

previous_room_states = {i: "comfortable" for i in ROOMS}
previous_house_state = "comfortable"

# ─── Utility ──────────────────────────────────────────────────────────────────

def safe_float(entity_id):
    """Safely get a float state value, returning None if unavailable."""
    try:
        val = state.get(entity_id)
        if val in (None, "unavailable", "unknown"):
            return None
        return float(val)
    except (ValueError, TypeError, NameError):
        return None

def get_area(i):
    """Get area name for a room by sensor index."""
    try:
        return area_name(f"sensor.indoor_{i}_temp") or f"Room {i}"
    except:
        return f"Room {i}"

# ─── Core calculations ────────────────────────────────────────────────────────

def dewpoint_f(temp_f, rh):
    """Calculate dewpoint in °F from temp in °F and relative humidity %."""
    T = (temp_f - 32) * 5 / 9
    a, b = 17.27, 237.7
    alpha = (a * T / (b + T)) + math.log(rh / 100.0)
    dp_c = (b * alpha) / (a - alpha)
    return dp_c * 9 / 5 + 32

def rh_from_dewpoint_f(temp_f, dp_f):
    """Back-calculate RH from temp and dewpoint, both in °F."""
    T = (temp_f - 32) * 5 / 9
    Td = (dp_f - 32) * 5 / 9
    return 100 * math.exp(17.625 * Td / (243.04 + Td)) / math.exp(17.625 * T / (243.04 + T))

def heat_index_f(temp_f, rh):
    """Rothfusz heat index formula, temp in °F, rh in %.
    Falls back to raw temp outside NOAA validity bounds."""
    if temp_f < 80 or rh < 40:
        return temp_f
    T, R = temp_f, rh
    hi = (
        -42.379
        + 2.04901523 * T
        + 10.14333127 * R
        - 0.22475541 * T * R
        - 0.00683783 * T * T
        - 0.05481717 * R * R
        + 0.00122874 * T * T * R
        + 0.00085282 * T * R * R
        - 0.00000199 * T * T * R * R
    )
    # Third validity check — result must also be ≥ 80°F
    if hi < 80:
        return temp_f
    return hi

def comfort_state(hi, center, range_val, tolerance):
    """Return comfort state string for a given heat index."""
    lower = center - range_val
    upper = center + range_val
    lower_tol = lower - tolerance
    upper_tol = upper + tolerance
    if hi < lower_tol:
        return "uncomfortable_cold"
    if hi < lower:
        return "less_comfortable_cold"
    if hi <= upper:
        return "comfortable"
    if hi <= upper_tol:
        return "less_comfortable_warm"
    return "uncomfortable_warm"

def house_aggregate(room_states):
    """Weighted average of room comfort states → house state."""
    active = [STATE_SEVERITY[s] for s in room_states if s in STATE_SEVERITY]
    if not active:
        return "comfortable"
    avg = sum(active) / len(active)
    if avg <= -1.5:
        return "uncomfortable_cold"
    if avg <= -0.5:
        return "less_comfortable_cold"
    if avg <= 0.5:
        return "comfortable"
    if avg <= 1.5:
        return "less_comfortable_warm"
    return "uncomfortable_warm"

def direction_label(prev, new):
    """Human-readable description of state transition."""
    if prev == new:
        return None
    return DIRECTION_MAP.get((prev, new), "↔️ Conditions changing")

def room_recommendation(room_hi, room_dp, room_state, outdoor_hi, outdoor_dp, dp_buffer):
    """Per-room action recommendation."""
    if room_state == "comfortable":
        return "No action needed — comfortable ✅"
    if room_state == "less_comfortable_warm":
        return "Trending warm — monitor 🟡"
    if room_state == "less_comfortable_cold":
        return "Trending cool — monitor 🔵"
    if room_state == "uncomfortable_warm":
        if outdoor_hi < room_hi and outdoor_dp <= room_dp + dp_buffer:
            return "Open windows — outside cooler and dry enough 🪟"
        return "Run A/C — outside too warm or humid ❄️"
    if room_state == "uncomfortable_cold":
        if outdoor_hi > room_hi:
            return "Open windows — outside warmer 🪟"
        return "Close windows, consider heat 🔥"
    return "Unknown"

# ─── Sensor update helpers ────────────────────────────────────────────────────

def update_sensors(room_data, outdoor_hi, outdoor_dp):
    """Push all derived sensor states into HA."""
    for r in room_data:
        i = r["id"]
        cs = r["state"]
        state.set(f"sensor.indoor_{i}_dewpoint",
                  value=round(r["dp"], 1),
                  new_attributes={"unit_of_measurement": "°F", "friendly_name": f"Indoor {i} Dewpoint"})
        state.set(f"sensor.indoor_{i}_rh_from_dewpoint",
                  value=round(r["rh2"], 1),
                  new_attributes={"unit_of_measurement": "%", "friendly_name": f"Indoor {i} RH from Dewpoint"})
        state.set(f"sensor.indoor_{i}_heat_index",
                  value=round(r["hi"], 1),
                  new_attributes={"unit_of_measurement": "°F", "friendly_name": f"Indoor {i} Heat Index"})
        state.set(f"sensor.room_{i}_comfort_state",
                  value=cs,
                  new_attributes={"friendly_name": f"Room {i} Comfort State"})
        state.set(f"sensor.room_{i}_comfort_label",
                  value=STATE_LABEL.get(cs, "Unknown"),
                  new_attributes={"friendly_name": f"Room {i} Comfort Label"})
        state.set(f"sensor.room_{i}_comfort_emoji",
                  value=STATE_EMOJI.get(cs, "❓"),
                  new_attributes={"friendly_name": f"Room {i} Comfort Emoji"})
        state.set(f"sensor.room_{i}_recommendation",
                  value=r["recommendation"],
                  new_attributes={"friendly_name": f"Room {i} Recommendation"})

    state.set("sensor.outdoor_dewpoint",
              value=round(outdoor_dp, 1),
              new_attributes={"unit_of_measurement": "°F", "friendly_name": "Outdoor Dewpoint"})
    state.set("sensor.outdoor_heat_index",
              value=round(outdoor_hi, 1),
              new_attributes={"unit_of_measurement": "°F", "friendly_name": "Outdoor Heat Index"})

# ─── Notification helpers ─────────────────────────────────────────────────────

def room_list_str(rooms):
    return "\n".join(
        f"· {r['area']}: {r['hi']:.1f}°F — {r['recommendation']}" for r in rooms
    )

def notify(title, message):
    service.call("notify", "mobile_app_johns_iphone_3", title=title, message=message)

# ─── Main loop ────────────────────────────────────────────────────────────────

@time_trigger("period(now, 10min)")
def climate_monitor():
    global previous_room_states, previous_house_state

    # Read comfort settings
    center    = safe_float("input_number.comfort_hi_center") or 74.0
    range_val = safe_float("input_number.comfort_hi_range") or 4.0
    tolerance = safe_float("input_number.comfort_hi_tolerance") or 3.0
    dp_buffer = safe_float("input_number.dewpoint_buffer") or 0.0

    # Outdoor calcs
    out_temp = safe_float("sensor.outdoor_temp")
    out_rh   = safe_float("sensor.outdoor_humidity")

    if out_temp is None or out_rh is None:
        log.warning("climate_monitor: outdoor sensors unavailable, skipping run")
        return

    out_dp  = dewpoint_f(out_temp, out_rh)
    out_rh2 = rh_from_dewpoint_f(out_temp, out_dp)
    out_hi  = round(heat_index_f(out_temp, out_rh2), 1)

    # Per-room calcs
    room_data   = []
    room_states = {}

    for i in ROOMS:
        temp = safe_float(f"sensor.indoor_{i}_temp")
        rh   = safe_float(f"sensor.indoor_{i}_humidity")

        if temp is None or rh is None:
            room_states[i] = "unavailable"
            continue

        dp   = dewpoint_f(temp, rh)
        rh2  = rh_from_dewpoint_f(temp, dp)
        hi   = round(heat_index_f(temp, rh2), 1)
        cs   = comfort_state(hi, center, range_val, tolerance)
        rec  = room_recommendation(hi, dp, cs, out_hi, out_dp, dp_buffer)
        area = get_area(i)

        room_states[i] = cs
        room_data.append({
            "id": i,
            "temp": temp,
            "rh": rh,
            "dp": round(dp, 1),
            "rh2": rh2,
            "hi": hi,
            "state": cs,
            "recommendation": rec,
            "area": area,
        })

    # Update all derived sensors
    update_sensors(room_data, out_hi, out_dp)

    # House aggregate
    active_states   = [v for v in room_states.values() if v != "unavailable"]
    new_house_state = house_aggregate(active_states)

    state.set("sensor.house_comfort_aggregate",
              value=new_house_state,
              new_attributes={"friendly_name": "House Comfort Aggregate"})
    state.set("sensor.house_comfort_label",
              value=STATE_LABEL.get(new_house_state, "Unknown"),
              new_attributes={"friendly_name": "House Comfort Label"})
    state.set("sensor.house_comfort_emoji",
              value=STATE_EMOJI.get(new_house_state, "❓"),
              new_attributes={"friendly_name": "House Comfort Emoji"})
    state.set("input_select.indoor_climate_state",
              value=new_house_state)

    # ── Notifications — only on state transitions ─────────────────────────────

    if new_house_state != previous_house_state:
        direction = direction_label(previous_house_state, new_house_state)
        emoji     = STATE_EMOJI.get(new_house_state, "❓")

        if new_house_state == "comfortable":
            notify(
                f"{emoji} All good 🌿",
                f"{direction}\nHouse is comfortable. Close windows, no heating or cooling needed.",
            )

        elif new_house_state == "less_comfortable_warm":
            affected = [r for r in room_data if r["state"] == "less_comfortable_warm"]
            notify(
                f"{emoji} Heads up — getting warm",
                f"{direction}\nHouse trending warm but tolerable.\n\n{room_list_str(affected)}",
            )

        elif new_house_state == "less_comfortable_cold":
            affected = [r for r in room_data if r["state"] == "less_comfortable_cold"]
            notify(
                f"{emoji} Heads up — getting cool",
                f"{direction}\nHouse trending cool but tolerable.\n\n{room_list_str(affected)}",
            )

        elif new_house_state == "uncomfortable_warm":
            affected = [r for r in room_data if r["state"] == "uncomfortable_warm"]
            recs = {r["recommendation"] for r in affected}
            if any("Open windows" in r for r in recs):
                notify(
                    "Open the windows 🪟",
                    f"{direction}\nOutside: {out_hi}°F, DP {out_dp:.1f}°F\n\n{room_list_str(affected)}",
                )
            else:
                notify(
                    "Run the A/C ❄️",
                    f"{direction}\nOutside: {out_hi}°F, DP {out_dp:.1f}°F — too warm or humid.\n\n{room_list_str(affected)}",
                )

        elif new_house_state == "uncomfortable_cold":
            affected = [r for r in room_data if r["state"] == "uncomfortable_cold"]
            recs = {r["recommendation"] for r in affected}
            if any("Open windows" in r for r in recs):
                notify(
                    "Open the windows 🪟",
                    f"{direction}\nOutside: {out_hi}°F — warmer than inside.\n\n{room_list_str(affected)}",
                )
            else:
                notify(
                    "Close windows, consider heat 🔥",
                    f"{direction}\nOutside: {out_hi}°F — won't help.\n\n{room_list_str(affected)}",
                )

    # Update previous states
    previous_room_states = dict(room_states)
    previous_house_state = new_house_state