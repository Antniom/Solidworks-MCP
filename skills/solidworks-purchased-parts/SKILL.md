---
name: solidworks-purchased-parts
description: >-
  Default handling of standard and bought parts in SolidWorks work: bolts, screws, nuts, washers, pins,
  inserts, bearings, bushings, motors, sensors, springs and other catalogue items are listed for download
  from TraceParts or the maker's site instead of being modeled, and only the holes, seats and envelopes they
  need are modeled. Use whenever a design, part or assembly needs such items, e.g. "add M8 bolts to the
  flange", "it needs a 6204 bearing", "what fasteners does this need", "put screws in these holes", "lista de
  parafusos", "rolamento", unless the user explicitly asks for the part itself to be modeled.
---

# Standard and purchased parts: source them, don't model them

> **First SolidWorks request of the session:** call `plugin_updates {"action": "status"}` once. If its state is `ask_user`, ask the user in one short question whether to turn on automatic updates for the SolidCADWorks plugin, then call it with `"enable"` or `"decline"` and carry on. For any other state, say nothing about updates.

Fasteners, bearings and other catalogue parts are bought, not designed. Their exact geometry comes from the
maker's CAD, which is better than a model built by hand and costs no modeling time. So by default:

1. **Model only what is yours:** the holes, counterbores, threads (as tap-drill holes), seats, shoulders and
   clearances the part needs, sized to the standard.
2. **List the parts** for the user to download, in the format below.
3. Once the user has downloaded the STEP files, insert them with `add_component` (see
   `solidworks-assemblies`). A STEP is imported once, and a native copy is saved next to it.

Model the part itself only when the user explicitly asks for it (for example "model the nut", "I need the
bolt as a part file", "we can't download CAD"). Then use `solidworks-din-parts`. When the user asks to insert
parts from their SolidWorks Toolbox, use `solidworks-toolbox`.

## Choosing the parts

- Pick the designation from the standard: ISO / DIN number, size, length, property class or grade, finish.
  A bolt length should leave 1–3 thread pitches past the nut. In a tapped hole, the engagement should be at
  least 1 × d in steel or 1.5–2 × d in aluminium.
- Size the modeled holes to the same standard, and say which hole each part goes in:
  - clearance holes ISO 273 medium (M6 → Ø6.6, M8 → Ø9, M10 → Ø11);
  - tap-drill holes for tapped threads (M8 → Ø6.8);
  - bearing seats to the bearing's bore and outside diameter with the fit stated (e.g. housing H7, shaft k6).
- Bearings, motors, sensors and similar parts need the exact maker part number. If the user hasn't given
  one, propose one and mark it `[VERIFY]`; don't invent part numbers.
- No CAD available for a part? Model a simple envelope (outer size plus mounting features) named
  `BUY_<Maker>_<PartNo>_ENVELOPE` with custom property `Envelope = Yes`, and say so in the list.

## The list

Write it as a Markdown file next to the model, or in `91_Fasteners/FASTENER_LIST_<module>.md` inside a
`solidworks-project` folder. Also show it in the chat. Name the file `PURCHASED_PARTS_<assembly or part>.md`.
Use this template:

```markdown
# Purchased parts — <assembly or part>

| # | Part | Designation | Material / class / finish | Qty | Where it goes | Modeled interface | Where to get the CAD | CAD status |
|---|---|---|---|---|---|---|---|---|
| 1 | Hex bolt | ISO 4017 M8×30 | Steel 8.8, zinc plated | 4 | Flange to housing, PCD 90 | 4 × Ø9 clearance holes | TraceParts: "ISO 4017 M8x30" | to download |
| 2 | Hex nut | ISO 4032 M8 | Steel 8, zinc plated | 4 | Under flange | — | TraceParts: "ISO 4032 M8" | to download |
| 3 | Deep-groove ball bearing | SKF 6204-2RSH [VERIFY] | — | 2 | Shaft Ø20 k6 in housing Ø47 H7 | Bearing seat Ø47 H7, shoulder Ø26 | SKF product page or TraceParts: "SKF 6204-2RSH" | to download |
```

- **Where to get the CAD:** give a search term for TraceParts (traceparts.com) or the maker's site (SKF,
  NSK, Misumi, McMaster-Carr, Bossard, Würth, or the motor or sensor maker). Don't invent URLs.
- **CAD status:** one of `to download`, `downloaded: <path>`, `inserted`, `envelope`.
- Group identical parts on one row. Total the quantity per assembly.

Don't download files yourself unless the user asks you to, and then tell them the file name and source
first.

## After the user downloads

1. Ask the user where they saved the files (or find them in the project's `90_Purchased_Parts` /
   `91_Fasteners` folders).
2. Insert each part with `add_component` and mate it into its hole or seat.
3. Run `check_interference` (threads are not modeled, so a bolt in a tapped hole overlaps as expected).
4. Update the CAD status column.
