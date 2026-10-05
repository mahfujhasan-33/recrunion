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

const jobForm = document.querySelector("#job-form");
if (jobForm) {
  jobForm.addEventListener("submit", submitJob);
}
