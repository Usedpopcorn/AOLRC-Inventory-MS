(() => {
  const notePane = document.getElementById("venue-profile-notes-pane");
  if (!notePane) return;

  const filterForm = document.getElementById("venueNotesFilterForm");
  const notePageInput = filterForm?.querySelector('input[name="note_page"]');
  const kindInput = document.getElementById("venueNoteKindInput");
  const filterButtons = Array.from(notePane.querySelectorAll("[data-note-filter]"));
  const searchInput = document.getElementById("venueNoteSearch");
  const itemFilterSelect = document.getElementById("venueNoteItemFilter");
  const listShell = document.getElementById("venueNotesListShell");
  const composer = document.getElementById("venueNoteComposer");
  const composerCollapse = document.getElementById("venueNoteComposerCollapse");
  const noteTitleInput = document.getElementById("newNoteTitle");
  const noteBodyInput = document.getElementById("newNoteBody");
  const noteTabButton = document.getElementById("venue-profile-notes-tab");
  const noteRefreshBanner = document.getElementById("venueNoteRefreshBanner");
  const noteRefreshBannerTitle = document.getElementById("venueNoteRefreshBannerTitle");
  const noteRefreshBannerBody = document.getElementById("venueNoteRefreshBannerBody");
  const noteRefreshLink = document.getElementById("venueNoteRefreshLink");
  const inlineNoteButtons = Array.from(document.querySelectorAll("[data-venue-inline-note-button]"));
  const inlineNoteModalEl = document.getElementById("venueInlineNoteModal");
  const inlineNoteModalContent = inlineNoteModalEl?.querySelector("[data-venue-inline-note-endpoint]");
  const inlineNoteModalEndpoint = inlineNoteModalContent?.getAttribute("data-venue-inline-note-endpoint") || "";
  const inlineNoteModalTitle = document.getElementById("venueInlineNoteModalLabel");
  const inlineNoteModalSubtitle = document.getElementById("venueInlineNoteModalSubtitle");
  const inlineNoteItemName = document.getElementById("venueInlineNoteItemName");
  const inlineNoteItemMeta = document.getElementById("venueInlineNoteItemMeta");
  const inlineNoteOpenFeedLink = document.getElementById("venueInlineNoteOpenFeedLink");
  const inlineNoteForm = document.getElementById("venueInlineNoteForm");
  const inlineNoteItemIdInput = document.getElementById("venueInlineNoteItemId");
  const inlineNoteScopeInput = document.getElementById("venueInlineNoteScope");
  const inlineNoteTitleInput = document.getElementById("venueInlineNoteTitle");
  const inlineNoteBodyInput = document.getElementById("venueInlineNoteBody");
  const inlineNoteSubmitBtn = document.getElementById("venueInlineNoteSubmitBtn");
  const inlineNoteFeedback = document.getElementById("venueInlineNoteFeedback");
  const inlineNoteFeedbackTitle = document.getElementById("venueInlineNoteFeedbackTitle");
  const inlineNoteFeedbackBody = document.getElementById("venueInlineNoteFeedbackBody");
  const inlineNoteLiveRegion = document.getElementById("venueInlineNoteLiveRegion");
  const inlineNoteModalDismissButtons = Array.from(
    inlineNoteModalEl?.querySelectorAll("[data-bs-dismiss='modal']") || []
  );
  const inlineNoteModal = inlineNoteModalEl && window.bootstrap?.Modal
    ? window.bootstrap.Modal.getOrCreateInstance(inlineNoteModalEl)
    : null;
  const initialFocus = listShell?.dataset.noteFocus || "";
  const searchDebounceMs = 220;
  let activeInlineNoteButton = null;
  let initialFocusApplied = false;
  let searchDebounceHandle = null;
  let manualInlineNoteBackdrop = null;

  function resetToFirstPage() {
    if (notePageInput) notePageInput.value = "1";
  }

  function submitFilters() {
    if (!filterForm) return;
    resetToFirstPage();
    filterForm.requestSubmit();
  }

  function focusTarget() {
    if (initialFocusApplied || !notePane.classList.contains("show")) return;
    initialFocusApplied = true;

    let target = listShell;
    if (initialFocus === "compose" && composer) {
      if (composerCollapse && window.bootstrap?.Collapse) {
        const collapseInstance = window.bootstrap.Collapse.getOrCreateInstance(composerCollapse, {
          toggle: false,
        });
        collapseInstance.show();
      }
      target = composer;
    }

    if (!target) return;

    window.requestAnimationFrame(() => {
      target.scrollIntoView({ block: "center", behavior: "smooth" });
      if (initialFocus === "compose") {
        (noteTitleInput || noteBodyInput)?.focus({ preventScroll: true });
      }
    });
  }

  function parseNoteCount(value) {
    const parsed = Number.parseInt(value || "", 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  }

  function buildNoteLabel(noteCount) {
    return `${noteCount} note${noteCount === 1 ? "" : "s"}`;
  }

  function buildInlineNoteAriaLabel(itemName, noteCount) {
    if (noteCount > 0) {
      return `Add note for ${itemName}. ${buildNoteLabel(noteCount)} already added.`;
    }
    return `Add note for ${itemName}`;
  }

  function buildInlineNoteTitle(noteCount) {
    return noteCount > 0 ? `Add note (${buildNoteLabel(noteCount)} existing)` : "Add note";
  }

  function buildNotesTabUrl(itemId) {
    const nextUrl = new URL(window.location.href);
    nextUrl.searchParams.set("profile_tab", "notes");
    nextUrl.searchParams.set("note_focus", "list");
    if (itemId) nextUrl.searchParams.set("note_item_id", itemId);
    else nextUrl.searchParams.delete("note_item_id");
    nextUrl.searchParams.delete("note_page");
    return `${nextUrl.pathname}${nextUrl.search}`;
  }

  function updateInlineNoteButtons(itemId, noteCount, itemName) {
    if (!itemId) return;

    document.querySelectorAll(`[data-venue-note-item-id="${itemId}"][data-venue-inline-note-button]`).forEach((button) => {
      const resolvedName = itemName || button.dataset.venueNoteItemName || "Tracked item";
      button.dataset.venueNoteItemName = resolvedName;
      button.dataset.noteCount = String(noteCount);
      button.setAttribute("aria-label", buildInlineNoteAriaLabel(resolvedName, noteCount));
      button.setAttribute("title", buildInlineNoteTitle(noteCount));

      const icon = button.querySelector("i");
      if (!icon) return;
      icon.classList.remove("bi-journal-plus", "bi-journal-text");
      icon.classList.add(noteCount > 0 ? "bi-journal-text" : "bi-journal-plus");
    });
  }

  function updateNoteBadges(itemId, noteCount, itemName) {
    if (!itemId || noteCount <= 0) return;

    document.querySelectorAll(`[data-venue-note-badge-item-id="${itemId}"]`).forEach((link) => {
      const resolvedName = itemName || link.dataset.venueNoteItemName || "Tracked item";
      const noteLabel = buildNoteLabel(noteCount);
      link.dataset.noteCount = String(noteCount);
      link.dataset.venueNoteItemName = resolvedName;
      link.href = buildNotesTabUrl(itemId);
      link.setAttribute("aria-label", `View ${noteLabel} for ${resolvedName}`);
      link.setAttribute("title", "View notes");

      const countLabel = link.querySelector("span:last-child");
      if (countLabel) {
        countLabel.textContent = noteLabel;
      }
    });
  }

  function clearInlineNoteFeedback() {
    if (!inlineNoteFeedback) return;
    inlineNoteFeedback.classList.add("d-none");
    if (inlineNoteFeedbackTitle) {
      inlineNoteFeedbackTitle.textContent = "Could not save note";
    }
    if (inlineNoteFeedbackBody) {
      inlineNoteFeedbackBody.textContent = "";
    }
  }

  function showInlineNoteFeedback(message, title = "Could not save note") {
    if (!inlineNoteFeedback) return;
    if (inlineNoteFeedbackTitle) {
      inlineNoteFeedbackTitle.textContent = title;
    }
    if (inlineNoteFeedbackBody) {
      inlineNoteFeedbackBody.textContent = message;
    }
    inlineNoteFeedback.classList.remove("d-none");
  }

  function resetInlineNoteForm() {
    inlineNoteForm?.reset();
    clearInlineNoteFeedback();
    if (inlineNoteSubmitBtn) {
      inlineNoteSubmitBtn.disabled = false;
      inlineNoteSubmitBtn.textContent = "Save Note";
    }
  }

  function showInlineNoteModalShell() {
    if (!inlineNoteModalEl) return;
    if (inlineNoteModal) {
      inlineNoteModal.show();
      return;
    }

    inlineNoteModalEl.style.display = "block";
    inlineNoteModalEl.removeAttribute("aria-hidden");
    inlineNoteModalEl.setAttribute("aria-modal", "true");
    inlineNoteModalEl.classList.add("show");
    document.body.classList.add("modal-open");

    if (!manualInlineNoteBackdrop) {
      manualInlineNoteBackdrop = document.createElement("div");
      manualInlineNoteBackdrop.className = "modal-backdrop fade show";
      document.body.appendChild(manualInlineNoteBackdrop);
    }

    window.setTimeout(() => inlineNoteTitleInput?.focus(), 0);
  }

  function hideInlineNoteModalShell() {
    if (!inlineNoteModalEl) return;
    if (inlineNoteModal) {
      inlineNoteModal.hide();
      return;
    }

    inlineNoteModalEl.classList.remove("show");
    inlineNoteModalEl.style.display = "none";
    inlineNoteModalEl.setAttribute("aria-hidden", "true");
    inlineNoteModalEl.removeAttribute("aria-modal");
    document.body.classList.remove("modal-open");
    if (manualInlineNoteBackdrop) {
      manualInlineNoteBackdrop.remove();
      manualInlineNoteBackdrop = null;
    }

    resetInlineNoteForm();
    if (activeInlineNoteButton) {
      activeInlineNoteButton.focus();
    }
    activeInlineNoteButton = null;
  }

  function showNotesRefreshBanner(itemId, itemName) {
    if (!noteRefreshBanner) return;
    if (noteRefreshBannerTitle) {
      noteRefreshBannerTitle.textContent = "New note added";
    }
    if (noteRefreshBannerBody) {
      noteRefreshBannerBody.textContent = itemName
        ? `${itemName} has a newly added note from the overview list.`
        : "An overview note was added.";
    }
    if (noteRefreshLink) {
      noteRefreshLink.href = buildNotesTabUrl(itemId);
    }
    noteRefreshBanner.classList.remove("d-none");
  }

  function primeInlineNoteModal(button) {
    if (!button) return;

    activeInlineNoteButton = button;
    const itemId = button.dataset.venueNoteItemId || "";
    const itemName = button.dataset.venueNoteItemName || "Tracked item";
    const noteCount = parseNoteCount(button.dataset.noteCount);

    resetInlineNoteForm();
    if (inlineNoteModalTitle) {
      inlineNoteModalTitle.textContent = `Add Note for ${itemName}`;
    }
    if (inlineNoteModalSubtitle) {
      inlineNoteModalSubtitle.textContent = noteCount > 0
        ? `${buildNoteLabel(noteCount)} already live on this item. Add more context without leaving the overview.`
        : "Save item-specific venue context without leaving the overview.";
    }
    if (inlineNoteItemName) {
      inlineNoteItemName.textContent = itemName;
    }
    if (inlineNoteItemMeta) {
      inlineNoteItemMeta.textContent = noteCount > 0
        ? "New notes stay tagged to this item and will appear in the venue notes feed after refresh."
        : "This note will be attached to the current venue and tagged to the selected item.";
    }
    if (inlineNoteOpenFeedLink) {
      inlineNoteOpenFeedLink.href = buildNotesTabUrl(itemId);
      inlineNoteOpenFeedLink.classList.toggle("d-none", !itemId);
    }
    if (inlineNoteItemIdInput) {
      inlineNoteItemIdInput.value = itemId;
    }
    if (inlineNoteScopeInput) {
      inlineNoteScopeInput.value = itemName;
    }
  }

  function openInlineNoteModal(button) {
    if (!inlineNoteModalEl || !button) return;
    primeInlineNoteModal(button);
    showInlineNoteModalShell();
  }

  filterButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (kindInput) kindInput.value = button.dataset.noteFilter || "all";
      submitFilters();
    });
  });

  itemFilterSelect?.addEventListener("change", submitFilters);

  searchInput?.addEventListener("input", () => {
    if (searchDebounceHandle) {
      window.clearTimeout(searchDebounceHandle);
    }
    searchDebounceHandle = window.setTimeout(() => {
      searchDebounceHandle = null;
      submitFilters();
    }, searchDebounceMs);
  });

  searchInput?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (searchDebounceHandle) {
      window.clearTimeout(searchDebounceHandle);
      searchDebounceHandle = null;
    }
    submitFilters();
  });

  if (inlineNoteModalEl && inlineNoteForm) {
    inlineNoteButtons.forEach((button) => {
      button.addEventListener("click", (event) => {
        if (event.defaultPrevented) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        event.stopPropagation();
        openInlineNoteModal(button);
      });

      button.addEventListener("keydown", (event) => {
        const isActivationKey = event.key === "Enter" || event.key === " ";
        if (!isActivationKey) return;
        event.preventDefault();
        event.stopPropagation();
        openInlineNoteModal(button);
      });
    });

    if (inlineNoteModalEl && inlineNoteModal) {
      inlineNoteModalEl.addEventListener("hidden.bs.modal", () => {
        resetInlineNoteForm();
        if (activeInlineNoteButton) {
          activeInlineNoteButton.focus();
        }
        activeInlineNoteButton = null;
      });
    }

    inlineNoteModalDismissButtons.forEach((button) => {
      button.addEventListener("click", (event) => {
        if (inlineNoteModal) return;
        event.preventDefault();
        hideInlineNoteModalShell();
      });
    });

    if (inlineNoteModalEl && !inlineNoteModal) {
      inlineNoteModalEl.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        hideInlineNoteModalShell();
      });
    }

    inlineNoteForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearInlineNoteFeedback();

      if (!inlineNoteModalEndpoint) {
        showInlineNoteFeedback("The note form is missing its save endpoint. Refresh and try again.");
        return;
      }

      if (inlineNoteSubmitBtn) {
        inlineNoteSubmitBtn.disabled = true;
        inlineNoteSubmitBtn.textContent = "Saving...";
      }

      try {
        const response = await fetch(inlineNoteModalEndpoint, {
          method: "POST",
          headers: {
            "Accept": "application/json",
          },
          body: new FormData(inlineNoteForm),
        });
        const contentType = (response.headers.get("content-type") || "").toLowerCase();
        const payload = contentType.includes("application/json")
          ? await response.json()
          : null;

        if (!response.ok || !payload || payload.status !== "success") {
          showInlineNoteFeedback(
            payload?.error || "Your session may have expired. Refresh the page and try again."
          );
          return;
        }

        const itemId = String(payload.item_id || inlineNoteItemIdInput?.value || "");
        const itemName = payload.item_name || activeInlineNoteButton?.dataset.venueNoteItemName || "Tracked item";
        const noteCount = Math.max(1, parseNoteCount(String(payload.note_count || "1")));
        updateInlineNoteButtons(itemId, noteCount, itemName);
        updateNoteBadges(itemId, noteCount, itemName);
        showNotesRefreshBanner(itemId, itemName);
        if (inlineNoteLiveRegion) {
          inlineNoteLiveRegion.textContent = `${payload.message || "Note added."} ${itemName}.`;
        }
        hideInlineNoteModalShell();
      } catch (error) {
        showInlineNoteFeedback("Could not reach the server. Try again in a moment.");
      } finally {
        if (inlineNoteSubmitBtn) {
          inlineNoteSubmitBtn.disabled = false;
          inlineNoteSubmitBtn.textContent = "Save Note";
        }
      }
    });
  }

  noteTabButton?.addEventListener("shown.bs.tab", focusTarget);
  composerCollapse?.addEventListener("shown.bs.collapse", () => {
    if (!notePane.classList.contains("show")) return;
    window.requestAnimationFrame(() => {
      (noteTitleInput || noteBodyInput)?.focus({ preventScroll: true });
    });
  });

  focusTarget();
})();
