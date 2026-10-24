/**
 * XDROID Smart TV Activation — Clean Studio Interface Logic
 * Zero internal technical references (No cookie, vault, or proxy mentions)
 */

document.addEventListener("DOMContentLoaded", () => {
  // State
  let servicesData = [];
  let selectedService = null;
  let progressInterval = null;

  // DOM Elements
  const servicesGrid = document.getElementById("services-grid");
  const modalOverlay = document.getElementById("activation-modal");
  const modalCloseBtn = document.getElementById("modal-close-btn");
  
  // Modal Views
  const modalInputView = document.getElementById("modal-input-view");
  const modalProgressView = document.getElementById("modal-progress-view");
  const modalSuccessView = document.getElementById("modal-success-view");
  const modalErrorView = document.getElementById("modal-error-view");

  // Form & Inputs
  const activationForm = document.getElementById("activation-form");
  const tvCodeInput = document.getElementById("tv-code-input");
  const codeLengthHint = document.getElementById("code-length-hint");
  const modalAppLogo = document.getElementById("modal-app-logo");
  const modalServiceName = document.getElementById("modal-service-name");
  const modalServiceDesc = document.getElementById("modal-service-desc");
  const submitBtn = document.getElementById("submit-btn");
  const progressStatusText = document.getElementById("progress-status-text");

  // Result Elements
  const resService = document.getElementById("res-service");
  const resDevice = document.getElementById("res-device");
  const resPlan = document.getElementById("res-plan");
  const resCountry = document.getElementById("res-country");
  const errorMessageText = document.getElementById("error-message-text");
  const successDoneBtn = document.getElementById("success-done-btn");
  const errorRetryBtn = document.getElementById("error-retry-btn");

  // Official Real App Brand Logos (Direct from official brand assets)
  const officialLogos = {
    netflix: `<img src="/static/icons/netflix.svg" alt="Netflix" class="official-logo-img logo-netflix">`,
    primevideo: `<img src="/static/icons/primevideo.png" alt="Prime Video" class="official-logo-img logo-prime">`,
    crunchyroll: `<img src="/static/icons/crunchyroll.png" alt="Crunchyroll" class="official-logo-img logo-crunchyroll">`,
    hbomax: `<img src="/static/icons/hbomax.svg" alt="HBO Max" class="official-logo-img logo-hbomax">`,
    spotify: `<img src="/static/icons/spotify.svg" alt="Spotify" class="official-logo-img logo-spotify">`,
    surfshark: `<img src="/static/icons/surfshark.svg" alt="Surfshark" class="official-logo-img logo-surfshark">`,
    jiohotstar: `<img src="/static/icons/jiohotstar.png" alt="JioHotstar" class="official-logo-img logo-hotstar">`
  };

  // Toast Function
  function showToast(message, type = "info") {
    const toast = document.getElementById("toast");
    const toastMsg = document.getElementById("toast-msg");
    const toastIcon = document.getElementById("toast-icon");

    toastMsg.textContent = message;
    if (type === "error") {
      toastIcon.className = "fa-solid fa-circle-exclamation";
      toastIcon.style.color = "#dc2626";
    } else if (type === "success") {
      toastIcon.className = "fa-solid fa-circle-check";
      toastIcon.style.color = "#059669";
    } else {
      toastIcon.className = "fa-solid fa-circle-info";
      toastIcon.style.color = "#2563eb";
    }

    toast.classList.add("show");
    setTimeout(() => {
      toast.classList.remove("show");
    }, 3500);
  }

  // ========================================================
  // Body Scroll & Modal Sync Helper
  // ========================================================
  function syncBodyScroll() {
    const anyModal = document.querySelector(".modal-overlay.active");
    if (anyModal) {
      document.body.style.overflow = "hidden";
      document.documentElement.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
      document.documentElement.style.overflow = "";
    }
  }

  // ========================================================
  // Shrinkme.io Ad Monetization Engine (Free Access Handshake)
  // ========================================================
  window.freeServiceLogins = {};

  async function fetchAdPassStatus() {
    try {
      const token = localStorage.getItem("xdroid_ad_token") || "";
      const email = window.currentUserEmail || "";
      if (!token && !email) return;

      let res;
      try {
        res = await fetch(`/api/token/status?token=${encodeURIComponent(token)}&email=${encodeURIComponent(email)}`);
      } catch (_) {
        res = await fetch(`/api/ads/status?token=${encodeURIComponent(token)}&email=${encodeURIComponent(email)}`);
      }
      const data = await res.json();
      if (data.success && data.services) {
        window.freeServiceLogins = data.services;
        if (servicesData && servicesData.length > 0) {
          renderServicesGrid(servicesData);
        }
      }
    } catch (_) {}
  }

  // Access Denied Modal Elements
  const accessDeniedModal = document.getElementById("access-denied-modal");
  const accessDeniedCloseBtn = document.getElementById("access-denied-close-btn");
  const accessDeniedMsg = document.getElementById("access-denied-msg");
  const btnGenerateToken = document.getElementById("btn-generate-access-token");

  function openAccessDeniedModal(msg) {
    if (!accessDeniedModal) return;
    if (msg && accessDeniedMsg) {
      accessDeniedMsg.textContent = msg;
    }
    accessDeniedModal.classList.add("active");
    syncBodyScroll();
  }

  function closeAccessDeniedModal() {
    if (!accessDeniedModal) return;
    accessDeniedModal.classList.remove("active");
    syncBodyScroll();
  }

  if (accessDeniedCloseBtn) accessDeniedCloseBtn.addEventListener("click", closeAccessDeniedModal);
  if (accessDeniedModal) {
    accessDeniedModal.addEventListener("click", (e) => {
      if (e.target === accessDeniedModal) closeAccessDeniedModal();
    });
  }

  if (btnGenerateToken) {
    btnGenerateToken.addEventListener("click", () => triggerAdUnlockFlow(btnGenerateToken));
  }

  // Handle Return from Shrinkme Ad Gateway
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get("ad_unlocked") === "true") {
    const adTok = urlParams.get("token");
    if (adTok) {
      localStorage.setItem("xdroid_ad_token", adTok);
    }
    showToast("🎉 Access Token Activated! 1 Free Login unlocked for ALL platforms.", "success");
    window.history.replaceState({}, document.title, window.location.pathname);
    fetchAdPassStatus();
  } else if (urlParams.get("ad_error")) {
    showToast("Token generation incomplete. Please complete the ad gate to generate your token.", "error");
    window.history.replaceState({}, document.title, window.location.pathname);
  }

  // Trigger Shrinkme Ad Unlock Flow
  async function triggerAdUnlockFlow(triggerBtn) {
    let originalHtml = "";
    if (triggerBtn) {
      originalHtml = triggerBtn.innerHTML;
      triggerBtn.disabled = true;
      triggerBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> <span>Generating Access Token...</span>`;
    }
    try {
      let res;
      try {
        // Try /api/token/generate first (avoids adblocker /ads/ pattern match)
        res = await fetch("/api/token/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: window.currentUserEmail || null,
            chat_id: localStorage.getItem("xdroid_tg_chat_id") || null
          })
        });
      } catch (clientErr) {
        // Fallback to legacy endpoint if network intercepted
        res = await fetch("/api/ads/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: window.currentUserEmail || null,
            chat_id: localStorage.getItem("xdroid_tg_chat_id") || null
          })
        });
      }

      const data = await res.json();
      if (data.success && data.short_url) {
        if (data.token) {
          localStorage.setItem("xdroid_ad_token", data.token);
        }
        window.location.href = data.short_url;
      } else {
        showToast(data.error || "Ad gateway busy. Please try again in a moment.", "error");
        if (triggerBtn) {
          triggerBtn.disabled = false;
          triggerBtn.innerHTML = originalHtml;
        }
      }
    } catch (err) {
      console.error("[!] Ad unlock error:", err);
      showToast("Ad blocker or network issue detected. Please disable AdBlock / Brave Shields and retry.", "error");
      if (triggerBtn) {
        triggerBtn.disabled = false;
        triggerBtn.innerHTML = originalHtml;
      }
    }
  }

  // Load Services from API
  async function loadServices() {
    try {
      let res = await fetch("/api/services");
      if (!res.ok) {
        res = await fetch("/api/config");
      }
      const data = await res.json();

      if (data.status === "success" && Array.isArray(data.services)) {
        servicesData = data.services;
        renderServicesGrid(servicesData);
        // Refresh ad pass balances
        fetchAdPassStatus();
      } else {
        showToast("Service information unavailable", "error");
      }
    } catch (err) {
      console.error(err);
      servicesGrid.innerHTML = `
        <div style="grid-column: 1/-1; text-align: center; padding: 3rem 1rem; color: #64748b; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0;">
          <i class="fa-solid fa-circle-exclamation" style="font-size: 2.2rem; color: #dc2626; margin-bottom: 0.75rem;"></i>
          <h3 style="font-size: 1.15rem; color: #0f172a; margin-bottom: 0.25rem;">Unable to Load Services</h3>
          <p style="font-size: 0.9rem;">Please refresh the page or check back in a few moments.</p>
        </div>
      `;
    }
  }

  // Render Services Cards
  function renderServicesGrid(services) {
    servicesGrid.innerHTML = "";

    services.forEach(svc => {
      const card = document.createElement("div");
      card.className = "service-card";
      card.style.setProperty("--card-brand", svc.brand_color);

      const isReady = svc.is_available;
      const logoHtml = officialLogos[svc.id] || `<i class="fa-solid fa-tv" style="font-size: 1.8rem; color: ${svc.brand_color}"></i>`;

      card.innerHTML = `
        <div>
          <div class="card-top">
            <div class="app-icon-wrap">
              ${logoHtml}
            </div>
          </div>
          <div class="card-content">
            <h3>${svc.name}</h3>
            <div class="service-genre">${svc.category}</div>
            <p>${svc.description}</p>
          </div>
        </div>
        <div class="card-action">
          <span>Pair Device</span>
          <i class="fa-solid fa-arrow-right"></i>
        </div>
      `;

      card.addEventListener("click", () => {
        if (!isReady) {
          showToast(`${svc.name} gateway is currently busy. Please try again shortly.`, "error");
          return;
        }
        openActivationModal(svc);
      });

      servicesGrid.appendChild(card);
    });
  }

  // Mobile Hamburger Menu Toggle
  const navHamburgerBtn = document.getElementById("nav-hamburger-btn");
  const navLinks = document.getElementById("nav-links");

  if (navHamburgerBtn && navLinks) {
    navHamburgerBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const isOpen = navLinks.classList.toggle("show-mobile");
      const icon = navHamburgerBtn.querySelector("i");
      if (icon) {
        icon.className = isOpen ? "fa-solid fa-xmark" : "fa-solid fa-bars";
      }
    });

    const links = navLinks.querySelectorAll(".nav-link");
    links.forEach(l => {
      l.addEventListener("click", () => {
        navLinks.classList.remove("show-mobile");
        const icon = navHamburgerBtn.querySelector("i");
        if (icon) icon.className = "fa-solid fa-bars";
      });
    });

    document.addEventListener("click", (e) => {
      if (!navHamburgerBtn.contains(e.target) && !navLinks.contains(e.target)) {
        navLinks.classList.remove("show-mobile");
        const icon = navHamburgerBtn.querySelector("i");
        if (icon) icon.className = "fa-solid fa-bars";
      }
    });
  }

  // Open Modal for Service
  function openActivationModal(svc) {
    selectedService = svc;
    
    // Set UI Details
    modalServiceName.textContent = `Pair ${svc.name}`;
    modalServiceDesc.textContent = svc.description;
    
    const codeLabelText = document.getElementById("code-input-label-text");
    if (svc.id === "jiohotstar") {
      if (codeLabelText) codeLabelText.textContent = "TV Screen QR Link";
      codeLengthHint.textContent = "ACTIVATION URL / LINK";
      tvCodeInput.placeholder = "https://www.hotstar.com/activate/device?qr_code=...";
      tvCodeInput.maxLength = 255;
    } else {
      if (codeLabelText) codeLabelText.textContent = "TV Screen Code";
      codeLengthHint.textContent = `${svc.code_length} ${svc.code_type.toUpperCase()} DIGITS/CHARS`;
      tvCodeInput.placeholder = svc.placeholder;
      tvCodeInput.maxLength = svc.code_length;
    }
    tvCodeInput.value = "";

    // Set real app logo in modal
    modalAppLogo.innerHTML = officialLogos[svc.id] || "";

    // Reset Views
    switchModalView("input");

    modalOverlay.classList.add("active");
    syncBodyScroll();
    setTimeout(() => tvCodeInput.focus(), 150);
  }

  // Close Modal
  function closeActivationModal() {
    modalOverlay.classList.remove("active");
    if (progressInterval) {
      clearInterval(progressInterval);
      progressInterval = null;
    }
    syncBodyScroll();
  }

  // Switch Modal Views: "input" | "progress" | "success" | "error"
  function switchModalView(view) {
    modalInputView.style.display = view === "input" ? "block" : "none";
    modalProgressView.classList.toggle("active", view === "progress");
    modalSuccessView.classList.toggle("active", view === "success");
    modalErrorView.classList.toggle("active", view === "error");
  }

  // Handle Input Auto-formatting
  tvCodeInput.addEventListener("input", (e) => {
    if (!selectedService) return;

    if (selectedService.id === "jiohotstar") {
      // JioHotstar allows full activation link or code - preserve full URL
      return;
    }

    if (selectedService.code_type === "numeric") {
      // Numbers only for Netflix
      e.target.value = e.target.value.replace(/\D/g, "");
    } else {
      // Alphanumeric + Auto Uppercase
      e.target.value = e.target.value.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
    }
  });

  // ========================================================
  // Direct QR Scanner & Camera Engine
  // ========================================================
  const scannerModal = document.getElementById("camera-scanner-modal");
  const videoElem = document.getElementById("camera-stream-video");
  const btnScanTvQr = document.getElementById("btn-scan-tv-qr");
  const btnUploadDirectQr = document.getElementById("btn-upload-direct-qr");
  const directQrFileInput = document.getElementById("direct-qr-file-input");
  const cameraCloseBtn = document.getElementById("camera-modal-close-btn");
  const btnCameraCancel = document.getElementById("btn-camera-cancel");
  const btnUploadQrFile = document.getElementById("btn-upload-qr-file");
  const qrCameraInput = document.getElementById("qr-camera-input");
  const cameraStatusHint = document.getElementById("camera-status-hint");

  let cameraStream = null;
  let scanAnimationId = null;
  let isScannerActive = false;
  let scanThrottleTimer = 0;
  let qrOffscreenCanvas = null;
  let qrOffscreenCtx = null;

  // Process an image file from camera capture or upload
  async function handleQrImageFile(file) {
    if (!file) return;
    showToast("Analyzing TV screen QR code...", "info");

    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = (e) => resolve(e.target.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });

      const img = await new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = reject;
        image.src = dataUrl;
      });

      // Downscale to max 1000px dimension for optimal fast scanning
      let w = img.width;
      let h = img.height;
      const maxDim = 1000;
      if (w > maxDim || h > maxDim) {
        if (w > h) {
          h = Math.round((h * maxDim) / w);
          w = maxDim;
        } else {
          w = Math.round((w * maxDim) / h);
          h = maxDim;
        }
      }

      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, w, h);

      let qrData = null;

      // 1. Try local jsQR
      if (typeof jsQR !== "undefined") {
        try {
          const imgData = ctx.getImageData(0, 0, w, h);
          const res = jsQR(imgData.data, w, h, { inversionAttempts: "attemptBoth" });
          if (res && res.data) {
            qrData = res.data;
          }
        } catch (_) {}
      }

      // 2. If jsQR didn't catch it, fallback to server-side qrserver decoding
      if (!qrData) {
        try {
          const downscaledBase64 = canvas.toDataURL("image/jpeg", 0.85);
          const apiRes = await fetch("/api/scan-qr", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ image_base64: downscaledBase64 })
          });
          const apiData = await apiRes.json();
          if (apiData.success && apiData.qr_data) {
            qrData = apiData.qr_data;
          }
        } catch (_) {}
      }

      if (qrData) {
        closeLiveScanner();
        processExtractedQr(qrData);
      } else {
        showToast("No QR code detected. Please ensure the TV screen QR is clear and well lit.", "error");
      }
    } catch (err) {
      console.error("[!] Error analyzing QR image:", err);
      showToast("Could not process image. Please try again or type manually.", "error");
    }
  }

  // Hook up Direct QR file inputs
  if (directQrFileInput) {
    directQrFileInput.addEventListener("change", (e) => {
      const file = e.target.files && e.target.files[0];
      if (file) handleQrImageFile(file);
      directQrFileInput.value = "";
    });
  }

  if (qrCameraInput) {
    qrCameraInput.addEventListener("change", (e) => {
      const file = e.target.files && e.target.files[0];
      if (file) handleQrImageFile(file);
      qrCameraInput.value = "";
    });
  }

  // Upload QR Button triggers file picker
  if (btnUploadDirectQr && directQrFileInput) {
    btnUploadDirectQr.addEventListener("click", () => {
      directQrFileInput.removeAttribute("capture");
      directQrFileInput.click();
    });
  }

  // Scan QR (Camera) button
  if (btnScanTvQr) {
    btnScanTvQr.addEventListener("click", () => {
      // Prioritize live in-browser camera stream across both PC and Mobile
      const hasMediaDevices = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
      if (hasMediaDevices) {
        openLiveScanner();
      } else if (directQrFileInput) {
        directQrFileInput.setAttribute("capture", "environment");
        directQrFileInput.click();
      } else {
        openLiveScanner();
      }
    });
  }

  if (btnUploadQrFile && qrCameraInput) {
    btnUploadQrFile.addEventListener("click", () => {
      qrCameraInput.removeAttribute("capture");
      qrCameraInput.click();
    });
  }

  async function openLiveScanner() {
    if (!scannerModal || !videoElem) return;
    isScannerActive = true;
    scannerModal.classList.add("active");
    syncBodyScroll();

    // Push history state so mobile hardware/browser back safely dismisses the scanner
    try {
      if (!window.history.state || window.history.state.modal !== "camera-scanner") {
        window.history.pushState({ modal: "camera-scanner" }, "");
      }
    } catch (_) {}

    if (cameraStatusHint) {
      cameraStatusHint.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Initializing camera...`;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      if (cameraStatusHint) {
        cameraStatusHint.innerHTML = `<i class="fa-solid fa-camera"></i> Live webcam unavailable. Click 'Upload Photo' to select QR image.`;
      }
      return;
    }

    try {
      const constraints = {
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1280 },
          height: { ideal: 720 }
        }
      };

      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia(constraints);
      } catch (_) {
        stream = await navigator.mediaDevices.getUserMedia({ video: true });
      }

      // Check if user clicked cancel or navigated back while camera initialized
      if (!isScannerActive) {
        if (stream) stream.getTracks().forEach(t => t.stop());
        return;
      }

      cameraStream = stream;
      videoElem.srcObject = stream;
      videoElem.setAttribute("playsinline", "true");
      videoElem.setAttribute("muted", "true");

      try {
        await videoElem.play();
      } catch (playErr) {
        console.warn("Video play error:", playErr);
      }

      if (!isScannerActive) {
        closeLiveScanner();
        return;
      }

      if (cameraStatusHint) {
        cameraStatusHint.innerHTML = `<i class="fa-solid fa-camera-rotate"></i> Point camera at TV screen QR...`;
      }

      scanThrottleTimer = 0;
      scanVideoLoop();
    } catch (err) {
      console.warn("Camera getUserMedia not available:", err);
      if (cameraStatusHint) {
        cameraStatusHint.innerHTML = `<i class="fa-solid fa-triangle-exclamation" style="color: #ef4444;"></i> Camera access blocked. Click 'Upload Photo' to select QR image.`;
      }
    }
  }

  function closeLiveScanner(fromPopstate = false) {
    isScannerActive = false;
    if (scanAnimationId) {
      cancelAnimationFrame(scanAnimationId);
      scanAnimationId = null;
    }
    if (videoElem) {
      try { videoElem.pause(); } catch (_) {}
      videoElem.srcObject = null;
    }
    if (cameraStream) {
      cameraStream.getTracks().forEach(t => {
        try { t.stop(); } catch (_) {}
      });
      cameraStream = null;
    }
    if (scannerModal) {
      scannerModal.classList.remove("active");
    }
    syncBodyScroll();

    if (!fromPopstate && window.history.state && window.history.state.modal === "camera-scanner") {
      try {
        window.history.back();
      } catch (_) {}
    }
  }

  function scanVideoLoop() {
    if (!isScannerActive || !cameraStream) return;

    if (!videoElem || videoElem.readyState < 2 || videoElem.videoWidth === 0) {
      scanAnimationId = requestAnimationFrame(scanVideoLoop);
      return;
    }

    // Throttle scanning to 5 fps (~200ms) to ensure 0% CPU freeze
    const now = performance.now();
    if (now - scanThrottleTimer < 200) {
      scanAnimationId = requestAnimationFrame(scanVideoLoop);
      return;
    }
    scanThrottleTimer = now;

    try {
      const vw = videoElem.videoWidth;
      const vh = videoElem.videoHeight;
      const maxDim = 480;
      let dw = vw;
      let dh = vh;
      if (vw > maxDim || vh > maxDim) {
        if (vw > vh) {
          dh = Math.round((vh * maxDim) / vw);
          dw = maxDim;
        } else {
          dw = Math.round((vw * maxDim) / vh);
          dh = maxDim;
        }
      }

      if (!qrOffscreenCanvas) {
        qrOffscreenCanvas = document.createElement("canvas");
        qrOffscreenCtx = qrOffscreenCanvas.getContext("2d", { willReadFrequently: true });
      }
      if (qrOffscreenCanvas.width !== dw || qrOffscreenCanvas.height !== dh) {
        qrOffscreenCanvas.width = dw;
        qrOffscreenCanvas.height = dh;
      }

      qrOffscreenCtx.drawImage(videoElem, 0, 0, dw, dh);
      const imgData = qrOffscreenCtx.getImageData(0, 0, dw, dh);
      let found = null;

      if (typeof jsQR !== "undefined") {
        const code = jsQR(imgData.data, dw, dh, { inversionAttempts: "dontInvert" });
        if (code && code.data) {
          found = code.data;
        }
      }

      if (found) {
        closeLiveScanner();
        processExtractedQr(found);
        return;
      }
    } catch (e) {
      console.warn("Scan frame error:", e);
    }

    if (isScannerActive) {
      scanAnimationId = requestAnimationFrame(scanVideoLoop);
    }
  }

  if (cameraCloseBtn) cameraCloseBtn.addEventListener("click", () => closeLiveScanner());
  if (btnCameraCancel) btnCameraCancel.addEventListener("click", () => closeLiveScanner());
  if (scannerModal) {
    scannerModal.addEventListener("click", (e) => {
      if (e.target === scannerModal) closeLiveScanner();
    });
  }

  // Extract Code or URL from Scanned QR
  function processExtractedQr(rawQr) {
    if (!rawQr) {
      showToast("QR code was empty.", "error");
      return;
    }

    const cleanRaw = rawQr.trim();

    // Auto-detect service from QR if not currently matched
    const lower = cleanRaw.toLowerCase();
    if (lower.includes("hotstar.com") || lower.includes("jiohotstar") || lower.includes("qr_code=")) {
      if (!selectedService || selectedService.id !== "jiohotstar") {
        const jioSvc = availableServices.find(s => s.id === "jiohotstar");
        if (jioSvc) openActivationModal(jioSvc);
      }
    } else if (lower.includes("netflix.com") || lower.includes("/tv8") || lower.includes("nftv=")) {
      if (!selectedService || selectedService.id !== "netflix") {
        const nSvc = availableServices.find(s => s.id === "netflix");
        if (nSvc) openActivationModal(nSvc);
      }
    } else if (lower.includes("amazon.com") || lower.includes("primevideo.com") || lower.includes("/mytv")) {
      if (!selectedService || selectedService.id !== "primevideo") {
        const pSvc = availableServices.find(s => s.id === "primevideo");
        if (pSvc) openActivationModal(pSvc);
      }
    } else if (lower.includes("crunchyroll.com")) {
      if (!selectedService || selectedService.id !== "crunchyroll") {
        const cSvc = availableServices.find(s => s.id === "crunchyroll");
        if (cSvc) openActivationModal(cSvc);
      }
    } else if (lower.includes("spotify.com") || lower.startsWith("spotify:")) {
      if (!selectedService || selectedService.id !== "spotify") {
        const spSvc = availableServices.find(s => s.id === "spotify");
        if (spSvc) openActivationModal(spSvc);
      }
    } else if (lower.includes("max.com") || lower.includes("hbomax.com")) {
      if (!selectedService || selectedService.id !== "hbomax") {
        const hSvc = availableServices.find(s => s.id === "hbomax");
        if (hSvc) openActivationModal(hSvc);
      }
    }

    // 1. JioHotstar: Strictly uses full URL / QR Link (as in main.py)
    if (selectedService && selectedService.id === "jiohotstar") {
      let finalUrl = cleanRaw;
      if (!finalUrl.startsWith("http://") && !finalUrl.startsWith("https://")) {
        if (finalUrl.startsWith("www.") || finalUrl.includes("hotstar.com")) {
          finalUrl = "https://" + finalUrl.replace(/^https?:\/\//, "");
        } else if (finalUrl.includes("qr_code=")) {
          const m = finalUrl.match(/qr_code=([a-zA-Z0-9]+)/i);
          const codePart = m ? m[1] : finalUrl;
          finalUrl = `https://www.hotstar.com/activate/device?qr_code=${codePart}`;
        } else {
          const codePart = finalUrl.replace(/[^a-zA-Z0-9]/g, "");
          finalUrl = `https://www.hotstar.com/activate/device?qr_code=${codePart}`;
        }
      }
      tvCodeInput.value = finalUrl;
      showToast("JioHotstar QR Link Ready!", "success");
      return;
    }

    // 2. Netflix: 8-digit numeric code
    if (selectedService && selectedService.id === "netflix") {
      let netflixCode = "";
      try {
        if (cleanRaw.startsWith("http://") || cleanRaw.startsWith("https://")) {
          const urlObj = new URL(cleanRaw);
          netflixCode = urlObj.searchParams.get("code") ||
                        urlObj.searchParams.get("nftv") ||
                        urlObj.searchParams.get("pin") ||
                        urlObj.searchParams.get("c") || "";
          if (!netflixCode) {
            const m = urlObj.pathname.match(/\/(\d{8})/);
            if (m) netflixCode = m[1];
          }
        }
      } catch (_) {}

      if (!netflixCode) {
        const match8 = cleanRaw.match(/\b\d{8}\b/);
        if (match8) {
          netflixCode = match8[0];
        } else {
          const digits = cleanRaw.replace(/\D/g, "");
          if (digits.length >= 8) netflixCode = digits.substring(0, 8);
        }
      }

      if (netflixCode) {
        tvCodeInput.value = netflixCode;
        showToast(`Netflix Code Auto-filled: ${netflixCode}`, "success");
        return;
      }
    }

    // 3. Other Platforms (Prime, Crunchyroll, Spotify, HBO Max, Surfshark: 6 Alphanumeric)
    let extractedCode = "";
    try {
      if (cleanRaw.startsWith("http://") || cleanRaw.startsWith("https://")) {
        const urlObj = new URL(cleanRaw);
        extractedCode = urlObj.searchParams.get("code") ||
                        urlObj.searchParams.get("user_code") ||
                        urlObj.searchParams.get("linkingCode") ||
                        urlObj.searchParams.get("pin") ||
                        urlObj.searchParams.get("c") ||
                        urlObj.searchParams.get("tv_code") ||
                        urlObj.searchParams.get("qr_code") ||
                        urlObj.searchParams.get("activation_code") || "";
        if (!extractedCode) {
          const matchPath = urlObj.pathname.match(/(?:\/pair\/|\/activate\/|\/link\/|^\/)([a-zA-Z0-9]{6})(?:[/?#:]|$)/i);
          if (matchPath) {
            extractedCode = matchPath[1];
          } else {
            const parts = urlObj.pathname.split("/").filter(Boolean);
            const last = parts[parts.length - 1];
            if (last && last.length === 6 && /^[a-zA-Z0-9]+$/.test(last)) {
              extractedCode = last;
            }
          }
        }
      }
    } catch (_) {}

    if (!extractedCode) {
      const tokens = cleanRaw.match(/\b[A-Za-z0-9]{6}\b/g);
      if (tokens) {
        const ignored = ["HTTPS", "HTTP", "AMAZON", "SPOTIFY", "CRUNCH", "MAXCOM", "MYTV00"];
        const valid = tokens.find(t => !ignored.includes(t.toUpperCase()));
        if (valid) extractedCode = valid;
      }
    }

    if (extractedCode) {
      extractedCode = extractedCode.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
      if (selectedService && extractedCode.length > selectedService.code_length) {
        extractedCode = extractedCode.substring(0, selectedService.code_length);
      }
      tvCodeInput.value = extractedCode;
      showToast(`${selectedService ? selectedService.name : "TV"} Code Auto-filled: ${extractedCode}`, "success");
    } else {
      tvCodeInput.value = cleanRaw;
      showToast("QR Code Scanned!", "success");
    }
  }

  // Handle Form Submission
  activationForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!selectedService) return;

    const rawCode = tvCodeInput.value.trim();

    if (selectedService.id === "jiohotstar") {
      if (rawCode.length < 4) {
        showToast("Please enter or scan the JioHotstar QR activation link.", "error");
        tvCodeInput.focus();
        return;
      }
    } else {
      if (rawCode.length !== selectedService.code_length) {
        showToast(`Please enter the complete ${selectedService.code_length}-character code.`, "error");
        tvCodeInput.focus();
        return;
      }
    }

    // Switch to animated loading view
    switchModalView("progress");
    startProgressAnimation(selectedService.name);

    try {
      const response = await fetch("/api/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          service: selectedService.id,
          tv_code: rawCode,
          chat_id: localStorage.getItem("xdroid_tg_chat_id") || null,
          email: window.currentUserEmail || null,
          ad_token: localStorage.getItem("xdroid_ad_token") || null
        })
      });

      const data = await response.json();
      clearInterval(progressInterval);

      if (data.success) {
        // Success View
        resService.textContent = data.service_name || selectedService.name;
        resDevice.textContent = data.device || "Smart TV Screen";
        resPlan.textContent = data.plan || "Active Subscription";
        resCountry.textContent = data.country || "Global";
        switchModalView("success");
        fetchAdPassStatus();
      } else {
        if (data.need_ad) {
          closeActivationModal();
          openAccessDeniedModal(data.error);
        } else {
          // Error View
          errorMessageText.textContent = data.error || "Unable to authorize the provided TV screen code. Please verify the code on your television screen.";
          switchModalView("error");
          if (data.need_plan) {
            setTimeout(() => {
              closeActivationModal();
              openPaymentModal();
            }, 1800);
          }
        }
      }
    } catch (err) {
      clearInterval(progressInterval);
      errorMessageText.textContent = "Unable to connect to the authorization gateway. Please check your internet connection.";
      switchModalView("error");
    }
  });

  // Clean, professional progress messages
  function startProgressAnimation(serviceName) {
    const statuses = [
      `Connecting to ${serviceName} gateway...`,
      "Verifying device activation code...",
      "Negotiating device authorization...",
      "Registering Smart TV screen...",
      "Finalizing device pairing..."
    ];

    let index = 0;
    progressStatusText.textContent = statuses[0];

    if (progressInterval) clearInterval(progressInterval);

    progressInterval = setInterval(() => {
      index = (index + 1) % statuses.length;
      progressStatusText.textContent = statuses[index];
    }, 2200);
  }

  // Modal Event Listeners
  modalCloseBtn.addEventListener("click", closeActivationModal);
  successDoneBtn.addEventListener("click", closeActivationModal);
  errorRetryBtn.addEventListener("click", () => switchModalView("input"));

  modalOverlay.addEventListener("click", (e) => {
    if (e.target === modalOverlay) closeActivationModal();
  });

  // Policy Modal Handlers
  const policyModal = document.getElementById("policy-modal");
  const policyCloseBtn = document.getElementById("policy-close-btn");
  const openTermsBtn = document.getElementById("open-terms-btn");
  const openPrivacyBtn = document.getElementById("open-privacy-btn");
  const tabBtnTerms = document.getElementById("tab-btn-terms");
  const tabBtnPrivacy = document.getElementById("tab-btn-privacy");
  const termsContent = document.getElementById("terms-content");
  const privacyContent = document.getElementById("privacy-content");

  function openPolicy(tab = "terms") {
    if (!policyModal) return;
    switchPolicyTab(tab);
    policyModal.classList.add("active");
    syncBodyScroll();
  }

  function closePolicy() {
    if (!policyModal) return;
    policyModal.classList.remove("active");
    syncBodyScroll();
  }

  function switchPolicyTab(tab) {
    if (tab === "terms") {
      tabBtnTerms.classList.add("active");
      tabBtnPrivacy.classList.remove("active");
      termsContent.style.display = "block";
      privacyContent.style.display = "none";
    } else {
      tabBtnPrivacy.classList.add("active");
      tabBtnTerms.classList.remove("active");
      termsContent.style.display = "none";
      privacyContent.style.display = "block";
    }
  }

  if (openTermsBtn) openTermsBtn.addEventListener("click", () => openPolicy("terms"));
  if (openPrivacyBtn) openPrivacyBtn.addEventListener("click", () => openPolicy("privacy"));
  if (policyCloseBtn) policyCloseBtn.addEventListener("click", closePolicy);
  if (tabBtnTerms) tabBtnTerms.addEventListener("click", () => switchPolicyTab("terms"));
  if (tabBtnPrivacy) tabBtnPrivacy.addEventListener("click", () => switchPolicyTab("privacy"));

  if (policyModal) {
    policyModal.addEventListener("click", (e) => {
      if (e.target === policyModal) closePolicy();
    });
  }

  // ===================================================
  // Telegram Free Trial Verification Modal Logic
  // ===================================================
  const trialModal = document.getElementById("trial-modal");
  const trialCloseBtn = document.getElementById("trial-close-btn");
  const openTrialBtn = document.getElementById("open-trial-btn");
  const heroTrialBtn = document.getElementById("hero-trial-btn");
  const tgVerifyForm = document.getElementById("tg-verify-form");
  const tgChatIdInput = document.getElementById("tg-chat-id-input");
  const verifySubmitBtn = document.getElementById("verify-submit-btn");
  const verifyStatusBanner = document.getElementById("verify-status-banner");

  function openTrialModal() {
    if (!trialModal) return;
    const savedId = localStorage.getItem("xdroid_tg_chat_id");
    if (savedId && tgChatIdInput) tgChatIdInput.value = savedId;
    if (verifyStatusBanner) verifyStatusBanner.style.display = "none";
    trialModal.classList.add("active");
    syncBodyScroll();
  }

  function closeTrialModal() {
    if (!trialModal) return;
    trialModal.classList.remove("active");
    syncBodyScroll();
  }

  if (openTrialBtn) openTrialBtn.addEventListener("click", openTrialModal);
  if (heroTrialBtn) heroTrialBtn.addEventListener("click", openTrialModal);
  if (trialCloseBtn) trialCloseBtn.addEventListener("click", closeTrialModal);

  if (trialModal) {
    trialModal.addEventListener("click", (e) => {
      if (e.target === trialModal) closeTrialModal();
    });
  }

  if (tgVerifyForm) {
    tgVerifyForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const chatId = tgChatIdInput.value.trim();
      if (!chatId) return;

      verifySubmitBtn.disabled = true;
      verifySubmitBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> <span>Verifying Telegram Channels...</span>`;
      verifyStatusBanner.style.display = "none";

      try {
        const res = await fetch("/api/auth/verify-telegram", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            chat_id: chatId,
            email: window.currentUserEmail || null
          })
        });

        const data = await res.json();
        verifySubmitBtn.disabled = false;
        verifySubmitBtn.innerHTML = `<i class="fa-solid fa-shield-check"></i> <span>Verify Membership &amp; Claim Trial</span>`;

        if (data.success) {
          localStorage.setItem("xdroid_tg_chat_id", data.chat_id);
          if (data.already_claimed) {
            verifyStatusBanner.className = "verify-status-banner warning";
            verifyStatusBanner.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> <div><strong>Notice:</strong> ${data.message}</div>`;
            verifyStatusBanner.style.display = "flex";
            showToast("Free trial already claimed for this account.", "info");
            updateUserBadge(data.chat_id, data.remaining_logins);
            hideTrialOptions();
            setTimeout(() => {
              closeTrialModal();
            }, 2500);
          } else {
            verifyStatusBanner.className = "verify-status-banner success";
            verifyStatusBanner.innerHTML = `<i class="fa-solid fa-circle-check"></i> <div><strong>Success!</strong> ${data.message} (${data.remaining_logins} Logins Available)</div>`;
            verifyStatusBanner.style.display = "flex";

            showToast("Telegram Verified! Free trial activated.", "success");
            updateUserBadge(data.chat_id, data.remaining_logins);
            hideTrialOptions();

            setTimeout(() => {
              closeTrialModal();
            }, 2000);
          }
        } else {
          verifyStatusBanner.className = "verify-status-banner error";
          verifyStatusBanner.innerHTML = `<i class="fa-solid fa-circle-exclamation"></i> <div><strong>Verification Failed:</strong> ${data.message}</div>`;
          verifyStatusBanner.style.display = "flex";
          showToast(data.message, "error");
        }
      } catch (err) {
        verifySubmitBtn.disabled = false;
        verifySubmitBtn.innerHTML = `<i class="fa-solid fa-shield-check"></i> <span>Verify Membership &amp; Claim Trial</span>`;
        verifyStatusBanner.className = "verify-status-banner error";
        verifyStatusBanner.innerHTML = `<i class="fa-solid fa-circle-exclamation"></i> <div><strong>Connection Error:</strong> Could not connect to verification server.</div>`;
        verifyStatusBanner.style.display = "flex";
      }
    });
  }

  // ===================================================
  // Google Firebase Authentication & Auth Modal Logic
  // ===================================================
  const googleLoginBtn = document.getElementById("google-login-btn");
  const userProfileWrap = document.getElementById("user-profile-wrap");
  const userProfileBtn = document.getElementById("user-profile-btn");
  const userAvatar = document.getElementById("user-avatar");
  const userName = document.getElementById("user-name");
  const userEmailDisplay = document.getElementById("user-email-display");
  const userPlanTag = document.getElementById("user-plan-tag");
  const userDropdownCard = document.getElementById("user-dropdown-card");
  const openVerifyModalBtn = document.getElementById("open-verify-modal-btn");
  const logoutBtn = document.getElementById("logout-btn");

  // Auth Modal Elements
  const authModal = document.getElementById("auth-modal");
  const authCloseBtn = document.getElementById("auth-close-btn");
  const modalGoogleSigninBtn = document.getElementById("modal-google-signin-btn");

  window.currentUserEmail = null;

  function openAuthModal() {
    if (!authModal) return;
    authModal.classList.add("active");
    syncBodyScroll();
  }

  function closeAuthModal() {
    if (!authModal) return;
    authModal.classList.remove("active");
    syncBodyScroll();
  }

  if (authCloseBtn) authCloseBtn.addEventListener("click", closeAuthModal);
  if (authModal) {
    authModal.addEventListener("click", (e) => {
      if (e.target === authModal) closeAuthModal();
    });
  }

  function triggerGoogleSignIn() {
    if (!window.firebase || !firebase.auth) {
      showToast("Authentication service is loading. Please try again.", "info");
      return;
    }
    const provider = new firebase.auth.GoogleAuthProvider();
    firebase.auth().signInWithPopup(provider)
      .then(() => {
        closeAuthModal();
        showToast("Signed in successfully!", "success");
      })
      .catch((err) => {
        console.error("Google sign in error:", err);
        if (err.code !== "auth/popup-closed-by-user") {
          showToast("Sign in failed. Please try again.", "error");
        }
      });
  }

  if (modalGoogleSigninBtn) {
    modalGoogleSigninBtn.addEventListener("click", triggerGoogleSignIn);
  }

  if (googleLoginBtn) {
    googleLoginBtn.addEventListener("click", () => {
      openAuthModal();
    });
  }

  // Squircle Avatar Dropdown Toggle
  if (userProfileBtn && userDropdownCard) {
    userProfileBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      userDropdownCard.classList.toggle("show");
    });

    document.addEventListener("click", (e) => {
      if (!userProfileBtn.contains(e.target) && !userDropdownCard.contains(e.target)) {
        userDropdownCard.classList.remove("show");
      }
    });
  }

  if (openVerifyModalBtn) {
    openVerifyModalBtn.addEventListener("click", () => {
      if (userDropdownCard) userDropdownCard.classList.remove("show");
      openTrialModal();
    });
  }

  function hideTrialOptions() {
    if (openVerifyModalBtn) openVerifyModalBtn.style.display = "none";
    if (openTrialBtn) openTrialBtn.style.display = "none";
    if (heroTrialBtn) heroTrialBtn.style.display = "none";
    const authModalNotes = document.querySelectorAll(".auth-modal-note");
    authModalNotes.forEach(el => el.style.display = "none");
  }

  function updateUserBadge(chatIdOrEmail, logins) {
    if (userPlanTag) {
      userPlanTag.textContent = logins > 0 ? `${logins} Logins Remaining` : "0 Logins Remaining";
    }
  }

  async function initFirebaseAuth() {
    try {
      const configRes = await fetch("/api/config/firebase");
      const firebaseConfig = await configRes.json();

      if (!firebaseConfig.apiKey) return;
      if (!firebase.apps.length) {
        firebase.initializeApp(firebaseConfig);
      }

      firebase.auth().onAuthStateChanged(async (user) => {
        if (user) {
          window.currentUserEmail = user.email;
          if (googleLoginBtn) googleLoginBtn.style.display = "none";
          if (userProfileWrap) userProfileWrap.style.display = "block";
          if (userAvatar) userAvatar.src = user.photoURL || `https://ui-avatars.com/api/?name=${encodeURIComponent(user.displayName || "User")}&background=2563eb&color=fff`;
          if (userName) userName.textContent = user.displayName || user.email.split("@")[0];
          if (userEmailDisplay) userEmailDisplay.textContent = user.email;

          // Fetch user status
          const savedChatId = localStorage.getItem("xdroid_tg_chat_id");
          try {
            const statusRes = await fetch("/api/auth/user-status", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ email: user.email, chat_id: savedChatId })
            });
            const statusData = await statusRes.json();
            updateUserBadge(user.email, statusData.remaining_logins);
            if (statusData.free_service_logins) {
              window.freeServiceLogins = statusData.free_service_logins;
              if (servicesData && servicesData.length > 0) {
                renderServicesGrid(servicesData);
              }
            }

            // If user already claimed trial, hide all trial options immediately
            if (statusData.free_trial_claimed) {
              hideTrialOptions();
            }

            // Only prompt verification if user hasn't verified and hasn't claimed trial yet
            if (!statusData.is_verified && !statusData.free_trial_claimed) {
              setTimeout(() => {
                openTrialModal();
                showToast("Complete Telegram verification to claim 1-Day Free Trial!", "info");
              }, 600);
            }
          } catch (e) {
            if (!savedChatId) {
              setTimeout(() => {
                openTrialModal();
                showToast("Complete Telegram verification to claim 1-Day Free Trial!", "info");
              }, 600);
            }
          }
        } else {
          window.currentUserEmail = null;
          if (googleLoginBtn) googleLoginBtn.style.display = "flex";
          if (userProfileWrap) userProfileWrap.style.display = "none";
          if (userDropdownCard) userDropdownCard.classList.remove("show");
        }
      });

      if (logoutBtn) {
        logoutBtn.addEventListener("click", () => {
          firebase.auth().signOut().then(() => {
            if (userDropdownCard) userDropdownCard.classList.remove("show");
            showToast("Signed out successfully", "info");
          });
        });
      }
    } catch (err) {
      console.warn("Firebase Auth initialization skipped:", err);
    }
  }

  // ==========================================
  // Premium Plans & UPI QR Payment Engine
  // ==========================================
  const paymentModal = document.getElementById("payment-modal");
  const paymentCloseBtn = document.getElementById("payment-close-btn");
  const navBuyPremiumBtn = document.getElementById("nav-buy-premium-btn");
  const openPlansModalBtn = document.getElementById("open-plans-modal-btn");
  
  const planSelectionView = document.getElementById("plan-selection-view");
  const planQrView = document.getElementById("plan-qr-view");
  const planSuccessView = document.getElementById("plan-success-view");
  const btnBackToPlans = document.getElementById("btn-back-to-plans");
  
  const payQrImg = document.getElementById("pay-qr-img");
  const payOrderId = document.getElementById("pay-order-id");
  const payAmountDisplay = document.getElementById("pay-amount-display");
  const payUpiId = document.getElementById("pay-upi-id");
  const btnPayUpiMobile = document.getElementById("btn-pay-upi-mobile");
  const qrSelectedPlanName = document.getElementById("qr-selected-plan-name");
  
  const btnToggleUtr = document.getElementById("btn-toggle-utr");
  const utrSubmitForm = document.getElementById("utr-submit-form");
  const utrInput = document.getElementById("utr-input");
  const btnUtrSubmit = document.getElementById("btn-utr-submit");
  const utrToggleIcon = document.getElementById("utr-toggle-icon");
  
  const succPlanName = document.getElementById("succ-plan-name");
  const succLogins = document.getElementById("succ-logins");
  const succExpiry = document.getElementById("succ-expiry");
  const btnStartPairingNow = document.getElementById("btn-start-pairing-now");

  let activeOrderPoll = null;
  let currentActiveOrderId = null;

  // Dynamically load plans from server database
  async function loadPlans() {
    try {
      const res = await fetch("/api/payment/plans");
      const data = await res.json();
      if (data.status === "success" && data.plans) {
        Object.keys(data.plans).forEach(planKey => {
          const plan = data.plans[planKey];
          const card = document.querySelector(`.plan-card[data-plan="${planKey}"]`);
          if (card) {
            const priceEl = card.querySelector(".plan-price");
            const periodStr = planKey === "monthly" ? "/mo" : (planKey === "quarterly" ? "/3mo" : "/yr");
            if (priceEl) priceEl.innerHTML = `₹${Math.round(plan.amount)}<span class="plan-period">${periodStr}</span>`;
            const metaEl = card.querySelector(".plan-meta-tag");
            if (metaEl) metaEl.textContent = `${plan.logins} Activations • ${plan.days} Days`;
          }
        });
      }
    } catch (e) {
      console.warn("Could not load dynamic plan prices", e);
    }
  }

  function openPaymentModal() {
    if (!paymentModal) return;
    loadPlans();
    if (planSelectionView) planSelectionView.style.display = "block";
    if (planQrView) planQrView.style.display = "none";
    if (planSuccessView) planSuccessView.style.display = "none";
    paymentModal.classList.add("active");
    syncBodyScroll();
  }

  function closePaymentModal() {
    if (!paymentModal) return;
    if (activeOrderPoll) {
      clearInterval(activeOrderPoll);
      activeOrderPoll = null;
    }
    paymentModal.classList.remove("active");
    syncBodyScroll();
  }

  if (paymentCloseBtn) paymentCloseBtn.addEventListener("click", closePaymentModal);
  if (paymentModal) {
    paymentModal.addEventListener("click", (e) => {
      if (e.target === paymentModal) closePaymentModal();
    });
  }

  // Hero Quick-Action Button
  const heroPlansBtn = document.getElementById("hero-plans-btn");
  if (heroPlansBtn) {
    heroPlansBtn.addEventListener("click", () => {
      openPaymentModal();
    });
  }

  // Navbar Button (Open plans directly without demanding Google sign-in upfront)
  if (navBuyPremiumBtn) {
    navBuyPremiumBtn.addEventListener("click", () => {
      openPaymentModal();
    });
  }
  if (openPlansModalBtn) {
    openPlansModalBtn.addEventListener("click", () => {
      if (userDropdownCard) userDropdownCard.classList.remove("show");
      openPaymentModal();
    });
  }

  if (btnBackToPlans) {
    btnBackToPlans.addEventListener("click", () => {
      if (activeOrderPoll) clearInterval(activeOrderPoll);
      if (planQrView) planQrView.style.display = "none";
      if (planSelectionView) planSelectionView.style.display = "block";
    });
  }

  // Handle Plan Selection Click
  const planSelectButtons = document.querySelectorAll(".btn-select-plan");
  planSelectButtons.forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!window.currentUserEmail) {
        showToast("Please sign in with Google first.", "info");
        closePaymentModal();
        openAuthModal();
        return;
      }
      const planKey = btn.getAttribute("data-plan") || "monthly";
      await initiateOrder(planKey);
    });
  });

  async function initiateOrder(planKey) {
    if (!window.currentUserEmail) {
      showToast("Please sign in with Google first.", "info");
      closePaymentModal();
      openAuthModal();
      return;
    }

    const savedChatId = localStorage.getItem("xdroid_tg_chat_id");
    const email = window.currentUserEmail || null;

    showToast("Generating dynamic UPI QR code...", "info");

    try {
      const res = await fetch("/api/payment/create-order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plan_key: planKey,
          chat_id: savedChatId,
          email: email
        })
      });
      const data = await res.json();

      if (!data.success) {
        showToast("Unable to initialize order. Please try again.", "error");
        return;
      }

      currentActiveOrderId = data.order_id;
      if (qrSelectedPlanName) qrSelectedPlanName.textContent = `${data.plan_name} Plan • ₹${data.amount.toFixed(2)} (${data.logins} Logins)`;
      if (payQrImg) payQrImg.src = data.qr_data_url;
      if (payOrderId) payOrderId.innerHTML = `${data.order_id} <i class="fa-regular fa-copy"></i>`;
      if (payAmountDisplay) payAmountDisplay.textContent = `₹${data.amount.toFixed(2)}`;
      if (payUpiId) payUpiId.innerHTML = `${data.upi_id} <i class="fa-regular fa-copy"></i>`;
      if (btnPayUpiMobile) btnPayUpiMobile.href = data.upi_uri;

      // Switch view
      if (planSelectionView) planSelectionView.style.display = "none";
      if (planQrView) planQrView.style.display = "block";
      if (planSuccessView) planSuccessView.style.display = "none";

      // Start automatic polling loop (checks every 4 seconds)
      startAutoPaymentPolling(data.order_id);
    } catch (err) {
      console.error(err);
      showToast("Order generation failed. Please check connection.", "error");
    }
  }

  function startAutoPaymentPolling(orderId) {
    if (activeOrderPoll) clearInterval(activeOrderPoll);

    activeOrderPoll = setInterval(async () => {
      try {
        const res = await fetch("/api/payment/check-status", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ order_id: orderId })
        });
        const data = await res.json();

        if (data.verified) {
          clearInterval(activeOrderPoll);
          handlePaymentSuccess(data);
        }
      } catch (err) {
        console.warn("Auto-check poll error:", err);
      }
    }, 4000);
  }

  function handlePaymentSuccess(data) {
    if (activeOrderPoll) clearInterval(activeOrderPoll);
    showToast("Payment Verified! Premium Activated 🚀", "success");

    if (succPlanName) succPlanName.textContent = (data.plan || "Premium").toUpperCase();
    if (succLogins) succLogins.textContent = `${data.remaining_logins || 10} Logins Available`;
    if (succExpiry) succExpiry.textContent = data.expires_at || "30 Days";

    updateUserBadge(localStorage.getItem("xdroid_tg_chat_id"), data.remaining_logins);

    if (planSelectionView) planSelectionView.style.display = "none";
    if (planQrView) planQrView.style.display = "none";
    if (planSuccessView) planSuccessView.style.display = "block";
  }

  // Toggle UTR Input
  if (btnToggleUtr && utrSubmitForm) {
    btnToggleUtr.addEventListener("click", () => {
      const isHidden = utrSubmitForm.style.display === "none";
      utrSubmitForm.style.display = isHidden ? "block" : "none";
      if (utrToggleIcon) {
        utrToggleIcon.className = isHidden ? "fa-solid fa-chevron-up" : "fa-solid fa-chevron-down";
      }
    });
  }

  // Submit Manual UTR
  if (utrSubmitForm && utrInput) {
    utrSubmitForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const utr = utrInput.value.trim();
      if (utr.length < 6) {
        showToast("Please enter a valid UPI Reference / UTR Number.", "error");
        return;
      }

      if (btnUtrSubmit) {
        btnUtrSubmit.disabled = true;
        btnUtrSubmit.textContent = "Verifying...";
      }

      try {
        const res = await fetch("/api/payment/verify-utr", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ order_id: currentActiveOrderId, utr: utr })
        });
        const data = await res.json();
        if (btnUtrSubmit) {
          btnUtrSubmit.disabled = false;
          btnUtrSubmit.textContent = "Verify";
        }

        if (data.verified) {
          handlePaymentSuccess(data);
        } else {
          showToast(data.error || "Payment not verified yet. Please check UTR.", "error");
        }
      } catch (err) {
        if (btnUtrSubmit) {
          btnUtrSubmit.disabled = false;
          btnUtrSubmit.textContent = "Verify";
        }
        showToast("Verification request failed. Please try again.", "error");
      }
    });
  }

  // Copyable Order ID and UPI ID
  if (payOrderId) {
    payOrderId.addEventListener("click", () => {
      if (currentActiveOrderId) {
        navigator.clipboard.writeText(currentActiveOrderId);
        showToast("Order ID copied to clipboard!", "success");
      }
    });
  }

  if (payUpiId) {
    payUpiId.addEventListener("click", () => {
      const upiText = payUpiId.textContent.trim().replace(/\s.*$/, "");
      navigator.clipboard.writeText(upiText);
      showToast("UPI ID copied to clipboard!", "success");
    });
  }

  if (btnStartPairingNow) {
    btnStartPairingNow.addEventListener("click", () => {
      closePaymentModal();
      window.scrollTo({ top: document.getElementById("platforms").offsetTop - 80, behavior: "smooth" });
    });
  }

  // Browser Back Button & Hardware Back Gesture Listener (popstate)
  window.addEventListener("popstate", () => {
    if (scannerModal && scannerModal.classList.contains("active")) {
      closeLiveScanner(true);
    } else if (modalOverlay && modalOverlay.classList.contains("active")) {
      closeActivationModal();
    } else if (paymentModal && paymentModal.classList.contains("active")) {
      closePaymentModal();
    } else if (accessDeniedModal && accessDeniedModal.classList.contains("active")) {
      closeAccessDeniedModal();
    } else if (authModal && authModal.classList.contains("active")) {
      closeAuthModal();
    } else if (trialModal && trialModal.classList.contains("active")) {
      closeTrialModal();
    } else if (policyModal && policyModal.classList.contains("active")) {
      closePolicy();
    }
    syncBodyScroll();
  });

  // Keyboard shortcut (Escape closes active modal)
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (scannerModal && scannerModal.classList.contains("active")) closeLiveScanner();
      if (paymentModal && paymentModal.classList.contains("active")) closePaymentModal();
      if (authModal && authModal.classList.contains("active")) closeAuthModal();
      if (trialModal && trialModal.classList.contains("active")) closeTrialModal();
      if (policyModal && policyModal.classList.contains("active")) closePolicy();
      if (accessDeniedModal && accessDeniedModal.classList.contains("active")) closeAccessDeniedModal();
      if (modalOverlay && modalOverlay.classList.contains("active")) closeActivationModal();
      if (userDropdownCard) userDropdownCard.classList.remove("show");
    }
  });

  // Check saved trial on launch
  const initialChatId = localStorage.getItem("xdroid_tg_chat_id");
  if (initialChatId) {
    fetch("/api/auth/user-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: initialChatId })
    }).then(r => r.json()).then(data => {
      if (data.is_verified) {
        updateUserBadge(initialChatId, data.remaining_logins);
      }
      if (data.free_trial_claimed) {
        hideTrialOptions();
      }
    }).catch(() => {});
  }

  // Initialize
  loadServices();
  loadPlans();
  initFirebaseAuth();
});

