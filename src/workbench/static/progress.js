document.querySelectorAll("[data-run-id]").forEach((card) => {
  const runId = card.dataset.runId;
  let stream;

  function setText(selector, value, fallback = "—") {
    const node = card.querySelector(selector);
    if (node) node.textContent = value ?? fallback;
  }

  function render(data) {
    card.dataset.status = data.status;
    setText("[data-status]", data.status.replaceAll("_", " "));
    setText("[data-message]", data.message);
    setText("[data-completed]", data.completed_pages);
    setText("[data-total]", data.total_pages);
    setText("[data-percent]", `${data.percent}%`);
    setText("[data-retries]", data.retry_count);
    setText("[data-attempt]", data.job_attempt || 1);
    setText("[data-review-items]", data.review_items);
    setText("[data-current-page]", data.current_page ? `Page ${data.current_page}: ${data.current_filename || ""}` : "—");
    setText("[data-current-pass]", data.current_pass ? data.current_pass.replaceAll("_", " ") : "—");
    setText("[data-api-attempt]", data.api_attempt ? `${data.api_attempt}/${data.max_api_attempts}` : "—");
    setText("[data-queue-position]", data.queue_position || "—");
    setText("[data-last-update]", data.last_update ? new Date(data.last_update).toLocaleTimeString() : "—");
    setText("[data-step-elapsed]", data.step_elapsed_seconds == null ? "—" : formatDuration(data.step_elapsed_seconds));

    const bar = card.querySelector("[data-progress-bar]");
    if (bar) {
      bar.style.width = `${data.percent}%`;
      bar.setAttribute("aria-valuenow", data.percent);
    }
    const resume = card.querySelector("[data-resume]");
    if (resume) resume.hidden = !data.can_resume;
    let stalled = card.querySelector("[data-stall-warning]");
    if (!stalled) {
      stalled = document.createElement("p");
      stalled.className = "warn";
      stalled.dataset.stallWarning = "";
      stalled.textContent = "No extraction checkpoint for more than 10 minutes. The request may still be running; check Recent activity before restarting the worker.";
      card.querySelector("[data-message]").after(stalled);
    }
    stalled.hidden = !data.possibly_stalled;
    const error = card.querySelector("[data-error]");
    if (error) {
      error.hidden = !data.error;
      error.querySelector("pre").textContent = data.error || "";
    }
    let action = card.querySelector("[data-action-required]");
    if (!action) {
      action = document.createElement("p");
      action.className = "action-required";
      action.dataset.actionRequired = "";
      card.querySelector("[data-error]").before(action);
    }
    action.textContent = data.action_required || "";
    action.hidden = !data.action_required;
    const timeline = card.querySelector("[data-events]");
    if (timeline) {
      timeline.replaceChildren(...data.events.slice(-6).reverse().map((event) => {
        const item = document.createElement("li");
        const at = event.at ? new Date(event.at).toLocaleTimeString() : "";
        item.textContent = `${at} · ${event.message || event.type}`;
        return item;
      }));
    }
    if (data.terminal && stream) stream.close();
  }

  function formatDuration(totalSeconds) {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`;
  }

  fetch(`/runs/${runId}/status`).then((response) => response.json()).then(render);
  stream = new EventSource(`/runs/${runId}/events`);
  stream.onmessage = (event) => render(JSON.parse(event.data));
});
