# Desk service marks

Local files only: nothing here is fetched from a CDN at run time. The mapping from a
connection to a file is `static/js/desk-v1-service-icons.js`; the CSS is
`static/css/desk-v1-service-icons.css`. Marks are never recoloured, stretched or
redrawn, and each sits in clear space (a white tile with padding) or fills its own tile.

All marks are trademarks of their owners and are used here only to identify the
service the user connected, next to the connection's name (UX-02).

Path data for the single-colour logos comes from the Simple Icons set (CC0 1.0, public
domain dedication; `LICENSE.md` in the package). The only edits: a `fill` in the
vendor's own colour, the `<title>` removed, and for Instagram and Snapchat a rounded
tile behind the glyph.

| File | Source | Licence / terms | Notes |
|---|---|---|---|
| `youtube.svg` | simple-icons 16.35.0 | CC0 | YouTube red `#FF0000` |
| `instagram.svg` | simple-icons 16.35.0 glyph | CC0 | White glyph on Instagram's gradient tile (yellow `#FED576`, orange `#F47133`, magenta `#BC3081`, blue `#4C63D2`). The gradient stops are an approximation of the app icon's, not copied from an Instagram file; replace with the Meta brand-resources file if exact stops matter |
| `facebook.svg` | simple-icons 16.35.0 | CC0 | Facebook blue `#0866FF` |
| `tiktok.svg` | simple-icons 16.35.0 | CC0 | Black, the single-colour form |
| `x.svg` | simple-icons 16.35.0 | CC0 | Black |
| `reddit.svg` | simple-icons 16.35.0 | CC0 | Reddit orange `#FF4500` |
| `linkedin.svg` | simple-icons **13.21.0** | CC0 | Removed from Simple Icons after 13.x at the owner's request; the 13.21.0 file on npm is the source. LinkedIn blue `#0A66C2` |
| `snapchat.svg` | simple-icons 16.35.0 glyph | CC0 | White ghost with black outline on yellow `#FFFC00`, the app-icon arrangement; the outline is a stroke added here |
| `github.svg` | simple-icons 16.35.0 | CC0 | GitHub black `#181717` |
| `notion.svg` | simple-icons 16.35.0 | CC0 | Black |
| `discord.svg` | simple-icons 16.35.0 | CC0 | Blurple `#5865F2` |
| `openai.svg` | simple-icons **13.21.0** | CC0 | Black (OpenAI's guidelines allow black or white) |
| `googledrive.svg` | Wikimedia Commons, `File:Google Drive icon (2020).svg` | Public domain (trademark noted on the page) | Four-colour mark as published, unchanged |
| `googlephotos.svg` | Wikimedia Commons, `File:Google Photos icon (2020).svg` | Public domain (trademark noted on the page) | As published; editor tooling attributes and an empty bounding path removed, shapes and fills untouched |
| `google.svg` | Wikimedia Commons, `File:Google "G" logo.svg` | Public domain (trademark noted on the page) | Four-colour G as published, unchanged. Used for the Google engine (Veo + Gemini image) |
| `higgsfield.png` | `https://higgsfield.ai/icon.png`, the site's own app icon, 192x192, fetched 2026-10-15 | **No licence stated and no press kit found.** | The one mark not taken from a brand-assets page or a CC0 set. Shipped as published because the Desk is for connecting a service the user already uses; swap it if Higgsfield publishes a brand kit or objects |

Services with no file here show a two-letter monogram on a colour derived from the
service's name, so the same service is the same colour everywhere.

To add one: put the file here, add a line to `MARKS` (and the ids, hosts or name words
that mean it) in `desk-v1-service-icons.js`, and add a row above.
