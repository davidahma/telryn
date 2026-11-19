"use strict";
const byId = (id) => document.getElementById(id);
const intake = byId("intake");
const grade = byId("grade");
const resolution = byId("resolution");
const message = byId("message");
let active = "";
let disputes = [];

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
    if (byId("mode").value === "budget") {
      settings.planning = {
        minutes: Number(source.get("minutes")), minutes_per_grade: Number(source.get("minutes_per_grade")),
        expected_rate: Number(source.get("expected_rate")) / 100
      };
      for (const key of ["flagged_rate", "unflagged_rate"]) {
        if (source.get(key)) settings.planning[key] = Number(source.get(key)) / 100;
      }
    } else {
      settings.flagged_sample = Number(source.get("flagged_sample"));
      settings.unflagged_sample = Number(source.get("unflagged_sample"));
    }
  } else {
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
  if (plan.coverage_complete && plan.scenario_interval) {
    const range = plan.scenario_interval;
    content.push(textNode("p", "At the rates you entered, a typical count gives a 95% interval of " +
        range[0] + "–" + range[1] + ". Expected interval width: " + Number(plan.expected_interval_width).toFixed(1) +
        " cases. With zero findings the upper bound would be " + plan.zero_findings_upper + "."));
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
  return finding + " (" + grade.disposition + ")";
}
function displayDispute() {
  const item = disputes[Number(resolution.elements.dispute.value)];
  byId("answers").textContent = item ? "Primary: " + answerText(item.primary) + ". Second: " + answerText(item.second) + "." : "";
}

async function selectRun(id) {
  active = id;
  byId("current").hidden = false;
  byId("report").href = id + "/report.html";
  byId("packet").href = id + "/grades.csv";
  location.hash = id;
  resolution.hidden = true;
  byId("agreement").textContent = "";
  const status = await request(id + "/review");
  if (active !== id) return;
  byId("agreement").textContent = status.compared ? "Independent reviewers agreed on severity for " + status.agreed + " of " + status.compared + " definite pairs." : "";
  disputes = status.open_disputes;
  resolution.elements.dispute.replaceChildren(...disputes.map((item, index) => new Option(item.case_id, index)));
  resolution.hidden = !disputes.length;
  displayDispute();
}

async function historyList() {
  const list = byId("history").querySelector("ul");
  const runs = await request("audits");
  list.replaceChildren();
  for (const run of runs) {
    const link = textNode("a", run.week + " · " + run.rubric + " · " + run.id.slice(0, 6));
    link.href = "#" + run.id;
    link.addEventListener("click", () => selectRun(run.id).then(() => byId("current").scrollIntoView()).catch(showError));
    const item = document.createElement("li");
    item.append(link);
    list.append(item);
  }
  byId("history").hidden = !runs.length;
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = event.submitter;
  button.disabled = true;
  try {
    if (form === intake && button.id === "preview") {
      forecast(await postFiles("plan", intake));
      message.textContent = "Preview ready. You can adjust the budget before creating the audit.";
      return;
    }
    let result;
    if (form === resolution) {
      const item = disputes[Number(form.elements.dispute.value)];
      const body = {case_id: item.case_id, dispute_id: item.dispute_id, reviewer: form.elements.reviewer.value,
        choice: form.elements.choice.value, reason: form.elements.reason.value, minutes: Number(form.elements.minutes.value)};
      result = await request(active + "/adjudicate", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    } else {
      result = await postFiles(form === intake ? "audits" : active + "/" + grade.elements.round.value, form);
    }
    await selectRun(result.id);
    await historyList();
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

for (const form of [intake, grade, resolution]) form.addEventListener("submit", submit);
intake.addEventListener("input", () => { byId("forecast").hidden = true; });
resolution.elements.dispute.addEventListener("change", displayDispute);
byId("mode").addEventListener("change", setMode);
setMode();
loadRubrics().catch(showError);
historyList().catch(showError);
if (/^#[a-f0-9]{24}$/.test(location.hash)) selectRun(location.hash.slice(1)).catch(showError);
