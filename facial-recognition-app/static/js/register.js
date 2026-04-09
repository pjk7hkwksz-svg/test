// ===================== Drop Zone =====================
const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const uploadPreview = document.getElementById("uploadPreview");

dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("dragover");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  const file = e.dataTransfer.files[0];
  if (file) showUploadPreview(file);
});

fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) showUploadPreview(fileInput.files[0]);
});

function showUploadPreview(file) {
  const reader = new FileReader();
  reader.onload = () => {
    uploadPreview.src = reader.result;
    uploadPreview.classList.remove("hidden");
  };
  reader.readAsDataURL(file);
}

// ===================== Webcam =====================
const video = document.getElementById("video");
const canvas = document.getElementById("canvas");
const startCamBtn = document.getElementById("startCam");
const captureBtn = document.getElementById("captureBtn");
const capturePreview = document.getElementById("capturePreview");
const imageDataInput = document.getElementById("image_data");

let stream = null;

startCamBtn.addEventListener("click", async () => {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: true });
    video.srcObject = stream;
    captureBtn.disabled = false;
    startCamBtn.textContent = "Camera On";
    startCamBtn.disabled = true;
  } catch (err) {
    showMsg("registerMsg", "Could not access camera: " + err.message, "error");
  }
});

captureBtn.addEventListener("click", () => {
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  const dataURL = canvas.toDataURL("image/jpeg", 0.9);
  imageDataInput.value = dataURL;
  capturePreview.src = dataURL;
  capturePreview.classList.remove("hidden");
  // Stop stream
  stream?.getTracks().forEach((t) => t.stop());
  video.srcObject = null;
  captureBtn.disabled = true;
  startCamBtn.disabled = false;
  startCamBtn.textContent = "Retake";
});

// ===================== Form Submit =====================
document.getElementById("registerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const msg = document.getElementById("registerMsg");
  const submitBtn = form.querySelector('button[type="submit"]');

  // Determine active tab
  const isWebcam = document.getElementById("tab-webcam").classList.contains("active");

  // Validate image source
  if (isWebcam && !imageDataInput.value) {
    showMsg("registerMsg", "Please capture a photo first.", "error");
    return;
  }
  if (!isWebcam && !fileInput.files[0]) {
    showMsg("registerMsg", "Please select an image file.", "error");
    return;
  }

  const formData = new FormData();
  formData.append("name", document.getElementById("nameInput").value.trim());

  if (isWebcam) {
    formData.append("image_data", imageDataInput.value);
    // Remove file input so it's not sent
  } else {
    formData.append("image", fileInput.files[0]);
  }

  submitBtn.disabled = true;
  submitBtn.textContent = "Registering...";

  try {
    const res = await fetch("/api/register", { method: "POST", body: formData });
    const data = await res.json();

    if (res.ok) {
      showMsg("registerMsg", data.message, "success");
      form.reset();
      uploadPreview.classList.add("hidden");
      capturePreview.classList.add("hidden");
      imageDataInput.value = "";
    } else {
      showMsg("registerMsg", data.error || "Registration failed", "error");
    }
  } catch {
    showMsg("registerMsg", "Network error — please try again.", "error");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Register Face";
  }
});

function showMsg(id, text, type) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = `msg ${type}`;
  el.classList.remove("hidden");
}
