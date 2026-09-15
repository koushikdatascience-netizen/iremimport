(() => {
    if ("BarcodeDetector" in window) return;

    class ServerBackedBarcodeDetector {
        constructor(options = {}) {
            this.formats = Array.isArray(options.formats) ? options.formats : [];
        }

        static async getSupportedFormats() {
            return ["qr_code"];
        }

        async detect(source) {
            const width = Number(source?.displayWidth || source?.videoWidth || source?.naturalWidth || source?.width || 0);
            const height = Number(source?.displayHeight || source?.videoHeight || source?.naturalHeight || source?.height || 0);
            if (!width || !height) {
                throw new Error("QR image could not be prepared for scanning.");
            }

            const canvas = document.createElement("canvas");
            canvas.width = width;
            canvas.height = height;
            const context = canvas.getContext("2d", {willReadFrequently: false});
            if (!context) throw new Error("QR scanner could not create an image canvas.");
            context.drawImage(source, 0, 0, width, height);

            const blob = await new Promise((resolve, reject) => {
                canvas.toBlob((value) => {
                    if (value) resolve(value);
                    else reject(new Error("QR image could not be prepared for upload."));
                }, "image/png");
            });

            const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, "?"));
            const token = hashParams.get("session") || sessionStorage.getItem("exciseSession") || "";
            const basePath = window.location.pathname.startsWith("/excise-import/") ? "/excise-import" : "";
            const form = new FormData();
            form.append("file", blob, "qr-scan.png");

            const response = await fetch(`${basePath}/api/v1/document-import/qr/decode`, {
                method: "POST",
                headers: token ? {Authorization: `Bearer ${token}`} : {},
                body: form,
            });
            const text = await response.text();
            let payload = {};
            if (text) {
                try {
                    payload = JSON.parse(text);
                } catch {
                    payload = {detail: text};
                }
            }
            if (!response.ok) {
                throw new Error(payload.detail || payload.error || `QR scan failed (HTTP ${response.status})`);
            }

            const rawValue = String(payload.value || "").trim();
            if (!rawValue) return [];
            return [{rawValue, format: "qr_code", boundingBox: null, cornerPoints: []}];
        }
    }

    window.BarcodeDetector = ServerBackedBarcodeDetector;
})();
