(() => {
  const modalEl = document.getElementById("feedbackSubmitModal");
  if (!modalEl) return;

  const formEl = document.getElementById("feedbackSubmitForm");
  const typeInput = document.getElementById("feedbackSubmissionTypeInput");
  const nextInput = document.getElementById("feedbackNextInput");
  const sourcePathInput = document.getElementById("feedbackSourcePathInput");
  const sourceQueryInput = document.getElementById("feedbackSourceQueryInput");
  const titleEl = document.getElementById("feedbackSubmitModalLabel");
  const descriptionEl = document.getElementById("feedbackSubmitModalDescription");
  const helperTextEl = document.getElementById("feedbackSubmitHelperText");
  const submitButtonEl = document.getElementById("feedbackSubmitButton");
  const anonymousRowEl = document.getElementById("feedbackAnonymousRow");
  const anonymousInputEl = document.getElementById("feedbackAnonymousInput");
  const summaryInputEl = document.getElementById("feedbackSummaryInput");

  const triggerButtons = document.querySelectorAll("[data-feedback-trigger]");
  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);

  const COPY_BY_TYPE = {
    feedback: {
      title: "Send Feedback",
      description: "Share a quick idea, improvement, or rough edge you ran into.",
      helperText: "We automatically include the current page so the team has context.",
      submitLabel: "Send Feedback",
      showAnonymous: true,
    },
    bug_report: {
      title: "Report a Bug",
      description: "Describe the bug clearly so the team can reproduce and review it.",
      helperText: "Include what you expected, what happened instead, and any steps that help reproduce it.",
      submitLabel: "Report Bug",
      showAnonymous: false,
    },
  };

  function captureCurrentPage() {
    const currentPath = window.location.pathname || "/dashboard";
    const currentQuery = window.location.search.startsWith("?")
      ? window.location.search.slice(1)
      : window.location.search;
    sourcePathInput.value = currentPath;
    sourceQueryInput.value = currentQuery;
    nextInput.value = currentQuery ? `${currentPath}?${currentQuery}` : currentPath;
  }

  function applySubmissionType(rawType) {
    const submissionType = rawType === "bug_report" ? "bug_report" : "feedback";
    const copy = COPY_BY_TYPE[submissionType];

    typeInput.value = submissionType;
    titleEl.textContent = copy.title;
    descriptionEl.textContent = copy.description;
    helperTextEl.textContent = copy.helperText;
    submitButtonEl.textContent = copy.submitLabel;
    anonymousRowEl.classList.toggle("d-none", !copy.showAnonymous);
    if (!copy.showAnonymous) {
      anonymousInputEl.checked = false;
    }
  }

  triggerButtons.forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      captureCurrentPage();
      applySubmissionType(button.getAttribute("data-submission-type"));
      modal.show();
    });
  });

  modalEl.addEventListener("show.bs.modal", () => {
    captureCurrentPage();
  });

  modalEl.addEventListener("shown.bs.modal", () => {
    summaryInputEl?.focus();
  });

  formEl?.addEventListener("reset", () => {
    window.setTimeout(() => {
      applySubmissionType("feedback");
      captureCurrentPage();
    }, 0);
  });
})();
