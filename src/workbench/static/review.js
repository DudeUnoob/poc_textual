const form = document.querySelector("[data-review-form]");

if (form) {
  const reviewer = form.querySelector("[data-reviewer]");
  const value = form.querySelector("[data-review-value]");
  const storageKey = "census-workbench-reviewer";
  const savedReviewer = localStorage.getItem(storageKey);

  if (savedReviewer) reviewer.value = savedReviewer;
  (savedReviewer ? value : reviewer).focus();

  form.addEventListener("submit", () => {
    if (reviewer.value.trim()) localStorage.setItem(storageKey, reviewer.value.trim());
  });

  reviewer.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      value.focus();
    }
  });

  value.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      form.querySelector('[data-shortcut="enter"]').click();
    }
  });

  document.addEventListener("keydown", (event) => {
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
