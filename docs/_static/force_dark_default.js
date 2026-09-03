// Default to dark mode unless the visitor has explicitly chosen a theme.
// Furo persists the user's choice in localStorage under the key "theme".
(function () {
    try {
        if (!localStorage.getItem("theme")) {
            document.documentElement.dataset.theme = "dark";
        }
    } catch (_e) { /* localStorage disabled — fall back to OS preference */ }
})();
