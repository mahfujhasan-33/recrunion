function splitLines(value) {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function buildPayload(form) {
  const data = new FormData(form);
  const minimumExperience = data.get("minimum_experience").trim();

  return {
    title: data.get("title"),
    location: data.get("location"),
    employment_type: data.get("employment_type"),
    application_email: data.get("application_email"),
    required_skills: splitLines(data.get("required_skills")),
    preferred_skills: splitLines(data.get("preferred_skills")),
    minimum_experience: minimumExperience ? Number(minimumExperience) : null,
    qualifications: splitLines(data.get("qualifications")),
    selection_criteria: splitLines(data.get("selection_criteria")),
  };
}

async function submitJob(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const errorBox = document.querySelector("#form-error");
  const jobId = form.dataset.jobId;
  const endpoint = jobId ? `/api/v1/jobs/${jobId}` : "/api/v1/jobs";

  errorBox.hidden = true;

  try {
    const response = await fetch(endpoint, {
      method: jobId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload(form)),
    });
    const body = await response.json();

    if (!response.ok) {
      const validationMessage = body.detail?.[0]?.msg;
      throw new Error(validationMessage || body.message || "Unable to save the job.");
    }

    window.location.assign(`/jobs/${body.id}`);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

async function requestJobAction(endpoint, options = {}) {
  const response = await fetch(endpoint, options);
  const body = await response.json();

  if (!response.ok) {
    const validationMessage = body.detail?.[0]?.msg;
    throw new Error(validationMessage || body.message || "The request could not be completed.");
  }
  return body;
}

function setDescriptionFeedback(message, isError = false) {
  const feedback = document.querySelector("#description-feedback");
  if (!feedback) return;
  feedback.textContent = message;
  feedback.classList.toggle("success-message", !isError);
  feedback.hidden = false;
}

async function runDescriptionAction(button, action, loadingMessage) {
  button.disabled = true;
  setDescriptionFeedback(loadingMessage);
  try {
    await action();
    window.location.reload();
  } catch (error) {
    setDescriptionFeedback(error.message, true);
    button.disabled = false;
  }
}

const jobForm = document.querySelector("#job-form");
if (jobForm) {
  jobForm.addEventListener("submit", submitJob);
}

const descriptionSection = document.querySelector(".job-description");
const jobId = descriptionSection?.dataset.jobId;
const generateButton = document.querySelector("#generate-description");
if (generateButton && jobId) {
  generateButton.addEventListener("click", () =>
    runDescriptionAction(
      generateButton,
      () => requestJobAction(`/api/v1/jobs/${jobId}/generate-description`, { method: "POST" }),
      "Generating the job description…",
    ),
  );
}

const descriptionForm = document.querySelector("#description-form");
if (descriptionForm && jobId) {
  descriptionForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const submitButton = descriptionForm.querySelector('button[type="submit"]');
    const content = new FormData(descriptionForm).get("content");
    runDescriptionAction(
      submitButton,
      () =>
        requestJobAction(`/api/v1/jobs/${jobId}/description`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        }),
      "Saving the reviewed description…",
    );
  });
}

const approveButton = document.querySelector("#approve-description");
if (approveButton && jobId) {
  approveButton.addEventListener("click", () =>
    runDescriptionAction(
      approveButton,
      () => requestJobAction(`/api/v1/jobs/${jobId}/approve`, { method: "POST" }),
      "Approving the reviewed description…",
    ),
  );
}
