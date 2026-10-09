const screeningJobId = document.querySelector("#application-upload-form")?.dataset.jobId;
const screeningFeedback = document.querySelector("#application-feedback");
const screeningTableBody = document.querySelector("#screening-ranking-body");
const screenReadyButton = document.querySelector("#screen-ready-applications");
const screeningProgress = document.querySelector("#screening-progress");
const screeningProgressMessage = document.querySelector("#screening-progress-message");
const screeningProgressPercentage = document.querySelector("#screening-progress-percentage");
const screeningProgressBar = document.querySelector("#screening-progress-bar");
const screeningPanel = document.querySelector("#candidate-screening-panel");
const screeningTitle = document.querySelector("#candidate-screening-title");
const screeningContent = document.querySelector("#candidate-screening-content");
const rankingExplanation = document.querySelector("#candidate-ranking-explanation");

async function screeningFetchJson(url, options = {}) {
  const response = await fetch(url, options);
  let body;
  try {
    body = await response.json();
  } catch {
    if (!response.ok) {
      throw new Error("The screening request failed on the server. Please try again.");
    }
    throw new Error("The screening service returned an invalid response.");
  }
  if (!response.ok) throw new Error(body.message || "The screening request could not be completed.");
  return body;
}

function screeningShowFeedback(message) {
  screeningFeedback.textContent = message;
  screeningFeedback.hidden = false;
}

function screeningUpdateProgress(task) {
  screeningProgress.hidden = false;
  screeningProgressMessage.textContent = task.progress_message;
  screeningProgressPercentage.textContent = `${task.progress}%`;
  screeningProgressBar.value = task.progress;
}

async function screeningPollTask(taskId) {
  while (true) {
    const task = await screeningFetchJson(`/api/v1/tasks/${taskId}`);
    screeningUpdateProgress(task);
    if (["COMPLETED", "FAILED"].includes(task.status)) return task;
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}

function screeningSummary(summary) {
  if (!summary) return "—";
  return `${summary.met} Met · ${summary.partially_met} Partially Met · ${summary.unmet} Unmet`;
}

function screeningCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.append(cell);
  return cell;
}

function screeningStatusCell(row, entry) {
  const cell = screeningCell(row, "");
  const badge = document.createElement("span");
  badge.className = "status-badge";
  const status = entry.screening_status || "NOT SCREENED";
  if (["FAILED", "NOT SCREENED"].includes(status) || !entry.is_current) {
    badge.classList.add("warning");
  }
  badge.textContent = entry.is_current ? status.replaceAll("_", " ") : "STALE";
  cell.append(badge);
}

function renderScreeningRanking(body) {
  screeningTableBody.replaceChildren();
  const entries = [...body.ranked, ...body.unranked];
  if (!entries.length) {
    const row = document.createElement("tr");
    const cell = screeningCell(row, "No applications are available for screening.");
    cell.colSpan = 7;
    screeningTableBody.append(row);
    return;
  }
  entries.forEach((entry) => {
    const row = document.createElement("tr");
    screeningCell(row, entry.rank ? String(entry.rank) : "Not ranked");
    screeningCell(row, entry.candidate_reference);
    screeningCell(row, screeningSummary(entry.required));
    screeningCell(row, screeningSummary(entry.preferred));
    screeningStatusCell(row, entry);
    screeningCell(row, entry.ranking_explanation);
    const actionCell = screeningCell(row, "");
    if (entry.screening_id) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button secondary compact";
      button.textContent = "View screening";
      button.dataset.screeningApplicationId = entry.application_id;
      actionCell.append(button);
    }
    screeningTableBody.append(row);
  });
}

async function refreshScreeningRanking() {
  if (!screeningJobId) return;
  const body = await screeningFetchJson(`/api/v1/jobs/${screeningJobId}/screening`);
  renderScreeningRanking(body);
}

async function startCandidateScreening(applicationId, button) {
  button.disabled = true;
  screeningFeedback.hidden = true;
  try {
    const started = await screeningFetchJson(
      `/api/v1/jobs/${screeningJobId}/applications/${applicationId}/screen`,
      { method: "POST" },
    );
    const task = await screeningPollTask(started.task_id);
    await refreshScreeningRanking();
    if (task.status === "FAILED") {
      throw new Error(task.error_message_safe || "Candidate screening failed.");
    }
  } catch (error) {
    screeningShowFeedback(error.message);
  } finally {
    button.disabled = false;
  }
}

function appendScreeningSection(title, matches) {
  if (!matches.length) return;
  const section = document.createElement("section");
  section.className = "profile-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading);
  matches.forEach((match) => {
    const card = document.createElement("article");
    card.className = "policy-finding";
    const status = document.createElement("span");
    status.className = "status-badge";
    if (match.status !== "MET") status.classList.add("warning");
    status.textContent = match.status.replaceAll("_", " ");
    const requirement = document.createElement("h4");
    requirement.textContent = match.requirement_text;
    const reason = document.createElement("p");
    reason.textContent = match.justification;
    card.append(status, requirement, reason);
    match.evidence.forEach((evidence) => {
      const quote = document.createElement("blockquote");
      quote.textContent = `Page ${evidence.page_number}: ${evidence.excerpt}`;
      card.append(quote);
    });
    section.append(card);
  });
  screeningContent.append(section);
}

async function showCandidateScreening(applicationId) {
  screeningFeedback.hidden = true;
  try {
    const detail = await screeningFetchJson(
      `/api/v1/jobs/${screeningJobId}/applications/${applicationId}/screening`,
    );
    screeningContent.replaceChildren();
    screeningTitle.textContent = detail.candidate_reference;
    rankingExplanation.textContent = detail.ranking_explanation || "Not ranked — screening incomplete.";
    appendScreeningSection(
      "Required requirements",
      detail.matches.filter((item) => item.requirement_type === "REQUIRED"),
    );
    appendScreeningSection(
      "Preferred requirements",
      detail.matches.filter((item) => item.requirement_type === "PREFERRED"),
    );
    screeningPanel.hidden = false;
    screeningPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    screeningShowFeedback(error.message);
  }
}

document.querySelector("#applications-table-body")?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-screen-application-id]");
  if (button) startCandidateScreening(button.dataset.screenApplicationId, button);
});

screeningTableBody?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-screening-application-id]");
  if (button) showCandidateScreening(button.dataset.screeningApplicationId);
});

screenReadyButton?.addEventListener("click", async () => {
  screenReadyButton.disabled = true;
  screeningFeedback.hidden = true;
  try {
    const batch = await screeningFetchJson(
      `/api/v1/jobs/${screeningJobId}/applications/screen-ready`,
      { method: "POST" },
    );
    if (!batch.queued) {
      screeningShowFeedback(
        batch.current ? "All eligible candidate screenings are already current." : "No READY candidates are waiting for screening.",
      );
      await refreshScreeningRanking();
      return;
    }
    await Promise.all(batch.tasks.map((task) => screeningPollTask(task.task_id)));
    await refreshScreeningRanking();
  } catch (error) {
    screeningShowFeedback(error.message);
  } finally {
    screenReadyButton.disabled = false;
  }
});

document.querySelector("#close-candidate-screening")?.addEventListener("click", () => {
  screeningPanel.hidden = true;
});

document.addEventListener("applications:rendered", refreshScreeningRanking);
refreshScreeningRanking().catch((error) => screeningShowFeedback(error.message));
