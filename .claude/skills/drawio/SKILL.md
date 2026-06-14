---
name: drawio
description: Always use when user asks to create, generate, draw, or design a diagram, flowchart, architecture diagram, ER diagram, sequence diagram, class diagram, network diagram, mockup, wireframe, or UI sketch, or mentions draw.io, drawio, drawoi, .drawio files, or diagram export to PNG/SVG/PDF.
---

# Draw.io Diagram Skill

Generate draw.io diagrams as native `.drawio` files. Optionally export to PNG, SVG, or PDF with the diagram XML embedded (so the exported file remains editable in draw.io), or generate a browser URL that opens the diagram directly in the draw.io editor.

## How to create a diagram

1. **Generate draw.io XML** in mxGraphModel format for the requested diagram
2. **Write the XML** to a `.drawio` file in the current working directory using the Write tool
3. **Handle the requested output format**:
   - `png` / `svg` / `pdf` → locate the draw.io CLI (see [draw.io CLI](#drawio-cli)), export with `--embed-diagram`, then delete the source `.drawio` file. If the CLI is not found, keep the `.drawio` file and tell the user they can install the draw.io desktop app to enable export, or use `url` mode instead, or open the `.drawio` file directly
   - `url` → generate a browser URL from the XML and open it (see [Browser URL output](#browser-url-output)). Keep the `.drawio` file as a persistent local copy
   - *(no format)* → no extra step; the `.drawio` file is the output
4. **Open the result** — the exported file if exported, the browser URL if `url`, or the `.drawio` file otherwise. If the open command fails, print the file path (or URL) so the user can open it manually

## Choosing the output format

Check the user's request for a format preference. Examples:

- `/drawio create a flowchart` → `flowchart.drawio`
- `/drawio png flowchart for login` → `login-flow.drawio.png`
- `/drawio svg: ER diagram` → `er-diagram.drawio.svg`
- `/drawio pdf architecture overview` → `architecture-overview.drawio.pdf`
- `/drawio url flowchart for user login` → opens browser at `app.diagrams.net` with the diagram, keeps `login-flow.drawio` locally

If no format is mentioned, just write the `.drawio` file and open it in draw.io. The user can always ask to export later.

### Supported export formats

| Format | Embed XML | Notes |
|--------|-----------|-------|
| `png` | Yes (`-e`) | Viewable everywhere, editable in draw.io |
| `svg` | Yes (`-e`) | Scalable, editable in draw.io |
| `pdf` | Yes (`-e`) | Printable, editable in draw.io |
| `jpg` | No | Lossy, no embedded XML support |

PNG, SVG, and PDF all support `--embed-diagram` — the exported file contains the full diagram XML, so opening it in draw.io recovers the editable diagram.

## Browser URL output

When the user requests `url` format, generate a draw.io URL that opens the diagram directly in the browser editor at `app.diagrams.net` — no draw.io Desktop required.

### How it works

1. The `.drawio` file is written to disk as usual (gives the user a persistent local copy they can re-edit)
2. The XML is compressed with Node.js's built-in `zlib` and base64-encoded
3. The result is embedded in a `https://app.diagrams.net/#create=...` URL
4. The URL is opened in the default browser

This uses only Node.js built-in modules (`zlib`, `child_process`) — no external dependencies.

### URL generation

Run this `node -e` one-liner to read the `.drawio` file and print the URL (replace `DIAGRAM.drawio` with the actual filename):

```bash
URL=$(node -e '
const fs = require("fs");
const zlib = require("zlib");
const xml = fs.readFileSync(process.argv[1], "utf8");
const compressed = zlib.deflateRawSync(encodeURIComponent(xml)).toString("base64");
const payload = encodeURIComponent(JSON.stringify({ type: "xml", compressed: true, data: compressed }));
console.log("https://app.diagrams.net/?grid=0&pv=0&border=10&edit=_blank#create=" + payload);
' DIAGRAM.drawio)
```

The URL format matches the MCP Tool Server. Node.js's `zlib.deflateRawSync` and `pako.deflateRaw` both implement RFC 1951 and produce identical output, so URLs from either source are interchangeable.

### Opening the URL

| Environment | Command |
|-------------|---------|
| macOS | `open "$URL"` |
| Linux (native) | `xdg-open "$URL"` |
| WSL2 | Write a temp `.url` file, open via `cmd.exe` (see below) |
| Windows (native) | Write a temp `.url` file, open via `start` (see below) |

**Why the `.url` workaround on Windows/WSL2?** `cmd.exe`'s `start` command treats `&` as a command separator and strips everything after `#` in URLs. The diagram payload lives in the `#create=...` fragment, so passing the URL directly causes
