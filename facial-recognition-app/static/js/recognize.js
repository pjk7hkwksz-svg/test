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
    showMsg("recognizeMsg", "Could not access camera: " + err.message, "error");
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
  stream?.getTracks().forEach((t) => t.stop());
  video.srcObject = null;
  captureBtn.disabled = true;
  startCamBtn.disabled = false;
  startCamBtn.textContent = "Retake";
});

// ===================== Form Submit =====================
document.getElementById("recognizeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const submitBtn = form.querySelector('button[type="submit"]');
  const resultsEl = document.getElementById("results");
  const annotatedImg = document.getElementById("annotatedImage");
  const matchList = document.getElementById("matchList");

  const isWebcam = document.getElementById("tab-webcam").classList.contains("active");

  if (isWebcam && !imageDataInput.value) {
    showMsg("recognizeMsg", "Please capture a photo first.", "error");
    return;
  }
  if (!isWebcam && !fileInput.files[0]) {
    showMsg("recognizeMsg", "Please select an image file.", "error");
    return;
  }

  const formData = new FormData();
  if (isWebcam) {
    formData.append("image_data", imageDataInput.value);
  } else {
    formData.append("image", fileInput.files[0]);
  }

  submitBtn.disabled = true;
  submitBtn.textContent = "Identifying...";
  resultsEl.classList.add("hidden");
  hideMsg("recognizeMsg");

  try {
    const res = await fetch("/api/recognize", { method: "POST", body: formData });
    const data = await res.json();

    if (res.ok) {
      // Annotated image
      annotatedImg.src = data.annotated_image;
      // Match list
      matchList.innerHTML = "";
      data.results.forEach((r, i) => {
        const isKnown = r.name !== "Unknown";
        const li = document.createElement("li");
        li.className = "match-item";
        li.innerHTML = `
          <span class="match-badge ${isKnown ? "known" : "unknown"}">
            ${isKnown ? r.name : "Unknown"}
          </span>
          ${isKnown
            ? `<div class="confidence-bar-wrap">
                 <div class="confidence-bar" style="width:${r.confidence}%"></div>
               </div>
               <span style="font-size:.85rem;color:var(--muted);white-space:nowrap">
                 ${r.confidence}% match
               </span>`
            : `<span style="color:var(--muted);font-size:.9rem">No match found</span>`
          }
        `;
        matchList.appendChild(li);
      });
      resultsEl.classList.remove("hidden");
    } else {
      showMsg("recognizeMsg", data.error || "Recognition failed", "error");
    }
  } catch {
    showMsg("recognizeMsg", "Network error — please try again.", "error");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Identify Faces";
  }
});

function showMsg(id, text, type) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = `msg ${type}`;
  el.classList.remove("hidden");
}

function hideMsg(id) {
  const el = document.getElementById(id);
  el.classList.add("hidden");
}
