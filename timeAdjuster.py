import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from math import radians, sin, cos, sqrt, atan2
import os
import sys

from fit2gpx_lightning import fit_to_gpx

PAUSE_THRESHOLD_S = 10


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def format_time(dt):
    s = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}" + "000Z"
    return s


def main():
    activity_file = input("Activity file path: ").strip()

    ext = os.path.splitext(activity_file)[1].lower()
    if ext == ".fit":
        gpx_file = activity_file.rsplit(".", 1)[0] + ".gpx"
        fit_to_gpx(activity_file, gpx_file)
        print(f"Converted {activity_file} -> {gpx_file}")
    elif ext == ".gpx":
        gpx_file = activity_file
    else:
        print("Unsupported file type. Use a .fit or .gpx file.")
        sys.exit(1)

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

    # Target pace in seconds per meter
    if unit == "mi":
        target_spm = target_pace_seconds / 1609.344
    else:
        target_spm = target_pace_seconds / 1000.0

    # Parse GPX
    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
    ET.register_namespace("", "http://www.topografix.com/GPX/1/1")
    tree = ET.parse(gpx_file)
    root = tree.getroot()
    pts = root.findall(".//gpx:trkpt", ns)

    if len(pts) < 2:
        print("Not enough trackpoints.")
        sys.exit(1)

    # Build arrays of times, positions, and segment data
    times = []
    lats = []
    lons = []
    for pt in pts:
        times.append(parse_time(pt.find("gpx:time", ns).text))
        lats.append(float(pt.get("lat")))
        lons.append(float(pt.get("lon")))

    # Classify each segment as moving or paused, compute distances
    segments_dt = []  # original time delta in seconds
    segments_dist = []  # distance in meters
    segments_is_pause = []

    for i in range(1, len(pts)):
        dt = (times[i] - times[i - 1]).total_seconds()
        dist = haversine(lats[i - 1], lons[i - 1], lats[i], lons[i])
        segments_dt.append(dt)
        segments_dist.append(dist)
        segments_is_pause.append(dt >= PAUSE_THRESHOLD_S)

    # Current moving time and distance
    moving_time = sum(dt for dt, is_p in zip(segments_dt, segments_is_pause) if not is_p)
    moving_dist = sum(d for d, is_p in zip(segments_dist, segments_is_pause) if not is_p)

    if moving_dist == 0:
        print("No moving distance detected.")
        sys.exit(1)

    current_spm = moving_time / moving_dist  # seconds per meter
    scale = target_spm / current_spm

    current_pace_km = current_spm * 1000
    target_pace_km = target_spm * 1000
    print(f"\nCurrent moving pace: {int(current_pace_km // 60)}:{int(current_pace_km % 60):02d} /km")
    print(f"Target pace:         {int(target_pace_km // 60)}:{int(target_pace_km % 60):02d} /km")
    print(f"Scale factor:        {scale:.4f}")
    print(f"Distance:            {moving_dist / 1000:.2f} km / {moving_dist / 1609.344:.2f} mi")

    pause_count = sum(1 for p in segments_is_pause if p)
    pause_time = sum(dt for dt, is_p in zip(segments_dt, segments_is_pause) if is_p)
    print(f"Pauses:              {pause_count} ({pause_time:.0f}s total, preserved)")

    # Build new timestamps
    new_times = [times[0]]
    for i in range(len(segments_dt)):
        if segments_is_pause[i]:
            new_dt = segments_dt[i]  # preserve pause duration
        else:
            new_dt = segments_dt[i] * scale
        new_times.append(new_times[-1] + timedelta(seconds=new_dt))

    # Write new times back into the GPX
    for pt, new_t in zip(pts, new_times):
        time_el = pt.find("gpx:time", ns)
        time_el.text = format_time(new_t)

    out_file = gpx_file.rsplit(".", 1)[0] + "_adjusted.gpx"
    tree.write(out_file, xml_declaration=True, encoding="UTF-8")

    new_moving = sum(
        (new_times[i + 1] - new_times[i]).total_seconds()
        for i in range(len(segments_dt))
        if not segments_is_pause[i]
    )
    new_pace_km = (new_moving / moving_dist) * 1000
    print(f"\nNew moving pace:     {int(new_pace_km // 60)}:{int(new_pace_km % 60):02d} /km")
    new_total = (new_times[-1] - new_times[0]).total_seconds()
    print(f"New total time:      {int(new_total // 60)}:{int(new_total % 60):02d}")
    print(f"Saved to: {out_file}")


if __name__ == "__main__":
    main()
