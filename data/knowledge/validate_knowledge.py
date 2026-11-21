"""Validate the travel knowledge catalogue. Requires jsonschema >= 4."""
import csv
import json
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent


def read_json(filename):
    return json.loads((ROOT / filename).read_text(encoding="utf-8"))


def check(condition, message):
    if not condition:
        raise ValueError(message)


def validate():
    places = read_json("places.json")
    cities = read_json("cities.json")
    categories = read_json("categories.json")
    routes = read_json("route_templates.json")
    manifest = read_json("manifest.json")
    schema = read_json("places.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(places), key=lambda error: str(error.path))
    check(not errors, "Schema validation failed\n" + "\n".join(str(error) for error in errors[:5]))

    place_ids = {place["id"] for place in places}
    city_ids = {city["id"] for city in cities}
    category_ids = {category["id"] for category in categories}
    check(len(place_ids) == len(places), "Duplicate place IDs")
    check(len(city_ids) == len(cities) == 16, "Expected 16 unique cities")
    check(len({route["id"] for route in routes}) == len(routes), "Duplicate route IDs")
    places_by_id = {place["id"]: place for place in places}
    counts = Counter(place["city_id"] for place in places)
    route_counts = Counter(route["city_id"] for route in routes)
    for city in cities:
        city_id = city["id"]
        check(counts[city_id] >= 30, f"Too few places for {city_id}")
        check(counts[city_id] == city["places_collected"], f"City count mismatch for {city_id}")
        check(route_counts[city_id] == 3, f"Expected 3 route templates for {city_id}")
    for place in places:
        check(place["city_id"] in city_ids, f"Unknown city for {place['id']}")
        check(set(place["categories"]) <= category_ids, f"Unknown category for {place['id']}")
        parent_id = place["parent_place_id"]
        if parent_id:
            check(parent_id in place_ids, f"Missing parent {parent_id}")
            check(places_by_id[parent_id]["city_id"] == place["city_id"], "Parent city mismatch")
        if place["opening_hours"]["status"] == "unknown":
            check(place["opening_hours"]["weekly"] is None, "Unknown hours contain a weekly schedule")
        check(not place["route_ready"], "Publication readiness needs a separate review")
    with (ROOT / "category-coverage.csv").open(encoding="utf-8-sig", newline="") as stream:
        category_review = list(csv.DictReader(stream))
    check(len(category_review) == len(cities), "Category coverage city count mismatch")
    cities_by_name = {city["name"]: city for city in cities}
    for row in category_review:
        city = cities_by_name[row["city"]]
        selected = [place for place in places if place["city_id"] == city["id"]]
        actual_categories = Counter(category for place in selected for category in place["categories"])
        check(int(row["places"]) == len(selected), "Category coverage place count mismatch")
        check(city["category_counts"] == dict(actual_categories), "City category counts mismatch")
        check(int(row["museum_places"]) == sum(place["entity_type"] == "museum" for place in selected), "Museum count mismatch")
        for category_id in category_ids:
            check(int(row[category_id]) == actual_categories[category_id], "Category coverage mismatch")
    map_candidates = read_json("map_candidates.json")
    map_by_id = {item["id"]: item for item in map_candidates}
    check(len(map_by_id) == len(map_candidates), "Duplicate OSM candidate IDs")
    map_sources = read_json("map_sources.json")
    check(map_sources["license"] == "ODbL-1.0", "Missing OSM licence")
    check({item["city_id"] for item in map_sources["cities"] if item["status"] == "ok"} == city_ids, "Incomplete city map sources")
    for candidate in map_candidates:
        check(candidate["city_id"] in city_ids, "Unknown map candidate city")
        check(candidate["source_url"].startswith("https://www.openstreetmap.org/"), "Invalid OSM source URL")
    matched_ids = set()
    for match in read_json("map_matches.json"):
        check(match["map_id"] in map_by_id, "Missing map match candidate")
        check(match["place_id"] in places_by_id, "Missing matched place")
        check(match["map_id"] not in matched_ids, "OSM object assigned to multiple places")
        matched_ids.add(match["map_id"])
        place = places_by_id[match["place_id"]]
        candidate = map_by_id[match["map_id"]]
        check(place["city_id"] == candidate["city_id"], "Map match city mismatch")
        check(place["external_ids"]["osm"] == candidate["external_id"], "OSM identifier mismatch")
        if match["rule"] == "name_and_proximity" and candidate["tags"].get("leisure") in ["park", "garden"]:
            check(place["entity_type"] in ["public_space", "square", "waterfront", "street"], "Park matched to an incompatible venue")
    check(manifest["counts"]["map_candidates"] == len(map_candidates), "Map candidate count mismatch")
    check(manifest["counts"]["map_imported_places"] == sum(place["record_status"] == "map_imported" for place in places), "Map import count mismatch")
    for place in places:
        if place["record_status"] == "map_imported":
            check(place.get("external_ids", {}).get("osm"), "Imported map place has no OSM ID")
            check(any(source.get("license") == "ODbL-1.0" for source in place["sources"]), "Imported map place has no licence attribution")
    signatures = set()
    for route in routes:
        check(route["city_id"] in city_ids, "Unknown route city")
        check(route["status"] == "draft" and not route["route_ready"], "Expected route draft")
        check(route["walking_minutes"] is None and route["distance_meters"] is None, "Unverified walking metrics")
        stops = [stop for day in route["days"] for stop in day["stops"]]
        ids = [stop["place_id"] for stop in stops]
        check(len(ids) == len(set(ids)) and len(ids) >= 3, f"Invalid route stops for {route['id']}")
        signature = (route["city_id"], tuple(sorted(ids)))
        check(signature not in signatures, f"Repeated route selection for {route['id']}")
        signatures.add(signature)
        for day in route["days"]:
            check([stop["order"] for stop in day["stops"]] == list(range(1, len(day["stops"]) + 1)), "Stop order mismatch")
        for stop in stops:
            check(stop["place_id"] in place_ids, "Missing route place")
            place = places_by_id[stop["place_id"]]
            check(place["city_id"] == route["city_id"], "Route stop city mismatch")
            check(place["location"] and place["location"]["status"] != "conflicting", "Route coordinate gap")
            check(stop["duration_minutes"] > 0 and stop["duration_basis"] == "editorial_estimate", "Invalid duration estimate")
    for issue in read_json("quality_issues.json"):
        check(issue["place_id"] in place_ids, "Quality issue references a missing place")
    for old_id, new_id in read_json("aliases.json").items():
        check(new_id in place_ids or new_id in read_json("aliases.json"), f"Missing alias target {new_id}")
        check(old_id != new_id, "Alias points to itself")
    with (ROOT / "places-review.csv").open(encoding="utf-8-sig", newline="") as stream:
        review = list(csv.DictReader(stream))
    check({row["id"] for row in review} == place_ids and len(review) == len(places), "CSV mismatch")
    totals = manifest["counts"]
    check(totals["places"] == len(places) and totals["route_templates"] == len(routes), "Manifest totals mismatch")
    check(totals["by_city"] == dict(counts), "Manifest city counts mismatch")
    actual_files = {path.name for path in ROOT.iterdir() if path.is_file()}
    check(set(manifest["files"]) == actual_files, "Manifest file list mismatch")
    print(f"Validated {len(cities)} cities, {len(places)} places and {len(routes)} route templates")


if __name__ == "__main__":
    validate()
