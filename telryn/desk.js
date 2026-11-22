"use strict";
const byId = (id) => document.getElementById(id);
const intake = byId("intake");
const grade = byId("grade");
const resolution = byId("resolution");
const message = byId("message");
const records = byId("records");
const policyForm = byId("policy-form");
const bankForm = byId("bank");
const subject = document.body.dataset.subject;
let recordVersion = 0;

let active = "";
let disputes = [];
let draftDispute = "";
let reviewRequest = 0;
let historyRequest = 0;
let intakeVersion = 0;

async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function showError(error) {
  message.textContent = error.message;
}

function setMode() {
  const budget = byId("mode").value === "budget";
  for (const [name, enabled] of [["budget", budget], ["counts", !budget]]) {
    byId(name).disabled = !enabled;
    byId(name).hidden = !enabled;
  }
  byId("preview").hidden = !budget;
  byId("forecast").hidden = true;
}

function upload(form) {
  const source = new FormData(form);
  const settings = {};
  const body = new FormData();
  if (form === intake) {
    Object.assign(settings, {
      week: source.get("week"), rubric: source.get("rubric"),
      rubric_question: source.get("rubric_question"), screen_version: source.get("screen_version"),
      group_by: source.getAll("group_by")
    });
    if (source.get("scores")?.name || source.get("scorecard") || source.get("score_version") || source.get("score_cutoff")) {
      settings.score_audit = {scorecard: source.get("scorecard"), version: source.get("score_version"),
        cutoff: source.get("score_cutoff"), scope: source.get("score_scope")};
      if (source.get("score_format")) {
        const native = {format:source.get("score_format"), attest_revisions:source.has("attest_revisions")};
        if (source.get("case_prefix")) native.case_prefix = source.get("case_prefix");
        if (native.format !== "langfuse-v2") native.pass_percent = Number(source.get("pass_percent"));
        if (source.get("complaint_evaluator")) native.complaint_evaluator = source.get("complaint_evaluator");
        settings.score_audit.native = native;
      }
    }
    if (byId("mode").value === "budget") {
      settings.planning = {
        minutes: Number(source.get("minutes")), minutes_per_grade: Number(source.get("minutes_per_grade"))
      };
      for (const key of ["expected_rate", "flagged_rate", "unflagged_rate", "detection_target"]) {
        if (source.get(key)) settings.planning[key] = Number(source.get(key)) / 100;
      }
      for (const key of ["detection_cases", "second_review_minutes", "adjudication_minutes"]) {
        if (source.get(key)) settings.planning[key] = Number(source.get(key));
      }
    } else {
      settings.flagged_sample = Number(source.get("flagged_sample"));
      settings.unflagged_sample = Number(source.get("unflagged_sample"));
    }
  } else if (form === records) {
    settings.confirmed_links = source.getAll("link").map(ids => JSON.parse(ids));
    settings.link_reviewer = source.get("link_reviewer");
  } else if (form === grade) {
    settings.reviewer = source.get("reviewer");
  }
  body.append("settings", JSON.stringify(settings));
  for (const [name, value] of source) {
    if (value instanceof File && value.name) {
      if (value.size > 24 * 1024 * 1024) throw new Error(value.name + " exceeds the 24 MiB per-file limit. Use the CLI for this batch.");
      body.append(name, value);
    }
  }
  return body;
}

async function postFiles(path, form) {
  const req = new Request(new URL(path, location.href), {method: "POST", body: upload(form)});
  // Measure exactly what the server receives, including multipart boundaries.
  const body = await req.blob();
  if (body.size > 64 * 1024 * 1024) throw new Error("The complete upload exceeds 64 MiB. Use the CLI for this batch.");
  // Blob.type normalizes case, but the boundary in the serialized body is case-sensitive.
  return request(path, {method: "POST", headers: req.headers, body});
}

function textNode(tag, value) {
  const el = document.createElement(tag);
  el.textContent = value;
  return el;
}

function forecast(plan) {
  const box = byId("forecast");
  const content = [textNode("h2", "Before you draw")];
  content.push(textNode("p", plan.assigned + " cases fit within " + plan.budget_minutes + " minutes; " +
      plan.unused_minutes + " minutes remain."));
  for (const line of plan.decision_lines) content.push(textNode("p", line));
  if (plan.coverage_complete && plan.scenario_interval) {
    const range = plan.scenario_interval;
    content.push(textNode("p", "At the rates you entered, a typical count gives a 95% interval of " +
        range[0] + "–" + range[1] + ". Expected interval width: " + Number(plan.expected_interval_width).toFixed(1) +
        " cases. With zero findings the upper bound would be " + plan.zero_findings_upper + "."));
  } else if (plan.coverage_complete) {
    content.push(textNode("p", "With zero findings the 95% upper bound would be " + plan.zero_findings_upper + ". Enter an expected rate to forecast the interval width."));
  } else {
    content.push(textNode("p", "The budget leaves a group unsampled. Increase review minutes or use fewer grouping columns for a full-population estimate."));
  }
  const list = document.createElement("ul");
  for (const group of plan.allocations) list.append(textNode("li", group.label + ": grade " + group.sample + " of " + group.population));
  box.replaceChildren(...content, list);
  box.hidden = false;
}

function answerText(grade) {
  if (!grade) return "pending";
  const finding = grade.serious == null ? "insufficient evidence" : grade.serious ? "yes" : "no";
  const dimensions = grade.category ? " · category " + grade.category + ", vulnerability " + grade.vulnerability + ", redress " + grade.redress + ". Basis: " + grade.classification_reason : "";
  return finding + " (" + grade.disposition + ")" + dimensions;
}
function displayDispute() {
  const selected = resolution.elements.dispute.value;
  if (selected !== draftDispute) {
    for (const name of ["reviewer", "choice", "reason", "minutes"]) resolution.elements[name].value = "";
    draftDispute = selected;
  }
  const item = disputes.find((entry) => entry.dispute_id === selected);
  byId("answers").textContent = item ? "Primary: " + answerText(item.primary) + ". Second: " + answerText(item.second) + "." : "";
}

function progressText(work) {
  const returned = work.assigned - work.primary_outstanding;
  const counts = [returned + " of " + work.assigned + " primary grades returned"];
  if (work.primary_outstanding) counts.push(work.primary_outstanding + " unanswered");
  if (work.insufficient_undisputed) counts.push(work.insufficient_undisputed + " insufficient");
  if (work.open_disputes) counts.push(work.open_disputes + " awaiting adjudication");
  if (work.handling_unknown) counts.push(work.handling_unknown + " with prior handling unknown");
  return counts.join(" · ");
}

function displayFollowUp(items) {
  const section = byId("follow-up");
  const body = section.querySelector("tbody");
  body.replaceChildren();
  for (const item of items) {
    const row = document.createElement("tr");
    const reason = item.reason === "insufficient_evidence" ? "Insufficient evidence" : "Prior handling unknown";
    const route = item.route === "primary_amendment" ? "First reviewer amends primary grade" : "New independent review";
    for (const value of [item.case_id, item.revision, item.evidence_ref, item.completed_at || "Not supplied",
        reason, item.primary_reviewer || "Not recorded", route]) {
      row.append(textNode("td", value));
    }
    body.append(row);
  }
  section.hidden = !items.length;
}

async function selectRun(id) {
  const requestNumber = ++reviewRequest;
  if (active !== id) {
    grade.reset();
    resolution.reset();
    draftDispute = "";
    records.reset();
    bankForm.reset();
    policyForm.reset();
    clearCandidates();
    byId("bank-digest").textContent = "";
    byId("policy-result").textContent = "";
  }
  active = id;
  byId("current").hidden = true;
  if (location.hash !== "#" + id) window.history.pushState(null, "", "#" + id);
  resolution.hidden = true;
  byId("agreement").textContent = "";
  const status = await request(id + "/review");
  if (active !== id || requestNumber !== reviewRequest) return;
  byId("current-title").textContent = status.week + " · " + status.rubric;
  byId("progress").textContent = progressText(status.progress);
  byId("manifest-download").href = id + "/manifest.json";
  byId("report").href = id + "/report.html";
  byId("packet").href = id + "/grades.csv";
  byId("outstanding").href = id + "/outstanding.csv";
  byId("outstanding").hidden = !status.progress.primary_outstanding;
  const steps = [];
  if (status.progress.primary_outstanding) steps.push("Send unanswered assignments to the next primary reviewer.");
  if (status.progress.insufficient_undisputed) steps.push("Check the evidence for insufficient grades and return corrections against the original rows.");
  if (status.progress.open_disputes) steps.push("Ask an independent adjudicator to resolve the disagreements below.");
  if (status.progress.handling_unknown) steps.push("Check whether another control had already found the cases with unknown prior handling.");
  byId("next-step").textContent = steps.join(" ");
  byId("next-step").hidden = !steps.length;
  displayFollowUp(status.follow_up);
  byId("agreement").textContent = status.compared ? "Independent reviewers agreed on severity for " + status.agreed + " of " + status.compared + " definite pairs." : "";
  disputes = status.open_disputes;
  resolution.elements.dispute.replaceChildren(...disputes.map((item) => new Option(item.case_id, item.dispute_id)));
  if (disputes.some((item) => item.dispute_id === draftDispute)) resolution.elements.dispute.value = draftDispute;
  resolution.hidden = !disputes.length;
  displayDispute();
  setIdentity();
  byId("current").hidden = false;
}

async function historyList() {
  const requestNumber = ++historyRequest;
  const list = byId("history").querySelector("ul");
  const runs = await request("audits");
  if (requestNumber !== historyRequest) return;
  list.replaceChildren();
  for (const run of runs) {
    const item = document.createElement("li");
    if (run.verified) {
      const link = textNode("a", run.week + " · " + run.rubric + " · " + run.id.slice(0, 6));
      link.href = "#" + run.id;
      link.addEventListener("click", (event) => {
        event.preventDefault();
        selectRun(run.id).then(() => byId("current").scrollIntoView()).catch(showError);
      });
      item.append(link, textNode("div", progressText(run.progress)));
    } else {
      item.textContent = "Unavailable audit · " + run.id;
    }
    list.append(item);
  }
  byId("recovery").hidden = runs.every((run) => run.verified);
  if (!byId("recovery").hidden) byId("history").open = true;
  byId("history").hidden = !runs.length;
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = event.submitter;
  const originalRun = active;
  button.disabled = true;
  try {
    if (form === intake && button.id === "preview") {
      const version = intakeVersion;
      const plan = await postFiles("plan", intake);
      if (version !== intakeVersion) return;
      forecast(plan);
      message.textContent = "Preview ready. You can adjust the budget before creating the audit.";
      return;
    }
    let result;
    if (form === resolution) {
      const item = disputes.find((entry) => entry.dispute_id === form.elements.dispute.value);
      const body = {case_id: item.case_id, dispute_id: item.dispute_id, reviewer: form.elements.reviewer.value,
        choice: form.elements.choice.value, reason: form.elements.reason.value, minutes: Number(form.elements.minutes.value)};
      result = await request(active + "/adjudicate", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    } else {
      result = await postFiles(form === intake ? "audits" : active + "/" + grade.elements.round.value, form);
    }
    if (active === originalRun) await selectRun(result.id);
    await historyList();
    if (active !== result.id) {
      message.textContent = "Changes saved. Open the updated audit from saved audits.";
      return;
    }
    message.textContent = form === intake ? "Audit ready. Give the grading CSV to your reviewer." :
        form === grade ? "Grades recorded. Open the updated report." : "Adjudication recorded.";
    byId("current").scrollIntoView();
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
}

async function loadRubrics() {
  const values = await request("rubrics");
  const field = byId("starter");
  for (const [key, preset] of Object.entries(values)) field.add(new Option(preset[0], key));
  field.addEventListener("change", () => {
    if (values[field.value]) {
      intake.elements.rubric_question.value = values[field.value][1];
      intake.elements.rubric.value = "fintech-" + field.value + "-v1";
    }
  });
}

intake.elements.policy_pack.addEventListener("change", () => {
  const packed = intake.elements.policy_pack.files.length > 0;
  intake.elements.rubric.disabled = packed;
  intake.elements.rubric_question.disabled = packed;
  byId("starter").disabled = packed;
});
for (const form of [intake, grade, resolution]) form.addEventListener("submit", submit);
intake.addEventListener("input", () => { intakeVersion++; byId("forecast").hidden = true; });
resolution.elements.dispute.addEventListener("change", displayDispute);
byId("mode").addEventListener("change", setMode);
setMode();
loadRubrics().catch(showError);
historyList().catch(showError);
function resumeFromLocation() {
  if (/^#[a-f0-9]{24}(?:[a-f0-9]{8})?$/.test(location.hash)) {
    selectRun(location.hash.slice(1)).catch(showError);
  } else {
    active = "";
    byId("current").hidden = true;
  }
}
window.addEventListener("hashchange", resumeFromLocation);
resumeFromLocation();


function setIdentity() {
  if (!subject) return;
  for (const field of [grade.elements.reviewer, resolution.elements.reviewer, records.elements.link_reviewer]) {
    field.value = subject;
    field.readOnly = true;
  }
}
function clearCandidates() {
  recordVersion++;
  byId("link-candidates").hidden = true;
  byId("link-candidates").querySelector("div").replaceChildren();
}
for (const name of ["contacts","clock"]) records.elements[name].addEventListener("change", clearCandidates);
intake.elements.score_format.addEventListener("change", () => {
  const fmt = intake.elements.score_format.value;
  byId("native-options").disabled = !fmt;
  byId("native-options").hidden = !fmt;
  const numeric = fmt && fmt !== "langfuse-v2";
  byId("percent-label").hidden = !numeric;
  intake.elements.pass_percent.disabled = !numeric;
  intake.elements.pass_percent.required = Boolean(numeric);
  byId("classifier-label").hidden = fmt !== "langfuse-v2";
  intake.elements.complaint_evaluator.disabled = fmt !== "langfuse-v2";
});
byId("suggest").addEventListener("click", async () => {
  clearCandidates();
  const version = recordVersion;
  const run = active;
  try {
    const result = await postFiles(run + "/links", records);
    if (run !== active || version !== recordVersion) return;
    const box = byId("link-candidates");
    for (const group of result.suggestions) {
      const label = document.createElement("label");
      const choice = document.createElement("input");
      choice.type = "checkbox";
      choice.name = "link";
      choice.value = JSON.stringify(group.case_ids);
      label.append(choice,document.createTextNode(group.case_ids.join(", ") + " · payment " + group.payment_ref));
      box.querySelector("div").append(label);
    }
    box.hidden = !result.suggestions.length;
    message.textContent = box.hidden ? "No candidate links found." : "Check each proposed link against the source contacts.";
  } catch (error) { showError(error); }
});
for (const form of [records,policyForm,bankForm]) form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.submitter;
  const run = active;
  button.disabled = true;
  try {
    if (form === bankForm) {
      const response = await fetch(run + "/bank", {method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify(Object.fromEntries(new FormData(form)))});
      if (!response.ok) throw new Error(await response.text());
      const blob = await response.blob();
      if (active !== run) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "bank-statement.html";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      byId("bank-digest").textContent = "Supply this SHA-256 through a separate authenticated channel: " + response.headers.get("X-Telryn-SHA256");
    } else if (form === policyForm) {
      const preview = button.value === "preview";
      const result = await postFiles(run + (preview ? "/policy-preview" : "/policy-review"), form);
      if (active !== run) return;
      const notices = result.notices.flatMap(n => [...(n.changed_clauses || []), ...(n.changed_pack_fields || [])]);
      const impact = result.affected ? (preview ? "Affected: " : "Marked for review: ") + notices.join(", ") : "This change does not affect the issued audit.";
      byId("policy-result").textContent = [impact, ...(result.calendar_warnings || [])].join(" ");
    } else {
      const result = await postFiles(run + "/records",form);
      if (active !== run) return;
      message.textContent = "Records attached. Evidence receipt: " + result.receipt;
    }
  } catch (error) { showError(error); }
  finally { button.disabled = false; }
});
setIdentity();
