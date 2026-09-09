const form = document.querySelector("[data-review-form]");

if (form) {
  const reviewer = form.querySelector("[data-reviewer]");
  const values = [...form.querySelectorAll("[data-review-value]")];
  const value = values[0];
  const storageKey = "census-workbench-reviewer";
  const savedReviewer = localStorage.getItem(storageKey);

  if (savedReviewer && reviewer) reviewer.value = savedReviewer;
  (savedReviewer && value ? value : reviewer || value)?.focus();

  form.addEventListener("submit", () => {
    if (reviewer?.value.trim()) localStorage.setItem(storageKey, reviewer.value.trim());
  });

  const leaseEndpoint = form.querySelector("[data-lease-renew-url]")?.dataset.leaseRenewUrl;
  if (leaseEndpoint) {
    const renewLease = async () => {
      const body = new FormData();
      ["csrf_token", "lease_token", "review_session"].forEach(name => {
        const input = form.elements.namedItem(name);
        if (input?.value) body.append(name, input.value);
      });
      const response = await fetch(leaseEndpoint, {
        method: "POST",
        credentials: "same-origin",
        body,
      });
      if (!response.ok) {
        form.querySelector("[data-save-row]")?.setAttribute("disabled", "");
        throw new Error("This edit reservation expired. Copy your draft, then reload.");
      }
    };
    const interval = window.setInterval(() => renewLease().catch(error => {
      window.clearInterval(interval);
      window.alert(error.message);
    }), 30_000);
    window.addEventListener("pagehide", () => window.clearInterval(interval), {once: true});
  }

  reviewer?.addEventListener("keydown", (event) => {
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
