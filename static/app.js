(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const state = { uploadId: null, audioUrl: null, duration: 0, segments: [], active: 0, attempts: {} };
  const screens = ["upload", "processing", "review", "practice"];
  const audio = { review: $("#review-audio"), practice: $("#practice-audio") };

  function show(name) { screens.forEach((screen) => $("#" + screen + "-screen").classList.toggle("hidden", screen !== name)); }
  function format(ms) { const seconds = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`; }
  function clean(text) { return text.toLowerCase().replace(/[“”"'.,!?;:()[\]{}]/g, "").trim().split(/\s+/).filter(Boolean); }
  async function request(url, options = {}) { const response = await fetch(url, options); const data = response.status === 204 ? null : await response.json(); if (!response.ok) throw new Error(data.error || "请求失败，请重试。"); return data; }
  async function requestWithRetry(url, options) { let last; for (let attempt = 0; attempt < 3; attempt += 1) { try { return await request(url, options); } catch (error) { last = error; if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 700 * (attempt + 1))); } } throw last; }
  function setProcessing(message, progress) { $("#processing-message").textContent = message; $("#processing-progress").style.width = `${progress}%`; }

  function validateFile(file) {
    if (!file) return "请选择音频文件。";
    if (!/\.(mp3|wav|m4a)$/i.test(file.name)) return "仅支持 MP3、WAV、M4A 文件。";
    if (file.size > 500 * 1024 * 1024) return "文件超过 500MB 限制。";
    return null;
  }
  async function upload(file) {
    const error = validateFile(file); if (error) { $("#upload-message").textContent = error; return; }
    show("processing"); setProcessing("正在创建安全上传会话…", 2);
    try {
      const init = await request("/api/uploads/init", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename: file.name, size: file.size }) });
      state.uploadId = init.uploadId; const total = Math.ceil(file.size / init.chunkSize);
      for (let part = 0; part < total; part += 1) {
        setProcessing(`正在上传第 ${part + 1}/${total} 个分片…`, Math.round(5 + (part / total) * 55));
        await requestWithRetry(`/api/uploads/${state.uploadId}/parts`, { method: "POST", headers: { "X-Part-Number": part, "X-Total-Parts": total }, body: file.slice(part * init.chunkSize, Math.min(file.size, (part + 1) * init.chunkSize)) });
      }
      state.audioUrl = URL.createObjectURL(file); [audio.review, audio.practice].forEach((node) => node.src = state.audioUrl);
      setProcessing("上传完成，正在检查 30 分钟时长限制…", 62);
      await request("/api/transcriptions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ uploadId: state.uploadId, totalParts: total }) });
      poll();
    } catch (err) { show("upload"); $("#upload-message").textContent = err.message; }
  }
  async function poll() {
    try {
      const job = await request(`/api/transcriptions/${state.uploadId}`);
      setProcessing(job.message || "正在处理…", job.progress || 65);
      if (job.status === "ready") { state.duration = job.duration; state.segments = job.segments; renderReview(); show("review"); return; }
      if (job.status === "failed") throw new Error(job.message);
      window.setTimeout(poll, 1100);
    } catch (err) { show("upload"); $("#upload-message").textContent = `处理失败：${err.message}`; }
  }
  function listenAt(segment, target = audio.review) { target.currentTime = segment.startMs / 1000; target.play(); const stop = () => { if (target.currentTime >= segment.endMs / 1000) { target.pause(); target.removeEventListener("timeupdate", stop); } }; target.addEventListener("timeupdate", stop); }
  function renderReview() {
    $("#audio-meta").textContent = `${state.segments.length} 个句子 · ${format(state.duration)} · 拖动时间边界可微调`;
    const list = $("#segment-editor"); list.innerHTML = ""; const template = $("#segment-template");
    state.segments.forEach((segment, index) => {
      const node = template.content.firstElementChild.cloneNode(true); const text = $(".segment-text", node); const start = $(".start-range", node); const end = $(".end-range", node); const value = $(".range-value", node);
      $(".segment-meta", node).textContent = `第 ${index + 1} 句 · ${format(segment.startMs)} – ${format(segment.endMs)} · 置信度 ${Math.round(segment.confidence * 100)}%${segment.needsReview ? " · 建议检查" : ""}`;
      text.value = segment.text; start.max = end.max = state.duration; start.value = segment.startMs; end.value = segment.endMs;
      const sync = () => { segment.text = text.value; segment.startMs = Math.min(+start.value, +end.value - 100); segment.endMs = Math.max(+end.value, segment.startMs + 100); start.value = segment.startMs; end.value = segment.endMs; value.textContent = `${format(segment.startMs)} – ${format(segment.endMs)}`; };
      text.addEventListener("input", sync); start.addEventListener("input", sync); end.addEventListener("input", sync); sync();
      $(".listen", node).onclick = () => listenAt(segment); $(".split", node).onclick = () => { const middle = Math.round((segment.startMs + segment.endMs) / 2); const words = segment.text.split(/\s+/); state.segments.splice(index, 1, { ...segment, id: crypto.randomUUID(), endMs: middle, text: words.slice(0, Math.ceil(words.length / 2)).join(" "), needsReview: true }, { ...segment, id: crypto.randomUUID(), startMs: middle, text: words.slice(Math.ceil(words.length / 2)).join(" "), needsReview: true }); renderReview(); };
      $(".merge", node).disabled = index === 0; $(".merge", node).onclick = () => { if (!index) return; const prior = state.segments[index - 1]; prior.endMs = segment.endMs; prior.text = `${prior.text} ${segment.text}`; prior.needsReview = true; state.segments.splice(index, 1); renderReview(); };
      list.appendChild(node);
    });
  }
  async function saveSegments() { const saved = await request(`/api/transcriptions/${state.uploadId}/segments`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ segments: state.segments }) }); state.segments = saved.segments; renderReview(); }
  function renderPractice() {
    const segment = state.segments[state.active]; $("#practice-title").textContent = `第 ${state.active + 1} / ${state.segments.length} 句`;
    $("#review-flag").classList.toggle("hidden", !segment.needsReview); $("#dictation-input").value = state.attempts[segment.id]?.text || ""; $("#comparison").classList.add("hidden");
    const nav = $("#sentence-nav"); nav.innerHTML = ""; state.segments.forEach((item, i) => { const button = document.createElement("button"); button.className = i === state.active ? "active" : ""; button.innerHTML = `<small>${String(i + 1).padStart(2, "0")}</small><span>${state.attempts[item.id]?.done ? "已检查" : format(item.startMs)}</span>`; button.onclick = () => { state.active = i; renderPractice(); listenAt(item, audio.practice); }; nav.appendChild(button); });
  }
  function compare() {
    const segment = state.segments[state.active], typed = clean($("#dictation-input").value), answer = clean(segment.text); let html = "", correct = 0;
    const max = Math.max(typed.length, answer.length); for (let i = 0; i < max; i += 1) { if (typed[i] === answer[i]) { html += `<span class="good">${answer[i]} </span>`; correct += 1; } else if (typed[i]) html += `<span class="bad">${typed[i]} </span>`; else html += `<span class="missing">${answer[i]} </span>`; }
    const score = answer.length ? Math.round(correct / answer.length * 100) : 0; state.attempts[segment.id] = { text: $("#dictation-input").value, done: true, score }; const out = $("#comparison"); out.innerHTML = `<span class="answer-label">准确率 ${score}% · 参考答案</span>${html}`; out.classList.remove("hidden"); renderPractice();
  }
  const zone = $("#drop-zone"); $("#audio-file").addEventListener("change", (e) => upload(e.target.files[0])); ["dragenter", "dragover"].forEach((event) => zone.addEventListener(event, (e) => { e.preventDefault(); zone.classList.add("dragging"); })); ["dragleave", "drop"].forEach((event) => zone.addEventListener(event, (e) => { e.preventDefault(); zone.classList.remove("dragging"); })); zone.addEventListener("drop", (e) => upload(e.dataTransfer.files[0]));
  $("#save-slices").onclick = () => saveSegments().catch((e) => alert(e.message)); $("#start-practice").onclick = async () => { try { await saveSegments(); state.active = 0; renderPractice(); show("practice"); } catch (e) { alert(e.message); } }; $("#back-to-review").onclick = () => { renderReview(); show("review"); };
  $("#previous-sentence").onclick = () => { state.active = Math.max(0, state.active - 1); renderPractice(); listenAt(state.segments[state.active], audio.practice); }; $("#next-sentence").onclick = () => { state.active = Math.min(state.segments.length - 1, state.active + 1); renderPractice(); listenAt(state.segments[state.active], audio.practice); }; $("#repeat-sentence").onclick = () => listenAt(state.segments[state.active], audio.practice); $("#check-answer").onclick = compare; $("#show-answer").onclick = () => { $("#dictation-input").value = state.segments[state.active].text; compare(); };
  document.querySelectorAll("[data-speed]").forEach((button) => button.onclick = () => { document.querySelectorAll("[data-speed]").forEach((item) => item.classList.remove("selected")); button.classList.add("selected"); audio.practice.playbackRate = +button.dataset.speed; });
  window.addEventListener("beforeunload", () => { if (state.uploadId) fetch(`/api/transcriptions/${state.uploadId}`, { method: "DELETE", keepalive: true }); });
})();
