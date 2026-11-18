# Bacchus

Bacchus turns recorded narration and a creative brief into a reviewed MP4. AI can transcribe the recording, plan the whole cut, and design or generate artwork. The local studio validates scene coverage, tracks approvals, mixes sound, and renders the approved candidate.

## Install Bacchus on Windows x64

Download the [Bacchus 0.3.2 Windows installer](releases/0.3.2/Bacchus-0.3.2-windows-x64.msi) and its [SHA-256 checksum](releases/0.3.2/Bacchus-0.3.2-windows-x64.sha256). This unsigned preview includes Python and the app's dependencies. The `v0.3.2` tag records the source and installer for this release.

1. Run the MSI, then open **Bacchus** from the Start menu. Bacchus opens its local workspace in your default browser.
2. In **Setup**, choose **Install media tools** if the readiness checks are incomplete. This downloads a verified FFmpeg build into Bacchus's user data.
3. To close the app, select **Quit Bacchus** in the sidebar. Quit Bacchus before running a newer installer or uninstalling it from **Settings → Apps** in Windows.

Manual workflows work offline once the media tools are available. Add provider keys in Bacchus **Settings** to use AI features.

## Storage

Bacchus stores projects, settings and the default art library in `%LOCALAPPDATA%\Bacchus` on Windows. **Settings** shows the active data and art library locations. The Windows installer manages the application files separately.

To back up Bacchus, quit the app, then copy its data folder and any separately configured art library. To restore a Bacchus 0.3.0 or 0.3.1 backup, copy its contents into an empty data folder with Bacchus closed. The application supports `--data <folder>` for a different storage location, such as an external drive.

## Configure AI

Bacchus uses Anthropic's Claude Sonnet 5.5 (`claude-sonnet-5-5`) by default to plan scenes, choose motion and write artwork briefs. Enter an Anthropic API key in **Settings** or set `ANTHROPIC_API_KEY` before launch to enable planning. Paste spoken text or import matching word timings, then use approved library artwork or upload images for the plan. Bacchus renders the approved plan locally. You can select another Claude model with structured outputs in **Settings** or through `BACCHUS_CLAUDE_MODEL`. [Claude Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview) · [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

To use an optional OpenAI feature, add an OpenAI API key in **Settings** or set `OPENAI_API_KEY`. Then enable the specific feature you want.

- **Transcription.** Enable OpenAI transcription in **Settings** or set `BACCHUS_USE_OPENAI_TRANSCRIPTION=1`. When requested, Bacchus sends a compact copy of the recording for speech-to-text. [File transcription](https://developers.openai.com/api/docs/guides/speech-to-text)
- **Image generation.** Enable OpenAI image generation in **Settings** or set `BACCHUS_USE_OPENAI_IMAGES=1`. When requested, Bacchus sends the selected artwork brief and style to generate an image. [Image generation API](https://developers.openai.com/api/reference/resources/images/methods/generate)
- **Scene planning.** Choose OpenAI as your planning provider in **Settings** or set `BACCHUS_PLANNER_PROVIDER=openai`. [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

Each provider call starts at your request. Planning sends the transcript, brief, cue timings, and relevant approved artwork names and descriptions to the selected provider. Bacchus records attempts and usage where available, limits requests per project, and leaves retries to you. Keys entered in **Settings** last until Bacchus exits. Nonsecret preferences persist in the data folder.

## Make a cut

1. Import an MP3, FLAC, or WAV. Paste an existing transcript, import recording-matched word timings, or transcribe it with the optional OpenAI feature. Pick a visual style, set the creative brief, and lock intake.
2. Generate a plan or start from the local editable draft. Review the scenes, motion, and artwork requirements, then approve the saved plan.
3. Use artwork from the library, upload prepared images, or use the combined request with your image tool. If you enabled OpenAI image generation, you can also make illustrations in Bacchus. Review and approve the images for this cut.
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
