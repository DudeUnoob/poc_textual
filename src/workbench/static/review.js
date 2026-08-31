const form = document.querySelector("[data-review-form]");

if (form) {
  const reviewer = form.querySelector("[data-reviewer]");
  const values = [...form.querySelectorAll("[data-review-value]")];
  const value = values[0];
  const storageKey = "census-workbench-reviewer";
  const savedReviewer = localStorage.getItem(storageKey);

  if (savedReviewer) reviewer.value = savedReviewer;
  (savedReviewer && value ? value : reviewer).focus();

  form.addEventListener("submit", () => {
    if (reviewer.value.trim()) localStorage.setItem(storageKey, reviewer.value.trim());
  });

  reviewer.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (value) value.focus();
    }
  });

  values.forEach((input, index) => input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      const next = values[index + 1];
      if (next) next.focus();
      else form.querySelector("[data-save-row], [data-shortcut=enter]")?.focus();
    }
  }));

  document.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && form.matches("[data-row-review]")) {
      event.preventDefault();
      form.requestSubmit();
      return;
    }
    const isTyping = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
    if (isTyping || event.metaKey || event.ctrlKey || event.altKey) return;
    const key = event.key.toLowerCase();
    const shortcut = key === "enter" ? "enter" : key;
    const button = form.querySelector(`[data-shortcut="${shortcut}"]`);
    if (button) {
      event.preventDefault();
      button.click();
    }
  });
}
