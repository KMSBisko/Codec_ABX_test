/* ═══════════════════════════════════════════════════════════════
   Codec ABX Tester — Web Edition JavaScript
   Handles all UI interactions and API communication
   ═══════════════════════════════════════════════════════════════ */

// ── State ────────────────────────────────────────────────────────

const state = {
    catalog: {},
    stagesA: [],
    stagesB: [],
    currentTrial: 0,
    totalTrials: 0,
    correctTrials: 0,
    audioDuration: 0,
    audioContext: null,
    // Pre-loaded AudioBuffers (loaded once after preprocessing)
    bufferA: null,
    bufferB: null,
    isPlaying: false,
    isPaused: false,
    // Current active source node and gain nodes for crossfade switching
    currentSource: null,
    gainNode: null,       // master gain for the playing source
    fadeOutGain: null,    // temporary gain for fading out old source
    // Shared timeline state
    playbackStartTime: 0,   // audioContext.currentTime when playback started
    playbackOffset: 0,      // start position within the buffer (seconds)
    pausePosition: 0,       // position where playback was paused
    currentSide: "a",       // currently playing side: 'a', 'b', or 'x'
    loopActive: false,
    // Polling for preprocess status
    pollInterval: null,
    // Track which side X maps to for the current trial
    xIsA: true,
    // Auto-advance: keep audio playing after answer and auto-start next trial
    autoAdvanceEnabled: true,
    // Scrub state tracking — prevents animation from fighting user drag
    isScrubbing: false,
    // Guard flag: when true, old source onended should NOT call stopPlayback()
    switchingSource: false,
};

// ── i18n Translations ────────────────────────────────────────────

const translations = {
    en: {
        input_title: "Input & Configuration",
        audio_file_label: "Audio File (WAV / FLAC)",
        drop_file_placeholder: "Drop file or click to browse",
        sample_rate_mode_label: "Sample Rate Mode",
        label_mapping_label: "Label Mapping Mode",
        codec_config_title: "Codec Configuration",
        side_a_label: "Side A Pipeline",
        side_b_label: "Side B Pipeline",
        add_stage_btn: "+ Add Stage",
        bw_limit_label: "Bandwidth Limit (Low-Pass)",
        hz_label: "Hz",
        preprocess_btn: "▶ Preprocess & Validate",
        cancel_btn: "✕ Cancel",
        validation_title: "Validation Results",
        abx_title: "ABX Playback",
        trial_counter: (t, c) => `Trial: ${t}`,
        score_display: (c, t) => `Score: ${c}/${t} correct`,
        play_a_btn: "▶ Play A",
        play_x_btn: "▶ Play X",
        play_b_btn: "▶ Play B",
        loop_label: "Loop:",
        answer_a_btn: "X is A",
        answer_b_btn: "X is B",
        next_trial_btn: "Next Trial →",
        reset_trials_btn: "↺ Reset Trials",
        statistics_title: "Statistics",
        total_trials_label: "Total Trials:",
        correct_label: "Correct:",
        p_value_label: "One-tailed p-value:",
        export_json_btn: "Export JSON",
        export_csv_btn: "Export CSV",
        diagnostics_title: "Diagnostics",
        refresh_diag_btn: "↺ Refresh",
    },
    vi: {
        input_title: "Đầu vào & Cấu hình",
        audio_file_label: "Tệp âm thanh (WAV / FLAC)",
        drop_file_placeholder: "Kéo thả tệp hoặc nhấn để chọn",
        sample_rate_mode_label: "Chế độ tần số mẫu",
        label_mapping_label: "Chế độ gán nhãn",
        codec_config_title: "Cấu hình Codec",
        side_a_label: "Đường ống Bên A",
        side_b_label: "Đường ống Bên B",
        add_stage_btn: "+ Thêm Giai đoạn",
        bw_limit_label: "Giới hạn băng thông (Low-Pass)",
        hz_label: "Hz",
        preprocess_btn: "▶ Tiền xử lý & Kiểm tra",
        cancel_btn: "✕ Hủy",
        validation_title: "Kết quả kiểm tra",
        abx_title: "Phát ABX",
        trial_counter: (t, c) => `Lần thử: ${t}`,
        score_display: (c, t) => `Điểm: ${c}/${t} đúng`,
        play_a_btn: "▶ Phát A",
        play_x_btn: "▶ Phát X",
        play_b_btn: "▶ Phát B",
        loop_label: "Lặp:",
        answer_a_btn: "X là A",
        answer_b_btn: "X là B",
        next_trial_btn: "Lần thử tiếp →",
        reset_trials_btn: "↺ Đặt lại lần thử",
        statistics_title: "Thống kê",
        total_trials_label: "Tổng số lần thử:",
        correct_label: "Đúng:",
        p_value_label: "Giá trị p một phía:",
        export_json_btn: "Xuất JSON",
        export_csv_btn: "Xuất CSV",
        diagnostics_title: "Chẩn đoán",
        refresh_diag_btn: "↺ Làm mới",
    },
};

let currentLang = "en";

function t(key, ...args) {
    const lang = translations[currentLang] || translations.en;
    const val = lang[key];
    if (typeof val === "function") return val(...args);
    return val || translations.en[key] || key;
}

function applyTranslations() {
    document.querySelectorAll("[data-i18n]").forEach(el => {
        const key = el.getAttribute("data-i18n");
        const translated = t(key);
        if (el.tagName === "OPTION") {
            el.textContent = translated[0] || translated;
        } else {
            el.textContent = translated;
        }
    });
}

// ── Theme Management ─────────────────────────────────────────────

const themeOrder = ["theme-light", "theme-dark", "theme-oled"];
let currentThemeIdx = 0;

function cycleTheme() {
    document.body.classList.remove(themeOrder[currentThemeIdx]);
    currentThemeIdx = (currentThemeIdx + 1) % themeOrder.length;
    document.body.classList.add(themeOrder[currentThemeIdx]);
    localStorage.setItem("abx_theme", currentThemeIdx);
}

// ── Zoom Management ──────────────────────────────────────────────

const zoomLevels = [75, 100, 125, 150];
let currentZoomIdx = 1; // Start at 100%

function setZoom(delta) {
    currentZoomIdx = Math.max(0, Math.min(zoomLevels.length - 1, currentZoomIdx + delta));
    const zoom = zoomLevels[currentZoomIdx];
    document.getElementById("mainContent").style.fontSize = `${zoom / 100}rem`;
    document.getElementById("zoomLevel").textContent = `${zoom}%`;
    localStorage.setItem("abx_zoom", currentZoomIdx);
}

// ── API Helper ───────────────────────────────────────────────────

async function api(url, options = {}) {
    const resp = await fetch(url, {
        headers: { "Accept": "application/json" },
        ...options,
    });
    if (resp.ok) return resp.json();
    const err = await resp.json().catch(() => ({ error: resp.statusText }));
    throw new Error(err.error || `HTTP ${resp.status}`);
}

// ── Initialization ───────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
    // Restore preferences
    const savedTheme = localStorage.getItem("abx_theme");
    if (savedTheme !== null) {
        currentThemeIdx = parseInt(savedTheme);
        document.body.classList.remove(themeOrder[0]);
        document.body.classList.add(themeOrder[currentThemeIdx]);
    }

    const savedZoom = localStorage.getItem("abx_zoom");
    if (savedZoom !== null) {
        currentZoomIdx = parseInt(savedZoom);
        setZoom(0);
    }

    // Load codec catalog
    try {
        state.catalog = await api("/api/catalog");
        populateCodecOptions();
        addStage("A");
        addStage("B");
    } catch (e) {
        console.error("Failed to load catalog:", e);
    }

    // Wire up toolbar controls
    document.getElementById("themeToggle").addEventListener("click", cycleTheme);
    document.getElementById("zoomIn").addEventListener("click", () => setZoom(1));
    document.getElementById("zoomOut").addEventListener("click", () => setZoom(-1));
    document.getElementById("langSelect").addEventListener("change", (e) => {
        currentLang = e.target.value;
        applyTranslations();
    });

    // File upload handlers
    setupFileUpload();

    // Scrubber handlers: update display on input, seek on release (change)
    const scrubber = document.getElementById("scrubSlider");

    // Track active drag to suppress animation updates during user interaction
    scrubber.addEventListener("mousedown", () => { state.isScrubbing = true; });
    scrubber.addEventListener("touchstart", () => { state.isScrubbing = true; }, { passive: true });

    scrubber.addEventListener("input", () => {
        // Just update the time display while dragging
        updateScrubber();
    });
    scrubber.addEventListener("change", () => {
        handleScrub();
        state.isScrubbing = false;
    });
    // Also clear on mouseup in case 'change' is delayed
    scrubber.addEventListener("mouseup", () => { state.isScrubbing = false; });
    scrubber.addEventListener("touchend", () => { state.isScrubbing = false; });
});

// ── Codec Options Population ─────────────────────────────────────

function populateCodecOptions() {
    const optionsHtml = Object.values(state.catalog).map(c =>
        `<option value="${c.codec_id}">${c.ui_name}</option>`
    ).join("");

    document.querySelectorAll(".codec-select").forEach(sel => {
        sel.innerHTML = '<option value="">— Select Codec —</option>' + optionsHtml;
    });
}

// ── Stage Management ─────────────────────────────────────────────

function addStage(side) {
    const container = document.getElementById(`stages${side}`);
    const row = document.createElement("div");
    row.className = "stage-row";

    // Codec select
    const codecSel = document.createElement("select");
    codecSel.className = "codec-select";
    const options = Object.values(state.catalog).map(c =>
        `<option value="${c.codec_id}">${c.ui_name}</option>`
    ).join("");
    codecSel.innerHTML = '<option value="">— Select Codec —</option>' + options;

    // Bitrate select (populated on change)
    const bitrateSel = document.createElement("select");
    bitrateSel.className = "bitrate-select";
    bitrateSel.innerHTML = '<option value="0">— Bitrate —</option>';

    codecSel.addEventListener("change", () => {
        const codec = state.catalog[codecSel.value];
        if (codec && !codec.pipeline_noop) {
            bitrateSel.innerHTML = codec.bitrate_options_kbps.map(b =>
                `<option value="${b}">${b} kbps</option>`
            ).join("");
        } else {
            bitrateSel.innerHTML = '<option value="0">N/A (passthrough)</option>';
        }
    });

    // Remove button
    const removeBtn = document.createElement("button");
    removeBtn.className = "stage-remove-btn";
    removeBtn.textContent = "✕";
    removeBtn.addEventListener("click", () => row.remove());

    row.appendChild(codecSel);
    row.appendChild(bitrateSel);
    row.appendChild(removeBtn);
    container.appendChild(row);
}

// ── Bandwidth Limit Toggle ───────────────────────────────────────

function toggleBwLimit(side) {
    const enabled = document.getElementById(`bwLimitEnabled${side}`).checked;
    const controls = document.getElementById(`bwLimitControls${side}`);
    if (enabled) {
        controls.classList.remove("hidden");
    } else {
        controls.classList.add("hidden");
    }
}

// ── File Upload ──────────────────────────────────────────────────

function setupFileUpload() {
    const input = document.getElementById("fileUpload");
    const dropZone = document.getElementById("fileDropZone");
    const display = document.getElementById("fileNameDisplay");

    // Click to browse
    dropZone.addEventListener("click", () => input.click());

    input.addEventListener("change", async (e) => {
        if (e.target.files.length === 0) return;
        await uploadFile(e.target.files[0], display);
    });

    // Drag & Drop
    dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropZone.style.borderColor = "var(--accent-color)";
    });

    dropZone.addEventListener("dragleave", () => {
        dropZone.style.borderColor = "";
    });

    dropZone.addEventListener("drop", async (e) => {
        e.preventDefault();
        dropZone.style.borderColor = "";
        if (e.dataTransfer.files.length > 0) {
            await uploadFile(e.dataTransfer.files[0], display);
        }
    });
}

async function uploadFile(file, displayEl) {
    const formData = new FormData();
    formData.append("file", file);

    try {
        const result = await api("/api/upload", {
            method: "POST",
            body: formData,
        });
        displayEl.textContent = `✓ ${result.filename}`;
        displayEl.style.color = "var(--success-color)";
    } catch (e) {
        displayEl.textContent = `✕ Error: ${e.message}`;
        displayEl.style.color = "var(--error-color)";
    }
}

// ── Preprocessing ────────────────────────────────────────────────

async function startPreprocess() {
    // Collect config
    const stagesA = collectStages("A");
    const stagesB = collectStages("B");

    const config = {
        sample_rate_mode: document.getElementById("sampleRateMode").value,
        label_mapping_mode: document.getElementById("labelMappingMode").value,
        stages_a: stagesA,
        stages_b: stagesB,
        bandwidth_limit_a_enabled: document.getElementById("bwLimitEnabledA").checked,
        bandwidth_limit_a_hz: parseInt(document.getElementById("bwLimitHzA").value) || null,
        bandwidth_limit_b_enabled: document.getElementById("bwLimitEnabledB").checked,
        bandwidth_limit_b_hz: parseInt(document.getElementById("bwLimitHzB").value) || null,
    };

    try {
        await api("/api/config", { method: "POST", body: JSON.stringify(config), headers: { "Content-Type": "application/json" } });
        await api("/api/preprocess", { method: "POST" });

        // Show progress area
        document.getElementById("progressArea").classList.remove("hidden");
        document.getElementById("btnPreprocess").classList.add("hidden");
        document.getElementById("btnCancelPreprocess").classList.remove("hidden");

        // Start polling
        state.pollInterval = setInterval(pollPreprocessStatus, 500);
    } catch (e) {
        alert(`Preprocess error: ${e.message}`);
    }
}

function collectStages(side) {
    const container = document.getElementById(`stages${side}`);
    const rows = container.querySelectorAll(".stage-row");
    const stages = [];
    rows.forEach(row => {
        const codecId = row.querySelector(".codec-select").value;
        const bitrateKbps = parseInt(row.querySelector(".bitrate-select").value) || 0;
        if (codecId) {
            stages.push({ codec_id: codecId, bitrate_kbps: bitrateKbps });
        }
    });
    return stages;
}

async function pollPreprocessStatus() {
    try {
        const status = await api("/api/preprocess/status");
        document.getElementById("progressBar").style.width = `${status.progress_pct}%`;
        document.getElementById("progressMessage").textContent = status.message;

        if (status.ready) {
            clearInterval(state.pollInterval);
            document.getElementById("btnPreprocess").classList.remove("hidden");
            document.getElementById("btnCancelPreprocess").classList.add("hidden");

            // Show ABX section
            document.getElementById("abxSection").classList.remove("hidden");
            document.getElementById("diagnosticsSection").classList.remove("hidden");

            // Stop any currently playing audio, reset timeline to 0, and reset ABX trials
            resetPlaybackForNewPreprocess();

            // Load audio info (re-load new buffers)
            await loadAudioInfo();

            // Show validation notes if any
            const diag = await api("/api/diagnostics");
            if (diag.metadata && diag.metadata.validation_notes && diag.metadata.validation_notes.length > 0) {
                document.getElementById("validationResult").classList.remove("hidden");
                const ul = document.getElementById("validationNotes");
                ul.innerHTML = diag.metadata.validation_notes.map(n => `<li>${n}</li>`).join("");
            }

            // Start first trial with reset state
            await startNewTrial();
        } else if (status.error) {
            clearInterval(state.pollInterval);
            document.getElementById("progressMessage").textContent = `Error: ${status.error}`;
            document.getElementById("btnPreprocess").classList.remove("hidden");
            document.getElementById("btnCancelPreprocess").classList.add("hidden");
        }
    } catch (e) {
        console.log("Poll error:", e);
    }
}

/**
 * Reset all playback state when preprocessing completes with new audio.
 * - Stops any currently playing audio
 * - Resets timeline/scrubber to 0
 * - Clears cached buffers so they reload for the new audio
 * - Resets ABX trial state (score, trials, etc.)
 */
function resetPlaybackForNewPreprocess() {
    const ctx = state.audioContext;
    if (ctx && state.currentSource) {
        const now = ctx.currentTime;
        // Quick fade out to prevent click
        if (state.gainNode) {
            state.gainNode.gain.cancelScheduledValues(now);
            state.gainNode.gain.setValueAtTime(state.gainNode.gain.value, now);
            state.gainNode.gain.linearRampToValueAtTime(0, now + 0.05);
        }
        try { state.currentSource.stop(now + 0.06); } catch (_) {}
    }

    // Reset playback flags
    state.isPlaying = false;
    state.isPaused = false;
    state.loopActive = false;
    state.currentSource = null;
    state.gainNode = null;
    state.playbackOffset = 0;
    state.pausePosition = 0;

    // Clear cached buffers so new audio is loaded fresh
    state.bufferA = null;
    state.bufferB = null;

    // Reset scrubber to position 0
    const slider = document.getElementById("scrubSlider");
    if (slider) {
        slider.value = 0;
        updateScrubber();
    }
    stopScrubberAnimation();

    // Reset play/pause button
    const btnPlayPause = document.getElementById("btnPlayPause");
    if (btnPlayPause) btnPlayPause.textContent = "▶";
    updatePlayButtons();

    // Reset ABX trial state on server and client
    api("/api/trial/reset", { method: "POST" }).then(() => {
        state.totalTrials = 0;
        state.correctTrials = 0;
        updateStatsDisplay();
        document.getElementById("trialFeedback").classList.add("hidden");
    }).catch(e => console.log("Trial reset error:", e));
}

async function cancelPreprocess() {
    await api("/api/preprocess/cancel", { method: "POST" });
}

// ── Audio Loading & Playback ─────────────────────────────────────

/**
 * Pre-load both audio buffers A and B after preprocessing completes.
 * This eliminates the ~10s delay on first play by fetching once upfront.
 * Also checks if A and B are identical (same codec/no-op) to share a single buffer.
 */
async function loadAudioInfo() {
    try {
        // Ensure audio context exists (must be created after user gesture)
        if (!state.audioContext) {
            state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        }

        const info = await api("/api/audio/info");
        state.audioDuration = info.duration_seconds;
        document.getElementById("scrubSlider").max = Math.round(state.audioDuration * 10);
        updateScrubber();

        // Set loop end default to audio duration
        const loopEndInput = document.getElementById("loopEnd");
        if (loopEndInput && parseFloat(loopEndInput.value) === 0) {
            loopEndInput.value = state.audioDuration.toFixed(2);
        }

        // Pre-load both buffers A and B in parallel for instant switching
        await Promise.all([
            preloadBuffer("a"),
            preloadBuffer("b"),
        ]);

        // Check if A and B are identical (same codec config, noop passthrough, etc.)
        // If so, share a single buffer for zero-perception difference
        state.buffersIdentical = info.buffers_identical || false;
        if (state.buffersIdentical && state.bufferA) {
            // Use the same buffer reference for both sides
            state.bufferB = state.bufferA;
        }
    } catch (e) {
        console.error("Audio info error:", e);
    }
}

/**
 * Fetch and decode a single audio buffer from the server.
 */
async function preloadBuffer(side) {
    const resp = await fetch(`/api/audio/${side}`);
    const arrayBuf = await resp.arrayBuffer();
    const buffer = await state.audioContext.decodeAudioData(arrayBuf.slice(0));
    if (side === "a") state.bufferA = buffer;
    else state.bufferB = buffer;
}

/**
 * Get the AudioBuffer for a given side.
 * 'x' resolves to whichever of A or B the backend assigned as X.
 * When buffers are identical (same codec/no-op), all sides share one buffer.
 */
function getBufferForSide(side) {
    if (state.buffersIdentical) {
        // All sides sound identical — return the single shared buffer
        return state.bufferA;
    }
    if (side === "a") return state.bufferA;
    if (side === "b") return state.bufferB;
    // x: use the buffer matching the current trial's X assignment
    return state.xIsA ? state.bufferA : state.bufferB;
}

/**
 * Compute the current shared-timeline playback position (seconds).
 */
function getCurrentTimelinePosition() {
    const ctx = state.audioContext;
    if (!ctx) return 0;
    const now = ctx.currentTime;
    const elapsed = now - state.playbackStartTime;
    return state.playbackOffset + elapsed;
}

/**
 * Switch the playback side (A, B, or X) WITHOUT affecting play/pause state.
 * - If playing: seamlessly switch the source buffer at the current timeline position
 * - If paused: just swap the buffer for the current side, stay paused
 * - If not playing yet: just remember which side to play when playback starts
 * This is the ONLY behavior — no auto-start, no auto-resume, no pause toggle.
 */
function switchSide(side) {
    const ctx = state.audioContext;
    if (!ctx) {
        ctx = state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }

    // Resume context if suspended (common browser requirement)
    if (ctx.state === "suspended") {
        ctx.resume();
    }

    const buffer = getBufferForSide(side);
    if (!buffer) return;  // still loading

    const loopEnabled = document.getElementById("loopEnabled").checked;
    let loopStartVal = parseFloat(document.getElementById("loopStart").value) || 0;
    let loopEndVal = parseFloat(document.getElementById("loopEnd").value) || buffer.duration;

    // Clamp loop region
    loopStartVal = Math.max(0, Math.min(loopStartVal, buffer.duration));
    loopEndVal = Math.max(loopStartVal + 0.01, Math.min(loopEndVal, buffer.duration));

    if (state.isPlaying && state.currentSource) {
        /* ── Already playing: SEAMLESS switch source at same timeline position ── */
        const now = ctx.currentTime;

        // Compute current playback position (shared timeline) BEFORE stopping old source
        let playOffset = getCurrentTimelinePosition();

        // Handle loop wrap within the active region
        if (loopEnabled && playOffset >= loopEndVal) {
            playOffset = loopStartVal + (playOffset - loopStartVal) % (loopEndVal - loopStartVal);
        } else if (!loopEnabled && playOffset >= buffer.duration) {
            // Audio finished, restart from beginning
            playOffset = 0;
        }

        // Quick micro-fade on old source's gain to prevent pop/click artifact
        const fadeDuration = 256 / ctx.sampleRate;  // ~5.3ms at 48kHz (one audio block)
        if (state.gainNode) {
            state.gainNode.gain.cancelScheduledValues(now);
            state.gainNode.gain.setValueAtTime(state.gainNode.gain.value, now);
            state.gainNode.gain.linearRampToValueAtTime(0, now + fadeDuration);
        }

        // Set guard flag BEFORE stopping old source so its onended won't call stopPlayback()
        state.switchingSource = true;

        // Stop old source after fade completes
        const oldSource = state.currentSource;
        oldSource.stop(now + fadeDuration + 0.001);

        // Start new source immediately at the same timeline position
        startSource(side, buffer, playOffset, loopEnabled, loopStartVal, loopEndVal);

    } else if (state.isPaused) {
        /* ── Paused: just update currentSide, stay paused ── */
        state.currentSide = side;
        // No source restart — when user presses play/pause to resume, it will use the new side

    } else {
        /* ── Not playing yet: just remember which side to start with ── */
        state.currentSide = side;
    }

    updatePlayButtons();
}

/**
 * Legacy wrapper: playAudio(side) — kept for backward compatibility.
 * Now simply delegates to switchSide (side buttons = side switcher only).
 */
function playAudio(side) {
    switchSide(side);
}

/**
 * Toggle global play/pause for the timeline.
 * Pauses at current position, resumes from same position on next call.
 */
function togglePlayPause() {
    const ctx = state.audioContext;
    if (!ctx) {
        ctx = state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (ctx.state === "suspended") {
        ctx.resume();
    }

    const btn = document.getElementById("btnPlayPause");

    if (state.isPlaying && state.currentSource) {
        /* ── PAUSE: capture current position, stop source ── */
        state.pausePosition = getCurrentTimelinePosition();
        const now = ctx.currentTime;
        const fadeDuration = 256 / ctx.sampleRate;

        if (state.gainNode) {
            state.gainNode.gain.cancelScheduledValues(now);
            state.gainNode.gain.setValueAtTime(state.gainNode.gain.value, now);
            state.gainNode.gain.linearRampToValueAtTime(0, now + fadeDuration);
        }

        const oldSource = state.currentSource;
        try { oldSource.stop(now + 0.05); } catch (_) {}

        state.isPlaying = false;
        state.isPaused = true;
        state.loopActive = false;
        state.currentSource = null;
        state.gainNode = null;

        if (btn) btn.textContent = "▶";
        stopScrubberAnimation();

    } else if (state.isPaused && state.bufferA) {
        /* ── RESUME: play from pause position using current side ── */
        const buffer = getBufferForSide(state.currentSide);
        if (!buffer) return;

        const loopEnabled = document.getElementById("loopEnabled").checked;
        let loopStartVal = parseFloat(document.getElementById("loopStart").value) || 0;
        let loopEndVal = parseFloat(document.getElementById("loopEnd").value) || buffer.duration;
        loopStartVal = Math.max(0, Math.min(loopStartVal, buffer.duration));
        loopEndVal = Math.max(loopStartVal + 0.01, Math.min(loopEndVal, buffer.duration));

        let playOffset = state.pausePosition;
        if (loopEnabled) {
            playOffset = Math.max(playOffset, loopStartVal);
            playOffset = Math.min(playOffset, loopEndVal);
        }

        startSource(state.currentSide, buffer, playOffset, loopEnabled, loopStartVal, loopEndVal);
        state.isPaused = false;

        if (btn) btn.textContent = "⏸";

    } else {
        /* ── Not playing at all: start playback from scrubber position ── */
        const side = state.currentSide || "a";
        const buffer = getBufferForSide(side);
        if (!buffer) return;

        const slider = document.getElementById("scrubSlider");
        const scrubValue = parseInt(slider.value) || 0;
        let playOffset = scrubValue / 10.0;

        const loopEnabled = document.getElementById("loopEnabled").checked;
        let loopStartVal = parseFloat(document.getElementById("loopStart").value) || 0;
        let loopEndVal = parseFloat(document.getElementById("loopEnd").value) || buffer.duration;
        loopStartVal = Math.max(0, Math.min(loopStartVal, buffer.duration));
        loopEndVal = Math.max(loopStartVal + 0.01, Math.min(loopEndVal, buffer.duration));

        if (loopEnabled) {
            playOffset = Math.max(playOffset, loopStartVal);
        }

        startSource(side, buffer, playOffset, loopEnabled, loopStartVal, loopEndVal);

        if (btn) btn.textContent = "⏸";
    }

    updatePlayButtons();
}

/**
 * Start a new AudioBufferSourceNode at the given offset.
 * Uses Web Audio API's native loop support for efficient looping.
 * Uses a PERSISTENT master gain node to avoid audio path disconnect/reconnect
 * artifacts when switching between sides (seamless transition).
 */
function startSource(side, buffer, playOffset, loopEnabled, loopStart, loopEnd) {
    const ctx = state.audioContext;
    const now = ctx.currentTime;

    // Stop existing source if any (should already be stopped, but safety check)
    // Capture reference BEFORE stopping so we can track it in onended guard
    const previousSource = state.currentSource;
    if (previousSource) {
        try { previousSource.stop(now); } catch (_) {}
    }

    const source = ctx.createBufferSource();
    source.buffer = buffer;

    // Configure native Web Audio API looping within [loopStart, loopEnd]
    if (loopEnabled) {
        source.loop = true;
        source.loopStart = loopStart;
        source.loopEnd = loopEnd;
    } else {
        source.loop = false;
    }

    // Use a persistent master gain node to avoid audio path changes during side switches.
    // This eliminates pops/clicks caused by disconnecting and reconnecting nodes.
    if (!state.masterGainNode) {
        state.masterGainNode = ctx.createGain();
        state.masterGainNode.gain.value = 1.0;
        state.masterGainNode.connect(ctx.destination);
    }

    // Per-source gain node for optional fade effects (connected to master)
    const gainNode = ctx.createGain();
    gainNode.gain.value = 1.0;
    source.connect(gainNode);
    gainNode.connect(state.masterGainNode);

    // Start playback from the given offset within the buffer
    source.start(0, playOffset);

    // Update currentSource BEFORE assigning onended so the guard check works correctly
    state.currentSource = source;
    state.gainNode = gainNode;
    state.playbackStartTime = now;
    state.playbackOffset = playOffset;
    state.isPlaying = true;
    state.loopActive = loopEnabled;

    // When playback ends (non-loop mode), stop cleanly.
    // IMPORTANT: only act if this source is still the current one —
    // otherwise another source (e.g. from scrubbing or side-switch) has
    // already replaced it and we should NOT interrupt playback.
    source.onended = () => {
        if (!state.isPlaying) return;

        // Guard: if a side-switch or scrub is in progress, defer to the new source
        if (state.switchingSource && state.currentSource !== source) {
            state.switchingSource = false;
            return;
        }

        if (loopEnabled && state.currentSource === source) {
            // Native loop should handle this, but as a fallback restart from loopStart
            startSource(state.currentSide, getBufferForSide(state.currentSide),
                        loopStart, true, loopStart, loopEnd);
        } else if (!loopEnabled && state.currentSource === source) {
            // Non-loop playback genuinely finished (not superseded by scrub/switch)
            stopPlayback();
        }
        // Otherwise: a newer source has taken over — do nothing.

        // Clear guard flag after processing
        state.switchingSource = false;
    };

    // Start scrubber animation for timeline tracking
    startScrubberAnimation(loopStart, loopEnd);
}

/**
 * Stop the current playback and clean up.
 */
function stopPlayback() {
    const ctx = state.audioContext;
    if (!ctx || !state.currentSource) return;

    const now = ctx.currentTime;
    if (state.gainNode) {
        // Quick fade out to prevent click on final stop
        const fadeDuration = 256 / ctx.sampleRate;
        state.gainNode.gain.cancelScheduledValues(now);
        state.gainNode.gain.setValueAtTime(state.gainNode.gain.value, now);
        state.gainNode.gain.linearRampToValueAtTime(0, now + fadeDuration);
    }

    const oldSource = state.currentSource;
    try { oldSource.stop(now + 0.05); } catch (_) {}

    state.isPlaying = false;
    state.loopActive = false;
    state.currentSource = null;
    state.gainNode = null;

    updatePlayButtons();
    stopScrubberAnimation();
}

/**
 * Update button visual state.
 * For seamless transition, NO visual indicator shows which side is playing.
 * Only the play/pause button reflects global playback state.
 */
function updatePlayButtons() {
    const btnA = document.getElementById("btnPlayA");
    const btnX = document.getElementById("btnPlayX");
    const btnB = document.getElementById("btnPlayB");
    const btnPlayPause = document.getElementById("btnPlayPause");

    // Remove active class from all side buttons (no visual indicator)
    [btnA, btnX, btnB].forEach(b => b.classList.remove("active"));

    // Update play/pause button text to reflect global state
    if (btnPlayPause) {
        btnPlayPause.textContent = state.isPlaying ? "⏸" : "▶";
    }
}

// ── Scrubber / Timeline ──────────────────────────────────────────

let scrubberAnimFrame = null;

/**
 * Handle user interaction with the scrub slider.
 * While playing, restarts the source at the new position (real-time seek).
 * When not playing, just updates the display.
 */
let scrubDebounceTimer = null;

function handleScrub(e) {
    const slider = document.getElementById("scrubSlider");
    const val = parseInt(slider.value);
    const seekTime = val / 10.0;

    updateScrubber();

    if (!state.isPlaying || !state.audioContext) return;

    // Debounce rapid scrub events: restart source only after user releases
    // For immediate response, we restart the source on each scrub change
    const buffer = getBufferForSide(state.currentSide);
    if (!buffer) return;

    const ctx = state.audioContext;
    const now = ctx.currentTime;
    const loopEnabled = document.getElementById("loopEnabled").checked;
    let loopStart = parseFloat(document.getElementById("loopStart").value) || 0;
    let loopEnd = parseFloat(document.getElementById("loopEnd").value) || buffer.duration;

    // Clamp seek position to valid range
    let clampedTime = Math.max(0, Math.min(seekTime, buffer.duration - 0.01));
    if (loopEnabled) {
        clampedTime = Math.max(clampedTime, loopStart);
        clampedTime = Math.min(clampedTime, loopEnd);
    }

    // Fade out current source quickly
    const fadeDuration = 256 / ctx.sampleRate;
    if (state.gainNode) {
        state.gainNode.gain.cancelScheduledValues(now);
        state.gainNode.gain.setValueAtTime(state.gainNode.gain.value, now);
        state.gainNode.gain.linearRampToValueAtTime(0, now + fadeDuration);
    }

    // Set guard flag so old source's onended won't interrupt playback during seek
    state.switchingSource = true;

    // Stop old source
    if (state.currentSource) {
        try { state.currentSource.stop(now + fadeDuration + 0.01); } catch (_) {}
    }

    // Restart at new position with same side — playback continues seamlessly
    startSource(state.currentSide, buffer, clampedTime, loopEnabled, loopStart, loopEnd);
}

/**
 * Update the time display based on slider value.
 */
function updateScrubber() {
    const slider = document.getElementById("scrubSlider");
    const val = parseInt(slider.value);
    const time = (val / 10);
    const total = state.audioDuration;
    document.getElementById("timeDisplay").textContent =
        `${formatTime(time)} / ${formatTime(total)}`;
}

let scrubberLoopStart = 0;
let scrubberLoopEnd = 0;

/**
 * Start animating the scrubber to follow playback position.
 */
function startScrubberAnimation(loopStart, loopEnd) {
    stopScrubberAnimation();  // cancel any existing animation

    scrubberLoopStart = loopStart;
    scrubberLoopEnd = loopEnd;

    function animate() {
        if (!state.isPlaying || !state.audioContext) {
            stopScrubberAnimation();
            return;
        }

        const now = state.audioContext.currentTime;
        let elapsed = now - state.playbackStartTime;
        let currentTime = state.playbackOffset + elapsed;

        // Clamp to loop region if active
        if (state.loopActive) {
            if (currentTime >= scrubberLoopEnd) {
                currentTime = scrubberLoopStart + (currentTime - scrubberLoopStart) % (scrubberLoopEnd - scrubberLoopStart);
            }
        } else if (currentTime >= state.audioDuration) {
            currentTime = state.audioDuration;
        }

        // Update slider position (1 unit = 0.1 second)
        const slider = document.getElementById("scrubSlider");
        // Only update slider if not being dragged by user (use state flag + CSS check for reliability)
        if (!state.isScrubbing && !slider.matches(":active")) {
            slider.value = Math.round(currentTime * 10);
            updateScrubber();
        }

        scrubberAnimFrame = requestAnimationFrame(animate);
    }

    scrubberAnimFrame = requestAnimationFrame(animate);
}

/**
 * Stop the scrubber animation.
 */
function stopScrubberAnimation() {
    if (scrubberAnimFrame) {
        cancelAnimationFrame(scrubberAnimFrame);
        scrubberAnimFrame = null;
    }
}

function formatTime(seconds) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
}

// ── Trial Management ─────────────────────────────────────────────

async function startNewTrial() {
    try {
        const trialInfo = await api("/api/trial/start", { method: "POST" });
        const stats = await api("/api/trial/stats");
        state.totalTrials = stats.total_trials;
        state.correctTrials = stats.correct_trials;

        // Track which side X maps to (A or B) for correct audio playback
        state.xIsA = trialInfo.x_is === "A";

        updateStatsDisplay();

        // Hide feedback from previous trial
        document.getElementById("trialFeedback").classList.add("hidden");
    } catch (e) {
        console.error("Trial start error:", e);
    }
}

async function submitAnswer(answer) {
    try {
        const result = await api("/api/trial/answer", {
            method: "POST",
            body: JSON.stringify({ answer }),
            headers: { "Content-Type": "application/json" },
        });

        const trial = result.trial;
        const feedbackEl = document.getElementById("trialFeedback");
        const textEl = document.getElementById("feedbackText");

        updateStatsDisplay();

        // Auto-advance: start next trial immediately, keep audio playing
        if (state.autoAdvanceEnabled) {
            // If currently playing X, switch to the new X automatically so audio keeps flowing
            if (state.isPlaying && state.currentSide === "x") {
                const buffer = getBufferForSide("x");
                if (buffer) {
                    const ctx = state.audioContext;
                    const now = ctx.currentTime;
                    const fadeDuration = 256 / ctx.sampleRate;

                    // Fade out old source
                    if (state.gainNode) {
                        state.gainNode.gain.cancelScheduledValues(now);
                        state.gainNode.gain.setValueAtTime(state.gainNode.gain.value, now);
                        state.gainNode.gain.linearRampToValueAtTime(0, now + fadeDuration);
                    }

                    const oldSource = state.currentSource;
                    try { oldSource.stop(now + fadeDuration + 0.01); } catch (_) {}

                    // Restart X at current timeline position with the new underlying buffer
                    let playOffset = getCurrentTimelinePosition();
                    const loopEnabled = document.getElementById("loopEnabled").checked;
                    let loopStartVal = parseFloat(document.getElementById("loopStart").value) || 0;
                    let loopEndVal = parseFloat(document.getElementById("loopEnd").value) || buffer.duration;

                    if (loopEnabled && playOffset >= loopEndVal) {
                        playOffset = loopStartVal + (playOffset - loopStartVal) % (loopEndVal - loopStartVal);
                    }

                    startSource("x", buffer, playOffset, loopEnabled, loopStartVal, loopEndVal);
                }
            }

            // Start next trial FIRST (hides feedback), then show previous trial's result
            // This avoids the flash: feedback is hidden → shown in one smooth transition
            await startNewTrial();
            // Now show the feedback for the trial that was just answered
            textEl.textContent = trial.correct ? `✓ Correct! (X was ${trial.x_is})` : `✕ Incorrect. (X was ${trial.x_is})`;
            textEl.className = trial.correct ? "correct" : "incorrect";
            feedbackEl.classList.remove("hidden");
        } else {
            // No auto-advance: show feedback immediately
            textEl.textContent = trial.correct ? `✓ Correct! (X was ${trial.x_is})` : `✕ Incorrect. (X was ${trial.x_is})`;
            textEl.className = trial.correct ? "correct" : "incorrect";
            feedbackEl.classList.remove("hidden");
        }
    } catch (e) {
        alert(`Answer error: ${e.message}`);
    }
}

function nextTrial() {
    startNewTrial();
}

async function resetTrials() {
    try {
        await api("/api/trial/reset", { method: "POST" });
        state.totalTrials = 0;
        state.correctTrials = 0;
        updateStatsDisplay();
        document.getElementById("trialFeedback").classList.add("hidden");
        await startNewTrial();
    } catch (e) {
        alert(`Reset error: ${e.message}`);
    }
}

function updateStatsDisplay() {
    const stats = state; // Use local state which is synced from API
    document.getElementById("statTotal").textContent = stats.totalTrials;
    document.getElementById("statCorrect").textContent = stats.correctTrials;

    // Fetch p-value from server
    api("/api/trial/stats").then(s => {
        document.getElementById("statPValue").textContent = s.p_value_one_tailed.toFixed(4);
    });

    document.getElementById("trialCounter").textContent = t("trial_counter", stats.totalTrials + 1, stats.correctTrials);
    document.getElementById("scoreDisplay").textContent = t("score_display", stats.correctTrials, stats.totalTrials);
}

// ── Export Functions ─────────────────────────────────────────────

function exportJSON() {
    window.open("/api/export/json", "_blank");
}

function exportCSV() {
    const a = document.createElement("a");
    a.href = "/api/export/csv";
    a.download = "abx_trials.csv";
    a.click();
}

// ── Diagnostics ──────────────────────────────────────────────────

async function loadDiagnostics() {
    try {
        const diag = await api("/api/diagnostics");
        document.getElementById("diagnosticsOutput").textContent = JSON.stringify(diag, null, 2);
    } catch (e) {
        document.getElementById("diagnosticsOutput").textContent = `Error: ${e.message}`;
    }
}

// ── Keyboard Shortcuts ───────────────────────────────────────────

document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;

    switch (e.key.toLowerCase()) {
        case " ":
            e.preventDefault();
            togglePlayPause();
            break;
        case "a": playAudio("a"); break;
        case "x": playAudio("x"); break;
        case "b": playAudio("b"); break;
        case "1": submitAnswer("A"); break;
        case "2": submitAnswer("B"); break;
    }
});