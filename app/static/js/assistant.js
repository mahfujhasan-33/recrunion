const app = document.querySelector("#assistant-app");
let conversation = null;
let activeTaskId = null;

async function requestJSON(url, options = {}) {
  const response = await fetch(url, options);
  const body = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    const validation = body?.detail?.[0]?.msg;
    throw new Error(validation || body?.message || "The request could not be completed.");
  }
  return body;
}

function showError(message) {
  const error = document.querySelector("#assistant-error");
  error.textContent = message;
  error.hidden = false;
}

function clearError() {
  document.querySelector("#assistant-error").hidden = true;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderMessages(messages) {
  const container = document.querySelector("#assistant-messages");
  container.replaceChildren();
  messages.forEach((message) => {
    const item = element("article", `assistant-message ${message.role.toLowerCase()}`);
    item.append(element("span", "message-role", message.role.replace("_", " ")));
    item.append(element("p", "", message.content));
    container.append(item);
  });
  container.scrollTop = container.scrollHeight;
}

function inputField(label, name, value = "", type = "text") {
  const wrapper = element("label", "");
  wrapper.append(document.createTextNode(label));
  const input = document.createElement("input");
  input.name = name;
  input.type = type;
  input.value = value ?? "";
  wrapper.append(input);
  return wrapper;
}

function textAreaField(label, name, values = []) {
  const wrapper = element("label", "");
  wrapper.append(document.createTextNode(label));
  const input = document.createElement("textarea");
  input.name = name;
  input.rows = 3;
  input.value = (values || []).join("\n");
  wrapper.append(input);
  return wrapper;
}

function renderRequirements(artifact) {
  const form = document.querySelector("#requirements-form");
  form.replaceChildren();
  if (!artifact) return;
  const draft = artifact.payload;
  const grid = element("div", "form-grid");
  grid.append(inputField("Role title", "title", draft.title));
  grid.append(inputField("Location", "location", draft.location));
  const employmentLabel = element("label", "", "Employment type");
  const employment = document.createElement("select");
  employment.name = "employment_type";
  ["", "FULL_TIME", "PART_TIME", "CONTRACT", "INTERNSHIP", "TEMPORARY"].forEach(
    (value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value ? value.replaceAll("_", " ") : "Select";
      option.selected = draft.employment_type === value;
      employment.append(option);
    },
  );
  employmentLabel.append(employment);
  grid.append(employmentLabel);
  grid.append(inputField("Application email", "application_email", draft.application_email, "email"));
  grid.append(inputField("Minimum experience (years)", "minimum_experience", draft.minimum_experience, "number"));
  form.append(grid);
  form.append(textAreaField("Required skills (one per line)", "required_skills", draft.required_skills));
  form.append(textAreaField("Preferred skills (one per line)", "preferred_skills", draft.preferred_skills));
  form.append(textAreaField("Qualifications (one per line)", "qualifications", draft.qualifications));
  form.append(textAreaField("Selection criteria (one per line)", "selection_criteria", draft.selection_criteria));
  const actions = element("div", "actions");
  const save = element("button", "button secondary", "Save requirements");
  save.type = "submit";
  actions.append(save);
  const generate = element("button", "button", "Generate JD");
  generate.type = "button";
  generate.addEventListener("click", () => sendMessage("Generate the job description now."));
  actions.append(generate);
  form.append(actions);
  form.dataset.artifactId = artifact.id;
}

function lines(value) {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function requirementsPayload(form) {
  const data = new FormData(form);
  const experience = data.get("minimum_experience").trim();
  return {
    title: data.get("title").trim() || null,
    location: data.get("location").trim() || null,
    employment_type: data.get("employment_type") || null,
    application_email: data.get("application_email").trim() || null,
    required_skills: lines(data.get("required_skills")),
    preferred_skills: lines(data.get("preferred_skills")),
    minimum_experience: experience ? Number(experience) : null,
    qualifications: lines(data.get("qualifications")),
    selection_criteria: lines(data.get("selection_criteria")),
  };
}

function renderRequiredActions(actions) {
  const container = document.querySelector("#required-actions");
  container.replaceChildren();
  actions.forEach((action) => {
    const card = element("article", `required-action ${action.severity.toLowerCase()}`);
    card.append(element("strong", "", action.label));
    card.append(element("p", "", action.message));
    if (action.code === "RECHECK_POLICY") {
      const button = element("button", "button secondary compact", "Recheck now");
      button.type = "button";
      button.addEventListener("click", () => sendMessage("Recheck policy alignment."));
      card.append(button);
    }
    if (action.code === "PUBLISH_JOB" || action.code === "RETRY_JOB_PUBLICATION") {
      const retry = action.code === "RETRY_JOB_PUBLICATION";
      const button = element(
        "button",
        "button compact",
        retry ? "Confirm retry" : "Publish to Bluesky",
      );
      button.type = "button";
      button.addEventListener("click", () => confirmPublication(retry));
      card.append(button);
    }
    container.append(card);
  });
}

function renderPublication(publication) {
  if (!publication) return null;
  const card = element("article", "publication-summary");
  const heading = element("div", "card-heading");
  heading.append(element("strong", "", `Bluesky attempt ${publication.attempt_number}`));
  heading.append(
    element(
      "span",
      `status-badge ${publication.status === "FAILED" ? "warning" : ""}`,
      publication.status,
    ),
  );
  card.append(heading);
  if (publication.published_at) {
    card.append(
      element("p", "hint", `Published ${new Date(publication.published_at).toLocaleString()}`),
    );
  }
  if (publication.error_message_safe) {
    card.append(element("p", "", publication.error_message_safe));
  }
  if (publication.external_url) {
    const link = element("a", "button secondary compact", "View Bluesky post");
    link.href = publication.external_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    card.append(link);
  }
  return card;
}

function renderDescription(workspace) {
  const container = document.querySelector("#jd-preview");
  container.replaceChildren();
  const job = workspace.job;
  if (!job?.jd_content) {
    container.className = "workspace-empty";
    container.textContent = "Generate a JD when the requirements are ready.";
    return;
  }
  container.className = "jd-workspace";
  const heading = element("div", "card-heading");
  heading.append(element("strong", "", `Current JD · version ${job.jd_version}`));
  heading.append(element("span", "status-badge", job.status));
  container.append(heading);
  const editor = document.createElement("textarea");
  editor.id = "workspace-jd-editor";
  editor.rows = 24;
  editor.value = job.jd_content;
  editor.disabled = job.status !== "GENERATED";
  container.append(editor);
  if (job.status === "GENERATED") {
    const actions = element("div", "actions workspace-actions");
    const save = element("button", "button secondary", "Save JD edits");
    save.type = "button";
    save.addEventListener("click", () => saveDescription(job.id, editor.value));
    actions.append(save);
    const enhance = element("button", "button secondary", "Enhance from evidence");
    enhance.type = "button";
    enhance.addEventListener("click", () => sendMessage("Enhance the JD using policy evidence."));
    actions.append(enhance);
    const approve = element("button", "button", "Confirm approval");
    approve.type = "button";
    approve.addEventListener("click", confirmApproval);
    actions.append(approve);
    container.append(actions);
  }
  const publication = renderPublication(workspace.publication);
  if (publication) container.append(publication);
  const proposal = workspace.proposal;
  if (proposal?.status === "PROPOSED") {
    const proposalCard = element("article", "proposal-card");
    proposalCard.append(element("p", "eyebrow", "Enhancement proposal"));
    proposalCard.append(
      element("p", "hint", `Compared with JD version ${proposal.base_job_version}. The current JD is unchanged.`),
    );
    const proposalText = document.createElement("textarea");
    proposalText.rows = 24;
    proposalText.value = proposal.payload.content;
    proposalText.readOnly = true;
    proposalCard.append(proposalText);
    const findings = proposal.payload.policy_findings || [];
    if (findings.length) {
      const findingList = element("div", "proposal-findings");
      findingList.append(element("strong", "", "Expected policy alignment after applying"));
      findings.forEach((finding) => {
        findingList.append(
          element(
            "p",
            "hint",
            `${finding.status.replaceAll("_", " ")} · ${finding.explanation}`,
          ),
        );
      });
      proposalCard.append(findingList);
    }
    const apply = element("button", "button", "Apply proposal");
    apply.type = "button";
    apply.addEventListener("click", () => applyProposal(proposal.id));
    proposalCard.append(apply);
    container.append(proposalCard);
  }
}

function renderEvidence(review) {
  const container = document.querySelector("#policy-evidence");
  container.replaceChildren();
  if (!review) {
    container.className = "workspace-empty";
    container.textContent = "Policy findings appear after JD generation.";
    return;
  }
  container.className = "evidence-list";
  const summary = element("div", "card-heading");
  summary.append(element("strong", "", `${review.retrieval_count} policy evidence item(s)`));
  summary.append(
    element("span", `status-badge ${review.is_current ? "" : "warning"}`, review.is_current ? "CURRENT" : "RECHECK REQUIRED"),
  );
  container.append(summary);
  if (review.message) container.append(element("p", "", review.message));
  review.findings.forEach((finding) => {
    const card = element("article", "policy-finding");
    const heading = element("div", "card-heading");
    heading.append(element("strong", "", finding.source_filename));
    heading.append(element("span", `status-badge policy-${finding.status.toLowerCase()}`, finding.status.replaceAll("_", " ")));
    card.append(heading);
    card.append(element("blockquote", "", finding.evidence_excerpt));
    card.append(element("p", "", finding.explanation));
    card.append(element("p", "hint", `Relates to ${finding.related_jd_section.replaceAll("_", " ").toLowerCase()}`));
    container.append(card);
  });
}

function render(state) {
  conversation = state;
  renderMessages(state.messages);
  renderRequirements(state.workspace.requirements);
  renderRequiredActions(state.workspace.required_actions);
  renderDescription(state.workspace);
  renderEvidence(state.workspace.policy_review);
  document.querySelector("#workspace-title").textContent = state.workspace.job?.title || state.title;
  document.querySelector("#workspace-status").textContent = state.workspace.job?.status || state.workspace.requirements?.status || "WORKING";
  const publication = state.workspace.publication;
  if (
    !activeTaskId &&
    publication &&
    ["QUEUED", "PUBLISHING"].includes(publication.status)
  ) {
    activeTaskId = publication.processing_task_id;
    pollTask();
  }
}

async function refreshConversation() {
  render(await requestJSON(`/api/v1/assistant/conversations/${conversation.id}`));
}

async function sendMessage(content) {
  if (!content.trim() || activeTaskId) return;
  clearError();
  try {
    const queued = await requestJSON(`/api/v1/assistant/conversations/${conversation.id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    document.querySelector("#assistant-input").value = "";
    activeTaskId = queued.task_id;
    await refreshConversation();
    pollTask();
  } catch (error) {
    showError(error.message);
  }
}

async function pollTask() {
  if (!activeTaskId) return;
  const progress = document.querySelector("#assistant-progress");
  progress.hidden = false;
  try {
    const task = await requestJSON(`/api/v1/tasks/${activeTaskId}`);
    document.querySelector("#progress-bar").value = task.progress;
    document.querySelector("#progress-percentage").textContent = `${task.progress}%`;
    document.querySelector("#progress-message").textContent = task.progress_message;
    if (task.status === "COMPLETED") {
      activeTaskId = null;
      await refreshConversation();
      window.setTimeout(() => {
        progress.hidden = true;
      }, 1500);
      return;
    }
    if (task.status === "FAILED") {
      activeTaskId = null;
      await refreshConversation();
      showError(task.error_message_safe || "The assistant task failed.");
      return;
    }
    window.setTimeout(pollTask, 1000);
  } catch (error) {
    activeTaskId = null;
    showError(error.message);
  }
}

async function saveDescription(jobId, content) {
  clearError();
  try {
    await requestJSON(`/api/v1/jobs/${jobId}/description`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    await refreshConversation();
  } catch (error) {
    showError(error.message);
  }
}

async function applyProposal(artifactId) {
  clearError();
  try {
    render(
      await requestJSON(`/api/v1/assistant/conversations/${conversation.id}/proposals/${artifactId}/apply`, {
        method: "POST",
      }),
    );
  } catch (error) {
    showError(error.message);
  }
}

async function confirmApproval() {
  if (!window.confirm("Approve this current JD? Recruiter approval is authoritative.")) return;
  clearError();
  try {
    render(
      await requestJSON(`/api/v1/assistant/conversations/${conversation.id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: true }),
      }),
    );
  } catch (error) {
    showError(error.message);
  }
}

async function confirmPublication(retry) {
  const action = retry ? "retry publishing" : "publish";
  if (
    !window.confirm(
      `Confirm that you want to ${action} this approved job on Bluesky? This is an external action.`,
    )
  ) {
    return;
  }
  clearError();
  try {
    const endpoint = retry ? "publication/retry" : "publish";
    const publication = await requestJSON(
      `/api/v1/assistant/conversations/${conversation.id}/${endpoint}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: true }),
      },
    );
    activeTaskId = publication.processing_task_id;
    await refreshConversation();
    pollTask();
  } catch (error) {
    showError(error.message);
  }
}

document.querySelector("#assistant-composer")?.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(document.querySelector("#assistant-input").value);
});

document.querySelector("#requirements-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  const form = event.currentTarget;
  try {
    render(
      await requestJSON(`/api/v1/assistant/conversations/${conversation.id}/artifacts/${form.dataset.artifactId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload: requirementsPayload(form) }),
      }),
    );
  } catch (error) {
    showError(error.message);
  }
});

document.querySelector("#new-conversation")?.addEventListener("click", async () => {
  if (!window.confirm("Start a new hiring conversation? Existing jobs remain available.")) return;
  clearError();
  try {
    render(await requestJSON("/api/v1/assistant/conversations", { method: "POST" }));
  } catch (error) {
    showError(error.message);
  }
});

document.querySelectorAll(".workspace-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".workspace-tab").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".workspace-view").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    document.querySelector(`[data-view="${tab.dataset.tab}"]`).classList.add("active");
  });
});

if (app) {
  requestJSON("/api/v1/assistant/conversations/latest")
    .then(render)
    .catch((error) => showError(error.message));
}
