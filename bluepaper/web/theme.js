(() => {
  const root = document.documentElement;
  const button = document.getElementById("theme");
  const themeColor = document.querySelector('meta[name="theme-color"]');
  const dialog = document.getElementById("about-dialog");

  function applyTheme(light, persist) {
    root.classList.toggle("light", light);
    if (themeColor) themeColor.content = light ? "#fafafa" : "#0a0a0a";
    if (button) {
      const label = light ? "Switch to dark appearance" : "Switch to light appearance";
      button.setAttribute("aria-label", label);
      button.title = label;
    }
    if (persist) {
      try { localStorage.setItem("bluepaper-theme", light ? "light" : "dark"); } catch (_) {}
      window.bluepaperRefreshTurnstile?.();
    }
  }

  applyTheme(root.classList.contains("light"), false);
  button?.addEventListener("click", () => applyTheme(!root.classList.contains("light"), true));
  document.querySelector("[data-open-about]")?.addEventListener("click", () => dialog?.showModal());
  document.querySelector("[data-close-about]")?.addEventListener("click", () => dialog?.close());
  if (dialog && window.location.hash === "#about") dialog.showModal();
  dialog?.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
})();
