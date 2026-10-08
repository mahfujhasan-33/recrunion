const uploadForm = document.querySelector("#application-upload-form");
const fileInput = document.querySelector("#application-files");
const selectedFiles = document.querySelector("#selected-application-files");
const feedback = document.querySelector("#application-feedback");
const summary = document.querySelector("#application-upload-summary");
const resultList = document.querySelector("#application-upload-results");
const tableBody = document.querySelector("#applications-table-body");
const applicationCount = document.querySelector("#application-count");

function showFeedback(message) {
  feedback.textContent = message;
  feedback.hidden = false;
}

function formatBytes(value) {
  return `${(value / 1024).toFixed(1)} KiB`;
}

function renderSelectedFiles() {
  selectedFiles.replaceChildren();
  const files = [...fileInput.files];
  if (!files.length) {
    const item = document.createElement("li");
    item.textContent = "No files selected.";
    selectedFiles.append(item);
    return;
  }
  files.forEach((file) => {
    const item = document.createElement("li");
    item.textContent = `${file.name} · ${formatBytes(file.size)}`;
    selectedFiles.append(item);
  });
}

function addCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.append(cell);
  return cell;
}

function renderApplications(applications) {
  tableBody.replaceChildren();
  applicationCount.textContent = String(applications.length);
  if (!applications.length) {
    const row = document.createElement("tr");
    const cell = addCell(row, "No applications have been imported.");
    cell.colSpan = 7;
    tableBody.append(row);
    return;
  }
  applications.forEach((application) => {
    const row = document.createElement("tr");
    addCell(row, application.reference);
    addCell(row, application.candidate_display_reference);
    addCell(row, application.original_filename);
    addCell(row, formatBytes(application.file_size));
    addCell(row, application.source.replaceAll("_", " "));
    const statusCell = addCell(row, "");
    const badge = document.createElement("span");
    badge.className = "status-badge";
    badge.textContent = application.status;
    statusCell.append(badge);
    addCell(row, new Date(application.imported_at).toLocaleString());
    tableBody.append(row);
  });
}

async function refreshApplications() {
  const response = await fetch(`/api/v1/jobs/${uploadForm.dataset.jobId}/applications`);
  if (!response.ok) return;
  renderApplications(await response.json());
}

function renderSummary(body) {
  ["received", "imported", "duplicates", "invalid", "failed"].forEach((field) => {
    document.querySelector(`#summary-${field}`).textContent = String(body[field]);
  });
  resultList.replaceChildren();
  body.files.forEach((file) => {
    const item = document.createElement("li");
    item.textContent = `${file.filename}: ${file.outcome.replaceAll("_", " ")} — ${file.message}`;
    resultList.append(item);
  });
  summary.hidden = false;
}

fileInput?.addEventListener("change", renderSelectedFiles);

uploadForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  feedback.hidden = true;
  const button = document.querySelector("#upload-applications");
  button.disabled = true;
  try {
    const response = await fetch(
      `/api/v1/jobs/${uploadForm.dataset.jobId}/applications/upload`,
      { method: "POST", body: new FormData(uploadForm) },
    );
    const body = await response.json();
    if (!response.ok) throw new Error(body.message || "Unable to upload the selected CVs.");
    renderSummary(body);
    await refreshApplications();
    uploadForm.reset();
    renderSelectedFiles();
  } catch (error) {
    showFeedback(error.message);
  } finally {
    button.disabled = false;
  }
});
