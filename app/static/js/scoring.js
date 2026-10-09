const scoringForm = document.querySelector("#scoring-config-form");
const scoringJobId = scoringForm?.dataset.jobId;
const scoringFeedback = document.querySelector("#scoring-feedback");
const scoringVersion = document.querySelector("#scoring-config-version");
const scoreAllButton = document.querySelector("#score-screened-applications");
const scoresBody = document.querySelector("#candidate-scores-body");
const scorePanel = document.querySelector("#candidate-score-panel");
const scoreTitle = document.querySelector("#candidate-score-title");
const scoreContent = document.querySelector("#candidate-score-content");

const weightFields = {
  TECHNICAL_SKILLS: "technical_skills_weight",
  RELEVANT_EXPERIENCE: "relevant_experience_weight",
  ACADEMIC_PROFESSIONAL_QUALIFICATIONS: "qualifications_weight",
  ROLE_SPECIFIC_CRITERIA: "role_specific_criteria_weight",
};

const dimensionLabels = {
  TECHNICAL_SKILLS: "Technical Skills",
  RELEVANT_EXPERIENCE: "Relevant Experience",
  ACADEMIC_PROFESSIONAL_QUALIFICATIONS: "Academic / Professional Qualifications",
  ROLE_SPECIFIC_CRITERIA: "Role-Specific Criteria",
};

async function scoringFetchJson(url, options = {}) {
  const response = await fetch(url, options);
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error("The scoring service returned an invalid response.");
  }
  if (!response.ok) throw new Error(body.message || "The scoring request could not be completed.");
  return body;
}

function showScoringFeedback(message, isSuccess = false) {
  scoringFeedback.textContent = message;
  scoringFeedback.classList.toggle("success", isSuccess);
  scoringFeedback.hidden = false;
}

function scoreCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.append(cell);
  return cell;
}

function formatPercentage(value) {
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function renderConfig(config) {
  scoringVersion.textContent = `Version ${config.version}`;
  config.dimensions.forEach((dimension) => {
    const fieldName = weightFields[dimension.dimension];
    scoringForm.elements[fieldName].value = dimension.configured_weight;
    const label = scoringForm.querySelector(`[data-effective-weight="${dimension.dimension}"]`);
    label.textContent = dimension.applicable
      ? `Effective: ${formatPercentage(dimension.effective_weight)}`
      : "Effective: N/A for this job";
  });
}

async function refreshScoringConfig() {
  if (!scoringJobId) return;
  renderConfig(await scoringFetchJson(`/api/v1/jobs/${scoringJobId}/scoring-config`));
}

function scoreStateCell(row, entry) {
  const cell = scoreCell(row, "");
  const badge = document.createElement("span");
  badge.className = "status-badge";
  badge.textContent = entry.is_current ? "CURRENT" : entry.suitability_score === null ? "NOT SCORED" : "STALE";
  if (!entry.is_current) badge.classList.add("warning");
  cell.append(badge);
  if (entry.stale_reason) {
    const reason = document.createElement("p");
    reason.className = "hint";
    reason.textContent = entry.stale_reason;
    cell.append(reason);
  }
}

function renderScores(body) {
  scoresBody.replaceChildren();
  if (!body.candidates.length) {
    const row = document.createElement("tr");
    const cell = scoreCell(row, "No applications are available.");
    cell.colSpan = 7;
    scoresBody.append(row);
    return;
  }
  body.candidates.forEach((entry) => {
    const row = document.createElement("tr");
    scoreCell(row, entry.candidate_reference);
    scoreCell(row, entry.match_rank ? String(entry.match_rank) : "Not ranked");
    scoreCell(row, entry.suitability_score === null ? "Not scored" : `${Number(entry.suitability_score).toFixed(2)} / 100`);
    scoreCell(row, entry.required_gaps === null ? "—" : String(entry.required_gaps));
    scoreCell(row, entry.evidence_coverage || "—");
    scoreStateCell(row, entry);
    const action = scoreCell(row, "");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "button secondary compact";
    button.dataset.scoreApplicationId = entry.application_id;
    button.dataset.hasScore = entry.suitability_score !== null ? "true" : "false";
    button.dataset.scoreCurrent = entry.is_current ? "true" : "false";
    button.textContent = entry.suitability_score === null ? "Calculate score" : entry.is_current ? "View score" : "Recalculate";
    action.append(button);
    scoresBody.append(row);
  });
}

async function refreshScores() {
  if (!scoringJobId) return;
  renderScores(await scoringFetchJson(`/api/v1/jobs/${scoringJobId}/scores`));
}

function appendRequirementEvidence(container, requirement) {
  const item = document.createElement("article");
  item.className = "policy-finding";
  const heading = document.createElement("h4");
  heading.textContent = `${requirement.requirement_type.replaceAll("_", " ")} · ${requirement.requirement_text}`;
  const outcome = document.createElement("p");
  outcome.textContent = `${requirement.match_status.replaceAll("_", " ")} · ${Number(requirement.points).toFixed(2)} points · importance ${requirement.importance}`;
  item.append(heading, outcome);
  requirement.evidence.forEach((evidence) => {
    const quote = document.createElement("blockquote");
    quote.textContent = `Page ${evidence.page_number}: ${evidence.excerpt}`;
    item.append(quote);
  });
  if (!requirement.evidence.length) {
    const gap = document.createElement("p");
    gap.className = "hint";
    gap.textContent = "No persisted CV evidence is linked to this outcome.";
    item.append(gap);
  }
  container.append(item);
}

function renderScoreDetail(detail) {
  scoreContent.replaceChildren();
  scoreTitle.textContent = `${detail.candidate_reference} · ${Number(detail.suitability_score).toFixed(2)} / 100`;
  const summary = document.createElement("p");
  summary.textContent = `Match Rank: ${detail.match_rank || "Not ranked"} · Required Gaps: ${detail.required_gaps} · Evidence Coverage: ${detail.evidence_coverage} · Config version ${detail.scoring_config_version}`;
  const overall = document.createElement("p");
  overall.textContent = detail.justification;
  scoreContent.append(summary, overall);
  detail.dimensions.forEach((dimension) => {
    const section = document.createElement("section");
    section.className = "profile-section";
    const heading = document.createElement("h3");
    heading.textContent = dimensionLabels[dimension.dimension];
    const metrics = document.createElement("p");
    metrics.textContent = dimension.applicable
      ? `Score ${Number(dimension.raw_score).toFixed(2)} / 100 · Configured weight ${dimension.configured_weight} · Effective ${formatPercentage(dimension.effective_weight)} · Contribution ${Number(dimension.weighted_contribution).toFixed(2)}`
      : `N/A · Configured weight ${dimension.configured_weight} · no score penalty`;
    const explanation = document.createElement("p");
    explanation.textContent = dimension.justification;
    section.append(heading, metrics, explanation);
    dimension.requirements.forEach((requirement) => appendRequirementEvidence(section, requirement));
    scoreContent.append(section);
  });
  if (detail.excluded_requirements.length) {
    const section = document.createElement("section");
    section.className = "profile-section";
    const heading = document.createElement("h3");
    heading.textContent = "Excluded from Suitability Score";
    section.append(heading);
    detail.excluded_requirements.forEach((requirement) => {
      const note = document.createElement("p");
      note.textContent = `${requirement.requirement_text}: ${requirement.reason}`;
      section.append(note);
    });
    scoreContent.append(section);
  }
  scorePanel.hidden = false;
  scorePanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function showScore(applicationId) {
  renderScoreDetail(await scoringFetchJson(`/api/v1/jobs/${scoringJobId}/applications/${applicationId}/score`));
}

async function calculateScore(applicationId, button) {
  button.disabled = true;
  try {
    const detail = await scoringFetchJson(`/api/v1/jobs/${scoringJobId}/applications/${applicationId}/score`, { method: "POST" });
    renderScoreDetail(detail);
    await refreshScores();
  } catch (error) {
    showScoringFeedback(error.message);
  } finally {
    button.disabled = false;
  }
}

scoringForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  scoringFeedback.hidden = true;
  const payload = Object.fromEntries(new FormData(scoringForm).entries());
  try {
    const config = await scoringFetchJson(`/api/v1/jobs/${scoringJobId}/scoring-config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderConfig(config);
    showScoringFeedback("Scoring weights saved. Existing scores using different weights are now shown as stale.", true);
    await refreshScores();
  } catch (error) {
    showScoringFeedback(error.message);
  }
});

scoreAllButton?.addEventListener("click", async () => {
  scoreAllButton.disabled = true;
  scoringFeedback.hidden = true;
  try {
    const result = await scoringFetchJson(`/api/v1/jobs/${scoringJobId}/applications/score-screened`, { method: "POST" });
    showScoringFeedback(`Scoring complete: ${result.scored} new, ${result.stale_replaced} refreshed, ${result.already_current} already current, ${result.skipped} skipped, ${result.failed} failed.`, true);
    await refreshScores();
  } catch (error) {
    showScoringFeedback(error.message);
  } finally {
    scoreAllButton.disabled = false;
  }
});

scoresBody?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-score-application-id]");
  if (!button) return;
  if (button.dataset.hasScore === "true" && button.dataset.scoreCurrent === "true") {
    showScore(button.dataset.scoreApplicationId).catch((error) => showScoringFeedback(error.message));
  } else {
    calculateScore(button.dataset.scoreApplicationId, button);
  }
});

document.querySelector("#close-candidate-score")?.addEventListener("click", () => {
  scorePanel.hidden = true;
});

document.addEventListener("applications:rendered", refreshScores);
Promise.all([refreshScoringConfig(), refreshScores()]).catch((error) => showScoringFeedback(error.message));
