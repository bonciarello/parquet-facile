"""Test suite for CSV → Parquet Converter."""

import io
import os
import sys
import json
import csv as csv_module
import pytest

# Ensure the app module can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _make_csv(rows: list[list], headers: list[str] | None = None) -> io.BytesIO:
    """Create an in-memory CSV file from rows."""
    buf = io.StringIO()
    writer = csv_module.writer(buf)
    if headers:
        writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    buf.seek(0)
    bbuf = io.BytesIO(buf.getvalue().encode("utf-8"))
    bbuf.name = "test.csv"
    return bbuf


class TestIndex:
    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"<!DOCTYPE html>" in resp.data or b"<html" in resp.data


class TestUpload:
    def test_no_file(self, client):
        resp = client.post("/api/upload")
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "error" in data

    def test_upload_valid_csv(self, client):
        buf = _make_csv(
            [["Alice", "30", "1.75"], ["Bob", "25", "1.82"]],
            headers=["name", "age", "height"],
        )
        resp = client.post("/api/upload", data={"file": (buf, "test.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "token" in data
        assert len(data["columns"]) == 3
        assert len(data["preview"]) == 2
        assert data["preview"][0]["name"] == "Alice"

    def test_upload_integers_detected(self, client):
        buf = _make_csv(
            [["1", "2", "3"], ["4", "5", "6"]],
            headers=["a", "b", "c"],
        )
        resp = client.post("/api/upload", data={"file": (buf, "test.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for col in data["columns"]:
            assert col["detected_type"] == "integer"

    def test_upload_floats_detected(self, client):
        buf = _make_csv(
            [["1.5", "2.7"], ["3.14", "4.0"]],
            headers=["x", "y"],
        )
        resp = client.post("/api/upload", data={"file": (buf, "test.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for col in data["columns"]:
            assert col["detected_type"] == "float"

    def test_upload_strings_detected(self, client):
        buf = _make_csv(
            [["hello", "world"], ["foo", "bar"]],
            headers=["col1", "col2"],
        )
        resp = client.post("/api/upload", data={"file": (buf, "test.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for col in data["columns"]:
            assert col["detected_type"] == "string"

    def test_upload_booleans_detected(self, client):
        buf = _make_csv(
            [["true", "false"], ["false", "true"]],
            headers=["flag1", "flag2"],
        )
        resp = client.post("/api/upload", data={"file": (buf, "test.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for col in data["columns"]:
            assert col["detected_type"] == "boolean"

    def test_upload_returns_preview_up_to_5_rows(self, client):
        rows = [[f"val{i},{j}" for j in range(3)] for i in range(20)]
        headers = ["a", "b", "c"]
        buf = _make_csv(rows, headers)
        resp = client.post("/api/upload", data={"file": (buf, "test.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["row_count"] == 20
        assert len(data["preview"]) == 5

    def test_upload_rejects_non_csv(self, client):
        buf = io.BytesIO(b"hello world")
        buf.name = "test.txt"
        resp = client.post("/api/upload", data={"file": (buf, "test.txt")},
                           content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_upload_single_row(self, client):
        buf = _make_csv([["single", "1", "3.14"]], headers=["text", "num", "pi"])
        resp = client.post("/api/upload", data={"file": (buf, "test.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["row_count"] == 1
        assert len(data["preview"]) == 1

    def test_upload_large_file(self, client):
        """Simulate a file with many rows to ensure it's handled."""
        rows = [[str(i), str(i * 1.5), f"text_{i}"] for i in range(1000)]
        headers = ["id", "value", "label"]
        buf = _make_csv(rows, headers)
        resp = client.post("/api/upload", data={"file": (buf, "test.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["row_count"] == 1000


class TestConvert:
    def test_convert_valid(self, client):
        buf = _make_csv(
            [["Alice", "30", "1.75"], ["Bob", "25", "1.82"]],
            headers=["name", "age", "height"],
        )
        up = client.post("/api/upload", data={"file": (buf, "test.csv")},
                         content_type="multipart/form-data")
        token = json.loads(up.data)["token"]

        resp = client.post("/api/convert",
                           data=json.dumps({"token": token, "types": {}}),
                           content_type="application/json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "download_url" in data
        assert data["parquet_size_bytes"] > 0

    def test_convert_with_type_overrides(self, client):
        # A numeric column stored as text should be convertible with override
        buf = _make_csv(
            [["abc", "123"], ["def", "456"]],
            headers=["label", "code"],
        )
        up = client.post("/api/upload", data={"file": (buf, "test.csv")},
                         content_type="multipart/form-data")
        token = json.loads(up.data)["token"]

        # Force "code" to integer
        resp = client.post("/api/convert",
                           data=json.dumps({"token": token, "types": {"code": "integer"}}),
                           content_type="application/json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "download_url" in data

    def test_convert_invalid_token(self, client):
        resp = client.post("/api/convert",
                           data=json.dumps({"token": "nonexistent", "types": {}}),
                           content_type="application/json")
        assert resp.status_code == 404

    def test_convert_with_force_string(self, client):
        buf = _make_csv(
            [["1", "2"], ["3", "4"]],
            headers=["a", "b"],
        )
        up = client.post("/api/upload", data={"file": (buf, "test.csv")},
                         content_type="multipart/form-data")
        token = json.loads(up.data)["token"]

        resp = client.post("/api/convert",
                           data=json.dumps({"token": token, "types": {"a": "string", "b": "string"}}),
                           content_type="application/json")
        assert resp.status_code == 200


class TestDownload:
    def test_download_parquet(self, client):
        buf = _make_csv(
            [["x", "y"], ["1", "2"]],
            headers=["col1", "col2"],
        )
        up = client.post("/api/upload", data={"file": (buf, "test.csv")},
                         content_type="multipart/form-data")
        token = json.loads(up.data)["token"]

        conv = client.post("/api/convert",
                           data=json.dumps({"token": token, "types": {}}),
                           content_type="application/json")
        assert conv.status_code == 200

        resp = client.get(f"/api/download/{token}")
        assert resp.status_code == 200
        assert resp.mimetype == "application/octet-stream"
        # Should start with Parquet magic bytes
        assert resp.data[:4] == b"PAR1"

    def test_download_invalid_token(self, client):
        resp = client.get("/api/download/nonexistent")
        assert resp.status_code == 404


class TestSEO:
    def test_robots_txt(self, client):
        resp = client.get("/robots.txt")
        assert resp.status_code == 200
        assert b"User-agent" in resp.data
        assert b"sitemap.xml" in resp.data

    def test_sitemap_xml(self, client):
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert b"<urlset" in resp.data or b"urlset" in resp.data
        assert b"cristianporco.it" in resp.data

    def test_canonical_in_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8").lower()
        assert 'canonical' in html
        assert 'cristianporco.it/app/convertitore-di-file-csv-in-formato-parquet-con-validazione-schema' in html


class TestEdgeCases:
    def test_csv_with_commas_in_quoted_fields(self, client):
        buf = _make_csv(
            [['"Smith, John"', "42"], ['"Doe, Jane"', "33"]],
            headers=["full_name", "age"],
        )
        resp = client.post("/api/upload", data={"file": (buf, "test.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        # Should have parsed as 2 columns
        assert len(data["columns"]) == 2

    def test_csv_with_empty_values(self, client):
        buf = io.BytesIO(b"name,age,city\nAlice,,Roma\nBob,25,\n")
        buf.name = "test.csv"
        resp = client.post("/api/upload", data={"file": (buf, "test.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["columns"]) == 3
        # Empty values should be null in preview
        assert data["preview"][0]["age"] is None
        assert data["preview"][1]["city"] is None

    def test_csv_mixed_types_column_with_override(self, client):
        """Column with mixed numbers and text — user can force string type."""
        buf = _make_csv(
            [["123", "abc"], ["456", "def"], ["789", "ghi"]],
            headers=["code", "label"],
        )
        up = client.post("/api/upload", data={"file": (buf, "test.csv")},
                         content_type="multipart/form-data")
        token = json.loads(up.data)["token"]

        # Force "code" as string (even though detected as integer)
        resp = client.post("/api/convert",
                           data=json.dumps({"token": token, "types": {"code": "string"}}),
                           content_type="application/json")
        assert resp.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
