(() => {
  const modalEl = document.getElementById("supplyNotesModal");
  const modalContentEl = document.getElementById("supplyNotesModalContent");
  if (!modalEl || !modalContentEl) return;

  const bootstrapApi = window.bootstrap;
  const modalInstance = bootstrapApi?.Modal
    ? bootstrapApi.Modal.getOrCreateInstance(modalEl)
    : null;
  let requestCounter = 0;

  function buildPageUrl(itemId = "", noteFocus = "") {
    const nextUrl = new URL(window.location.href);
    if (itemId) {
      nextUrl.searchParams.set("note_item_id", itemId);
      if (noteFocus) nextUrl.searchParams.set("note_focus", noteFocus);
      else nextUrl.searchParams.delete("note_focus");
    } else {
      nextUrl.searchParams.delete("note_item_id");
      nextUrl.searchParams.delete("note_focus");
    }
    return `${nextUrl.pathname}${nextUrl.search}`;
  }

  function replacePageUrl(itemId = "", noteFocus = "") {
    window.history.replaceState({}, "", buildPageUrl(itemId, noteFocus));
  }

  function showModalShell() {
    if (modalInstance) {
      modalInstance.show();
      return;
    }

    modalEl.classList.add("show");
    modalEl.style.display = "block";
    modalEl.removeAttribute("aria-hidden");
    document.body.classList.add("modal-open");
  }

  function hideModalShell() {
    replacePageUrl();
    modalEl.dataset.activeNoteItemId = "";
    modalEl.dataset.noteFocus = "";

    if (modalInstance) {
      modalInstance.hide();
      return;
    }

    modalEl.classList.remove("show");
    modalEl.style.display = "none";
    modalEl.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
  }

  function setLoadingState() {
    modalContentEl.innerHTML = `
      <div class="supply-notes-modal-loading">
        <div class="spinner-border spinner-border-sm text-secondary" role="status" aria-hidden="true"></div>
        <span>Loading notes...</span>
      </div>
    `;
  }

  function getModalStateEl() {
    return modalContentEl.querySelector("[data-supply-note-modal-root]");
  }

  function getModalState() {
    const stateEl = getModalStateEl();
    if (!stateEl) return null;
    return {
      element: stateEl,
      itemId: stateEl.dataset.noteItemId || "",
      noteCount: Number(stateEl.dataset.noteCount || 0),
      noteFocus: stateEl.dataset.noteFocus || "list",
      editorMode: stateEl.dataset.editorMode || "create",
    };
  }

  function getComposerElements() {
    return {
      collapseEl: modalContentEl.querySelector("#supplyNoteComposerCollapse"),
      actionInput: modalContentEl.querySelector("[data-supply-note-action-input]"),
      noteIdInput: modalContentEl.querySelector("[data-supply-note-id-input]"),
      titleInput: modalContentEl.querySelector("[data-supply-note-title-input]"),
      bodyInput: modalContentEl.querySelector("[data-supply-note-body-input]"),
      titleLabel: modalContentEl.querySelector("[data-supply-note-compose-title]"),
      description: modalContentEl.querySelector("[data-supply-note-compose-description]"),
      cancelButton: modalContentEl.querySelector("[data-supply-note-cancel-edit]"),
      submitButton: modalContentEl.querySelector("[data-supply-note-submit-label]"),
      toggleButton: modalContentEl.querySelector("[data-supply-note-compose-toggle]"),
    };
  }

  function setComposeMode(mode, options = {}) {
    const {
      actionInput,
      noteIdInput,
      titleInput,
      bodyInput,
      titleLabel,
      description,
      cancelButton,
      submitButton,
    } = getComposerElements();
    const state = getModalState();
    const isEditMode = mode === "edit";

    if (actionInput) actionInput.value = isEditMode ? "edit_note" : "create_note";
    if (noteIdInput) noteIdInput.value = isEditMode ? options.noteId || "" : "";
    if (titleInput) titleInput.value = options.title ?? "";
    if (bodyInput) bodyInput.value = options.body ?? "";
    if (titleLabel) titleLabel.textContent = isEditMode ? "Edit Note" : "New Note";
    if (description) {
      description.textContent = isEditMode
        ? "Update this note for all venues using this supply item."
        : "Add cross-venue context or reminders for this supply item.";
    }
    if (cancelButton) cancelButton.classList.toggle("d-none", !isEditMode);
    if (submitButton) submitButton.textContent = isEditMode ? "Save Changes" : "Save Note";
    if (state) {
      state.element.dataset.editorMode = mode;
      state.element.dataset.noteFocus = "compose";
    }
  }

  function showComposer() {
    const { collapseEl, toggleButton } = getComposerElements();
    if (!collapseEl) return;
    if (bootstrapApi?.Collapse) {
      const collapseInstance = bootstrapApi.Collapse.getOrCreateInstance(collapseEl, {
        toggle: false,
      });
      collapseInstance.show();
    } else {
      collapseEl.classList.add("show");
    }
    toggleButton?.setAttribute("aria-expanded", "true");
  }

  function hideComposer() {
    const { collapseEl, toggleButton } = getComposerElements();
    if (!collapseEl) return;
    if (bootstrapApi?.Collapse) {
      const collapseInstance = bootstrapApi.Collapse.getOrCreateInstance(collapseEl, {
        toggle: false,
      });
      collapseInstance.hide();
    } else {
      collapseEl.classList.remove("show");
    }
    toggleButton?.setAttribute("aria-expanded", "false");
  }

  function focusComposer() {
    const { titleInput, bodyInput } = getComposerElements();
    window.requestAnimationFrame(() => {
      (titleInput || bodyInput)?.focus({ preventScroll: true });
    });
  }

  function resetComposer() {
    setComposeMode("create", { title: "", body: "" });
    showComposer();
    focusComposer();
  }

  function enterEditMode(trigger) {
    let noteTitle = "";
    let noteBody = "";

    try {
      noteTitle = JSON.parse(trigger.getAttribute("data-note-title") || "\"\"");
    } catch {
      noteTitle = "";
    }

    try {
      noteBody = JSON.parse(trigger.getAttribute("data-note-body") || "\"\"");
    } catch {
      noteBody = "";
    }

    setComposeMode("edit", {
      noteId: trigger.getAttribute("data-note-id") || "",
      title: noteTitle,
      body: noteBody,
    });
    showComposer();
    focusComposer();
  }

  function updateTriggerState(itemId, noteCount) {
    if (!itemId) return;

    document.querySelectorAll(`[data-supply-note-item-id="${itemId}"]`).forEach((trigger) => {
      const hasNotes = noteCount > 0;
      const canManage = trigger.dataset.supplyNoteCanManage === "true";
      const noteFocus = hasNotes ? "list" : "compose";
      const baseUrl = trigger.getAttribute("data-supply-note-base-url");
      const itemName = trigger.dataset.supplyItemName || "this item";
      const noteLabel = `${noteCount} note${noteCount === 1 ? "" : "s"}`;
      const href = buildPageUrl(itemId, noteFocus);

      if (!hasNotes && !canManage) return;

      trigger.setAttribute("href", href);
      trigger.dataset.supplyNoteFocus = noteFocus;
      if (baseUrl) {
        const modalUrl = new URL(baseUrl, window.location.origin);
        modalUrl.searchParams.set("note_focus", noteFocus);
        trigger.setAttribute("data-supply-note-url", `${modalUrl.pathname}${modalUrl.search}`);
      }

      if (hasNotes) {
        trigger.setAttribute("data-note-count", String(noteCount));
        trigger.setAttribute("title", noteLabel[0].toUpperCase() + noteLabel.slice(1));
        trigger.setAttribute("aria-label", `View ${noteLabel} for ${itemName}`);
      } else {
        trigger.removeAttribute("data-note-count");
        trigger.setAttribute("title", "Add note");
        trigger.setAttribute("aria-label", `Add note for ${itemName}`);
      }

      trigger.innerHTML = `<i class="bi ${hasNotes ? "bi-journal-text" : "bi-journal-plus"}" aria-hidden="true"></i>`;
    });
  }

  function syncModalState() {
    const state = getModalState();
    if (!state) return;

    updateTriggerState(state.itemId, state.noteCount);

    if (state.noteFocus === "compose") {
      showComposer();
      focusComposer();
    } else {
      hideComposer();
    }
  }

  async function requestModalContent(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
      },
      ...options,
    });
    const html = await response.text();
    return { html, response };
  }

  async function loadModal(url, { showModal = true } = {}) {
    const requestId = ++requestCounter;
    setLoadingState();

    if (showModal) {
      replacePageUrl();
      showModalShell();
    }

    try {
      const { html } = await requestModalContent(url);
      if (requestId !== requestCounter) return;
      modalContentEl.innerHTML = html;
      syncModalState();
    } catch {
      if (requestId !== requestCounter) return;
      modalContentEl.innerHTML = `
        <div class="modal-header supply-notes-modal-header">
          <div class="supply-notes-modal-heading">
            <h2 class="modal-title supply-notes-modal-title" id="supplyNotesModalLabel">Supply Notes</h2>
            <p class="supply-notes-modal-subtitle mb-0">Unable to load notes right now.</p>
          </div>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body supply-notes-modal-body">
          <div class="ui-empty-state ui-empty-state-compact">
            <div class="ui-empty-state-icon" aria-hidden="true"><i class="bi bi-exclamation-circle"></i></div>
            <div class="ui-empty-state-copy">
              <div class="ui-empty-state-title">Notes could not be loaded.</div>
              <div class="ui-empty-state-body">Try opening the note again in a moment.</div>
            </div>
          </div>
        </div>
      `;
    }
  }

  async function handleNoteFormSubmit(form) {
    const confirmMessage = form.getAttribute("data-confirm-message");
    if (confirmMessage && !window.confirm(confirmMessage)) {
      return;
    }

    const formData = new FormData(form);
    const activeItemId = String(formData.get("item_id") || "");

    setLoadingState();

    try {
      const { html } = await requestModalContent(form.action, {
        method: "POST",
        body: formData,
      });
      modalContentEl.innerHTML = html;
      syncModalState();

      const state = getModalState();
      updateTriggerState(state?.itemId || activeItemId, state?.noteCount || 0);
    } catch {
      modalContentEl.innerHTML = `
        <div class="modal-header supply-notes-modal-header">
          <div class="supply-notes-modal-heading">
            <h2 class="modal-title supply-notes-modal-title" id="supplyNotesModalLabel">Supply Notes</h2>
            <p class="supply-notes-modal-subtitle mb-0">Unable to save note changes right now.</p>
          </div>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body supply-notes-modal-body">
          <div class="ui-empty-state ui-empty-state-compact">
            <div class="ui-empty-state-icon" aria-hidden="true"><i class="bi bi-exclamation-circle"></i></div>
            <div class="ui-empty-state-copy">
              <div class="ui-empty-state-title">Note changes were not saved.</div>
              <div class="ui-empty-state-body">Try again in a moment.</div>
            </div>
          </div>
        </div>
      `;
    }
  }

  document.addEventListener("click", (event) => {
    const noteTrigger = event.target.closest("[data-supply-note-url]");
    if (noteTrigger) {
      event.preventDefault();
      event.stopPropagation();

      loadModal(noteTrigger.getAttribute("data-supply-note-url"), {
        showModal: !modalEl.contains(noteTrigger),
      });
      return;
    }

    if (!modalEl.contains(event.target)) return;

    const dismissButton = event.target.closest("[data-bs-dismiss='modal']");
    if (dismissButton) {
      event.preventDefault();
      event.stopPropagation();
      hideModalShell();
      return;
    }

    const editTrigger = event.target.closest("[data-supply-note-edit]");
    if (editTrigger) {
      event.preventDefault();
      enterEditMode(editTrigger);
      return;
    }

    const cancelEditButton = event.target.closest("[data-supply-note-cancel-edit]");
    if (cancelEditButton) {
      event.preventDefault();
      resetComposer();
      return;
    }

    const composeToggleButton = event.target.closest("[data-supply-note-compose-toggle]");
    if (composeToggleButton) {
      const { actionInput } = getComposerElements();
      if (actionInput?.value === "edit_note") {
        setComposeMode("create", { title: "", body: "" });
      }
    }
  });

  modalEl.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-supply-note-form]");
    if (!form) return;
    event.preventDefault();
    handleNoteFormSubmit(form);
  });

  modalEl.addEventListener("shown.bs.collapse", (event) => {
    if (event.target.id !== "supplyNoteComposerCollapse") return;
    const state = getModalState();
    if (!state) return;
    state.element.dataset.noteFocus = "compose";
    focusComposer();
  });

  modalEl.addEventListener("hidden.bs.collapse", (event) => {
    if (event.target.id !== "supplyNoteComposerCollapse") return;
    const state = getModalState();
    if (!state) return;
    state.element.dataset.noteFocus = "list";
  });

  modalEl.addEventListener("hidden.bs.modal", () => {
    replacePageUrl();
    modalEl.dataset.activeNoteItemId = "";
    modalEl.dataset.noteFocus = "";
  });

  const initialItemId = modalEl.dataset.activeNoteItemId || "";
  if (initialItemId) {
    const noteFocus = modalEl.dataset.noteFocus || "list";
    const initialUrl = new URL(modalContentEl.dataset.modalEndpoint, window.location.origin);
    initialUrl.searchParams.set("item_id", initialItemId);
    initialUrl.searchParams.set("note_focus", noteFocus);
    loadModal(`${initialUrl.pathname}${initialUrl.search}`);
  }
})();
