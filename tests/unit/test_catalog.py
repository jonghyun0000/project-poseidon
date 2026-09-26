from poseidon.core.catalog import Catalog


def test_catalog_dataset_roundtrip(tmp_path):
    cat = Catalog(tmp_path / "cat.sqlite")
    cat.register_dataset(collection="forcing", domain="east-asia", cycle="20260803T06",
                         uri="/x.zarr", source_id="noaa-gfs-0p25",
                         retrieval={"url": "http://example", "steps": [0, 3]})
    rows = cat.find_datasets("forcing", "east-asia", "20260803T06")
    assert len(rows) == 1
    assert rows[0]["source_id"] == "noaa-gfs-0p25"
    assert cat.find_datasets("forcing", "east-asia", "19990101T00") == []


def test_run_state_machine_and_events(tmp_path):
    cat = Catalog(tmp_path / "cat.sqlite")
    run_id = cat.create_run(cycle="20260803T06", engine="ingest", domain="east-asia")
    cat.set_status(run_id, "INGESTING")
    cat.set_status(run_id, "DEGRADED", degraded=True)
    run = cat.get_run(cycle="20260803T06", engine="ingest", domain="east-asia")
    assert run is not None
    assert run["status"] == "DEGRADED"
    assert run["degraded"] == 1

    with cat._conn() as c:
        events = [r["event"] for r in
                  c.execute("SELECT event FROM cycle_event WHERE run_id=? ORDER BY ts", (run_id,))]
    assert "CREATED:WAITING_SOURCES" in events
    assert "STATUS:DEGRADED" in events
