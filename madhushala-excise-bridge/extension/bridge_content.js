(function () {
  if (window.__MADHUSHALA_EXTENSION_BRIDGE__) return;
  window.__MADHUSHALA_EXTENSION_BRIDGE__ = true;

  window.postMessage({source: "madhushala-extension", type: "READY"}, window.location.origin);

  window.addEventListener("message", async (event) => {
    if (event.source !== window) return;
    const message = event.data || {};
    if (message.source !== "madhushala-web" || !message.requestId) return;

    try {
      const response = await chrome.runtime.sendMessage(message);
      window.postMessage({
        source: "madhushala-extension",
        requestId: message.requestId,
        ok: true,
        response,
      }, window.location.origin);
    } catch (error) {
      window.postMessage({
        source: "madhushala-extension",
        requestId: message.requestId,
        ok: false,
        error: error.message || "Extension request failed",
      }, window.location.origin);
    }
  });
})();
