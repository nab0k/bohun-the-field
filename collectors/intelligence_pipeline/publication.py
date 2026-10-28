"""Local public-projection preview only. No deployment, Notion read or Git writes."""
from .contracts import canonical_url, digest, exact_fields, json_text
from .editorial import MaterialVersion, EditorDecision, QAReceipt, public_projection


def approved_preview(records, *, authorized=False, trusted_checkers=frozenset()):
    if len(records) > 20:
        raise ValueError("PUBLIC_PREVIEW_LIMIT")
    items, features = [], []
    for raw in records:
        exact_fields(raw, {"material", "editorial", "qa", "geography", "source_urls"})
        data = dict(raw["material"]); data["source_hashes"] = tuple(data["source_hashes"])
        version = MaterialVersion(**data)
        item = public_projection(version, EditorDecision(**raw["editorial"]), QAReceipt(**raw["qa"]),
                                 enabled=authorized, trusted_checkers=trusted_checkers)
        if item is None:
            continue
        urls = [canonical_url(u) for u in raw["source_urls"]]
        if not urls or len(urls) > 5:
            raise ValueError("PUBLIC_SOURCE_REFS_REQUIRED")
        item["source_urls"] = urls
        geography = raw["geography"]
        if geography is not None:
            exact_fields(geography, {"country", "longitude", "latitude", "kind", "evidence_ref", "confirmed"})
            # First preview supports safe country centroids, never company/facility coordinates.
            if geography["country"] == "UA" or geography["kind"] != "country_centroid" or geography["confirmed"] is not True:
                raise ValueError("PUBLIC_GEOGRAPHY_NOT_PERMITTED")
            x,y = geography["longitude"], geography["latitude"]
            if any(type(v) not in (int,float) for v in (x,y)) or not (-180 <= x <= 180 and -90 <= y <= 90):
                raise ValueError("PUBLIC_GEOGRAPHY_INVALID")
            if geography["evidence_ref"] not in urls:
                raise ValueError("PUBLIC_GEOGRAPHY_EVIDENCE_REQUIRED")
            features.append({"type":"Feature", "id":version.material_id,
                             "geometry":{"type":"Point", "coordinates":[x,y]},
                             "properties":{"material_id":version.material_id, "version":version.version,
                                           "content_hash":version.content_hash, "headline":version.title}})
        items.append(item)
    if len({i["material_id"] for i in items}) != len(items):
        raise ValueError("PUBLIC_DUPLICATE_MATERIAL")
    core = {"items":items, "map":{"type":"FeatureCollection", "features":features}}
    return {"schema_version":"exp009-approved-preview-v1", "snapshot_hash":digest(core), **core}
