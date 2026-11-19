"""Application data shipped with the desktop bundle on every platform."""

from pathlib import Path


RESOURCE_FILES = (
    "engine/FONTS-OFL.txt",
    "engine/OFL-BebasNeue.txt",
    "engine/OFL-Montserrat.txt",
    "engine/bacchus_canvas.js",
    "engine/bfg/ORIGIN.txt",
    "engine/bfg/engine.js",
    "engine/bfg/figures.js",
    "engine/bfg/frame-contract.js",
    "engine/bfg/kit.js",
    "engine/bfg/maps.js",
    "engine/bfg/props.js",
    "engine/bfg-adapter.js",
    "engine/bfg-inputs.js",
    "engine/fonts-course.js",
    "engine/fonts.js",
    "packs/editorial/pack.json",
    "packs/editorial/plan.md",
    "style_guides/hand-drawn-explainer.md",
    "style_guides/night-signal.md",
    "style_guides/professional-corporate.md",
    "style_guides/studio-ink.md",
    "style_guides/warm-editorial.md",
    "ui/app.js",
    "ui/index.html",
    "ui/styles.css",
)


def bundle_data(project):
    """Select individual files so a local build cannot pick up adjacent work."""
    root = project / "bacchus" / "resources"
    data = []
    for name in RESOURCE_FILES:
        source = root / name
        if not source.is_file() or source.resolve() != source:
            raise ValueError(f"Missing or linked application resource: {name}")
        destination = "bacchus/resources/" + Path(name).parent.as_posix()
        data.append((str(source), destination))
    return data


def verify_bundled_resources(root):
    expected = set(RESOURCE_FILES)
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != expected:
        raise ValueError(
            f"The bundled application resources differ from the release manifest. "
            f"Missing: {sorted(expected - actual)}. Unexpected: {sorted(actual - expected)}."
        )
