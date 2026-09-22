(() => {
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

  const TURNSTILE_SITEKEY = "0x4AAAAAAE_xSgJ6g787dvMB";
  const TURNSTILE_TEST_SITEKEY = "1x00000000000000000000AA";

  const els = {
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
    jobFile: document.getElementById("job-file"),
    jobError: document.getElementById("job-error"),
    preview: document.getElementById("preview"),
    previewStatus: document.getElementById("preview-status"),
    pdfPages: document.getElementById("pdf-pages"),
    downloadBtn: document.getElementById("download-btn"),
    deleteBtn: document.getElementById("delete-btn"),
    report: document.getElementById("report"),
    reportBanner: document.getElementById("report-banner"),
    hitsTable: document.getElementById("hits-table"),
    hitsBody: document.getElementById("hits-body"),
    reportCaveat: document.getElementById("report-caveat"),
  };

  const state = {
    file: null,
    conversionId: null,
    pollTimer: null,
    turnstileId: null,
    turnstileToken: "",
    previewToken: 0,
    pdfjs: null,
    pdfBlob: null,
    pdfPromise: null,
    downloadName: "document-safe.pdf",
  };

  sessionStorage.removeItem("bluepaper.apiKey");

  function showSubmitError(message) {
    els.submitError.hidden = !message;
    els.submitError.textContent = message || "";
  }

  function api(path, options = {}) {
    return fetch(path, options);
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

  function setReady() {
    els.convertBtn.disabled = !state.file || !state.turnstileToken;
  }

  async function loadSource() {
    try {
      const response = await api("/v1/source");
      if (!response.ok) {
        return;
      }
      const source = await response.json();
      const commit = source.commit
        ? ` @ ${escapeHtml(source.commit.slice(0, 7))}`
        : "";
      const sourceUrl = escapeHtml(source.source_url);
      els.sourceLine.innerHTML = `<a href="/docs">API</a> · <a href="${sourceUrl}">Corresponding source${commit}</a>`;
    } catch {
      /* the footer already links to the repository */
    }
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
    setReady();
  }

  function safePdfName(filename) {
    const raw = String(filename || "")
      .replaceAll("\\", "/")
      .split("/")
      .pop()
      .trim();
    const stem = raw.replace(/\.[^./]+$/, "").replace(/^\.+|\.+$/g, "").trim();
    const cleaned = stem
      .replace(/[\u0000-\u001f"\\/:*?<>|]+/g, "_")
      .replace(/^[\s._]+|[\s._]+$/g, "");
    const base = (cleaned || "document").slice(0, 180);
    return `${base}-safe.pdf`;
  }

  function filenameFromDisposition(header) {
    if (!header) {
      return "";
    }
    const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
    if (star) {
      try {
        return decodeURIComponent(star[1].trim());
      } catch {
        /* use the quoted fallback */
      }
    }
    const plain = /filename="([^"]*)"/.exec(header);
    return plain ? plain[1] : "";
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
    els.jobFile.textContent = state.downloadName;
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
      if (job.status === "succeeded") {
        fetchPdf().catch((err) => showSubmitError(err.message || "Preview failed"));
      }
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
    state.downloadName = safePdfName(state.file && state.file.name);
    clearPreview();
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
    setReady();
  }

  window.bluepaperTurnstile = function () {
    const widget = document.getElementById("turnstile-widget");
    if (!widget || !window.turnstile || state.turnstileId !== null) {
      return;
    }
    const host = window.location.hostname;
    const sitekey =
      host === "localhost" || host === "127.0.0.1"
        ? TURNSTILE_TEST_SITEKEY
        : TURNSTILE_SITEKEY;
    state.turnstileId = window.turnstile.render(widget, {
      sitekey,
      action: "queue-conversion",
      theme: "dark",
      callback(token) {
        state.turnstileToken = token;
        setReady();
      },
      "expired-callback"() {
        state.turnstileToken = "";
        setReady();
      },
      "error-callback"() {
        state.turnstileToken = "";
        setReady();
        showSubmitError("Bot check failed. Retry the widget.");
      },
    });
  };

  function clearPreview() {
    state.previewToken += 1;
    state.pdfPromise = null;
    state.pdfBlob = null;
    els.preview.hidden = true;
    els.previewStatus.hidden = true;
    els.pdfPages.hidden = true;
    els.pdfPages.replaceChildren();
  }

  async function renderPdfPages(blob) {
    if (!state.pdfjs) {
      state.pdfjs = await import("/ui/vendor/pdf.min.mjs");
      state.pdfjs.GlobalWorkerOptions.workerSrc = "/ui/vendor/pdf.worker.min.mjs";
    }
    const data = new Uint8Array(await blob.arrayBuffer());
    const pdf = await state.pdfjs.getDocument({ data }).promise;
    const fragment = document.createDocumentFragment();
    const displayWidth = Math.max((els.preview.clientWidth || 640) - 48, 320);
    const pixelRatio = window.devicePixelRatio || 1;
    for (let i = 1; i <= pdf.numPages; i += 1) {
      const page = await pdf.getPage(i);
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({
        scale: Math.min(4, (displayWidth * pixelRatio) / base.width),
      });
      const canvas = document.createElement("canvas");
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", `Page ${i} of ${pdf.numPages}`);
      const context = canvas.getContext("2d");
      await page.render({ canvasContext: context, viewport }).promise;
      fragment.appendChild(canvas);
    }
    return fragment;
  }

  async function loadPreview() {
    if (!state.conversionId) {
      return null;
    }
    const token = ++state.previewToken;
    state.pdfBlob = null;
    els.preview.hidden = false;
    els.previewStatus.hidden = false;
    els.previewStatus.textContent = "Loading preview…";
    els.pdfPages.hidden = true;
    els.pdfPages.replaceChildren();
    const response = await api(`/v1/conversions/${state.conversionId}/pdf`);
    if (token !== state.previewToken) {
      return null;
    }
    if (!response.ok) {
      els.previewStatus.textContent = "Preview unavailable.";
      showSubmitError(await readError(response));
      return null;
    }
    const blob = await response.blob();
    if (token !== state.previewToken) {
      return null;
    }
    const typed =
      blob.type === "application/pdf"
        ? blob
        : new Blob([blob], { type: "application/pdf" });
    const named =
      filenameFromDisposition(response.headers.get("Content-Disposition")) ||
      state.downloadName;
    state.downloadName = named;
    els.jobFile.textContent = named;
    state.pdfBlob = typed;
    let pages;
    try {
      pages = await renderPdfPages(typed);
    } catch {
      if (token === state.previewToken) {
        els.previewStatus.textContent = "Preview unavailable.";
      }
      return token === state.previewToken ? typed : null;
    }
    if (token !== state.previewToken) {
      return null;
    }
    els.pdfPages.replaceChildren(pages);
    els.previewStatus.hidden = true;
    els.pdfPages.hidden = false;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    els.preview.scrollIntoView({
      behavior: reduce ? "auto" : "smooth",
      block: "start",
    });
    return typed;
  }

  function fetchPdf() {
    if (state.pdfBlob) {
      return Promise.resolve(state.pdfBlob);
    }
    if (!state.pdfPromise) {
      const pending = loadPreview().finally(() => {
        if (state.pdfPromise === pending) {
          state.pdfPromise = null;
        }
      });
      state.pdfPromise = pending;
    }
    return state.pdfPromise;
  }

  async function downloadPdf() {
    if (!state.conversionId) {
      return;
    }
    const blob = await fetchPdf();
    if (!blob) {
      return;
    }
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = state.downloadName || safePdfName(state.file && state.file.name);
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
    clearPreview();
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

  els.convertForm.addEventListener("submit", (event) => {
    queueConversion(event).catch((err) => {
      showSubmitError(err.message || "Conversion failed");
      setReady();
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

  setReady();
  loadSource();
})();
