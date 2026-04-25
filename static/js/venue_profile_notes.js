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
  const initialFocus = listShell?.dataset.noteFocus || "";
  let initialFocusApplied = false;
  let searchDebounceHandle = null;
  const searchDebounceMs = 220;

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

  document.getElementById("venue-profile-notes-tab")?.addEventListener("shown.bs.tab", focusTarget);
  composerCollapse?.addEventListener("shown.bs.collapse", () => {
    if (!notePane.classList.contains("show")) return;
    window.requestAnimationFrame(() => {
      (noteTitleInput || noteBodyInput)?.focus({ preventScroll: true });
    });
  });

  focusTarget();
})();
