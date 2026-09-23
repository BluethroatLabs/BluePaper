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
    signBtn: document.getElementById("sign-btn"),
    sign: document.getElementById("sign"),
    signDraw: document.getElementById("sign-draw"),
    signType: document.getElementById("sign-type"),
    signModeDraw: document.getElementById("sign-mode-draw"),
    signModeType: document.getElementById("sign-mode-type"),
    signPad: document.getElementById("sign-pad"),
    signName: document.getElementById("sign-name"),
    signClear: document.getElementById("sign-clear"),
    signStatus: document.getElementById("sign-status"),
    signClearMarks: document.getElementById("sign-clear-marks"),
    signDownload: document.getElementById("sign-download"),
    signError: document.getElementById("sign-error"),
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
    pdfLib: null,
    pdfBlob: null,
    pdfPromise: null,
    downloadName: "document-safe.pdf",
    signaturePad: null,
    signaturePadCtor: null,
    signMode: "draw",
    marks: [],
    signing: false,
    padResizeTimer: 0,
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
    state.marks = [];
    state.signing = false;
    els.preview.hidden = true;
    els.previewStatus.hidden = true;
    els.pdfPages.hidden = true;
    els.pdfPages.classList.remove("is-placing");
    els.pdfPages.replaceChildren();
    els.sign.hidden = true;
    els.signBtn.hidden = true;
    showSignError("");
  }

  async function renderPdfPages(blob) {
    if (!state.pdfjs) {
      state.pdfjs = await import("/ui/vendor/pdf.min.mjs");
      state.pdfjs.GlobalWorkerOptions.workerSrc = "/ui/vendor/pdf.worker.min.mjs";
    }
    const data = new Uint8Array(await blob.arrayBuffer());
    const pdf = await state.pdfjs.getDocument({ data }).promise;
    const fragment = document.createDocumentFragment();
    const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    const pageStyle = getComputedStyle(els.pdfPages);
    const padY =
      (parseFloat(pageStyle.paddingTop) || 0) + (parseFloat(pageStyle.paddingBottom) || 0);
    const padX =
      (parseFloat(pageStyle.paddingLeft) || 0) + (parseFloat(pageStyle.paddingRight) || 0);
    const previewBox = Math.min(window.innerHeight * 0.88, 64 * rootPx);
    const fitted = (previewBox - padY - 2) / 1.4142;
    const column = Math.max((els.preview.clientWidth || 640) - padX, 280);
    const displayWidth = Math.max(Math.min(column, fitted), 280);
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
      canvas.setAttribute("aria-hidden", "true");
      const context = canvas.getContext("2d");
      await page.render({ canvasContext: context, viewport }).promise;
      const frame = document.createElement("button");
      frame.type = "button";
      frame.className = "pdf-page";
      frame.dataset.page = String(i - 1);
      frame.setAttribute("aria-label", `Page ${i} of ${pdf.numPages}`);
      frame.appendChild(canvas);
      fragment.appendChild(frame);
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
    try {
      await showSign();
    } catch (err) {
      showSignError(err.message || "Could not load the signing library.");
    }
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

  function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function downloadPdf() {
    if (!state.conversionId) {
      return;
    }
    const blob = await fetchPdf();
    if (!blob) {
      return;
    }
    triggerDownload(
      blob,
      state.downloadName || safePdfName(state.file && state.file.name),
    );
  }

  function showSignError(message) {
    els.signError.hidden = !message;
    els.signError.textContent = message || "";
  }

  function signedPdfName(filename) {
    const name = filename || "document-safe.pdf";
    if (name.toLowerCase().endsWith("-safe.pdf")) {
      return `${name.slice(0, -"-safe.pdf".length)}-signed.pdf`;
    }
    if (name.toLowerCase().endsWith(".pdf")) {
      return `${name.slice(0, -4)}-signed.pdf`;
    }
    return `${name}-signed.pdf`;
  }

  function signatureReady() {
    if (state.signMode === "type") {
      return Boolean(els.signName.value.trim());
    }
    return Boolean(state.signaturePad && !state.signaturePad.isEmpty());
  }

  function renderSignStatus() {
    const ready = signatureReady();
    const count = state.marks.length;
    els.pdfPages.classList.toggle("is-placing", ready);
    let message = "Draw or type a signature, then click a page in the preview.";
    if (ready && count === 0) {
      message = "Click a page in the preview to place the signature.";
    } else if (count === 1) {
      message = ready
        ? "1 mark placed. Click the preview to add another."
        : "1 mark placed.";
    } else if (count > 1) {
      message = ready
        ? `${count} marks placed. Click the preview to add another.`
        : `${count} marks placed.`;
    }
    els.signStatus.textContent = message;
    els.signClearMarks.disabled = count === 0 || state.signing;
    els.signDownload.disabled = count === 0 || state.signing;
  }

  function resizePad(preserve) {
    const canvas = els.signPad;
    if (els.signDraw.hidden) {
      return;
    }
    const ratio = Math.max(window.devicePixelRatio || 1, 1);
    const width = canvas.offsetWidth;
    const height = canvas.offsetHeight;
    if (!width || !height) {
      return;
    }
    const nextW = Math.floor(width * ratio);
    const nextH = Math.floor(height * ratio);
    const stamp = `${nextW}x${nextH}`;
    if (canvas.dataset.bitmap === stamp) {
      return;
    }
    canvas.dataset.bitmap = stamp;
    const data = preserve && state.signaturePad ? state.signaturePad.toData() : null;
    canvas.width = nextW;
    canvas.height = nextH;
    canvas.getContext("2d").setTransform(ratio, 0, 0, ratio, 0, 0);
    if (!state.signaturePad) {
      return;
    }
    if (data && data.length) {
      state.signaturePad.fromData(data);
    } else {
      state.signaturePad.clear();
    }
  }

  async function ensurePad() {
    if (!state.signaturePadCtor) {
      const mod = await import("/ui/vendor/signature_pad.min.mjs");
      state.signaturePadCtor = mod.default;
    }
    if (!state.signaturePad) {
      resizePad(false);
      state.signaturePad = new state.signaturePadCtor(els.signPad, {
        penColor: "#141414",
        backgroundColor: "rgba(0,0,0,0)",
        minWidth: 0.8,
        maxWidth: 2.4,
      });
      state.signaturePad.addEventListener("endStroke", () => {
        renderSignStatus();
      });
      return;
    }
    resizePad(true);
  }

  async function showSign() {
    els.sign.hidden = false;
    els.signBtn.hidden = false;
    if (state.signMode === "draw") {
      await ensurePad();
    }
    renderSignStatus();
  }

  function setSignMode(mode) {
    state.signMode = mode;
    const draw = mode === "draw";
    els.signDraw.hidden = !draw;
    els.signType.hidden = draw;
    els.signModeDraw.classList.toggle("is-active", draw);
    els.signModeType.classList.toggle("is-active", !draw);
    els.signModeDraw.setAttribute("aria-pressed", draw ? "true" : "false");
    els.signModeType.setAttribute("aria-pressed", draw ? "false" : "true");
    if (draw) {
      ensurePad()
        .then(() => renderSignStatus())
        .catch((err) => {
          showSignError(err.message || "Could not load the signing library.");
        });
      return;
    }
    els.signName.focus();
    renderSignStatus();
  }

  function clearDraft() {
    if (state.signaturePad) {
      state.signaturePad.clear();
    }
    els.signName.value = "";
    renderSignStatus();
  }

  function clearMarks() {
    state.marks = [];
    els.pdfPages.querySelectorAll(".sign-mark").forEach((mark) => mark.remove());
    renderSignStatus();
  }

  function cropCanvasToInk(source) {
    const ctx = source.getContext("2d");
    const { width, height } = source;
    const pixels = ctx.getImageData(0, 0, width, height).data;
    let minX = width;
    let minY = height;
    let maxX = -1;
    let maxY = -1;
    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i + 3] < 8) {
        continue;
      }
      const p = i / 4;
      const x = p % width;
      const y = (p - x) / width;
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
    }
    if (maxX < 0) {
      return null;
    }
    const margin = Math.round(Math.max(width, height) * 0.02);
    minX = Math.max(0, minX - margin);
    minY = Math.max(0, minY - margin);
    maxX = Math.min(width - 1, maxX + margin);
    maxY = Math.min(height - 1, maxY + margin);
    const croppedWidth = maxX - minX + 1;
    const croppedHeight = maxY - minY + 1;
    const out = document.createElement("canvas");
    out.width = croppedWidth;
    out.height = croppedHeight;
    out
      .getContext("2d")
      .drawImage(
        source,
        minX,
        minY,
        croppedWidth,
        croppedHeight,
        0,
        0,
        croppedWidth,
        croppedHeight,
      );
    return out;
  }

  async function typedSignature(name) {
    const fontFamily = '"Instrument Serif", "Times New Roman", serif';
    try {
      await document.fonts.load(`italic 96px ${fontFamily}`);
    } catch {
      /* fall back to the next installed serif */
    }
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    const ratio = Math.max(window.devicePixelRatio || 1, 1);
    let fontSize = 96;
    ctx.font = `italic ${fontSize}px ${fontFamily}`;
    let metrics = ctx.measureText(name);
    while (metrics.width > 1400 && fontSize > 36) {
      fontSize -= 4;
      ctx.font = `italic ${fontSize}px ${fontFamily}`;
      metrics = ctx.measureText(name);
    }
    const padX = Math.round(fontSize * 0.15);
    const width = Math.ceil(metrics.width + padX * 2);
    const height = Math.ceil(fontSize * 1.35);
    canvas.width = Math.ceil(width * ratio);
    canvas.height = Math.ceil(height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.font = `italic ${fontSize}px ${fontFamily}`;
    ctx.fillStyle = "#141414";
    ctx.textBaseline = "middle";
    ctx.fillText(name, padX, height / 2);
    return {
      dataUrl: canvas.toDataURL("image/png"),
      aspect: canvas.width / canvas.height,
    };
  }

  async function currentSignature() {
    if (state.signMode === "type") {
      const name = els.signName.value.trim();
      if (!name) {
        return null;
      }
      return typedSignature(name);
    }
    if (!state.signaturePad || state.signaturePad.isEmpty()) {
      return null;
    }
    const cropped = cropCanvasToInk(els.signPad);
    if (!cropped) {
      return null;
    }
    return {
      dataUrl: cropped.toDataURL("image/png"),
      aspect: cropped.width / cropped.height,
    };
  }

  function markBox(rect, clientX, clientY, aspect) {
    let widthRatio = 0.3;
    let heightRatio = (widthRatio * rect.width) / aspect / rect.height;
    const maxHeight = 0.22;
    if (heightRatio > maxHeight) {
      heightRatio = maxHeight;
      widthRatio = (heightRatio * rect.height * aspect) / rect.width;
    }
    let left = (clientX - rect.left) / rect.width - widthRatio / 2;
    let top = (clientY - rect.top) / rect.height - heightRatio / 2;
    left = Math.min(Math.max(left, 0), Math.max(0, 1 - widthRatio));
    top = Math.min(Math.max(top, 0), Math.max(0, 1 - heightRatio));
    return { left, top, widthRatio };
  }

  async function placeSignature(pageEl, clientX, clientY) {
    const rect = pageEl.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      return;
    }
    const signature = await currentSignature();
    if (!signature) {
      els.signStatus.textContent = "Draw or type a signature first.";
      return;
    }
    const box = markBox(rect, clientX, clientY, signature.aspect);
    const img = document.createElement("img");
    img.className = "sign-mark";
    img.alt = "";
    img.draggable = false;
    img.src = signature.dataUrl;
    img.style.left = `${box.left * 100}%`;
    img.style.top = `${box.top * 100}%`;
    img.style.width = `${box.widthRatio * 100}%`;
    pageEl.appendChild(img);
    state.marks.push({
      pageIndex: Number(pageEl.dataset.page),
      left: box.left,
      top: box.top,
      widthRatio: box.widthRatio,
      dataUrl: signature.dataUrl,
      aspect: signature.aspect,
    });
    showSignError("");
    renderSignStatus();
  }

  function dataUrlToBytes(dataUrl) {
    const encoded = dataUrl.slice(dataUrl.indexOf(",") + 1);
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }

  async function downloadSignedPdf() {
    if (!state.marks.length || state.signing) {
      return;
    }
    const blob = await fetchPdf();
    if (!blob) {
      return;
    }
    state.signing = true;
    renderSignStatus();
    showSignError("");
    try {
      if (!state.pdfLib) {
        state.pdfLib = await import("/ui/vendor/pdf-lib.esm.min.mjs");
      }
      const pdfDoc = await state.pdfLib.PDFDocument.load(await blob.arrayBuffer());
      const cache = new Map();
      for (const mark of state.marks) {
        let image = cache.get(mark.dataUrl);
        if (!image) {
          image = await pdfDoc.embedPng(dataUrlToBytes(mark.dataUrl));
          cache.set(mark.dataUrl, image);
        }
        const page = pdfDoc.getPage(mark.pageIndex);
        const { width, height } = page.getSize();
        const sigWidth = mark.widthRatio * width;
        const sigHeight = sigWidth / mark.aspect;
        page.drawImage(image, {
          x: mark.left * width,
          y: height - mark.top * height - sigHeight,
          width: sigWidth,
          height: sigHeight,
        });
      }
      const bytes = await pdfDoc.save();
      triggerDownload(
        new Blob([bytes], { type: "application/pdf" }),
        signedPdfName(state.downloadName),
      );
    } catch (err) {
      showSignError(err.message || "Could not sign the PDF.");
    } finally {
      state.signing = false;
      renderSignStatus();
    }
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
  els.signBtn.addEventListener("click", () => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    els.sign.scrollIntoView({
      behavior: reduce ? "auto" : "smooth",
      block: "start",
    });
  });
  els.signModeDraw.addEventListener("click", () => setSignMode("draw"));
  els.signModeType.addEventListener("click", () => setSignMode("type"));
  els.signName.addEventListener("input", () => renderSignStatus());
  els.signClear.addEventListener("click", clearDraft);
  els.signClearMarks.addEventListener("click", clearMarks);
  els.signDownload.addEventListener("click", () => {
    downloadSignedPdf().catch((err) => showSignError(err.message));
  });
  els.pdfPages.addEventListener("click", (event) => {
    const page = event.target.closest(".pdf-page");
    if (!page || !els.pdfPages.contains(page)) {
      return;
    }
    const rect = page.getBoundingClientRect();
    const keyboard = event.detail === 0;
    const x = keyboard ? rect.left + rect.width / 2 : event.clientX;
    const y = keyboard ? rect.top + rect.height / 2 : event.clientY;
    placeSignature(page, x, y).catch((err) => showSignError(err.message));
  });
  window.addEventListener("resize", () => {
    window.clearTimeout(state.padResizeTimer);
    state.padResizeTimer = window.setTimeout(() => {
      if (!els.sign.hidden && state.signaturePad) {
        resizePad(true);
      }
    }, 150);
  });
  els.deleteBtn.addEventListener("click", () => {
    deleteJob().catch((err) => showSubmitError(err.message));
  });

  setReady();
  loadSource();
})();
