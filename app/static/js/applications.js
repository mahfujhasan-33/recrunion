const uploadForm = document.querySelector("#application-upload-form");
const fileInput = document.querySelector("#application-files");
const selectedFiles = document.querySelector("#selected-application-files");
const feedback = document.querySelector("#application-feedback");
const summary = document.querySelector("#application-upload-summary");
const resultList = document.querySelector("#application-upload-results");
const tableBody = document.querySelector("#applications-table-body");
const applicationCount = document.querySelector("#application-count");
const processPendingButton = document.querySelector("#process-pending-applications");
const processingProgress = document.querySelector("#candidate-processing-progress");
const progressMessage = document.querySelector("#candidate-progress-message");
const progressPercentage = document.querySelector("#candidate-progress-percentage");
const progressBar = document.querySelector("#candidate-progress-bar");
const profilePanel = document.querySelector("#candidate-profile-panel");
const profileTitle = document.querySelector("#candidate-profile-title");
const profileContent = document.querySelector("#candidate-profile-content");
const closeProfileButton = document.querySelector("#close-candidate-profile");

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
    cell.colSpan = 9;
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
    addStatusCell(row, application.status);
    addStatusCell(row, application.processing_status);
    addCell(row, new Date(application.imported_at).toLocaleString());
    const actions = addCell(row, "");
    if (application.processing_status === "READY") {
      actions.append(actionButton("View profile", "profileApplicationId", application.id, true));
    } else if (["IMPORTED", "FAILED", "NEEDS_REVIEW"].includes(application.processing_status)) {
      const label = application.processing_status === "IMPORTED" ? "Process CV" : "Retry processing";
      actions.append(actionButton(label, "processApplicationId", application.id));
    } else {
      const waiting = document.createElement("span");
      waiting.className = "hint";
      waiting.textContent = "Task in progress";
      actions.append(waiting);
    }
    tableBody.append(row);
  });
}

function addStatusCell(row, status) {
  const cell = addCell(row, "");
  const badge = document.createElement("span");
  badge.className = "status-badge";
  if (["FAILED", "NEEDS_REVIEW"].includes(status)) badge.classList.add("warning");
  badge.textContent = status.replaceAll("_", " ");
  cell.append(badge);
}

function actionButton(label, dataName, id, secondary = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `button compact${secondary ? " secondary" : ""}`;
  button.textContent = label;
  button.dataset[dataName] = id;
  return button;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.message || "The request could not be completed.");
  return body;
}

async function refreshApplications() {
  renderApplications(await fetchJson(`/api/v1/jobs/${uploadForm.dataset.jobId}/applications`));
}

function updateProgress(task) {
  processingProgress.hidden = false;
  progressMessage.textContent = task.progress_message;
  progressPercentage.textContent = `${task.progress}%`;
  progressBar.value = task.progress;
}

async function pollTask(taskId) {
  while (true) {
    const task = await fetchJson(`/api/v1/tasks/${taskId}`);
    updateProgress(task);
    if (["COMPLETED", "FAILED"].includes(task.status)) return task;
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}

async function startProcessing(applicationId, button) {
  button.disabled = true;
  feedback.hidden = true;
  try {
    const started = await fetchJson(
      `/api/v1/jobs/${uploadForm.dataset.jobId}/applications/${applicationId}/process`,
      { method: "POST" },
    );
    await refreshApplications();
    const task = await pollTask(started.task_id);
    await refreshApplications();
    if (task.status === "FAILED") throw new Error(task.error_message_safe || "CV processing failed.");
  } catch (error) {
    showFeedback(error.message);
  } finally {
    button.disabled = false;
  }
}

function profileItemText(item) {
  return Object.entries(item)
    .filter(([key, value]) => key !== "evidence_chunk_ids" && value !== null && value !== "")
    .map(([key, value]) => `${key.replaceAll("_", " ")}: ${Array.isArray(value) ? value.join(", ") : value}`)
    .join(" · ");
}

function appendProfileSection(title, items, evidenceById) {
  if (!items.length) return;
  const section = document.createElement("section");
  section.className = "profile-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading);
  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "policy-finding";
    const value = document.createElement("p");
    value.textContent = profileItemText(item);
    card.append(value);
    item.evidence_chunk_ids.forEach((id) => {
      const evidence = evidenceById.get(id);
      if (!evidence) return;
      const quote = document.createElement("blockquote");
      quote.textContent = `Page ${evidence.page_number}: ${evidence.content}`;
      card.append(quote);
    });
    section.append(card);
  });
  profileContent.append(section);
}

async function showProfile(applicationId) {
  feedback.hidden = true;
  try {
    const application = await fetchJson(
      `/api/v1/jobs/${uploadForm.dataset.jobId}/applications/${applicationId}`,
    );
    if (!application.candidate_profile) throw new Error("The candidate profile is not ready.");
    profileContent.replaceChildren();
    profileTitle.textContent = application.candidate_display_reference;
    const profile = application.candidate_profile.profile;
    const evidenceById = new Map(
      application.candidate_profile.evidence.map((item) => [item.id, item]),
    );
    appendProfileSection("Summary", profile.summary ? [profile.summary] : [], evidenceById);
    appendProfileSection("Contact details", profile.contact_details, evidenceById);
    appendProfileSection("Education", profile.education, evidenceById);
    appendProfileSection("Work history", profile.work_history, evidenceById);
    appendProfileSection("Skills", profile.skills, evidenceById);
    appendProfileSection("Certifications", profile.certifications, evidenceById);
    appendProfileSection("Projects", profile.projects, evidenceById);
    appendProfileSection("Technologies and tools", profile.technologies_tools, evidenceById);
    profilePanel.hidden = false;
    profilePanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showFeedback(error.message);
  }
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

tableBody?.addEventListener("click", (event) => {
  const processButton = event.target.closest("[data-process-application-id]");
  if (processButton) {
    startProcessing(processButton.dataset.processApplicationId, processButton);
    return;
  }
  const profileButton = event.target.closest("[data-profile-application-id]");
  if (profileButton) showProfile(profileButton.dataset.profileApplicationId);
});

processPendingButton?.addEventListener("click", async () => {
  processPendingButton.disabled = true;
  feedback.hidden = true;
  try {
    const batch = await fetchJson(
      `/api/v1/jobs/${uploadForm.dataset.jobId}/applications/process-pending`,
      { method: "POST" },
    );
    if (!batch.queued) {
      showFeedback("There are no newly imported CVs waiting for processing.");
      return;
    }
    await refreshApplications();
    await Promise.all(batch.tasks.map((task) => pollTask(task.task_id)));
    await refreshApplications();
  } catch (error) {
    showFeedback(error.message);
  } finally {
    processPendingButton.disabled = false;
  }
});

closeProfileButton?.addEventListener("click", () => {
  profilePanel.hidden = true;
});
