const documentForm = document.querySelector("#company-document-form");
const documentFeedback = document.querySelector("#document-feedback");

function showDocumentFeedback(message, isError = false) {
  documentFeedback.textContent = message;
  documentFeedback.classList.toggle("success-message", !isError);
  documentFeedback.hidden = false;
}

if (documentForm) {
  documentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = documentForm.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const response = await fetch("/api/v1/company-documents", {
        method: "POST",
        body: new FormData(documentForm),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.message || "Unable to upload the document.");
      showDocumentFeedback("Upload accepted. Processing has started.");
      window.setTimeout(() => window.location.reload(), 750);
    } catch (error) {
      showDocumentFeedback(error.message, true);
      button.disabled = false;
    }
  });
}

document.querySelectorAll(".delete-document").forEach((button) => {
  button.addEventListener("click", async () => {
    const row = button.closest("[data-document-id]");
    button.disabled = true;
    try {
      const response = await fetch(`/api/v1/company-documents/${row.dataset.documentId}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.message || "Unable to delete the document.");
      }
      row.remove();
    } catch (error) {
      showDocumentFeedback(error.message, true);
      button.disabled = false;
    }
  });
});

if (document.querySelector("[data-document-id] .status-badge")) {
  const pending = [...document.querySelectorAll("[data-document-id] .status-badge")].some(
    (badge) => badge.textContent.trim() === "UPLOADED" || badge.textContent.trim() === "PROCESSING",
  );
  if (pending) window.setTimeout(() => window.location.reload(), 3000);
}
