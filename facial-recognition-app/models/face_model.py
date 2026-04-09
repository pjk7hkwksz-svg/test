import face_recognition
import numpy as np
import cv2
from PIL import Image
import io


def load_image_from_bytes(image_bytes: bytes) -> np.ndarray:
    """Load an image from raw bytes into a numpy RGB array."""
    pil_image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return np.array(pil_image)


def get_face_encodings(image_bytes: bytes) -> list[np.ndarray]:
    """
    Return a list of 128-d face encodings found in the image.
    Each encoding is a numpy array.
    """
    image = load_image_from_bytes(image_bytes)
    locations = face_recognition.face_locations(image, model="hog")
    encodings = face_recognition.face_encodings(image, locations)
    return encodings


def recognize_faces(
    image_bytes: bytes,
    known_encodings: list[np.ndarray],
    known_names: list[str],
    tolerance: float = 0.5,
) -> list[dict]:
    """
    Identify faces in the given image against a list of known encodings.

    Returns a list of dicts with keys:
        name       – matched name or "Unknown"
        confidence – match confidence 0–100 (%)
        location   – (top, right, bottom, left) bounding box
    """
    image = load_image_from_bytes(image_bytes)
    locations = face_recognition.face_locations(image, model="hog")
    encodings = face_recognition.face_encodings(image, locations)

    results = []
    for encoding, location in zip(encodings, locations):
        name = "Unknown"
        confidence = 0.0

        if known_encodings:
            distances = face_recognition.face_distance(known_encodings, encoding)
            best_idx = int(np.argmin(distances))
            best_distance = float(distances[best_idx])

            if best_distance <= tolerance:
                name = known_names[best_idx]
                confidence = round((1 - best_distance) * 100, 1)

        results.append(
            {
                "name": name,
                "confidence": confidence,
                "location": {
                    "top": location[0],
                    "right": location[1],
                    "bottom": location[2],
                    "left": location[3],
                },
            }
        )

    return results


def draw_boxes(image_bytes: bytes, results: list[dict]) -> bytes:
    """
    Draw labeled bounding boxes on the image and return the result as JPEG bytes.
    """
    image = load_image_from_bytes(image_bytes)
    rgb = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

    for r in results:
        loc = r["location"]
        top, right, bottom, left = loc["top"], loc["right"], loc["bottom"], loc["left"]
        color = (0, 200, 0) if r["name"] != "Unknown" else (0, 0, 220)
        label = f"{r['name']} {r['confidence']}%" if r["name"] != "Unknown" else "Unknown"

        cv2.rectangle(rgb, (left, top), (right, bottom), color, 2)
        cv2.rectangle(rgb, (left, bottom - 28), (right, bottom), color, cv2.FILLED)
        cv2.putText(
            rgb, label, (left + 4, bottom - 8),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1,
        )

    _, buf = cv2.imencode(".jpg", rgb)
    return buf.tobytes()
