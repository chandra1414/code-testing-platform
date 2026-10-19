(() => {
  const form = document.getElementById("exam-form");
  if (!form) return;

  const panes = [...document.querySelectorAll(".question-pane")];
  const buttons = [...document.querySelectorAll(".question-jump")];
  const saveStatus = document.getElementById("save-status");
  const csrfToken = form.querySelector("[name=csrfmiddlewaretoken]").value;
  const saveUrl = `${form.action.replace(/submit\/$/, "")}save/`;
  const marked = new Set();
  let current = 0;
  let timerExpired = false;
  const editors = new Map();
  const pendingTimers = new Map();

  if (window.CodeMirror) {
    document.querySelectorAll("textarea[data-code]").forEach((textarea) => {
      const editor = window.CodeMirror.fromTextArea(textarea, {
        mode: "python",
        lineNumbers: true,
        indentUnit: 4,
        tabSize: 4,
        lineWrapping: true,
      });
      editors.set(textarea, editor);
      editor.on("change", () => scheduleSave(textarea.closest(".question-pane")));
    });
  }

  panes.forEach((pane, index) => {
    const button = pane.querySelector(".review-toggle");
    if (button?.dataset.marked === "true") {
      marked.add(index);
      buttons[index]?.classList.add("review");
    }
  });

  function show(index) {
    current = Math.max(0, Math.min(index, panes.length - 1));
    panes.forEach((pane, paneIndex) => pane.classList.toggle("d-none", paneIndex !== current));
    buttons.forEach((button, buttonIndex) => button.classList.toggle("active", buttonIndex === current));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function answered(pane) {
    if (pane.querySelector("input[type=radio]")) return Boolean(pane.querySelector("input[type=radio]:checked"));
    const textarea = pane.querySelector("textarea[data-code]");
    const editor = textarea && editors.get(textarea);
    return Boolean((editor ? editor.getValue() : textarea?.value || "").trim());
  }

  function updateProgress() {
    let answeredCount = 0;
    panes.forEach((pane, index) => {
      const hasAnswer = answered(pane);
      if (hasAnswer) answeredCount += 1;
      buttons[index]?.classList.toggle("answered", hasAnswer);
      buttons[index]?.classList.toggle("review", marked.has(index));
    });
    document.getElementById("answered-count").textContent = answeredCount;
    document.getElementById("unanswered-count").textContent = panes.length - answeredCount;
    document.getElementById("review-count").textContent = marked.size;
  }

  async function save(pane, extra = {}) {
    const data = new FormData();
    data.set("csrfmiddlewaretoken", csrfToken);
    data.set("question_id", pane.dataset.questionId);
    Object.entries(extra).forEach(([key, value]) => data.set(key, value));
    const checked = pane.querySelector("input[type=radio]:checked");
    if (checked) data.set("option_id", checked.value);
    const textarea = pane.querySelector("textarea[data-code]");
    if (textarea) {
      const editor = editors.get(textarea);
      data.set("code", editor ? editor.getValue() : textarea.value);
    }
    try {
      saveStatus.textContent = "Saving…";
      const response = await fetch(saveUrl, {
        method: "POST",
        body: data,
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Could not save answer.");
      saveStatus.textContent = "All changes saved.";
      return body;
    } catch (error) {
      saveStatus.textContent = error.message;
      saveStatus.classList.add("text-danger");
      return null;
    }
  }

  function scheduleSave(pane) {
    if (!pane) return;
    window.clearTimeout(pendingTimers.get(pane));
    pendingTimers.set(pane, window.setTimeout(() => save(pane), 450));
    updateProgress();
  }

  buttons.forEach((button) => button.addEventListener("click", () => show(Number(button.dataset.index))));
  panes.forEach((pane, index) => {
    pane.querySelector(".previous-question")?.addEventListener("click", () => show(index - 1));
    pane.querySelector(".next-question")?.addEventListener("click", () => show(index + 1));
    pane.querySelectorAll("input[type=radio]").forEach((input) => input.addEventListener("change", () => {
      updateProgress();
      save(pane);
    }));
    pane.querySelector(".review-toggle")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const value = marked.has(index) ? "false" : "true";
      const result = await save(pane, { mark_for_review: value });
      if (!result) return;
      if (result.marked_for_review) marked.add(index);
      else marked.delete(index);
      button.dataset.marked = String(result.marked_for_review);
      button.textContent = result.marked_for_review ? "★ Marked for review" : "☆ Mark for review";
      updateProgress();
    });
    pane.querySelector(".run-code")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const textarea = pane.querySelector("textarea[data-code]");
      const editor = textarea && editors.get(textarea);
      const data = new FormData();
      data.set("csrfmiddlewaretoken", csrfToken);
      data.set("question_id", pane.dataset.questionId);
      data.set("code", editor ? editor.getValue() : textarea?.value || "");
      const output = pane.querySelector(".run-output");
      button.disabled = true;
      output.textContent = "Running sample…";
      output.classList.remove("d-none");
      try {
        const response = await fetch(button.dataset.url, {
          method: "POST",
          body: data,
          headers: { "X-Requested-With": "XMLHttpRequest" },
          credentials: "same-origin",
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "The sandbox could not run this code.");
        output.textContent = result.output || "(no output)";
      } catch (error) {
        output.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
  });
  updateProgress();

  const timer = document.getElementById("countdown");
  let seconds = Number(timer.dataset.seconds);
  function tick() {
    const minutes = Math.floor(Math.max(0, seconds) / 60);
    const remaining = Math.max(0, seconds) % 60;
    timer.textContent = `${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
    timer.parentElement.classList.toggle("urgent", seconds <= 60);
    if (seconds <= 0) {
      timerExpired = true;
      form.requestSubmit();
      return;
    }
    seconds -= 1;
    window.setTimeout(tick, 1000);
  }
  tick();

  form.addEventListener("submit", async (event) => {
    if (timerExpired) return;
    event.preventDefault();
    if (!window.confirm("Submit your assessment now? You cannot change your answers after submitting.")) return;
    document.getElementById("submit-test").disabled = true;
    await Promise.all(panes.map((pane) => {
      window.clearTimeout(pendingTimers.get(pane));
      return save(pane);
    }));
    form.submit();
  });
})();
