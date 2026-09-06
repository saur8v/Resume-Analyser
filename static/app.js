const jdUrlInput = document.getElementById("jd-url");
const jdManualInput = document.getElementById("jd-manual");
const fetchJdBtn = document.getElementById("fetch-jd-btn");
const resumeFileInput = document.getElementById("resume-file");
const fileDropLabel = document.getElementById("file-drop-label");
const analyzeBtn = document.getElementById("analyze-btn");
const analyzeHint = document.getElementById("analyze-hint");
const toastContainer = document.getElementById("toast-container");

const jdDoc = document.getElementById("jd-doc");
const jdContent = document.getElementById("jd-content");
const analysisDoc = document.getElementById("analysis-doc");
const analysisContent = document.getElementById("analysis-content");
const emptyState = document.getElementById("empty-state");
const downloadPdfBtn = document.getElementById("download-pdf-btn");

let jobDescriptionText = "";
let analysisMarkdown = "";

function logStatus(message, kind = "") {
  const toast = document.createElement("div");
  toast.classList.add("toast");
  if (kind) toast.classList.add(`status-${kind}`);
  toast.textContent = message;
  toast.style.animationDuration = kind === "error" ? "5s" : "3.2s";
  toastContainer.appendChild(toast);

  // animationend removes it in the normal case; the timeout is a safety net
  // for prefers-reduced-motion, where the animation is disabled entirely.
  const duration = kind === "error" ? 5000 : 3200;
  toast.addEventListener("animationend", () => toast.remove(), { once: true });
  setTimeout(() => toast.remove(), duration + 200);
}

function updateAnalyzeAvailability() {
  const hasResume = resumeFileInput.files.length > 0;
  const hasJd = jobDescriptionText.trim().length > 0 || jdManualInput.value.trim().length > 0;
  analyzeBtn.disabled = !(hasResume && hasJd);
  analyzeHint.textContent = hasJd
    ? "Ready — upload a resume if you haven't yet."
    : "Fetch a job description first, or paste one in below.";
}

resumeFileInput.addEventListener("change", () => {
  fileDropLabel.textContent = resumeFileInput.files.length
    ? resumeFileInput.files[0].name
    : "Choose a file or drop it here";
  updateAnalyzeAvailability();
});

jdManualInput.addEventListener("input", updateAnalyzeAvailability);

fetchJdBtn.addEventListener("click", async () => {
  const url = jdUrlInput.value.trim();
  if (!url) {
    logStatus("Enter a job posting URL first.", "error");
    return;
  }

  fetchJdBtn.disabled = true;
  fetchJdBtn.textContent = "Fetching...";
  logStatus(`Fetching job description from ${url}`);

  try {
    const res = await fetch("/api/job-description", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || "Failed to fetch job description.");
    }

    jobDescriptionText = data.job_description;
    jdContent.innerHTML = marked.parse(jobDescriptionText);
    jdDoc.classList.remove("hidden");
    emptyState.classList.add("hidden");
    logStatus("Job description extracted.", "ok");
  } catch (err) {
    logStatus(err.message, "error");
  } finally {
    fetchJdBtn.disabled = false;
    fetchJdBtn.textContent = "Fetch job description";
    updateAnalyzeAvailability();
  }
});

analyzeBtn.addEventListener("click", async () => {
  const resumeFile = resumeFileInput.files[0];
  const jd = jobDescriptionText.trim() || jdManualInput.value.trim();

  if (!resumeFile || !jd) return;

  analyzeBtn.disabled = true;
  analyzeBtn.textContent = "Analyzing...";
  logStatus("Analyzing resume against job description...");

  try {
    const formData = new FormData();
    formData.append("resume", resumeFile);
    formData.append("job_description", jd);

    const res = await fetch("/api/analyze", {
      method: "POST",
      body: formData,
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || "Failed to analyze resume.");
    }

    // Keep the "Job Posting" page in sync with whatever JD was actually
    // used for the analysis, even if it came from the paste-in textarea
    // rather than the scraper.
    if (!jobDescriptionText.trim()) {
      jobDescriptionText = jd;
      jdContent.innerHTML = marked.parse(jobDescriptionText);
      jdDoc.classList.remove("hidden");
    }

    analysisMarkdown = data.improvements;
    analysisContent.innerHTML = marked.parse(analysisMarkdown);
    analysisDoc.classList.remove("hidden");
    emptyState.classList.add("hidden");
    downloadPdfBtn.classList.remove("hidden");
    logStatus("Analysis complete.", "ok");
  } catch (err) {
    logStatus(err.message, "error");
  } finally {
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analyze against job description";
    updateAnalyzeAvailability();
  }
});

downloadPdfBtn.addEventListener("click", async () => {
  downloadPdfBtn.disabled = true;
  downloadPdfBtn.textContent = "Preparing PDF...";
  logStatus("Building PDF summary...");

  try {
    const res = await fetch("/api/pdf-summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_description: jobDescriptionText,
        improvements: analysisMarkdown,
      }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Failed to generate PDF.");
    }

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "resume-analysis-summary.pdf";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);

    logStatus("PDF downloaded.", "ok");
  } catch (err) {
    logStatus(err.message, "error");
  } finally {
    downloadPdfBtn.disabled = false;
    downloadPdfBtn.textContent = "Download PDF summary";
  }
});
