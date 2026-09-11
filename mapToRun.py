import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from math import radians, sin, cos, sqrt, atan2
import json
import sys
import urllib.request

GPX_NS = "http://www.topografix.com/GPX/1/1"
INTERPOLATION_STEP_M = 3  # generate a point roughly every 3 meters


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def fetch_elevations(lats, lons):
    """Fetch elevations from Open-Meteo API in batches."""
    elevations = []
    batch_size = 100
    for i in range(0, len(lats), batch_size):
        batch_lats = lats[i : i + batch_size]
        batch_lons = lons[i : i + batch_size]
        lat_str = ",".join(f"{lat:.6f}" for lat in batch_lats)
        lon_str = ",".join(f"{lon:.6f}" for lon in batch_lons)
        url = f"https://api.open-meteo.com/v1/elevation?latitude={lat_str}&longitude={lon_str}"
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())
        elevations.extend(data["elevation"])
    return elevations


def interpolate_route(lats, lons, step_m):
    """Interpolate sparse route points into dense points spaced ~step_m apart."""
    dense_lats = [lats[0]]
    dense_lons = [lons[0]]
    # Track which original segment each dense point falls on, and fraction along it
    dense_seg_info = [(0, 0.0)]  # (segment_index, fraction)

    for i in range(1, len(lats)):
        dist = haversine(lats[i - 1], lons[i - 1], lats[i], lons[i])
        if dist < 0.1:
            continue
        n_steps = max(1, int(dist / step_m))
        for s in range(1, n_steps + 1):
            frac = s / n_steps
            lat = lats[i - 1] + frac * (lats[i] - lats[i - 1])
            lon = lons[i - 1] + frac * (lons[i] - lons[i - 1])
            dense_lats.append(lat)
            dense_lons.append(lon)
            dense_seg_info.append((i - 1, frac))

    return dense_lats, dense_lons, dense_seg_info


def interpolate_elevations(route_elevations, dense_seg_info):
    """Interpolate elevation for dense points from route point elevations."""
    dense_elevations = []
    for seg_idx, frac in dense_seg_info:
        ele_start = route_elevations[seg_idx]
        ele_end = route_elevations[seg_idx + 1] if seg_idx + 1 < len(route_elevations) else ele_start
        dense_elevations.append(ele_start + frac * (ele_end - ele_start))
    return dense_elevations


def grade_pace_factor(grade):
    """Return a multiplier on base pace given a grade (rise/run as decimal).

    Uphill is positive (slower), downhill is negative (faster).
    Uses a model roughly based on real-world running effort scaling.
    Clamped so downhills don't produce unrealistically fast paces.
    """
    factor = 1.0 + 3.5 * grade
    return max(0.7, min(factor, 2.5))


def format_time(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}" + "000Z"


def main():
    gpx_file = input("Route GPX file: ").strip()

    pace_input = input("Target pace (m:ss): ").strip()
    parts = pace_input.split(":")
    if len(parts) != 2:
        print("Invalid pace format. Use m:ss (e.g. 7:30)")
        sys.exit(1)
    target_pace_seconds = int(parts[0]) * 60 + int(parts[1])

    unit = input("Per mile or kilometer? (mi/km): ").strip().lower()
    if unit not in ("mi", "km"):
        print("Invalid unit. Use 'mi' or 'km'.")
        sys.exit(1)

    start_input = input("Start date and time (YYYY-MM-DD HH:MM:SS): ").strip()
    start_time = datetime.strptime(start_input, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    # Target pace in seconds per meter
    if unit == "mi":
        base_spm = target_pace_seconds / 1609.344
    else:
        base_spm = target_pace_seconds / 1000.0

    # Parse route GPX
    ns = {"gpx": GPX_NS}
    tree = ET.parse(gpx_file)
    root = tree.getroot()
    route_pts = root.findall(".//gpx:trkpt", ns)

    if len(route_pts) < 2:
        print("Not enough route points.")
        sys.exit(1)

    route_lats = [float(pt.get("lat")) for pt in route_pts]
    route_lons = [float(pt.get("lon")) for pt in route_pts]

    # Check if route already has elevation
    has_elevation = all(pt.find("gpx:ele", ns) is not None for pt in route_pts)

    if has_elevation:
        route_elevations = [float(pt.find("gpx:ele", ns).text) for pt in route_pts]
        print("Using elevation from GPX file.")
    else:
        print("Fetching elevation data...")
        route_elevations = fetch_elevations(route_lats, route_lons)
        print(f"Elevation range: {min(route_elevations):.0f}m - {max(route_elevations):.0f}m")

    # Interpolate route into dense points
    dense_lats, dense_lons, dense_seg_info = interpolate_route(
        route_lats, route_lons, INTERPOLATION_STEP_M
    )
    dense_elevations = interpolate_elevations(route_elevations, dense_seg_info)

    n = len(dense_lats)
    print(f"Generated {n} trackpoints from {len(route_pts)} route points.")

    # Calculate distances and grades between consecutive dense points
    distances = []
    grades = []
    for i in range(1, n):
        dist = haversine(dense_lats[i - 1], dense_lons[i - 1], dense_lats[i], dense_lons[i])
        distances.append(dist)
        if dist > 0.1:
            grade = (dense_elevations[i] - dense_elevations[i - 1]) / dist
        else:
            grade = 0.0
        grades.append(grade)

    # Build timestamps using grade-adjusted pace
    timestamps = [start_time]
    for i in range(len(distances)):
        factor = grade_pace_factor(grades[i])
        dt = distances[i] * base_spm * factor
        timestamps.append(timestamps[-1] + timedelta(seconds=dt))

    # Compute stats
    total_dist = sum(distances)
    total_time = (timestamps[-1] - timestamps[0]).total_seconds()
    avg_pace_km = (total_time / total_dist) * 1000 if total_dist > 0 else 0

    total_gain = sum(
        dense_elevations[i] - dense_elevations[i - 1]
        for i in range(1, n)
        if dense_elevations[i] > dense_elevations[i - 1]
    )
    total_loss = sum(
        dense_elevations[i - 1] - dense_elevations[i]
        for i in range(1, n)
        if dense_elevations[i] < dense_elevations[i - 1]
    )

    print(f"\nDistance:        {total_dist / 1000:.2f} km / {total_dist / 1609.344:.2f} mi")
    print(f"Avg pace:        {int(avg_pace_km // 60)}:{int(avg_pace_km % 60):02d} /km")
    print(f"Total time:      {int(total_time // 60)}:{int(total_time % 60):02d}")
    print(f"Elevation gain:  {total_gain:.0f}m")
    print(f"Elevation loss:  {total_loss:.0f}m")

    # Build output GPX
    ET.register_namespace("", GPX_NS)
    gpx = ET.Element("gpx", version="1.1", xmlns=GPX_NS)
    trk = ET.SubElement(gpx, "trk")
    trkseg = ET.SubElement(trk, "trkseg")

    for i in range(n):
        trkpt = ET.SubElement(
            trkseg, "trkpt",
            lat=f"{dense_lats[i]:.8f}",
            lon=f"{dense_lons[i]:.8f}",
        )
        ele = ET.SubElement(trkpt, "ele")
        ele.text = f"{dense_elevations[i]:.1f}"
        time_el = ET.SubElement(trkpt, "time")
        time_el.text = format_time(timestamps[i])

    out_file = gpx_file.rsplit(".", 1)[0] + "_activity.gpx"
    ET.indent(gpx, space="  ")
    out_tree = ET.ElementTree(gpx)
    out_tree.write(out_file, xml_declaration=True, encoding="UTF-8")
    print(f"Saved to: {out_file}")


if __name__ == "__main__":
    main()
