/* ============================================================
   CSV → Parquet Converter — Client-side logic
   ============================================================ */
(function () {
  "use strict";

  /* ---------- DOM refs ---------- */
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const dropZone = $("#drop-zone");
  const fileInput = $("#file-input");
  const fileName = $("#file-name");
  const analyzeBtn = $("#analyze-btn");
  const uploadError = $("#upload-error");
  const uploadProgress = $("#upload-progress");

  const stepUpload = $("#step-upload");
  const stepPreview = $("#step-preview");
  const stepDownload = $("#step-download");

  const previewMeta = $("#preview-meta");
  const previewTableHead = document.querySelector("#preview-table thead");
  const previewTableBody = document.querySelector("#preview-table tbody");
  const typeFields = $("#type-fields");
  const previewError = $("#preview-error");
  const convertBtn = $("#convert-btn");
  const convertProgress = $("#convert-progress");

  const downloadMeta = $("#download-meta");
  const downloadBtn = $("#download-btn");

  /* ---------- State ---------- */
  let currentToken = null;
  let currentColumns = [];
  let fileNameText = "";

  /* ---------- Helpers ---------- */
  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; }
  function clear(el) { el.innerHTML = ""; }

  function showError(el, msg) {
    el.textContent = msg;
    show(el);
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  }

  function formatNumber(n) {
    return n.toLocaleString("it-IT");
  }

  function displayValue(val) {
    if (val === null || val === undefined) return "—";
    if (typeof val === "boolean") return val ? "true" : "false";
    return String(val);
  }

  /* ---------- File selection ---------- */
  function selectFile(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      showError(uploadError, "Il file deve avere estensione .csv. Seleziona un file CSV valido.");
      return;
    }
    fileNameText = file.name;
    fileName.textContent = file.name + " (" + formatBytes(file.size) + ")";
    hide(uploadError);
    analyzeBtn.disabled = false;
  }

  dropZone.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files.length > 0) selectFile(fileInput.files[0]);
  });

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });

  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("drag-over");
  });

  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    selectFile(file);
    if (file) {
      // Set up the file input so the analyze button can use it
      const dt = new DataTransfer();
      dt.items.add(file);
      fileInput.files = dt.files;
      analyzeBtn.disabled = false;
    }
  });

  /* ---------- Analyze / Upload ---------- */
  analyzeBtn.addEventListener("click", async () => {
    const file = fileInput.files[0];
    if (!file) {
      showError(uploadError, "Seleziona prima un file CSV da caricare.");
      return;
    }

    hide(uploadError);
    show(uploadProgress);
    analyzeBtn.disabled = true;

    const formData = new FormData();
    formData.append("file", file);

    try {
      const resp = await fetch("api/upload", { method: "POST", body: formData });
      const data = await resp.json();

      if (!resp.ok) {
        showError(uploadError, data.error || "Errore sconosciuto durante l'upload.");
        hide(uploadProgress);
        analyzeBtn.disabled = false;
        return;
      }

      currentToken = data.token;
      currentColumns = data.columns;
      renderPreview(data);
      hide(uploadProgress);
      hide(stepUpload);
      show(stepPreview);
      // Scroll to preview
      stepPreview.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      showError(uploadError, "Errore di rete: impossibile raggiungere il server. Riprova.");
      hide(uploadProgress);
      analyzeBtn.disabled = false;
    }
  });

  /* ---------- Render Preview ---------- */
  function renderPreview(data) {
    const { columns, preview, row_count, file_size_bytes } = data;

    previewMeta.textContent =
      "File: " + formatBytes(file_size_bytes) +
      " · Righe: " + formatNumber(row_count) +
      " · Colonne: " + columns.length +
      ". Di seguito le prime " + preview.length + " righe.";

    // Build table
    clear(previewTableHead);
    clear(previewTableBody);

    // Header row with column names
    const headerRow = document.createElement("tr");
    const typeRow = document.createElement("tr");
    typeRow.className = "type-row";

    columns.forEach((col) => {
      const thName = document.createElement("th");
      thName.textContent = col.name;
      thName.scope = "col";
      headerRow.appendChild(thName);

      const thType = document.createElement("th");
      const badge = document.createElement("span");
      badge.className = "type-badge type-badge--" + col.detected_type;
      badge.textContent = col.detected_type;
      thType.appendChild(badge);
      typeRow.appendChild(thType);
    });

    previewTableHead.appendChild(headerRow);
    previewTableHead.appendChild(typeRow);

    // Data rows
    preview.forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((col) => {
        const td = document.createElement("td");
        td.textContent = displayValue(row[col.name]);
        tr.appendChild(td);
      });
      previewTableBody.appendChild(tr);
    });

    // Type override fields
    clear(typeFields);
    columns.forEach((col) => {
      const div = document.createElement("div");
      div.className = "type-field";

      const label = document.createElement("label");
      label.setAttribute("for", "type-" + col.index);
      label.textContent = col.name;

      const select = document.createElement("select");
      select.id = "type-" + col.index;
      select.setAttribute("data-col", col.name);
      const types = ["string", "integer", "float", "boolean", "date"];
      types.forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t;
        opt.textContent = t;
        if (t === col.detected_type) opt.selected = true;
        select.appendChild(opt);
      });

      const hint = document.createElement("span");
      hint.className = "detected-hint";
      hint.textContent = "Rilevato: " + col.detected_type;

      div.appendChild(label);
      div.appendChild(select);
      div.appendChild(hint);
      typeFields.appendChild(div);
    });

    hide(previewError);
    hide(convertProgress);
  }

  /* ---------- Convert ---------- */
  convertBtn.addEventListener("click", async () => {
    if (!currentToken) return;

    hide(previewError);
    show(convertProgress);
    convertBtn.disabled = true;

    // Gather type overrides
    const overrides = {};
    const selects = $$("#type-fields select");
    selects.forEach((sel) => {
      const colName = sel.getAttribute("data-col");
      overrides[colName] = sel.value;
    });

    try {
      const resp = await fetch("api/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: currentToken, types: overrides }),
      });
      const data = await resp.json();

      if (!resp.ok) {
        showError(previewError, data.error || "Errore durante la conversione.");
        hide(convertProgress);
        convertBtn.disabled = false;
        return;
      }

      renderDownload(data);
      hide(convertProgress);
      hide(stepPreview);
      show(stepDownload);
      stepDownload.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      showError(previewError, "Errore di rete durante la conversione. Riprova.");
      hide(convertProgress);
      convertBtn.disabled = false;
    }
  });

  /* ---------- Render Download ---------- */
  function renderDownload(data) {
    const { parquet_size_bytes, row_count, download_url } = data;
    downloadMeta.textContent =
      "Il file Parquet è stato generato correttamente. " +
      formatNumber(row_count) + " righe processate — dimensione: " +
      formatBytes(parquet_size_bytes) + ".";

    downloadBtn.onclick = () => {
      window.location.href = download_url;
    };
  }

  /* ---------- Back / Restart ---------- */
  $("#back-btn").addEventListener("click", resetToUpload);
  $("#restart-btn").addEventListener("click", resetToUpload);

  function resetToUpload() {
    hide(stepPreview);
    hide(stepDownload);
    show(stepUpload);
    hide(uploadProgress);
    hide(convertProgress);
    hide(uploadError);
    hide(previewError);
    fileName.textContent = "";
    analyzeBtn.disabled = true;
    fileInput.value = "";
    currentToken = null;
    currentColumns = [];
    fileNameText = "";
    stepUpload.scrollIntoView({ behavior: "smooth", block: "start" });
  }

})();
