// Shared utilities used across all pages

function showToast(message, duration = 3000) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.add("hidden"), duration);
}

async function deleteFace(id, name) {
  if (!confirm(`Delete "${name}" from the database?`)) return;
  try {
    const res = await fetch(`/api/faces/${id}`, { method: "DELETE" });
    const data = await res.json();
    if (res.ok) {
      document.getElementById(`row-${id}`)?.remove();
      showToast(`"${name}" deleted`);
    } else {
      showToast(data.error || "Delete failed");
    }
  } catch {
    showToast("Network error");
  }
}

// Tab switching — works on any page that has .tab-bar/.tab elements
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.dataset.tab;
    // Update active tab button
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    // Show/hide content
    document.querySelectorAll(".tab-content").forEach((c) => {
      const show = c.id === `tab-${target}`;
      c.classList.toggle("active", show);
      c.classList.toggle("hidden", !show);
    });
  });
});
