(() => {
  "use strict";

  const MAX_CAPTION = Number(document.body.dataset.maxCaption) || 2200;
  const MAX_REELS = Number(document.body.dataset.maxReels) || 30;
  const DEFAULT_REELS = Number(document.body.dataset.defaultReels) || 12;
  const MAX_ATTEMPTS = Number(document.body.dataset.maxAttempts) || 5;

  const ACTIVE_STATUSES = new Set([
    "queued",
    "downloading",
    "uploading",
    "posting",
    "verifying",
  ]);

  const STATUS_LABELS = {
    queued: "En cola",
    downloading: "Descargando",
    uploading: "Subiendo a TikTok",
    posting: "Publicando",
    verifying: "Verificando",
    published: "Publicado",
    failed: "Error",
    interrupted: "Interrumpido",
  };

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const $ = (id) => document.getElementById(id);

  const ui = {
    igCard: $("igCard"),
    igBadge: $("igBadge"),
    igUser: $("igUser"),
    igMessage: $("igMessage"),
    connectIg: $("connectIg"),
    toggleIgCookie: $("toggleIgCookie"),
    igCookieForm: $("igCookieForm"),
    igSessionid: $("igSessionid"),
    igCsrf: $("igCsrf"),
    saveIgCookie: $("saveIgCookie"),
    tiktokCard: $("tiktokCard"),
    tiktokBadge: $("tiktokBadge"),
    tiktokUser: $("tiktokUser"),
    tiktokMessage: $("tiktokMessage"),
    connectTiktok: $("connectTiktok"),
    banners: $("banners"),
    reelsForm: $("reelsForm"),
    username: $("username"),
    limit: $("limit"),
    loadReels: $("loadReels"),
    reelsHint: $("reelsHint"),
    reelsGrid: $("reelsGrid"),
    reelsEmpty: $("reelsEmpty"),
    selectionBar: $("selectionBar"),
    selectionCount: $("selectionCount"),
    reviewButton: $("reviewButton"),
    reviewModal: $("reviewModal"),
    reviewList: $("reviewList"),
    rightsCheck: $("rightsCheck"),
    reviewError: $("reviewError"),
    publishButton: $("publishButton"),
    closeReview: $("closeReview"),
    cancelReview: $("cancelReview"),
    jobsSummary: $("jobsSummary"),
    jobsList: $("jobsList"),
    jobsEmpty: $("jobsEmpty"),
    toast: $("toast"),
  };

  const state = {
    status: null,
    reels: [],
    selected: new Set(),
    reviewItems: [],
    jobs: [],
    jobsSignature: "",
    bannersSignature: "",
    previousConnection: { instagram: null, tiktok: null },
    statusLoading: false,
    jobsLoading: false,
    statusFailed: false,
    jobsFailed: false,
    lastFocus: null,
    toastTimer: null,
    pollTimer: null,
    jobsTimer: null,
    busy: { instagram: false, tiktok: false, cookie: false, reels: false, publish: false },
  };

  class ApiError extends Error {
    constructor(message, code, status) {
      super(message);
      this.code = code;
      this.status = status;
    }
  }

  async function api(path, { method = "GET", payload, headers = {} } = {}) {
    const config = {
      method,
      headers: { Accept: "application/json", ...headers },
      cache: "no-store",
    };
    if (payload !== undefined) {
      config.headers["Content-Type"] = "application/json";
      config.body = JSON.stringify(payload);
    }
    let response;
    try {
      response = await fetch(path, config);
    } catch (error) {
      throw new ApiError(
        "No se pudo contactar con el servidor local. Comprueba que sigue en ejecución.",
        "network_error",
        0
      );
    }
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok) {
      const detail = data && data.error ? data.error : {};
      throw new ApiError(
        detail.message || `La operación falló (código ${response.status}).`,
        detail.code || "http_error",
        response.status
      );
    }
    return data;
  }

  function showToast(message, kind = "info") {
    if (!message) return;
    window.clearTimeout(state.toastTimer);
    ui.toast.textContent = message;
    ui.toast.className = `toast is-visible${kind === "error" ? " is-error" : ""}${
      kind === "success" ? " is-success" : ""
    }`;
    state.toastTimer = window.setTimeout(() => {
      ui.toast.classList.remove("is-visible");
    }, kind === "error" ? 7000 : 4500);
  }

  // ------------------------------------------------------------------
  // Fechas y duración
  // ------------------------------------------------------------------
  const dateFormatter = new Intl.DateTimeFormat("es-ES", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
  const timeFormatter = new Intl.DateTimeFormat("es-ES", {
    hour: "2-digit",
    minute: "2-digit",
  });

  function formatDate(value) {
    if (!value) return "Fecha desconocida";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Fecha desconocida";
    return `${dateFormatter.format(date)} · ${timeFormatter.format(date)}`;
  }

  function formatDuration(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds <= 0) return "Sin duración";
    const total = Math.round(seconds);
    const minutes = Math.floor(total / 60);
    const rest = String(total % 60).padStart(2, "0");
    return `${minutes}:${rest}`;
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("es-ES").format(value);
  }

  // ------------------------------------------------------------------
  // Estado de conexiones y banners
  // ------------------------------------------------------------------
  function setButtonBusy(button, busy, label) {
    button.disabled = busy;
    if (busy && label) button.textContent = label;
  }

  function renderConnections() {
    if (!state.status) return;
    renderConnection("instagram", state.status.instagram);
    renderConnection("tiktok", state.status.tiktok);
    renderBanners();
    announceConnectionChanges();
  }

  function renderConnection(platform, data) {
    const isInstagram = platform === "instagram";
    const nodes = isInstagram
      ? {
          card: ui.igCard,
          badge: ui.igBadge,
          user: ui.igUser,
          message: ui.igMessage,
          button: ui.connectIg,
        }
      : {
          card: ui.tiktokCard,
          badge: ui.tiktokBadge,
          user: ui.tiktokUser,
          message: ui.tiktokMessage,
          button: ui.connectTiktok,
        };

    const waiting = data.state === "waiting_login";
    const error = data.state === "error";
    const connected = Boolean(data.connected);
    const flag = waiting ? "waiting" : error ? "error" : connected ? "connected" : "offline";

    nodes.card.classList.remove("is-connected", "is-waiting", "is-error");
    if (flag === "waiting") nodes.card.classList.add("is-waiting");
    else if (flag === "error") nodes.card.classList.add("is-error");
    else if (flag === "connected") nodes.card.classList.add("is-connected");

    nodes.badge.className = "status-pill";
    if (flag === "waiting") {
      nodes.badge.classList.add("is-waiting");
      nodes.badge.textContent = "Esperando";
    } else if (flag === "error") {
      nodes.badge.classList.add("is-error");
      nodes.badge.textContent = "Error";
    } else if (flag === "connected") {
      nodes.badge.classList.add("is-online");
      nodes.badge.textContent = "Conectado";
    } else {
      nodes.badge.classList.add("is-offline");
      nodes.badge.textContent = "Sin conectar";
    }

    if (isInstagram) {
      if (connected && data.username) nodes.user.textContent = `@${data.username}`;
      else if (waiting) nodes.user.textContent = "Completa el inicio de sesión";
      else nodes.user.textContent = "Cuenta de origen";
    } else {
      nodes.user.textContent = connected
        ? "Sesión activa en TikTok Studio"
        : waiting
          ? "Completa el inicio de sesión"
          : "Cuenta de destino";
    }

    nodes.message.textContent =
      waiting || error ? data.message || "" : "";

    const busy = state.busy[platform];
    nodes.button.disabled = busy || waiting;
    if (busy) nodes.button.textContent = "Abriendo navegador…";
    else if (waiting) nodes.button.textContent = "Esperando inicio de sesión…";
    else nodes.button.textContent = connected ? "Reconectar" : isInstagram ? "Conectar Instagram" : "Conectar TikTok";
  }

  function announceConnectionChanges() {
    for (const platform of ["instagram", "tiktok"]) {
      const current = state.status[platform];
      const previous = state.previousConnection[platform];
      const name = platform === "instagram" ? "Instagram" : "TikTok";
      if (previous && previous.state === "waiting_login" && current.state === "connected") {
        showToast(`${name} conectado correctamente.`, "success");
      } else if (previous && previous.state === "waiting_login" && current.state === "error") {
        showToast(current.message || `No se pudo conectar ${name}.`, "error");
      }
      state.previousConnection[platform] = {
        state: current.state,
        connected: Boolean(current.connected),
      };
    }
  }

  function renderBanners() {
    const status = state.status;
    if (!status) return;
    const items = [];

    for (const platform of ["instagram", "tiktok"]) {
      const data = status[platform];
      const name = platform === "instagram" ? "Instagram" : "TikTok";
      if (data.state === "waiting_login") {
        items.push({
          kind: "info",
          spinner: true,
          title: `Esperando el inicio de sesión de ${name}`,
          text:
            data.message ||
            `Completa el acceso en la ventana de Chromium. La ventana se cerrará sola al detectar la sesión.`,
          action: null,
        });
      } else if (data.state === "error" && data.message) {
        items.push({
          kind: "danger",
          spinner: false,
          title: `No se pudo conectar ${name}`,
          text: data.message,
          action: { label: "Reintentar", handler: () => startConnect(platform) },
        });
      }
    }

    const worker = status.worker;
    if (worker && worker.paused) {
      const authPause = ["instagram_auth", "tiktok_auth"].includes(worker.pause_code);
      items.push({
        kind: "danger",
        spinner: false,
        title: "Cola de publicaciones pausada",
        text:
          worker.pause_message ||
          "Revisa el estado de las cuentas antes de continuar.",
        action: authPause
          ? null
          : { label: "Reanudar cola", handler: resumeWorker },
      });
    }

    const signature = JSON.stringify(
      items.map((item) => [item.kind, item.title, item.text, Boolean(item.action)])
    );
    if (signature === state.bannersSignature) return;
    state.bannersSignature = signature;

    ui.banners.replaceChildren();
    for (const item of items) {
      const banner = el("div", `banner banner-${item.kind}`);
      if (item.spinner) banner.appendChild(el("span", "spinner"));
      const body = el("div", "banner-body");
      body.appendChild(el("p", "banner-title", item.title));
      body.appendChild(el("p", "banner-text", item.text));
      banner.appendChild(body);
      if (item.action) {
        const button = el("button", "btn btn-secondary btn-small", item.action.label);
        button.type = "button";
        button.addEventListener("click", item.action.handler);
        banner.appendChild(button);
      }
      ui.banners.appendChild(banner);
    }
  }

  async function resumeWorker() {
    try {
      await api("/api/worker/resume", { method: "POST", payload: {} });
      showToast("Cola reanudada.", "success");
      await Promise.all([refreshStatus(), refreshJobs()]);
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function refreshStatus() {
    if (state.statusLoading) return state.status;
    state.statusLoading = true;
    try {
      const data = await api("/api/status");
      state.status = data;
      state.statusFailed = false;
      renderConnections();
      return data;
    } catch (error) {
      if (!state.statusFailed) {
        state.statusFailed = true;
        showToast(error.message, "error");
      }
      return state.status;
    } finally {
      state.statusLoading = false;
    }
  }

  async function startConnect(platform) {
    if (state.busy[platform]) return;
    state.busy[platform] = true;
    renderConnections();
    try {
      await api(`/api/connect/${platform}`, { method: "POST" });
      showToast(
        "Se abrió una ventana de Chromium. Completa el inicio de sesión allí; esta página se actualizará sola.",
        "info"
      );
      await refreshStatus();
    } catch (error) {
      showToast(error.message, "error");
      await refreshStatus();
    } finally {
      state.busy[platform] = false;
      renderConnections();
    }
  }

  async function saveInstagramCookie(event) {
    event.preventDefault();
    if (state.busy.cookie) return;
    const sessionid = ui.igSessionid.value.trim();
    const csrftoken = ui.igCsrf.value.trim();
    if (sessionid.length < 10) {
      showToast("La cookie sessionid parece demasiado corta.", "error");
      return;
    }
    state.busy.cookie = true;
    setButtonBusy(ui.saveIgCookie, true, "Validando…");
    try {
      const data = await api("/api/instagram/session", {
        method: "POST",
        payload: { sessionid, csrftoken: csrftoken || null },
      });
      ui.igSessionid.value = "";
      ui.igCsrf.value = "";
      ui.igCookieForm.classList.add("hidden");
      ui.toggleIgCookie.setAttribute("aria-expanded", "false");
      showToast(`Sesión de Instagram conectada como @${data.username}.`, "success");
      await refreshStatus();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      state.busy.cookie = false;
      setButtonBusy(ui.saveIgCookie, false);
      ui.saveIgCookie.textContent = "Guardar sesión";
    }
  }

  // ------------------------------------------------------------------
  // Reels
  // ------------------------------------------------------------------
  function setReelsEmpty(title, hint) {
    const titleNode = ui.reelsEmpty.querySelector("p:first-child");
    const hintNode = ui.reelsEmpty.querySelector(".hint");
    if (titleNode) titleNode.textContent = title;
    if (hintNode) hintNode.textContent = hint;
  }

  function showReelsEmpty(show) {
    ui.reelsEmpty.classList.toggle("hidden", !show);
  }

  function updateSelectionBar() {
    const count = state.selected.size;
    ui.selectionCount.textContent = `${count} ${count === 1 ? "seleccionado" : "seleccionados"}`;
    ui.selectionBar.classList.toggle("hidden", count === 0);
  }

  function renderReels() {
    ui.reelsGrid.replaceChildren();
    const fragment = document.createDocumentFragment();

    for (const reel of state.reels) {
      const published = Boolean(reel.already_published);
      const selected = state.selected.has(reel.shortcode);
      const card = el(
        "article",
        `reel-card${published ? " is-published" : ""}${selected ? " is-selected" : ""}`
      );

      const label = el("label", "reel-select");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selected;
      checkbox.disabled = published;
      checkbox.setAttribute(
        "aria-label",
        `${published ? "Ya publicado. " : ""}Seleccionar reel del ${formatDate(reel.taken_at)}`
      );
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) state.selected.add(reel.shortcode);
        else state.selected.delete(reel.shortcode);
        card.classList.toggle("is-selected", checkbox.checked);
        updateSelectionBar();
      });
      label.appendChild(checkbox);
      label.appendChild(el("span", "reel-check"));

      if (published) label.appendChild(el("span", "reel-flag", "Ya publicado"));

      const image = document.createElement("img");
      image.className = "reel-thumb";
      image.src = reel.thumbnail_url;
      image.alt = `Miniatura del reel del ${formatDate(reel.taken_at)}`;
      image.loading = "lazy";
      image.addEventListener("error", () => image.classList.add("is-broken"));
      label.appendChild(image);

      const bodyNode = el("div", "reel-body");
      const meta = el("div", "reel-meta");
      meta.appendChild(el("span", "", formatDate(reel.taken_at)));
      meta.appendChild(el("span", "", formatDuration(reel.duration)));
      bodyNode.appendChild(meta);

      const caption = el(
        "p",
        `reel-caption${reel.caption ? "" : " is-empty"}`,
        reel.caption || "Sin descripción en Instagram"
      );
      if (reel.caption) caption.title = reel.caption;
      bodyNode.appendChild(caption);
      label.appendChild(bodyNode);
      card.appendChild(label);
      fragment.appendChild(card);
    }

    ui.reelsGrid.appendChild(fragment);
    showReelsEmpty(state.reels.length === 0);
    updateSelectionBar();
  }

  async function loadReels(event) {
    event.preventDefault();
    if (state.busy.reels) return;

    const username = ui.username.value.trim().replace(/^@+/, "");
    const limit = Number(ui.limit.value);
    if (!username) {
      showToast("Introduce una cuenta de Instagram.", "error");
      ui.username.focus();
      return;
    }
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_REELS) {
      showToast(`La cantidad debe estar entre 1 y ${MAX_REELS}.`, "error");
      ui.limit.focus();
      return;
    }

    state.busy.reels = true;
    setButtonBusy(ui.loadReels, true, "Cargando reels…");
    ui.reelsHint.textContent = "Consultando Instagram. Esto puede tardar unos segundos…";
    try {
      const data = await api("/api/reels", {
        method: "POST",
        payload: { username, limit },
      });
      state.reels = data.reels || [];
      state.selected.clear();
      renderReels();
      const count = state.reels.length;
      if (count === 0) {
        setReelsEmpty(
          "No se encontraron reels en esa cuenta.",
          "Comprueba que la cuenta es pública y que tiene reels publicados."
        );
        ui.reelsHint.textContent = `Se consultó @${data.username}, pero no hay reels disponibles.`;
      } else {
        ui.reelsHint.textContent = `Se cargaron ${count} ${count === 1 ? "reel" : "reels"} de @${data.username}. Selecciona los que quieras publicar.`;
      }
    } catch (error) {
      ui.reelsHint.textContent =
        error.code === "instagram_cooldown"
          ? error.message
          : "Máximo 12 reels por carga recomendado. Instagram puede limitar solicitudes repetidas.";
      showToast(error.message, "error");
      if (state.reels.length === 0) {
        showReelsEmpty(true);
        setReelsEmpty(
          "No se pudieron cargar los reels.",
          "Revisa el mensaje de error e inténtalo de nuevo."
        );
      }
    } finally {
      state.busy.reels = false;
      setButtonBusy(ui.loadReels, false);
      ui.loadReels.textContent = "Cargar reels";
    }
  }

  // ------------------------------------------------------------------
  // Revisión y publicación
  // ------------------------------------------------------------------
  function openReview() {
    if (state.selected.size === 0) return;
    state.reviewItems = state.reels.filter((reel) =>
      state.selected.has(reel.shortcode)
    );
    if (state.reviewItems.length === 0) return;

    ui.reviewList.replaceChildren();
    const fragment = document.createDocumentFragment();

    for (const reel of state.reviewItems) {
      const item = el("div", "review-item");

      const image = document.createElement("img");
      image.className = "review-thumb";
      image.src = reel.thumbnail_url;
      image.alt = `Miniatura del reel del ${formatDate(reel.taken_at)}`;
      image.addEventListener("error", () => image.classList.add("is-broken"));
      item.appendChild(image);

      const fields = el("div", "review-fields");
      const topline = el("div", "review-topline");
      const name = el(
        "span",
        "review-name",
        `@${reel.username} · ${formatDate(reel.taken_at)}`
      );
      const counter = el("span", "review-counter", `0 / ${MAX_CAPTION}`);
      topline.appendChild(name);
      topline.appendChild(counter);
      fields.appendChild(topline);

      const textarea = document.createElement("textarea");
      textarea.className = "review-textarea";
      textarea.maxLength = MAX_CAPTION;
      textarea.value = reel.caption || "";
      textarea.placeholder = "Escribe la descripción para TikTok…";
      textarea.setAttribute(
        "aria-label",
        `Descripción para el reel del ${formatDate(reel.taken_at)}`
      );
      const refreshCounter = () => {
        counter.textContent = `${textarea.value.length} / ${MAX_CAPTION}`;
        counter.classList.toggle(
          "is-near",
          textarea.value.length > MAX_CAPTION * 0.9
        );
      };
      textarea.addEventListener("input", refreshCounter);
      refreshCounter();
      fields.appendChild(textarea);

      item.appendChild(fields);
      fragment.appendChild(item);
      reel.textarea = textarea;
    }

    ui.reviewList.appendChild(fragment);
    ui.rightsCheck.checked = false;
    ui.publishButton.disabled = true;
    ui.publishButton.textContent = "Publicar seleccionados";
    ui.reviewError.classList.add("hidden");
    ui.reviewError.textContent = "";

    state.lastFocus = document.activeElement;
    ui.reviewModal.classList.remove("hidden");
    document.body.classList.add("modal-open");
    const firstTextarea = ui.reviewList.querySelector("textarea");
    if (firstTextarea) firstTextarea.focus();
    else ui.closeReview.focus();
  }

  function closeReview() {
    ui.reviewModal.classList.add("hidden");
    document.body.classList.remove("modal-open");
    state.reviewItems = [];
    if (state.lastFocus && typeof state.lastFocus.focus === "function") {
      state.lastFocus.focus();
    }
    state.lastFocus = null;
  }

  function showReviewError(message) {
    ui.reviewError.textContent = message;
    ui.reviewError.classList.remove("hidden");
  }

  async function publishSelection() {
    if (state.busy.publish) return;
    if (!ui.rightsCheck.checked) {
      showReviewError("Debes confirmar que tienes derechos sobre el contenido.");
      return;
    }
    const items = state.reviewItems.map((reel) => ({
      shortcode: reel.shortcode,
      caption: (reel.textarea ? reel.textarea.value : reel.caption || "").slice(
        0,
        MAX_CAPTION
      ),
    }));
    if (items.length === 0) {
      showReviewError("No hay reels seleccionados.");
      return;
    }

    state.busy.publish = true;
    ui.reviewError.classList.add("hidden");
    setButtonBusy(ui.publishButton, true, "Enviando a la cola…");
    try {
      const data = await api("/api/publish", {
        method: "POST",
        payload: { items, confirm_rights: true },
      });
      const created = data.jobs ? data.jobs.length : 0;
      const skipped = data.skipped ? data.skipped.length : 0;
      closeReview();
      state.selected.clear();
      renderReels();
      showToast(
        skipped > 0
          ? `${created} ${created === 1 ? "reel añadido" : "reels añadidos"} a la cola; ${skipped} omitidos.`
          : `${created} ${created === 1 ? "reel añadido" : "reels añadidos"} a la cola de publicación.`,
        "success"
      );
      await Promise.all([refreshJobs(), refreshStatus()]);
    } catch (error) {
      showReviewError(error.message);
    } finally {
      state.busy.publish = false;
      setButtonBusy(ui.publishButton, false);
      ui.publishButton.disabled = !ui.rightsCheck.checked;
      ui.publishButton.textContent = "Publicar seleccionados";
    }
  }

  // ------------------------------------------------------------------
  // Cola e historial
  // ------------------------------------------------------------------
  function jobSignatures(jobs) {
    return JSON.stringify(
      jobs.map((job) => [
        job.id,
        job.status,
        job.message,
        job.error ? job.error.message : null,
        job.screenshot_url,
        job.post_clicked,
        job.attempts,
        job.updated_at,
      ])
    );
  }

  function renderJobsSummary() {
    const total = state.jobs.length;
    const active = state.jobs.filter((job) => ACTIVE_STATUSES.has(job.status)).length;
    const published = state.jobs.filter((job) => job.status === "published").length;
    const failed = state.jobs.filter(
      (job) => job.status === "failed" || job.status === "interrupted"
    ).length;

    ui.jobsSummary.replaceChildren();
    if (total === 0) return;
    for (const [label, value] of [
      ["Total", total],
      ["En curso", active],
      ["Publicados", published],
      ["Con error", failed],
    ]) {
      ui.jobsSummary.appendChild(
        el("span", "summary-chip", `${label}: ${formatNumber(value)}`)
      );
    }
  }

  function makeJobCard(job) {
    const card = el(
      "article",
      `job-card${ACTIVE_STATUSES.has(job.status) ? " is-active" : ""}${
        job.status === "failed" || job.status === "interrupted" ? " is-failed" : ""
      }`
    );

    const thumbWrap = el("div", "job-thumb-wrap");
    const thumb = document.createElement("img");
    thumb.className = "job-thumb";
    thumb.src = job.thumbnail_url;
    thumb.alt = "";
    thumb.loading = "lazy";
    thumb.addEventListener("error", () => thumb.classList.add("is-broken"));
    thumbWrap.appendChild(thumb);
    card.appendChild(thumbWrap);

    const main = el("div", "job-main");
    const topLine = el("div", "job-topline");
    topLine.appendChild(el("span", "job-id", `Trabajo #${job.id}`));
    topLine.appendChild(el("span", "job-date", formatDate(job.created_at)));
    main.appendChild(topLine);

    const caption = el(
      "p",
      "job-caption",
      job.caption || "Sin descripción"
    );
    main.appendChild(caption);

    const message = job.error && job.error.message ? job.error.message : job.message;
    if (message) main.appendChild(el("p", "job-message", message));

    if (job.screenshot_url) {
      const links = el("div", "job-links");
      const link = document.createElement("a");
      link.href = job.screenshot_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Ver captura del navegador";
      links.appendChild(link);
      main.appendChild(links);
    }
    card.appendChild(main);

    const side = el("div", "job-side");
    const label = STATUS_LABELS[job.status] || job.status;
    side.appendChild(el("span", `job-status status-${job.status}`, label));

    const retryable = job.status === "failed" || job.status === "interrupted";
    if (retryable) {
      if (job.attempts >= MAX_ATTEMPTS) {
        side.appendChild(el("span", "job-date", "Máximo de intentos"));
      } else {
        const retry = el("button", "btn btn-ghost btn-small", "Reintentar");
        retry.type = "button";
        retry.addEventListener("click", () => retryJob(job, retry));
        side.appendChild(retry);
      }
    }
    card.appendChild(side);
    return card;
  }

  function renderJobs() {
    const signature = jobSignatures(state.jobs);
    if (signature === state.jobsSignature) return;
    state.jobsSignature = signature;

    renderJobsSummary();
    ui.jobsList.replaceChildren();
    const hasJobs = state.jobs.length > 0;
    ui.jobsEmpty.classList.toggle("hidden", hasJobs);
    if (!hasJobs) return;

    const fragment = document.createDocumentFragment();
    for (const job of state.jobs) fragment.appendChild(makeJobCard(job));
    ui.jobsList.appendChild(fragment);
  }

  async function refreshJobs() {
    if (state.jobsLoading) return;
    state.jobsLoading = true;
    try {
      const data = await api("/api/jobs?limit=50");
      state.jobs = data.jobs || [];
      state.jobsFailed = false;
      renderJobs();
    } catch (error) {
      if (!state.jobsFailed && !state.statusFailed) {
        state.jobsFailed = true;
        showToast(error.message, "error");
      }
    } finally {
      state.jobsLoading = false;
    }
  }

  async function retryJob(job, button) {
    if (job.post_clicked) {
      const accepted = window.confirm(
        "Este intento llegó a pulsar Publicar y no se pudo confirmar el resultado.\n\n" +
          "Antes de reintentar, comprueba en tu cuenta de TikTok si el video ya está publicado. " +
          "Si continúas, podrías crear un duplicado.\n\n¿Quieres reintentar de todos modos?"
      );
      if (!accepted) return;
    }
    button.disabled = true;
    button.textContent = "Enviando…";
    try {
      await api(`/api/jobs/${job.id}/retry`, {
        method: "POST",
        payload: { acknowledge_duplicate_risk: Boolean(job.post_clicked) },
      });
      button.textContent = "En cola";
      showToast("Reintento añadido a la cola.", "success");
      await Promise.all([refreshJobs(), refreshStatus()]);
    } catch (error) {
      showToast(error.message, "error");
      button.disabled = false;
      button.textContent = "Reintentar";
    }
  }

  // ------------------------------------------------------------------
  // Eventos e inicio
  // ------------------------------------------------------------------
  function bindEvents() {
    ui.connectIg.addEventListener("click", () => startConnect("instagram"));
    ui.connectTiktok.addEventListener("click", () => startConnect("tiktok"));

    ui.toggleIgCookie.addEventListener("click", () => {
      const hidden = ui.igCookieForm.classList.toggle("hidden");
      ui.toggleIgCookie.setAttribute("aria-expanded", String(!hidden));
      if (!hidden) ui.igSessionid.focus();
    });
    ui.igCookieForm.addEventListener("submit", saveInstagramCookie);

    ui.reelsForm.addEventListener("submit", loadReels);
    ui.reviewButton.addEventListener("click", openReview);

    ui.rightsCheck.addEventListener("change", () => {
      ui.publishButton.disabled = !ui.rightsCheck.checked || state.busy.publish;
    });
    ui.publishButton.addEventListener("click", publishSelection);
    ui.closeReview.addEventListener("click", closeReview);
    ui.cancelReview.addEventListener("click", closeReview);
    ui.reviewModal.addEventListener("click", (event) => {
      if (event.target === ui.reviewModal) closeReview();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !ui.reviewModal.classList.contains("hidden")) {
        closeReview();
      }
    });

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refreshStatus();
        refreshJobs();
      }
    });
  }

  function init() {
    ui.limit.max = String(MAX_REELS);
    ui.limit.value = String(Math.min(DEFAULT_REELS, MAX_REELS));
    bindEvents();
    refreshStatus();
    refreshJobs();
    state.pollTimer = window.setInterval(() => {
      if (!document.hidden) refreshStatus();
    }, 2500);
    state.jobsTimer = window.setInterval(() => {
      if (!document.hidden) refreshJobs();
    }, 3000);
  }

  init();
})();
