const interviewForm = document.querySelector("#interview-scheduling-form");
const interviewPanel = document.querySelector("#interview-scheduling-panel");
const interviewCandidate = document.querySelector("#interview-candidate-reference");
const interviewEmail = document.querySelector("#interview-invitation-email");
const interviewEmailSource = document.querySelector("#interview-email-source");
const interviewFormFeedback = document.querySelector("#interview-scheduling-feedback");
const interviewFeedback = document.querySelector("#interview-feedback");
const interviewsBody = document.querySelector("#scheduled-interviews-body");
const interviewJobId = interviewForm?.dataset.jobId;
let activeInterviewApplicationId = null;

async function interviewFetchJson(url, options = {}) {
  const response = await fetch(url, options);
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error("The interview service returned an invalid response.");
  }
  if (!response.ok) throw new Error(body.message || "The interview request could not be completed.");
  return body;
}

function showInterviewFeedback(target, message, success = false) {
  target.textContent = message;
  target.classList.toggle("success", success);
  target.hidden = false;
}

function interviewCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.append(cell);
  return cell;
}

function localInterviewTime(interview) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: interview.timezone,
  }).format(new Date(interview.scheduled_start_at));
}

function renderInterviews(interviews) {
  interviewsBody.replaceChildren();
  if (!interviews.length) {
    const row = document.createElement("tr");
    const cell = interviewCell(row, "No interviews have been scheduled.");
    cell.colSpan = 7;
    interviewsBody.append(row);
    return;
  }
  interviews.forEach((interview) => {
    const row = document.createElement("tr");
    interviewCell(row, interview.candidate_reference);
    interviewCell(row, interview.job_title);
    interviewCell(row, `${localInterviewTime(interview)} (${interview.timezone})`);
    interviewCell(row, `${interview.duration_minutes} minutes`);
    const status = interviewCell(row, "");
    const statusBadge = document.createElement("span");
    statusBadge.className = "status-badge";
    statusBadge.textContent = interview.status;
    status.append(statusBadge);
    const invitation = interviewCell(row, "");
    const invitationBadge = document.createElement("span");
    invitationBadge.className = "status-badge";
    if (["FAILED", "NOT_CONFIGURED"].includes(interview.invitation_status)) {
      invitationBadge.classList.add("warning");
    }
    invitationBadge.textContent = interview.invitation_status.replaceAll("_", " ");
    invitation.append(invitationBadge);
    if (interview.invitation_error_message) {
      const error = document.createElement("p");
      error.className = "hint";
      error.textContent = interview.invitation_error_message;
      invitation.append(error);
    }
    const actions = interviewCell(row, "");
    const openRoom = document.createElement("a");
    openRoom.className = "button secondary compact";
    openRoom.href = `/interviews/${interview.id}/room`;
    openRoom.textContent = "Open room";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "button secondary compact";
    copy.dataset.copyInterviewLink = interview.candidate_join_url;
    copy.textContent = "Copy link";
    const resend = document.createElement("button");
    resend.type = "button";
    resend.className = "button compact";
    resend.dataset.resendInterviewId = interview.id;
    resend.textContent = interview.invitation_status === "SENT" ? "Resend invitation" : "Retry invitation";
    actions.append(openRoom, copy, resend);
    interviewsBody.append(row);
  });
}

async function refreshInterviews() {
  if (!interviewJobId) return;
  renderInterviews(await interviewFetchJson(`/api/v1/jobs/${interviewJobId}/interviews`));
}

const interviewEmailSourceLabels = {
  APPLICATION_EMAIL: "Application email",
  CV_EXTRACTED_EMAIL: "CV extracted email",
  NOT_AVAILABLE: "Not available",
};

document.querySelector("#candidate-scores-body")?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-schedule-application-id]");
  if (!button) return;
  button.disabled = true;
  interviewFormFeedback.hidden = true;
  try {
    const applicationId = button.dataset.scheduleApplicationId;
    const defaults = await interviewFetchJson(
      `/api/v1/applications/${applicationId}/interview-scheduling-defaults`,
    );
    activeInterviewApplicationId = applicationId;
    interviewCandidate.textContent = defaults.candidate_reference;
    interviewEmail.value = defaults.invitation_email || "";
    interviewEmailSource.textContent = interviewEmailSourceLabels[defaults.email_source] || "Not available";
    const timezoneInput = interviewForm.elements.timezone;
    if (!timezoneInput.value) {
      timezoneInput.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    }
    interviewPanel.hidden = false;
    interviewPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    interviewEmail.focus();
  } catch (error) {
    showInterviewFeedback(interviewFeedback, error.message);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#close-interview-scheduling")?.addEventListener("click", () => {
  interviewPanel.hidden = true;
  activeInterviewApplicationId = null;
});

interviewForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeInterviewApplicationId) return;
  const button = document.querySelector("#submit-interview-schedule");
  button.disabled = true;
  interviewFormFeedback.hidden = true;
  const values = Object.fromEntries(new FormData(interviewForm).entries());
  values.duration_minutes = Number(values.duration_minutes);
  try {
    const interview = await interviewFetchJson(
      `/api/v1/applications/${activeInterviewApplicationId}/interviews`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      },
    );
    const invitationMessage = interview.invitation_status === "SENT"
      ? "Invitation sent."
      : `Interview saved; invitation is ${interview.invitation_status.replaceAll("_", " ").toLowerCase()}.`;
    showInterviewFeedback(interviewFeedback, `Interview scheduled. ${invitationMessage}`, true);
    interviewPanel.hidden = true;
    activeInterviewApplicationId = null;
    interviewForm.reset();
    await refreshInterviews();
  } catch (error) {
    showInterviewFeedback(interviewFormFeedback, error.message);
  } finally {
    button.disabled = false;
  }
});

interviewsBody?.addEventListener("click", async (event) => {
  const copy = event.target.closest("[data-copy-interview-link]");
  if (copy) {
    try {
      await navigator.clipboard.writeText(copy.dataset.copyInterviewLink);
      showInterviewFeedback(interviewFeedback, "Secure candidate link copied.", true);
    } catch {
      showInterviewFeedback(interviewFeedback, "The browser could not copy the secure link.");
    }
    return;
  }
  const resend = event.target.closest("[data-resend-interview-id]");
  if (!resend) return;
  resend.disabled = true;
  try {
    const interview = await interviewFetchJson(
      `/api/v1/interviews/${resend.dataset.resendInterviewId}/resend-invitation`,
      { method: "POST" },
    );
    const message = interview.invitation_status === "SENT"
      ? "Invitation sent."
      : interview.invitation_error_message || "Invitation could not be sent.";
    showInterviewFeedback(interviewFeedback, message, interview.invitation_status === "SENT");
    await refreshInterviews();
  } catch (error) {
    showInterviewFeedback(interviewFeedback, error.message);
  } finally {
    resend.disabled = false;
  }
});

refreshInterviews().catch((error) => showInterviewFeedback(interviewFeedback, error.message));
