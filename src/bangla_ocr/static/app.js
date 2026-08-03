document.addEventListener("DOMContentLoaded", () => {
  const input = document.querySelector("#pdf");
  if (input) {
    const dropzone = input.closest(".dropzone");
    input.addEventListener("change", () => {
      const strong = dropzone?.querySelector("strong");
      if (strong && input.files?.length) strong.textContent = input.files[0].name;
    });
    ["dragenter", "dragover"].forEach((eventName) => {
      dropzone?.addEventListener(eventName, () => dropzone.classList.add("dragging"));
    });
    ["dragleave", "drop"].forEach((eventName) => {
      dropzone?.addEventListener(eventName, () => dropzone.classList.remove("dragging"));
    });
  }

  const pageMode = document.querySelector("#page-mode");
  const customPages = document.querySelector(".custom-pages");
  const syncPageMode = () => {
    customPages?.classList.toggle("hidden", pageMode?.value !== "custom");
  };
  pageMode?.addEventListener("change", syncPageMode);
  syncPageMode();

  const ocrEngine = document.querySelector("#ocr-engine");
  const ocrGate = document.querySelector(".engine-gate");
  const ocrTitle = document.querySelector("#ocr-engine-title");
  const ocrReason = document.querySelector("#ocr-engine-reason");
  const ocrSubmit = document.querySelector(".import-card button[type='submit']");
  const ocrSubmitLabel = document.querySelector("#ocr-submit-label");
  const ocrDialog = document.querySelector("#ocr-unavailable-dialog");
  const ocrDialogTitle = document.querySelector("#ocr-dialog-title");
  const ocrDialogReason = document.querySelector("#ocr-dialog-reason");
  const ocrDialogClose = document.querySelector("#ocr-dialog-close");
  let lastAvailableEngine = null;
  const syncOcrEngine = (showUnavailablePopup = false) => {
    if (!ocrEngine) return;
    const option = ocrEngine.options[ocrEngine.selectedIndex];
    const available = option?.dataset.available === "true";
    const label = option?.dataset.label || option?.textContent?.trim() || "OCR";
    const reason = option?.dataset.reason || "No availability information.";
    if (ocrTitle) ocrTitle.textContent = `${label} ${available ? "ready" : "unavailable"}`;
    if (ocrReason) ocrReason.textContent = reason;
    ocrGate?.classList.toggle("engine-ready", available);
    ocrGate?.classList.toggle("engine-blocked", !available);
    ocrGate?.querySelector(".status-dot")?.classList.toggle("status-good", available);
    ocrGate?.querySelector(".status-dot")?.classList.toggle("status-error", !available);
    if (ocrSubmit) ocrSubmit.disabled = !available;
    if (ocrSubmitLabel) {
      ocrSubmitLabel.textContent = available
        ? `Start OCR with ${label}`
        : "Choose an available OCR model";
    }
    if (available) {
      lastAvailableEngine = option.value;
      return;
    }
    if (showUnavailablePopup) {
      if (ocrDialogTitle) ocrDialogTitle.textContent = `${label} is unavailable`;
      if (ocrDialogReason) ocrDialogReason.textContent = reason;
      if (typeof ocrDialog?.showModal === "function") {
        ocrDialog.showModal();
      } else {
        window.alert(`${label} is unavailable.\n\n${reason}`);
      }
    }
  };
  ocrEngine?.addEventListener("change", () => syncOcrEngine(true));
  ocrDialogClose?.addEventListener("click", () => {
    ocrDialog?.close();
    if (lastAvailableEngine) {
      ocrEngine.value = lastAvailableEngine;
      syncOcrEngine(false);
    }
    ocrEngine?.focus();
  });
  syncOcrEngine(false);

  const jobPanel = document.querySelector("[data-job-id]");
  if (jobPanel) {
    const jobId = jobPanel.dataset.jobId;
    const message = jobPanel.querySelector(".job-message");
    const open = jobPanel.querySelector(".job-open");
    const label = jobPanel.querySelector(".job-status-label");
    const percent = jobPanel.querySelector(".job-percent");
    const pageCount = jobPanel.querySelector(".job-page-count");
    const progress = jobPanel.querySelector(".progress-track span");
    const indicator = jobPanel.querySelector(".live-indicator");
    const systemFree = jobPanel.querySelector("[data-resource-system]");
    const outputFree = jobPanel.querySelector("[data-resource-output]");
    const ramFree = jobPanel.querySelector("[data-resource-ram]");
    const lastSpeed = jobPanel.querySelector("[data-speed-last]");
    const averageSpeed = jobPanel.querySelector("[data-speed-average]");
    const eta = jobPanel.querySelector("[data-speed-eta]");
    const updateJob = (job) => {
      const previousStatus = jobPanel.dataset.status;
      const value = Number(job.progress_percent || 0);
      message.textContent = job.message || "Working…";
      label.textContent = String(job.status || "running").replaceAll("_", " ");
      percent.textContent = `${value}%`;
      progress.style.width = `${value}%`;
      jobPanel.dataset.status = job.status;
      if (job.resource_latest?.disks?.system?.free && systemFree) {
        systemFree.textContent = job.resource_latest.disks.system.free;
      }
      if (job.resource_latest?.disks?.output?.free && outputFree) {
        outputFree.textContent = job.resource_latest.disks.output.free;
      }
      if (job.resource_latest?.memory?.physical_available && ramFree) {
        ramFree.textContent = job.resource_latest.memory.physical_available;
      }
      if (job.last_page_duration && lastSpeed) {
        lastSpeed.textContent = job.last_page_duration;
      }
      if (job.average_page_duration && averageSpeed) {
        averageSpeed.textContent = job.average_page_duration;
      }
      if (job.eta && eta) eta.textContent = job.eta;
      if (job.progress_total) {
        pageCount.textContent = `Page ${job.progress_current || 0} of ${job.progress_total}`;
      }
      if (job.status === "complete" && job.redirect_url) {
        indicator?.classList.add("complete");
        open.href = job.redirect_url;
        open.classList.remove("hidden");
        window.setTimeout(() => window.location.replace(job.redirect_url), 350);
        return false;
      }
      if (["failed", "interrupted", "stalled", "preserved"].includes(job.status)) {
        indicator?.classList.add("failed");
        if (!["failed", "interrupted", "stalled", "preserved"].includes(previousStatus)) {
          window.setTimeout(() => window.location.reload(), 250);
        }
        return false;
      }
      return true;
    };
    const poll = async () => {
      try {
        const response = await fetch(`/api/jobs/${jobId}`, {cache: "no-store"});
        if (!response.ok) throw new Error("Job status is unavailable");
        const job = await response.json();
        if (updateJob(job)) window.setTimeout(poll, 1200);
      } catch (_error) {
        message.textContent = "Reconnecting to the OCR job…";
        window.setTimeout(poll, 2500);
      }
    };
    poll();
  }

  document.querySelectorAll("[data-confirm-engine-switch]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const engineLabel = form.dataset.engineLabel || "another OCR engine";
      const confirmed = window.confirm(
        `Continue this document with ${engineLabel}? Completed pages will be kept, ` +
        "and the engine change will be recorded in the audit."
      );
      if (!confirmed) event.preventDefault();
    });
  });

  const pageFilter = document.querySelector("#page-filter");
  const pageCards = Array.from(document.querySelectorAll("[data-page-grid] .page-card"));
  const filterEmpty = document.querySelector("#page-filter-empty");
  const filterPages = () => {
    const value = pageFilter?.value || "all";
    let visible = 0;
    pageCards.forEach((card) => {
      const verified = card.dataset.verified === "true";
      const flagged = card.dataset.flagged === "true";
      const show = value === "all"
        || (value === "verified" && verified)
        || (value === "needs-review" && !verified)
        || (value === "flagged" && flagged);
      card.classList.toggle("hidden", !show);
      if (show) visible += 1;
    });
    filterEmpty?.classList.toggle("hidden", visible !== 0);
  };
  pageFilter?.addEventListener("change", filterPages);

  document.querySelector("[data-page-jump]")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const bookId = form.dataset.bookId;
    const page = Number(form.querySelector("input")?.value);
    if (bookId && Number.isInteger(page) && page > 0) {
      window.location.assign(`/books/${encodeURIComponent(bookId)}/pages/${page}`);
    }
  });

  const selector = document.querySelector("#line-selector");
  const image = document.querySelector("#source-image");
  const highlight = document.querySelector("#region-highlight");
  const stage = document.querySelector(".image-stage");
  const renderHighlight = () => {
    if (!selector || !image || !highlight || !stage) return;
    const option = selector.options[selector.selectedIndex];
    const bbox = option?.dataset.bbox?.split(",").map(Number);
    if (!bbox || bbox.length !== 4 || bbox.some(Number.isNaN)) {
      highlight.style.display = "none";
      return;
    }
    const imageRect = image.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    const sx = imageRect.width / image.naturalWidth;
    const sy = imageRect.height / image.naturalHeight;
    highlight.style.display = "block";
    highlight.style.left = `${imageRect.left - stageRect.left + bbox[0] * sx}px`;
    highlight.style.top = `${imageRect.top - stageRect.top + bbox[1] * sy}px`;
    highlight.style.width = `${(bbox[2] - bbox[0]) * sx}px`;
    highlight.style.height = `${(bbox[3] - bbox[1]) * sy}px`;
  };
  selector?.addEventListener("change", renderHighlight);
  image?.addEventListener("load", renderHighlight);
  window.addEventListener("resize", renderHighlight);

  document.querySelector(".zoom-reset")?.addEventListener("click", () => {
    stage?.classList.toggle("zoomed");
  });

  const copyPageButton = document.querySelector("[data-copy-page-text]");
  copyPageButton?.addEventListener("click", async () => {
    const text = document.querySelector("#text")?.value || "";
    const originalLabel = copyPageButton.textContent;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.querySelector("#text");
        textarea?.focus();
        textarea?.select();
        if (!document.execCommand("copy")) throw new Error("Copy unavailable");
      }
      copyPageButton.textContent = "Copied";
    } catch (_error) {
      copyPageButton.textContent = "Copy failed";
    }
    window.setTimeout(() => {
      copyPageButton.textContent = originalLabel;
    }, 1600);
  });

  const reviewForm = document.querySelector("[data-review-form]");
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && reviewForm) {
      event.preventDefault();
      const saveButton = reviewForm.querySelector('button[name="save_direction"][value="stay"]');
      if (typeof reviewForm.requestSubmit === "function") reviewForm.requestSubmit(saveButton);
    }
  });
});
