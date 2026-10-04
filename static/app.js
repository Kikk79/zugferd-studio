/**
 * ZUGFeRD Studio - Frontend logic.
 *
 * The server is the single source of truth for amounts: every edit is sent to
 * /api/calculate (debounced) and the totals / findings shown here come back
 * from there. Server text is only ever inserted with textContent.
 */

document.addEventListener("DOMContentLoaded", () => {
  const $ = (id) => document.getElementById(id);

  const dropZone = $("dropZone");
  const fileInput = $("fileInput");
  const profileSelect = $("profileSelect");
  const uploadSection = $("uploadSection");
  const loadingSection = $("loadingSection");
  const resultSection = $("resultSection");
  const itemsTableBody = $("itemsTableBody");
  const editForm = $("editInvoiceForm");
  const toast = $("toast");

  // Fields bound to the invoice object via their data-path attribute
  const bound = Array.from(document.querySelectorAll("[data-path]"));

  let sessionId = null;
  let invoice = {};        // full invoice object; the form edits it, unknown keys are preserved
  let items = [];
  let calcTimer = null;
  let calcSeq = 0;

  const money = (value, currency) =>
    new Intl.NumberFormat("de-DE", { style: "currency", currency: /^[A-Z]{3}$/.test(currency) ? currency : "EUR" })
      .format(value || 0);

  // ------------------------------------------------------------ paths
  function getPath(obj, path) {
    return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
  }
  function setPath(obj, path, value) {
    const keys = path.split(".");
    const last = keys.pop();
    const target = keys.reduce((o, k) => (o[k] = o[k] && typeof o[k] === "object" ? o[k] : {}), obj);
    target[last] = value;
  }

  // ------------------------------------------------------------ upload
  ["dragenter", "dragover"].forEach((ev) =>
    dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((ev) =>
    dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.remove("dragover"); }));
  dropZone.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) handleFileUpload(e.dataTransfer.files[0]); });
  $("browseBtn").addEventListener("click", openWithDialog);
  fileInput.addEventListener("change", (e) => { if (e.target.files.length) handleFileUpload(e.target.files[0]); });
  $("sampleBtn").addEventListener("click", loadSampleInvoice);
  $("btnNewUpload").addEventListener("click", resetToUpload);

  async function readError(response, fallback) {
    const body = await response.json().catch(() => ({}));
    return body.detail && typeof body.detail === "string" ? body.detail : fallback;
  }

  async function handleFileUpload(file) {
    if (![".pdf", ".docx", ".doc"].some((ext) => file.name.toLowerCase().endsWith(ext))) {
      showToast("Bitte nur PDF- oder Word-Dokumente (.docx, .doc) hochladen!", "error");
      return;
    }
    showLoading("Dokument wird analysiert...");
    const formData = new FormData();
    formData.append("file", file);
    formData.append("profile", profileSelect.value);
    formData.append("auto_generate", "true");
    try {
      const response = await fetch("/api/upload", { method: "POST", body: formData });
      if (!response.ok) throw new Error(await readError(response, "Fehler beim Verarbeiten des Dokuments"));
      displayResult(await response.json());
    } catch (err) {
      console.error(err);
      resetToUpload();
      showToast("Fehler: " + err.message, "error");
    }
  }

  // Windows "Open" dialog (runs in the local server) - the source folder is then known,
  // so the ZUGFeRD PDF can be saved next to the original invoice.
  async function openWithDialog() {
    showLoading("Bitte Rechnung im Windows-Dialog auswählen...");
    try {
      const response = await fetch("/api/pick-file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: profileSelect.value }),
      });
      if (response.status === 501) {          // no native dialog available -> browser picker
        resetToUpload();
        fileInput.click();
        return;
      }
      if (!response.ok) throw new Error(await readError(response, "Fehler beim Öffnen der Datei"));
      const data = await response.json();
      if (data.cancelled) return resetToUpload();
      displayResult(data);
    } catch (err) {
      console.error(err);
      resetToUpload();
      showToast("Fehler: " + err.message, "error");
    }
  }

  // Invoice dragged onto ZUGFeRD-Studio.exe: the launcher opens this page with ?pending=1
  async function loadPending() {
    history.replaceState(null, "", location.pathname);
    showLoading("Rechnung wird analysiert...");
    try {
      const response = await fetch("/api/pending", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: profileSelect.value }),
      });
      if (!response.ok) throw new Error(await readError(response, "Fehler beim Verarbeiten der Datei"));
      const data = await response.json();
      if (data.empty) return resetToUpload();
      displayResult(data);
    } catch (err) {
      console.error(err);
      resetToUpload();
      showToast("Fehler: " + err.message, "error");
    }
  }

  async function loadSampleInvoice() {
    showLoading("Muster-Rechnung wird erzeugt...");
    try {
      const response = await fetch("/api/sample");
      if (!response.ok) throw new Error(await readError(response, "Fehler beim Laden der Muster-Rechnung"));
      displayResult(await response.json());
    } catch (err) {
      console.error(err);
      resetToUpload();
      showToast("Fehler: " + err.message, "error");
    }
  }

  function showLoading(msg) {
    $("loadingText").textContent = msg;
    uploadSection.classList.add("hidden");
    resultSection.classList.add("hidden");
    loadingSection.classList.remove("hidden");
    const steps = ["step1", "step2", "step3"].map($);
    steps.forEach((s, i) => { s.classList.toggle("active", i === 0); });
    setTimeout(() => steps[1].classList.add("active"), 400);
    setTimeout(() => steps[2].classList.add("active"), 800);
  }

  function resetToUpload() {
    fileInput.value = "";
    sessionId = null;
    invoice = {};
    items = [];
    resultSection.classList.add("hidden");
    loadingSection.classList.add("hidden");
    uploadSection.classList.remove("hidden");
  }

  // ------------------------------------------------------------ result
  const STATES = {
    valid_zugferd: { cls: "ok", title: "ZUGFeRD E-Rechnung erfolgreich erzeugt" },
    already_zugferd: { cls: "info", title: "Das Dokument ist bereits eine ZUGFeRD E-Rechnung" },
    needs_review: { cls: "warn", title: "Bitte Angaben prüfen und ergänzen" },
    business_rules_failed: { cls: "error", title: "Erzeugt, aber EN-16931-Regeln verletzt" },
  };

  function displayResult(data) {
    loadingSection.classList.add("hidden");
    resultSection.classList.remove("hidden");

    sessionId = data.session_id;
    invoice = data.invoice_data || {};
    items = invoice.items = invoice.items || [];
    if (data.profile && Array.from(profileSelect.options).some((o) => o.value === data.profile)) {
      profileSelect.value = data.profile;
    }

    const state = STATES[data.status] || STATES.needs_review;
    $("resultMainTitle").textContent = state.title;
    $("resultStateIcon").className = "success-badge-icon state-" + state.cls;
    $("resultProfileBadge").textContent = "Profil: " + (data.profile || "").toUpperCase();
    $("resultFileName").textContent = ruleSummary(data);
    showOutput(data.output);
    $("btnUpdateLabel").textContent = data.pdf_download_url ? "ZUGFeRD neu erstellen" : "ZUGFeRD erstellen";

    setDownload($("btnDownloadPdf"), data.pdf_download_url);
    setDownload($("btnDownloadXml"), data.xml_download_url);

    fillForm();
    renderItems(data.totals ? data.totals.line_nets : []);
    renderTotals(data.totals);
    renderFindings(data);

    $("xmlCodeBlock").textContent = data.xml_content || "(Noch kein XML – erst nach dem Erstellen verfügbar.)";
    $("pdfPreviewFrame").src = data.preview_url + "?t=" + Date.now();
  }

  function showOutput(output) {
    const line = $("resultOutput");
    line.classList.remove("output-ok", "output-error");
    if (!output) {
      line.classList.add("hidden");
      return;
    }
    line.classList.remove("hidden");
    if (output.saved) {
      line.textContent = "Gespeichert: " + output.path + (output.opened ? " (geöffnet)" : "");
      line.classList.add("output-ok");
    } else {
      line.textContent = "Nicht gespeichert: " + output.error;
      line.classList.add("output-error");
      showToast(output.error, "error");
    }
  }

  function ruleSummary(data) {
    const file = data.filename ? `Datei: ${data.filename} • ` : "";
    const br = data.business_rules || {};
    if (!data.xml_content) return file + "Noch kein XML erzeugt";
    if (br.status === "passed") return file + "XSD- und EN-16931-Schematron-Prüfung bestanden";
    if (br.status === "failed") return file + `${br.failures.length} Schematron-Regel(n) verletzt`;
    return file + "XSD-geprüft • " + (br.detail || "Geschäftsregeln nicht geprüft");
  }

  function setDownload(anchor, url) {
    if (url) {
      anchor.href = url;
      anchor.classList.remove("disabled");
      anchor.removeAttribute("aria-disabled");
    } else {
      anchor.removeAttribute("href");
      anchor.classList.add("disabled");
      anchor.setAttribute("aria-disabled", "true");
    }
  }

  function fillForm() {
    const hideZero = new Set(["prepaid_amount", "payment.skonto_days", "payment.skonto_percent"]);
    bound.forEach((el) => {
      const v = getPath(invoice, el.dataset.path);
      const empty = v === undefined || v === null || (hideZero.has(el.dataset.path) && !Number(v));
      el.value = empty ? "" : String(v);
    });
    const first = (invoice.preceding_invoices || [])[0] || {};
    $("precedingId").value = first.id || "";
    $("precedingDate").value = first.date || "";
  }

  function readForm() {
    bound.forEach((el) => setPath(invoice, el.dataset.path, el.value.trim()));
    const rest = (invoice.preceding_invoices || []).slice(1);
    const id = $("precedingId").value.trim();
    invoice.preceding_invoices = (id ? [{ id, date: $("precedingDate").value.trim() }] : []).concat(rest);
    if (!invoice.prepaid_amount) invoice.prepaid_amount = 0;
    ["seller.country", "buyer.country", "currency"].forEach((p) =>
      setPath(invoice, p, String(getPath(invoice, p) || "").toUpperCase()));
    invoice.items = items;
    return invoice;
  }

  // ------------------------------------------------------------ findings
  function renderGroup(container, title, rows) {
    container.replaceChildren();
    container.classList.toggle("hidden", rows.length === 0);
    if (!rows.length) return;
    const h = document.createElement("strong");
    h.textContent = title;
    const ul = document.createElement("ul");
    rows.forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      ul.appendChild(li);
    });
    container.append(h, ul);
  }

  function renderFindings(data) {
    const issues = data.issues || { errors: [], warnings: [] };
    const br = data.business_rules || { failures: [] };
    const errors = issues.errors.map((i) => i.message)
      .concat((br.failures || []).map((f) => `[${f.id}] ${f.text}`));
    renderGroup($("issuesErrors"), "Zu beheben", errors);
    renderGroup($("issuesWarnings"), "Bitte prüfen", issues.warnings.map((i) => i.message));
    renderGroup($("issuesNotes"), "Hinweise zur Texterkennung", data.extraction_notes || []);
    $("issuesPanel").classList.toggle("hidden", !(errors.length || issues.warnings.length || (data.extraction_notes || []).length));
  }

  // ------------------------------------------------------------ items
  function renderItems(lineNets) {
    itemsTableBody.replaceChildren();
    $("tabItemsCount").textContent = items.length;
    items.forEach((item, index) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input type="text" data-f="name" value="${escapeHtml(item.name || "")}" /></td>
        <td><input type="number" step="any" data-f="quantity" value="${item.quantity ?? 1}" style="width:80px" /></td>
        <td><input type="text" data-f="unit" value="${escapeHtml(item.unit || "C62")}" style="width:70px" /></td>
        <td><input type="number" step="any" data-f="unit_price" value="${item.unit_price ?? 0}" style="width:100px" /></td>
        <td><input type="number" step="any" data-f="tax_percent" value="${item.tax_percent ?? 19}" style="width:60px" /> %</td>
        <td class="font-mono line-net" style="text-align:right">${lineNets && lineNets[index] !== undefined ? money(lineNets[index], invoice.currency) : "–"}</td>
        <td style="text-align:center"><button type="button" class="btn-remove-row" title="Position löschen">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
        </button></td>`;
      tr.querySelectorAll("input").forEach((input) => input.addEventListener("input", () => {
        const f = input.dataset.f;
        item[f] = f === "name" || f === "unit" ? input.value : (input.value === "" ? 0 : parseFloat(input.value));
        scheduleCalc();
      }));
      tr.querySelector(".btn-remove-row").addEventListener("click", () => {
        items.splice(index, 1);
        renderItems([]);
        scheduleCalc();
      });
      itemsTableBody.appendChild(tr);
    });
  }

  $("btnAddItem").addEventListener("click", () => {
    items.push({ name: `Neue Position ${items.length + 1}`, quantity: 1, unit: "C62", unit_price: 0,
                 tax_percent: items.length ? items[0].tax_percent : 19 });
    renderItems([]);
    scheduleCalc();
  });

  // ------------------------------------------------------------ live calculation
  function renderTotals(t) {
    if (!t) return;
    const cur = t.currency;
    $("kpiInvoiceId").textContent = invoice.invoice_id || "–";
    $("kpiInvoiceDate").textContent = invoice.issue_date || "–";
    $("kpiNetTotal").textContent = money(t.net, cur);
    $("kpiGrossTotal").textContent = money(t.due, cur);
    $("summaryNet").textContent = money(t.net, cur);
    $("summaryGross").textContent = money(t.gross, cur);
    $("summaryDue").textContent = money(t.due, cur);
    $("summaryPrepaid").textContent = "– " + money(t.prepaid, cur);
    $("summaryPrepaidRow").classList.toggle("hidden", !t.prepaid);
    const taxRows = $("summaryTaxRows");
    taxRows.replaceChildren();
    t.tax_groups.forEach((g) => {
      const row = document.createElement("div");
      row.className = "totals-row";
      const label = document.createElement("span");
      label.textContent = `zzgl. ${g.percent.toLocaleString("de-DE")} % USt auf ${money(g.basis, cur)}:`;
      const val = document.createElement("span");
      val.className = "font-mono";
      val.textContent = money(g.tax, cur);
      row.append(label, val);
      taxRows.appendChild(row);
    });
    document.querySelectorAll(".line-net").forEach((cell, i) => {
      if (t.line_nets[i] !== undefined) cell.textContent = money(t.line_nets[i], cur);
    });
  }

  function scheduleCalc() {
    clearTimeout(calcTimer);
    calcTimer = setTimeout(runCalc, 250);
  }

  async function runCalc() {
    const seq = ++calcSeq;
    try {
      const response = await fetch("/api/calculate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invoice_data: readForm() }),
      });
      if (!response.ok || seq !== calcSeq) return;
      const data = await response.json();
      renderTotals(data.totals);
      renderFindings({ issues: data.issues, extraction_notes: [], business_rules: { failures: [] } });
    } catch (err) {
      console.error(err);
    }
  }

  bound.concat([$("precedingId"), $("precedingDate")]).forEach((el) => el.addEventListener("input", scheduleCalc));

  // ------------------------------------------------------------ generate
  editForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!sessionId) return showToast("Keine aktive Sitzung gefunden.", "error");

    const btn = $("btnUpdateInvoice");
    const original = btn.innerHTML;
    btn.textContent = "Wird erstellt...";
    btn.disabled = true;
    try {
      const response = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, profile: profileSelect.value, invoice_data: readForm() }),
      });
      if (response.status === 422) {
        const body = await response.json();
        renderFindings({ issues: body.issues, extraction_notes: [], business_rules: { failures: [] } });
        showToast(body.detail, "error");
        return;
      }
      if (!response.ok) throw new Error(await readError(response, "Fehler beim Erstellen"));
      const data = await response.json();
      displayResult(data);
      showToast(data.status === "business_rules_failed" ? "Erstellt, aber Regelverstöße gefunden." : "ZUGFeRD-Rechnung erstellt!",
                data.status === "business_rules_failed" ? "error" : "success");
    } catch (err) {
      console.error(err);
      showToast("Fehler: " + err.message, "error");
    } finally {
      btn.innerHTML = original;
      btn.disabled = false;
    }
  });

  // ------------------------------------------------------------ tabs / copy / helpers
  document.querySelectorAll(".tab-btn").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    const pane = $(btn.dataset.tab);
    if (pane) pane.classList.add("active");
  }));

  $("btnCopyXml").addEventListener("click", () => {
    const text = $("xmlCodeBlock").textContent;
    if (!text) return;
    navigator.clipboard.writeText(text)
      .then(() => showToast("Factur-X XML in Zwischenablage kopiert!", "success"))
      .catch(() => showToast("Kopieren fehlgeschlagen.", "error"));
  });

  // ------------------------------------------------------------ views + settings
  const viewBtns = Array.from(document.querySelectorAll(".view-btn"));

  function showView(id) {
    viewBtns.forEach((b) => b.classList.toggle("active", b.dataset.view === id));
    ["viewInvoice", "viewSettings"].forEach((v) => $(v).classList.toggle("hidden", v !== id));
    if (id === "viewSettings") loadSettings();
  }
  viewBtns.forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

  function applySettings(cfg) {
    $("setOutputDir").value = cfg.output_dir || "";
    $("setOpenPdf").checked = !!cfg.open_pdf;
    $("setAiEnabled").checked = !!cfg.ai_enabled;
    $("aiEnvNote").classList.toggle("hidden", !cfg.ai_forced_by_env);
    $("settingsIni").textContent = cfg.ini_path ? "Gespeichert in: " + cfg.ini_path : "";
  }

  async function loadSettings() {
    try {
      const response = await fetch("/api/settings");
      if (!response.ok) throw new Error(await readError(response, "Einstellungen konnten nicht geladen werden"));
      applySettings(await response.json());
    } catch (err) {
      showToast("Fehler: " + err.message, "error");
    }
  }

  $("btnPickFolder").addEventListener("click", async () => {
    try {
      const response = await fetch("/api/pick-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initial: $("setOutputDir").value.trim() }),
      });
      if (response.status === 501) return showToast("Ordnerdialog nicht verfügbar - bitte den Pfad eintippen.", "error");
      if (!response.ok) throw new Error(await readError(response, "Ordnerdialog fehlgeschlagen"));
      const data = await response.json();
      if (!data.cancelled) $("setOutputDir").value = data.path;
    } catch (err) {
      showToast("Fehler: " + err.message, "error");
    }
  });

  $("btnSaveSettings").addEventListener("click", async () => {
    const btn = $("btnSaveSettings");
    btn.disabled = true;
    try {
      const response = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          output_dir: $("setOutputDir").value.trim(),
          open_pdf: $("setOpenPdf").checked,
          ai_enabled: $("setAiEnabled").checked,
        }),
      });
      if (!response.ok) throw new Error(await readError(response, "Speichern fehlgeschlagen"));
      applySettings(await response.json());
      showToast("Einstellungen gespeichert.", "success");
    } catch (err) {
      showToast("Fehler: " + err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  function escapeHtml(str) {
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  let toastTimer = null;
  function showToast(message, type = "info") {
    toast.textContent = message;
    toast.style.borderColor = type === "success" ? "#10b981" : type === "error" ? "#ef4444" : "#6366f1";
    toast.style.backgroundColor = type === "success" ? "#064e3b" : type === "error" ? "#7f1d1d" : "#1e1b4b";
    toast.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.add("hidden"), type === "error" ? 6000 : 3500);
  }
  if (new URLSearchParams(location.search).has("pending")) loadPending();
});
