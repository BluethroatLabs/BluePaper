(() => {
  const KEY_STORAGE = "bluepaper.apiKey";
  const ACCEPT = [
    ".pdf",
    ".docx",
    ".doc",
    ".docm",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".odt",
    ".ods",
    ".odp",
    ".odg",
    ".hwp",
    ".hwpx",
    ".epub",
    ".jpg",
    ".jpeg",
    ".gif",
    ".png",
    ".svg",
    ".bmp",
    ".pnm",
    ".pbm",
    ".ppm",
    ".tif",
    ".tiff",
  ];
  const OCR_LANGS = [
    ["eng", "English"],
    ["fra", "French"],
    ["deu", "German"],
    ["spa", "Spanish"],
    ["ita", "Italian"],
    ["por", "Portuguese"],
    ["nld", "Dutch"],
    ["rus", "Russian"],
    ["ara", "Arabic"],
    ["heb", "Hebrew"],
    ["hin", "Hindi"],
    ["chi_sim", "Chinese (Simplified)"],
    ["chi_tra", "Chinese (Traditional)"],
    ["jpn", "Japanese"],
    ["kor", "Korean"],
    ["tur", "Turkish"],
    ["pol", "Polish"],
    ["ukr", "Ukrainian"],
    ["vie", "Vietnamese"],
  ];

  const els = {
    keyForm: document.getElementById("key-form"),
    keyInput: document.getElementById("api-key"),
    keySubmit: document.getElementById("key-submit"),
    keyStatus: document.getElementById("key-status"),
    sourceLine: document.getElementById("source-line"),
    convertForm: document.getElementById("convert-form"),
    convertBtn: document.getElementById("convert-btn"),
    file: document.getElementById("file"),
    drop: document.getElementById("drop"),
    dropTitle: document.getElementById("drop-title"),
    dropMeta: document.getElementById("drop-meta"),
    ocr: document.getElementById("ocr-lang"),
    submitError: document.getElementById("submit-error"),
    jobEmpty: document.getElementById("job-empty"),
    job: document.getElementById("job"),
    jobStatus: document.getElementById("job-status"),
    jobId: document.getElementById("job-id"),
    jobStage: document.getElementById("job-stage"),
    jobSha: document.getElementById("job-sha"),
    jobCreated: document.getElementById("job-created"),
    jobError: document.getElementById("job-error"),
    downloadBtn: document.getElementById("download-btn"),
    deleteBtn: document.getElementById("delete-btn"),
    report: document.getElementById("report"),
    reportBanner: document.getElementById("report-banner"),
    hitsTable: document.getElementById("hits-table"),
    hitsBody: document.getElementById("hits-body"),
    reportCaveat: document.getElementById("report-caveat"),
  };

  const state = {
    key: sessionStorage.getItem(KEY_STORAGE) || "",
    file: null,
    conversionId: null,
    pollTimer: null,
    turnstileId: null,
    turnstileToken: "",
  };

  function toast(message) {
    els.keyStatus.textContent = message;
    els.keyStatus.classList.toggle("is-on", Boolean(message));
  }

  function showSubmitError(message) {
    els.submitError.hidden = !message;
    els.submitError.textContent = message || "";
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (state.key) {
      headers.set("Authorization", `Bearer ${state.key}`);
    }
    return fetch(path, { ...options, headers });
  }

  async function readError(response) {
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") {
        return body.detail;
      }
    } catch {
      /* ignore */
    }
    return `${response.status} ${response.statusText}`;
  }

  function setConnected(connected) {
    els.convertBtn.disabled = !connected || !state.file || !state.turnstileToken;
    els.keySubmit.textContent = connected ? "Disconnect" : "Connect";
    els.keyInput.disabled = connected;
    if (connected) {
      els.keyInput.value = "••••••••";
    }
  }

  async function connect(key) {
    state.key = key.trim();
    if (!state.key) {
      throw new Error("API key required");
    }
    const response = await api("/v1/source");
    if (!response.ok) {
      state.key = "";
      sessionStorage.removeItem(KEY_STORAGE);
      throw new Error(await readError(response));
    }
    sessionStorage.setItem(KEY_STORAGE, state.key);
    const source = await response.json();
    const commit = source.commit ? ` @ ${escapeHtml(source.commit.slice(0, 7))}` : "";
    const sourceUrl = escapeHtml(source.source_url);
    els.sourceLine.innerHTML = `<a href="/docs">API</a> · <a href="${sourceUrl}">Corresponding source${commit}</a>`;
    setConnected(true);
    toast("Connected");
    window.setTimeout(() => toast(""), 1600);
  }

  function disconnect() {
    stopPoll();
    state.key = "";
    state.file = null;
    state.conversionId = null;
    sessionStorage.removeItem(KEY_STORAGE);
    els.keyInput.value = "";
    els.keyInput.disabled = false;
    resetDropLabel();
    setConnected(false);
    toast("Disconnected");
    window.setTimeout(() => toast(""), 1600);
  }

  function resetDropLabel() {
    els.dropTitle.textContent = "Drop an untrusted document";
    els.dropMeta.textContent =
      "PDF, Office, ODF, EPUB, HWP, or images. Typically 32 MiB max.";
  }

  function setFile(file) {
    if (!file) {
      return;
    }
    const name = file.name || "upload.bin";
    const ext = `.${name.split(".").pop().toLowerCase()}`;
    if (!ACCEPT.includes(ext)) {
      showSubmitError(`Unsupported file type (${ext || "unknown"})`);
      return;
    }
    state.file = file;
    els.dropTitle.textContent = name;
    els.dropMeta.textContent = `${formatBytes(file.size)} · ready to queue`;
    showSubmitError("");
    setConnected(Boolean(state.key));
  }

  function formatBytes(n) {
    if (n < 1024) {
      return `${n} B`;
    }
    if (n < 1024 * 1024) {
      return `${(n / 1024).toFixed(1)} KiB`;
    }
    return `${(n / (1024 * 1024)).toFixed(1)} MiB`;
  }

  function stopPoll() {
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function statusClass(status) {
    if (status === "running") return "is-running";
    if (status === "succeeded") return "is-succeeded";
    if (status === "failed") return "is-failed";
    return "";
  }

  function renderJob(job) {
    els.jobEmpty.hidden = true;
    els.job.hidden = false;
    els.jobStatus.textContent = job.status;
    els.jobStatus.className = `badge ${statusClass(job.status)}`;
    els.jobId.textContent = job.id;
    els.jobStage.textContent = job.stage || "—";
    els.jobSha.textContent = job.sha256;
    els.jobSha.title = job.sha256;
    els.jobCreated.textContent = job.created_at || "—";
    if (job.error) {
      els.jobError.hidden = false;
      els.jobError.textContent = job.error;
    } else {
      els.jobError.hidden = true;
      els.jobError.textContent = "";
    }
    els.downloadBtn.hidden = job.status !== "succeeded";
  }

  function renderReport(report) {
    els.report.hidden = false;
    const justified = Boolean(report.conversion_justified);
    els.reportBanner.className = `report-banner ${justified ? "is-justified" : "is-clean"}`;
    els.reportBanner.textContent = justified
      ? "Indicators found — conversion stripped active constructs"
      : "No indicators — not a malware verdict";
    const hits = Array.isArray(report.hits) ? report.hits : [];
    els.hitsTable.hidden = hits.length === 0;
    els.hitsBody.replaceChildren();
    for (const hit of hits) {
      const tr = document.createElement("tr");
      const offset =
        hit.first_offset === null || hit.first_offset === undefined
          ? "—"
          : String(hit.first_offset);
      tr.innerHTML = `<td>${escapeHtml(hit.id)}</td><td>${hit.count}</td><td>${offset}</td>`;
      els.hitsBody.appendChild(tr);
    }
    if (report.caveat) {
      els.reportCaveat.hidden = false;
      els.reportCaveat.textContent = report.caveat;
    } else {
      els.reportCaveat.hidden = true;
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function refreshJob() {
    if (!state.conversionId) {
      return;
    }
    const response = await api(`/v1/conversions/${state.conversionId}`);
    if (!response.ok) {
      showSubmitError(await readError(response));
      stopPoll();
      return;
    }
    const job = await response.json();
    renderJob(job);
    const terminal = ["succeeded", "failed", "cancelled"].includes(job.status);
    if (terminal) {
      stopPoll();
      const reportRes = await api(`/v1/conversions/${state.conversionId}/report`);
      if (reportRes.ok) {
        renderReport(await reportRes.json());
      } else if (reportRes.status !== 409) {
        showSubmitError(await readError(reportRes));
      }
      return;
    }
    state.pollTimer = window.setTimeout(refreshJob, 1000);
  }

  async function queueConversion(event) {
    event.preventDefault();
    showSubmitError("");
    if (!state.key) {
      showSubmitError("Connect with the operator API key first.");
      return;
    }
    if (!state.file) {
      showSubmitError("Choose a document.");
      return;
    }
    if (!state.turnstileToken) {
      showSubmitError("Complete the bot check.");
      return;
    }
    const data = new FormData();
    data.append("file", state.file, state.file.name);
    data.append("cf-turnstile-response", state.turnstileToken);
    if (els.ocr.value) {
      data.append("ocr_lang", els.ocr.value);
    }
    els.convertBtn.disabled = true;
    let response;
    try {
      response = await api("/v1/conversions", { method: "POST", body: data });
    } finally {
      resetTurnstile();
    }
    if (!response.ok) {
      showSubmitError(await readError(response));
      return;
    }
    const accepted = await response.json();
    state.conversionId = accepted.id;
    els.report.hidden = true;
    els.hitsTable.hidden = true;
    renderJob(accepted);
    stopPoll();
    await refreshJob();
  }

  function resetTurnstile() {
    state.turnstileToken = "";
    if (state.turnstileId !== null && window.turnstile) {
      window.turnstile.reset(state.turnstileId);
    }
    setConnected(Boolean(state.key));
  }

  window.bluepaperTurnstile = function () {
    const widget = document.getElementById("turnstile-widget");
    if (!widget || !window.turnstile || state.turnstileId !== null) {
      return;
    }
    state.turnstileId = window.turnstile.render(widget, {
      sitekey: "0x4AAAAAAE_xSgJ6g787dvMB",
      action: "queue-conversion",
      theme: "dark",
      callback(token) {
        state.turnstileToken = token;
        setConnected(Boolean(state.key));
      },
      "expired-callback"() {
        state.turnstileToken = "";
        setConnected(Boolean(state.key));
      },
      "error-callback"() {
        state.turnstileToken = "";
        setConnected(Boolean(state.key));
        showSubmitError("Bot check failed. Retry the widget.");
      },
    });
  };

  async function downloadPdf() {
    if (!state.conversionId) {
      return;
    }
    const response = await api(`/v1/conversions/${state.conversionId}/pdf`);
    if (!response.ok) {
      showSubmitError(await readError(response));
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "safe.pdf";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function deleteJob() {
    if (!state.conversionId) {
      return;
    }
    const response = await api(`/v1/conversions/${state.conversionId}`, {
      method: "DELETE",
    });
    if (!response.ok && response.status !== 204) {
      showSubmitError(await readError(response));
      return;
    }
    stopPoll();
    state.conversionId = null;
    els.job.hidden = true;
    els.jobEmpty.hidden = false;
    els.report.hidden = true;
  }

  function fillOcr() {
    for (const [code, label] of OCR_LANGS) {
      const option = document.createElement("option");
      option.value = code;
      option.textContent = label;
      els.ocr.appendChild(option);
    }
  }

  els.file.setAttribute("accept", ACCEPT.join(","));
  fillOcr();

  els.keyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.key) {
      disconnect();
      return;
    }
    try {
      await connect(els.keyInput.value);
    } catch (err) {
      setConnected(false);
      toast(err.message || "Invalid API key");
    }
  });

  els.convertForm.addEventListener("submit", (event) => {
    queueConversion(event).catch((err) => {
      showSubmitError(err.message || "Conversion failed");
      setConnected(Boolean(state.key));
    });
  });

  els.file.addEventListener("change", () => {
    if (els.file.files && els.file.files[0]) {
      setFile(els.file.files[0]);
    }
  });

  ["dragenter", "dragover"].forEach((type) => {
    els.drop.addEventListener(type, (event) => {
      event.preventDefault();
      els.drop.classList.add("is-drag");
    });
  });
  ["dragleave", "drop"].forEach((type) => {
    els.drop.addEventListener(type, (event) => {
      event.preventDefault();
      els.drop.classList.remove("is-drag");
    });
  });
  els.drop.addEventListener("drop", (event) => {
    const file = event.dataTransfer && event.dataTransfer.files[0];
    if (file) {
      setFile(file);
    }
  });

  els.downloadBtn.addEventListener("click", () => {
    downloadPdf().catch((err) => showSubmitError(err.message));
  });
  els.deleteBtn.addEventListener("click", () => {
    deleteJob().catch((err) => showSubmitError(err.message));
  });

  setConnected(false);
  if (state.key) {
    connect(state.key).catch(() => {
      state.key = "";
      sessionStorage.removeItem(KEY_STORAGE);
      els.keyInput.value = "";
      els.keyInput.disabled = false;
      setConnected(false);
      toast("Saved API key was rejected");
    });
  }
})();
