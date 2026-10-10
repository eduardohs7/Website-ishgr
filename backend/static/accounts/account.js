document.addEventListener("DOMContentLoaded", () => {
  const menu = document.querySelector(".hamburger");
  const links = document.querySelector(".nav-links");
  if (menu && links) {
    menu.addEventListener("click", () => {
      const active = links.classList.toggle("active");
      menu.classList.toggle("active", active);
      menu.setAttribute("aria-expanded", String(active));
    });
  }
  const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
  const field = document.getElementById("id_token");
  if (token && field) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    if (/^[A-Za-z0-9_-]{43}$/.test(token)) field.value = token;
  }
});
