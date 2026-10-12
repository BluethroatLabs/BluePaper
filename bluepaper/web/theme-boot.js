try {
  if (localStorage.getItem("bluepaper-theme") === "light") {
    document.documentElement.classList.add("light");
  }
} catch (_) {
  // Storage may be disabled; dark remains the default.
}
