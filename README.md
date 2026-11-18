# Bacchus

Bacchus turns recorded narration and a creative brief into a reviewed MP4. AI can transcribe the recording, plan the whole cut, and design or generate artwork. The local studio validates scene coverage, tracks approvals, mixes sound, and renders the approved candidate.

## Install Bacchus on Windows x64

Download the [Bacchus 0.3.1 Windows installer](releases/0.3.1/Bacchus-0.3.1-windows-x64.msi) and its [SHA-256 checksum](releases/0.3.1/Bacchus-0.3.1-windows-x64.sha256). This unsigned preview includes Python and the app's dependencies. The `v0.3.1` tag records the source and installer for this release.

1. Run the MSI, then open **Bacchus** from the Start menu. Bacchus opens its local workspace in your default browser.
2. In **Setup**, choose **Install media tools** if the readiness checks are incomplete. This downloads a verified FFmpeg build into Bacchus's user data.
3. To close the app, select **Quit Bacchus** in the sidebar. Quit Bacchus before running a newer installer or uninstalling it from **Settings → Apps** in Windows.

Manual workflows work offline once the media tools are available. Add provider keys in Bacchus **Settings** to use AI features.

## Storage

Bacchus stores projects, settings and the default art library in `%LOCALAPPDATA%\Bacchus` on Windows. **Settings** shows the active data and art library locations. The Windows installer manages the application files separately.

To back up Bacchus, quit the app, then copy its data folder and any separately configured art library. To restore a Bacchus 0.3.0 backup, copy its contents into an empty data folder with Bacchus closed. The application supports `--data <folder>` for a different storage location, such as an external drive.

## Configure AI

Enter an OpenAI API key in **Settings**, or set `OPENAI_API_KEY` before launch, to enable transcription, planning, and artwork generation. Bacchus defaults to `gpt-transcribe` for transcription, `gpt-6-luna` for planning, and `gpt-image-2.5-flare-2026-09-08` for illustrations. Change the model fields in Settings or provide `BACCHUS_TRANSCRIBE_MODEL`, `BACCHUS_OPENAI_MODEL`, and `BACCHUS_IMAGE_MODEL` before the first setup. [File transcription](https://developers.openai.com/api/docs/guides/speech-to-text) · [Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs) · [Image generation API](https://developers.openai.com/api/reference/resources/images/methods/generate)

To select Claude for planning, choose Anthropic and enter its key and a Claude model ID that supports structured outputs. The equivalent environment variables are `BACCHUS_PLANNER_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, and `BACCHUS_CLAUDE_MODEL`. The OpenAI key controls transcription and artwork independently. [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

Keys entered in the GUI last until the Bacchus process exits. Nonsecret settings persist in the data folder, and keys supplied through the environment remain available on launch. Each provider call starts from a button. Transcription sends a compact copy of the recording to OpenAI. Planning sends the transcript, brief, cue timings, and a compact set of relevant approved artwork names and descriptions. Image generation sends the selected art brief and style to OpenAI. Bacchus records attempts and usage where available, limits requests per project, and leaves retries to you.

## Make a cut

1. Import an MP3, FLAC, or WAV. Transcribe it in Bacchus, paste an existing transcript, or import recording-matched word timings. Pick a visual style, set the creative brief, and lock intake.
2. Generate a plan or start from the local editable draft. Review the scenes, motion, and artwork requirements, then approve the saved plan.
3. Generate the requested illustrations in Bacchus or use the combined request with your image tool. Upload any prepared artwork and approve images for this cut.
4. Choose a built-in score with transition accents, import music and place effects, or select no music. Listen to the same mix used in the final video, then approve it.
5. Review the cut, run checks, and accept the candidate. Press **Render video** to download the H.264 MP4, SRT captions, and a render receipt.

The offline example starts with an approved visual plan. For your own recording, manual transcript entry, the local starter plan, image import and built-in soundtracks also work offline. The app supports recordings up to 30 minutes and three visual treatments over the same title, editorial, and illustration layouts. Export is 720p at 24 fps. OpenAI file transcription and manually entered transcript text use estimated timing. Import a `timing.json` with matching `audio.sha256`, `duration`, and `words` to use source-linked cues. Editing the imported transcript text switches it to estimated timing.

Bacchus keeps the original uploaded recording, normalized media, plans, approvals, request metadata, and exports in its data folder. Editing a plan, referenced asset, or sound mix requires fresh approval of the resulting candidate. Earlier completed MP4s and receipts remain on disk.

## Reuse artwork

Approving an uploaded or generated image saves it to the shared **Art library**. Existing approved images have a **Save to library** action. Approving a replacement under the same original project artwork ID creates a new revision when its image changes. Open the library to inspect earlier revisions and pin any one of them to a project under its own scene ID. A project keeps a copy of that exact image and records its library revision in the render receipt.

The planner receives a shortlist of up to 20 library images matched to the brief and narration by title, filename, and art description. If it selects a match, Bacchus pins that version when it saves the plan. Review those selections along with the scene plan.

By default the art library lives in `library/` inside the Bacchus data folder. The launcher's `--library <folder>` option can point to a separate storage or backup location. Keep `entries/` and `blobs/` together. The library's JSON files store version histories, and the blobs are PNGs named by their content hash. A separate Git checkout can track those two directories and ignore `.library.lock` and temporary `.tmp` files.

## Build and test

The app entry point, interface, and planner resources are in `bacchus/`. Installer, bundle, and release verification tools are in `packaging/`. Exact release installers and checksums live in `releases/`.

Build on the target platform with Python 3.11 or newer and a virtual environment. Media tests use a full FFmpeg build with H.264 (`libx264`), AAC and PCM encoding, and the `sidechaincompress` and `alimiter` filters. [FFmpeg download and license information](https://ffmpeg.org/download.html)

```text
python -m pip install -r requirements.txt -r packaging/requirements.txt
python -m unittest discover -s tests -v
python packaging/build.py
```

On Windows, test the built application and its video export with `python packaging/smoke_bundle.py dist/Bacchus/Bacchus.exe --render`.

The [native release workflow](.github/workflows/release.yml) defines Windows installer and macOS and Linux bundle builds, including packaged application smoke tests. The app ships under the [MIT License](LICENSE). See the [harness research](docs/harness-research.md) and [source workflow audit](docs/source-workflows.md) for design context.
