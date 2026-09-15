#set page(paper: "a4", margin: 2.2cm)
#set text(font: "Libertinus Serif", size: 11pt)
#set heading(numbering: "1.")
#set document(title: "BluePaper conversion memo", author: "BluePaper")

#align(right)[
  #text(size: 9pt, fill: rgb("#475569"))[BluePaper production sample]
]

= Conversion memo

This two-page document checks that production conversion preserves
readable structure: headings, a table, a list, Unicode, and a hyperlink.

The original PDF may contain a `/URI` annotation from the link below.
The rebuilt PDF is pixels (and optional OCR), so active PDF features
cannot survive.

== What to look at

- Headings stay in reading order
- The table cells remain distinguishable
- The colored callout is still a block of color
- The link text is visible even if it is no longer clickable

#link("https://github.com/BluethroatLabs/BluePaper")[BluePaper source repository]

#figure(
  table(
    columns: (auto, 1fr, auto),
    inset: 8pt,
    align: left,
    stroke: 0.5pt + rgb("#94a3b8"),
    fill: (col, row) => if row == 0 { rgb("#dbeafe") } else { white },
    [*Stage*], [*Meaning*], [*HTTP*],
    [queued], [Accepted, not started], [202],
    [running], [Sandbox converting], [200],
    [succeeded], [Safe PDF + report ready], [200],
    [failed], [Report may still exist], [200],
  ),
  caption: [Conversion status values from the HTTP API],
)

#box(
  width: 100%,
  inset: 12pt,
  fill: rgb("#fef3c7"),
  stroke: 1pt + rgb("#d97706"),
  radius: 4pt,
)[
  *Unicode check.* Café, naïve, 日本語, العربية, emoji: 📄 → 🔒
]

== Indicators (informational)

These strings are ordinary page text, not live PDF actions. If they
appear in the original-byte report, that is the regexp pass seeing
literals. Zero hits is not a malware verdict.

#raw("/JavaScript  /OpenAction  /Launch  /EmbeddedFile")

#pagebreak()

= Page two

Keep a second page so the worker has more than one rasterized frame to
rebuild.

#lorem(90)

```
POST /v1/conversions
GET  /v1/conversions/{id}
GET  /v1/conversions/{id}/report
GET  /v1/conversions/{id}/pdf
```

#align(bottom)[
  #line(length: 100%, stroke: 0.4pt + rgb("#cbd5e1"))
  #text(size: 9pt, fill: rgb("#64748b"))[
    Page 2 of 2 · Typst original · expect a pixel-rebuilt PDF from production
  ]
]
