"""Core service camera DB editing: password, Turkish numbers, rename tombstone, delete rules."""
from __future__ import annotations

import io
import json

import pytest

from cctv_simulator import config, database
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import Server


def call(method, **params):
    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    server.wait_idle()
    return next(json.loads(x) for x in out.getvalue().splitlines() if json.loads(x).get("id") == 1)


def ok(method, **params):
    r = call(method, **params)
    assert "result" in r, r.get("error")
    return r["result"]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    lib = tmp_path / "lib.json"
    lib.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(database, "editable_camera_library_path", lambda: lib)
    pw = {"v": "gizli"}
    monkeypatch.setattr(config, "get_admin_password", lambda: pw["v"])
    monkeypatch.setattr(config, "set_admin_password", lambda new: pw.__setitem__("v", new))
    return lib


def test_writes_need_the_admin_password():
    assert call("library.auth", password="yanlış")["error"]["data"] == {"auth": False}
    assert ok("library.auth", password="gizli")["ok"]
    r = call("library.save", password="yanlış", record={"model_name": "X"})
    assert "şifresi hatalı" in r["error"]["message"]


def test_save_parses_turkish_numbers_and_shows_up_in_the_library(isolated):
    ok("library.save", password="gizli", record={
        "model_name": "Test 4MP", "sensor_name": '1/2.8"', "resolution_name": "4 MP (2K - 2688x1520)",
        "focal_min_mm": "2,8", "focal_max_mm": "12", "white_light_range_m": "1.500", "ip_rating": "IP67"})
    stored = json.loads(isolated.read_text(encoding="utf-8"))["Test 4MP"]
    assert stored["focal_min_mm"] == 2.8 and stored["white_light_range_m"] == 1500 and stored["ip_rating"] == "IP67"
    assert any(m["name"] == "Test 4MP" for m in ok("library.list")["models"])
    bad = call("library.save", password="gizli", record={"model_name": "Y", "focal_min_mm": "iki"})
    assert bad["error"]["data"] == {"field": "focal_min_mm"}


def test_renaming_a_builtin_tombstones_it(isolated):
    builtin = next(n for n, m in config.DEFAULT_CAMERA_LIBRARY.items() if m)
    ok("library.save", password="gizli", old_name=builtin, record={"model_name": builtin + " (düzeltilmiş)"})
    names = [m["name"] for m in ok("library.list")["models"]]
    assert builtin + " (düzeltilmiş)" in names and builtin not in names
    assert json.loads(isolated.read_text(encoding="utf-8"))[builtin] is None


def test_existing_name_needs_overwrite_and_builtins_cannot_be_deleted():
    builtin = next(n for n, m in config.DEFAULT_CAMERA_LIBRARY.items() if m)
    r = call("library.save", password="gizli", record={"model_name": builtin})
    assert r["error"]["data"] == {"exists": True}
    assert "silinemez" in call("library.delete", password="gizli", name=builtin)["error"]["message"]
    ok("library.save", password="gizli", record={"model_name": "Geçici"})
    ok("library.delete", password="gizli", name="Geçici")
    assert all(m["name"] != "Geçici" for m in ok("library.list")["models"])


def test_password_change_and_record_view():
    ok("library.set_password", password="gizli", new_password="yeni")
    assert call("library.auth", password="gizli")["error"]
    assert ok("library.auth", password="yeni")["ok"]
    builtin = next(n for n, m in config.DEFAULT_CAMERA_LIBRARY.items() if m)
    rec = ok("library.record", name=builtin)
    assert rec["builtin"] is True and rec["user_entry"] is False
    f = ok("library.fields")
    keys = {x["key"]: x for x in f["fields"]}
    assert keys["focal_min_mm"]["numeric"] and keys["overview"]["multiline"] and f["sensors"]
