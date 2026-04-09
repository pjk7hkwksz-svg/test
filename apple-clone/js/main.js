// ===================== NAV SCROLL EFFECT =====================
const nav = document.getElementById("mainNav");
window.addEventListener("scroll", () => {
  nav.classList.toggle("scrolled", window.scrollY > 10);
}, { passive: true });

// ===================== MOBILE MENU =====================
const hamburger = document.getElementById("hamburger");
const mobileMenu = document.getElementById("mobileMenu");

hamburger.addEventListener("click", () => {
  const open = hamburger.classList.toggle("open");
  mobileMenu.classList.toggle("open", open);
  document.body.style.overflow = open ? "hidden" : "";
});

// Close menu when a link is clicked
mobileMenu.querySelectorAll("a").forEach((link) => {
  link.addEventListener("click", () => {
    hamburger.classList.remove("open");
    mobileMenu.classList.remove("open");
    document.body.style.overflow = "";
  });
});

// Close menu on Escape key
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && hamburger.classList.contains("open")) {
    hamburger.classList.remove("open");
    mobileMenu.classList.remove("open");
    document.body.style.overflow = "";
  }
});

// ===================== SCROLL REVEAL =====================
const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("visible");
        observer.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.1 }
);

document.querySelectorAll(".product-card, .hero, .services-strip").forEach((el) => {
  el.classList.add("reveal");
  observer.observe(el);
});

// ===================== CTA INTERACTION =====================
document.querySelectorAll(".cta").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    // Ripple effect
    const ripple = document.createElement("span");
    ripple.style.cssText = `
      position:absolute; border-radius:50%; background:rgba(255,255,255,.3);
      transform:scale(0); animation:ripple .5s linear;
      width:60px; height:60px;
      left:${e.offsetX - 30}px; top:${e.offsetY - 30}px;
      pointer-events:none;
    `;
    btn.style.position = "relative";
    btn.style.overflow = "hidden";
    btn.appendChild(ripple);
    setTimeout(() => ripple.remove(), 500);
  });
});

// ===================== INJECT REVEAL + RIPPLE CSS =====================
const style = document.createElement("style");
style.textContent = `
  .reveal {
    opacity: 0;
    transform: translateY(30px);
    transition: opacity .6s ease, transform .6s ease;
  }
  .reveal.visible {
    opacity: 1;
    transform: translateY(0);
  }
  @keyframes ripple {
    to { transform: scale(4); opacity: 0; }
  }
`;
document.head.appendChild(style);
