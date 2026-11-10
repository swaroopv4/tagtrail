const $ = (selector) => document.querySelector(selector);
let project = null,
  selected = null,
  tab = "findings",
  busy = false,
  viewedSource = null;
const names = {
  pending: "PENDING",
  approved: "APPROVED",
  dismissed: "DISMISSED",
  acknowledged: "ACKNOWLEDGED",
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on"))
      node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "value") node.value = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat())
    if (child !== null && child !== undefined)
      node.append(
        child instanceof Node ? child : document.createTextNode(String(child)),
      );
  return node;
}

function notice(message, error = false) {
  const target = $("#notification");
  target.hidden = false;
  target.className = `notice ${error ? "error" : ""}`;
  target.textContent = message;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {
      const data = await response.json();
      message = Array.isArray(data.detail)
        ? data.detail
            .map((x) => `${x.loc?.slice(1).join(".")}: ${x.msg}`)
            .join("; ")
        : data.detail || message;
    } catch {
      /* Plain HTTP errors retain the status message. */
    }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function jsonPost(body) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

async function action(operation) {
  if (busy) return;
  busy = true;
  document.body.classList.add("busy");
  syncButtons();
  try {
    await operation();
  } catch (error) {
    notice(error.message, true);
  } finally {
    busy = false;
    document.body.classList.remove("busy");
    syncButtons();
  }
}

function syncButtons() {
  $("#run-checks").disabled =
    busy ||
    !project ||
    !project.documents.some((d) => d.role === "register") ||
    project.documents.length < 2;
  $("#export-pack").disabled = busy || !project?.active_run;
  $("#upload-button").disabled = busy || !project;
  $("#load-demo").disabled = busy;
  $("#new-project").disabled = busy;
  document.querySelectorAll(".decision-buttons button").forEach((button) => {
    button.disabled = busy;
  });
}

async function refreshProjects() {
  const projects = await api("/api/projects");
  $("#project-select").replaceChildren(
    el("option", { value: "" }, "Choose a project"),
    ...projects.map((p) => el("option", { value: p.id }, p.name)),
  );
  if (project) $("#project-select").value = project.id;
  return projects;
}

async function openProject(id) {
  project = await api(`/api/projects/${id}`);
  selected = null;
  viewedSource = null;
  location.hash = id;
  $("#project-select").value = id;
  render();
}

function render() {
  document.body.classList.toggle("has-project", Boolean(project));
  const findings = project?.findings || [],
    documents = project?.documents || [];
  if (!findings.some((f) => f.id === selected))
    selected =
      findings.find((f) => f.status === "pending")?.id ||
      findings[0]?.id ||
      null;
  const baseline = documents.find((d) => d.role === "register");
  const metrics = [
    [documents.length, "SOURCE", "DOCUMENTS"],
    [baseline?.record_count || 0, "REGISTER", "TAGS"],
    [
      findings.filter((f) => f.status === "pending").length,
      "PENDING",
      "REVIEW",
    ],
    [
      findings.filter((f) => f.status === "approved").length,
      "APPROVED",
      "CELL EDITS",
    ],
  ];
  $("#metrics").replaceChildren(
    ...metrics.map(([number, a, b], i) =>
      el(
        "div",
        { class: "metric" },
        el("strong", { class: i === 3 ? "highlight" : "" }, number),
        el("span", {}, a, el("br"), b),
      ),
    ),
  );
  $("#run-state").textContent = !project
    ? "No package selected"
    : project.active_run
      ? `Checked · generation ${project.generation}`
      : "Sources changed · run checks";
  $("#document-count").textContent = documents.length;
  $("#finding-count").textContent = findings.length;
  $("#documents").replaceChildren(...documents.map(docCard));
  if (!documents.length)
    $("#documents").append(
      el(
        "p",
        { class: "small-note" },
        "Add your register and reference files, or load the synthetic demo package.",
      ),
    );
  $("#warnings").replaceChildren(
    ...(project?.warnings || []).map((w) =>
      el(
        "div",
        { class: "warning-box" },
        el("strong", {}, w.filename),
        w.message,
      ),
    ),
  );
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
    button.setAttribute("aria-selected", button.dataset.tab === tab);
  });
  $("#status-filter").hidden = tab !== "findings";
  renderResults();
  renderReview();
  syncButtons();
}

function docCard(doc) {
  return el(
    "div",
    { class: "doc-card" },
    el(
      "div",
      { class: "doc-main" },
      el(
        "span",
        { class: "file-icon" },
        doc.filename.split(".").pop().toUpperCase(),
      ),
      el(
        "div",
        {},
        el("strong", {}, doc.filename),
        el(
          "p",
          { class: "doc-meta" },
          `${doc.revision} · ${doc.record_count} records`,
        ),
        el(
          "span",
          { class: "role-chip" },
          doc.role === "register"
            ? "COMPARISON REGISTER"
            : doc.complete_list
              ? "COMPLETE LIST"
              : "PARTIAL REFERENCE",
        ),
      ),
    ),
    el(
      "div",
      { class: "doc-links" },
      el(
        "button",
        {
          class: "link-button",
          onClick: () =>
            action(async () => {
              viewedSource = await api(
                `/api/projects/${project.id}/documents/${doc.id}`,
              );
              tab = "sources";
              render();
            }),
        },
        "Inspect records",
      ),
      el(
        "a",
        {
          class: "link-button",
          href: `/api/projects/${project.id}/documents/${doc.id}/download`,
        },
        "Download",
      ),
      el(
        "button",
        {
          class: "link-button danger-link",
          onClick: () => {
            if (
              confirm(
                `Remove ${doc.filename}? Existing decisions will become stale. Original bytes remain in local history.`,
              )
            )
              action(async () => {
                project = await api(
                  `/api/projects/${project.id}/documents/${doc.id}`,
                  { method: "DELETE" },
                );
                viewedSource = null;
                render();
                notice(
                  "Source removed. Run checks again before reviewing or exporting.",
                );
              });
          },
        },
        "Remove",
      ),
    ),
  );
}

function empty(title, message, demo = false) {
  const content = el(
    "div",
    { class: "empty-state" },
    el("div", { class: "empty-icon" }, "⌁"),
    el("h3", {}, title),
    el("p", {}, message),
  );
  if (demo)
    content.append(
      el(
        "button",
        { class: "button green empty-action", onClick: loadDemo },
        "Try the synthetic package ↗",
      ),
    );
  return content;
}

function renderResults() {
  const target = $("#results-content");
  target.replaceChildren();
  const query = $("#search").value.toLowerCase().trim();
  if (!project) {
    target.append(
      empty(
        "Follow the evidence.",
        "Load a synthetic package with a register, a schedule, a PDF drawing and a DXF drawing. Find mismatches and review the corrections end to end.",
        true,
      ),
    );
    return;
  }
  if (tab === "audit") {
    for (const event of project.audit) {
      target.append(
        el(
          "div",
          { class: "audit-event" },
          el("strong", {}, event.action.replaceAll("_", " ")),
          el(
            "p",
            {},
            `${event.actor} · ${new Date(event.created_at).toLocaleString()}`,
          ),
          el(
            "details",
            {},
            el("summary", {}, "Inspect event"),
            el("pre", {}, JSON.stringify(event.detail, null, 2)),
          ),
        ),
      );
    }
    return;
  }
  if (tab === "sources") {
    if (!viewedSource) {
      target.append(
        empty(
          "Inspect an actual source.",
          "Choose “Inspect records” on a document to see the parsed tags, values, locations and evidence snippets.",
        ),
      );
      return;
    }
    const rows = viewedSource.records.filter(
      (r) => !query || `${r.tag} ${r.excerpt}`.toLowerCase().includes(query),
    );
    const table = el(
      "table",
      { class: "record-table" },
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          ...["Tag", "Location", "Extracted evidence"].map((h) =>
            el("th", {}, h),
          ),
        ),
      ),
    );
    table.append(
      el(
        "tbody",
        {},
        ...rows
          .slice(0, 200)
          .map((r) =>
            el(
              "tr",
              {},
              el("td", {}, r.tag),
              el("td", {}, r.location),
              el("td", {}, r.excerpt),
            ),
          ),
      ),
    );
    target.append(
      el(
        "div",
        { class: "record-source" },
        el("h3", {}, viewedSource.filename),
        el("p", { class: "small-note" }, `SHA256 ${viewedSource.sha256}`),
        el("div", { class: "record-table-wrap" }, table),
        el(
          "p",
          { class: "small-note" },
          `Showing ${Math.min(rows.length, 200)} of ${rows.length} matching records. All records are used in checks.`,
        ),
      ),
    );
    return;
  }
  if (!project.active_run) {
    target.append(
      empty(
        "Ready for a fresh check.",
        "Upload a comparison register and at least one reference, then run checks. Every source change invalidates earlier approvals.",
      ),
    );
    return;
  }
  const status = $("#status-filter").value;
  const filtered = project.findings.filter(
    (f) =>
      (status === "all" || f.status === status) &&
      (!query ||
        `${f.tag} ${f.title} ${f.source.filename}`
          .toLowerCase()
          .includes(query)),
  );
  if (!project.findings.length) {
    target.append(
      empty(
        project.warnings.length
          ? "No discrepancies in extracted records."
          : "No discrepancies found in supported checks.",
        "Only explicit table fields and recognizable drawing tags were checked. Geometry, connectivity and engineering correctness still require review.",
      ),
    );
    return;
  }
  if (!filtered.length) {
    target.append(
      empty(
        "No matching findings.",
        "Change your search or review-status filter.",
      ),
    );
    return;
  }
  for (const finding of filtered) {
    const button = el(
      "button",
      {
        class: `finding ${selected === finding.id ? "selected" : ""}`,
        "aria-label": `${finding.tag}: ${finding.title}`,
        onClick: () => {
          selected = finding.id;
          renderResults();
          renderReview();
        },
      },
      el(
        "div",
        { class: "finding-top" },
        el(
          "span",
          { class: "finding-tag" },
          el("i", { class: `severity-dot ${finding.severity}` }),
          finding.tag,
        ),
        el(
          "span",
          { class: `status ${finding.status}` },
          names[finding.status],
        ),
      ),
      el("p", { class: "finding-title" }, finding.title),
      el(
        "p",
        { class: "finding-file" },
        `${finding.source.filename} · ${finding.source.location}`,
      ),
    );
    if (finding.proposal)
      button.append(
        el(
          "div",
          { class: "delta-inline" },
          el("span", { class: "delta-before" }, finding.actual || "(blank)"),
          "→",
          el("span", { class: "delta-after" }, finding.expected),
        ),
      );
    target.append(button);
  }
  target.append(
    el(
      "p",
      { class: "footer-note" },
      `${filtered.length} of ${project.findings.length} findings · proposals use the chosen register, not inferred design rules`,
    ),
  );
}

function evidence(label, source) {
  return el(
    "div",
    { class: "evidence-card" },
    el("p", { class: "evidence-label" }, label),
    el("strong", {}, source.filename),
    el("p", { class: "evidence-location" }, source.location),
    el("p", { class: "evidence-excerpt" }, source.excerpt),
    el("p", { class: "hash" }, `SHA256 ${source.sha256.slice(0, 20)}…`),
  );
}

function renderReview() {
  const target = $("#review-content");
  target.replaceChildren();
  const finding = project?.findings.find((f) => f.id === selected);
  if (!finding) {
    target.append(
      el(
        "div",
        { class: "review-empty" },
        "Select a finding to compare its source evidence and review a proposed correction.",
      ),
    );
    return;
  }
  target.append(
    el("p", { class: "review-tag" }, finding.tag),
    el("h3", { class: "review-title" }, finding.title),
    el("p", { class: "review-explanation" }, finding.explanation),
  );
  if (finding.authority)
    target.append(evidence("COMPARISON REGISTER", finding.authority));
  target.append(evidence("REFERENCE EVIDENCE", finding.source));
  if (finding.proposal)
    target.append(
      el(
        "div",
        { class: "proposal" },
        el(
          "div",
          {},
          el("span", {}, "CURRENT CELL"),
          el("strong", {}, finding.actual || "(blank)"),
        ),
        el(
          "div",
          {},
          el("span", {}, "PROPOSED CELL"),
          el("strong", {}, finding.expected),
        ),
      ),
    );
  else
    target.append(
      el(
        "div",
        { class: "warning-box" },
        "Manual review only. Confirm scope or correct the source and upload a new revision. Acknowledging does not fix the discrepancy.",
      ),
    );
  if (finding.status !== "pending")
    target.append(
      el(
        "p",
        { class: "decision-meta" },
        `${names[finding.status]} by ${finding.reviewer}. ${finding.note || "No note added."}`,
      ),
    );
  const reviewer = el("input", {
    id: "reviewer",
    value: localStorage.getItem("tagtrail-reviewer") || "",
    placeholder: "Your name",
    maxlength: "80",
    autocomplete: "name",
  });
  const note = el(
    "textarea",
    {
      id: "review-note",
      placeholder:
        "Reason or follow-up (required for manual acknowledgement or dismissal)",
      maxlength: "800",
    },
    finding.note || "",
  );
  const buttons = el("div", { class: "decision-buttons" });
  buttons.append(
    el(
      "button",
      {
        class: "button green",
        type: "button",
        onClick: () =>
          saveDecision(
            finding,
            finding.proposal ? "approved" : "acknowledged",
            reviewer.value,
            note.value,
          ),
      },
      finding.proposal ? "Approve correction" : "Acknowledge",
    ),
    el(
      "button",
      {
        class: "button outline",
        type: "button",
        onClick: () =>
          saveDecision(finding, "dismissed", reviewer.value, note.value),
      },
      "Dismiss",
    ),
  );
  if (finding.status !== "pending")
    buttons.append(
      el(
        "button",
        {
          class: "button quiet",
          type: "button",
          onClick: () =>
            saveDecision(finding, "pending", reviewer.value, note.value),
        },
        "Reopen",
      ),
    );
  target.append(
    el(
      "div",
      { class: "review-form" },
      el("label", { for: "reviewer" }, "Reviewer name (local audit label)"),
      reviewer,
      el("label", { for: "review-note" }, "Review note"),
      note,
      buttons,
      el(
        "p",
        { class: "review-notice" },
        "Approvals affect exported copies only. Original uploads stay unchanged. Reviewer names are local labels, not authenticated engineering signatures.",
      ),
    ),
  );
}

function saveDecision(finding, status, reviewer, note) {
  action(async () => {
    if (reviewer.trim().length < 2)
      throw new Error("Enter your reviewer name before saving a decision.");
    project = await api(
      `/api/projects/${project.id}/findings/${finding.id}/decision`,
      jsonPost({
        run_id: project.active_run,
        expected_version: finding.decision_version,
        status,
        reviewer,
        note,
      }),
    );
    localStorage.setItem("tagtrail-reviewer", reviewer.trim());
    render();
    notice(
      status === "approved"
        ? "Correction approved. It will appear in the exported copy; the uploaded file is unchanged."
        : `Decision saved: ${status}.`,
    );
  });
}

function loadDemo() {
  return action(async () => {
    project = await api("/api/demo", { method: "POST" });
    selected = null;
    viewedSource = null;
    tab = "findings";
    await refreshProjects();
    location.hash = project.id;
    render();
    notice(
      "Synthetic package loaded and checked. Three cell corrections can be reviewed; ambiguous and missing tags require manual review.",
    );
  });
}

$("#load-demo").addEventListener("click", loadDemo);
$("#new-project").addEventListener("click", () =>
  $("#project-dialog").showModal(),
);
$("#cancel-project").addEventListener("click", () =>
  $("#project-dialog").close(),
);
$("#project-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    project = await api(
      "/api/projects",
      jsonPost({ name: $("#project-name").value }),
    );
    selected = null;
    viewedSource = null;
    await refreshProjects();
    location.hash = project.id;
    $("#project-dialog").close();
    render();
    notice(
      "Project created. Add your comparison register and reference documents.",
    );
  });
});
$("#project-select").addEventListener("change", () =>
  action(async () => {
    const id = $("#project-select").value;
    if (id) await openProject(id);
    else {
      project = null;
      location.hash = "";
      render();
    }
  }),
);
$("#upload-role").addEventListener("change", () => {
  $("#register-warning").hidden = $("#upload-role").value !== "register";
});
$("#upload-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    if (!project) throw new Error("Create or choose a project first.");
    const file = $("#upload-file").files[0];
    if (!file) throw new Error("Choose a document.");
    const form = new FormData();
    form.append("file", file);
    form.append("role", $("#upload-role").value);
    form.append("revision", $("#upload-revision").value);
    form.append("complete_list", $("#complete-list").checked);
    project = await api(`/api/projects/${project.id}/documents`, {
      method: "POST",
      body: form,
    });
    viewedSource = null;
    $("#upload-file").value = "";
    render();
    notice(
      "Document added. Previous approvals are stale; run checks on the current package.",
    );
  });
});
$("#run-checks").addEventListener("click", () =>
  action(async () => {
    project = await api(`/api/projects/${project.id}/analyze`, {
      method: "POST",
    });
    selected = null;
    tab = "findings";
    render();
    notice(
      `Checks completed: ${project.findings.length} findings. Re-running checks starts a fresh review.`,
    );
  }),
);
$("#export-pack").addEventListener("click", () =>
  action(async () => {
    const response = await fetch(
      `/api/projects/${project.id}/export?run_id=${encodeURIComponent(project.active_run)}`,
      { method: "POST" },
    );
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.detail || "Export failed.");
    }
    const blob = await response.blob(),
      url = URL.createObjectURL(blob),
      link = el("a", { href: url, download: "tagtrail-review-pack.zip" });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    project = await api(`/api/projects/${project.id}`);
    render();
    notice(
      "Review pack downloaded: originals, approved copies, findings, audit history and source hashes.",
    );
  }),
);
document.querySelectorAll(".tab").forEach((button) =>
  button.addEventListener("click", () => {
    tab = button.dataset.tab;
    render();
  }),
);
$("#search").addEventListener("input", renderResults);
$("#status-filter").addEventListener("change", renderResults);

action(async () => {
  render();
  const projects = await refreshProjects();
  const id = location.hash.slice(1) || projects[0]?.id;
  if (id) await openProject(id);
});
