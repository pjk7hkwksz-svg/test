"""
Facial Recognition App — Flask entry point
"""

from flask import Flask, request, jsonify, render_template, send_file
import io
import base64

from database.db import init_db, add_face, get_all_faces, delete_face, get_face_thumbnail, get_face_count
from models.face_model import get_face_encodings, recognize_faces, draw_boxes

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

# Allowed image extensions
ALLOWED = {"png", "jpg", "jpeg", "gif", "webp"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED


def read_upload(request_file) -> bytes:
    """Read uploaded file bytes."""
    return request_file.read()


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    face_count = get_face_count()
    faces = [{"id": f["id"], "name": f["name"], "created_at": f["created_at"]}
             for f in get_all_faces()]
    return render_template("index.html", face_count=face_count, faces=faces)


@app.route("/register")
def register_page():
    return render_template("register.html")


@app.route("/recognize")
def recognize_page():
    return render_template("recognize.html")


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/faces", methods=["GET"])
def api_list_faces():
    faces = get_all_faces()
    return jsonify([
        {"id": f["id"], "name": f["name"], "created_at": f["created_at"]}
        for f in faces
    ])


@app.route("/api/faces/<int:face_id>/thumbnail")
def api_face_thumbnail(face_id: int):
    data = get_face_thumbnail(face_id)
    if data is None:
        return jsonify({"error": "No thumbnail"}), 404
    return send_file(io.BytesIO(data), mimetype="image/jpeg")


@app.route("/api/faces/<int:face_id>", methods=["DELETE"])
def api_delete_face(face_id: int):
    deleted = delete_face(face_id)
    if deleted:
        return jsonify({"message": "Deleted successfully"})
    return jsonify({"error": "Face not found"}), 404


@app.route("/api/register", methods=["POST"])
def api_register():
    """
    Enroll a new face.
    Expects multipart/form-data with:
      - name  (str)
      - image (file) OR image_data (base64 data URL)
    """
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    image_bytes = _get_image_bytes(request)
    if image_bytes is None:
        return jsonify({"error": "No image provided"}), 400

    encodings = get_face_encodings(image_bytes)

    if not encodings:
        return jsonify({"error": "No face detected in the image"}), 422
    if len(encodings) > 1:
        return jsonify({"error": "Multiple faces detected — please provide a photo with a single face"}), 422

    face_id = add_face(name, encodings[0], image_bytes)
    return jsonify({"message": f"'{name}' registered successfully", "id": face_id}), 201


@app.route("/api/recognize", methods=["POST"])
def api_recognize():
    """
    Identify faces in an uploaded image.
    Expects multipart/form-data with:
      - image (file) OR image_data (base64 data URL)
    Returns JSON with matches and an annotated image (base64).
    """
    image_bytes = _get_image_bytes(request)
    if image_bytes is None:
        return jsonify({"error": "No image provided"}), 400

    all_faces = get_all_faces()
    known_encodings = [f["encoding"] for f in all_faces]
    known_names = [f["name"] for f in all_faces]

    results = recognize_faces(image_bytes, known_encodings, known_names)

    if not results:
        return jsonify({"error": "No faces detected in the image"}), 422

    annotated = draw_boxes(image_bytes, results)
    annotated_b64 = base64.b64encode(annotated).decode()

    return jsonify({
        "results": results,
        "annotated_image": f"data:image/jpeg;base64,{annotated_b64}",
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_image_bytes(req) -> bytes | None:
    """Extract image bytes from either a file upload or base64 data URL."""
    if "image" in req.files:
        f = req.files["image"]
        if f.filename and allowed_file(f.filename):
            return read_upload(f)

    data_url = req.form.get("image_data", "")
    if data_url.startswith("data:image"):
        header, encoded = data_url.split(",", 1)
        return base64.b64decode(encoded)

    return None


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
