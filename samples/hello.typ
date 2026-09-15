#set page(paper: "a4", margin: 2.4cm)
#set text(font: "Libertinus Serif", size: 12pt)
#set document(title: "BluePaper production smoke", author: "BluePaper")

#align(center)[
  #text(size: 28pt, weight: "bold")[BluePaper]
  #v(0.4em)
  #text(size: 14pt, fill: rgb("#1d4ed8"))[Production conversion smoke test]
]

#v(1.2em)

This one-page PDF was compiled with Typst and uploaded to the live BluePaper API.
The sanitized copy should keep the words and layout as pixels, without the original PDF graph.

#v(0.8em)

#box(
  width: 100%,
  inset: 14pt,
  fill: rgb("#eff6ff"),
  stroke: 1pt + rgb("#1d4ed8"),
  radius: 4pt,
)[
  *Expected result after conversion*
  - Status `succeeded`
  - A rebuilt PDF fetched from `GET /v1/conversions/{id}/pdf`
  - A regexp report from `GET /v1/conversions/{id}/report`
]

#v(1em)

#grid(
  columns: (1fr, 1fr),
  gutter: 12pt,
  [*Compiled*], [2026-09-14],
  [*Tool*], [Typst + curl],
  [*Sample*], [`hello.pdf`],
)

#v(1.4em)
#align(center)[
  #text(size: 9pt, fill: rgb("#64748b"))[BluePaper production sample · hello]
]
