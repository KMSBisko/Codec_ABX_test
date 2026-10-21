# Codec ABX Tester Design Document (v1.0)

## 🎯 Purpose & Goals
This document details the design of the Local Codec ABX Tester application, a desktop tool designed to rigorously test and compare audio codec performance by running side-by-side (A/B) or randomized (ABX) listening tests against a defined set of preprocessing and validation criteria.

**Primary Goal:** To provide an audibly accurate and statistically sound platform for comparing the perceptual quality of different audio codecs under controlled conditions.
**Secondary Goals:** Ensure high test validity through strict pre-processing, offer flexible pipeline customization, and deliver results in easily consumable formats (JSON/CSV).

## 🏛️ Architectural Overview: MVC Pattern
The application adheres to a modified Model-View-Controller (MVC) pattern implemented using PyQt6 for the GUI. The separation of concerns ensures that business logic is testable independently of the user interface.

*   **Model:** Contains the core data, state, and processing logic.
    *   `models.py`: Defines all shared data structures (e.g., `PipelineStageConfig`, `TrialResult`).
    *   `audio_pipeline.py`: Handles the heavy lifting of audio transformation (FFmpeg integration).
    *   `abx_engine.py`: Manages the statistical state, trial randomization, and scoring logic.
*   **View:** The graphical representation built primarily in `main.py`. It is responsible for rendering data from the Model and capturing user input.
*   **Controller:** Primarily resides within `app/main.py`. It acts as the intermediary, receiving signals from the View (user actions) and invoking methods on the Model components, then updating the View with results.

## 🧩 Core Components & Responsibilities

| Component | File | Primary Responsibility | Key Interactions |
| :--- | :--- | :--- | :--- |
| **Main Window** | `main.py` | Orchestration, UI Construction, Event Handling (Controller) | Interacts with all other components; manages state transitions. |
| **Audio Pipeline** | `audio_pipeline.py` | Audio Transformation & Codec Encoding/Decoding | Takes raw audio $\rightarrow$ applies stages $\rightarrow$ outputs PCM NumPy arrays. |
| **ABX Engine** | `abx_engine.py` | Statistical Logic & Trial Management | Manages trial count, calculates p-value, tracks score based on user input. |
| **Player** | `player.py` | Audio Playback Control | Uses `sounddevice` to stream preprocessed PCM data; handles crossfades and timeline scrubbing. |
| **Logger** | `logger.py` | Data Persistence & Reporting | Exports final session results into structured JSON/CSV files. |

## 🌊 Data Flow: Key Execution Paths

### 1. Preprocessing Path (Setup Phase)
This path converts the source file into ready-to-use, processed audio arrays for testing.
**Flow:** User Action $\rightarrow$ `main.py` (Controller) $\rightarrow$ `PrepareWorker` (QThread) $\rightarrow$ `AudioPipeline.prepare_session()` (Model) $\rightarrow$ **Output**: Signals (`finished`, `failed`) containing NumPy arrays ($\text{Array}_A, \text{Array}_B$) $\rightarrow$ `main.py` (View/Controller) updates UI and initializes `SynchronizedABXPlayer`.

### 2. Trial Execution Path (Testing Phase)
This path runs the actual listening tests once preprocessing is complete.
**Flow:** User Action $\rightarrow$ `main.py` (Controller) $\rightarrow$ Selects Array ($\text{Array}_A$ or $\text{Array}_B$) $\rightarrow$ `SynchronizedABXPlayer.play()` (Model/View) streams audio $\rightarrow$ User Input $\rightarrow$ `ABXEngine.submit_answer(choice)` (Model) $\rightarrow$ **Output**: Updated Score & P-Value $\rightarrow$ `main.py` (View) updates diagnostics display.

## ⚙️ Functionality Deep Dive

### A. Audio Pipeline Stages
The pipeline is highly configurable, allowing for up to 4 stages per side (A/B). Each stage encapsulates a codec transformation:
*   **Stage Operation:** Encode $\rightarrow$ Decode $\rightarrow$ PCM Output.
*   **Codec Selection:** Supports Lossless (Reference), No-op Passthrough, Opus, AAC, Ogg Vorbis, SBC, aptX, aptX HD, LDAC.
*   **Bitrate Control:** Specific bitrates are set per stage/codec choice to stress test artifact audibility.
*   **Fallback Mechanism:** If a selected codec (e.g., `aptx`) is unavailable in the FFmpeg build, the pipeline automatically simulates it using an AAC profile matched to the requested bitrate.

### B. Validation Checks (Pre-ABX)
Before any trial begins, the system validates critical parameters:
1.  **Sample Rate Consistency:** $\text{SR}_A = \text{SR}_B$. If not equal, resampling is performed based on user choice (Native or Forced 48 kHz).
2.  **Alignment Lag Compensation:** Estimates and compensates for any timing drift introduced by the pipeline stages.
3.  **Effective Length Match:** Ensures both sides have the same total audio duration after processing.
4.  **Loudness Delta:** Measures $\text{LUFS}_A - \text{LUFS}_B$. Must be $<\mathbf{0.1\ dB}$ for a valid test run.

### C. ABX Engine Logic
*   **Randomization:** Uses `random.SystemRandom()` to ensure trials are cryptographically random.
*   **Scoring:** Tracks the number of correct answers ($k$) out of total trials ($n$).
*   **Statistical Output:** Calculates the **one-tailed binomial p-value**, representing $P(X \ge k | n, p=0.5)$.

### D. Playback & User Experience (UX)
*   **Playback Control:** Uses `sounddevice` for low-latency streaming from preloaded NumPy arrays.
*   **Switching:** A/B/X switching is managed via a short **click-suppression crossfade** to prevent audible clicks during transitions.
*   **Scrubbing:** The timeline maintains sample-accurate alignment, allowing users to scrub back and forth without introducing phase shifts.

## 🛠️ Technical Decisions & Constraints

| Area | Decision/Constraint | Implementation Detail |
| :--- | :--- | :--- |
| **Threading** | Asynchronous Preprocessing | `PrepareWorker` (QThread) handles all FFmpeg calls to keep the GUI responsive. |
| **FFmpeg Integration** | External Binary Dependency | Uses bundled binaries (`third_party/ffmpeg/`) for portability, relying on Python wrappers to interface with them. |
| **Sample Rate Control** | Explicit Override | User can force 48 kHz resampling regardless of source file rate, ensuring consistency across all stages. |
| **Loudness Target** | Fixed Standard | EBU R128 target is strictly set to $\mathbf{-16\ LUFS}$. |
| **Pipeline Limit** | Hardcoded Max Stages | Maximum configurable stages per side is 4 (configurable in `main.py`). |

## 🚀 Deployment & Execution Summary
*   **Entry Point:** `run_abx.py`
*   **Dependencies:** Python 3.11+, FFmpeg/FFprobe on PATH.
*   **Distribution:** PyInstaller script (`tools/build_windows_exe.ps1`) creates a single, self-extracting `.exe`.