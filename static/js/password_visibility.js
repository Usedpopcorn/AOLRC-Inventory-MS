(function () {
  function setPasswordVisibility(input, button, shouldShow) {
    input.type = shouldShow ? "text" : "password";
    button.setAttribute("aria-pressed", shouldShow ? "true" : "false");
    button.setAttribute(
      "aria-label",
      `${shouldShow ? "Hide" : "Show"} ${input.labels?.[0]?.textContent || "password"}`
    );

    const icon = button.querySelector("i");
    if (icon) {
      icon.classList.toggle("bi-eye", !shouldShow);
      icon.classList.toggle("bi-eye-slash", shouldShow);
    }
  }

  function bindPasswordToggle(button) {
    if (button.dataset.passwordToggleBound === "true") return;

    const targetId = button.dataset.passwordToggle;
    const input = document.getElementById(targetId);
    if (!input) return;

    button.dataset.passwordToggleBound = "true";
    button.addEventListener("pointerdown", (event) => {
      event.preventDefault();
    });

    button.addEventListener("click", () => {
      setPasswordVisibility(input, button, input.type === "password");
      input.focus();
    });

    input.addEventListener("blur", () => {
      if (input.type === "text") {
        setPasswordVisibility(input, button, false);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-password-toggle]").forEach(bindPasswordToggle);
  });
})();
