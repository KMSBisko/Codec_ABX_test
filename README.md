# Local Codec ABX Tester

Language: English | [Tiếng Việt](#tiếng-việt)

Desktop ABX tool for local codec listening tests with strict preprocessing and validation controls.

## What This App Enforces

- Input: WAV/FLAC (FLAC converted to WAV)
- Sample-rate modes:
  - Native sample-rate (default): keep source sample-rate as target
  - Forced 48 kHz: explicit `soxr` resample before any codec stage
- Offline codec pipeline:
  - Side A and Side B each run their own stage pipeline (1 to 4 stages per side)
  - Each stage: Encode -> decode -> pass PCM to next stage
  - Stage count defaults to 1 for both sides (equivalent to classic single-stage ABX)
  - No-op / Lossless passthrough stage is available for bit-identical pipeline hops
  - Explicit sample-rate control (`-ar`) to avoid hidden conversion
- Loudness:
  - EBU R128 LUFS normalization target: `-16 LUFS`
  - Normalization is applied once after full pipeline processing
  - Gain-only normalization
  - No-clipping guard (global attenuation if needed)
- Validation checks before ABX:
  - Same sample-rate for A/B
  - Alignment lag estimate and compensation after full pipeline outputs
  - Same effective length
  - Loudness delta report (target < 0.1 dB)
- Optional post-pipeline bandwidth limiting:
  - Side A and Side B can be configured independently
  - Cutoff per side: 14 kHz / 16 kHz / 18 kHz
- ABX engine:
  - Randomized X each trial
  - Running score + one-tailed binomial p-value
- Playback:
  - Shared global timeline
  - Scrub preserves alignment
  - A/B/X switch uses a short click-suppression crossfade
  - Device selection + exclusive mode request (WASAPI exclusive supported when available)
- Logging:
  - Export JSON/CSV results

## Supported Codec Profiles

Direct:
- Lossless (Unprocessed Reference)
- No-op / Lossless passthrough (pipeline stage)
- Opus (`libopus`)
- AAC (`aac`)
- SBC (`sbc` if your ffmpeg build supports it)

Simulated Bluetooth labels (implemented through available codecs/bitrates):
- Simulated aptX
- Simulated aptX HD
- Simulated LDAC

Additional bitrate notes:
- Lower bitrate options are available for direct codecs (Opus/AAC/SBC) to stress-test artifact audibility.
- Simulated LDAC now uses AAC at 330/660/990 kbps for more reliable high-bitrate preprocessing across ffmpeg builds.

## Prerequisites

1. Python 3.11+
2. ffmpeg + ffprobe on PATH

Check:

```powershell
ffmpeg -version
ffprobe -version
```

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python run_abx.py
```

## Build A Single Windows EXE (No Python Needed For End Users)

This project includes a PyInstaller setup for a one-file GUI executable.

1. Put ffmpeg binaries in:
  - `third_party/ffmpeg/bin/ffmpeg.exe`
  - `third_party/ffmpeg/bin/ffprobe.exe`

2. Build:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows_exe.ps1
```

3. Share this file with users:
  - `dist/run_abx.exe`

Notes:
- End users do not need Python installed.
- On first launch, some antivirus tools may scan the self-extracting exe.
- If SmartScreen warns on unsigned apps, users may need to click "More info" -> "Run anyway".

Build troubleshooting:
- Run the build from a normal (non-admin) terminal.
- If you previously hit `PermissionError` in `build/run_abx/...`, close any app/process that may lock files (Explorer preview, antivirus scan in progress), then run the build script again.
- The script now uses a fresh PyInstaller work folder each run to avoid stale lock issues.

## Example Test File Included

Generate bundled example input:

```powershell
python tools/generate_example_audio.py
```

This creates:
- `examples/example_abx_input.wav`

## Basic Usage

1. Select WAV/FLAC source.
2. Choose sample-rate mode (Native or Forced 48 kHz).
3. Configure Side A and Side B pipeline stages.
  - Set **Stage count A** and **Stage count B** independently (1 to 4 each).
  - Default is A=1 and B=1, which behaves like classic single-stage ABX.
  - For each visible stage, choose codec and bitrate.
  - You can use **No-op / Lossless passthrough** in any stage.
  - You can enable optional bandwidth limit independently for A and B.
  - Choose **A/B label mapping** mode:
    - Fixed labels: Play A is Codec A, Play B is Codec B.
    - Blinded labels: Play A/B mapping is randomized each trial (may stay same or swap).
      To reduce deterministic behavior, swap probability increases gradually with no-change streak length.
4. Click **Preprocess A/B** and verify status metrics.
  - Use **Cancel Preprocess** to stop preprocessing while ffmpeg jobs are running.
5. Select output device (enable exclusive mode if desired).
6. Perform ABX trials with Play A/B/X and answer X=A or X=B.
  - Use **Cancel ABX Session** to reset trial state and score.
7. Use **Show/Refresh Diagnostics** to review in-app post-session details:
  - A/B label mapping mode (non-revealing in top summary)
  - Trial-by-trial X label/source, answer label/source, and correctness
  - Whether mapping changed for the next trial
  - Full mapping audit is placed in a lower section so it is not immediately visible
8. Export results as JSON/CSV.

## Notes On Experimental Validity

- For strongest validity, keep all OS audio enhancements disabled.
- Keep system volume fixed during the test.
- Use wired output or known stable exclusive-mode path when possible.
- A/B/X are rendered offline before trials to reduce runtime variability.

## Session Retention

The app auto-prunes local session folders under `sessions/`:
- Deletes session folders older than 1 day
- Keeps only the newest N session folders

You can tune this in code:
- `SESSION_MAX_KEEP`
- `SESSION_MAX_AGE`

See `app/main.py`.

## Quick Check

```powershell
python -m compileall app run_abx.py
```

---

## Tiếng Việt

# Trình kiểm tra ABX Codec cục bộ

Công cụ ABX desktop để kiểm tra nghe codec cục bộ với các ràng buộc tiền xử lý và kiểm định chặt chẽ.

## Ứng dụng đảm bảo những gì

- Đầu vào: WAV/FLAC (FLAC sẽ được chuyển sang WAV)
- Chế độ tần số lấy mẫu:
  - Native sample-rate (mặc định): giữ nguyên tần số lấy mẫu của nguồn
  - Ép 48 kHz: resample `soxr` rõ ràng trước mọi stage codec
- Pipeline codec offline:
  - Nhánh A và nhánh B mỗi nhánh chạy pipeline stage riêng (1 đến 4 stage cho mỗi nhánh)
  - Mỗi stage: Encode -> decode -> chuyển PCM sang stage tiếp theo
  - Số stage mặc định là 1 cho cả hai nhánh (tương đương ABX single-stage cổ điển)
  - Có stage No-op / Lossless passthrough để mô phỏng bước pipeline không làm biến đổi tín hiệu
  - Kiểm soát tần số lấy mẫu rõ ràng (`-ar`) để tránh chuyển đổi ẩn
- Loudness:
  - Mục tiêu chuẩn hóa EBU R128 LUFS: `-16 LUFS`
  - Chuẩn hóa chỉ áp dụng một lần sau khi hoàn tất toàn bộ pipeline
  - Chuẩn hóa theo gain-only
  - Chống clipping (giảm mức toàn cục nếu cần)
- Kiểm định trước ABX:
  - Cùng sample-rate cho A/B
  - Ước lượng và bù lệch căn chỉnh sau khi có đầu ra pipeline cuối cùng
  - Cùng độ dài hiệu dụng
  - Báo chênh lệch loudness (mục tiêu < 0.1 dB)
- Giới hạn băng thông sau pipeline (tùy chọn):
  - Cấu hình độc lập cho nhánh A và nhánh B
  - Điểm cắt mỗi nhánh: 14 kHz / 16 kHz / 18 kHz
- ABX engine:
  - X được random ở mỗi lượt
  - Điểm số chạy + p-value nhị thức một phía
- Playback:
  - Timeline toàn cục dùng chung
  - Scrub vẫn giữ căn chỉnh
  - Chuyển A/B/X dùng crossfade ngắn để giảm click
  - Chọn thiết bị + yêu cầu exclusive mode (WASAPI exclusive khi khả dụng)
- Logging:
  - Xuất kết quả JSON/CSV

## Các profile codec được hỗ trợ

Trực tiếp:
- Lossless (Unprocessed Reference)
- No-op / Lossless passthrough (pipeline stage)
- Opus (`libopus`)
- AAC (`aac`)
- SBC (`sbc`, nếu bản ffmpeg của bạn hỗ trợ)

Nhãn Bluetooth mô phỏng (được triển khai bằng codec/bitrate khả dụng):
- Simulated aptX
- Simulated aptX HD
- Simulated LDAC

Ghi chú bitrate bổ sung:
- Có thêm các mức bitrate thấp cho codec trực tiếp (Opus/AAC/SBC) để stress-test khả năng nghe ra artefact.
- Simulated LDAC hiện dùng AAC ở 330/660/990 kbps để tiền xử lý bitrate cao ổn định hơn trên nhiều bản ffmpeg.

## Yêu cầu trước khi chạy

1. Python 3.11+
2. ffmpeg + ffprobe trong PATH

Kiểm tra:

```powershell
ffmpeg -version
ffprobe -version
```

## Cài đặt

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Chạy ứng dụng

```powershell
python run_abx.py
```

## Build file EXE Windows (người dùng cuối không cần Python)

Dự án này có cấu hình PyInstaller để tạo GUI executable one-file.

1. Đặt binary ffmpeg tại:
  - `third_party/ffmpeg/bin/ffmpeg.exe`
  - `third_party/ffmpeg/bin/ffprobe.exe`

2. Build:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows_exe.ps1
```

3. Chia sẻ file này cho người dùng:
  - `dist/run_abx.exe`

Ghi chú:
- Người dùng cuối không cần cài Python.
- Lần chạy đầu, một số phần mềm antivirus có thể quét file exe tự giải nén.
- Nếu SmartScreen cảnh báo app chưa ký, người dùng có thể cần bấm "More info" -> "Run anyway".

Khắc phục lỗi build:
- Chạy lệnh build từ terminal thường (không cần quyền admin).
- Nếu trước đó gặp `PermissionError` trong `build/run_abx/...`, hãy đóng các tiến trình có thể đang khóa file (Explorer preview, antivirus đang quét), rồi chạy lại script build.
- Script hiện dùng thư mục làm việc PyInstaller mới ở mỗi lần chạy để tránh lỗi khóa file cũ.

## Có sẵn file test mẫu

Tạo file đầu vào mẫu đi kèm:

```powershell
python tools/generate_example_audio.py
```

Lệnh này tạo:
- `examples/example_abx_input.wav`

## Cách sử dụng cơ bản

1. Chọn nguồn WAV/FLAC.
2. Chọn chế độ tần số lấy mẫu (Native hoặc Ép 48 kHz).
3. Cấu hình các stage pipeline cho nhánh A và nhánh B.
  - Đặt **Stage count A** và **Stage count B** độc lập (1 đến 4 cho mỗi nhánh).
  - Mặc định là A=1 và B=1, hành vi giống ABX single-stage cổ điển.
  - Với mỗi stage hiển thị, chọn codec và bitrate.
  - Bạn có thể dùng **No-op / Lossless passthrough** ở bất kỳ stage nào.
  - Bạn có thể bật giới hạn băng thông độc lập cho A và B.
  - Chọn chế độ **A/B label mapping**:
    - Fixed labels: Play A là Codec A, Play B là Codec B.
    - Blinded labels: ánh xạ Play A/B được random theo từng lượt (có thể giữ nguyên hoặc đổi).
      Để giảm tính quyết định, xác suất đổi sẽ tăng dần theo chuỗi lượt không đổi ánh xạ.
4. Bấm **Preprocess A/B** và kiểm tra các chỉ số trạng thái.
  - Dùng **Cancel Preprocess** để dừng tiền xử lý khi các job ffmpeg đang chạy.
5. Chọn thiết bị phát (bật exclusive mode nếu cần).
6. Thực hiện ABX với Play A/B/X và trả lời X=A hoặc X=B.
  - Dùng **Cancel ABX Session** để reset lượt và điểm.
7. Dùng **Show/Refresh Diagnostics** để xem chẩn đoán sau phiên trong app:
  - Chế độ ánh xạ nhãn A/B (phần tóm tắt trên cùng không lộ đáp án)
  - Theo từng lượt: nhãn X/source, nhãn trả lời/source, đúng/sai
  - Trạng thái ánh xạ có đổi cho lượt tiếp theo hay không
  - Mapping audit đầy đủ được đặt ở phần thấp hơn để tránh nhìn thấy ngay lập tức
8. Xuất kết quả JSON/CSV.

## Ghi chú về tính hợp lệ thực nghiệm

- Để kết quả đáng tin hơn, nên tắt toàn bộ hiệu ứng âm thanh của hệ điều hành.
- Giữ âm lượng hệ thống cố định trong suốt bài test.
- Ưu tiên đường phát có dây hoặc đường exclusive mode ổn định.
- A/B/X được render offline trước khi làm bài để giảm biến thiên thời gian chạy.

## Chính sách lưu phiên

Ứng dụng tự dọn session cục bộ trong `sessions/`:
- Xóa session cũ hơn 1 ngày
- Chỉ giữ lại N session mới nhất

Bạn có thể chỉnh trong code:
- `SESSION_MAX_KEEP`
- `SESSION_MAX_AGE`

Xem `app/main.py`.

## Kiểm tra nhanh

```powershell
python -m compileall app run_abx.py
```
