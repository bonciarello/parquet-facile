"""CSV to Parquet Converter — Flask backend with PyArrow."""

import os
import uuid
import tempfile
import shutil
import traceback
from pathlib import Path

from flask import Flask, request, jsonify, send_file, render_template
import pyarrow as pa
import pyarrow.csv as csv
import pyarrow.parquet as pq

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

# Store session data keyed by a token
sessions: dict[str, dict] = {}


def _map_pa_type(t: pa.DataType) -> str:
    """Map a PyArrow type to a human-readable type name."""
    type_map = {
        pa.string(): "string",
        pa.large_string(): "string",
        pa.int8(): "integer",
        pa.int16(): "integer",
        pa.int32(): "integer",
        pa.int64(): "integer",
        pa.uint8(): "integer",
        pa.uint16(): "integer",
        pa.uint32(): "integer",
        pa.uint64(): "integer",
        pa.float16(): "float",
        pa.float32(): "float",
        pa.float64(): "float",
        pa.bool_(): "boolean",
        pa.date32(): "date",
        pa.date64(): "date",
        pa.timestamp("s"): "timestamp",
        pa.timestamp("ms"): "timestamp",
        pa.timestamp("us"): "timestamp",
        pa.timestamp("ns"): "timestamp",
    }
    return type_map.get(t, "string")


def _human_to_pa_type(name: str) -> pa.DataType:
    """Convert a human-readable type name to a PyArrow type."""
    mapping = {
        "string": pa.string(),
        "integer": pa.int64(),
        "float": pa.float64(),
        "boolean": pa.bool_(),
        "date": pa.date32(),
    }
    return mapping.get(name, pa.string())


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload_csv():
    """Upload a CSV file, analyse its schema, and return a preview."""
    if "file" not in request.files:
        return jsonify({"error": "Nessun file caricato."}), 400

    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".csv"):
        return jsonify({"error": "Il file deve avere estensione .csv"}), 400

    token = uuid.uuid4().hex
    upload_path = UPLOADS_DIR / f"{token}.csv"
    f.save(str(upload_path))

    try:
        # First pass: auto-detect schema
        read_opts = csv.ReadOptions(block_size=10 * 1024 * 1024)
        parse_opts = csv.ParseOptions(delimiter=None)  # auto-detect
        convert_opts = csv.ConvertOptions(
            strings_can_be_null=True,
            quoted_strings_can_be_null=True,
        )

        table = csv.read_csv(
            str(upload_path),
            read_options=read_opts,
            parse_options=parse_opts,
            convert_options=convert_opts,
        )

        columns = []
        preview_rows = []
        col_names = table.column_names
        num_preview = min(5, table.num_rows)

        for ci, name in enumerate(col_names):
            col = table.column(ci)
            detected = _map_pa_type(col.type)
            columns.append({
                "index": ci,
                "name": name,
                "detected_type": detected,
            })

        for ri in range(num_preview):
            row = {}
            for ci, name in enumerate(col_names):
                val = table.column(ci)[ri].as_py()
                row[name] = val
            preview_rows.append(row)

        sessions[token] = {
            "csv_path": str(upload_path),
            "columns": columns,
            "row_count": table.num_rows,
        }

        return jsonify({
            "token": token,
            "columns": columns,
            "preview": preview_rows,
            "row_count": table.num_rows,
            "file_size_bytes": upload_path.stat().st_size,
        })

    except Exception as e:
        # Clean up
        upload_path.unlink(missing_ok=True)
        return jsonify({"error": f"Errore nella lettura del CSV: {str(e)}"}), 422


@app.route("/api/convert", methods=["POST"])
def convert_to_parquet():
    """Convert the uploaded CSV to Parquet using (optionally overridden) column types."""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "Richiesta vuota."}), 400

    token = data.get("token")
    overrides = data.get("types", {})  # {col_name: "integer"|"float"|"string"|"boolean"}

    session = sessions.get(token)
    if not session:
        return jsonify({"error": "Sessione scaduta o token non valido. Ricarica il file."}), 404

    csv_path = session["csv_path"]
    if not os.path.isfile(csv_path):
        return jsonify({"error": "File CSV non più disponibile. Ricaricalo."}), 404

    try:
        # Build explicit schema based on detected + overridden types
        fields = []
        for col in session["columns"]:
            name = col["name"]
            type_name = overrides.get(name, col["detected_type"])
            fields.append(pa.field(name, _human_to_pa_type(type_name)))

        schema = pa.schema(fields)

        read_opts = csv.ReadOptions(block_size=10 * 1024 * 1024)
        parse_opts = csv.ParseOptions(delimiter=None)
        convert_opts = csv.ConvertOptions(
            column_types=schema,
            strings_can_be_null=True,
            quoted_strings_can_be_null=True,
        )

        table = csv.read_csv(
            csv_path,
            read_options=read_opts,
            parse_options=parse_opts,
            convert_options=convert_opts,
        )

        output_path = OUTPUTS_DIR / f"{token}.parquet"
        pq.write_table(table, str(output_path), compression="snappy")

        session["parquet_path"] = str(output_path)
        session["parquet_size"] = output_path.stat().st_size

        return jsonify({
            "token": token,
            "download_url": f"api/download/{token}",
            "parquet_size_bytes": output_path.stat().st_size,
            "columns": session["columns"],
            "row_count": session["row_count"],
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Errore nella conversione: {str(e)}"}), 422


@app.route("/api/download/<token>")
def download_parquet(token):
    """Download the generated Parquet file."""
    session = sessions.get(token)
    if not session:
        return jsonify({"error": "File non trovato. Sessione scaduta."}), 404

    parquet_path = session.get("parquet_path")
    if not parquet_path or not os.path.isfile(parquet_path):
        return jsonify({"error": "File Parquet non disponibile."}), 404

    return send_file(
        parquet_path,
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name="output.parquet",
    )


# Cleanup old files periodically (call via external cron or simple cleanup logic)
@app.before_request
def cleanup_old_files():
    """Remove upload/output files older than 1 hour."""
    import time
    now = time.time()
    one_hour = 3600

    for d in [UPLOADS_DIR, OUTPUTS_DIR]:
        for f in d.iterdir():
            if f.is_file() and (now - f.stat().st_mtime) > one_hour:
                f.unlink(missing_ok=True)

    # Clean up expired sessions
    expired = [t for t, s in sessions.items()
               if not os.path.isfile(s.get("csv_path", ""))]
    for t in expired:
        sessions.pop(t, None)


@app.route("/robots.txt")
def robots():
    return send_file(BASE_DIR / "robots.txt", mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap():
    return send_file(BASE_DIR / "sitemap.xml", mimetype="application/xml")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4601))
    app.run(host="0.0.0.0", port=port, debug=False)
