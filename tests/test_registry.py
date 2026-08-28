from backend.app.camera_identity import stable_camera_id
from backend.app.db import connect, registry
from backend.app.discovery.active_scan import RtspStream, ScannedHost


def test_password_encrypted_at_rest_and_roundtrips():
    registry.init_db()
    registry.upsert_camera("AA:BB:CC:00:11:22", name="Front", password="s3cr3t",
                           stream_path="/onvif1", last_ip="192.168.1.50")
    cam = registry.get_camera("aa:bb:cc:00:11:22")  # MAC lookup is case-insensitive
    assert cam is not None
    assert cam.password == "s3cr3t"          # decrypts
    assert cam.name == "Front"
    # raw blob must not contain the plaintext
    with connect() as c:
        blob = c.execute("SELECT password_enc FROM cameras WHERE mac=?",
                         ("aa:bb:cc:00:11:22",)).fetchone()[0]
    assert b"s3cr3t" not in bytes(blob)


def test_public_camera_id_is_stable_opaque_and_survives_native_mac_rekey():
    registry.init_db()
    created = registry.upsert_camera("aa:bb:cc:00:11:22", name="Front")

    assert created.camera_id == stable_camera_id("mac", "AA-BB-CC-00-11-22")
    assert created.camera_id.startswith("cam_")
    assert "aabbcc" not in created.camera_id
    assert registry.get_camera_by_id(created.camera_id).mac == "aa:bb:cc:00:11:22"

    moved = registry.rekey_camera("aa:bb:cc:00:11:22", "aa:bb:cc:dd:ee:01")
    assert moved is not None
    assert moved.camera_id == created.camera_id
    assert registry.get_camera_by_id(created.camera_id).mac == "aa:bb:cc:dd:ee:01"


def test_registry_primary_key_is_camera_id_and_mac_is_optional():
    registry.init_db()
    first = registry.upsert_camera(
        identity_kind="serial", identity_value="SERIAL-ONE", name="Serial camera"
    )
    second = registry.upsert_camera(
        identity_kind="vendor_device", identity_value="DEVICE-TWO", name="P2P camera"
    )

    assert first.mac == second.mac == ""
    assert first.camera_id != second.camera_id
    assert registry.get_camera_by_id(first.camera_id).name == "Serial camera"
    with connect() as connection:
        primary_key = next(
            row["name"] for row in connection.execute("PRAGMA table_info(cameras)") if row["pk"]
        )
    assert primary_key == "camera_id"


def test_camera_without_mac_can_be_updated_and_deleted_by_public_id():
    registry.init_db()
    camera = registry.upsert_camera(identity_kind="serial", identity_value="SERIAL-ONE")

    updated = registry.upsert_camera(
        camera_id=camera.camera_id, name="Updated", capabilities={"driver": "generic"}
    )
    assert updated.name == "Updated"
    assert updated.capabilities == {"driver": "generic"}

    registry.delete_camera_by_id(camera.camera_id)
    assert registry.get_camera_by_id(camera.camera_id) is None


def test_legacy_empty_mac_delete_cannot_remove_macless_cameras():
    registry.init_db()
    camera = registry.upsert_camera(identity_kind="serial", identity_value="SERIAL-ONE")
    registry.delete_camera("")
    assert registry.get_camera_by_id(camera.camera_id) is not None


def test_init_migrates_legacy_mac_primary_key_without_reencrypting_password():
    with connect() as connection:
        connection.executescript(
            """CREATE TABLE cameras (
                   mac TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
                   username TEXT NOT NULL DEFAULT 'admin', password_enc BLOB,
                   stream_path TEXT NOT NULL DEFAULT '', rtsp_port INTEGER NOT NULL DEFAULT 554,
                   last_ip TEXT NOT NULL DEFAULT '', vendor TEXT NOT NULL DEFAULT '',
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
               );"""
        )
        encrypted = registry._encrypt("preserved-secret")
        connection.execute(
            """INSERT INTO cameras
               (mac,name,username,password_enc,stream_path,rtsp_port,last_ip,vendor,
                created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("aa:bb:cc:dd:ee:01", "Legacy", "admin", encrypted, "/onvif1", 554,
             "10.0.0.5", "legacy", "created", "updated"),
        )

    registry.init_db()

    migrated = registry.get_camera("aa:bb:cc:dd:ee:01")
    assert migrated.camera_id == stable_camera_id("mac", migrated.mac)
    assert migrated.password == "preserved-secret"
    assert migrated.created_at == "created" and migrated.updated_at == "updated"
    with connect() as connection:
        stored = connection.execute(
            "SELECT password_enc FROM cameras WHERE camera_id = ?", (migrated.camera_id,)
        ).fetchone()[0]
        primary_key = next(
            row["name"] for row in connection.execute("PRAGMA table_info(cameras)") if row["pk"]
        )
    assert bytes(stored) == bytes(encrypted)
    assert primary_key == "camera_id"


def test_rtsp_url_built_from_parts():
    registry.init_db()
    registry.upsert_camera("aa:bb:cc:00:11:22", username="admin", password="pw",
                           stream_path="/onvif1", last_ip="10.0.0.9", rtsp_port=554)
    cam = registry.get_camera("aa:bb:cc:00:11:22")
    assert cam.rtsp_url == "rtsp://admin:pw@10.0.0.9:554/onvif1"


def test_rtsp_url_none_without_ip_or_path():
    registry.init_db()
    registry.upsert_camera("aa:bb:cc:00:11:22", password="pw")
    assert registry.get_camera("aa:bb:cc:00:11:22").rtsp_url is None


def test_delete_camera():
    registry.init_db()
    registry.upsert_camera("aa:bb:cc:00:11:22", name="x")
    registry.delete_camera("aa:bb:cc:00:11:22")
    assert registry.get_camera("aa:bb:cc:00:11:22") is None


def test_upsert_only_updates_given_fields():
    registry.init_db()
    registry.upsert_camera("aa:bb:cc:00:11:22", name="Orig", password="pw")
    registry.upsert_camera("aa:bb:cc:00:11:22", last_ip="10.0.0.1")  # name/password untouched
    cam = registry.get_camera("aa:bb:cc:00:11:22")
    assert cam.name == "Orig" and cam.password == "pw" and cam.last_ip == "10.0.0.1"


def test_capabilities_persist_as_json_and_default_empty():
    registry.init_db()
    registry.upsert_camera("aa:bb:cc:00:11:22", name="x")
    assert registry.get_camera("aa:bb:cc:00:11:22").capabilities == {}  # default
    caps = {"ptz": True, "has_audio": True, "open_ports": [554, 5000]}
    registry.upsert_camera("aa:bb:cc:00:11:22", capabilities=caps)
    cam = registry.get_camera("aa:bb:cc:00:11:22")
    assert cam.capabilities == caps
    assert cam.name == "x"  # unrelated fields untouched


# --- re-keying to the authoritative ONVIF MAC (docs/DECISIONS.md §23) ----------------

ARP_MAC = "aa:bb:cc:00:11:22"
ONVIF_MAC = "aa:bb:cc:dd:ee:01"


def _scanned(mac, arp_mac, ip="192.168.1.101"):
    return ScannedHost(address=ip, mac=mac, arp_mac=arp_mac, open_ports=[554, 5000])


def test_rekey_moves_the_record_and_keeps_everything():
    registry.init_db()
    registry.upsert_camera(ARP_MAC, name="Front", username="admin", password="pw",
                           stream_path="/onvif1", capabilities={"ptz": True})
    moved = registry.rekey_camera(ARP_MAC, ONVIF_MAC)
    assert moved is not None and moved.mac == ONVIF_MAC
    assert registry.get_camera(ARP_MAC) is None
    cam = registry.get_camera(ONVIF_MAC)
    assert cam.name == "Front" and cam.password == "pw"      # credentials survive the move
    assert cam.stream_path == "/onvif1" and cam.capabilities == {"ptz": True}


def test_rekey_refuses_when_the_target_is_already_registered():
    """Two real cameras — merging would silently discard one record's credentials."""
    registry.init_db()
    registry.upsert_camera(ARP_MAC, name="Front", password="pw1")
    registry.upsert_camera(ONVIF_MAC, name="Back", password="pw2")
    assert registry.rekey_camera(ARP_MAC, ONVIF_MAC) is None
    assert registry.get_camera(ARP_MAC).name == "Front"      # both left untouched
    assert registry.get_camera(ONVIF_MAC).name == "Back"


def test_rekey_unknown_source_is_a_noop():
    registry.init_db()
    assert registry.rekey_camera(ARP_MAC, ONVIF_MAC) is None
    assert registry.get_camera(ONVIF_MAC) is None


def test_reconcile_rekeys_instead_of_offering_a_duplicate_candidate():
    registry.init_db()
    registry.upsert_camera(ARP_MAC, name="Front", password="pw", stream_path="/onvif1")
    moves = []
    configured, candidates = registry.reconcile(
        [_scanned(mac=ONVIF_MAC, arp_mac=ARP_MAC)],
        on_rekey=lambda old, new: moves.append((old, new)),
    )
    assert candidates == []                                  # not a "new" camera
    assert [c.mac for c in configured] == [ONVIF_MAC]
    assert configured[0].name == "Front"
    assert moves == [(ARP_MAC, ONVIF_MAC)]                   # caller told to migrate recordings
    assert registry.get_camera(ONVIF_MAC).last_ip == "192.168.1.101"


def test_reconcile_leaves_a_genuinely_new_camera_as_a_candidate():
    registry.init_db()
    host = _scanned(mac=ONVIF_MAC, arp_mac=ARP_MAC)
    host.streams = [RtspStream(url="rtsp://192.168.1.101:554/onvif1", status=200)]
    moves = []
    configured, candidates = registry.reconcile([host], on_rekey=lambda o, n: moves.append((o, n)))
    assert configured == [] and moves == []
    assert [c.mac for c in candidates] == [ONVIF_MAC]
