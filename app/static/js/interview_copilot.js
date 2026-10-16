/* Recruiter-only F11 presentation. Media and transcription remain independent. */
(() => {
  "use strict";
  function elapsedLabel(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    return `${Math.floor(total / 60).toString().padStart(2, "0")}:${(total % 60).toString().padStart(2, "0")}`;
  }
  if (typeof module !== "undefined") module.exports = { elapsedLabel };
  if (typeof document === "undefined") return;
  const panel = document.getElementById("copilot-tab-panel");
  if (!panel) return;
  const base = `/api/v1/interviews/${encodeURIComponent(panel.dataset.interviewId)}`;
  const status = document.getElementById("copilot-status");
  const progress = document.getElementById("copilot-progress");
  const generate = document.getElementById("generate-interview-probes");
  const regenerate = document.getElementById("regenerate-interview-probes");
  const send = document.getElementById("send-copilot-question");
  const tabs = [document.getElementById("transcript-tab"), document.getElementById("copilot-tab")];
  let timer = null;
  let disposed = false;
  let loading = false;

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  async function request(path, options = {}) {
    const response = await fetch(path, options);
    const isJSON = response.headers.get("content-type")?.includes("application/json");
    const payload = isJSON ? await response.json() : null;
    if (!response.ok) throw new Error(payload?.message || "Interview assistance is unavailable. Your call and transcription can continue.");
    if (!payload) throw new Error("Interview assistance returned an unexpected response. Please retry.");
    return payload;
  }

  function selectTab(index, focus = false) {
    tabs.forEach((tab, item) => {
      tab.setAttribute("aria-selected", String(item === index));
      tab.tabIndex = item === index ? 0 : -1;
      document.getElementById(tab.getAttribute("aria-controls")).hidden = item !== index;
    });
    if (focus) tabs[index].focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(index));
    tab.addEventListener("keydown", (event) => {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        selectTab(event.key === "Home" ? 0 : event.key === "End" ? 1 : 1 - index, true);
      }
    });
  });

  function listSection(parent, title, values) {
    parent.append(element("h3", title));
    if (!values.length) return parent.append(element("p", "Not available from saved evidence.", "hint"));
    const list = element("ul");
    values.forEach(value => list.append(element("li", value)));
    parent.append(list);
  }

  function renderContext(data) {
    const context = document.getElementById("interview-candidate-context");
    context.replaceChildren(element("p", "Your interview brief", "eyebrow"), element("h2", "Candidate Info"));
    context.append(element("h3", data.candidate_reference), element("p", data.job_title));
    listSection(context, "Key skills", data.skills);
    listSection(context, "Experience", data.experience);
    listSection(context, "Qualifications", data.qualifications);
    listSection(context, "Evidence to explore", data.gaps.map(gap => `${gap.requirement_text} — ${gap.match_status.replaceAll("_", " ")}`));
  }

  function questionCard(probe, followUp = false) {
    const card = element("article", undefined, "interview-probe-card");
    const heading = element("div", undefined, "card-heading");
    heading.append(element("span", followUp ? "Requested follow-up" : `Priority ${probe.priority}`, "eyebrow"));
    if (!followUp) heading.append(element("span", probe.status.replaceAll("_", " "), "status-badge"));
    card.append(heading, element("h3", probe.question), element("p", `Why ask: ${probe.reason}`));
    if (probe.requirement_text) card.append(element("p", `Requirement: ${probe.requirement_text}`, "hint"));
    listSection(card, "Strong answer may cover", probe.expected_evidence);
    const details = element("details");
    details.append(element("summary", "Evidence references"));
    if (followUp) {
      details.append(element("p", `Requirement: ${probe.requirement_id}`, "hint"));
      details.append(element("p", probe.transcript_segment_ids.length ? `Saved transcript segments: ${probe.transcript_segment_ids.join(", ")}` : "Job/CV evidence; no saved transcript segment cited.", "hint"));
    }
    if (probe.evidence_refs) {
      const refs = probe.evidence_refs;
      details.append(element("p", `F1 match: ${refs.match_id} · ${refs.match_status}`, "hint"));
      if (refs.f1_justification) details.append(element("p", refs.f1_justification));
      details.append(element("p", `F2 source: ${refs.score_id} · ${refs.score_dimensions.join(", ")}`, "hint"));
      details.append(element("p", refs.cv_evidence_ids.length ? `CV chunks: ${refs.cv_evidence_ids.join(", ")}` : "Explicit evidence gap: no linked CV chunk.", "hint"));
      (refs.cv_evidence || []).forEach(item => details.append(element("blockquote", `CV page ${item.page}: ${item.excerpt}`)));
      (refs.transcript || []).forEach(item => details.append(element("blockquote", `${item.role} · ${item.id}: ${item.text}`)));
      if (!followUp && !probe.is_current) card.append(element("p", "Historical suggestion — source evidence has changed.", "hint"));
    }
    card.append(details);
    if (!followUp && probe.status === "READY") {
      const actions = element("div", undefined, "copilot-probe-actions");
      [["ASKED", "Mark Asked"], ["DISMISSED", "Dismiss"]].forEach(([nextStatus, label]) => {
        const button = element("button", label, "button secondary");
        button.type = "button";
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            await request(`${base}/probes/${encodeURIComponent(probe.id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: nextStatus }) });
            await refresh();
          } catch (error) { status.textContent = error.message; button.disabled = false; }
        });
        actions.append(button);
      });
      card.append(actions);
    }
    return card;
  }

  function render(data) {
    renderContext(data);
    const probes = document.getElementById("interview-probes");
    probes.replaceChildren();
    if (!data.probes.length) probes.append(element("p", "No saved suggestions yet. Generate questions when current F1/F2 evidence is ready.", "hint"));
    data.probes.forEach(probe => probes.append(questionCard(probe)));
    const replies = document.getElementById("copilot-replies");
    replies.replaceChildren();
    data.runs.filter(run => run.response).forEach(run => replies.append(questionCard(run.response, true)));
    const active = data.runs.filter(run => ["QUEUED", "RUNNING"].includes(run.task.status));
    generate.disabled = regenerate.disabled = active.some(run => run.kind === "PROBES");
    send.disabled = active.some(run => run.kind === "FOLLOW_UP");
    [generate, regenerate, send].forEach(button => button.setAttribute("aria-busy", String(button.disabled)));
    progress.hidden = !active.length;
    if (active.length) {
      progress.value = active[0].task.progress;
      status.textContent = `${active[0].task.progress}% · ${active[0].task.progress_message}`;
      scheduleRefresh();
    } else {
      const failed = data.runs[0]?.task;
      status.textContent = failed?.status === "FAILED" ? (failed.error_message_safe || "Assistance failed. Existing questions and your call are unchanged. Please retry.") : `${data.probes.filter(probe => probe.status === "READY").length} ready suggestions. ${data.gaps.length && data.gaps.length < 3 ? "Limited context: fewer than three supported requirements are available." : "Asked and dismissed history is preserved."}`;
    }
  }

  function scheduleRefresh() {
    clearTimeout(timer);
    if (!disposed) timer = setTimeout(refresh, 2000);
  }

  async function refresh() {
    if (loading || disposed) return;
    loading = true;
    try { render(await request(`${base}/probes`)); }
    catch (error) { status.textContent = `${error.message} Refresh the page to retry. Your call is unaffected.`; }
    finally { loading = false; }
  }

  async function enqueue(regenerateUnused, message = null) {
    const button = message ? send : (regenerateUnused ? regenerate : generate);
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    let queued = false;
    try {
      const payload = { request_id: crypto.randomUUID(), ...(message ? { message } : { regenerate: regenerateUnused }) };
      await request(`${base}/${message ? "copilot" : "probes/generate"}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      queued = true;
      await refresh();
    } catch (error) { status.textContent = error.message; }
    finally {
      if (!queued) { button.disabled = false; button.setAttribute("aria-busy", "false"); }
    }
  }
  generate.addEventListener("click", () => enqueue(false));
  regenerate.addEventListener("click", () => {
    if (window.confirm("Replace unused READY questions? Asked and dismissed history will remain.")) enqueue(true);
  });
  document.getElementById("copilot-question-form").addEventListener("submit", event => {
    event.preventDefault();
    const input = document.getElementById("copilot-question");
    if (input.checkValidity()) enqueue(false, input.value.trim());
  });

  const end = document.getElementById("leave-interview-room");
  const headerEnd = document.getElementById("end-interview-header");
  const elapsed = document.getElementById("interview-elapsed");
  let started = elapsed.dataset.startedAt ? Date.parse(elapsed.dataset.startedAt) : null;
  let ended = elapsed.dataset.endedAt ? Date.parse(elapsed.dataset.endedAt) : null;
  headerEnd.addEventListener("click", () => { if (!end.disabled) end.click(); });
  const clock = setInterval(() => {
    headerEnd.disabled = end.disabled;
    const state = document.getElementById("interview-lifecycle-status").textContent.trim();
    if (state === "ACTIVE" && started === null) started = Date.now();
    if (state === "ENDED" && ended === null) ended = Date.now();
    elapsed.textContent = elapsedLabel(started === null ? 0 : ((ended || Date.now()) - started) / 1000);
  }, 1000);
  window.addEventListener("pagehide", () => { disposed = true; clearTimeout(timer); clearInterval(clock); });
  refresh();
})();
