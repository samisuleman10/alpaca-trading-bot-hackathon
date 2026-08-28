# Diagrams

Each diagram is committed twice: the `.svg` is the source you edit, the `.png`
is the export for slides and for anything that will not render SVG.

Setup, once — the renderer dependency is declared in `hackathon/package.json`,
and it must be installed there, because Node resolves modules relative to the
script rather than to your working directory:

    cd hackathon && npm install

Regenerate a PNG after editing its SVG, from the repository root:

    node hackathon/scripts/render_diagram.js \
      hackathon/docs/diagrams/phase-flow.svg \
      hackathon/docs/diagrams/phase-flow.png 2

The trailing `2` is the zoom factor, so a 680x700 drawing exports at 1360x1400.

Colours are written into each SVG rather than pulled from a stylesheet, so the
file renders identically wherever it is opened.

| File | Shows |
| --- | --- |
| `phase-flow` | The eight phases of the build, from design to submission |
| `strategy-contract` | What the strategy function is allowed to see, and what it returns |
| `fill-timing` | When the backtest decides and when it fills |
